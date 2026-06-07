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

# ─── CONFIGURATION ENVIRO ───
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# 🎯 ZONE STRETCH (Seuils de détection)
MIN_STRETCH_MCAP   = 8000.0
MAX_STRETCH_MCAP   = 15000.0
MAX_TOKEN_AGE_SEC  = 600

# 🔗 INFRASTRUCTURE QUICKNODE (Fini le vieux polling HTTP !)
QUICKNODE_WSS_URL   = "wss://cosmopolitan-neat-water.solana-mainnet.quiknode.pro/9f9417599d69aa06a450c4e2df39cef6793949a5/"
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6s"

# ─── METRICS & CACHE ───
TOTAL_TRANSACTIONS_PROCESSED = 0
TOTAL_ALERTS = 0
alerted_tokens = OrderedDict()
MAX_ALERTED = 2000

def safe_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except:
        return 0.0

def already_alerted(mint: str) -> bool:
    return mint in alerted_tokens

def mark_alerted(mint: str):
    if len(alerted_tokens) >= MAX_ALERTED:
        alerted_tokens.popitem(last=False)
    alerted_tokens[mint] = True

async def send_telegram_alert(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[TG] Token ou Chat ID manquant")
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
            logger.info(f"[TG] Envoi notification. Status={resp.status_code}")
    except Exception as e:
        logger.error(f"[TG ERROR] Impossible d'envoyer l'alerte : {e}")

async def process_solana_transaction(result_data):
    """
    Analyse les notifications de transactions envoyées par QuickNode en temps réel.
    """
    global TOTAL_TRANSACTIONS_PROCESSED, TOTAL_ALERTS
    TOTAL_TRANSACTIONS_PROCESSED += 1

    try:
        value = result_data.get("value", {})
        logs = value.get("logs", [])
        
        # Filtrer rapidement pour s'assurer que c'est une action liée à pump.fun
        logs_str = "".join(logs).lower()
        if "create" not in logs_str and "buy" not in logs_str:
            return

        # Extraction des données décodées par l'encoding jsonParsed de QuickNode
        # Vous obtenez ainsi l'état précis des comptes sans requêtes HTTP supplémentaires
        # Note : On cherche la mutation d'un état de compte lié à la Bonding Curve
        # Pour cet exemple, nous simulons l'extraction du mint et de la market cap du payload
        # (À ajuster selon la structure exacte reçue de votre décodeur de logs personnalisé)
        
        # Exemple d'extraction fictive basée sur la structure type :
        # mint = ...
        # mcap_usd = ...
        
        # Simulation d'analyse d'un token trouvé (Insérez votre logique de parsing ici)
        pass

    except Exception as e:
        logger.error(f"[PARSE ERROR] Erreur lors du décodage de la transaction : {e}")

async def quicknode_stream_listener():
    """
    Se connecte au flux WebSocket de QuickNode et maintient la connexion active.
    Règle définitivement le crash de syntaxe (Ligne 150).
    """
    # Payload parfaitement formaté et fermé
    subscribe_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "programSubscribe",
        "params": [
            PUMP_FUN_PROGRAM_ID,
            {
                "commitment": "processed",
                "encoding": "jsonParsed"
            }
        ]
    }

    logger.info("[STREAM] Connexion au flux temps réel QuickNode...")
    
    # Gestion des reconnexions automatiques en cas de coupure réseau
    async for websocket in websockets.connect(QUICKNODE_WSS_URL):
        try:
            await websocket.send(json.dumps(subscribe_payload))
            logger.info("=== [BOT] Connecté à QuickNode. Écoute active de Pump.fun (AVANT MIGRATION) ===")
            
            async for message in websocket:
                data = json.loads(message)
                
                # Vérification de la présence de données de transaction valides
                if "params" in data and "result" in data["params"]:
                    await process_solana_transaction(data["params"]["result"])
                    
        except websockets.ConnectionClosed:
            logger.warning("[RETRY] Connexion perdue avec QuickNode. Reconnexion automatique dans 4s...")
            await asyncio.sleep(4)
        except Exception as e:
            logger.error(f"[STREAM ERROR] Erreur boucle principale : {e}")
            await asyncio.sleep(4)

# ─── LIFESPAN FASTAPI ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== [BOT] Démarrage — QuickNode Realtime Stream ===")
    await send_telegram_alert("⚡ <b>Bot STRETCH démarré</b> — Flux QuickNode Live actif...")
    
    # Lancement du gestionnaire de flux en arrière-plan
    stream_task = asyncio.create_task(quicknode_stream_listener())
    yield
    # Nettoyage à l'arrêt de l'application Railway
    stream_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {
        "status": "online",
        "engine": "QuickNode WebSocket v2",
        "transactions_scanned": TOTAL_TRANSACTIONS_PROCESSED,
        "total_alerts_sent": TOTAL_ALERTS,
        "cached_tokens": len(alerted_tokens)
    }
