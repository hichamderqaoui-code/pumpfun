"""
╔══════════════════════════════════════════════════════════════╗
║         SOLANA PUMP.FUN SNIPER BOT — VERSION DILUTION v2     ║
║   Suivi continu Axiom Pro — Idéal pour chasser les x50/x100  ║
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

# ── FILTRES CONFIGURABLES ───────────────────────────────────
MAX_TOP10_HOLD_PCT    = 29.0       # Seuil cible pour le Top 10 Holders
MIN_LIQUIDITY_USD     = 10000.0    # Objectif de liquidité minimum ($10,000)

# ── TIMINGS DE SURVEILLANCE CONTINUE ─────────────────────────
CHECK_INTERVAL_SEC    = 8          # Fréquence de rafraîchissement (toutes les 8s)
MAX_MONITOR_MINUTES   = 5          # Durée maximale de suivi pour chaque token (5 min)

PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
AXIOM_TOKEN_API       = "https://api.axiom.trade/v1/token/{mint}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# REQUÊTE DIRECTE SUR L'API D'INDEXATION D'AXIOM PRO
# ══════════════════════════════════════════════════════════════

async def fetch_axiom_pro_metrics(session: aiohttp.ClientSession, mint: str) -> dict | None:
    """ Récupère les métriques en temps réel calculées par l'indexeur d'Axiom """
    try:
        url = AXIOM_TOKEN_API.format(mint=mint)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        async with session.get(url, headers=headers, timeout=4) as r:
            if r.status == 200:
                data = await r.json()
                token_data = data.get("data", {})
                
                if not token_data:
                    return None
                    
                return {
                    "mcap": float(token_data.get("marketCapUsd", 0) or 0),
                    "liquidity": float(token_data.get("liquidityUsd", 0) or 0),
                    "top10_pct": float(token_data.get("top10Percentage", 100) or 100),
                    "holders_count": int(token_data.get("holdersCount", 0) or 0)
                }
    except Exception as e:
        log.debug(f"Erreur ou latence d'indexation Axiom pour {mint[:8]} : {e}")
    return None

# ══════════════════════════════════════════════════════════════
# MONITORING ET ATTENTE DE DILUTION (PAS DE BREAK)
# ══════════════════════════════════════════════════════════════

async def monitor_token(session: aiohttp.ClientSession, event: dict):
    mint = event.get("mint")
    symbol = event.get("symbol", "?")
    if not mint or mint in alerted_tokens: return
    
    # Enregistrement du token avec son heure de naissance
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    max_loops = int((MAX_MONITOR_MINUTES * 60) / CHECK_INTERVAL_SEC)
    
    log.info(f"👀 [Tracking] Entrée en surveillance pour {symbol} ({mint[:8]})")

    for _ in range(max_loops):
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        # Sécurité si validé ou annulé par ailleurs
        if alerted_tokens.get(mint) == "ALERTED": return

        axiom_data = await fetch_axiom_pro_metrics(session, mint)
        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])

        # Si Axiom n'a pas encore créé la fiche du token, on patiente
        if not axiom_data:
            log.info(f"⏱️ {symbol} ({elapsed}s) -> Axiom : Fiche non initialisée.")
            continue

        liquidity = axiom_data["liquidity"]
        top10_pct = axiom_data["top10_pct"]
        mcap = axiom_data["mcap"]

        log.info(f"⏱️ {symbol} ({elapsed}s) -> Liq: ${liquidity:,.0f} | Top 10 Axiom: {top10_pct}%")

        # CRITÈRE 1 : On attend impérativement que la liquidité passe les $10,000
        if liquidity < MIN_LIQUIDITY_USD:
            continue

        # GESTION DU FAUX POSITIF 100% : Si Axiom renvoie pile 100% ou 0%, les données de holders 
        # ne sont pas encore prêtes sur leur serveur. On attend la vraie valeur.
        if top10_pct >= 99.9 or top10_pct == 0.0:
            log.info(f"⏳ {symbol} ({elapsed}s) -> Liquide (${liquidity:,.0f}) mais calcul des holders en cours chez Axiom...")
            continue

        # CRITÈRE 2 : On vérifie si la distribution s'est affinée sous notre seuil
        if top10_pct > MAX_TOP10_HOLD_PCT:
            continue

        # LES DEUX FEUX SONT AU VERT -> DÉCLENCHEMENT DE L'ALERTE
        alerted_tokens[mint] = "ALERTED"
        
        # Nettoyage des caractères spéciaux pour le Markdown Telegram
        clean_name = event.get('name', '?').replace('*', '').replace('_', '').replace('`', '')
        clean_symbol = symbol.replace('*', '').replace('_', '').replace('`', '')

        msg = f"""🎯 *PÉPITE DILUÉE & VALIDÉE*
• *Jeton :* {clean_name} ({clean_symbol})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *METRICS EN DIRECT D'AXIOM*
├ 💧 Liquidité : *${liquidity:,.0f}* (Objectif >$10K ✅)
├ 💰 Market Cap : *${mcap:,.0f}*
└ ⏱️ Temps de tracking : *{elapsed}s après création*

👥 *DISTRIBUTION (Style ALIENS)*
├ 🎯 Top 10 Holders : *{top10_pct}%* (Seuil <29% ✅)
└ 👥 Total Holders : *{axiom_data['holders_count']}*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Axiom Trade](https://axiom.trade/token/{mint}) | [Pump.fun](https://pump.fun/{mint})"""

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            log.info(f"🚀 [ALERTE SUCCÈS] Signal envoyé pour {symbol} après {elapsed}s de suivi (Top 10: {top10_pct}%)")
        except Exception as e:
            log.error(f"Erreur d'envoi Telegram pour {symbol} : {e}")
        return

    # Si on sort de la boucle sans alerte, le token n'a pas rempli les critères en 5 min
    if alerted_tokens.get(mint) != "ALERTED":
        log.info(f"🛑 Fin de suivi (Expiré 5m) pour {symbol}.")

# ══════════════════════════════════════════════════════════════
# CONNEXION FLUX DE CRÉATION
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
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚡ *Mise à jour v2 : Mode Anti-Bug 100% Axiom activé.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
