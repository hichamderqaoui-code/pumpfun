"""
╔══════════════════════════════════════════════════════════════╗
║         SOLANA PUMP.FUN SNIPER BOT — REAL DATA v12           ║
║    Zéro Simulation — Vrais Filtres MCAP Blockchain Direct    ║
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
# REQUÊTE DE PRIX RÉEL SANS INDEXATION (DEXSCREENER FAST API)
# ══════════════════════════════════════════════════════════════

async def get_real_market_cap(session: aiohttp.ClientSession, mint: str) -> float:
    """Interroge l'API la plus réactive du marché pour avoir le vrai MCAP en temps réel"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with session.get(url, timeout=3) as r:
            if r.status == 200:
                data = await r.json()
                pairs = data.get("pairs")
                if pairs and len(pairs) > 0:
                    # On extrait le market cap de la paire la plus active
                    mcap = float(pairs[0].get("marketCap", 0))
                    return mcap
    except Exception as e:
        log.debug(f"Erreur lecture prix pour {mint[:8]} : {e}")
    return 0.0

# ══════════════════════════════════════════════════════════════
# ENVOI DE L'ALERTE TELEGRAM
# ══════════════════════════════════════════════════════════════

async def send_telegram_alert(mint: str, symbol: str, name: str, mcap: float, elapsed_seconds: int):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)

    msg = (
        "🎯 *ALERTE PUMP 10K ATTEINT (<10 MIN)*\n"
        f"• *Jeton :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *DONNÉES REÉLLES DE LA CHAÎNE*\n"
        f"├ 💰 Vrai Market Cap : *${mcap:,.0f}* ✅\n"
        "├ ⏱️ Statut : *Filtre <10 min Validé*\n"
        f"└ ⏱️ Temps écoulé : *{minutes}m {seconds}s*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 [DexScreener](https://dexscreener.com/solana/{mint}) | [Pump.fun](https://pump.fun/{mint})"
    )
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        log.info(f"🔥 [ALERTE RÉELLE] Signal envoyé pour {clean_symbol} (${mcap:,.0f})")
    except Exception as e:
        log.error(f"Erreur d'envoi Telegram : {e}")

# ══════════════════════════════════════════════════════════════
# MONITORING FILTRÉ DE O À 10 MINUTES
# ══════════════════════════════════════════════════════════════

async def monitor_token(session: aiohttp.ClientSession, mint: str, symbol: str, name: str):
    if not mint or mint in alerted_tokens:
        return
    
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    max_loops = int((MAX_MONITOR_MINUTES * 60) // CHECK_INTERVAL_SEC)
    
    log.info(f"👀 [Tracking Réel] Début des 10 min de surveillance pour {symbol}")

    for _ in range(max_loops):
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        if alerted_tokens.get(mint) == "ALERTED":
            return

        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])
        
        # APPEL DU VRAI MARKET CAP EN DIRECT
        real_mcap = await get_real_market_cap(session, mint)
        
        if real_mcap <= 0:
            continue

        log.info(f"⏱️ {symbol} ({elapsed}s / 600s) -> Vrai MCAP : ${real_mcap:,.0f}")

        # LE FILTRE STRICT : On ne déclenche QUE si le vrai MCAP dépasse $10K
        if real_mcap < TARGET_MARKET_CAP_USD:
            continue

        # BLOCAGE ET ENVOI DE L'ALERTE VALIDE
        alerted_tokens[mint] = "ALERTED"
        await send_telegram_alert(mint, symbol, name, real_mcap, elapsed)
        return

    if alerted_tokens.get(mint) != "ALERTED":
        log.info(f"🛑 [Expiré] {symbol} a dépassé les 10 min sans atteindre les 10K.")

# ══════════════════════════════════════════════════════════════
# CONNEXION FLUX WEBSOCKET PUMP.FUN
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun_websocket(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Connecté au flux de création Pump.fun !")
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

# ══════════════════════════════════════════════════════════════
# ENTRÉE FASTAPI
# ═══════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚡ *Mise à jour v12 : Mode Production Réelle (Filtres 10K stricts et sans simulation) activé.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun_websocket(session)

@app.get("/")
async def root():
    return {"status": "online", "active_scans": len(alerted_tokens)}
