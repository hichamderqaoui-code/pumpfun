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

# 🎯 TA STRATÉGIE DE FILTRAGE STRICTE
MIN_STRETCH_MCAP  = 8000.0   # Minimum 8k$ de Market Cap
MAX_STRETCH_MCAP  = 15000.0  # Maximum 15k$ de Market Cap
MAX_TOKEN_AGE_SEC = 1200     # Jeton ignoré après 20 minutes (20 * 60)

# 🔗 CONFIGURATION FLUX QUICKNODE
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
    Interroge l'API frontend de Pump.fun pour obtenir la MCAP réelle
    et les informations de création du jeton de manière fiable.
    """
    url = f"https://frontend-api.pump.fun/coins/{mint}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=3.0)
            if response.status_code == 200:
                data = response.json()
                
                # Calcul de la Market Cap en USD (basé sur le solde virtuel ou usd_market_cap fourni)
                usd_mcap = data.get("usd_market_cap")
                created_timestamp = data.get("created_timestamp", 0) / 1000 # Converti en secondes
                
                if usd_mcap is None:
                    # Fallback si le champ usd_market_cap est absent
                    market_cap_sol = data.get("market_cap", 0)
                    usd_mcap = market_cap_sol * 170.0 # Indexé sur un prix moyen du SOL
                
                return float(usd_mcap), created_timestamp
    except Exception as e:
        logger.error(f"[API ERROR] Erreur lors de la récupération des infos pour {mint} : {e}")
    return None, None

async def process_solana_transaction(result_data):
    """
    Analyse le stream de transactions et utilise l'API pour valider la stratégie.
    """
    global TOTAL_TRANSACTIONS_PROCESSED, TOTAL_ALERTS
    TOTAL_TRANSACTIONS_PROCESSED += 1

    try:
        value = result_data.get("value", {})
        meta = value.get("meta", {})
        logs = value.get("logs", [])
        
        if not logs or (meta and meta.get("err") is not None):
            return

        logs_str = "".join(logs)
        
        # On intercepte les logs clairs d'interactions avec Pump.fun
        if "Instruction: Buy" in logs_str or "Instruction: Create" in logs_str:
            
            # Extraction du Mint depuis les clés de comptes du dictionnaire de transaction
            mint = None
            
            # Méthode d'extraction de secours si postTokenBalances est vide
            # Le Mint est toujours présent dans les innerInstructions ou dans la liste des comptes modifiés
            post_token_balances = meta.get("postTokenBalances", []) if meta else []
            for balance in post_token_balances:
                token_address = balance.get("mint")
                if token_address and token_address.endswith("pump"):
                    mint = token_address
                    break

            # Si non trouvé dans les balances, on essaie via les staticAccountKeys du message
            if not mint and "transaction" in value:
                message = value["transaction"].get("message", {})
                account_keys = message.get("accountKeys", [])
                for acc in account_keys:
                    if isinstance(acc, str) and acc.endswith("pump") and acc != PUMP_FUN_PROGRAM_ID:
                        mint = acc
                        break
            
            if not mint or already_alerted(mint):
                return

            # Étape de validation via l'API Rapide
            mcap_usd, created_time = await fetch_pump_fun_data(mint)
            
            if mcap_usd is None or created_time == 0:
                return # Si l'API ne répond pas encore, on attend le prochain bloc
                
            now = time.time()
            age_sec = now - created_time

            # 1. Filtre sur l'âge maximal (20 minutes)
            if age_sec > MAX_TOKEN_AGE_SEC:
                return

            # 2. Filtre sur ta tranche de Market Cap (8k$ - 15k$)
            if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
                mark_alerted(mint)
                TOTAL_ALERTS += 1
                
                time_str = f"{int(age_sec // 60)}m {int(age_sec % 60)}s" if age_sec >= 60 else f"{age_sec:.0f}s"
                logger.info(f"🎯 TRADING ZONE DETECTÉE : {mint} | MCAP: {mcap_usd:,.0f}$ | Âge: {time_str}")
                
                msg = (
                    f"🎯 <b>MOONSHOT FILTRÉ (8k-15k)</b> 🎯\n\n"
                    f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                    f"• <b>Âge actuel :</b> <code>{time_str}</code> ⏱️\n"
                    f"• <b>Statut :</b> Avant Migration (26.8k$) 🚀\n\n"
                    f"📊 <b>Outils de trading :</b>\n"
                    f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                    f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
                    f"📥 <b>CA :</b> <code>{mint}</code>"
                )
                asyncio.create_task(send_telegram_alert(msg))

    except Exception as e:
        logger.error(f"[PARSE ERROR] {e}")

async def quicknode_stream_listener():
    """
    Connexion au flux WebSocket QuickNode.
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

    logger.info("[STREAM] Initialisation du flux QuickNode...")
    
    async for websocket in websockets.connect(QUICKNODE_WSS_URL):
        try:
            await websocket.send(json.dumps(subscribe_payload))
            logger.info("=== [BOT] Connecté à QuickNode. Écoute active (Tranche : 8k-15k$) ===")
            
            async for message in websocket:
                data = json.loads(message)
                if "params" in data and "result" in data["params"]:
                    await process_solana_transaction(data["params"]["result"])
                    
        except websockets.ConnectionClosed:
            logger.warning("[RETRY] Déconnexion du flux. Reconnexion automatique dans 4s...")
            await asyncio.sleep(4)
        except Exception as e:
            logger.error(f"[STREAM ERROR] {e}")
            await asyncio.sleep(4)

# ─── LIFESPAN FASTAPI ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== [START] Démarrage du Sniper Stratégique v3 ===")
    await send_telegram_alert("🚀 <b>Bot Filtre Stratégique v3 Actif</b> — En attente des tokens entre 8k$ et 15k$ (âge max 20m).")
    stream_task = asyncio.create_task(quicknode_stream_listener())
    yield
    stream_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {
        "status": "online",
        "zone_cible": f"{MIN_STRETCH_MCAP}$ à {MAX_STRETCH_MCAP}$",
        "age_max": "20 minutes",
        "tx_traitees": TOTAL_TRANSACTIONS_PROCESSED,
        "alertes_envoyees": TOTAL_ALERTS
    }
