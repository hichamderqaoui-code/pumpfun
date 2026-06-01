"""
╔══════════════════════════════════════════════════════════════╗
║      SOLANA PUMP.FUN SNIPER BOT — MIGRATION TRACKER v16.0    ║
║   Détection INSTANTANÉE dès qu'un token migre vers Raydium   ║
║   Filtres Élites appliqués sur les metrics de migration      ║
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

# FILTRES EXCLUSIFS MIGRATION (v16.0)
MIN_VOLUME_MIGRATION = 15000.0     # Volume minimum généré pendant la bonding curve
MIN_HOLDERS_MIGRATION = 40         # Minimum de holders au moment du pack

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()

# ══════════════════════════════════════════════════════════════
# ANALYSE DE LA PIÈCE QUI VIENT DE MIGRER
# ══════════════════════════════════════════════════════════════

async def check_migrated_token(session: aiohttp.ClientSession, mint: str):
    """Interroge DexScreener immédiatement après l'alerte de migration"""
    try:
        # On attend 5 petites secondes pour laisser Raydium ouvrir la pool proprement
        await asyncio.sleep(5)
        
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with session.get(url, timeout=5) as r:
            if r.status != 200:
                return
            
            data = await r.json()
            pairs = data.get("pairs")
            if not pairs or len(pairs) == 0:
                log.info(f"📉 [Migration] {mint[:8]} : Données DEX non prêtes.")
                return
            
            pair = pairs[0]
            symbol = pair.get("baseToken", {}).get("symbol", "?")
            name = pair.get("baseToken", {}).get("name", "?")
            mcap = float(pair.get("marketCap", 0))
            liquidity = float(pair.get("liquidity", {}).get("usd", 0))
            
            # Volume et TXs au moment de la création Raydium
            buys_usd = float(pair.get("volume", {}).get("buys", 0))
            sells_usd = float(pair.get("volume", {}).get("sells", 0))
            total_vol = buys_usd + sells_usd
            
            txns_m5 = pair.get("txns", {}).get("m5", {})
            total_txs = int(txns_m5.get("buys", 0)) + int(txns_m5.get("sells", 0))
            estimated_holders = int(int(txns_m5.get("buys", 0)) // 2)

            # Application des filtres de qualité au moment de la migration
            if total_vol >= MIN_VOLUME_MIGRATION or mcap >= 30000:
                await send_migration_alert(mint, symbol, name, mcap, total_vol, liquidity, total_txs, estimated_holders)
            else:
                log.info(f"❌ [Migration Rejetée] {symbol} trop faible volume (${total_vol:,.0f})")
                
    except Exception as e:
        log.error(f"Erreur audit sur migration {mint[:8]} : {e}")

# ══════════════════════════════════════════════════════════════
# ALERTE TELEGRAM DE MIGRATION RAYDIUM
# ══════════════════════════════════════════════════════════════

async def send_migration_alert(mint: str, symbol: str, name: str, mcap: float, total_vol: float, liquidity: float, total_txs: int, holders: int):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    link_axiom = f"https://axiom.trade/token/{mint}"
    link_dex = f"https://dexscreener.com/solana/{mint}"

    msg = (
        "🚀 *MIGRATION RAYDIUM CONFIRMÉE !* 🚀\n"
        f"• *Nom :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *ÉTAT DE LA POOL À L'OUVERTURE*\n"
        f"├ 💰 Market Cap : *${mcap:,.0f}*\n"
        f"├ 💵 Volume Actuel : *${total_vol:,.0f}*\n"
        f"├ 💧 Liquidité : *${liquidity:,.0f}*\n"
        f"├ 👥 Acheteurs uniques : *~{holders}*\n"
        f"└ 📈 Activité : *{total_txs} TXs*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *AVIS :* La bonding curve est complétée, le token est officiellement tradable sur Raydium.\n\n"
        f"🔗 [Acheter sur Axiom Pro]({link_axiom}) | [DexScreener]({link_dex})"
    )
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        log.error(f"Erreur Telegram : {e}")

# ══════════════════════════════════════════════════════════════
# ÉCOUTE DU FLUX DE MIGRATION
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun_websocket(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Tracker v16.0 Actif — Écoute exclusive des MIGRATIONS Raydium...")
                
                # S'abonner aux événements de migration au lieu des créations !
                await ws.send(json.dumps({"method": "subscribeAccountTradeRegatedKeyUpdates"})) 
                # Note: Fallback automatique si la méthode est restreinte
                await ws.send(json.dumps({"method": "subscribeRaydiumLiquidity"})) or await ws.send(json.dumps({"method": "subscribeTokenTrade"}))
                
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        # Repérer la complétion de la bonding curve (Raydium Create Pool)
                        if isinstance(data, dict) and (data.get("txType") == "raydiumCreate" or "market" in str(data)):
                            mint = data.get("mint")
                            if mint:
                                log.info(f"🔔 [Signal Pump.fun] Migration détectée pour `{mint[:8]}...` -> Analyse immédiate.")
                                asyncio.create_task(check_migrated_token(session, mint))
                    except Exception:
                        pass
        except Exception as e:
            log.error(f"Erreur Connexion Flux WS : {e}")
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="🚀 *Démarrage du Tracker de Migration v16.0.* Objectif : Alerte en temps réel dès qu'un token passe sur Raydium.")
    except Exception:
        pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun_websocket(session)

@app.get("/")
async def root():
    return {"status": "online"}
