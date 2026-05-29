"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — FILTRE LIQUIDITÉ         ║
║   Filtre Liquidité > 10K$ + Vrai Top 10 Traders < 29%        ║
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

# ── FILTRES STRICTS AXIOM PRO ───────────────────────────────
MAX_TOP10_HOLD_PCT    = 29         # Limite du Top 10 des vrais acheteurs
MIN_LIQUIDITY_USD     = 10000      # Filtre demandé : Minimum 10,000$ de liquidité
WAIT_BEFORE_CHECK_SEC = 45         # Laisse 45 secondes au lancement pour se développer

PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
HELIUS_RPC            = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
DEXSCREENER_API       = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

# Liste des adresses système (bonding curve) à ignorer pour le calcul des humains
PUMP_CURVES = ["5Q544fNpGWDbwS7oSyLEmu67JscBePySTWvKc4o9u8nd"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# COLLECTE ET ANALYSE DES DONNÉES
# ══════════════════════════════════════════════════════════════

async def fetch_dexscreener(session: aiohttp.ClientSession, mint: str) -> dict | None:
    try:
        url = DEXSCREENER_API.format(mint=mint)
        async with session.get(url, timeout=5) as r:
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
    """ Calcule le % du top 10 des traders par rapport à la supply totale (Style BullX) """
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
        async with session.post(HELIUS_RPC, json=payload, timeout=5) as r:
            if r.status == 200:
                data = await r.json()
                accounts = data.get("result", {}).get("value", [])
                if accounts:
                    # On filtre pour enlever la bonding curve
                    traders = [a for a in accounts if a.get("address") not in PUMP_CURVES]
                    
                    # On fait la somme des jetons du Top 10 des vrais traders
                    top10_sum = sum(float(a.get("uiAmount", 0) or 0) for a in traders[:10])
                    
                    # Supply globale fixe sur Pump.fun = 1 Milliard
                    TOTAL_SUPPLY = 1_000_000_000.0
                    return round((top10_sum / TOTAL_SUPPLY) * 100, 1)
    except: pass
    return 100.0

# ══════════════════════════════════════════════════════════════
# LOGIQUE DE TRAITEMENT
# ══════════════════════════════════════════════════════════════

async def process_token(session: aiohttp.ClientSession, event: dict) -> None:
    mint = event.get("mint")
    if not mint or mint in alerted_tokens: return
    alerted_tokens[mint] = time.time()

    # Pause de 45 secondes pour laisser le volume et la liquidité monter
    await asyncio.sleep(WAIT_BEFORE_CHECK_SEC)

    # 1. On vérifie d'abord la liquidité via DexScreener
    dex = await fetch_dexscreener(session, mint)
    liquidity = dex.get("liquidity", 0) if dex else 0
    mcap = dex.get("mcap", 0) if dex else 0

    if liquidity < MIN_LIQUIDITY_USD:
        log.info(f"❌ Rejeté {event.get('symbol')} : Liquidité insuffisante (${liquidity:,.0f} < $10K)")
        return

    # 2. Si la liquidité est bonne (>10K$), on analyse la répartition du Top 10 des traders
    top10_pct = await fetch_helius_top10_traders(session, mint)

    if top10_pct > MAX_TOP10_HOLD_PCT:
        log.info(f"❌ Rejeté {event.get('symbol')} : Liquidité OK (${liquidity:,.0f}) mais Top 10 trop haut ({top10_pct}%)")
        return

    # 3. ENVOI DE L'ALERTE CAR LES DEUX CRITÈRES SONT VALIDES
    dex_url = dex.get("pair_url", "") if dex else ""
    
    msg = f"""🎯 *PÉPITE VALIDÉE (Liq > 10K$ & Top 10 < 29%)*
• *Jeton :* {event.get('name', '?')} ({event.get('symbol', '?')})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *MARKET DATA (DexScreener)*
├ 💧 Liquidité : *${liquidity:,.0f}* (Filtre: >$10,000)
└ 💰 Market Cap : *${mcap:,.0f}*

👥 *DISTRIBUTION TRADERS (Style BullX)*
├ 🎯 Top 10 Holders : *{top10_pct}%* (Filtre: <29%)
└ 🟢 Statut : *Distribution Saine*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Pump.fun](https://pump.fun/{mint}){f' | [DexScreener]({dex_url})' if dex_url else ''}"""

    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        log.info(f"🚀 SIGNAL ENVOYÉ : {event.get('symbol')} | Liq: ${liquidity:,.0f} | Top 10: {top10_pct}%")
    except Exception as e:
        log.error(f"Erreur d'envoi Telegram : {e}")

# ══════════════════════════════════════════════════════════════
# CONNEXION AU FLUX
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Connecté au flux Pump.fun !")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            asyncio.create_task(process_token(session, data))
                    except: pass
        except:
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚙️ *Mise à jour : Filtre Liquidité Min 10K$ + Top 10 < 29% activé.*")
    except: pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
