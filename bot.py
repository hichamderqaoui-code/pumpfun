import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets  # Pense à ajouter 'websockets' dans ton requirements.txt

# Configurer ces variables dans l'onglet "Variables" sur Railway
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "TON_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "TON_CHAT_ID")

# Configuration de la stratégie (Entrée autour de 10k$ de Market Cap)
TARGET_MIN_MCAP = 9000     
TARGET_MAX_MCAP = 15000    

async def send_telegram_alert(message: str):
    """Envoie l'alerte sur ton canal Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=5.0)
            if response.status_code != 200:
                print(f"[TELEGRAM] ❌ Erreur API: {response.text}")
    except Exception as e:
        print(f"[TELEGRAM] ❌ Erreur réseau : {e}")

def analyser_et_alerter(token_data: dict):
    """
    Analyse le token en temps réel dès qu'un trade ou une création atteint notre cible
    """
    name = token_data.get("name", "Unknown")
    symbol = token_data.get("symbol", "MEME")
    mint = token_data.get("mint")
    
    # Calcul du Market Cap approximatif en USD sur Pump.fun 
    # (Formule basée sur le Virtual Sol Reserves ou le bonding curve de l'event)
    mcap_sol = token_data.get("marketCapSol", 0)
    # On prend une valeur indicative du cours du SOL (ex: 150$), à dynamiser si besoin
    mcap_usd = mcap_sol * 150 

    # ÉTAPE 1 : Log de suivi dans Railway pour TOUTES les paires qui bougent
    print(f"[ANALYSE] 🔍 {name} ({symbol}) | MCap : {mcap_usd:,.0f}$")

    # ÉTAPE 2 : Filtre de la stratégie (Fenêtre des 10k$)
    if TARGET_MIN_MCAP <= mcap_usd <= TARGET_MAX_MAX_MCAP:
        print(f"[BOT] 🎯 STRATÉGIE VALIDÉE POUR {name} ({mcap_usd:,.0f}$) ! Envoi Telegram...")
        
        multiplier_to_100k = 100000 / mcap_usd if mcap_usd > 0 else 10
        
        message = (
            f"🎯 *MEMECOIN DETECTÉ (CIBLE ~10k$ MCAP)*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap Actuel :* `{mcap_usd:,.0f}$`\n"
            f"• *Objectif de Sortie (100k$) :* `+{multiplier_to_100k*100:.0f}%` (x{multiplier_to_100k:.1f})\n\n"
            f"📈 *Liens de Trade Rapide :*\n"
            f"• [Photon Solana](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
            f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n"
            f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
            f"📥 *Adresse du Token :*\n`{mint}`"
        )
        
        # Lancer l'envoi en tâche de fond pour ne pas bloquer le WebSocket
        asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    """
    Se connecte au flux WebSocket de Pumpportal pour écouter les trades et créations
    """
    uri = "wss://pumpportal.fun/api/data"
    
    print("=== [BOT] Initialisation du flux WebSocket Solana ===")
    await send_telegram_alert("🚀 *Le bot d'analyse WebSocket Pump.fun (Stratégie 10k$) est en ligne !*")

    while True:
        try:
            print("[WEBSOCKET] Connexion au serveur de flux...")
            async with websockets.connect(uri) as websocket:
                print("[WEBSOCKET] ✅ Connecté ! Souscription aux événements...")
                
                # On s'abonne aux nouveaux tokens ET aux trades pour suivre l'évolution du Market Cap
                subscribe_message = {
                    "method": "subscribeNewToken"
                }
                await websocket.send(json.dumps(subscribe_message))
                
                # Optionnel : si tu veux aussi suivre l'évolution vers 10k des tokens existants, 
                # on peut s'abonner aux trades, mais attention au volume important de données.
                # subscribe_trades = {"method": "subscribeTokenTrade"}
                # await websocket.send(json.dumps(subscribe_trades))

                # Boucle d'écoute des messages
                async for message in websocket:
                    data = json.loads(message)
                    
                    # On s'assure que c'est un événement de token valide
                    if "mint" in data:
                        analyser_et_alerter(data)
                        
        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Connexion perdue. Tentative de reconnexion dans 5 secondes...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Erreur rencontrée : {e}")
            await asyncio.sleep(5)

# --- INITIALISATION COMPATIBLE RAILWAY (FASTAPI) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Lancement du thread WebSocket au démarrage de Railway
    bot_task = asyncio.create_task(solana_websocket_listener())
    yield
    bot_task.cancel()
    await bot_task

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {"status": "running", "mode": "WebSocket Pumpportal"}
