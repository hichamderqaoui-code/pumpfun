"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — COMPATIBLE BULLX         ║
║   Calcul du Top 10 des traders sur la Supply Totale (<29%)   ║
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

# ── FILTRAGE COPIÉ SUR TON ÉCRAN ────────────────────────────
MAX_TOP10_HOLD_PCT    = 29         # Limite stricte Axiom Pro (cercles verts)
WAIT_BEFORE_CHECK_SEC = 45         # Laisse le temps aux traders d'acheter

PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
HELIUS_RPC            = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
DEXSCREENER_API       = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

# Adresses liées aux protocoles de bonding curve à ignorer dans le Top 10 des traders
PUMP_CURVES = [
    "5Q544fNpGWDbwS7oSyLEmu67JscBePySTWvKc4o9u8nd", # Global Authority
]

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
                    # 1. On isole uniquement les comptes des vrais traders (on vire la curve)
                    trader_accounts = [a for a in accounts if a.get("address") not in PUMP_CURVES]
                    
                    # 2. La supply totale d'un token sur Pump.fun au lancement est TOUJOURS de 1 Milliard
                    TOTAL_PUMP_SUPPLY = 1_000_000_000.0
                    
                    # 3. Somme des jetons possédés par le Top 10 des traders réels
                    top10_tokens_sum = sum(float(a.get("uiAmount", 0) or 0) for a in trader_accounts[:10])
                    
                    # 4. Calcul du % réel par rapport à l'ensemble du token (comme sur ton écran)
                    top10_pct = (top10_tokens_sum / TOTAL_PUMP_SUPPLY) * 100
                    
                    return {"holder_count": len(trader_accounts), "top10_pct": round(top10_pct, 1)}
    except: pass
    return {"holder_count": 0, "top10_pct": 100}

# ══════════════════════════════════════════════════════════════
# LOGIQUE DE FILTRAGE
# ══════════════════════════════════════════════════════════════

async def process_token(session: aiohttp.ClientSession, event: dict) -> None:
    mint = event.get("mint")
    if not mint or mint in alerted_tokens: return
    alerted_tokens[mint] = time.time()

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

    # Comparaison avec ton seuil Axiom Pro
    if top10_pct > MAX_TOP10_HOLD_PCT:
        log.info(f"❌ Rejeté {event.get('symbol')} : Top 10 Traders à {top10_pct}% (Max {MAX_TOP10_HOLD_PCT}%)")
        return

    # ALERTE QUALIFIÉE VALIDÉE
    mcap_display = f"${mcap:,.0f}" if mcap > 0 else "Nouveau / Bas"
    dex_url = dex.get("pair_url", "") if dex else ""

    msg = f"""🎯 *ALERTE TOP 10 VALIDE (<29%)*
• *Jeton :* {event.get('name', '?')} ({event.get('symbol', '?')})
• *Mint :* `{mint}`

━━━━━━━━━━━━━━━━━━━━━
📊 *MARKET DATA (à {WAIT_BEFORE_CHECK_SEC}s)*
├ 💰 Market Cap : *{mcap_display}*
├ 💧 Liquidité : *${dex.get('liquidity', 0):,.0f}* if dex else "0"

👥 *DISTRIBUTION TRADERS (Style BullX)*
├ 🎯 Top 10 Holders : *{top10_pct}%* └ 👥 Wallets Actifs : *{holders.get('holder_count', '?')}*

━━━━━━━━━━━━━━━━━━━━━
🔗 [Pump.fun](https://pump.fun/{mint}){f' | [DexScreener]({dex_url})' if dex_url else ''}"""
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        log.info(f"🚀 PÉPITE ENVOYÉE : {event.get('symbol')} avec {top10_pct}% Hold")
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
