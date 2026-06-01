"""
╔══════════════════════════════════════════════════════════════╗
║    SOLANA PUMP.FUN SNIPER BOT — INSIDER 10K TRACKER v17.1    ║
║   Détection en DIRECT sur Pump.fun AVANT la migration Raydium ║
║   Correction stricte des blocs d'indentation (try/except)   ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import aiohttp
import json
import os
import logging
import websockets
from telegram import Bot
from telegram.constants import ParseMode
from fastapi import FastAPI

# GLOBALES DE CONFIGURATION
PUMPFUN_WS_PRIMARY = "wss://pumpportal.fun/api/data"
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# SEUILS STRATÉGIQUES SUR PUMP.FUN (AVANT MIGRATION)
TARGET_MCAP_PUMP = 10000.0         # Alerte dès que le Market Cap franchit 10K$ sur Pump.fun
MIN_TRADES_COUNT = 15              # Minimum 15 transactions pour valider l'intérêt organique

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()

# Dictionnaires de suivi en mémoire vive
token_stats = {}
alerted_tokens = set()

# ══════════════════════════════════════════════════════════════
# ENVOI DE L'ALERTE INSIDER (10K EN COURS SUR PUMP.FUN)
# ══════════════════════════════════════════════════════════════

async def send_insider_alert(mint: str, symbol: str, name: str, mcap: float, total_vol: float, total_txs: int):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    # Liens optimisés pour l'achat immédiat avant migration
    link_axiom = f"https://axiom.trade/token/{mint}"
    link_pump = f"https://pump.fun/{mint}"

    msg = (
        "🚨 *ALERTE SNIPER : FRANCHISSEMENT 10K$ !* 🚨\n"
        f"• *Nom :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📈 *DONNÉES LIVE (SUR PUMP.FUN)*\n"
        f"├ 💰 Market Cap Actuel : *${mcap:,.0f}* 🎯\n"
        f"├ 💵 Volume injecté : *${total_vol:,.0f}*\n"
        f"├ 📊 Nombre de Trades : *{total_txs} transactions*\n"
        f"└ ⛓️ Statut : *En cours de Bonding Curve* ⏳\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *STRATÉGIE :* Ce token vient d'exploser le plafond des 10K$ sur Pump.fun avec une bonne dynamique. Il n'a pas encore migré !\n\n"
        f"🔗 [Acheter Direct sur Axiom Pro]({link_axiom}) | [Voir sur Pump.fun]({link_pump})"
    )
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
