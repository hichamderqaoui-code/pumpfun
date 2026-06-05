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
SHYFT_API_KEY      = os.getenv("SHYFT_API_KEY", "").strip()

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

def analyser_donnees_shyft(data: dict):
    # Traitement des messages du flux filtré Shyft
    actions = data.get("actions", [])
    if not actions:
        return

    for action in actions:
        # On cible uniquement les transactions liées à Pump.fun
        info = action.get("info", {})
        mint = info.get("mint") or info.get("token_address")
        
        if not mint:
            continue

        now = time.time()
        if mint not in tracked_tokens:
            register_new_token(mint)

        token_info = tracked_tokens[mint]
        if token_info["alerted"] or (now - token_info["created_at"]) > MAX_TOKEN_AGE_SEC:
            continue

        # Extraction ou estimation des volumes/mcap
        mcap_usd = safe_float(info.get("market_cap_usd") or info.get("mcap"))
        
        # Si Shyft ne donne pas le mcap direct, on regarde les SOL de la transaction pour estimer la courbe
        if mcap_usd == 0.0 and "tokens_swapped" in info:
            swapped = safe_float(info.get("tokens_swapped"))
            if swapped > 0:
                mcap_sol = (1000000000 - swapped) * 0.00000003 + 30
                mcap_usd = mcap_sol * SOL_PRICE_USD

        if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
            token_info["alerted"] = True
            elapsed = now - token_info["created_at"]
            time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"

            print(f"🔥 [SHYFT EXPLOSION] Token {mint} à {mcap_usd:,.0f}$")

            message = (
                f"⚡ <b>PAIRE EN EXPLOSION (SHYFT STREAM)</b> ⚡\n\n"
                f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                f"• <b>Âge du Jeton :</b> <code>{time_str}</code> 🔥\n\n"
                f"📊 <b>Outils de Sniping :</b>\n"
                f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
                f"📥 <b>CA :</b> <code>{mint}</code>"
            )
            asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    global TOTAL_MESSAGES_RECEIVED
    
    if not SHYFT_API_KEY:
        print("[ERREUR] La variable SHYFT_API_KEY n'est pas configurée dans Railway !")
        return

    # Connexion directe à l'infrastructure gRPC/WebSocket optimisée de Shyft
    uri = f"wss://api.shyft.to/sol/v1/streaming?api_key={SHYFT_API_KEY}"
    print("=== [BOT] Connexion au WebSocket Dédié Shyft ===")
    
    await asyncio.sleep(2)
    asyncio.create_task(send_telegram_alert("⚡ <b>Sniper SHYFT Connecté</b> — Démarrage du flux de détection..."))

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                # Demande d'abonnement spécifique pour Pump.fun (évite le spam global)
                subscribe_payload = {
                    "method": "SUBSCRIBE",
                    "params": {
                        "accounts": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5AMX787Nz"],
                        "type": "transactions"
                    },
                    "id": 1
                }
                await websocket.send(json.dumps(subscribe_payload))
                print("[SHYFT] 📡 Flux de transaction ciblé et connecté.")

                async for message in websocket:
                    TOTAL_MESSAGES_RECEIVED += 1
                    if TOTAL_MESSAGES_RECEIVED % 50 == 0:
                        print(f"[SHYFT CHECK] {TOTAL_MESSAGES_RECEIVED} transactions analysées...")

                    try:
                        data = json.loads(message)
                        # Vérification de la présence de données de transaction
                        if "actions" in data:
                            analyser_donnees_shyft(data)
                    except Exception:
                        continue

        except Exception as e:
            print(f"[SHYFT RETRY] Déconnexion ({e}), reconnexion dans 4s...")
            await asyncio.sleep(4)

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
