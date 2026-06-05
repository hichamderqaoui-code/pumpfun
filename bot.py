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

# Compteur global de messages reçus
TOTAL_MESSAGES_RECEIVED = 0

tracked_tokens = OrderedDict()
MAX_TRACKED = 3000  # Augmenté pour stocker plus de tokens en mémoire

def safe_float(value, default=0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def register_new_token(mint: str, name: str = "Unknown", symbol: str = "TOKEN"):
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
            response = await client.post(url, json=payload, timeout=5.0)
            if response.status_code != 200:
                print(f"[TELEGRAM ERREUR API] -> {response.text}")
    except Exception as e:
        print(f"[TELEGRAM ERREUR CONNEXION] -> {e}")

def analyser_trade_streaming(data: dict):
    mint = data.get("mint")
    if not mint:
        return

    # Si le token n'est pas encore suivi, on l'enregistre immédiatement
    if mint not in tracked_tokens:
        register_new_token(
            mint, 
            name=data.get("name", "Jeton"), 
            symbol=data.get("symbol", "MEME")
        )

    token_info = tracked_tokens[mint]
    if token_info["alerted"]:
        return

    # Calcul du Market Cap instantané
    mcap_sol_brut = safe_float(data.get("marketCapSol"))
    mcap_usd = mcap_sol_brut * SOL_PRICE_USD

    # 🎯 VERIFICATION DE LA ZONE STRETCH
    if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
        token_info["alerted"] = True  
        
        name = token_info["name"]
        symbol = token_info["symbol"]
        now = time.time()
        elapsed = now - token_info["created_at"]
        time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"

        print(f"🔥 [STRETCH DETECTED] {name} est dans la colonne Stretch ({mcap_usd:,.0f}$) !")

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
    global TOTAL_MESSAGES_RECEIVED
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Initialisation du Sniper Mode STRETCH ===")
    
    await asyncio.sleep(2)
    asyncio.create_task(send_telegram_alert("📡 <b>Mode Stretch Axiom Actif</b> — Surveillance en temps réel de la zone 8k$-15k$."))

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                # Souscriptions aux flux de PumpPortal
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Flux connecté. Écoute globale démarrée.")

                async for message in websocket:
                    TOTAL_MESSAGES_RECEIVED += 1
                    
                    # Log de contrôle immédiat toutes les 100 données reçues du réseau
                    if TOTAL_MESSAGES_RECEIVED % 100 == 0:
                        print(f"[LIVE CHECK] Flux actif : {TOTAL_MESSAGES_RECEIVED} messages traités par le bot...")

                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    
                    # Interception des créations et des transactions
                    tx_type = data.get("txType")
                    event_type = data.get("eventType")
                    mint = data.get("mint")
                    
                    if mint:
                        if tx_type == "create" or event_type == "create":
                            register_new_token(
                                mint, 
                                name=data.get("name", "Unknown"), 
                                symbol=data.get("symbol", "TOKEN")
                            )
                        else:
                            # Traitement de toutes les actions d'achat/vente
                            analyser_trade_streaming(data)

        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] Connexion interrompue, reconnexion dans 3s...")
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
        "mode": "Alerte colonne Stretch Axiom (8k$-15k$)",
        "total_messages_processed": TOTAL_MESSAGES_RECEIVED,
        "tracked_tokens_count": len(tracked_tokens)
    }
