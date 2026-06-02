import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets

# Configuration des accès (à remplir dans l'onglet 'Variables' sur Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "TON_TOKEN_BOT")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "TON_CHAT_ID")

# Stratégie : Détection précise autour de la zone des 10k$ de Market Cap
TARGET_MIN_MCAP = 9500     
TARGET_MAX_MCAP = 15000    

alerted_tokens = set()

async def send_telegram_alert(message: str):
    """Gère l'envoi des notifications sur ton Telegram"""
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
        print(f"[TELEGRAM] ❌ Erreur de connexion : {e}")

def analyser_et_alerter(trade_data: dict):
    """
    Analyse chaque transaction en temps réel reçue pour les tokens suivis
    """
    mint = trade_data.get("mint")
    if not mint or mint in alerted_tokens:
        return

    name = trade_data.get("name", "Unknown Token")
    symbol = trade_data.get("symbol", "MEME")
    
    # Calcul du Market Cap en USD (Prix du SOL estimé à 150$)
    mcap_sol = trade_data.get("marketCapSol", 0)
    mcap_usd = mcap_sol * 150  

    # Affiche l'activité de transaction dans les logs Railway
    print(f"[STREAM] {name} ({symbol}) | MCap actuel : {mcap_usd:,.0f}$")

    # Évaluation du filtre stratégique (zone des 10k$)
    if TARGET_MIN_MCAP <= mcap_usd <= TARGET_MAX_MCAP:
        print(f"[BOT] 🎯 CRITÈRE VALIDÉ : {name} entre dans la zone cible ! ({mcap_usd:,.0f}$)")
        
        alerted_tokens.add(mint)
        multiplier_to_100k = 100000 / mcap_usd if mcap_usd > 0 else 10

        message = (
            f"🎯 *MEMECOIN EN PLEIN PUMP (CIBLE ~10k$)*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap Détecté :* `{mcap_usd:,.0f}$`\n"
            f"• *Potentiel objectif (100k$) :* `x{multiplier_to_100k:.1f}` (+{multiplier_to_100k*100:.0f}%)\n\n"
            f"📈 *Liens de Sniping direct :*\n"
            f"• [Photon Solana](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
            f"• [BullX Terminal](https://bullx.io/terminal?chain=solana&address={mint})\n"
            f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
            f"📥 *Adresse de contrat (CA) :*\n`{mint}`"
        )
        
        asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    """
    Moteur WebSocket hybride avec message de test de connexion Telegram
    """
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage du moteur WebSocket hybride ===")

    # 🔥 PIPELINE DE TEST : Envoi immédiat d'un ping sur Telegram au redémarrage
    test_msg = "🚀 *Lancement du Bot Solana réussi !* Le moteur hybride tourne. En attente d'un token qui franchit les 10k$..."
    asyncio.create_task(send_telegram_alert(test_msg))

    while True:
        try:
            async with websockets.connect(uri) as websocket:
                print("[WEBSOCKET] ✅ Canal ouvert. Activation du flux des créations...")
                
                # Écoute globale de chaque nouveau jeton créé
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                print("[WEBSOCKET] 📡 Surveillance globale initialisée.")

                async for message in websocket:
                    data = json.loads(message)
                    mint = data.get("mint")
                    
                    if mint:
                        if "marketCapSol" not in data or data.get("txType") == "create" or "uri" in data:
                            # Tracking automatique activé
                            await websocket.send(json.dumps({
                                "method": "subscribeTokenTrade",
                                "keys": [mint]
                            }))
                        
                        elif "marketCapSol" in data:
                            analyser_et_alerter(data)
                        
        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Déconnexion du flux. Relancement dans 5 secondes...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Alerte dysfonctionnement : {e}")
            await asyncio.sleep(5)

# --- LIFESPAN FASTAPI POUR RAILWAY ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(solana_websocket_listener())
    yield
    bot_task.cancel()
    await bot_task

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {"status": "active", "mode": "Hybrid Stream"}
