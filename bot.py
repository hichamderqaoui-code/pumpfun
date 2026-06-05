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

# Ajustement automatique au marché (SOL configuré sur Railway)
SOL_PRICE_USD      = float(os.getenv("SOL_PRICE_USD", "65.00"))  
MIGRATION_MCAP    = float(os.getenv("MIGRATION_USD", "27000.0"))  # Seuil de migration Raydium

# Zone d'entrée cible configurée via Railway (ex: 12000)
MAX_TRACK_MCAP    = float(os.getenv("TARGET_MCAP_USD", "12000.0"))  
MIN_TRACK_MCAP    = 7000.0   

# Filtres de qualité anti-spam
MIN_UNIQUE_TRADERS = int(os.getenv("MIN_HOLDERS", "5"))     # Sécurité traders uniques
MIN_TX_COUNT       = 15      # Minimum d'activité sur la curve
MAX_AGE_SECONDS    = int(os.getenv("MAX_AGE_SECONDS", "600"))    # Fenêtre max (ex: 10 minutes)

tracked_tokens = OrderedDict()
MAX_TRACKED = 1000

def safe_float(value, default=0.0) -> float:
    """Sécurise la conversion en float pour éviter les crashs sur payloads corrompus"""
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
        "symbol": data.get("symbol", "MEME"),
        "created_at": time.time(),
        "traders": set(),
        "alerted": False,
        "volume_usd": 0.0,
        "tx_count": 0,
        "buys": 0,
        "sells": 0
    }
    print(f"🆕 [WS CREATION] {data.get('name')} enregistré ({mint[:8]}...)")

async def send_telegram_alert(message: str, is_test: bool = False):
    token = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not token or not chat_id:
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
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=5.0)
    except Exception as e:
        print(f"[TELEGRAM ERREUR] -> {e}")

def analyser_trade_streaming(data: dict):
    mint = data.get("mint")
    if not mint:
        return

    # Auto-apprentissage : Si le trade arrive avant le message de création, on l'enregistre à la volée
    if mint not in tracked_tokens:
        register_new_token({
            "mint": mint,
            "name": data.get("name", "Unknown Jeton"),
            "symbol": data.get("symbol", "TOKEN")
        })

    token_info = tracked_tokens[mint]
    if token_info["alerted"]:
        return

    now = time.time()
    elapsed = now - token_info["created_at"]

    if elapsed > MAX_AGE_SECONDS:
        token_info["alerted"] = True
        return

    # Extraction sécurisée des données numériques pour éviter les crashs
    trader = data.get("traderPublicKey")
    sol_amount = safe_float(data.get("solAmount")) / 1e9  
    
    if trader:
        token_info["traders"].add(trader)
        
    token_info["tx_count"] += 1
    token_info["volume_usd"] += (sol_amount * SOL_PRICE_USD)

    tx_type = data.get("txType")
    if tx_type == "buy":
        token_info["buys"] += 1
    elif tx_type == "sell":
        token_info["sells"] += 1

    # Calcul fiable basé sur le marketCapSol natif de l'API de PumpPortal
    mcap_sol_brut = safe_float(data.get("marketCapSol"))
    mcap_usd = mcap_sol_brut * SOL_PRICE_USD
    
    unique_holders = len(token_info["traders"])

    # Log discret de streaming d'activité toutes les 5 transactions
    if token_info["tx_count"] % 5 == 0:
        print(f"⚡ [STREAM] {token_info['name'][:12]:<12} | MC: {mcap_usd:,.0f}$ | Traders: {unique_holders}")

    # 🎯 VALIDATION DE LA STRATÉGIE ET DES FILTRES
    if MIN_TRACK_MCAP <= mcap_usd <= MAX_TRACK_MCAP:
        if unique_holders >= MIN_UNIQUE_TRADERS and token_info["tx_count"] >= MIN_TX_COUNT:
            
            total_tx = token_info["buys"] + token_info["sells"]
            buy_ratio = token_info["buys"] / total_tx if total_tx > 0 else 0
            
            # Exigence de 65% de pression acheteuse pour éviter les rug/dumps rapides
            if buy_ratio < 0.65:
                return

            token_info["alerted"] = True
            
            name = token_info["name"]
            symbol = token_info["symbol"]
            progress_migration = (mcap_usd / MIGRATION_MCAP) * 100
            time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"
            potential_x = 100000.0 / mcap_usd if mcap_usd > 0 else 0

            print(f"🔥 [ALERTE] {name} validé ! Envoi Telegram...")

            message = (
                f"🚀 *PÉPITE DÉTECTÉE (CIBLE PRÉ-MIGRATION)* 🚀\n\n"
                f"• *Nom :* {name} ({symbol})\n"
                f"• *Market Cap Actuel :* `{mcap_usd:,.0f}$` 💰\n"
                f"• *Avancement Raydium :* `{progress_migration:.1f}%` (Seuil: {MIGRATION_MCAP:,.0f}$)\n"
                f"• *Acheteurs Uniques :* `{unique_holders} wallets` 👥\n"
                f"• *Pression Achat :* `{buy_ratio*100:.0f}% Buys` ({token_info['buys']}W / {token_info['sells']}L)\n"
                f"• *Volume Récent :* `{token_info['volume_usd']:,.0f}$`\n"
                f"• *Âge du Jeton :* `{time_str}` ⏱️\n"
                f"• *Potentiel Objectif 100K$ :* `x{potential_x:.1f}`\n\n"
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
    print("=== [BOT] Initialisation du Sniper Streaming WebSocket ===")
    
    # Message test envoyé au lancement
    await send_telegram_alert("⚡ *Sniper Streaming Actif* — Mode Surveillance global en cours...", is_test=True)

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Flux connecté. Écoute globale active.")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    
                    # Détection robuste selon les clés présentes dans le dictionnaire
                    # PumpPortal marque l'action de création par la clé "arguments" ou la méthode, ou l'absence de txType traditionnel
                    tx_type = data.get("txType")
                    event_type = data.get("eventType")
                    
                    if tx_type == "create" or event_type == "create" or "message" in data:
                        # Si c'est un message système ou de création pure
                        if data.get("mint"):
                            register_new_token(data)
                    else:
                        # Par défaut, si on reçoit un flux d'activité de transaction
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
        "market_mode": f"SOL Cible à {SOL_PRICE_USD}$",
        "tracked_tokens_count": len(tracked_tokens)
    }
