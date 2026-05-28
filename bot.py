"""
╔══════════════════════════════════════════════════════════════╗
║        SOLANA PUMP.FUN SNIPER BOT — RAILWAY + HELIUS         ║
║   Entrée < 10K mcap | Sortie > 100K | Avant migration Raydium ║
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
HELIUS_API_KEY   = os.environ["HELIUS_API_KEY"]   # https://helius.dev

# ── FILTRES PRINCIPAUX ──────────────────────────────────────
MIN_MCAP_USD          = 5_000      # ne pas entrer trop tôt
MAX_MCAP_ENTRY        = 30_000     # seuil max d'entrée (< 30K)
TARGET_MCAP_EXIT      = 100_000    # objectif de sortie
MAX_TOKEN_AGE_MIN     = 10         # token < 10 minutes
MIN_LIQUIDITY_SOL     = 10         # liquidité min en SOL
MIN_VOLUME_5MIN_USD   = 2_000      # volume 5 min minimum
MIN_HOLDER_COUNT      = 20         # nb wallets uniques minimum
MAX_DEV_HOLD_PCT      = 20         # dev ne détient pas > 20%
MAX_TOP10_HOLD_PCT    = 60         # top 10 ne concentrent pas > 60%
MIN_BUY_SELL_RATIO    = 1.5        # plus d'acheteurs que vendeurs
MIN_TX_PER_MIN        = 5          # activité minimum
RUGCHECK_MIN_SCORE    = 70         # score RugCheck minimum (sur 100)
REQUIRE_SOCIAL        = True       # exiger au moins 1 lien social

# ── ENDPOINTS ──────────────────────────────────────────────
PUMPFUN_WS_PRIMARY    = "wss://pumpportal.fun/api/data"
PUMPFUN_WS_FALLBACK   = "wss://frontend-api.pump.fun/socket.io/?EIO=4&transport=websocket"
RUGCHECK_API          = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
HELIUS_RPC            = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
DEXSCREENER_API       = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
RAYDIUM_MIGRATION_CAP = 69_000    # seuil officiel migration Raydium (~69K bonding curve)

# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)

# --- INITIALISATION FASTAPI POUR RAILWAY ---
app = FastAPI()

# Cache pour éviter les doublons
alerted_tokens: dict[str, float] = {}   # mint -> timestamp
token_data_cache: dict[str, dict] = {}  # mint -> data enrichi


# ══════════════════════════════════════════════════════════════
# FETCH HELPERS
# ══════════════════════════════════════════════════════════════

async def fetch_rugcheck(session: aiohttp.ClientSession, mint: str) -> dict:
    """Score de sécurité RugCheck (0-100, 100 = parfait)."""
    try:
        url = RUGCHECK_API.format(mint=mint)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                score = data.get("score", 0)
                risks = [r["name"] for r in data.get("risks", []) if r.get("level") in ("danger","warn")]
                return {"score": score, "risks": risks, "ok": score >= RUGCHECK_MIN_SCORE}
    except Exception as e:
        log.debug(f"RugCheck error {mint[:8]}: {e}")
    return {"score": 0, "risks": ["rugcheck_unavailable"], "ok": False}


async def fetch_dexscreener(session: aiohttp.ClientSession, mint: str) -> dict | None:
    """Données de marché enrichies via DexScreener."""
    try:
        url = DEXSCREENER_API.format(mint=mint)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                data = await r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    p = pairs[0]
                    return {
                        "mcap":         float(p.get("fdv", 0) or 0),
                        "liquidity":    float(p.get("liquidity", {}).get("usd", 0) or 0),
                        "volume_5m":    float(p.get("volume", {}).get("m5", 0) or 0),
                        "volume_1h":    float(p.get("volume", {}).get("h1", 0) or 0),
                        "price_change_5m": float(p.get("priceChange", {}).get("m5", 0) or 0),
                        "buys_5m":      int(p.get("txns", {}).get("m5", {}).get("buys", 0) or 0),
                        "sells_5m":     int(p.get("txns", {}).get("m5", {}).get("sells", 0) or 0),
                        "price_usd":    float(p.get("priceUsd", 0) or 0),
                        "pair_url":     p.get("url", ""),
                        "dex":          p.get("dexId", "pumpfun"),
                    }
    except Exception as e:
        log.debug(f"DexScreener error {mint[:8]}: {e}")
    return None


async def fetch_helius_holders(session: aiohttp.ClientSession, mint: str) -> dict:
    """Analyse de la distribution des holders via Helius."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint]
        }
        async with session.post(HELIUS_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status == 200:
                data = await r.json()
                accounts = data.get("result", {}).get("value", [])
                if not accounts:
                    return {}

                total_supply = sum(float(a.get("uiAmount", 0) or 0) for a in accounts)
                if total_supply == 0:
                    return {}

                top_holder_pct = (float(accounts[0].get("uiAmount", 0) or 0) / total_supply * 100) if accounts else 0
                top10_pct = sum(float(a.get("uiAmount", 0) or 0) for a in accounts[:10]) / total_supply * 100

                return {
                    "holder_count":   len(accounts),
                    "top_holder_pct": round(top_holder_pct, 1),
                    "top10_pct":      round(top10_pct, 1),
                    "ok_concentration": top10_pct <= MAX_TOP10_HOLD_PCT,
                }
    except Exception as e:
        log.debug(f"Helius holders error {mint[:8]}: {e}")
    return {}


# ══════════════════════════════════════════════════════════════
# SCORING & FILTRES
# ══════════════════════════════════════════════════════════════

def compute_signal_score(dex: dict, holders: dict, rug: dict, age_min: float) -> tuple[int, list[str]]:
    """Calcule un score de signal 0-100 et liste des signaux positifs."""
    score = 0
    signals = []

    # ── MOMENTUM (30 pts) ──────────────────────────────────
    if dex:
        buys  = dex.get("buys_5m", 0)
        sells = dex.get("sells_5m", 0)
        ratio = buys / max(sells, 1)

        if ratio >= 3.0:   score += 20; signals.append("🔥 Ratio B/S > 3x")
        elif ratio >= 1.5: score += 12; signals.append("📈 Ratio B/S > 1.5x")

        vol5m = dex.get("volume_5m", 0)
        if vol5m > 10_000:  score += 10; signals.append(f"⚡ Volume 5m ${vol5m:,.0f}")
        elif vol5m > 3_000: score += 5;  signals.append(f"📊 Volume 5m ${vol5m:,.0f}")

    # ── MCAP & ENTRÉE OPTIMALE (25 pts) ───────────────────
    mcap = dex.get("mcap", 0) if dex else 0
    if 8_000 <= mcap <= 15_000:   score += 25; signals.append(f"🎯 MCap idéal ${mcap:,.0f}")
    elif 5_000 <= mcap <= 30_000: score += 15; signals.append(f"✅ MCap entrée ${mcap:,.0f}")

    # ── LIQUIDITÉ (15 pts) ────────────────────────────────
    liq = dex.get("liquidity", 0) if dex else 0
    liq_sol = liq / 150  # estimation SOL
    if liq_sol >= 30:   score += 15; signals.append(f"💧 Liquidité forte {liq_sol:.0f} SOL")
    elif liq_sol >= 10: score += 8;  signals.append(f"💧 Liquidité ${liq:,.0f}")

    # ── DISTRIBUTION HOLDERS (15 pts) ─────────────────────
    if holders:
        top10 = holders.get("top10_pct", 100)
        if top10 <= 40:   score += 15; signals.append(f"👥 Distrib. saine top10={top10:.0f}%")
        elif top10 <= 60: score += 8;  signals.append(f"👥 Top 10 = {top10:.0f}%")
        nb = holders.get("holder_count", 0)
        if nb >= 50: signals.append(f"👥 {nb} holders")

    # ── SÉCURITÉ RUGCHECK (15 pts) ───────────────────────
    rug_score = rug.get("score", 0)
    if rug_score >= 85:  score += 15; signals.append(f"🛡️ RugCheck {rug_score}/100")
    elif rug_score >= 70: score += 8; signals.append(f"🛡️ RugCheck {rug_score}/100")

    # ── ÂGE DU TOKEN ─────────────────────────────────────
    if age_min <= 2:   signals.append(f"🕐 Token ultra-frais {age_min:.1f}min")
    elif age_min <= 5: signals.append(f"🕐 Token frais {age_min:.1f}min")
    else:              signals.append(f"⏱️ Age: {age_min:.1f}min")

    return min(score, 100), signals


def apply_hard_filters(event: dict, dex: dict | None, holders: dict, rug: dict, age_min: float) -> tuple[bool, str]:
    """Filtres OBLIGATOIRES — si l'un échoue, pas d'alerte."""
    if age_min > MAX_TOKEN_AGE_MIN:
        return False, f"token trop vieux ({age_min:.1f}min)"

    if dex is None:
        return False, "pas de données DexScreener"

    mcap = dex.get("mcap", 0)
    if mcap < MIN_MCAP_USD:
        return False, f"mcap trop faible (${mcap:,.0f})"
    if mcap > MAX_MCAP_ENTRY:
        return False, f"mcap trop élevé pour entrée (${mcap:,.0f})"

    liq = dex.get("liquidity", 0)
    liq_sol = liq / 150
    if liq_sol < MIN_LIQUIDITY_SOL:
        return False, f"liquidité insuffisante ({liq_sol:.1f} SOL)"

    vol5m = dex.get("volume_5m", 0)
    if vol5m < MIN_VOLUME_5MIN_USD:
        return False, f"volume 5min trop faible (${vol5m:,.0f})"

    buys  = dex.get("buys_5m", 0)
    sells = dex.get("sells_5m", 0)
    ratio = buys / max(sells, 1)
    tx_per_min = (buys + sells) / 5
    if ratio < MIN_BUY_SELL_RATIO:
        return False, f"ratio B/S faible ({ratio:.1f})"
    if tx_per_min < MIN_TX_PER_MIN:
        return False, f"activité faible ({tx_per_min:.1f} tx/min)"

    if holders:
        top10 = holders.get("top10_pct", 100)
        if top10 > MAX_TOP10_HOLD_PCT:
            return False, f"concentration top10 trop élevée ({top10:.0f}%)"
        nb = holders.get("holder_count", 0)
        if nb < MIN_HOLDER_COUNT:
            return False, f"trop peu de holders ({nb})"

    if not rug.get("ok", False):
        risks = ", ".join(rug.get("risks", ["unknown"])[:3])
        return False, f"RugCheck KO (score={rug.get('score',0)}, {risks})"

    if event.get("mint") in alerted_tokens:
        return False, "déjà alerté"

    return True, ""


# ══════════════════════════════════════════════════════════════
# FORMATAGE TELEGRAM
# ══════════════════════════════════════════════════════════════

def format_alert(event: dict, dex: dict, holders: dict, rug: dict,
                 age_min: float, score: int, signals: list[str]) -> str:
    """Construit le message Telegram formaté."""
    mint     = event.get("mint", "?")
    symbol   = event.get("symbol", "?")
    name     = event.get("name", symbol)
    mcap     = dex.get("mcap", 0)
    liq      = dex.get("liquidity", 0)
    vol5m    = dex.get("volume_5m", 0)
    price    = dex.get("price_usd", 0)
    buys     = dex.get("buys_5m", 0)
    sells    = dex.get("sells_5m", 0)
    chg5m    = dex.get("price_change_5m", 0)
    pair_url = dex.get("pair_url", f"https://pump.fun/{mint}")

    nb_holders = holders.get("holder_count", "?")
    top10      = holders.get("top10_pct", "?")
    rug_score  = rug.get("score", "?")

    bars = int(score / 10)
    bar  = "█" * bars + "░" * (10 - bars)

    signals_text = "\n".join(f"  {s}" for s in signals[:6])

    msg = f"""🚨 *SIGNAL SNIPER* — `{symbol}`

📛 *{name}*
`{mint[:20]}...`

━━━━━━━━━━━━━━━━━━━━━
📊 *MÉTRIQUES*
├ 💰 MCap     : *${mcap:,.0f}*
├ 💧 Liquidité : *${liq:,.0f}*
├ 📈 Vol 5min  : *${vol5m:,.0f}*
├ 🔁 B/S 5min  : *{buys}✅ / {sells}❌*
├ ⚡ Δ5min     : *{chg5m:+.1f}%*
└ 💵 Prix      : *${price:.8f}*

━━━━━━━━━━━━━━━━━━━━━
👥 *HOLDERS*
├ Nb wallets  : *{nb_holders}*
└ Top 10 hold : *{top10}%*

🛡️ *SÉCURITÉ*
└ RugCheck    : *{rug_score}/100*

⏱️ *Age token  : {age_min:.1f} min*

━━━━━━━━━━━━━━━━━━━━━
🎯 *SCORE SIGNAL : {score}/100*
`[{bar}]`

✨ *SIGNAUX POSITIFS*
{signals_text}

━━━━━━━━━━━━━━━━━━━━━
🎯 Entrée    : *~${mcap:,.0f}* mcap
🏁 Objectif  : *$100K+ mcap*
⚠️ Migration Raydium vers *~$69K* mcap

🔗 [Pump.fun](https://pump.fun/{mint}) | [DexScreener]({pair_url}) | [Birdeye](https://birdeye.so/token/{mint}) | [RugCheck](https://rugcheck.xyz/tokens/{mint})

⚠️ _DYOR — Not financial advice_"""
    return msg


# ══════════════════════════════════════════════════════════════
# PIPELINE DE TRAITEMENT
# ══════════════════════════════════════════════════════════════

async def process_token(session: aiohttp.ClientSession, event: dict) -> None:
    """Pipeline complet : fetch → filter → score → alert."""
    mint       = event.get("mint")
    created_ts = event.get("created_timestamp", time.time() * 1000) / 1000
    age_min    = (time.time() - created_ts) / 60

    if age_min > MAX_TOKEN_AGE_MIN:
        return

    dex_data, holder_data, rug_data = await asyncio.gather(
        fetch_dexscreener(session, mint),
        fetch_helius_holders(session, mint),
        fetch_rugcheck(session, mint),
        return_exceptions=True
    )
    dex     = dex_data     if isinstance(dex_data, dict) else None
    holders = holder_data  if isinstance(holder_data, dict) else {}
    rug     = rug_data     if isinstance(rug_data, dict) else {"score": 0, "risks": [], "ok": False}

    passed, reason = apply_hard_filters(event, dex, holders, rug, age_min)
    if not passed:
        log.debug(f"SKIP {mint[:8]} ({event.get('symbol','?')}): {reason}")
        return

    score, signals = compute_signal_score(dex, holders, rug, age_min)
    if score < 55:
        log.info(f"SCORE LOW {mint[:8]} ({event.get('symbol','?')}): {score}/100")
        return

    alerted_tokens[mint] = time.time()
    msg = format_alert(event, dex, holders, rug, age_min, score, signals)
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        log.info(f"✅ ALERT SENT: {event.get('symbol','?')} ({mint[:8]}) score={score} mcap=${dex.get('mcap',0):,.0f}")
    except Exception as e:
        log.error(f"Telegram error: {e}")

    token_data_cache[mint] = {
        "symbol": event.get("symbol"),
        "mcap_entry": dex.get("mcap") if dex else 0,
        "alerted_at": time.time(),
        "score": score,
    }


# ══════════════════════════════════════════════════════════════
# WEBSOCKET PUMP.FUN
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun(session: aiohttp.ClientSession, uri: str) -> None:
    """Se connecte au WebSocket Pump.fun et traite les nouveaux tokens."""
    reconnect_delay = 3

    while True:
        try:
            log.info(f"Connecting to {uri}...")
            async with websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                extra_headers={"Origin": "https://pump.fun"}
            ) as ws:
                log.info("✅ WebSocket connected!")
                reconnect_delay = 3

                subscribe_msg = json.dumps({"method": "subscribeNewToken"})
                await ws.send(subscribe_msg)
                log.info("Subscribed to newToken events")

                async for raw_msg in ws:
                    try:
                        if isinstance(raw_msg, bytes):
                            raw_msg = raw_msg.decode()
                        if raw_msg.startswith("0") or raw_msg.startswith("2"):
                            continue
                        for prefix in ("42", "43", "40", "41"):
                            if raw_msg.startswith(prefix):
                                raw_msg = raw_msg[2:]
                                break

                        data = json.loads(raw_msg)

                        if isinstance(data, dict) and data.get("txType") == "create":
                            asyncio.create_task(process_token(session, data))
                        elif isinstance(data, list) and len(data) >= 2:
                            event_name, payload = data[0], data[1] if len(data) > 1 else {}
                            if event_name in ("create", "newToken") and isinstance(payload, dict):
                                asyncio.create_task(process_token(session, payload))

                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        log.error(f"Message processing error: {e}")

        except (websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                OSError) as e:
            log.warning(f"WebSocket disconnected: {e}. Retry in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        except Exception as e:
            log.error(f"Unexpected WS error: {e}. Retry in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)


async def cleanup_cache() -> None:
    """Nettoie le cache des tokens alertés toutes les heures."""
    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - 7200  # 2h
        expired = [m for m, t in alerted_tokens.items() if t < cutoff]
        for m in expired:
            alerted_tokens.pop(m, None)
            token_data_cache.pop(m, None)
        if expired:
            log.info(f"Cache cleanup: removed {len(expired)} expired tokens")


async def send_startup_message() -> None:
    msg = f"""🤖 *Bot Sniper Solana démarré !*

⚙️ *Configuration active :*
├ MCap entrée   : ${MIN_MCAP_USD:,} – ${MAX_MCAP_ENTRY:,}
├ Objectif      : ${TARGET_MCAP_EXIT:,}+
├ Age max token : {MAX_TOKEN_AGE_MIN} min
├ Liquidité min : {MIN_LIQUIDITY_SOL} SOL
├ Volume 5m min : ${MIN_VOLUME_5MIN_USD:,}
├ Holders min   : {MIN_HOLDER_COUNT}
├ RugCheck min  : {RUGCHECK_MIN_SCORE}/100
├ Top10 max     : {MAX_TOP10_HOLD_PCT}%
└ Ratio B/S min : {MIN_BUY_SELL_RATIO}x

🌐 Source : Pump.fun WebSocket
🔑 On-chain : Helius API
✅ _Avant migration Raydium (~$69K mcap)_"""
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
        log.info("Startup message sent")
    except Exception as e:
        log.error(f"Failed to send startup message: {e}")

# ══════════════════════════════════════════════════════════════
# GESTION DES TÂCHES DE FOND AVEC FASTAPI
# ══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """Démarre les boucles asynchrones au lancement de FastAPI sur Railway."""
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    await send_startup_message()
    connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
    timeout   = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        await asyncio.gather(
            connect_pumpfun(session, PUMPFUN_WS_PRIMARY),
            cleanup_cache(),
        )

@app.get("/")
async def root():
    """Route de santé obligatoire pour Railway."""
    return {"status": "online", "bot": "Solana Pump.fun Sniper"}
