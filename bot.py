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
TARGET_MIN_MCAP = 9500     # Entrée dès que le volume pousse vers 10k
TARGET_MAX_MCAP = 15000    # Limite haute pour éviter d'entrer au sommet d'une bougie

# Dictionnaire pour éviter d'envoyer plusieurs alertes pour le même token s'il oscille autour de 10k
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
    Analyse chaque transaction en temps réel pour traquer l'évolution du Market Cap
    """
    mint = trade_data.get("mint")
    if not mint or mint in alerted_tokens:
        return

    name = trade_data.get("name", "Unknown Token")
    symbol = trade_data.get("symbol", "MEME")
    
    # Calcul du Market Cap Dynamique (Basé sur le prix en SOL fourni par Pumpportal)
    # Formule standard de valorisation Pump.fun : marketCapSol * Prix du SOL (~150$)
    mcap_sol = trade_data.get("marketCapSol", 0)
    mcap_usd = mcap_sol * 150 

    # Étape 1 : Logs de suivi légers pour voir l'activité globale du marché
    # (Tu verras ici les valeurs monter de 4k à 6k, puis 8k...)
    if mcap_usd > 5000:
        print(f"[VOLUME] 🔥 Activity on {name} ({symbol}) | MCap actuel : {mcap_usd:,.0f}$")

    # Étape 2 : Vérification du déclencheur de ta stratégie (La zone des 10k$)
    if TARGET_MIN_MCAP <= mcap_usd <= TARGET_MAX_MCAP:
        print(f"[BOT] 🎯 CRITÈRE VALIDÉ : {name} vient de franchir la zone cible ! ({mcap_usd:,.0f}$)")
        
        # Sécurité pour ne pas spammer si le prix fait du surplace à 10k$
        alerted_tokens.add(mint)
        
        # Calcul du multiplicateur théorique restant pour atteindre ton objectif de 100k$
        multiplier_to_100k = 100000 / mcap_usd if mcap_usd > 0 else 10

        # Construction de l'alerte ultra-rapide avec outils de sniping (Photon / BullX)
        message = (
            f"🎯 *MEMECOIN EN PLEIN PUMP (CIBLE ~10k$)*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap Détecté :* `{mcap_usd:,.0f}$`\n"
            f"• *Potentiel jusqu'à l'objectif (100k$) :* `x{multiplier_to_100k:.1f}` (+{multiplier_to_100k*100:.0f}%)\n\n"
            f"📈 *Liens de Sniping direct :*\n"
            f"• [Photon Solana](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
            f"• [BullX Terminal](https://bullx.io/terminal?chain=solana&address={mint})\n"
            f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
            f"📥 *Adresse de contrat (CA) :*\n`{mint}`"
        )
        
        # Exécution asynchrone immédiate en tâche de fond pour garder le WebSocket synchrone
        asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    """
    Maintenant connecté au flux complet des transactions pour traquer les hausses de prix
    """
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage du moteur WebSocket (Analyse des flux) ===")

    while True:
        try:
            async with websockets.connect(uri) as websocket:
                print("[WEBSOCKET] ✅ Canal ouvert. Injection des filtres de souscription...")
                
                # Écoute des transactions en direct sur Pump.fun
                await websocket.send(json.dumps({"method": "subscribeTokenTrade"}))
                print("[WEBSOCKET] 📡 Flux de tracking des prix activé H24.")

                async for message in websocket:
                    data = json.loads(message)
                    
                    # On filtre pour s'assurer que c'est un événement lié à un trade valide
                    if "mint" in data and "marketCapSol" in data:
                        analyser_et_alerter(data)
                        
        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Déconnexion du flux. Relancement dans 5 secondes...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Alerte dysfonctionnement : {e}")
            await asyncio.sleep(5)

# --- CONFIGURATION INTERFACE COMPATIBLE PRODUCTION RAILWAY ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Lancement automatique de la boucle d'analyse au déploiement de Railway
    bot_task = asyncio.create_task(solana_websocket_listener())
    yield
    bot_task.cancel()
    await bot_task

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {"status": "active", "mode": "Full Trade Stream Monitoring"}
