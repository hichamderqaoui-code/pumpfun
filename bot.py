import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets
from collections import OrderedDict
import time

# ─── CONFIG ───
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")
TARGET_MCAP_USD   = float(os.getenv("TARGET_MCAP_USD",  "5000"))
SOL_PRICE_USD     = float(os.getenv("SOL_PRICE_USD",    "75.33"))
MIN_HOLDERS       = int(os.getenv("MIN_HOLDERS",         "5"))
MAX_AGE_SECONDS   = int(os.getenv("MAX_AGE_SECONDS",    "600"))

tracked_tokens = OrderedDict()
MAX_TRACKED = 1000

# ─── FILE D'ATTENTE pour les alertes Telegram ───
alert_queue: asyncio.Queue = None


def calculer_market_cap_sol(v_tokens: float) -> float:
    TOTAL_SUPPLY = 1_000_000_000
    if v_tokens <= 0:
        return 0
    price_per_token_sol = 30 / v_tokens
    return TOTAL_SUPPLY * price_per_token_sol


def register_new_token(data: dict):
    mint = data.get("mint")
    if not mint or mint in tracked_tokens:
        return
    if len(tracked_tokens) >= MAX_TRACKED:
        tracked_tokens.popitem(last=False)
    tracked_tokens[mint] = {
        "name":       data.get("name", "Unknown"),
        "symbol":     data.get("symbol", "MEME"),
        "created_at": time.time(),
        "traders":    set(),
        "alerted":    False,
        "volume_usd": 0.0,
        "tx_count":   0
    }
    print(f"🆕 [WS CREATION] {data.get('name')} enregistré ({mint[:8]}...)")


async def send_telegram_alert(message: str, is_test: bool = False):
    token   = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not token or not chat_id:
        print(f"[TELEGRAM] ❌ MANQUANT token='{token[:10]}' chat_id='{chat_id}'")
        return
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    if not is_test:
        payload["parse_mode"]               = "Markdown"
        payload["disable_web_page_preview"] = True
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=10.0)
            if r.status_code != 200:
                print(f"[TELEGRAM] ❌ HTTP {r.status_code} : {r.text}")
            else:
                print(f"[TELEGRAM] ✅ Alerte envoyée.")
    except Exception as e:
        print(f"[TELEGRAM] ❌ Erreur : {e}")


async def telegram_worker():
    """Tâche dédiée qui envoie les alertes depuis la queue — évite le bug asyncio.create_task depuis sync"""
    global alert_queue
    print("[TELEGRAM WORKER] ✅ Démarré.")
    while True:
        message = await alert_queue.get()
        await send_telegram_alert(message)
        alert_queue.task_done()
        await asyncio.sleep(0.5)  # anti-flood Telegram


def analyser_trade_streaming(data: dict):
    """Analyse les transactions en temps réel — enqueue l'alerte au lieu de créer une task"""
    mint = data.get("mint")
    if not mint or mint not in tracked_tokens:
        return

    token_info = tracked_tokens[mint]
    if token_info["alerted"]:
        return

    now     = time.time()
    elapsed = now - token_info["created_at"]

    if elapsed > MAX_AGE_SECONDS:
        token_info["alerted"] = True
        return

    trader     = data.get("traderPublicKey")
    sol_amount = float(data.get("solAmount", 0)) / 1e9
    v_tokens   = float(data.get("vTokenReserves", 0)) / 1e6

    if trader:
        token_info["traders"].add(trader)

    token_info["tx_count"]   += 1
    token_info["volume_usd"] += (sol_amount * SOL_PRICE_USD)

    mcap_sol = data.get("marketCapSol", 0)
    if mcap_sol == 0 and v_tokens > 0:
        mcap_sol = calculer_market_cap_sol(v_tokens)

    mcap_usd       = mcap_sol * SOL_PRICE_USD
    unique_holders = len(token_info["traders"])

    if token_info["tx_count"] % 3 == 0:
        print(
            f"⚡ [STREAM] {token_info['name'][:12]:<12} | "
            f"MC: {mcap_usd:,.0f}$ | "
            f"Traders: {unique_holders} | "
            f"Age: {elapsed:.0f}s"
        )

    if mcap_usd >= TARGET_MCAP_USD:
        if unique_holders < MIN_HOLDERS:
            return

        token_info["alerted"] = True

        name     = token_info["name"]
        symbol   = token_info["symbol"]
        time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"
        mult     = 100000 / mcap_usd if mcap_usd > 0 else 10

        print(f"🎯 [ALERTE] {name} | MC: {mcap_usd:,.0f}$ | Traders: {unique_holders} | {time_str}")

        message = (
            f"🟢 *SIGNAL TEMPS RÉEL — PUMP FLASH*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap :* `{mcap_usd:,.0f}$` 🚀\n"
            f"• *Acheteurs Uniques :* `{unique_holders} portefeuilles` 👥\n"
            f"• *Volume généré :* `{token_info['volume_usd']:,.0f}$`\n"
            f"• *Total Transactions :* `{token_info['tx_count']}`\n"
            f"• *Temps depuis création :* `{time_str}` ⏱️\n"
            f"• *Objectif x100k :* `x{mult:.1f}`\n\n"
            "🔍 *Outils de Sniping :*\n"
            f"• [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
            f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n"
            f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
            "📥 *Contrat (CA) :*\n"
            f"`{mint}`"
        )

        # ✅ FIX : on met en queue au lieu de create_task depuis une fonction sync
        if alert_queue:
            alert_queue.put_nowait(message)
        else:
            print("[TELEGRAM] ❌ Queue non initialisée !")


async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage Sniper Streaming ===")
    await send_telegram_alert(
        f"⚡ *Sniper Streaming en ligne*\n\n"
        f"💰 Cible MC : `{TARGET_MCAP_USD:,.0f}$`\n"
        f"👥 Min traders : `{MIN_HOLDERS}`\n"
        f"⏱️ Fenêtre max : `{MAX_AGE_SECONDS//60} min`\n\n"
        "🟢 Alertes actives",
        is_test=True
    )

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("[WEBSOCKET] ✅ Flux connecté.")
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Surveillance globale active.")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except Exception:
                        continue

                    tx_type = data.get("txType")
                    if tx_type == "create":
                        register_new_token(data)
                    elif tx_type in ["buy", "sell"] or "marketCapSol" in data:
                        analyser_trade_streaming(data)

        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Reconnexion dans 3s...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Erreur : {e}")
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global alert_queue
    alert_queue = asyncio.Queue()
    worker_task = asyncio.create_task(telegram_worker())
    ws_task     = asyncio.create_task(solana_websocket_listener())
    yield
    ws_task.cancel()
    worker_task.cancel()
    for task in [ws_task, worker_task]:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/")
def home():
    return {
        "status":          "active",
        "mode":            "Streaming WebSocket",
        "target_mcap_usd": TARGET_MCAP_USD,
        "min_holders":     MIN_HOLDERS,
        "max_age_seconds": MAX_AGE_SECONDS,
        "tracked_tokens":  len(tracked_tokens),
        "queue_size":      alert_queue.qsize() if alert_queue else 0
    }


@app.get("/test-telegram")
async def test_telegram():
    await send_telegram_alert("✅ TEST OK — Bot connecté !", is_test=True)
    return {"status": "envoyé"}
