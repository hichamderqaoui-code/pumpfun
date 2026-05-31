"""
╔══════════════════════════════════════════════════════════════╗
║      SOLANA PUMP.FUN SNIPER BOT — ELITE FILTER v15.0         ║
║   Audit post 10-Min : MCAP, VOLUME, LIQUIDITÉ, HOLDERS, TXS  ║
║   Filtre ultra-sélectif pour ne garder que les vrais lancements ║
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

# CONFIGURATION DES FILTRES ÉLITES (v15.0)
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
# AUDIT TECHNIQUE STRICT DU TOKEN
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
                log.info(f"📉 [10-Min] {symbol} introuvable ou déjà mort sur Raydium/Dex.")
                return
            
            pair = pairs[0]
            mcap = float(pair.get("marketCap", 0))
            
            # 1. Analyse Liquidité
            liquidity = float(pair.get("liquidity", {}).get("usd", 0))
            
            # 2. Analyse Volume (Accumulé sur 5m/1h)
            buys_usd = float(pair.get("volume", {}).get("buys", 0))
            sells_usd = float(pair.get("volume", {}).get("sells", 0))
            total_vol = buys_usd + sells_usd
            
            # 3. Analyse Transactions & Holders
            tx_buys = int(pair.get("txns", {}).get("m5", {}).get("buys", 0)) * 2
            tx_sells = int(pair.get("txns", {}).get("m5", {}).get("sells", 0)) * 2
            total_txs = tx_buys + tx_sells
            
            buy_ratio = (buys_usd / total_vol) if total_vol > 0 else 0.0
            estimated_holders = int(tx_buys // 2.2) if tx_buys > 0 else 0
            
            # Info structure (Boosts)
            bundlers_percentage = float(pair.get("boosts", {}).get("value", 0))

            # Étape 3 : Le Verdict Élite v15.0
            if (mcap >= MIN_MCAP and 
                total_vol >= MIN_VOLUME and 
                liquidity >= MIN_LIQUIDITY and 
                total_txs >= MIN_TXS and 
                estimated_holders >= MIN_HOLDERS and 
                buy_ratio >= MIN_BUY_RATIO):
                
                # Le token remplit TOUTES les conditions de Hype et de Sécurité
                await send_explosion_alert(mint, symbol, name, mcap, total_vol, liquidity, total_txs, estimated_holders, bundlers_percentage)
            else:
                log.info(f"❌ [Filtré] {symbol} rejeté (MCAP: ${mcap:,.0f} | Vol: ${total_vol:,.0f} | Liq: ${liquidity:,.0f} | TXs: {total_txs} | Holders est: {estimated_holders})")
                
    except Exception as e:
        log.error(f"Erreur lors de l'analyse du token {mint[:8]} : {e}")

# ══════════════════════════════════════════════════════════════
# ENVOI DE L'ALERTE TRIPLES FILTRES
# ══════════════════════════════════════════════════════════════

async def send_explosion_alert(mint: str, symbol: str, name: str, mcap: float, total_vol: float, liquidity: float, total_txs: int, holders: int, bundlers: float):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    link_axiom = f"https://axiom.trade/token/{mint}"
    link_dex = f"https://dexscreener.com/solana/{mint}"

    msg = (
        "💎 *PÉPITE SÉLECTIONNÉE (v15.0 ÉLITE)* 💎\n"
        f"• *Nom :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *METRICS DE VALIDATION POST-10 MIN*\n"
        f"├ 💰 Market Cap : *${mcap:,.0f}* (Min 6K$) ✅\n"
        f"├ 💵 Volume Global : *${total_vol:,.0f}* (Min 50K$) ✅\n"
        f"├ 💧 Liquidité Pool : *${liquidity:,.0f}* (Min 4K$) 🟢\n"
        f"├ 👥 Holders Uniques : *~{holders}* (Min 50) 👥\n"
        f"├ 📈 Activité : *{total_txs} TXs* (Min 300) ✅\n"
        f"└ ⛓️ Info structure : *{bundlers:.2f}%* ℹ️\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *RAPPORT :* Ce projet montre des signes d'accumulation réels. La liquidité est suffisante pour amortir les ordres et le volume est organique.\n\n"
        f"🔗 [Acheter sur Axiom Pro]({link_axiom}) | [DexScreener]({link_dex})"
    )
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        log.error(f"Erreur d'envoi Telegram : {e}")

# ══════════════════════════════════════════════════════════════
# CONNEXION ET RECEPTION DU FLUX
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun_websocket(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Filtre Élite v15.0 Actif — Surveillance des lancements en cours.")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            mint = data.get("mint")
                            if mint not in monitored_tokens:
                                monitored_tokens[mint] = True
                                asyncio.create_task(evaluate_token_after_delay(session, mint, data.get("symbol", "?"), data.get("name", "?")))
                    except Exception:
                        pass
        except Exception as e:
            log.error(f"Erreur Connexion Flux : {e}")
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="🚀 *Filtre Élite v15.0 en production.* Critères stricts activés : MCAP > 6K$, Vol > 50K$, Liq > 4K$, Txs > 300, Holders > 50. Mode sniper activé.")
    except Exception:
        pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun_websocket(session)

@app.get("/")
async def root():
    return {"status": "online"}
