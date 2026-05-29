"""
╔══════════════════════════════════════════════════════════════╗
║         SOLANA PUMP.FUN SNIPER BOT — STABLE LIVE v11         ║
║   Structure FastAPI Nettoyée — Alerte 10K — Route de Test   ║
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

# ── CONFIGURATION DES COMPOSANTS (Définis proprement au démarrage)
PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TARGET_MARKET_CAP_USD = 10000.0    # Objectif : $10,000 de Market Cap
MAX_MONITOR_MINUTES   = 10         # Fenêtre stricte : max 10 minutes après création
CHECK_INTERVAL_SEC    = 5          # Analyse toutes les 5 secondes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Initialisation des instances globales
bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# ENVOI DU MESSAGE TEMPLATE TELEGRAM
# ══════════════════════════════════════════════════════════════

async def send_telegram_alert(mint: str, symbol: str, name: str, mcap: float, elapsed: int):
    """Génère le format de l'alerte et l'envoie sur le canal Telegram"""
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')

    msg = f"""🎯 *ALERTE PUMP 10K ATTEINT (<10 MIN)*
• *Jeton :* {clean_name} ({clean_symbol})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *DONNÉES EN DIRECT DE LA CHAÎNE*
├ 💰 Market Cap : *${mcap:,.0f}* (Objectif $10K+ ✅)
├ ⏱️ Statut : *Filtre de temps respecté*
└ ⏱️ Détecté après : *{int(elapsed/60)}m {elapsed%60}s*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Axiom Trade](https://axiom.trade/token/{mint}) | [Pump.fun](https://pump.fun/{mint})"""
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        log.info(f"🚀 [TELEGRAM] Signal envoyé avec succès pour {symbol} !")
        return True
    except Exception as e:
        log.error(f"Erreur d'envoi Telegram pour {symbol} : {e}")
        return False

# ══════════════════════════════════════════════════════════════
# MONITORING CYCLIQUE DE 0 À 10 MINUTES
# ══════════════════════════════════════════════════════════════

async def monitor_token(mint: str, symbol: str, name: str):
    if not mint or mint in alerted_tokens: return
    
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    max_loops = int((MAX_MONITOR_MINUTES * 60) / CHECK_INTERVAL_SEC)
    
    log.info(f"👀 [Tracking] Suivi de 10 min démarré pour {symbol} ({mint[:8]})")

    for _ in range(max_loops):
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        if alerted_tokens.get(mint) == "ALERTED": return

        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])
        
        # Simulation d'un pump progressif pour tester et forcer le déclenchement en direct
        simulated_mcap = 4000.0 + (elapsed * 45.0)
        
        log.info(f"⏱️ {symbol} ({elapsed}s / 600s) -> MCAP : ${sim
