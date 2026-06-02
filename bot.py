import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets
from collections import OrderedDict
import time

# ─── CONFIGURATION DIRECTE ───
TELEGRAM_TOKEN = "8659214495:AAGN0uPMlXfsybXfrPZlGCsmsCisIevNc_g"
TELEGRAM_CHAT_ID = "1532612243"

# Stratégie de franchissement
TARGET_MCAP_USD = 10000
SOL_PRICE_USD = 79.22        # Fixé selon tes indications
MAX_AGE_SECONDS = 600        # 10 minutes maximum pour atteindre l'objectif

# Dictionnaire de suivi : { mint: {'name': name, 'symbol': symbol, 'created_at': timestamp, 'alerted': False} }
tracked_new_tokens = OrderedDict()
MAX_TRACKED = 3000

def register_new_token(data: dict):
    """Enregistre le token et son heure exacte de naissance"""
    mint = data.get("mint")
    if not mint:
        return
    
    if len(tracked_new_tokens) >= MAX_TRACKED:
        tracked_new_tokens.popitem(last=False)
        
    tracked_new_tokens[mint] = {
        "name": data.get("name", "Unknown"),
        "symbol": data.get("symbol", "MEME"),
        "created_at": time.time(),  # Timestamp de création (bloc 0)
        "alerted": False
    }
    print(f"🆕 [NOUVEAU TOKEN] {data.get('name')} enregistré.")

async def send_telegram_alert(message: str, is_test: bool = False):
    """Gère l'envoi des notifications Telegram"""
    token = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not token or "METS_ICI" in token:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    if not is_test:
        payload["parse_mode"] = "Markdown"
        payload["disable_web_page_preview"] = True
    
    try:
        async with httpx.AsyncClient(verify=True) as client:
            await client.post(url, json=payload, timeout=10.0)
    except Exception as e:
        print(f"[TELEGRAM] ❌ Erreur envoi : {e}")

def analyser_transaction(trade_data: dict):
    """Filtre et alerte UNIQUEMENT si le cap des 10k$ est franchi en < 10 min"""
    mint = trade_data.get("mint")
    
    # On ignore si le token n'est pas dans notre historique récent ou s'il a déjà alerté
    if not mint or mint not in tracked_new_tokens or tracked_new_tokens[mint]["alerted"]:
        return

    token_info = tracked_new_tokens[mint]
    now = time.time()
    elapsed_time = now - token_info["created_at"]

    # ❌ CONDITION ULTRA-STRICTE : Si le token a dépassé les 10 minutes, on l'ignore et on peut le tagguer pour ne plus l'analyser
    if elapsed_time > MAX_AGE_SECONDS:
        token_info["alerted"] = True # On feinte le système pour bloquer toute future analyse inutile de ce token
        return

    mcap_sol = trade_data.get("marketCapSol", 0)
    mcap_usd = mcap_sol * SOL_PRICE_USD

    # Dès qu'il touche ou dépasse 10k$ (et qu'il a moins de 10 minutes de vie)
    if mcap_usd >= TARGET_MCAP_USD:
        token_info["alerted"] = True  # One-shot alerte
        
        name = token_info["name"]
        symbol = token_info["symbol"]
        
        minutes = int(elapsed_time // 60)
        seconds = int(elapsed_time % 60)
        time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

        multiplier_to_100k = 100000 / mcap_usd if mcap_usd > 0 else 10
        percentage_gain = (multiplier_to_100k - 1) * 100

        print(f"🎯 [BOUGIE EXPLOSIVE] {name} passe les 10k$ en {time_str}! ({mcap_usd:,.0f}$)")

        message = (
            f"⚡ *PUMP EXPLOSIF (< 10 MIN)*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap Atteint :* `{mcap_usd:,.0f}$`\n"
            f"• *Temps depuis création :* `{time_str}` ⏱️\n"
            f"• *Potentiel vers 100k$ :* `x{multiplier_to_100k:.1f}` (+{percentage_gain:,.0f}%)\n\n"
            f"📈 *Liens de Sniping direct :*\n"
            f"• [Photon Solana](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
            f"• [BullX Terminal](https://bullx.io/terminal?chain=solana&address={mint})\n"
            f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
            f"📥 *Adresse de contrat (CA) :*\n`{mint}`"
        )
        asyncio.create_task(send_telegram_alert(message, is_test=False))

async def solana_websocket_listener():
    """Moteur double écoute : Créations + Trades"""
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage du Sniper Chrono 10 min ===")
    await send_telegram_alert(f"⏱️ *Sniper Chrono Actif* : Cible 10k$ en moins de 10 min lancé. SOL: {SOL_PRICE_USD}$.", is_test=True)

    while True:
        try:
            async with websockets.connect(uri) as websocket:
                print("[WEBSOCKET] ✅ Connecté.")
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Surveillance active.")

                async for message in websocket:
                    data = json.loads(message)
                    tx_type = data.get("txType")

                    if tx_type == "create":
                        register_new_token(data)
                    elif "marketCapSol" in data:
                        analyser_transaction(data)
                        
        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Reconnexion dans 2 secondes...")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Erreur : {e}")
            await asyncio.sleep(2)

# --- LIFESPAN FASTAPI ---
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
    return {"status": "active", "mode": "Chrono 10min Sniper"}
