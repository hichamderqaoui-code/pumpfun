"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — RAILWAY + HELIUS         ║
║   Entrée libre (jusqu'à 35K) | Sortie > 100K | Flux Total    ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import aiohttp
import json
import os
import time
import logging
from datetime import datetime, timezone
from collections import defaultdict

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

# ── FILTRES VISUELS ET SÉCURITÉ ──────────────────────────────
MIN_MCAP_USD          = 5_000      
MAX_MCAP_ENTRY        = 35_000     # Augmenté pour voir tous les tokens de la curve
TARGET_MCAP_EXIT      = 100_000    # Objectif final
MAX_TOKEN_AGE_MIN     = 10         
MIN_LIQUIDITY_SOL     = 5          
MIN_VOLUME_5MIN_USD   = 1_500      
MIN_HOLDER_COUNT      = 15         
MAX_DEV_HOLD_PCT      = 20         
MAX_TOP10_HOLD_PCT    = 60         
MIN_BUY_SELL_RATIO    = 1.5        
MIN_TX_PER_MIN        = 5          
RUGCHECK_MIN_SCORE    = 70         
REQUIRE_SOCIAL        = True       

# ── ENDPOINTS ──────────────────────────────────────────────
PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
RUGCHECK_API          = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
HELIUS_RPC            = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
DEXSCREENER_API       = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
app = FastAPI()

alerted_tokens: dict[str, float] = {}
token_data_cache: dict[str, dict] = {}

# ══════════════════════════════════════════════════════════════
# FETCH HELPERS
# ══════════════════════════════════════════════════════════════

async def fetch_rugcheck(session: aiohttp.ClientSession, mint: str) -> dict:
    try:
        url = RUGCHECK_API.format(mint=mint)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                return {"score": data.get("score", 0), "risks": [r["name"] for r in data.get("risks", [])], "ok": data.get("score", 0) >= RUGCHECK_MIN_SCORE}
    except Exception as e:
        log.debug(f"RugCheck error {mint[:8]}: {e}")
    return {"score": 0, "risks": ["unavailable"], "ok": False}

async def fetch_dexscreener(session: aiohttp.ClientSession, mint: str) -> dict | None:
    try:
        url = DEXSCREENER_API.format(mint=mint)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    p = pairs[0]
                    return {
                        "mcap": float(p.get("fdv", 0) or 0),
                        "liquidity": float(p.get("liquidity", {}).get("usd", 0) or 0),
                        "volume_5m": float(p.get("volume", {}).get("m5", 0) or 0),
                        "price_change_5m": float(p.get("priceChange", {}).get("m5", 0) or 0),
                        "buys_5m": int(p.get("txns", {}).get("m5", {}).get("buys", 0) or 0),
                        "sells_5m": int(p.get("txns", {}).get("m5", {}).get("sells", 0) or 0),
                        "price_usd": float(p.get("priceUsd", 0) or 0),
                        "pair_url": p.get("url", "")
                    }
    except Exception as e:
        log.debug(f"DexScreener error {mint[:8]}: {e}")
    return None

async def fetch_helius_holders(session: aiohttp.ClientSession, mint: str) -> dict:
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}
        async with session.post(HELIUS_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status == 200:
                data = await r.json()
                accounts = data.get("result", {}).get("value", [])
                if accounts:
                    total_supply = sum(float(a.get("uiAmount", 0) or 0) for a in accounts)
                    if total_supply > 0:
                        top10_pct = sum(float(a.get("uiAmount", 0) or 0) for a in accounts[:10]) / total_supply * 100
                        return {"holder_count": len(accounts), "top10_pct": round(top10_pct, 1)}
    except Exception as e:
        log.debug(f"Helius holders error {mint[:8]}: {e}")
    return {}

# ══════════════════════════════════════════════════════════════
# FILTRES ET ENVOI
# ══════════════════════════════════════════════════════════════

def apply_hard_filters(event: dict, dex: dict | None, holders: dict, rug: dict, age_min: float) -> tuple[bool, str]:
    if age_min > MAX_TOKEN_AGE_MIN: return False, "too old"
    if dex is None: return False, "no dex data"
    
    mcap = dex.get("mcap", 0)
    if mcap < MIN_MCAP_USD: return False, f"mcap low (${mcap:,.0f})"
    if mcap > MAX_MCAP_ENTRY: return False, f"mcap above limit (${mcap:,.0f})"

    if (dex.get("liquidity", 0) / 150) < MIN_LIQUIDITY_SOL: return False, "low liquidity"
    if dex.get("volume_5m", 0) < MIN_VOLUME_5MIN_USD: return False, "low volume"
    if not rug.get("ok", False): return False, "RugCheck KO"
    if event.get("mint") in alerted_tokens: return False, "already alerted"
    
    return True, ""

def format_alert(event: dict, dex: dict, holders: dict, rug: dict, age_min: float) -> str:
    symbol = event.get("symbol", "?")
    mint = event.get("mint", "?")
    mcap = dex.get("mcap", 0)
    
    msg = f"""🚀 *ALERTE TOKEN DETECTÉ* — `{symbol}`
`{mint[:24]}...`

━━━━━━━━━━━━━━━━━━━━━
📊 *MARKET DATA*
├ 💰 Market Cap : *${mcap:,.0f}*
├ 💧 Liquidité : *${dex.get('liquidity', 0):,.0f}*
├ 📈 Vol 5min  : *${dex.get('volume_5m', 0):,.0f}*
└ 🔁 B/S 5min  : *{dex.get('buys_5m', 0)}✅ / {dex.get('sells_5m', 0)}❌*

👥 *HOLDERS & SECURITÉ*
├ Nb Wallets  : *{holders.get('holder_count', '?')}*
├ Top 10 Hold : *{holders.get('top10_pct', '?')}%*
└ RugCheck    : *{rug.get('score', '?')}/100*

⏱️ Âge : {age_min:.1f} min
━━━━━━━━━━━━━━━━━━━━━
🏁 Objectif Moonshot : *${TARGET_MCAP_EXIT:,.0f}+*

🔗 [Pump.fun](https://pump.fun/{mint}) | [DexScreener]({dex.get('pair_url', '')})"""
    return msg

async def process_token(session: aiohttp.ClientSession, event: dict) -> None:
    mint = event.get("mint")
    created_ts = event.get("created_timestamp", time.time() * 1000) / 1000
    age_min = (time.time() - created_ts) / 60

    if age_min > MAX_TOKEN_AGE_MIN: return

    dex, holders, rug = await asyncio.gather(
        fetch_dexscreener(session, mint),
        fetch_helius_holders(session, mint),
        fetch_rugcheck(session, mint),
        return_exceptions=True
    )
    
    if not isinstance(dex, dict): return
    holders = holders if isinstance(holders, dict) else {}
    rug = rug if isinstance(rug, dict) else {"score": 0, "ok": False}

    passed, reason = apply_hard_filters(event, dex, holders, rug, age_min)
    if not passed: return

    alerted_tokens[mint] = time.time()
    msg = format_alert(event, dex, holders, rug, age_min)
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception as e:
        log.error(f"Telegram error: {e}")

# ══════════════════════════════════════════════════════════════
# APP RUNNERS & WEBSOCKET
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20, ping_timeout=10) as ws:
                log.info("✅ Connected to Pump.fun Stream!")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async_msg = None
                async for raw_msg in ws:
                    if isinstance(raw_msg, bytes): raw_msg = raw_msg.decode()
                    if raw_msg.startswith("0") or raw_msg.startswith("2"): continue
                    for p in ("42", "43", "40", "41"):
                        if raw_msg.startswith(p):
                            raw_msg = raw_msg[len(p):]
                            break
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            asyncio.create_task(process_token(session, data))
                    except: pass
        except Exception as e:
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="🔄 *Mise à jour du bot effectuée : Plafond MCap relevé à $35K !*", parse_mode=ParseMode.MARKDOWN)
    except: pass
    connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        await connect_pumpfun(session)

@app.get("/")
async def root(): return {"status": "online"}
