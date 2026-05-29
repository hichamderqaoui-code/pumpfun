"""
╔══════════════════════════════════════════════════════════════╗
║         SOLANA PUMP.FUN SNIPER BOT — PURE VOLUME v6          ║
║   Suppression totale du Top 10 — Alerte brute sur le MCAP    ║
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

# ── FILTRE DE DÉCLENCHEMENT UNIQUE ──────────────────────────
MIN_MARKET_CAP_USD    = 12000.0    # Alerte dès que le volume pousse le MCAP ici

# ── TIMINGS DE SURVEILLANCE ─────────────────────────────────
CHECK_INTERVAL_SEC    = 6          # Fréquence de rafraîchissement rapide (toutes les 6s)
MAX_MONITOR_MINUTES   = 5          # Temps max de suivi après la création (5 min)

PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
AXIOM_TOKEN_API       = "https://api.axiom.trade/v1/token/{mint}"

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
        async with session.get(url, headers=headers, timeout=4) as r:
            if r.status == 200:
                data = await r.json()
                token_data = data.get("data", {})
                
                if not token_data:
                    return None
                    
                return {
                    "mcap": float(token_data.get("marketCapUsd", 0) or 0),
                    "liquidity": float(token_data.get("liquidityUsd", 0) or 0),
                    "holders_count": int(token_data.get("holdersCount", 0) or 0)
                }
    except Exception as e:
        log.debug(f"Erreur d'indexation Axiom pour {mint[:8]} : {e}")
    return None

# ══════════════════════════════════════════════════════════════
# MONITORING DIRECT SUR LE MARKET CAP
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

        if not axiom_data:
            continue

        mcap = axiom_data["mcap"]
        liquidity = axiom_data["liquidity"]

        log.info(f"⏱️ {symbol} ({elapsed}s) -> MCAP Axiom actuel: ${mcap:,.0f}")

        # CONDITION UNIQUE : Validation par la capitalisation boursière
        if mcap < MIN_MARKET_CAP_USD:
            continue

        # ENVOI IMMÉDIAT (Plus aucun blocage lié aux portefeuilles)
        alerted_tokens[mint] = "ALERTED"
        
        clean_name = event.get('name', '?').replace('*', '').replace('_', '').replace('`', '')
        clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')

        msg = f"""🚀 *SIGNAL VOLUMES ACTIFS*
• *Jeton :* {clean_name} ({clean_symbol})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *METRICS EN DIRECT D'AXIOM*
├ 💰 Market Cap : *${mcap:,.0f}* (Objectif >$12K ✅)
├ 💧 Liquide API : *${liquidity:,.0f}*
├ 👥 Total Holders : *{axiom_data['holders_count']}*
└ ⏱️ Temps de tracking : *{elapsed}s après création*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Axiom Trade](https://axiom.trade/token/{mint}) | [Pump.fun](https://pump.fun/{mint})"""

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            log.info(f"🚀 [ALERTE SUCCESS] {symbol} envoyé avec un MCAP de ${mcap:,.0f} après {elapsed}s")
        except Exception as e:
            log.error(f"Erreur d'envoi Telegram pour {symbol} : {e}")
        return

    if alerted_tokens.get(mint) != "ALERTED":
        log.debug(f"🛑 Fin de suivi (Expiré) pour {symbol}.")

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
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚡ *Mise à jour v6 : Mode Pure Volume Activé (Filtre Top 10 désactivé).*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
