"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — AXIOM PRO LINK v12.1     ║
║    Zéro Simulation — Intégration du Lien Axiom Pro Direct    ║
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

# GLOBALES DE CONFIGURATION
PUMPFUN_WS_PRIMARY = "wss://pumpportal.fun/api/data"
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TARGET_MARKET_CAP_USD = 10000.0    # Objectif strict : $10,000 USD
MAX_MONITOR_MINUTES = 10           # Temps max de suivi : 10 minutes
CHECK_INTERVAL_SEC = 6             # Interrogation toutes les 6 secondes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# REQUÊTE DE PRIX RÉEL VIA DEXSCREENER API
# ══════════════════════════════════════════════════════════════

async def get_real_market_cap(session: aiohttp.ClientSession, mint: str) -> float:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with session.get(url, timeout=3) as r:
            if r.status == 200:
                data = await r.json()
                pairs = data.get("pairs")
                if pairs and len(pairs) > 0:
                    return float(pairs[0].get("marketCap", 0))
    except Exception as e:
        log.debug(f"Erreur lecture prix pour {mint[:8]} : {e}")
    return 0.0

# ══════════════════════════════════════════════════════════════
# ENVOI DE L'ALERTE TELEGRAM AVEC LIEN AXIOM PRO
# ══════════════════════════════════════════════════════════════

async def send_telegram_alert(mint: str, symbol: str, name: str, mcap: float, elapsed_seconds: int):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)

    # Ajout du lien Axiom Trade / Pro mis en valeur dans la section liens
    msg = (
        "🎯 *ALERTE PUMP 10K ATTEINT (<10 MIN)*\n"
        f"• *Jeton :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *DONNÉES RÉELLES DE LA CHAÎNE*\n"
        f"├ 💰 Vrai Market Cap : *${mcap:,.0f}* ✅\n"
        "├ ⏱️ Statut : *Filtre <10 min Validé*\n"
        f"└ ⏱️ Temps écoulé : *{minutes}m {seconds}s*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 [Axiom Pro](https://axiom.trade/token/{mint}) | [DexScreener](https://dexscreener.com/solana/{mint}) | [Pump.fun](https
