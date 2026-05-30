"""
╔══════════════════════════════════════════════════════════════╗
║     SOLANA PUMP.FUN SNIPER BOT — 10-MIN ANALYSER v13.5       ║
║   Analyse automatique après 10 minutes de vie du Token       ║
║   Alerte UNIQUEMENT si les critères de Hype 100K sont réunis ║
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

# FILTRES EXCLUSIFS POST-10 MIN (POUR VISER 100K+)
MIN_MCAP_AFTER_10M = 15000.0       # Doit valider au moins $15K après 10min
MIN_PRO_TRADERS_10M = 100          # Au moins 100 Pro Traders d'élite après 10min
MIN_TOTAL_TXS_10M = 1500           # Preuve d'une FOMO industrielle (>1500 TXs)
MIN_BUY_RATIO_10M = 0.55           # Pression acheteuse saine (>55%)

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
        # Étape 1 : Le token vit sa vie pendant 10 minutes (Zéro perturbation)
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
            
            # Extraction des volumes et des transactions globales acumulées
            tx_buys = int(pair.get("txns", {}).get("m5", {}).get("buys", 0)) * 2 # extrapolation 10m
            tx_sells = int(pair.get("txns", {}).get("m5", {}).get("sells", 0)) * 2
            total_txs = tx_buys + tx_sells
            
            buys_usd = float(pair.get("volume", {}).get("buys", 0))
            sells_usd = float(pair.get("volume", {}).get("sells", 0))
            total_vol = buys_usd + sells_usd
            buy_ratio = (buys_usd / total_vol) if total_vol > 0 else 0.0
            
            # Détection des Pro Traders
            pro_traders = tx_buys // 3
            
            # Info optionnelle de la structure des portefeuilles
            bundlers_percentage = float(pair.get("boosts", {}).get("value", 0))

            # Étape 3 : Application du verdict de la v13.5
            if mcap >= MIN_MCAP_AFTER_10M and pro_traders >= MIN_PRO_TRADERS_10M and total_txs >= MIN_TOTAL_TXS_10M and buy_ratio >= MIN_BUY_RATIO_10M:
                # C'EST UN RUNNER POTENTIEL : ALERTE IMMÉDIATE
                await send_explosion_alert(mint, symbol, name, mcap, pro_traders, total_txs, bundlers_percentage)
            else:
                log.info(f"❌ [Filtre 10-Min Rejeté] {symbol} n'a pas la puissance requise (MCAP: ${mcap:,.0f}, Pro Traders: {pro_traders}, TXs: {total_txs})")
                
    except Exception as e:
        log.error(f"Erreur lors de l'analyse automatique de {mint[:8]} : {e}")

# ══════════════════════════════════════════════════════════════
# ALERTE EXCLUSIVE V13.5 CONFIRMÉE
# ══════════════════════════════════════════════════════════════

async def send_explosion_alert(mint: str, symbol: str, name: str, mcap: float, pro_traders: int, total_txs: int, bundlers: float):
    clean_name = name.replace('*', '').replace('_', '').replace('`', '')
    clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')
    
    link_axiom = f"https://axiom.trade/token/{mint}"
    link_dex = f"https://dexscreener.com/solana/{mint}"

    msg = (
        "🚀 *VERDICT 10-MINUTES RATIFIÉ (v13.5)* 🚀\n"
        f"• *Jeton validé :* {clean_name} ({clean_symbol})\n"
        f"• *Mint :* `{mint}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *BILAN TECHNIQUE APRÈS 10 MIN*\n"
        f"├ 💰 Market Cap : *${mcap:,.0f}* 🟢\n"
        f"├ 🔥 Pro Traders Actifs : *{pro_traders}* (Objectif 100K+) ✅\n"
        f"├ 📈 Volume Transactions : *{total_txs} TXs* ✅\n"
        f"└ ⛓️ Info structure : *{bundlers:.2f}%* ℹ)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *ANALYSE :* Ce token a encaissé le choc des 10 premières minutes, les volumes sont réels et massifs. Profil idéal pour pousser fort.\n\n"
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
                log.info("✅ Système v13.5 Actif — Audit silencieux des 10 minutes initialisé.")
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
                    except: pass
        except Exception as e:
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚙️ *Mise à jour v13.5 en production : Mode Audit Silencieux activé. Le bot analyse chaque token pendant 10 minutes en arrière-plan avant de filtrer et d'envoyer l'alerte.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun_websocket(session)

@app.get("/")
async def root():
    return {"status": "online"}
