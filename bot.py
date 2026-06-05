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

# Ajustement automatique au marché bas (SOL à 65$)
SOL_PRICE_USD     = float(os.getenv("SOL_PRICE_USD", "65.00"))  
MIGRATION_MCAP    = 27000.0  # Seuil de migration Raydium à 65$ / SOL

# Zone d'entrée cible (Autour de 10k$)
MIN_TRACK_MCAP    = 7000.0   
MAX_TRACK_MCAP    = 15000.0  

# Filtres de qualité anti-spam
MIN_UNIQUE_TRADERS = 8       # Élimine les Devs qui wash-tradent en solo
MIN_TX_COUNT       = 15      # Minimum d'activité sur la curve
MAX_AGE_SECONDS    = 900     # Le token doit avoir moins de 15 minutes

tracked_tokens = OrderedDict()
MAX_TRACKED = 1000

def register_new_token(data: dict):
    mint = data.get("mint")
    if not mint or mint in tracked_tokens:
        return
    
    if len(tracked_tokens) >= MAX_TRACKED:
        tracked_tokens.popitem(last=False)
        
    tracked_tokens[mint] = {
        "name": data.get("name", "Unknown"),
        "symbol": data.get("symbol", "MEME"),
        "created_at": time.time(),
        "traders": set(),
        "alerted": False,
        "volume_usd": 0.0,
        "tx_count": 0,
        "buys": 0,
        "sells": 0
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
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=5.0)
    except Exception as e:
        print(f"[TELEGRAM ERREUR] -> {e}")

def analyser_trade_streaming(data: dict):
    mint = data.get("mint")
    if not mint or mint not in tracked_tokens:
        return

    token_info = tracked_tokens[mint]
    if token_info["alerted"]:
        return

    now = time.time()
    elapsed = now - token_info["created_at"]

    if elapsed > MAX_AGE_SECONDS:
        token_info["alerted"] = True
        return

    # Extraction des données du trade
    trader = data.get("traderPublicKey")
    sol_amount = float(data.get("solAmount", 0)) / 1e9  
    v_sol = float(data.get("vSolReserves", 0)) / 1e9
    
    if trader:
        token_info["traders"].add(trader)
        
    token_info["tx_count"] += 1
    token_info["volume_usd"] += (sol_amount * SOL_PRICE_USD)

    if data.get("txType") == "buy":
        token_info["buys"] += 1
    elif data.get("txType") == "sell":
        token_info["sells"] += 1

    # Calcul du Market Cap exact basé sur les réserves virtuelles en SOL
    mcap_usd = v_sol * SOL_PRICE_USD if v_sol > 0 else (float(data.get("marketCapSol", 0)) * SOL_PRICE_USD)
    unique_holders = len(token_info["traders"])

    # 🎯 FILTRAGE LOGIQUE POUR SÉLECTIONNER UNIQUEMENT LES MEILLEURES PIÈCES
    if MIN_TRACK_MCAP <= mcap_usd <= MAX_TRACK_MCAP:
        if unique_holders >= MIN_UNIQUE_TRADERS and token_info["tx_count"] >= MIN_TX_COUNT:
            
            total_tx = token_info["buys"] + token_info["sells"]
            buy_ratio = token_info["buys"] / total_tx if total_tx > 0 else 0
            
            # On exige au moins 65% de pression acheteuse
            if buy_ratio < 0.65:
                return

            token_info["alerted"] = True
            
            name = token_info["name"]
            symbol = token_info["symbol"]
            progress_migration = (mcap_usd / MIGRATION_MCAP) * 100
            time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"
            
            # Calcul du multiplicateur pour atteindre ton objectif de 100k$
            potential_x = 100000.0 / mcap_usd

            print(f"🔥 [SIGNAL] {name} validé ! MC: {mcap_usd:,.0f}$")

            message = (
                f"🚀 *PÉPITE DÉTECTÉE (CIBLE ~10K)* 🚀\n\n"
                f"• *Nom :* {name} ({symbol})\n"
                f"• *Market Cap Actuel :* `{mcap_usd:,.0f}$` 💰\n"
                f"• *Avancement Raydium :* `{progress_migration:.1f}%` (Cible: {MIGRATION_MCAP:,.0f}$)\n"
                f"• *Acheteurs Uniques :* `{unique_holders} wallets` 👥\n"
                f"• *Pression Achat :* `{buy_ratio*100:.0f}% Buys` ({token_info['buys']}W / {token_info['sells']}L)\n"
                f"• *Volume :* `{token_info['volume_usd']:,.0f}$`\n"
                f"• *Âge du Jeton :* `{time_str}` ⏱️\n"
                f"• *Objectif 100K$ :* `x{potential_x:.1f}` potentiel à la sortie\n\n"
                "📊 *Liens d'Entrée Rapide :*\n"
                f"• [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
                f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n"
                f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
                "📥 *Adresse du Contrat (CA) :*\n"
                f"`{mint}`"
            )
            asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Initialisation du flux global PumpPortal ===")
    
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Écoute en direct des créations et des volumes...")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except:
                        continue
                    
                    tx_type = data.get("txType")
                    if tx_type == "create" or data.get("eventType") == "create":
                        register_new_token(data)
                    else:
                        analyser_trade_streaming(data)

        except websockets.exceptions.ConnectionClosed:
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
        "market_mode": "SOL Low Market (65$)",
        "tracked_tokens": len(tracked_tokens)
    }
