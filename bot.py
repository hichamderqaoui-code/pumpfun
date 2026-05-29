"""
╔══════════════════════════════════════════════════════════════╗
║         SOLANA PUMP.FUN SNIPER BOT — EDITION AXIOM PRO       ║
║   Zéro latence DexScreener — Data directes depuis Axiom API   ║
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

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ── FILTRES STRICTS DE TON ÉCRAN AXIOM ──────────────────────
MAX_TOP10_HOLD_PCT    = 29         # Limite Max Top 10 Holders (cercles verts)
MIN_LIQUIDITY_USD     = 10000      # Filtre demandé : Minimum 10,000$ de liquidité

# ── TIMINGS DE SURVEILLANCE CONTINU ─────────────────────────
CHECK_INTERVAL_SEC    = 8          # Fréquence de rafraîchissement très rapide
MAX_MONITOR_MINUTES   = 5          # Temps max de suivi pour un token (5 minutes)

PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
# URL de l'API d'indexation d'Axiom Pro
AXIOM_TOKEN_API       = "https://api.axiom.trade/v1/token/{mint}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# REQUÊTE DIRECTE SUR L'API INTERNE D'AXIOM PRO
# ══════════════════════════════════════════════════════════════

async def fetch_axiom_pro_metrics(session: aiohttp.ClientSession, mint: str) -> dict | None:
    """ Récupère les métriques exactes affichées sur l'interface d'Axiom Pro """
    try:
        url = AXIOM_TOKEN_API.format(mint=mint)
        # On imite un navigateur standard pour éviter d'être bloqué par leur pare-feu
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        async with session.get(url, headers=headers, timeout=4) as r:
            if r.status == 200:
                data = await r.json()
                token_data = data.get("data", {})
                
                # Extraction des valeurs exactes calculées par Axiom
                return {
                    "mcap": float(token_data.get("marketCapUsd", 0) or 0),
                    "liquidity": float(token_data.get("liquidityUsd", 0) or 0),
                    "top10_pct": float(token_data.get("top10Percentage", 100) or 100),
                    "holders_count": int(token_data.get("holdersCount", 0) or 0),
                    "is_indexed": True
                }
    except Exception as e:
        log.debug(f"Axiom pas encore synchronisé pour {mint[:8]}... TRACE: {e}")
    return None

# ══════════════════════════════════════════════════════════════
# TRACKING EN BOUCLE PAR JETON (POOLING AXIOM)
# ══════════════════════════════════════════════════════════════

async def monitor_token(session: aiohttp.ClientSession, event: dict):
    mint = event.get("mint")
    symbol = event.get("symbol", "?")
    if not mint or mint in alerted_tokens: return
    
    # Initialisation du tracker temporel
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    max_loops = int((MAX_MONITOR_MINUTES * 60) / CHECK_INTERVAL_SEC)
    
    log.info(f"👀 [Axiom Tracker] Début de suivi pour {symbol} ({mint[:8]})")

    for _ in range(max_loops):
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        # Sécurité si validé par un autre worker
        if alerted_tokens.get(mint) == "ALERTED": return

        # Interrogation directe d'Axiom
        axiom_data = await fetch_axiom_pro_metrics(session, mint)
        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])

        if not axiom_data:
            # Le token est trop récent, Axiom ne l'a pas encore traité dans sa base
            continue

        liquidity = axiom_data["liquidity"]
        top10_pct = axiom_data["top10_pct"]
        mcap = axiom_data["mcap"]

        log.info(f"⏱️ {symbol} ({elapsed}s) -> Liq Axiom: ${liquidity:,.0f} | Top 10 Axiom: {top10_pct}%")

        # ÉTAPE 1 : Le token doit obligatoirement franchir ton seuil de 10K$ de liquidité
        if liquidity < MIN_LIQUIDITY_USD:
            continue

        # ÉTAPE 2 : Si la liquidité est validée, on check immédiatement la concentration des traders
        if top10_pct > MAX_TOP10_HOLD_PCT:
            log.info(f"❌ Rejeté {symbol} ({elapsed}s) : Liq OK (${liquidity:,.0f}) mais Top 10 trop lourd ({top10_pct}%)")
            # Si le Top 10 est trop massif dès le début (ex: >40%), le dev a packer le token. On coupe le suivi.
            if top10_pct > 40.0: 
                break
            continue

        # FILTRES VERTS ENTIÈREMENT VALIDES -> ENVOI DU PING TELEGRAM
        alerted_tokens[mint] = "ALERTED"
        
        msg = f"""🎯 *ALERTE FLUX AXIOM PRO DETECTÉE*
• *Jeton :* {event.get('name', '?')} ({symbol})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *METRICS DIRECTES AXIOM*
├ 💧 Liquidité : *${liquidity:,.0f}* (Filtre: >$10K ✅)
├ 💰 Market Cap : *${mcap:,.0f}*
└ ⏱️ Détections : *{elapsed}s après création*

👥 *DISTRIBUTION TRADERS*
├ 🎯 Top 10 Holders : *{top10_pct}%* (Filtre: <29% ✅)
└ 👥 Vrais Wallets : *{axiom_data['holders_count']}*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Axiom Trade](https://axiom.trade/token/{mint}) | [Pump.fun](https://pump.fun/{mint})"""

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            log.info(f"🚀 [SUCCÈS] Alerte envoyée pour {symbol} à ${liquidity:,.0f} après {elapsed}s !")
        except Exception as e:
            log.error(f"Erreur d'envoi Telegram : {e}")
        return

    # Nettoyage si le token n'a pas décollé au bout de 5 minutes
    if alerted_tokens.get(mint) != "ALERTED":
        log.info(f"🛑 Fin de suivi (5 min écoulées) pour {symbol}.")

# ══════════════════════════════════════════════════════════════
# BOUCLE DE GESTION DU FLUX WS
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Connecté au flux de création Pump.fun !")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            asyncio.create_task(monitor_token(session, data))
                    except: pass
        except:
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="🚀 *Démarrage du Bot en Mode Moteur Axiom Pro (Vérification toutes les 8s).*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
