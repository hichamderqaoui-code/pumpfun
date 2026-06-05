import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets
from collections import OrderedDict
import time

# ─── CONFIGURATION VIA VARIABLES D'ENVIRONNEMENT ───
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
SOL_PRICE_USD      = float(os.getenv("SOL_PRICE_USD", "65.00"))  

# 🎯 CRITÈRES NOUVELLES PAIRES (Axiom Stretch Mode)
MIN_STRETCH_MCAP   = 8000.0   
MAX_STRETCH_MCAP   = 15000.0  
MAX_TOKEN_AGE_SEC  = 900.0    # 15 minutes max

TOTAL_MESSAGES_RECEIVED = 0
tracked_tokens = OrderedDict()
MAX_TRACKED = 3000  

def safe_float(value, default=0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def register_new_token(mint: str, name: str, symbol: str):
    if not mint or mint in tracked_tokens:
        return
    if len(tracked_tokens) >= MAX_TRACKED:
        tracked_tokens.popitem(last=False)
        
    tracked_tokens[mint] = {
        "name": name,
        "symbol": symbol,
        "created_at": time.time(),
        "alerted": False
    }

async def send_telegram_alert(message: str):
    token = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=5.0)
    except Exception as e:
        print(f"[TELEGRAM ERREUR] -> {e}")

def analyser_trade_streaming(data: dict):
    mint = data.get("mint")
    if not mint:
        return

    if mint not in tracked_tokens:
        register_new_token(mint, data.get("name", "Jeton"), data.get("symbol", "PUMP"))

    token_info = tracked_tokens[mint]
    if token_info["alerted"]:
        return

    # Vérification de l'âge du token
    now = time.time()
    if (now - token_info["created_at"]) > MAX_TOKEN_AGE_SEC:
        return

    mcap_sol_brut = safe_float(data.get("marketCapSol"))
    mcap_usd = mcap_sol_brut * SOL_PRICE_USD

    if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
        token_info["alerted"] = True
        elapsed = now - token_info["created_at"]
        time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"

        print(f"🔥 [EXPLOSION] {token_info['name']} à {mcap_usd:,.0f}$")

        message = (
            f"⚡ <b>NOUVELLE PAIRE EN EXPLOSION</b> ⚡\n\n"
            f"• <b>Nom :</b> {token_info['name']} ({token_info['symbol']})\n"
            f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
            f"• <b>Depuis création :</b> <code>{time_str}</code> 🔥\n\n"
            f"📊 <b>Sniper Direct :</b>\n"
            f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
            f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
            f"📥 <b>CA :</b> <code>{mint}</code>"
        )
        asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    global TOTAL_MESSAGES_RECEIVED
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Retour sur Flux PumpPortal Standard ===")
    
    await asyncio.sleep(2)
    asyncio.create_task(send_telegram_alert("📡 <b>Sniper Mode Actif</b> — Surveillance de la zone Stretch 8k$-15k$..."))

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                # On s'abonne aux lancements ET aux trades
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] Connecxion établie. Analyse du flux en cours.")

                async for message in websocket:
                    TOTAL_MESSAGES_RECEIVED += 1
                    if TOTAL_MESSAGES_RECEIVED % 200 == 0:
                        print(f"[LIVE CHECK] {TOTAL_MESSAGES_RECEIVED} transactions analysées...")

                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue

                    tx_type = data.get("txType")
                    event_type = data.get("eventType")
                    mint = data.get("mint")

                    if mint:
                        if tx_type == "create" or event_type == "create":
                            register_new_token(mint, data.get("name", "Unknown"), data.get("symbol", "TOKEN"))
                        elif tx_type in ["buy", "sell"]:
                            analyser_trade_streaming(data)

        except Exception as e:
            print(f"[WEBSOCKET RETRY] Erreur de connexion ({e}), reconnexion dans 3s...")
            await asyncio.sleep(3)

@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_task = asyncio.create_task(solana_websocket_listener())
    yield
    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)

@app.get("/")
def health_check():
    return {
        "status": "online",
        "total_messages_processed": TOTAL_MESSAGES_RECEIVED,
        "tracked_tokens_count": len(tracked_tokens)
    }
