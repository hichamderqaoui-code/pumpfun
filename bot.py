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

# 🎯 STRATÉGIE DE FILTRAGE CIBLE
MIN_STRETCH_MCAP  = 8000.0   
MAX_STRETCH_MCAP  = 15000.0  
MAX_TOKEN_AGE_SEC = 1200     # 20 minutes

# 🔗 CONFIGURATION QUICKNODE ( logsSubscribe )
QUICKNODE_WSS_URL   = "wss://cosmopolitan-neat-water.solana-mainnet.quiknode.pro/9f9417599d69aa06a450c4e2df39cef6793949a5/"
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6s"

# ─── MÉMOIRE & STATISTIQUES ───
TOTAL_TRANSACTIONS_PROCESSED = 0
TOTAL_ALERTS = 0
alerted_tokens = OrderedDict()
MAX_ALERTED = 2000

def already_alerted(mint: str) -> bool:
    return mint in alerted_tokens

def mark_alerted(mint: str):
    if len(alerted_tokens) >= MAX_ALERTED:
        alerted_tokens.popitem(last=False)
    alerted_tokens[mint] = True

async def send_telegram_alert(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[TG] Configuration Telegram manquante.")
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
            logger.info(f"[TG] Notification envoyée (Status={resp.status_code})")
    except Exception as e:
        logger.error(f"[TG ERROR] Échec envoi Telegram : {e}")

async def fetch_pump_fun_data(mint: str):
    """
    Interroge l'API de Pump.fun pour récupérer les vraies métriques du jeton.
    """
    url = f"https://frontend-api.pump.fun/coins/{mint}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=3.0)
            if response.status_code == 200:
                data = response.json()
                usd_mcap = data.get("usd_market_cap")
                created_timestamp = data.get("created_timestamp", 0) / 1000 
                
                if usd_mcap is None:
                    market_cap_sol = data.get("market_cap", 0)
                    usd_mcap = market_cap_sol * 170.0 
                
                return float(usd_mcap), created_timestamp
    except Exception as e:
        # L'API peut mettre quelques millisecondes à indexer le token lors du Create
        pass
    return None, None

async def extract_mint_from_tx(signature: str) -> str:
    """
    Si les logs contiennent une activité Pump.fun, on utilise une requête RPC 
    rapide pour extraire le Mint exact de la transaction (méthode 100% fiable).
    """
    # Remplacement de l'adresse wss par l'adresse http correspondante de ton QuickNode
    rpc_url = QUICKNODE_WSS_URL.replace("wss://", "https://")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(rpc_url, json=payload, timeout=3.0)
            if response.status_code == 200:
                res_json = response.json()
                result = res_json.get("result", {})
                if not result:
                    return None
                
                # Scan des comptes de jetons post-transaction
                meta = result.get("meta", {})
                if meta:
                    post_token_balances = meta.get("postTokenBalances", []) or []
                    for balance in post_token_balances:
                        mint = balance.get("mint")
                        if mint and mint.endswith("pump"):
                            return mint
    except Exception as e:
        logger.error(f"[RPC ERROR] Impossible de récupérer la TX {signature}: {e}")
    return None

async def process_solana_logs(result_data):
    """
    Traite le flux reçu par logsSubscribe
    """
    global TOTAL_TRANSACTIONS_PROCESSED, TOTAL_ALERTS
    TOTAL_TRANSACTIONS_PROCESSED += 1

    try:
        value = result_data.get("value", {})
        logs = value.get("logs", [])
        signature = value.get("signature")

        if not logs or not signature:
            return

        logs_str = "".join(logs)
        
        # Détection immédiate d'une action sur Pump.fun
        if "Instruction: Create" in logs_str or "Instruction: Buy" in logs_str:
            logger.info(f"[⚡ ACTION DETECTÉE] Analyse de la TX: {signature}")
            
            # Récupération du Mint
            mint = await extract_mint_from_tx(signature)
            
            if not mint or already_alerted(mint):
                return

            # Vérification des specs sur l'API de Pump.fun
            mcap_usd, created_time = await fetch_pump_fun_data(mint)
            if mcap_usd is None or created_time == 0:
                return
                
            now = time.time()
            age_sec = now - created_time

            # Filtre de temps (20 minutes max)
            if age_sec > MAX_TOKEN_AGE_SEC:
                return

            # Filtre de Market Cap (8k$ - 15k$)
            if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
                mark_alerted(mint)
                TOTAL_ALERTS += 1
                
                time_str = f"{int(age_sec // 60)}m {int(age_sec % 60)}s" if age_sec >= 60 else f"{age_sec:.0f}s"
                logger.info(f"🎯 TRADING ZONE DETECTÉE : {mint} | MCAP: {mcap_usd:,.0f}$")
                
                msg = (
                    f"🎯 <b>MOONSHOT FILTRÉ (8k-15k)</b> 🎯\n\n"
                    f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                    f"• <b>Âge actuel :</b> <code>{time_str}</code> ⏱️\n"
                    f"• <b>Cible Migration :</b> 26.8k$ 🚀\n\n"
                    f"📊 <b>Analyseurs :</b>\n"
                    f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                    f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
                    f"📥 <b>CA :</b> <code>{mint}</code>"
                )
                asyncio.create_task(send_telegram_alert(msg))

    except Exception as e:
        logger.error(f"[PROCESS ERROR] {e}")

async def quicknode_stream_listener():
    """
    Abonnement au flux via logsSubscribe, filtré directement au niveau de QuickNode
    sur le programme Pump.fun pour ne pas surcharger la bande passante.
    """
    subscribe_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [
            {"mentions": [PUMP_FUN_PROGRAM_ID]},
            {"commitment": "processed"}
        ]
    }

    logger.info("[STREAM] Initialisation du flux de logs QuickNode...")
    
    async for websocket in websockets.connect(QUICKNODE_WSS_URL):
        try:
            await websocket.send(json.dumps(subscribe_payload))
            logger.info("=== [BOT] Connecté au flux logsSubscribe de QuickNode. Écoute active ===")
            
            async for message in websocket:
                data = json.loads(message)
                if "params" in data and "result" in data["params"]:
                    await process_solana_logs(data["params"]["result"])
                    
        except websockets.ConnectionClosed:
            logger.warning("[RETRY] Flux déconnecté. Reconnexion dans 4s...")
            await asyncio.sleep(4)
        except Exception as e:
            logger.error(f"[STREAM ERROR] {e}")
            await asyncio.sleep(4)

# ─── LIFESPAN FASTAPI ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== [START] Démarrage du Sniper Stratégique v4 ===")
    await send_telegram_alert("🚀 <b>Bot Filtre Stratégique v4 En Ligne</b> — Écoute par logsSubscribe activée.")
    stream_task = asyncio.create_task(quicknode_stream_listener())
    yield
    stream_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {
        "status": "online",
        "zone_cible": f"{MIN_STRETCH_MCAP}$ à {MAX_STRETCH_MCAP}$",
        "tx_traitees": TOTAL_TRANSACTIONS_PROCESSED,
        "alertes_envoyees": TOTAL_ALERTS
    }
