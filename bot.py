"""
╔══════════════════════════════════════════════════════════════╗
║     SOLANA PUMP.FUN SNIPER BOT — SURVEILLANCE DYNAMIQUE     ║
║  Suivi pendant 5 min : Alerte dès que Liq > 10K$ & Top10 < 29% ║
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
HELIUS_API_KEY   = os.environ["HELIUS_API_KEY"]

# ── FILTRES CONFIGURABLES ───────────────────────────────────
MAX_TOP10_HOLD_PCT    = 29         # Seuil max Top 10 Traders
MIN_LIQUIDITY_USD     = 10000      # Objectif de liquidité minimum

# ── TIMINGS DE SURVEILLANCE DYNAMIQUE ───────────────────────
CHECK_INTERVAL_SEC    = 15         # Re-vérifie le token toutes les 15 secondes
MAX_MONITOR_MINUTES   = 5          # Temps max de suivi pour un token (5 minutes)

PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
HELIUS_RPC            = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
DEXSCREENER_API       = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

PUMP_CURVES = ["5Q544fNpGWDbwS7oSyLEmu67JscBePySTWvKc4o9u8nd"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# COLLECTE DES DONNÉES
# ══════════════════════════════════════════════════════════════

async def fetch_dexscreener(session: aiohttp.ClientSession, mint: str) -> dict | None:
    try:
        url = DEXSCREENER_API.format(mint=mint)
        async with session.get(url, timeout=4) as r:
            if r.status == 200:
                data = await r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    p = pairs[0]
                    return {
                        "mcap": float(p.get("fdv", 0) or 0),
                        "liquidity": float(p.get("liquidity", {}).get("usd", 0) or 0),
                        "pair_url": p.get("url", "")
                    }
    except: pass
    return None

async def fetch_helius_top10_traders(session: aiohttp.ClientSession, mint: str) -> float:
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
        async with session.post(HELIUS_RPC, json=payload, timeout=4) as r:
            if r.status == 200:
                data = await r.json()
                accounts = data.get("result", {}).get("value", [])
                if accounts:
                    traders = [a for a in accounts if a.get("address") not in PUMP_CURVES]
                    top10_sum = sum(float(a.get("uiAmount", 0) or 0) for a in traders[:10])
                    TOTAL_SUPPLY = 1_000_000_000.0
                    return round((top10_sum / TOTAL_SUPPLY) * 100, 1)
    except: pass
    return 100.0

# ══════════════════════════════════════════════════════════════
# BOUCLE DE SURVEILLANCE CONTINUE PAR JETON
# ══════════════════════════════════════════════════════════════

async def monitor_token(session: aiohttp.ClientSession, event: dict):
    mint = event.get("mint")
    symbol = event.get("symbol", "?")
    if not mint or mint in alerted_tokens: return
    
    # Marque le token comme "en cours d'analyse" avec son timestamp de départ
    alerted_tokens[mint] = {"status": "monitoring", "start_time": time.time()}
    
    max_loops = int((MAX_MONITOR_MINUTES * 60) / CHECK_INTERVAL_SEC)
    log.info(f"👀 Surveillance lancée pour {symbol} ({mint[:8]}) pendant {MAX_MONITOR_MINUTES} min max...")

    for current_loop in range(max_loops):
        # Pause entre chaque vérification
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        
        # Sécurité : Si un autre thread l'a validé entre temps
        if alerted_tokens.get(mint) == "ALERTED":
            return

        # 1. Extraction des données marchés
        dex = await fetch_dexscreener(session, mint)
        liquidity = dex.get("liquidity", 0) if dex else 0
        mcap = dex.get("mcap", 0) if dex else 0

        # Log de suivi discret
        elapsed = int(time.time() - alerted_tokens[mint]["start_time"])
        log.info(f"⏱️ {symbol} ({elapsed}s) -> Liq: ${liquidity:,.0f} | Mcap: ${mcap:,.0f}")

        # Condition 1 : La liquidité n'a pas encore atteint les 10K$ ? On continue à attendre.
        if liquidity < MIN_LIQUIDITY_USD:
            continue

        # Condition 2 : La liquidité est supérieure à 10K$, on check le Top 10 Traders
        top10_pct = await fetch_helius_top10_traders(session, mint)

        if top10_pct > MAX_TOP10_HOLD_PCT:
            log.info(f"❌ {symbol} à {elapsed}s : Liq OK (${liquidity:,.0f}) mais Top 10 bloquant ({top10_pct}%)")
            # Optionnel : On peut décider de casser la boucle si le top 10 est irrattrapable (ex: > 50%)
            if top10_pct > 45.0:
                log.info(f"💀 {symbol} trop concentré d'un coup. Arrêt de la surveillance.")
                break
            continue

        # DÉCLENCHEMENT DE L'ALERTE : Les deux filtres sont au vert !
        alerted_tokens[mint] = "ALERTED"
        dex_url = dex.get("pair_url", "") if dex else ""
        
        msg = f"""🎯 *ALERTE DYNAMIQUE VALIDÉE*
• *Jeton :* {event.get('name', '?')} ({symbol})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *MARKET DATA (Détecté à {elapsed}s)*
├ 💧 Liquidité : *${liquidity:,.0f}* (Objectif >$10K ✅)
└ 💰 Market Cap : *${mcap:,.0f}*

👥 *DISTRIBUTION TRADERS (Style BullX)*
├ 🎯 Top 10 Holders : *{top10_pct}%* (Filtre <29% ✅)
└ 🟢 Statut : *Signal Validé en cours de pump*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Pump.fun](https://pump.fun/{mint}){f' | [DexScreener]({dex_url})' if dex_url else ''}"""

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            log.info(f"🚀 SIGNAL ENVOYÉ EN DIFFÉRÉ : {symbol} après {elapsed}s de suivi !")
        except Exception as e:
            log.error(f"Erreur Telegram : {e}")
        return

    # Si on arrive ici, le token a expiré sans jamais remplir les critères
    if alerted_tokens.get(mint) != "ALERTED":
        log.info(f"🛑 Fin de suivi (Expiré 5m) pour {symbol}. Critères non atteints.")

# ══════════════════════════════════════════════════════════════
# CONNEXION ET ENTRÉE
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Connecté au flux Pump.fun Stream En Direct !")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            # Lance un gestionnaire indépendant pour surveiller CE token précis en tâche de fond
                            asyncio.create_task(monitor_token(session, data))
                    except: pass
        except:
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="📈 *Démarrage du Bot avec Tracking Continu (5 min max par token).*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
