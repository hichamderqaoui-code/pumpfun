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

# Seuil déclencheur unique réclamé
MIN_TRACK_MCAP    = 7000.0   

tracked_tokens = OrderedDict()
MAX_TRACKED = 2000  # Capacité augmentée pour suivre le flux global sans saturation

def safe_float(value, default=0.0) -> float:
    """Sécurise les conversions numériques contre les payloads d'API corrompus"""
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
        print("[TELEGRAM] Identifiants manquants dans les variables d'environnement.")
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
                print(f"[TELEGRAM ERREUR API] Code {response.status_code} -> {response.text}")
    except Exception as e:
        print(f"[TELEGRAM ERREUR CONNEXION] -> {e}")

def analyser_trade_streaming(data: dict):
    mint = data.get("mint")
    if not mint:
        return

    # Mécanisme d'auto-apprentissage si le trade devance le flux de création pure
    if mint not in tracked_tokens:
        register_new_token({
            "mint": mint,
            "name": data.get("name", "Jeton Inconnu"),
            "symbol": data.get("symbol", "MEME")
        })

    token_info = tracked_tokens[mint]
    if token_info["alerted"]:
        return

    # Calcul temps réel calqué sur le marketCapSol natif de PumpPortal
    mcap_sol_brut = safe_float(data.get("marketCapSol"))
    mcap_usd = mcap_sol_brut * SOL_PRICE_USD

    # 🎯 TRIGGER STRATÉGIQUE : Alerte immédiate dès le passage des 7 000 $
    if mcap_usd >= MIN_TRACK_MCAP:
        token_info["alerted"] = True  # Verrouillage pour éviter les doublons d'alertes
        
        name = token_info["name"]
        symbol = token_info["symbol"]
        now = time.time()
        elapsed = now - token_info["created_at"]
        time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"

        print(f"🔥 [ALERTE SEUIL] {name} a franchi les 7000$ ({mcap_usd:,.0f}$) ! Envoi Telegram...")

        message = (
            f"🎯 <b>SEUIL DES 7,000$ ATTEINT</b> 🎯\n\n"
            f"• <b>Nom :</b> {name} ({symbol})\n"
            f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
            f"• <b>Temps écoulé depuis création :</b> <code>{time_str}</code> ⏱️\n\n"
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
    print("=== [BOT] Initialisation du Sniper 7000$ MCAP ===")
    
    # Étape 1 : Temporisation réseau pour laisser le conteneur Railway s'établir complètement
    await asyncio.sleep(2)
    
    # Étape 2 : Envoi asynchrone découplé pour ne pas bloquer le démarrage de la boucle infinie
    asyncio.create_task(send_telegram_alert("⚡ <b>Sniper 7000$ MCAP Actif</b> — Écoute globale en cours...", is_test=True))

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Flux connecté. Analyse de tous les jetons active.")

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
        "mode": "Alerte immédiate au passage des 7000$",
        "tracked_tokens_count": len(tracked_tokens)
    }
