import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets

# ─── REMPLACE DIRECTEMENT ICI AVEC TES VRAIES INFOS ───
TELEGRAM_TOKEN = "8659214495:AAGN0uPMlXfsybXfrPZlGCsmsCisIevNc_g"  # Mets ton token complet ici entre les guillemets
TELEGRAM_CHAT_ID =  1532612243 # Mets ton chat ID ici (sans guillemets si c'est un nombre, ou avec)

# Stratégie : Détection précise autour de la zone des 10k$ de Market Cap
TARGET_MIN_MCAP = 9500     
TARGET_MAX_MCAP = 15000    

alerted_tokens = set()

async def send_telegram_alert(message: str):
    """Gère l'envoi des notifications sur ton Telegram"""
    # Utilisation prioritaire des chaînes en dur configurées au-dessus
    token = TELEGRAM_TOKEN if "TON_TOKEN" not in TELEGRAM_TOKEN else os.getenv("TELEGRAM_TOKEN")
    chat_id = TELEGRAM_CHAT_ID if "TON_CHAT" not in TELEGRAM_CHAT_ID else os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or "TON_TOKEN" in token:
        print("[TELEGRAM] ❌ Erreur : Le Token Telegram n'est pas configuré dans le code !")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=8.0)
            if response.status_code != 200:
                print(f"[TELEGRAM] ❌ Erreur API HTTP {response.status_code}: {response.text}")
            else:
                print("[TELEGRAM] ✅ Message de notification envoyé avec succès !")
    except Exception as e:
        print(f"[TELEGRAM] ❌ Erreur critique de connexion : {e}")

def analyser_et_alerter(trade_data: dict):
    """Analyse chaque transaction en temps réel reçue pour les tokens suivis"""
    mint = trade_data.get("mint")
    if not mint or mint in alerted_tokens:
        return

    name = trade_data.get("name", "Unknown Token")
    symbol = trade_data.get("symbol", "MEME")
    
    mcap_sol = trade_data.get("marketCapSol", 0)
    mcap_usd = mcap_sol * 150  

    print(f"[STREAM] {name} ({symbol}) | MCap actuel : {mcap_usd:,.0f}$")

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
    """Moteur WebSocket hybride"""
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage du moteur WebSocket hybride ===")

    # Test immédiat au démarrage
    test_msg = "🚀 *Test Système* : Si tu lis ce message, la liaison Telegram de ton bot fonctionne parfaitement !"
    asyncio.create_task(send_telegram_alert(test_msg))

    while True:
        try:
            async with websockets.connect(uri) as websocket:
                print("[WEBSOCKET] ✅ Canal ouvert. Activation du flux des créations...")
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                print("[WEBSOCKET] 📡 Surveillance globale initialisée.")

                async for message in websocket:
                    data = json.loads(message)
                    mint = data.get("mint")
                    
                    if mint:
                        if "marketCapSol" not in data or data.get("txType") == "create" or "uri" in data:
                            print(f"[NEW TOKEN] Découverte de : {data.get('name', '???')} ({mint}) -> Tracking activé.")
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
