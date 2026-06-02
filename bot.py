import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets
from collections import OrderedDict

# ─── CONFIGURATION DIRECTE ───
TELEGRAM_TOKEN = "8659214495:AAGN0uPMlXfsybXfrPZlGCsmsCisIevNc_g"
TELEGRAM_CHAT_ID = "1532612243"

# Stratégie de franchissement des 10k$
TARGET_MCAP_USD = 10000
SOL_PRICE_USD = 150.0  # À ajuster selon le marché

# On garde en mémoire uniquement les tokens récents (max 1000) pour ne pas saturer le script
# Structure : { mint: {'name': name, 'symbol': symbol, 'alerted': False} }
tracked_new_tokens = OrderedDict()
MAX_TRACKED = 1000

def register_new_token(data: dict):
    """Enregistre le token dès sa création sur Pump.fun"""
    mint = data.get("mint")
    if not mint:
        return
    
    # Si le dictionnaire dépasse la taille max, on vire le plus ancien
    if len(tracked_new_tokens) >= MAX_TRACKED:
        tracked_new_tokens.popitem(last=False)
        
    tracked_new_tokens[mint] = {
        "name": data.get("name", "Unknown"),
        "symbol": data.get("symbol", "MEME"),
        "alerted": False
    }
    print(f"🆕 [NOUVEAU TOKEN] {data.get('name')} ({data.get('symbol')}) enregistré.")

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
            response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code != 200:
                print(f"[TELEGRAM] ❌ Erreur API : {response.text}")
    except Exception as e:
        print(f"[TELEGRAM] ❌ Erreur envoi : {e}")

def analyser_transaction(trade_data: dict):
    """Analyse le flux et check si un NOUVEAU token franchit les 10k$"""
    mint = trade_data.get("mint")
    
    # On traite UNIQUEMENT si c'est un token récent qu'on a vu naître et qui n'a pas encore alerté
    if not mint or mint not in tracked_new_tokens or tracked_new_tokens[mint]["alerted"]:
        return

    mcap_sol = trade_data.get("marketCapSol", 0)
    mcap_usd = mcap_sol * SOL_PRICE_USD

    # Dès qu'il touche ou dépasse 10k$
    if mcap_usd >= TARGET_MCAP_USD:
        tracked_new_tokens[mint]["alerted"] = True  # One-shot alerte
        
        token_info = tracked_new_tokens[mint]
        name = token_info["name"]
        symbol = token_info["symbol"]
        
        multiplier_to_100k = 100000 / mcap_usd if mcap_usd > 0 else 10
        percentage_gain = (multiplier_to_100k - 1) * 100

        print(f"🎯 [CIBLE ATTEINTE] {name} vient de passer les 10k$ ! ({mcap_usd:,.0f}$)")

        message = (
            f"🚀 *NOUVELLE PAIRE PASSE LES 10K$*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap Actuel :* `{mcap_usd:,.0f}$`\n"
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
    print("=== [BOT] Démarrage du Sniper de nouvelles paires ===")
    await send_telegram_alert("⚡ *Sniper Actif* : Surveillance exclusive des nouvelles paires franchissant 10k$.", is_test=True)

    while True:
        try:
            async with websockets.connect(uri) as websocket:
                print("[WEBSOCKET] ✅ Connecté.")
                
                # 1. S'abonner aux créations de tokens
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                # 2. S'abonner aux trades mondiaux
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                
                print("[WEBSOCKET] 📡 Écoute double flux activée.")

                async for message in websocket:
                    data = json.loads(message)
                    tx_type = data.get("txType")

                    if tx_type == "create":
                        # C'est une création -> on stocke le token
                        register_new_token(data)
                    elif "marketCapSol" in data:
                        # C'est un trade -> on vérifie s'il passe les 10k
                        analyser_transaction(data)
                        
        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Déconnexion. Reconnexion dans 2 secondes...")
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
    return {"status": "active", "mode": "New Pairs 10k Tracker"}
