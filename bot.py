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
    Analyse chaque transaction en temps réel pour traquer l'évolution du Market Cap
    """
    mint = trade_data.get("mint")
    if not mint or mint in alerted_tokens:
        return

    name = trade_data.get("name", "Unknown Token")
    symbol = trade_data.get("symbol", "MEME")
    
    # Récupération et calcul du Market Cap en USD (Prix du SOL indicatif à 150$)
    mcap_sol = trade_data.get("marketCapSol", 0)
    mcap_usd = mcap_sol * 150  

    # Ligne de log systématique pour voir le stream défiler en direct
    print(f"[STREAM] Token: {name} ({symbol}) | MCap actuel : {mcap_usd:,.0f}$")

    # Vérification de ta stratégie (Fenêtre autour des 10k$)
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
    """Se connecte au flux complet des transactions Pumpportal"""
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage du moteur WebSocket (Analyse des flux) ===")

    while True:
        try:
            async with websockets.connect(uri) as websocket:
                print("[WEBSOCKET] ✅ Canal ouvert. Injection des filtres de souscription...")
                
                # Écoute des transactions en direct
                await websocket.send(json.dumps({"method": "subscribeTokenTrade"}))
                print("[WEBSOCKET] 📡 Flux de tracking des prix activé H24.")

                async for message in websocket:
                    data = json.loads(message)
                    if "mint" in data and "marketCapSol" in data:
                        analyser_et_alerter(data)
                        
        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Déconnexion du flux. Relancement dans 5 secondes...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Alerte dysfonctionnement : {e}")
            await asyncio.sleep(5)

# ─── ATTRIBUT CRUCIAL REQUIS PAR UVICORN / RAILWAY ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(solana_websocket_listener())
    yield
    bot_task.cancel()
    await bot_task

# C'est cette variable exacte "app" qui manquait dans ton code !
app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {"status": "active", "mode": "Full Trade Stream Monitoring"}
