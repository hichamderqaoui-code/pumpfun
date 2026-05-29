"""
╔══════════════════════════════════════════════════════════════╗
║         SOLANA PUMP.FUN SNIPER BOT — DYNAMIC FILTERS         ║
║   Filtre Holders Évolutif : Spécial Bundlers (0-10 min)      ║
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

# ── FILTRES SÉCURISÉS & DYNAMIQUES ──────────────────────────
MIN_MARKET_CAP_USD    = 15000.0    # Déclencheur de suivi (Seuil MCAP stable)

# Configuration du Top 10 Holders évolutif
MAX_TOP10_START_PCT   = 35.0       # Seuil toléré durant les 10 premières minutes (Spécial Bundlers)
MAX_TOP10_LATE_PCT    = 29.0       # Seuil strict appliqué après les 10 premières minutes

# ── TIMINGS DE SURVEILLANCE EXTENSION 10 MIN+ ────────────────
CHECK_INTERVAL_SEC    = 8          # Fréquence de rafraîchissement (toutes les 8s)
MAX_MONITOR_MINUTES   = 12         # Augmenté à 12 min pour analyser le comportement après les 10 min de vie

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
                    "top10_pct": float(token_data.get("top10Percentage", 100) or 100),
                    "holders_count": int(token_data.get("holdersCount", 0) or 0)
                }
    except Exception as e:
        log.debug(f"Erreur d'indexation Axiom pour {mint[:8]} : {e}")
    return None

# ══════════════════════════════════════════════════════════════
# MONITORING AVEC FILTRAGE DYNAMIQUE TEMPOREL
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

        liquidity = axiom_data["liquidity"]
        top10_pct = axiom_data["top10_pct"]
        mcap = axiom_data["mcap"]

        # Détermination dynamique du seuil selon l'âge du token
        if elapsed <= 600:  # Moins ou égal à 10 minutes (600 secondes)
            current_max_top10 = MAX_TOP10_START_PCT
            period_label = "Phase Initiale (0-10m)"
        else:
            current_max_top10 = MAX_TOP10_LATE_PCT
            period_label = "Phase Tardive (>10m)"

        log.info(f"⏱️ {symbol} ({elapsed}s) -> MCAP: ${mcap:,.0f} | Top 10: {top10_pct}% | Limite temporaire: {current_max_top10}%")

        # CRITÈRE 1 : Le Market Cap doit passer les $15,000
        if mcap < MIN_MARKET_CAP_USD:
            continue

        # PROTECTION ANTI-BUG CACHE 100% (Attente du chargement initial d'Axiom)
        if top10_pct >= 99.9 or top10_pct == 0.0:
            continue

        # CRITÈRE 2 : Application du filtre dynamique (35% si < 10min, sinon 29%)
        if top10_pct > current_max_top10:
            continue

        # VALIDATION ET ENVOI DE L'ALERTE
        alerted_tokens[mint] = "ALERTED"
        
        clean_name = event.get('name', '?').replace('*', '').replace('_', '').replace('`', '')
        clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')

        msg = f"""🎯 *PÉPITE ENTRÉE VALIDÉE*
• *Jeton :* {clean_name} ({clean_symbol})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *METRICS EN DIRECT D'AXIOM*
├ 💰 Market Cap : *${mcap:,.0f}* (Filtre: >$15K ✅)
├ 💧 Liquide API : *${liquidity:,.0f}*
└ ⏱️ Âge au trigger : *{elapsed}s ({int(elapsed/60)}m {elapsed%60}s)*

👥 *DISTRIBUTION TRADERS ({period_label})*
├ 🎯 Top 10 Holders : *{top10_pct}%* (Seuil maximum: {current_max_top10}% ✅)
└ 👥 Total Holders : *{axiom_data['holders_count']}*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Axiom Trade](https://axiom.trade/token/{mint}) | [Pump.fun](https://pump.fun/{mint})"""

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            log.info(f"🚀 [ALERTE] Signal envoyé pour {symbol} ({period_label}) à {top10_pct}% (MCAP: ${mcap:,.0f})")
        except Exception as e:
            log.error(f"Erreur d'envoi Telegram pour {symbol} : {e}")
        return

    if alerted_tokens.get(mint) != "ALERTED":
        log.info(f"🛑 Fin de suivi (Expiré {MAX_MONITOR_MINUTES}m) pour {symbol}.")

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
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚡ *Mise à jour v4 : Filtre intelligent Bundlers (35% < 10 min / 29% > 10 min) activé.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
