import asyncio
import json
import os
import time
import logging
import websockets
from telegram import Bot
from telegram.constants import ParseMode
from fastapi import FastAPI

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

PUMPFUN_WS_PRIMARY = "wss://pumpportal.fun/api/data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

async def process_token(event: dict) -> None:
    mint = event.get("mint")
    if not mint or mint in alerted_tokens: return

    alerted_tokens[mint] = time.time()
    
    # Message ultra-brut envoyé à la seconde zéro
    msg = f"""🚀 *FLUX BRUT PUMP.FUN DETECTÉ*
• *Nom :* {event.get('name', '?')}
• *Symbol :* {event.get('symbol', '?')}
• *Mint :* `{mint}`

🔗 [Pump.fun](https://pump.fun/{mint})"""
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
        log.info(f"✅ Alerte envoyée pour {event.get('symbol')}")
    except Exception as e:
        log.error(f"❌ Erreur d'envoi Telegram: {e}")

async def connect_pumpfun():
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Connecté au flux Pump.fun !")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            asyncio.create_task(process_token(data))
                    except: pass
        except Exception as e:
            log.error(f"Connexion perdue, reconnexion... {e}")
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    # Message de validation pour confirmer le déploiement
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚡ *Mode Diagnostic : Flux 100% ouvert sans aucun filtre !*")
    except: pass
    asyncio.create_task(connect_pumpfun())

@app.get("/")
async def root(): return {"status": "online"}
