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

# 🎯 CONFIGURATION FILTRE STRETCH (Axiom Pro Mode)
MIN_STRETCH_MCAP   = 8000.0   # Début de la zone Stretch
MAX_STRETCH_MCAP   = 15000.0  # Fin de la zone d'alerte Stretch

# ─── COMPTEUR DE VÉRIFICATION EN DIRECT ───
TOTAL_TRADES_ANALYSED = 0

tracked_tokens = OrderedDict()
MAX_TRACKED = 2000  

def safe_float(value, default=0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def register_new_token(data: dict):
    mint = data.get("mint")
    if not mint or mint in tracked_tokens:
        return
    
    if len(tracked_tokens) >= MAX_TRACKED:
        tracked_tokens.popitem(last=False)
        
    tracked_tokens[mint] = {
        "name": data.get("name", "Unknown"),
        "symbol": data.get("symbol", "TOKEN"),
        "created_at": time.time(),
        "alerted": False
    }

async def send_telegram_alert(message: str, is_test: bool = False):
    token = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": message
    }
    if not is_test:
        payload["parse_mode"] = "HTML"
        payload["disable_web_page_preview"] = True
        
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=5.0)
            if response.status_code != 200:
                print(f"[TELEGRAM ERREUR] -> {response.text}")
    except Exception as e:
        print(f"[TELEGRAM ERREUR] -> {e}")

def analyser_trade_streaming(data: dict):
    global TOTAL_TRADES_ANALYSED
    mint = data.get("mint")
    if not mint:
        return

    # 📈 COMPTEUR LIVE : Affiche l'activité en tâche de fond toutes les 500 transactions reçues
    TOTAL_TRADES_ANALYSED += 1
    if TOTAL_TRADES_ANALYSED % 500 == 0:
        print(f"[LIVE CHECK] Bot actif. {TOTAL_TRADES_ANALYSED} transactions analysées en tâche de fond...")

    if mint not in tracked_tokens:
        register_new_token({
            "mint": mint,
            "name": data.get("name", "Jeton"),
            "symbol": data.get("symbol", "MEME")
        })

    token_info = tracked_tokens[mint]
    if token_info["alerted"]:
        return

    # Calcul du Market Cap instantané
    mcap_sol_brut = safe_float(data.get("marketCapSol"))
    mcap_usd = mcap_sol_brut * SOL_PRICE_USD

    # 🎯 FILTRE DE LA COLONNE STRETCH (8k$ - 15k$)
    if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
        token_info["alerted"] = True  
        
        name = token_info["name"]
        symbol = token_info["symbol"]
        now = time.time()
        elapsed = now - token_info["created_at"]
        time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"

        print(f"🔥 [STRETCH DETECTED] {name} entre dans la zone Stretch ({mcap_usd:,.0f}$) ! Alerte Telegram...")

        message = (
            f"📈 <b>ZONING STRETCH (AXIOM MODE)</b> 📈\n\n"
            f"• <b>Nom :</b> {name} ({symbol})\n"
            f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
            f"• <b>Âge du Jeton :</b> <code>{time_str}</code> ⏱️\n\n"
            "📊 <b>Liens d'Entrée Rapide :</b>\n"
            f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
            f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n"
            f"• <a href='https://dexscreener.com/solana/{mint}'>Dexscreener</a>\n\n"
            "📥 <b>Adresse du Contrat (CA) :</b>\n"
            f"<code>{mint}</code>"
        )
        asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Initialisation du Sniper Mode STRETCH ===")
    
    await asyncio.sleep(2)
    asyncio.create_task(send_telegram_alert("📡 <b>Mode Stretch Axiom Actif</b> — Surveillance de la zone 8k$-15k$ avec indicateur d'activité...", is_test=True))

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Flux connecté. Scan Stretch en cours.")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    
                    tx_type = data.get("txType")
                    event_type = data.get("eventType")
                    
                    if tx_type == "create" or event_type == "create" or "message" in data:
                        if data.get("mint"):
                            register_new_token(data)
                    else:
                        if tx_type in ["buy", "sell"]:
                            analyser_trade_streaming(data)

        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] Connexion perdue, reconnexion dans 3s...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[WEBSOCKET ERREUR] -> {e}")
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
        "mode": "Alerte entrée colonne Stretch (8k$-15k$)",
        "tracked_tokens_count": len(tracked_tokens),
        "total_trades_processed": TOTAL_TRADES_ANALYSED
    }
