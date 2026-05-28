"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — RAILWAY + HELIUS         ║
║     Filtre Top 10 < 29% (Axiom Pro) + Pause Synchro          ║
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

# ──────────────────────────────────────────────────────────────
# CONFIG (variables Railway / .env)
# ──────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
HELIUS_API_KEY   = os.environ["HELIUS_API_KEY"]

# ── TON FILTRE STRICT ────────────────────────────────────────
MAX_TOP10_HOLD_PCT    = 29         # Limite stricte Axiom Pro
WAIT_BEFORE_CHECK_SEC = 5          # Pause nécessaire pour laisser Helius indexer le token

# ── ENDPOINTS ──────────────────────────────────────────────
PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
HELIUS_RPC            = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# RECHERCHE DES HOLDERS (HELIUS)
# ══════════════════════════════════════════════════════════════

async def fetch_helius_holders(session: aiohttp.ClientSession, mint: str) -> dict:
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
        async with session.post(HELIUS_RPC, json=payload, timeout=6) as r:
            if r.status == 200:
                data = await r.json()
                accounts = data.get("result", {}).get("value", [])
                if accounts:
                    total_supply = sum(float(a.get("uiAmount", 0) or 0) for a in accounts)
                    if total_supply > 0:
                        top10_pct = sum(float(a.get("uiAmount", 0) or 0) for a in accounts[:10]) / total_supply * 100
                        return {"holder_count": len(accounts), "top10_pct": round(top10_pct, 1), "ok": True}
    except Exception as e:
        log.debug(f"Erreur Helius pour {mint[:8]}: {e}")
    # Si Helius échoue ou ne trouve rien, on renvoie 100% par sécurité pour ne pas spammer un token potentiellement dangereux
    return {"holder_count": 0, "top10_pct": 100, "ok": False}

# ══════════════════════════════════════════════════════════════
# TRAITEMENT DU TOKEN
# ══════════════════════════════════════════════════════════════

async def process_token(session: aiohttp.ClientSession, event: dict) -> None:
    mint = event.get("mint")
    if not mint or mint in alerted_tokens: return

    # ÉTAPE CLÉ : On attend que le token soit bien déployé partout
    await asyncio.sleep(WAIT_BEFORE_CHECK_SEC)

    holders = await fetch_helius_holders(session, mint)
    top10_pct = holders.get("top10_pct", 100)

    # Filtrage strict
    if top10_pct > MAX_TOP10_HOLD_PCT:
        log.info(f"❌ Bloqué : {event.get('symbol')} a un Top 10 à {top10_pct}% (Seuil : {MAX_TOP10_HOLD_PCT}%)")
        return

    alerted_tokens[mint] = time.time()
    
    msg = f"""🚀 *TOKEN VALIDÉ (<29% Top 10)* — `{event.get('symbol', '?')}`
`{mint}`

━━━━━━━━━━━━━━━━━━━━━
👥 *HOLDERS*
├ Nb Wallets  : *{holders.get('holder_count', '?')}*
└ 🎯 Top 10 Hold : *{top10_pct}%* 

━━━━━━━━━━━━━━━━━━━━━
🔗 [Pump.fun](https://pump.fun/{mint})"""
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        log.error(f"Erreur d'envoi Telegram: {e}")

# ══════════════════════════════════════════════════════════════
# CONNEXION ET LANCEMENT
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
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚙️ *Mise à jour : Activation du filtre strict Top 10 < 29%. Calme de retour dans le canal.*", parse_mode=ParseMode.MARKDOWN)
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
