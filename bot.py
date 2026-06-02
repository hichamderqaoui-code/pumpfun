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
TARGET_MCAP_SOL = float(os.getenv("TARGET_MCAP_SOL", "133"))  # ~10 000$ / 75$ SOL
MAX_AGE_SECONDS = int(os.getenv("MAX_AGE_SECONDS", "600"))
SOL_PRICE_USD = float(os.getenv("SOL_PRICE_USD", "75.33"))

tracked_new_tokens = OrderedDict()
MAX_TRACKED = 3000
_websocket = None


def register_new_token(data: dict):
    mint = data.get("mint")
    if not mint:
        return None
    if len(tracked_new_tokens) >= MAX_TRACKED:
        tracked_new_tokens.popitem(last=False)
    tracked_new_tokens[mint] = {
        "name": data.get("name", "Unknown"),
        "symbol": data.get("symbol", "MEME"),
        "created_at": time.time(),
        "alerted": False
    }
    print(f"🆕 [NOUVEAU TOKEN] {data.get('name')} enregistré. (mint: {mint[:8]}...)")
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


def analyser_transaction(trade_data: dict):
    mint = trade_data.get("mint")
    if not mint or mint not in tracked_new_tokens:
        return

    token_info = tracked_new_tokens[mint]
    if token_info["alerted"]:
        return

    now = time.time()
    elapsed_time = now - token_info["created_at"]

    if elapsed_time > MAX_AGE_SECONDS:
        token_info["alerted"] = True
        return

    mcap_sol = trade_data.get("marketCapSol", 0)
    mcap_usd = mcap_sol * SOL_PRICE_USD

    if mcap_sol > 0:
        print(f"[TRADE] {token_info['name']} | MC: {mcap_sol:.1f} SOL ({mcap_usd:,.0f}$) | Seuil: {TARGET_MCAP_SOL} SOL | {elapsed_time:.0f}s")

    if mcap_sol >= TARGET_MCAP_SOL:
        token_info["alerted"] = True

        name = token_info["name"]
        symbol = token_info["symbol"]
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)
        time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
        multiplier_to_100k = 100000 / mcap_usd if mcap_usd > 0 else 10

        print(f"🎯 [BOUGIE EXPLOSIVE] {name} → {mcap_sol:.0f} SOL ({mcap_usd:,.0f}$) en {time_str}!")

        message = (
            "⚡ *PUMP EXPLOSIF (< 10 MIN)*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap :* `{mcap_usd:,.0f}$` ({mcap_sol:.0f} SOL)\n"
            f"• *Temps depuis création :* `{time_str}` ⏱️\n"
            f"• *Potentiel vers 100k$ :* `x{multiplier_to_100k:.1f}`\n\n"
            "📈 *Liens :*\n"
            f"• [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
            f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n"
            f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
            "📥 *CA :*\n"
            f"`{mint}`"
        )
        asyncio.create_task(send_telegram_alert(message))


async def solana_websocket_listener():
    global _websocket
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage Sniper ===")
    await send_telegram_alert(
        "⏱️ *Sniper Actif*\n"
        f"Cible : `{TARGET_MCAP_SOL} SOL` (~`{TARGET_MCAP_SOL * SOL_PRICE_USD:,.0f}$`)\n"
        f"SOL fixé à `{SOL_PRICE_USD}$`",
        is_test=True
    )

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                _websocket = websocket
                print("[WEBSOCKET] ✅ Connecté.")
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Abonné : nouveaux tokens + tous trades.")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue

                    tx_type = data.get("txType")

                    if tx_type == "create":
                        mint = register_new_token(data)
                        if mint and _websocket:
                            try:
                                await _websocket.send(json.dumps({
                                    "method": "subscribeTokenTrade",
                                    "keys": [mint]
                                }))
                            except Exception:
                                pass

                    elif tx_type in ("buy", "sell") or "marketCapSol" in data:
                        analyser_transaction(data)

        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Déconnecté. Reconnexion dans 3s...")
            _websocket = None
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Erreur : {e}")
            _websocket = None
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(solana_websocket_listener())
    yield
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/")
def home():
    return {
        "status": "active",
        "sol_price": SOL_PRICE_USD,
        "target_mcap_sol": TARGET_MCAP_SOL,
        "target_mcap_usd": TARGET_MCAP_SOL * SOL_PRICE_USD,
        "tracked_tokens": len(tracked_new_tokens)
    }
