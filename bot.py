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

# 🎯 CONFIGURATION STRATÉGIE EXPLOSION (STRETCH)
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

def register_new_token(mint: str):
    if not mint or mint in tracked_tokens:
        return
    if len(tracked_tokens) >= MAX_TRACKED:
        tracked_tokens.popitem(last=False)
        
    tracked_tokens[mint] = {
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

def analyser_logs_solana(params: dict):
    result = params.get("result", {})
    value = result.get("value", {})
    logs = value.get("logs", [])
    
    is_pump_transaction = False
    for log in logs:
        if "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5AMX787Nz" in log:
            is_pump_transaction = True
            break
            
    if not is_pump_transaction:
        return

    tx = value.get("transaction", {})
    meta = tx.get("meta", {}) if tx else {}
    if not meta:
        return
        
    post_balances = meta.get("postTokenBalances", [])
    mint = None
    ui_amount = 0.0
    
    for balance in post_balances:
        if balance.get("owner") == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5AMX787Nz":
            mint = balance.get("mint")
            ui_amount = safe_float(balance.get("uiTokenAmount", {}).get("uiAmount"))
            break

    if not mint:
        return

    now = time.time()
    if mint not in tracked_tokens:
        register_new_token(mint)

    token_info = tracked_tokens[mint]
    if token_info["alerted"] or (now - token_info["created_at"]) > MAX_TOKEN_AGE_SEC:
        return

    if ui_amount > 0:
        mcap_sol = (1000000000 - ui_amount) * 0.00000003 + 30 
        mcap_usd = mcap_sol * SOL_PRICE_USD

        if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
            token_info["alerted"] = True
            elapsed = now - token_info["created_at"]
            time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"

            print(f"🔥 [EXPLOSION DETECTED] Token {mint} à {mcap_usd:,.0f}$")

            message = (
                f"⚡ <b>NOUVELLE PAIRE EN EXPLOSION (SOLANA RPC)</b> ⚡\n\n"
                f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                f"• <b>Âge de la paire :</b> <code>{time_str}</code> 🔥\n\n"
                f"📊 <b>Outils de Sniping :</b>\n"
                f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
                f"📥 <b>CA :</b> <code>{mint}</code>"
            )
            asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    global TOTAL_MESSAGES_RECEIVED
    
    # 📡 Utilisation du WebSocket Public de la Communauté Solana (Sans clé / Sans limite 429)
    uri = "wss://api.mainnet-beta.solana.com"
    print("=== [BOT] Connexion au WebSocket Public Solana ===")
    
    await asyncio.sleep(2)
    asyncio.create_task(send_telegram_alert("⚡ <b>Sniper Public Connecté</b> — Scan de la zone Stretch réinitialisé..."))

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                subscribe_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5AMX787Nz"]},
                        {"commitment": "confirmed"}
                    ]
                }
                await websocket.send(json.dumps(subscribe_payload))
                print("[SOLANA RPC] 📡 Flux public raccordé avec succès.")

                async for message in websocket:
                    TOTAL_MESSAGES_RECEIVED += 1
                    if TOTAL_MESSAGES_RECEIVED % 50 == 0:
                        print(f"[RPC CHECK] {TOTAL_MESSAGES_RECEIVED} logs réseau analysés...")

                    try:
                        data = json.loads(message)
                        params = data.get("params")
                        if params:
                            analyser_logs_solana(params)
                    except Exception:
                        continue

        except Exception as e:
            print(f"[RPC RETRY] Erreur réseau ({e}), reconnexion dans 5s...")
            await asyncio.sleep(5)

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
        "messages_scanned": TOTAL_MESSAGES_RECEIVED,
        "tracked_count": len(tracked_tokens)
    }
