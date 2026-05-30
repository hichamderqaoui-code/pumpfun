"""
╔══════════════════════════════════════════════════════════════╗
║     SOLANA PUMP.FUN SNIPER BOT — MIGRATION HUNTER v13.1      ║
║   Filtre 1: Réseaux Sociaux | Filtre 2: >1000 TXs | >40 Wallets║
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
CHECK_INTERVAL_SEC = 5             # Analyse toutes les 5 secondes

# FILTRES EXCLUSIFS PROFIL "MIGRATED" (V13.1)
MIN_UNIQUE_BUYERS = 40             # Minimum 40 acheteurs différents
MIN_BUY_VOLUME_RATIO = 0.60        # Minimum 60% d'achats (Pression verte)
MIN_TOTAL_TRANSACTIONS = 1000      # Au moins 1000 transactions au compteur (Volume de hype)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# ANALYSE DE LA PRESSION BLOCKCHAIN ET DU VOLUME DE TRANSACTIONS
# ══════════════════════════════════════════════════════════════

async def check_token_metrics(session: aiohttp.ClientSession, mint: str):
    """Vérifie si le token a le profil d'un futur runner qui va migrer"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with session.get(url, timeout=3) as r:
            if r.status != 200:
                return False, 0.0, 0, 0.0, 0
            
            data = await r.json()
            pairs = data.get("pairs")
            if not pairs or len(pairs) == 0:
                return False, 0.0, 0, 0.0, 0
            
            pair = pairs[0]
            mcap = float(pair.get("marketCap", 0))
            
            # 1. Calcul du volume et ratio de pression acheteuse (m5)
            m5_stats = pair.get("volume", {})
            buys_usd = float(m5_stats.get("buys", 0))
            sells_usd = float(m5_stats.get("sells", 0))
            total_vol = buys_usd + sells_usd
            buy_ratio = (buys_usd / total_vol) if total_vol > 0 else 0.0
            
            # 2. Nombre de wallets acheteurs (m5)
            buyers = int(pair.get("txns", {}).get("m5", {}).get("buys", 0))
            
            # 3. Nombre total cumulé de transactions (Somme des buys + sells globaux)
            tx_buys = int(pair.get("txns", {}).get("m5", {}).get("buys", 0))
            tx_sells = int(pair.get("txns", {}).get("m5", {}).get("sells", 0))
            total_txs = tx_buys + tx_sells
            
            # Validation stricte de l'activité industrielle
            if (buyers >= MIN_UNIQUE_BUYERS and 
                buy_ratio >= MIN_BUY_VOLUME_RATIO and 
                total_txs >= MIN_TOTAL_TRANSACTIONS):
                return True, mcap, buyers, buy_ratio, total_txs
                
            return False, mcap, buyers, buy_ratio, total_txs
    except Exception as e:
        log.debug(f"Erreur métriques DexScreener : {e}")
    return False, 0.0, 0, 0.0, 0

# ══════════════════════════════════════════════════════════════
# TELEGRAM ALERTE AVEC INDICATION DES CRITÈRES DE MIGRATION
# ══════════════════════════════════════════════════════════════

async def send_telegram_alert(mint: str, symbol: str, name: str, mcap: float, elapsed_seconds: int, buyers: int, buy_ratio: float, total_txs: int):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)

    link_axiom = f"https://axiom.trade/token/{mint}"
    link_dex = f"https://dexscreener.com/solana/{mint}"
    link_pump = f"https://pump.fun/{mint}"

    msg = (
        "💎 *PROFIL 'RAYDIUM MIGRATION' DÉTECTÉ* 💎\n"
        f"• *Jeton :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *ANALYSE DES COMPORTEMENTS GAGNANTS*\n"
        f"├ 👥 Unique Buyers : *{buyers} wallets* (Min {MIN_UNIQUE_BUYERS}) ✅\n"
        f"├ 📈 Total Txs : *{total_txs}* (Min {MIN_TOTAL_TRANSACTIONS}) ✅\n"
        f"└ 🟢 Pression Buys : *{buy_ratio*100:.1f}%* ✅\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *MARCHÉ EN DIRECT*\n"
        f"├ 💰 Market Cap : *${mcap:,.0f}*\n"
        f"└ ⏱️ Temps : *{minutes}m {seconds}s*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 [Axiom Pro]({link_axiom}) | [DexScreener]({link_dex}) | [Pump.fun]({link_pump})"
    )
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        log.info(f"🚀 [SIGNAL EMIS] {clean_symbol} validé.")
    except Exception as e:
        log.error(f"Erreur Telegram : {e}")

# ══════════════════════════════════════════════════════════════
# MONITORING FILTRÉ (RÉSEAUX SOCIAUX + ACTIVITÉ BLOCKCHAIN)
# ══════════════════════════════════════════════════════════════

async def monitor_token(session: aiohttp.ClientSession, mint: str, symbol: str, name: str):
    if not mint or mint in alerted_tokens:
        return
    
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    max_loops = int((MAX_MONITOR_MINUTES * 60) // CHECK_INTERVAL_SEC)

    for _ in range(max_loops):
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        if alerted_tokens.get(mint) == "ALERTED":
            return

        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])
        is_valid, real_mcap, buyers, buy_ratio, total_txs = await check_token_metrics(session, mint)
        
        if real_mcap < TARGET_MARKET_CAP_USD:
            continue

        # Si le prix passe 10K mais ne valide pas les critères industriels de volume
        if not is_valid:
            log.info(f"❌ [Rejeté] {symbol} (Txs: {total_txs}, Wallets: {buyers}) -> Pas assez solide pour migrer.")
            alerted_tokens[mint] = "ALERTED"
            return

        alerted_tokens[mint] = "ALERTED"
        await send_telegram_alert(mint, symbol, name, real_mcap, elapsed, buyers, buy_ratio, total_txs)
        return

# ══════════════════════════════════════════════════════════════
# FILTRE INITIAL À LA CRÉATION (REJET DES PROJETS SANS RÉSEAUX)
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun_websocket(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Sniper v13.1 Connecté (Filtres Sociaux + TXs Actifs)")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            # Extraction des réseaux sociaux optionnels fournis au mint par le Dev
                            has_twitter = bool(data.get("twitter"))
                            has_telegram = bool(data.get("telegram"))
                            has_website = bool(data.get("website"))
                            
                            # FILTRE SOCIAL : S'il n'y a STRICTEMENT AUCUN réseau social relié, on ignore immédiatement
                            if not (has_twitter or has_telegram or has_website):
                                continue
                            
                            mint = data.get("mint")
                            symbol = data.get("symbol", "?")
                            name = data.get("name", "?")
                            asyncio.create_task(monitor_token(session, mint, symbol, name))
                    except: pass
        except Exception as e:
            log.error(f"Erreur Flux WebSocket : {e}")
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚡ *Mise à jour v13.1 Active : Mode Chasseur de Migration en cours. Seuls les tokens avec réseaux sociaux et transactions massives seront transmis.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun_websocket(session)

@app.get("/")
async def root():
    return {"status": "online", "active_scans": len(alerted_tokens)}
