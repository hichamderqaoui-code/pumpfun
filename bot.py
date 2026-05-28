"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — RAILWAY + HELIUS         ║
║   Filtre Temporisé 45s | Top 10 < 29% | Sécurisé & Calme     ║
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

# ──────────────────────────────────────────────────────────────
# CONFIG (variables Railway / .env)
# ──────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
HELIUS_API_KEY   = os.environ["HELIUS_API_KEY"]

# ── FILTRES ADAPTÉS POUR UN FLUX PROPRE ──────────────────────
MAX_TOP10_HOLD_PCT    = 29         # Ton filtre Axiom Pro strict
MIN_MCAP_USD          = 7_000      # Élimine les tokens mort-nés instantanément
WAIT_BEFORE_CHECK_SEC = 45         # Laisse 45s aux holders pour se diluer et à l'admin pour buy

# ── ENDPOINTS ──────────────────────────────────────────────
PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
HELIUS_RPC            = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
DEXSCREENER_API       = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()
alerted_tokens = {}

# ══════════════════════════════════════════════════════════════
# DATA FETCHERS
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

async def fetch_helius_holders(session: aiohttp.ClientSession, mint: str) -> dict:
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
        async with session.post(HELIUS_RPC, json=payload, timeout=5) as r:
            if r.status == 200:
                data = await r.json()
                accounts = data.get("result", {}).get("value", [])
                if accounts:
                    total_supply = sum(float(a.get("uiAmount", 0) or 0) for a in accounts)
                    if total_supply > 0:
                        # Exclure le plus gros wallet s'il s'agit de la bonding curve à 100% non mise à jour
                        top10_pct = sum(float(a.get("uiAmount", 0) or 0) for a in accounts[:10]) / total_supply * 100
                        return {"holder_count": len(accounts), "top10_pct": round(top10_pct, 1)}
    except: pass
    return {"holder_count": 0, "top10_pct": 100}

# ══════════════════════════════════════════════════════════════
# FILTRAGE CHIRURGICAL AFTER-MARKET
# ══════════════════════════════════════════════════════════════

async def process_token(session: aiohttp.ClientSession, event: dict) -> None:
    mint = event.get("mint")
    if not mint: return

    # 1. BARRIÈRE ANTI-DOUBLON STRICTE ET IMMÉDIATE
    if mint in alerted_tokens: return
    alerted_tokens[mint] = time.time()

    # 2. TEMPORISATION : On attend 45 secondes que le marché bouge
    await asyncio.sleep(WAIT_BEFORE_CHECK_SEC)

    # 3. FETCH DATA EN PARALLÈLE
    dex, holders = await asyncio.gather(
        fetch_dexscreener(session, mint),
        fetch_helius_holders(session, mint),
        return_exceptions=True
    )

    dex = dex if isinstance(dex, dict) else None
    holders = holders if isinstance(holders, dict) else {"top10_pct": 100, "holder_count": 0}

    # 4. FILTRAGE DÉFINITIF
    mcap = dex.get("mcap", 0) if dex else 0
    top10_pct = holders.get("top10_pct", 100)

    # Filtre de capitalisation minimal pour écarter les rug instantanés
    if mcap < MIN_MCAP_USD:
        log.info(f"❌ Rejeté {event.get('symbol')} : Mcap trop bas (${mcap:,.0f})")
        return

    # Filtre strict de ton Top 10 Axiom Pro
    if top10_pct > MAX_TOP10_HOLD_PCT:
        log.info(f"❌ Rejeté {event.get('symbol')} : Concentration trop haute ({top10_pct}%)")
        return

    # 5. ENVOI DE L'ALERTE QUALIFIÉE
    msg = f"""🚀 *PÉPITE VALIDÉE (<29% TOP 10)* — `{event.get('symbol', '?')}`
`{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *MARKET DATA (Après {WAIT_BEFORE_CHECK_SEC}s)*
├ 💰 Market Cap : *${mcap:,.0f}*
├ 💧 Liquidité : *${dex.get('liquidity', 0):,.0f}*

👥 *HOLDERS*
├ 🎯 Top 10 Hold : *{top10_pct}%* (Filtre: <29%)
└ 👥 Total Wallets : *{holders.get('holder_count', '?')}*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Pump.fun](https://pump.fun/{mint}) | [DexScreener]({dex.get('pair_url', '') if dex else ''})"""
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        log.info(f"✅ Alerte de qualité envoyée pour {event.get('symbol')}")
    except Exception as e:
        log.error(f"Erreur Telegram: {e}")

# ══════════════════════════════════════════════════════════════
# CONNEXION
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
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
