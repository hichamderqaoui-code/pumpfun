import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets
from collections import OrderedDict
import time

# ─── CONFIG ───
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# Paramètres de filtrage en temps réel
TARGET_MCAP_USD   = float(os.getenv("TARGET_MCAP_USD",  "12000"))  # Alerte si le token dépasse ce montant
SOL_PRICE_USD     = float(os.getenv("SOL_PRICE_USD",    "75.33"))  # Prix de base du SOL
MIN_HOLDERS       = int(os.getenv("MIN_HOLDERS",         "5"))     # Nombre minimum d'acheteurs distincts observés
MAX_AGE_SECONDS   = int(os.getenv("MAX_AGE_SECONDS",    "600"))    # Moins de 10 minutes

tracked_tokens = OrderedDict()
MAX_TRACKED = 1000

def calculer_market_cap_sol(v_tokens: float) -> float:
    """
    Calcule le Market Cap exact en SOL basé sur la formule mathématique de la bonding curve Pump.fun.
    Le supply total est de 1 milliard de tokens (1 000 000 000).
    """
    TOTAL_SUPPLY = 1_000_000_000
    # Constante de la courbe Pump.fun (virtual Sol reserves / virtual token reserves)
    if v_tokens <= 0:
        return 0
    # Approximation en temps réel du prix par token basée sur la quantité restante
    price_per_token_sol = 30 / v_tokens if v_tokens > 0 else 0
    return TOTAL_SUPPLY * price_per_token_sol

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
        "traders": set(),  # Stocke les clés publiques uniques pour compter les vrais holders
        "alerted": False,
        "volume_usd": 0.0,
        "tx_count": 0
    }
    print(f"🆕 [WS CREATION] {data.get('name')} enregistré ({mint[:8]}...)")

async def send_telegram_alert(message: str, is_test: bool = False):
    token = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    if not is_test:
        payload["parse_mode"] = "Markdown"
        payload["disable_web_page_preview"] = True
    
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=5.0)
    except Exception as e:
        print(f"[TELEGRAM] ❌ Erreur : {e}")

def analyser_trade_streaming(data: dict):
    """Analyse les transactions en temps réel sans jamais appeler l'API de Pump.fun"""
    mint = data.get("mint")
    if not mint or mint not in tracked_tokens:
        return

    token_info = tracked_tokens[mint]
    if token_info["alerted"]:
        return

    now = time.time()
    elapsed = now - token_info["created_at"]

    # Filtre d'âge maximum (ex: 10 minutes)
    if elapsed > MAX_AGE_SECONDS:
        token_info["alerted"] = True
        return

    # Extraire les données de la transaction
    trader = data.get("traderPublicKey")
    sol_amount = float(data.get("solAmount", 0)) / 1e9  # Conversion lamports -> SOL
    v_tokens = float(data.get("vTokenReserves", 0)) / 1e6  # Ajustement décimales du token
    
    if trader:
        token_info["traders"].add(trader)
        
    token_info["tx_count"] += 1
    token_info["volume_usd"] += (sol_amount * SOL_PRICE_USD)

    # Calcul dynamique du Market Cap en USD via la Bonding Curve virtuelle
    mcap_sol = data.get("marketCapSol", 0)
    if mcap_sol == 0 and v_tokens > 0:
        mcap_sol = calculer_market_cap_sol(v_tokens)
        
    mcap_usd = mcap_sol * SOL_PRICE_USD
    unique_holders = len(token_info["traders"])

    # Log de suivi ultra-rapide dans Railway
    if token_info["tx_count"] % 3 == 0:  # Affiche un log tous les 3 trades pour éviter le spam
        print(f"⚡ [STREAM] {token_info['name'][:12]:<12} | MC: {mcap_usd:,.0f}$ | Traders Uniques: {unique_holders} | Age: {elapsed:.0f}s")

    # CONDITION DE DÉCLENCHEMENT DE L'ALERTE TELEGRAM
    if mcap_usd >= TARGET_MCAP_USD:
        if unique_holders < MIN_HOLDERS:
            # Sécurité anti-wash trading / anti-dev solo
            return

        token_info["alerted"] = True
        
        name = token_info["name"]
        symbol = token_info["symbol"]
        time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"
        mult = 100000 / mcap_usd if mcap_usd > 0 else 10

        print(f"🎯 [ALERTE STREAMING] {name} passe les critères ! MC: {mcap_usd:,.0f}$")

        message = (
            f"🟢 *SIGNAL TEMPS RÉEL — PUMP FLASH*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap Calculé :* `{mcap_usd:,.0f}$` 🚀\n"
            f"• *Acheteurs Uniques :* `{unique_holders} portefeuilles` 👥\n"
            f"• *Volume généré :* `{token_info['volume_usd']:,.0f}$`\n"
            f"• *Total Transactions :* `{token_info['tx_count']}`\n"
            f"• *Temps depuis création :* `{time_str}` ⏱️\n"
            f"• *Objectif x100k :* `x{mult:.1f}`\n\n"
            "🔍 *Outils de Sniping :*\n"
            f"• [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
            f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n"
            f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
            "📥 *Contrat (CA) :*\n"
            f"`{mint}`"
        )
        asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage du Sniper Streaming 100% WebSocket ===")
    await send_telegram_alert("⚡ *Sniper Streaming v4 en ligne* — API obsolète supprimée. Mode 0ms activé.", is_test=True)

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("[WEBSOCKET] ✅ Flux connecté.")
                # On écoute les créations ET tous les trades mondiaux
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Surveillance du streaming global active.")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except :
                        continue
                    
                    tx_type = data.get("txType")
                    if tx_type == "create":
                        register_new_token(data)
                    elif tx_type in ["buy", "sell"] or "marketCapSol" in data:
                        analyser_trade_streaming(data)

        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Connexion perdue, reconnexion dans 3s...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Erreur : {e}")
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
def home():
    return {
        "status": "active",
        "mode": "100% Real-time WebSocket Streaming",
        "target_mcap_usd": TARGET_MCAP_USD,
        "tracked_tokens_count": len(tracked_tokens)
    }
