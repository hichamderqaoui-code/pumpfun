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
TARGET_MCAP_USD   = float(os.getenv("TARGET_MCAP_USD",  "3000"))
SOL_PRICE_USD     = float(os.getenv("SOL_PRICE_USD",    "66.12"))
MIN_HOLDERS       = int(os.getenv("MIN_HOLDERS",        "5"))
MAX_AGE_SECONDS   = int(os.getenv("MAX_AGE_SECONDS",    "600"))
MIGRATION_USD     = float(os.getenv("MIGRATION_USD",    "27000"))

tracked_tokens: OrderedDict = OrderedDict()
MAX_TRACKED = 1000
alert_queue: asyncio.Queue = None


def calculer_market_cap_usd(data: dict) -> float:
    mcap_sol = float(data.get("marketCapSol") or 0)
    if mcap_sol > 0:
        return mcap_sol * SOL_PRICE_USD
    v_tokens = float(data.get("vTokenReserves") or 0) / 1e6
    if v_tokens > 0:
        return (30 / v_tokens) * 1_000_000_000 * SOL_PRICE_USD
    return 0.0


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
        "tx_count":   0,
    }
    print(f"🆕 [WS CREATION] {data.get('name')} ({mint[:8]}...)")


async def send_telegram_alert(message: str, is_test: bool = False):
    token   = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not token or not chat_id:
        print(f"[TELEGRAM] ❌ MANQUANT token='{token[:10]}...' chat_id='{chat_id}'")
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
    print("[TELEGRAM WORKER] ✅ Démarré.")
    while True:
        try:
            message = await asyncio.wait_for(alert_queue.get(), timeout=30.0)
            await send_telegram_alert(message)
            alert_queue.task_done()
            await asyncio.sleep(0.5)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"[TELEGRAM WORKER] ❌ {e}")


def analyser_trade_streaming(data: dict):
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
    sol_amount = float(data.get("solAmount") or 0) / 1e9

    if trader:
        token_info["traders"].add(trader)

    token_info["tx_count"]   += 1
    token_info["volume_usd"] += sol_amount * SOL_PRICE_USD

    mcap_usd       = calculer_market_cap_usd(data)
    unique_holders = len(token_info["traders"])
    bc_pct         = min((mcap_usd / MIGRATION_USD) * 100, 100) if MIGRATION_USD > 0 else 0

    if token_info["tx_count"] % 5 == 0:
        print(
            f"⚡ [STREAM] {token_info['name'][:14]:<14} | "
            f"MC: {mcap_usd:>8,.0f}$ | "
            f"BC: {bc_pct:.0f}% | "
            f"Traders: {unique_holders} | "
            f"{elapsed:.0f}s"
        )

    # Pas encore au seuil
    if mcap_usd < TARGET_MCAP_USD:
        return

    # Anti wash-trading
    if unique_holders < MIN_HOLDERS:
        return

    # Déjà migré → trop tard
    if mcap_usd >= MIGRATION_USD:
        token_info["alerted"] = True
        print(f"[SKIP] {token_info['name']} déjà migré ({mcap_usd:,.0f}$)")
        return

    token_info["alerted"] = True

    name     = token_info["name"]
    symbol   = token_info["symbol"]
    time_str = f"{int(elapsed//60)}m {int(elapsed%60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"
    mult_mig  = MIGRATION_USD / mcap_usd
    mult_100k = 100000 / mcap_usd
    reste_pct = 100 - bc_pct

    print(f"🎯 [ALERTE] {name} | MC: {mcap_usd:,.0f}$ | BC: {bc_pct:.0f}% | Traders: {unique_holders} | {time_str}")

    message = (
        f"🟢 *SIGNAL PRÉ-MIGRATION — {bc_pct:.0f}% vers Raydium*\n\n"
        f"• *Nom :* {name} ({symbol})\n"
        f"• *Market Cap :* `{mcap_usd:,.0f}$`\n"
        f"• *Bonding Curve :* `{bc_pct:.0f}%` — reste `{reste_pct:.0f}%` avant migration\n"
        f"• *Acheteurs uniques :* `{unique_holders}`\n"
        f"• *Volume :* `{token_info['volume_usd']:,.0f}$`\n"
        f"• *Transactions :* `{token_info['tx_count']}`\n"
        f"• *Âge :* `{time_str}` ⏱️\n\n"
        f"📈 *Potentiel :*\n"
        f"• x{mult_mig:.1f} jusqu'à migration (27K$)\n"
        f"• x{mult_100k:.1f} jusqu'à 100K$\n\n"
        "🔍 *Liens :*\n"
        f"• [Pump.fun](https://pump.fun/{mint})\n"
        f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n"
        f"• [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
        f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
        "📥 *CA :*\n"
        f"`{mint}`"
    )

    if alert_queue:
        alert_queue.put_nowait(message)
    else:
        print("[TELEGRAM] ❌ Queue non initialisée !")


async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Sniper Pré-Migration démarré ===")
    await send_telegram_alert(
        f"🚀 *Sniper Pré-Migration en ligne*\n\n"
        f"💰 Alerte à : `{TARGET_MCAP_USD:,.0f}$` MC\n"
        f"🎯 Migration Raydium : `{MIGRATION_USD:,.0f}$`\n"
        f"👥 Min traders : `{MIN_HOLDERS}`\n"
        f"💵 SOL : `{SOL_PRICE_USD}$`\n"
        f"⏱️ Fenêtre : `{MAX_AGE_SECONDS // 60} min`\n\n"
        "Entrée 3-8K$ → Sortie 50-100K$ 🎯",
        is_test=True
    )

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("[WEBSOCKET] ✅ Connecté.")
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Streaming global actif.")

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
        "sol_price_usd":   SOL_PRICE_USD,
        "target_mcap_usd": TARGET_MCAP_USD,
        "migration_usd":   MIGRATION_USD,
        "min_holders":     MIN_HOLDERS,
        "max_age_seconds": MAX_AGE_SECONDS,
        "tracked_tokens":  len(tracked_tokens),
        "queue_size":      alert_queue.qsize() if alert_queue else 0,
    }


@app.get("/test-telegram")
async def test_telegram():
    await send_telegram_alert("✅ TEST OK — Bot connecté !", is_test=True)
    return {"status": "envoyé"}
