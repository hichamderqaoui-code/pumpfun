"""
╔══════════════════════════════════════════════════════════════╗
║      SOLANA PUMP.FUN SNIPER BOT — ELITE FILTER v15.5         ║
║   Audit post 10-Min : MCAP, VOLUME, LIQUIDITÉ, HOLDERS, TXS  ║
║   Logs en temps réel activés pour un suivi transparent       ║
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

# CONFIGURATION DU CHRONO STRATÉGIQUE
WAIT_TIME_SECONDS = 600            # Attente obligatoire de 10 minutes (600s)

# CONFIGURATION DES FILTRES ÉLITES (v15.5)
MIN_MCAP = 6000.0                  # Market Cap Minimum : 6K$
MIN_VOLUME = 50000.0               # Volume Minimum après 10 min : 50K$ (Sécurisé & Réaliste)
MIN_LIQUIDITY = 4000.0             # Liquidité Minimum dans la Pool : 4K$
MIN_HOLDERS = 50                   # Minimum 50 Holders uniques estimés
MIN_TXS = 300                      # Minimum 300 Transactions au total
MIN_BUY_RATIO = 0.52               # Pression acheteuse saine (>52%)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
monitored_tokens = {}

# ══════════════════════════════════════════════════════════════
# AUDIT TECHNIQUE STRICT DU TOKEN APRÈS 10 MINUTES
# ══════════════════════════════════════════════════════════════

async def evaluate_token_after_delay(session: aiohttp.ClientSession, mint: str, symbol: str, name: str):
    try:
        # Étape 1 : Phase d'incubation (10 minutes)
        await asyncio.sleep(WAIT_TIME_SECONDS)
        
        # Étape 2 : Extraction des données consolidées sur DexScreener
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with session.get(url, timeout=5) as r:
            if r.status != 200:
                return
            
            data = await r.json()
            pairs = data.get("pairs")
            if not pairs or len(pairs) == 0:
                log.info(f"📉 [10-Min] {symbol} ({mint[:8]}) introuvable ou déjà mort sur Raydium/DEX.")
                return
            
            pair = pairs[0]
            mcap = float(pair.get("marketCap", 0))
            
            # 1. Analyse Liquidité
            liquidity = float(pair.get("liquidity", {}).get("usd", 0))
            
            # 2. Analyse Volume (Accumulé)
            buys_usd = float(pair.get("volume", {}).get("buys", 0))
            sells_usd = float(pair.get("volume", {}).get("sells", 0))
            total_vol = buys_usd + sells_usd
            
            # 3. Analyse Transactions & Holders
            tx_buys = int(pair.get("txns", {}).get("m
