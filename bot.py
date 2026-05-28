"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — VERSION FULL FLOW        ║
║   Filtre Top 10 Réel < 29% | Sans Limite de Market Cap       ║
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

# ── FILTRAGE CHIRURGICAL SANS BARRIÈRE DE MCAP ──────────────
MAX_TOP10_HOLD_PCT    = 29         # Limite stricte sur les vrais acheteurs (Axiom Pro)
WAIT_BEFORE_CHECK_SEC = 45         # Laisse 45s pour que les holders se distribuent

PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
HELIUS_RPC            = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
DEXSCREENER_API       = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

# Adresse officielle de la Bonding Curve globale de Pump.fun
PUMP_BONDING_CURVE_AUTHORITY = "5Q544fNpGWDbwS7oSyLEmu67JscBePySTWvKc4o9u8nd"

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
                    # On filtre pour ENLEVER la bonding curve du calcul de la supply humaine
                    trader_accounts = [a for a in accounts if a.get("address") != PUMP_BONDING_CURVE_AUTHORITY]
                    
                    total_trader_supply = sum(float(a.get("uiAmount", 0) or 0) for a in trader_accounts)
                    
                    if total_trader_supply > 0:
                        # Top 10 basé uniquement sur les vrais portefeuilles (traders)
                        top10_pct = sum(float(a.get("uiAmount", 0) or 0) for a in trader_accounts[:10]) / total_trader_supply * 100
                        return {"holder_count": len(trader_accounts), "top10_pct": round(top10_pct, 1)}
    except: pass
    return {"holder_count": 0, "top10_pct": 100}

# ══════════════════════════════════════════════════════════════
# LOGIQUE DE SÉLECTION
# ══════════════════════════════════════════════════════════════

async def process_token(session: aiohttp.ClientSession, event: dict) -> None:
    mint = event.get("mint")
    if not mint or mint in alerted_tokens: return
    alerted_tokens[mint] = time.time()

    # On attend 45 secondes pour laisser la distribution se faire
    await asyncio.sleep(WAIT_BEFORE_CHECK_SEC)

    dex, holders = await asyncio.gather(
        fetch_dexscreener(session, mint),
        fetch_helius_holders(session, mint),
        return_exceptions=True
    )

    dex = dex if isinstance(dex, dict) else None
    holders = holders if isinstance(holders, dict) else {"top10_pct": 100, "holder_count": 0}

    mcap = dex.get("mcap", 0) if dex else 0
    top10_pct = holders.get("top10_pct", 100)

    # FILTRE UNIQUE : Uniquement la concentration du Top 10 des traders
    if top10_pct > MAX_TOP10_HOLD_PCT:
        log.info(f"❌ Rejeté {event.get('symbol')} : Top 10 réel trop concentré ({top10_pct}%)")
        return

    # TOUT EST BON : ENVOI DU SIGNAL PEU IMPORTE LE MCAP
    mcap_display = f"${mcap:,.0f}" if mcap > 0 else "Non listé / Très bas"
    liq_display = f"${dex.get('liquidity', 0):,.0f}" if dex and dex.get('liquidity', 0) > 0 else "0"
    dex_url = dex.get("pair_url", "") if dex else ""

    msg = f"""🎯 *ALERTE PÉPITE VALIDÉE (<29% Top 10)*
• *Jeton :* {event.get('name', '?')} ({event.get('symbol', '?')})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *MARKET DATA (à {WAIT_BEFORE_CHECK_SEC}s)*
├ 💰 Market Cap : *{mcap_display}*
├ 💧 Liquidité : *{liq_display}*

👥 *DISTRIBUTION TRADERS (Hors Curve)*
├ 🎯 Top 10 Holders : *{top10_pct}%* (Filtre: <29%)
└ 👥 Wallets Actifs : *{holders.get('holder_count', '?')}*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Pump.fun](https://pump.fun/{mint}){f' | [DexScreener]({dex_url})' if dex_url else ''}"""
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        log.info(f"🚀 SIGNAL ENVOYÉ pour {event.get('symbol')} (Mcap: {mcap_display})")
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
