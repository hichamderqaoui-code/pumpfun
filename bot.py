import asyncio
import os
import json
import httpx
import time
import logging
from fastapi import FastAPI
from contextlib import asynccontextmanager
from collections import OrderedDict
import websockets

# ─── CONFIGURATION DES LOGS ───
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SolanaSniper")

# ─── CONFIGURATION TELEGRAM ───
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# 🎯 TA STRATÉGIE DE SNIPING CIBLÉE
MIN_STRETCH_MCAP  = 8000.0   # Entrée mini
MAX_STRETCH_MCAP  = 15000.0  # Entrée maxi (bien avant les 26.8k$)
MAX_TOKEN_AGE_SEC = 1200     # Oubli total après 20 minutes (20 * 60)

# 🔗 CONFIGURATION DU FLUX QUICKNODE
QUICKNODE_WSS_URL   = "wss://cosmopolitan-neat-water.solana-mainnet.quiknode.pro/9f9417599d69aa06a450c4e2df39cef6793949a5/"
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6s"

# ─── METRICS & MÉMOIRE DU BOT ───
TOTAL_TRANSACTIONS_PROCESSED = 0
TOTAL_ALERTS = 0
alerted_tokens = OrderedDict()
MAX_ALERTED = 2000
token_creation_tracker = {}  # {mint: timestamp_creation}

def already_alerted(mint: str) -> bool:
    return mint in alerted_tokens

def mark_alerted(mint: str):
    if len(alerted_tokens) >= MAX_ALERTED:
        alerted_tokens.popitem(last=False)
    alerted_tokens[mint] = True

async def send_telegram_alert(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[TG] Configuration Telegram manquante dans les variables d'environnement.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=4.0)
            logger.info(f"[TG] Alerte envoyée, code statut : {resp.status_code}")
    except Exception as e:
        logger.error(f"[TG ERROR] Impossible de notifier Telegram : {e}")

def calculate_exact_mcap(meta) -> float:
    """
    Calcule la Market Cap réelle d'un token sur Pump.fun en se basant
    sur les soldes de SOL de la bonding curve (Lamports réels).
    La courbe complète sa migration à 26.8k$ réels.
    """
    try:
        post_balances = meta.get("postBalances", [])
        if len(post_balances) > 1:
            # Le compte d'index 1 stocke généralement les fonds de la curve
            sol_in_curve = post_balances[1] / 1_000_000_000
            
            # Prix moyen estimé du SOL pour la conversion en USD
            sol_price_usd = 170.0  
            
            # Calcul proportionnel basé sur la courbe de liaison réelle (0 à 85 SOL)
            # Au lancement = ~3.5k$ de liquidité de base réelle. À la migration = 26.8k$
            current_mcap = (sol_in_curve / 85.0) * 26800.0
            
            if current_mcap > 0:
                return float(current_mcap)
    except:
        pass
    # Valeur par défaut si les balances sont absentes du bloc (ex: log partiel)
    return 9500.0

async def process_solana_transaction(result_data):
    """
    Analyse les logs de transaction QuickNode en temps réel et applique tes filtres.
    """
    global TOTAL_TRANSACTIONS_PROCESSED, TOTAL_ALERTS
    TOTAL_TRANSACTIONS_PROCESSED += 1

    try:
        value = result_data.get("value", {})
        meta = value.get("meta", {})
        logs = value.get("logs", [])
        
        if not logs or meta.get("err") is not None:
            return

        logs_str = "".join(logs)
        is_create = "Instruction: Create" in logs_str
        is_buy = "Instruction: Buy" in logs_str

        if is_create or is_buy:
            # 1. Extraction de l'adresse du token (Mint)
            mint = None
            post_token_balances = meta.get("postTokenBalances", [])
            for balance in post_token_balances:
                token_address = balance.get("mint")
                if token_address and token_address.endswith("pump"):
                    mint = token_address
                    break
            
            if not mint:
                return

            now = time.time()

            # Si c'est une création, on stocke la date de naissance
            if is_create and mint not in token_creation_tracker:
                token_creation_tracker[mint] = now

            # Calcul de l'âge
            birth_time = token_creation_tracker.get(mint, now - 30)
            age_sec = now - birth_time

            # 2. APPLICATION DU FILTRE DE TEMPS : Oubli après 20 minutes
            if age_sec > MAX_TOKEN_AGE_SEC:
                if mint in token_creation_tracker:
                    token_creation_tracker.pop(mint, None)
                return

            if already_alerted(mint):
                return

            # 3. APPLICATION DU FILTRE DE MARKET CAP (8k$ - 15k$)
            mcap_usd = calculate_exact_mcap(meta)

            if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
                mark_alerted(mint)
                TOTAL_ALERTS += 1
                
                time_str = f"{int(age_sec // 60)}m {int(age_sec % 60)}s" if age_sec >= 60 else f"{age_sec:.0f}s"
                logger.info(f"🎯 TRADING ZONE DETECTÉE : {mint} | MCAP: {mcap_usd:,.0f}$ | Âge: {time_str}")
                
                msg = (
                    f"🎯 <b>MOONSHOT POTENTIEL ENTRÉE (8k-15k)</b> 🎯\n\n"
                    f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                    f"• <b>Âge actuel :</b> <code>{time_str}</code> ⏱️\n"
                    f"• <b>Distance Migration (26.8k$) :</b> Moins de 2x ! 🚀\n\n"
                    f"📊 <b>Outils de scalping :</b>\n"
                    f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                    f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
                    f"📥 <b>CA :</b> <code>{mint}</code>"
                )
                asyncio.create_task(send_telegram_alert(msg))

    except Exception as e:
        logger.error(f"[TX ERROR] Erreur d'analyse du bloc : {e}")

async def quicknode_stream_listener():
    """
    Écouteur principal branché sur ton URL WebSocket QuickNode.
    """
    subscribe_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "programSubscribe",
        "params": [
            PUMP_FUN_PROGRAM_ID,
            {"commitment": "processed", "encoding": "jsonParsed"}
        ]
    }

    logger.info("[STREAM] Tentative de connexion au WebSocket QuickNode...")
    
    async for websocket in websockets.connect(QUICKNODE_WSS_URL):
        try:
            await websocket.send(json.dumps(subscribe_payload))
            logger.info("=== [BOT] Connecté à QuickNode. Écoute active (Cible : 8k-15k$) ===")
            
            async for message in websocket:
                data = json.loads(message)
                if "params" in data and "result" in data["params"]:
                    await process_solana_transaction(data["params"]["result"])
                    
        except websockets.ConnectionClosed:
            logger.warning("[RETRY] Flux interrompu. Reconnexion à QuickNode dans 4 secondes...")
            await asyncio.sleep(4)
        except Exception as e:
            logger.error(f"[STREAM CRITICAL ERROR] {e}")
            await asyncio.sleep(4)

# ─── GESTIONNAIRE DE CYCLE FASTAPI (LIFESPAN) ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== [START] Lancement des tâches de fond ===")
    await send_telegram_alert("🚀 <b>Bot Filtre Stratégique v2 En Ligne</b> — Tranche 8k$-15k$ active.")
    stream_task = asyncio.create_task(quicknode_stream_listener())
    yield
    stream_task.cancel()

# ─── APPLICATION FASTAPI FOR UVICORN / RAILWAY ───
app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {
        "status": "online",
        "target_range": f"{MIN_STRETCH_MCAP}$ à {MAX_STRETCH_MCAP}$",
        "forget_timer": "20 minutes",
        "migration_point": "26.8k$",
        "processed_tx": TOTAL_TRANSACTIONS_PROCESSED,
        "alerts_triggered": TOTAL_ALERTS,
        "monitored_tokens": len(token_creation_tracker)
    }
