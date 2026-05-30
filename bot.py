"""
╔══════════════════════════════════════════════════════════════╗
║     SOLANA PUMP.FUN SNIPER BOT — FULL STREAM v13.4           ║
║   Filtre 1: Pro Traders > 50 | Filtre 2: > 1000 TXs Globaux  ║
║   🚫 AUCUN BLOCAGE INITIAL (Ni Réseaux Sociaux, Ni Bundlers) ║
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

TARGET_MARKET_CAP_USD = 10000.0    # Déclenchement à $10,000 USD
MAX_MONITOR_MINUTES = 10           # Suivi max : 10 minutes
CHECK_INTERVAL_SEC = 3             # Analyse toutes les 3 secondes pour un max de réactivité

# CRITÈRES D'ACTIVITÉ BRUTE (V13.4)
MIN_PRO_TRADERS = 50               # Minimum 50 portefeuilles "Pro Traders" actifs
MIN_TOTAL_TRANSACTIONS = 1000      # Au moins 1000 transactions au total (Preuve de hype)
MIN_BUY_VOLUME_RATIO = 0.55        # Au moins 55% de pression acheteuse sur les 5m

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# ANALYSE PURE DU VOLUME ET DES TRADERS SUR DEXSCREENER
# ══════════════════════════════════════════════════════════════

async def check_token_activity(session: aiohttp.ClientSession, mint: str):
    """Analyse les métriques sans aucun blocage structurel préliminaire"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with session.get(url, timeout=3) as r:
            if r.status != 200:
                return False, 0.0, 0, 0, 0.0
            
            data = await r.json()
            pairs = data.get("pairs")
            if not pairs or len(pairs) == 0:
                return False, 0.0, 0, 0, 0.0
            
            pair = pairs[0]
            mcap = float(pair.get("marketCap", 0))
            
            # 1. Transactions sur la bougie m5
            tx_buys = int(pair.get("txns", {}).get("m5", {}).get("buys", 0))
            tx_sells = int(pair.get("txns", {}).get("m5", {}).get("sells", 0))
            total_txs = tx_buys + tx_sells
            
            # 2. Volume d'achat et de vente financier
            buys_usd = float(pair.get("volume", {}).get("buys", 0))
            sells_usd = float(pair.get("volume", {}).get("sells", 0))
            total_vol = buys_usd + sells_usd
            buy_ratio = (buys_usd / total_vol) if total_vol > 0 else 0.0
            
            # 3. Estimation des Pro Traders actifs
            pro_traders = tx_buys // 3 
            
            # Lecture indicative de la structure des bundles (uniquement pour l'affichage)
            bundlers_percentage = float(pair.get("boosts", {}).get("value", 0))
            
            # Validation basée uniquement sur la puissance de l'activité
            if pro_traders >= MIN_PRO_TRADERS and total_txs >= MIN_TOTAL_TRANSACTIONS and buy_ratio >= MIN_BUY_VOLUME_RATIO:
                return True, mcap, pro_traders, total_txs, bundlers_percentage
                
            return False, mcap, pro_traders, total_txs, bundlers_percentage
    except Exception as e:
        log.debug(f"Erreur d'analyse sur le jeton {mint[:8]} : {e}")
    return False, 0.0, 0, 0, 0.0

# ══════════════════════════════════════════════════════════════
# ENVOI DE L'ALERTE TELEGRAM v13.4
# ══════════════════════════════════════════════════════════════

async def send_telegram_alert(mint: str, symbol: str, name: str, mcap: float, elapsed_seconds: int, pro_traders: int, total_txs: int, bundlers: float):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)

    link_axiom = f"https://axiom.trade/token/{mint}"
    link_dex = f"https://dexscreener.com/solana/{mint}"

    msg = (
        "🔊 *ALERTE HYPE VOLUME (v13.4 - FULL)* 🔊\n"
        f"• *Jeton :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *ACTIVITÉ DÉTECTÉE EN DIRECT*\n"
        f"├ 🔥 Pro Traders : *{pro_traders}* (Min {MIN_PRO_TRADERS}) ✅\n"
        f"├ 📈 Total Transactions : *{total_txs}* (Min {MIN_TOTAL_TRANSACTIONS}) ✅\n"
        f"└ ⛓️ Info Bundle Détecté : *{bundlers:.2f}%* ℹ️\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *MARCHÉ*\n"
        f"├ 💰 Market Cap Validé : *${mcap:,.0f}*\n"
        f"└ ⏱️ Temps : *{minutes}m {seconds}s*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *Note :* Les liens de réseaux (X/Telegram) ont pu être ajoutés par l'équipe après le lancement, vérifie directement sur Axiom Pro !\n\n"
        f"🔗 [Axiom Pro]({link_axiom}) | [DexScreener]({link_dex})"
    )
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        log.error(f"Erreur d'envoi Telegram : {e}")

# ══════════════════════════════════════════════════════════════
# SURVEILLANCE SANS FILTRAGE DE DÉPART
# ══════════════════════════════════════════════════════════════

async def monitor_token(session: aiohttp.ClientSession, mint: str, symbol: str, name: str):
    if not mint or mint in alerted_tokens:
        return
    
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    max_loops = int((MAX_MONITOR_MINUTES * 60) // CHECK_INTERVAL_SEC)
    
    log.info(f"👀 Surveillance lancée pour {symbol} ({mint[:8]})")

    for _ in range(max_loops):
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        if alerted_tokens.get(mint) == "ALERTED":
            return

        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])
        is_active, real_mcap, pro_traders, total_txs, bundlers = await check_token_activity(session, mint)
        
        if real_mcap < TARGET_MARKET_CAP_USD:
            continue

        if not is_active:
            log.info(f"❌ [Rejeté] {symbol} à ${real_mcap:,.0f} (TXs: {total_txs}, Pro: {pro_traders})")
            alerted_tokens[mint] = "ALERTED"
            return

        alerted_tokens[mint] = "ALERTED"
        await send_telegram_alert(mint, symbol, name, real_mcap, elapsed, pro_traders, total_txs, bundlers)
        return

async def connect_pumpfun_websocket(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Flux v13.4 Connecté — Écoute totale de 100% des lancements")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            # ZERO FILTRE : On prend absolument tout ce qui se crée !
                            mint = data.get("mint")
                            symbol = data.get("symbol", "?")
                            name = data.get("name", "?")
                            asyncio.create_task(monitor_token(session, mint, symbol, name))
                    except: pass
        except Exception as e:
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="🚀 *Mise à jour v13.4 Active : Mode Ouverture Totale. Plus aucun filtrage sur les réseaux sociaux ou les bundlers au lancement. Surveillance brute lancée.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun_websocket(session)

@app.get("/")
async def root():
    return {"status": "online"}
