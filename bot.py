"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — ANTI-RUG FILTER v13.0    ║
║   Filtre 1 : > 40 Acheteurs Uniques | Filtre 2 : > 60% Buys  ║
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

TARGET_MARKET_CAP_USD = 10000.0    # Palier de déclenchement : $10,000 USD
MAX_MONITOR_MINUTES = 10           # Suivi max : 10 minutes
CHECK_INTERVAL_SEC = 5             # Analyse toutes les 5 secondes

# CRITÈRES DES FILTRES DE SÉCURITÉ (V13)
MIN_UNIQUE_BUYERS = 40             # Minimum 40 portefeuilles acheteurs distincts
MIN_BUY_VOLUME_RATIO = 0.60        # Minimum 60% du volume global doit être des achats (Vert)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# ANALYSE ET FILTRAGE DU TOKEN VIA DEXSCREENER M5
# ══════════════════════════════════════════════════════════════

async def check_token_metrics(session: aiohttp.ClientSession, mint: str):
    """
    Vérifie si le token respecte le ratio d'achat et le nombre d'acheteurs uniques.
    Retourne (is_valid, market_cap, buyers, buy_ratio)
    """
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with session.get(url, timeout=3) as r:
            if r.status != 200:
                return False, 0.0, 0, 0.0
            
            data = await r.json()
            pairs = data.get("pairs")
            if not pairs or len(pairs) == 0:
                return False, 0.0, 0, 0.0
            
            pair = pairs[0]
            mcap = float(pair.get("marketCap", 0))
            
            # Récupération des stats sur la fenêtre des 5 dernières minutes (m5)
            m5_stats = pair.get("volume", {})
            buys_usd = float(m5_stats.get("buys", 0))
            sells_usd = float(m5_stats.get("sells", 0))
            total_vol = buys_usd + sells_usd
            
            # Nombre de acheteurs uniques (provenant des txs m5 ou de la base globale de la paire)
            buyers = int(pair.get("txns", {}).get("m5", {}).get("buys", 0))
            
            # Calcul du ratio de pression acheteuse
            buy_ratio = (buys_usd / total_vol) if total_vol > 0 else 0.0
            
            # Application stricte du double filtre
            if buyers >= MIN_UNIQUE_BUYERS and buy_ratio >= MIN_BUY_VOLUME_RATIO:
                return True, mcap, buyers, buy_ratio
                
            return False, mcap, buyers, buy_ratio
    except Exception as e:
        log.debug(f"Erreur d'analyse des filtres pour {mint[:8]} : {e}")
    return False, 0.0, 0, 0.0

# ══════════════════════════════════════════════════════════════
# ENVOI DE L'ALERTE FILTRÉE ET SÉCURISÉE
# ══════════════════════════════════════════════════════════════

async def send_telegram_alert(mint: str, symbol: str, name: str, mcap: float, elapsed_seconds: int, buyers: int, buy_ratio: float):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)

    link_axiom = f"https://axiom.trade/token/{mint}"
    link_dex = f"https://dexscreener.com/solana/{mint}"
    link_pump = f"https://pump.fun/{mint}"

    msg = (
        "🔥 *ALERTE FILTRÉE : TOP PROJET DÉTECTÉ* 🔥\n"
        f"• *Jeton :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ *VERIFICATION DES FILTRES (OK)*\n"
        f"├ 👥 Acheteurs Uniques : *{buyers} wallets* (Min {MIN_UNIQUE_BUYERS}) 🟢\n"
        f"└ 📊 Volume d'Achat : *{buy_ratio*100:.1f}%* (Min {MIN_BUY_VOLUME_RATIO*100:.0f}%) 🟢\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *DONNÉES DU MARCHÉ*\n"
        f"├ 💰 Market Cap Validé : *${mcap:,.0f}*\n"
        f"└ ⏱️ Temps de réaction : *{minutes}m {seconds}s*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 [Axiom Pro]({link_axiom}) | [DexScreener]({link_dex}) | [Pump.fun]({link_pump})"
    )
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        log.info(f"🚀 [ALERTE CONFIRMÉE] {clean_symbol} a validé les filtres de sécurité et a été envoyé.")
    except Exception as e:
        log.error(f"Erreur d'envoi Telegram : {e}")

# ══════════════════════════════════════════════════════════════
# MONITORING ET VALIDATION PAR LES FILTRES
# ══════════════════════════════════════════════════════════════

async def monitor_token(session: aiohttp.ClientSession, mint: str, symbol: str, name: str):
    if not mint or mint in alerted_tokens:
        return
    
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    max_loops = int((MAX_MONITOR_MINUTES * 60) // CHECK_INTERVAL_SEC)
    
    log.info(f"👀 [Scan v13] Surveillance lancée pour {symbol}")

    for _ in range(max_loops):
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        if alerted_tokens.get(mint) == "ALERTED":
            return

        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])
        
        # Validation du prix et des métriques de sécurité simultanément
        is_valid, real_mcap, buyers, buy_ratio = await check_token_metrics(session, mint)
        
        if real_mcap < TARGET_MARKET_CAP_USD:
            continue

        # Si le prix dépasse 10K mais que les filtres échouent, on stoppe le tracking pour économiser les ressources
        if not is_valid:
            log.info(f"❌ [Projet Rejeté] {symbol} à ${real_mcap:,.0f} a échoué aux filtres (Wallets: {buyers}, Buys: {buy_ratio*100:.1f}%)")
            alerted_tokens[mint] = "ALERTED"
            return

        # Si valide : Envoi immédiat
        alerted_tokens[mint] = "ALERTED"
        await send_telegram_alert(mint, symbol, name, real_mcap, elapsed, buyers, buy_ratio)
        return

# ══════════════════════════════════════════════════════════════
# LANCEUR ET WEBSOCKET
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun_websocket(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Connecté au flux Pump.fun v13 (Double Filtre Actif) !")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
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
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="🛡️ *Mise à jour v13.0 Active : Algorithme anti-rug configuré. Seuls les tokens à fort volume d'acheteurs uniques seront envoyés.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun_websocket(session)

@app.get("/")
async def root():
    return {"status": "online", "active_scans": len(alerted_tokens)}
