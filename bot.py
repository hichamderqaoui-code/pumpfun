"""
╔══════════════════════════════════════════════════════════════╗
║         SOLANA PUMP.FUN SNIPER BOT — MULTI-FILTRES v7.1      ║
║   Filtres stricts + Logs de force pour débug d'API Axiom     ║
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

# ── CONFIGURATION DES 4 FILTRES MINIMUMS ────────────────────
MIN_HOLDERS           = 50         # Minimum 50 Holders distincts
MIN_MARKET_CAP_USD    = 5000.0     # Minimum $5,000 de Market Cap
MIN_LIQUIDITY_USD     = 5000.0     # Minimum $5,000 de Liquidité API
MIN_VOLUME_USD        = 5000.0     # Minimum $5,000 de Volume accumulé

# ── TIMINGS DE SURVEILLANCE ─────────────────────────────────
CHECK_INTERVAL_SEC    = 6          # Analyse rapide toutes les 6s
MAX_MONITOR_MINUTES   = 5          # Temps max de suivi après la création (5 min)

PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
AXIOM_TOKEN_API       = "https://api.axiom.trade/v1/token/{mint}"

# Force le niveau INFO pour être sûr de tout voir sur Railway
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# REQUÊTE DIRECTE SUR L'API D'INDEXATION D'AXIOM PRO
# ══════════════════════════════════════════════════════════════

async def fetch_axiom_pro_metrics(session: aiohttp.ClientSession, mint: str) -> dict | None:
    try:
        url = AXIOM_TOKEN_API.format(mint=mint)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        # Augmentation du timeout à 6s au cas où l'API sature
        async with session.get(url, headers=headers, timeout=6) as r:
            if r.status == 200:
                data = await r.json()
                token_data = data.get("data", {})
                
                if not token_data:
                    return None
                    
                return {
                    "mcap": float(token_data.get("marketCapUsd", 0) or 0),
                    "liquidity": float(token_data.get("liquidityUsd", 0) or 0),
                    "volume": float(token_data.get("volume5mUsd", 0) or token_data.get("volumeUsd", 0) or 0),
                    "holders_count": int(token_data.get("holdersCount", 0) or 0)
                }
    except Exception as e:
        log.info(f"⚠️ [API Axiom] Erreur ou Timeout pour {mint[:8]} : {e}")
    return None

# ══════════════════════════════════════════════════════════════
# MONITORING AVEC LOG DE FORCE DANS LA CONSOLE
# ══════════════════════════════════════════════════════════════

async def monitor_token(session: aiohttp.ClientSession, event: dict):
    mint = event.get("mint")
    symbol = event.get("symbol", "?")
    if not mint or mint in alerted_tokens: return
    
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    max_loops = int((MAX_MONITOR_MINUTES * 60) / CHECK_INTERVAL_SEC)
    
    log.info(f"👀 [Tracking] Entrée en surveillance pour {symbol} ({mint[:8]})")

    for _ in range(max_loops):
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        if alerted_tokens.get(mint) == "ALERTED": return

        axiom_data = await fetch_axiom_pro_metrics(session, mint)
        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])

        # LOG DE FORCE : Si l'API renvoie None, on veut le voir dans Railway !
        if not axiom_data:
            log.info(f"⏳ {symbol} ({elapsed}s) -> En attente d'indexation chez Axiom...")
            continue

        mcap = axiom_data["mcap"]
        liquidity = axiom_data["liquidity"]
        volume = axiom_data["volume"]
        holders = axiom_data["holders_count"]

        # Log d'état complet
        log.info(f"📊 {symbol} ({elapsed}s) -> MCAP: ${mcap:.0f} | Liq: ${liquidity:.0f} | Vol: ${volume:.0f} | Holders: {holders}")

        # VALIDATION STRICTE DES 4 CRITÈRES REQUIS
        if mcap < MIN_MARKET_CAP_USD:
            continue
        if liquidity < MIN_LIQUIDITY_USD:
            continue
        if volume < MIN_VOLUME_USD:
            continue
        if holders < MIN_HOLDERS:
            continue

        # TOUS LES FEUX SONT AU VERT
        alerted_tokens[mint] = "ALERTED"
        
        clean_name = event.get('name', '?').replace('*', '').replace('_', '').replace('`', '')
        clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')

        msg = f"""🎯 *PÉPITE REÇUE — CRITÈRES OK*
• *Jeton :* {clean_name} ({clean_symbol})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *METRICS EN DIRECT D'AXIOM*
├ 💰 Market Cap : *${mcap:,.0f}* (>$5K ✅)
├ 💧 Liquidité : *${liquidity:,.0f}* (>$5K ✅)
├ 💸 Volume : *${volume:,.0f}* (>$5K ✅)
├ 👥 Holders : *{holders}* (>{MIN_HOLDERS} ✅)
└ ⏱️ Temps de validation : *{elapsed}s après création*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Axiom Trade](https://axiom.trade/token/{mint}) | [Pump.fun](https://pump.fun/{mint})"""

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            log.info(f"🚀 [ALERTE] Signal envoyé pour {symbol} !")
        except Exception as e:
            log.error(f"Erreur d'envoi Telegram pour {symbol} : {e}")
        return

    if alerted_tokens.get(mint) != "ALERTED":
        log.info(f"🛑 Fin de suivi (Expiré) pour {symbol}.")

# ══════════════════════════════════════════════════════════════
# CONNEXION FLUX DE CRÉATION
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Connecté au flux de création Pump.fun !")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            asyncio.create_task(monitor_token(session, data))
                    except: pass
        except:
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚡ *Mise à jour v7.1 : Mode Débug Logs Axiom activé.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
