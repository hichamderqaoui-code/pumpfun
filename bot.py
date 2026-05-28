"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — RAILWAY + HELIUS         ║
║   Zéro Doublon + Retry Helius Synchro | Top 10 < 29%         ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import aiohttp
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
HELIUS_API_KEY   = os.environ["HELIUS_API_KEY"]

MAX_TOP10_HOLD_PCT    = 29         # Seuil strict Axiom Pro
PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
HELIUS_RPC            = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# RECHERCHE AVEC SYSTÈME DE RETRY POUR HELIUS
# ══════════════════════════════════════════════════════════════

async def fetch_helius_holders_with_retry(session: aiohttp.ClientSession, mint: str, retries=3) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
    
    for attempt in range(retries):
        try:
            async with session.post(HELIUS_RPC, json=payload, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    # Si Helius renvoie une erreur RPC (ex: token pas encore indexé), on attend et on retry
                    if "error" in data:
                        await asyncio.sleep(1)
                        continue
                        
                    accounts = data.get("result", {}).get("value", [])
                    if accounts:
                        total_supply = sum(float(a.get("uiAmount", 0) or 0) for a in accounts)
                        if total_supply > 0:
                            top10_pct = sum(float(a.get("uiAmount", 0) or 0) for a in accounts[:10]) / total_supply * 100
                            return {"holder_count": len(accounts), "top10_pct": round(top10_pct, 1), "ok": True}
        except Exception as e:
            log.debug(f"Tentative {attempt+1} échouée pour {mint[:8]}: {e}")
        
        if attempt < retries - 1:
            await asyncio.sleep(1) # Attendre 1 seconde avant la prochaine tentative
            
    return {"holder_count": 0, "top10_pct": 100, "ok": False}

# ══════════════════════════════════════════════════════════════
# TRAITEMENT SANS DOUBLON
# ══════════════════════════════════════════════════════════════

async def process_token(session: aiohttp.ClientSession, event: dict) -> None:
    mint = event.get("mint")
    if not mint: return

    # BARRIÈRE ANTI-DOUBLON IMMÉDIATE (Bloque les requêtes simultanées du WS)
    if mint in alerted_tokens:
        return
    alerted_tokens[mint] = time.time()

    # On attend 4 secondes pour laisser le temps au bloc de se confirmer
    await asyncio.sleep(4)

    # Récupération des holders avec le système de retry adaptatif
    holders = await fetch_helius_holders_with_retry(session, mint, retries=3)
    top10_pct = holders.get("top10_pct", 100)

    if top10_pct > MAX_TOP10_HOLD_PCT:
        log.info(f"❌ Bloqué : {event.get('symbol')} | Top 10 à {top10_pct}% (Seuil : {MAX_TOP10_HOLD_PCT}%)")
        return

    msg = f"""🚀 *TOKEN VALIDÉ (<29% Top 10)* — `{event.get('symbol', '?')}`
`{mint}`

━━━━━━━━━━━━━━━━━━━━━
👥 *HOLDERS (Données Réelles)*
├ Nb Wallets  : *{holders.get('holder_count', '?')}*
└ 🎯 Top 10 Hold : *{top10_pct}%* 

━━━━━━━━━━━━━━━━━━━━━
🔗 [Pump.fun](https://pump.fun/{mint})"""
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.error(f"Erreur Telegram: {e}")

# ══════════════════════════════════════════════════════════════
# BOUCLE DE CONNEXION
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Connecté au flux Pump.fun !")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            asyncio.create_task(process_token(session, data))
                    except: pass
        except Exception as e:
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    connector = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(connector=connector) as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
