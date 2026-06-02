import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets

# ─── CONFIGURATION DIRECTE ET SÉCURISÉE ───
TELEGRAM_TOKEN = "8659214495:AAGN0uPMlXfsybXfrPZlGCsmsCisIevNc_g"  # Ton token corrigé
TELEGRAM_CHAT_ID = "1532612243"  # Ton chat ID validé

# Stratégie : Détection précise autour de la zone cible de Market Cap
TARGET_MIN_MCAP = 9500     
TARGET_MAX_MCAP = 25000    # Augmenté à 25k$ pour ne rien rater des tokens vus sur Axiom

alerted_tokens = set()

async def send_telegram_alert(message: str, is_test: bool = False):
    """Gère l'envoi des notifications sur ton Telegram"""
    token = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    
    if not token or "METS_ICI" in token:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }
    
    if not is_test:
        payload["parse_mode"] = "Markdown"
        payload["disable_web_page_preview"] = True
    
    try:
        async with httpx.AsyncClient(verify=True) as client:
            response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code == 200:
                print("[TELEGRAM] ✅ Message envoyé avec succès sur ton Telegram.")
            else:
                print(f"[TELEGRAM] ❌ Erreur API HTTP {response.status_code} : {response.text}")
    except Exception as e:
        print(f"[TELEGRAM] ❌ Erreur système d'envoi : {e}")

def analyser_et_alerter(trade_data: dict):
    """Analyse les transactions du flux global en temps réel"""
    mint = trade_data.get("mint")
    if not mint or mint in alerted_tokens:
        return

    name = trade_data.get("name", "Unknown Token")
    symbol = trade_data.get("symbol", "MEME")
    
    # Calcul du Market Cap en USD (Basé sur le prix du SOL)
    mcap_sol = trade_data.get("marketCapSol", 0)
    mcap_usd = mcap_sol * 150  

    # Évaluation du filtre stratégique
    if TARGET_MIN_MCAP <= mcap_usd <= TARGET_MAX_MCAP:
        print(f"[BOT] 🎯 CRITÈRE VALIDE : {name} ({symbol}) est à {mcap_usd:,.0f}$ ! Envoi alerte...")
        
        alerted_tokens.add(mint)
        multiplier_to_100k = 100000 / mcap_usd if mcap_usd > 0 else 10

        message = (
            f"🎯 *MEMECOIN EN PLEIN PUMP (ZONE CIBLE)*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap Détecté :* `{mcap_usd:,.0f}$`\n"
            f"• *Potentiel objectif (100k$) :* `x{multiplier_to_100k:.1f}` (+{multiplier_to_100k*100:.0f}%)\n\n"
            f"📈 *Liens de Sniping direct :*\n"
            f"• [Photon Solana](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
            f"• [BullX Terminal](https://bullx.io/terminal?chain=solana&address={mint})\n"
            f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
            f"📥 *Adresse de contrat (CA) :*\n`{mint}`"
        )
        
        asyncio.create_task(send_telegram_alert(message, is_test=False))

async def solana_websocket_listener():
    """Moteur WebSocket v2 : Écoute globale ultra-rapide"""
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage du moteur de tracking global v2 ===")

    # Test au démarrage
    await send_telegram_alert("🚀 *Moteur v2 Actif* : Analyse globale des volumes lancée en temps réel !", is_test=True)

    while True:
        try:
            async with websockets.connect(uri) as websocket:
                print("[WEBSOCKET] ✅ Canal ouvert. Connexion au flux mondial de transactions...")
                
                # Abonnement direct au flux complet de TOUTES les transactions du site
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Surveillance globale active. Analyse des paires en cours...")

                async for message in websocket:
                    data = json.loads(message)
                    
                    # Si c'est une transaction contenant le Market Cap, on l'analyse directement
                    if "marketCapSol" in data:
                        analyser_et_alerter(data)
                        
        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Déconnexion du flux. Relancement dans 3 secondes...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Dysfonctionnement : {e}")
            await asyncio.sleep(3)

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
    return {"status": "active", "mode": "Global Volume Stream"}
