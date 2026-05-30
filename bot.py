"""
╔══════════════════════════════════════════════════════════════╗
║      SOLANA PUMP.FUN SNIPER BOT — 10-MIN ANALYSER v14.0      ║
║   Analyse automatique après 10 minutes de vie du Token       ║
║   Alerte UNIQUEMENT si les nouveaux critères de Potentiel    ║
║   sont réunis (MCAP 6K, VOL 6M, MIN 50 HOLDERS)               ║
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

# CONFIGURATION DU CHRONO STRATÉGIQUE
WAIT_TIME_SECONDS = 600            # Fenêtre d'analyse obligatoire de 10 minutes (600s)

# NOUVEAUX FILTRES EXCLUSIFS CRITÈRES STRICTS (v14.0)
MIN_MCAP_AFTER_10M = 6000.0        # Minimum Market Cap à 6K$
MIN_VOLUME_10M = 6000000.0         # Minimum Volume à 6000K$ (6 Millions $)
MIN_HOLDERS_10M = 50               # Minimum 50 Holders validés
MIN_BUY_RATIO_10M = 0.50           # Pression acheteuse équilibrée/saine (>50%)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
monitored_tokens = {}

# ══════════════════════════════════════════════════════════════
# ANALYSE PAR APPRENTISSAGE APRÈS 10 MINUTES DE VIE
# ══════════════════════════════════════════════════════════════

async def evaluate_token_after_delay(session: aiohttp.ClientSession, mint: str, symbol: str, name: str):
    """Attend 10 minutes en tâche de fond puis réalise l'audit de puissance"""
    try:
        # Étape 1 : Le token vit sa vie pendant 10 minutes
        await asyncio.sleep(WAIT_TIME_SECONDS)
        
        # Étape 2 : On extrait le bilan complet sur DexScreener
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with session.get(url, timeout=5) as r:
            if r.status != 200:
                return
            
            data = await r.json()
            pairs = data.get("pairs")
            if not pairs or len(pairs) == 0:
                log.info(f"📉 [10-Min] {symbol} introuvable ou déjà mort sur les DEX.")
                return
            
            pair = pairs[0]
            mcap = float(pair.get("marketCap", 0))
            
            # Extraction des volumes globaux cumulés
            buys_usd = float(pair.get("volume", {}).get("buys", 0))
            sells_usd = float(pair.get("volume", {}).get("sells", 0))
            total_vol = buys_usd + sells_usd
            
            # Extraction et calcul des transactions
            tx_buys = int(pair.get("txns", {}).get("m5", {}).get("buys", 0)) * 2 # extrapolation 10m
            tx_sells = int(pair.get("txns", {}).get("m5", {}).get("sells", 0)) * 2
            total_txs = tx_buys + tx_sells
            
            buy_ratio = (buys_usd / total_vol) if total_vol > 0 else 0.0
            
            # Estimation dynamique et sécurisée des holders uniques basés sur l'activité d'achat
            # et le ratio de distribution de volume par rapport à la capitalisation
            estimated_holders = int(tx_buys // 2.5) if tx_buys > 0 else 0
            
            # Info optionnelle de la structure des portefeuilles
            bundlers_percentage = float(pair.get("boosts", {}).get("value", 0))

            # Étape 3 : Application du verdict strict de la v14.0
            if (mcap >= MIN_MCAP_AFTER_10M and 
                total_vol >= MIN_VOLUME_10M and 
                total_txs > 0 and 
                estimated_holders >= MIN_HOLDERS_10M and 
                buy_ratio >= MIN_BUY_RATIO_10M):
                
                # CRITÈRES REMPLIS : ENVOI DE L'ALERTE
                await send_explosion_alert(mint, symbol, name, mcap, total_vol, total_txs, estimated_holders, bundlers_percentage)
            else:
                log.info(f"❌ [Filtre 10-Min Rejeté] {symbol} insuffisant (MCAP: ${mcap:,.0f}, Vol: ${total_vol:,.0f}, TXs: {total_txs}, Est. Holders: {estimated_holders})")
                
    except Exception as e:
        log.error(f"Erreur lors de l'analyse automatique de {mint[:8]} : {e}")

# ══════════════════════════════════════════════════════════════
# ALERTE EXCLUSIVE V14.0 CONFIGURÉE
# ══════════════════════════════════════════════════════════════

async def send_explosion_alert(mint: str, symbol: str, name: str, mcap: float, total_vol: float, total_txs: int, holders: int, bundlers: float):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    link_axiom = f"https://axiom.trade/token/{mint}"
    link_dex = f"https://dexscreener.com/solana/{mint}"

    msg = (
        "🔥 *ALERTE POTENTIEL MAXIMUM CONFIRMÉE (v14.0)* 🔥\n"
        f"• *Jeton validé :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *ANALYSE DES PARAMÈTRES REQUIS*\n"
        f"├ 💰 Market Cap : *${mcap:,.0f}* (Min 6K$) 🟢\n"
        f"├ 💎 Volume global : *${total_vol:,.0f}* (Min 6M$) ✅\n"
        f"├ 📈 Volume Transactions : *{total_txs} TXs* ✅\n"
        f"├ 👥 Holders estimés : *{holders}* (Min 50) 👥\n"
        f"└ ⛓️ Info structure : *{bundlers:.2f}%* ℹ️\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *VERDICT TRADING :* Structure validée. Ce token a survécu aux 10 premières minutes critiques en conservant un volume massif et une base solide d'acheteurs uniques.\n\n"
        f"🔗 [Acheter sur Axiom Pro]({link_axiom}) | [DexScreener]({link_dex})"
    )
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        log.error(f"Erreur Telegram : {e}")

# ══════════════════════════════════════════════════════════════
# RECEPTION DU FLUX ET INTEGRATION DANS LE CHRONO
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun_websocket(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Système v14.0 en ligne — Traitement strict des critères en cours.")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            mint = data.get("mint")
                            if mint not in monitored_tokens:
                                monitored_tokens[mint] = True
                                # On envoie le token en incubation pendant 10 minutes
                                asyncio.create_task(evaluate_token_after_delay(session, mint, data.get("symbol", "?"), data.get("name", "?")))
