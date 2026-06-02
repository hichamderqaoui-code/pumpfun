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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8659214495:AAGN0uPMlXfsybXfrPZlGCsmsCisIevNc_g")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1532612243")
TARGET_MCAP_USD = float(os.getenv("TARGET_MCAP_USD", "10000"))
MAX_AGE_SECONDS = int(os.getenv("MAX_AGE_SECONDS", "600"))
SOL_PRICE_USD = float(os.getenv("SOL_PRICE_USD", "75.33"))
POLL_INTERVAL = 10  # Vérification DexScreener toutes les 10s

tracked_new_tokens = OrderedDict()
MAX_TRACKED = 500  # Réduit car on poll chaque token


def register_new_token(data: dict):
    mint = data.get("mint")
    if not mint or mint in tracked_new_tokens:
        return None
    if len(tracked_new_tokens) >= MAX_TRACKED:
        tracked_new_tokens.popitem(last=False)
    tracked_new_tokens[mint] = {
        "name": data.get("name", "Unknown"),
        "symbol": data.get("symbol", "MEME"),
        "created_at": time.time(),
        "alerted": False
    }
    print(f"🆕 [NOUVEAU TOKEN] {data.get('name')} | {mint[:8]}...")
    return mint


async def send_telegram_alert(message: str, is_test: bool = False):
    token = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    if not is_test:
        payload["parse_mode"] = "Markdown"
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


async def check_mcap_dexscreener(mint: str) -> float:
    """Retourne le market cap USD depuis DexScreener, 0 si indispo"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            data = r.json()
            pairs = data.get("pairs", [])
            if pairs:
                mc = pairs[0].get("fdv") or pairs[0].get("marketCap") or 0
                return float(mc)
    except Exception:
        pass
    return 0.0


async def monitor_tokens():
    """Boucle qui poll DexScreener sur tous les tokens actifs"""
    print("[MONITOR] 🔍 Démarrage surveillance DexScreener...")
    while True:
        now = time.time()
        # Copie pour éviter les modifications pendant l'itération
        mints = list(tracked_new_tokens.keys())

        for mint in mints:
            token_info = tracked_new_tokens.get(mint)
            if not token_info or token_info["alerted"]:
                continue

            elapsed = now - token_info["created_at"]

            # Token trop vieux → on abandonne
            if elapsed > MAX_AGE_SECONDS:
                token_info["alerted"] = True
                continue

            mcap_usd = await check_mcap_dexscreener(mint)

            if mcap_usd > 0:
                print(f"[POLL] {token_info['name']} | MC: {mcap_usd:,.0f}$ | {elapsed:.0f}s écoulées")

            if mcap_usd >= TARGET_MCAP_USD:
                token_info["alerted"] = True

                name = token_info["name"]
                symbol = token_info["symbol"]
                minutes = int(elapsed // 60)
                seconds = int(elapsed % 60)
                time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
                multiplier = 100000 / mcap_usd if mcap_usd > 0 else 10

                print(f"🎯 [ALERTE] {name} → {mcap_usd:,.0f}$ en {time_str}!")

                message = (
                    "🚨 *TOKEN À ANALYSER — 10K$ ATTEINT*\n\n"
                    f"• *Nom :* {name} ({symbol})\n"
                    f"• *Market Cap :* `{mcap_usd:,.0f}$`\n"
                    f"• *Temps depuis création :* `{time_str}` ⏱️\n"
                    f"• *Potentiel x vers 100k$ :* `x{multiplier:.1f}`\n\n"
                    "🔍 *Analyse avant entrée :*\n"
                    f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n"
                    f"• [RugCheck](https://rugcheck.xyz/tokens/{mint})\n"
                    f"• [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
                    f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n\n"
                    "📥 *CA :*\n"
                    f"`{mint}`"
                )
                await send_telegram_alert(message)

        await asyncio.sleep(POLL_INTERVAL)


async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage Sniper 10K$ ===")
    await send_telegram_alert(
        "🚨 *Sniper 10K$ Actif*\n"
        f"Cible : `{TARGET_MCAP_USD:,.0f}$` en < 10 min\n"
        f"SOL : `{SOL_PRICE_USD}$`\n"
        "Surveillance DexScreener toutes les 10s",
        is_test=True
    )

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("[WEBSOCKET] ✅ Connecté.")
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                print("[WEBSOCKET] 📡 Abonné aux nouveaux tokens.")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    if data.get("txType") == "create":
                        register_new_token(data)

        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Reconnexion dans 3s...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Erreur : {e}")
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_task = asyncio.create_task(solana_websocket_listener())
    monitor_task = asyncio.create_task(monitor_tokens())
    yield
    ws_task.cancel()
    monitor_task.cancel()
    for task in [ws_task, monitor_task]:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/")
def home():
    active = sum(1 for t in tracked_new_tokens.values() if not t["alerted"])
    return {
        "status": "active",
        "sol_price": SOL_PRICE_USD,
        "target_mcap_usd": TARGET_MCAP_USD,
        "tracked_tokens": len(tracked_new_tokens),
        "active_tokens": active
    }
