"""
╔══════════════════════════════════════════════════════════════╗
║         SOLANA PUMP.FUN SNIPER BOT — 10 MIN WINDOW v9.1      ║
║   Objectif Unique $10K MCAP — Fenêtre Stricte de 10 Minutes  ║
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

# ── CONFIGURATION UNIQUE REQUISE ────────────────────────────
TARGET_MARKET_CAP_USD = 10000.0    # Déclenchement dès que le MCAP atteint ou dépasse $10K
MAX_MONITOR_MINUTES   = 10         # LIMITE STRICTE : On ne suit pas le token au-delà de 10 minutes

# ── TIMING DE SURVEILLANCE RAPIDE ───────────────────────────
CHECK_INTERVAL_SEC    = 6          # Analyse toutes les 6 secondes pour être ultra réactif

PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
AXIOM_TOKEN_API       = "https://api.axiom.trade/v1/token/{mint}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# REQUÊTE SUR L'API D'AXIOM PRO
# ══════════════════════════════════════════════════════════════

async def fetch_axiom_pro_metrics(session: aiohttp.ClientSession, mint: str) -> dict | None:
    try:
        url = AXIOM_TOKEN_API.format(mint=mint)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        async with session.get(url, headers=headers, timeout=4) as r:
            if r.status == 200:
                data = await r.json()
                token_data = data.get("data", {})
                
                if not token_data:
                    return None
                    
                return {
                    "mcap": float(token_data.get("marketCapUsd", 0) or 0),
                    "liquidity": float(token_data.get("liquidityUsd", 0) or 0),
                    "volume": float(token_data.get("volumeUsd", 0) or token_data.get("volume5mUsd", 0) or 0),
                    "holders_count": int(token_data.get("holdersCount", 0) or 0)
                }
    except Exception as e:
        log.debug(f"Attente d'indexation Axiom pour {mint[:8]} : {e}")
    return None

# ══════════════════════════════════════════════════════════════
# MONITORING CYCLIQUE (FENÊTRE DE 10 MINUTES MAX)
# ══════════════════════════════════════════════════════════════

async def monitor_token(session: aiohttp.ClientSession, event: dict):
    mint = event.get("mint")
    symbol = event.get("symbol", "?")
    if not mint or mint in alerted_tokens: return
    
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    max_loops = int((MAX_MONITOR_MINUTES * 60) / CHECK_INTERVAL_SEC)
    
    log.info(f"👀 [Tracking] Surveillance max 10 min lancée pour {symbol} ({mint[:8]})")

    for _ in range(max_loops):
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        if alerted_tokens.get(mint) == "ALERTED": return

        axiom_data = await fetch_axiom_pro_metrics(session, mint)
        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])

        # Si Axiom n'a pas encore de données prêtes, on ne s'arrête pas, on attend le prochain cycle (toutes les 6s)
        if not axiom_data:
            continue

        mcap = axiom_data["mcap"]
        holders = axiom_data["holders_count"]

        # Log de suivi en temps réel pour ta console Railway
        log.info(f"⏱️ {symbol} ({elapsed}s / 600s) -> MCAP actuel : ${mcap:,.0f} | Holders : {holders}")

        # Sécurité anti-bug (évite les valeurs à 0 au tout début du calcul de l'API)
        if mcap <= 0:
            continue

        # FILTRE UNIQUE : Est-ce qu'on a atteint l'objectif de $10K avant la fin des 10 min ?
        if mcap < TARGET_MARKET_CAP_USD:
            continue

        # TOUS LES FEUX SONT AU VERT -> ENVOI IMMÉDIAT DU SIGNAL SUR TELEGRAM
        alerted_tokens[mint] = "ALERTED"
        
        clean_name = event.get('name', '?').replace('*', '').replace('_', '').replace('`', '')
        clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')

        msg = f"""🎯 *ALERTE PUMP 10K ATTEINT (<10 MIN)*
• *Jeton :* {clean_name} ({clean_symbol})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *DONNÉES EN DIRECT AXIOM*
├ 💰 Market Cap : *${mcap:,.0f}* (Objectif $10K+ ✅)
├ 💧 Liquidité : *${axiom_data['liquidity']:,.0f}*
├ 💸 Volume : *${axiom_data['volume']:,.0f}*
├ 👥 Holders : *{holders}*
└ ⏱️ Temps écoulé depuis création : *{int(elapsed/60)}m {elapsed%60}s* ━━━━━━━━━━━━━━━━━━━━━
🔗 [Axiom Trade](https://axiom.trade/token/{mint}) | [Pump.fun](https://pump.fun/{mint})"""

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            log.info(f"🚀 [ALERTE Telegram] {symbol} a touché ${mcap:,.0f} à {elapsed}s !")
        except Exception as e:
            log.error(f"Erreur Telegram pour {symbol} : {e}")
        return

    if alerted_tokens.get(mint) != "ALERTED":
        log.info(f"🛑 [Expiré] Fin des 10 minutes de suivi pour {symbol}.")

# ══════════════════════════════════════════════════════════════
# CONNEXION FLUX PUMP.FUN
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
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚡ *Mise à jour v9.1 : Objectif unique $10K MCAP (Fenêtre stricte 0 à 10 min) actif.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
