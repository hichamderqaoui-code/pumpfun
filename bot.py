import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets
from collections import OrderedDict
import time

# ─── CONFIG ───
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
TARGET_MCAP_USD     = float(os.getenv("TARGET_MCAP_USD",     "10000"))
MAX_AGE_SECONDS     = int(os.getenv("MAX_AGE_SECONDS",       "600"))
SOL_PRICE_USD       = float(os.getenv("SOL_PRICE_USD",       "75.33"))
MIN_HOLDERS         = int(os.getenv("MIN_HOLDERS",           "50"))
MIN_VOLUME_USD      = float(os.getenv("MIN_VOLUME_USD",      "5000"))
MIN_LIQUIDITY_USD   = float(os.getenv("MIN_LIQUIDITY_USD",   "5000"))
MIN_AGE_SECONDS     = int(os.getenv("MIN_AGE_SECONDS",       "120"))   # 2 min minimum
MAX_TOP10_PCT       = float(os.getenv("MAX_TOP10_PCT",        "30.0")) # top10 holders < 30%
MIN_TXS_5MIN        = int(os.getenv("MIN_TXS_5MIN",          "20"))    # >20 tx sur 5 min
POLL_INTERVAL       = 10

tracked_new_tokens = OrderedDict()
MAX_TRACKED = 500


def register_new_token(data: dict):
    mint = data.get("mint")
    if not mint or mint in tracked_new_tokens:
        return None
    if len(tracked_new_tokens) >= MAX_TRACKED:
        tracked_new_tokens.popitem(last=False)
    tracked_new_tokens[mint] = {
        "name":       data.get("name", "Unknown"),
        "symbol":     data.get("symbol", "MEME"),
        "created_at": time.time(),
        "alerted":    False
    }
    print(f"🆕 [NOUVEAU TOKEN] {data.get('name')} | {mint[:8]}...")
    return mint


async def send_telegram_alert(message: str, is_test: bool = False):
    token    = str(TELEGRAM_TOKEN).strip()
    chat_id  = str(TELEGRAM_CHAT_ID).strip()
    url      = f"https://api.telegram.org/bot{token}/sendMessage"
    payload  = {"chat_id": chat_id, "text": message}
    if not is_test:
        payload["parse_mode"]             = "Markdown"
        payload["disable_web_page_preview"] = True
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, timeout=10.0)
            if r.status_code != 200:
                print(f"[TELEGRAM] ❌ HTTP {r.status_code} : {r.text}")
            else:
                print(f"[TELEGRAM] ✅ Alerte envoyée.")
    except Exception as e:
        print(f"[TELEGRAM] ❌ Erreur : {e}")


async def check_token_dexscreener(mint: str) -> dict:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            data = r.json()
            pairs = data.get("pairs", [])
            if not pairs:
                return {}
            p = pairs[0]

            # Transactions sur 5 min
            txs_5m = 0
            txns = p.get("txns", {})
            m5   = txns.get("m5", {})
            txs_5m = int(m5.get("buys", 0)) + int(m5.get("sells", 0))

            # Variation de prix sur 5 min
            price_change_5m = float((p.get("priceChange") or {}).get("m5") or 0)

            # Top 10 holders % (si dispo dans info)
            info = p.get("info") or {}
            top10_pct = float(info.get("top10HolderPercent") or 0) * 100

            # Mint authority (si dispo)
            mint_disabled = info.get("mintAuthorityDisabled", None)

            return {
                "mcap_usd":        float(p.get("fdv") or p.get("marketCap") or 0),
                "volume_usd":      float((p.get("volume") or {}).get("h24") or 0),
                "liquidity":       float((p.get("liquidity") or {}).get("usd") or 0),
                "holders":         int(info.get("holders") or 0),
                "txs_5m":          txs_5m,
                "price_change_5m": price_change_5m,
                "top10_pct":       top10_pct,
                "mint_disabled":   mint_disabled,
            }
    except Exception as e:
        print(f"[DEXSCREENER] ❌ Erreur {mint[:8]} : {e}")
        return {}


def calculer_score(d: dict, elapsed: float) -> tuple[int, list[str]]:
    """Retourne un score /100 et la liste des points forts"""
    score  = 0
    points = []

    # Holders (max 20 pts)
    h = d["holders"]
    if h >= 200:
        score += 20; points.append("👥 Holders solides (200+)")
    elif h >= 100:
        score += 14; points.append("👥 Bons holders (100+)")
    elif h >= 50:
        score += 8

    # Volume (max 20 pts)
    v = d["volume_usd"]
    if v >= 50000:
        score += 20; points.append("📊 Volume fort (50K$+)")
    elif v >= 20000:
        score += 14; points.append("📊 Volume correct (20K$+)")
    elif v >= 5000:
        score += 8

    # Liquidité (max 20 pts)
    liq = d["liquidity"]
    if liq >= 20000:
        score += 20; points.append("💧 Liquidité forte (20K$+)")
    elif liq >= 10000:
        score += 14; points.append("💧 Bonne liquidité (10K$+)")
    elif liq >= 5000:
        score += 8

    # Transactions 5 min (max 15 pts)
    tx = d["txs_5m"]
    if tx >= 100:
        score += 15; points.append("⚡ Activité intense (100+ tx/5min)")
    elif tx >= 50:
        score += 10; points.append("⚡ Bonne activité (50+ tx/5min)")
    elif tx >= 20:
        score += 6

    # Prix en hausse sur 5 min (max 15 pts)
    pc = d["price_change_5m"]
    if pc >= 20:
        score += 15; points.append("📈 Momentum fort (+20%/5min)")
    elif pc >= 5:
        score += 10; points.append("📈 Momentum positif (+5%/5min)")
    elif pc > 0:
        score += 5

    # Top 10 holders (max 10 pts)
    t10 = d["top10_pct"]
    if 0 < t10 <= 15:
        score += 10; points.append("🔒 Distribution saine (<15%)")
    elif t10 <= 25:
        score += 6
    elif t10 <= 30:
        score += 2

    # Âge (bonus jusqu'à 10 pts) — ni trop rapide ni trop lent
    mins = elapsed / 60
    if 3 <= mins <= 7:
        score += 10; points.append(f"⏱️ Timing idéal ({mins:.0f} min)")
    elif 2 <= mins <= 10:
        score += 5

    return min(score, 100), points


def emoji_score(score: int) -> str:
    if score >= 80: return "🟢"
    if score >= 60: return "🟡"
    return "🔴"


async def monitor_tokens():
    print("[MONITOR] 🔍 Surveillance DexScreener démarrée...")
    while True:
        now   = time.time()
        mints = list(tracked_new_tokens.keys())

        for mint in mints:
            token_info = tracked_new_tokens.get(mint)
            if not token_info or token_info["alerted"]:
                continue

            elapsed = now - token_info["created_at"]

            if elapsed > MAX_AGE_SECONDS:
                token_info["alerted"] = True
                continue

            # Pas encore assez vieux
            if elapsed < MIN_AGE_SECONDS:
                continue

            d = await check_token_dexscreener(mint)
            if not d:
                continue

            mcap_usd  = d["mcap_usd"]
            volume    = d["volume_usd"]
            liquidity = d["liquidity"]
            holders   = d["holders"]
            txs_5m    = d["txs_5m"]
            pc_5m     = d["price_change_5m"]
            top10     = d["top10_pct"]

            print(
                f"[POLL] {token_info['name'][:18]:<18} | "
                f"MC:{mcap_usd:>8,.0f}$ | "
                f"Vol:{volume:>7,.0f}$ | "
                f"Liq:{liquidity:>7,.0f}$ | "
                f"H:{holders:>4} | "
                f"Tx5m:{txs_5m:>4} | "
                f"Δ5m:{pc_5m:>+6.1f}% | "
                f"{elapsed:.0f}s"
            )

            # ── MC minimum ──
            if mcap_usd < TARGET_MCAP_USD:
                continue

            # ── Filtres obligatoires ──
            reasons = []
            if holders < MIN_HOLDERS:
                reasons.append(f"holders {holders}<{MIN_HOLDERS}")
            if volume < MIN_VOLUME_USD:
                reasons.append(f"vol {volume:,.0f}$<{MIN_VOLUME_USD:,.0f}$")
            if liquidity < MIN_LIQUIDITY_USD:
                reasons.append(f"liq {liquidity:,.0f}$<{MIN_LIQUIDITY_USD:,.0f}$")
            if txs_5m < MIN_TXS_5MIN:
                reasons.append(f"tx5m {txs_5m}<{MIN_TXS_5MIN}")
            if top10 > MAX_TOP10_PCT and top10 > 0:
                reasons.append(f"top10 {top10:.0f}%>{MAX_TOP10_PCT:.0f}%")
            if pc_5m <= 0:
                reasons.append(f"prix baisse ({pc_5m:+.1f}%/5min)")

            if reasons:
                print(f"[FILTRE] ❌ {token_info['name']} → {' | '.join(reasons)}")
                continue

            # ── Score ──
            score, points = calculer_score(d, elapsed)
            token_info["alerted"] = True

            name     = token_info["name"]
            symbol   = token_info["symbol"]
            mins_e   = int(elapsed // 60)
            secs_e   = int(elapsed % 60)
            time_str = f"{mins_e}m {secs_e}s" if mins_e > 0 else f"{secs_e}s"
            mult     = 100000 / mcap_usd if mcap_usd > 0 else 10
            em       = emoji_score(score)

            print(f"🎯 [ALERTE] {name} | Score: {score}/100 | {mcap_usd:,.0f}$ | {time_str}")

            # ── Points forts formatés ──
            points_str = "\n".join(f"  {p}" for p in points) if points else "  (aucun point fort)"

            message = (
                f"{em} *SIGNAL {score}/100 — ANALYSE AVANT ENTRÉE*\n\n"
                f"• *Nom :* {name} ({symbol})\n"
                f"• *Market Cap :* `{mcap_usd:,.0f}$`\n"
                f"• *Volume 24h :* `{volume:,.0f}$`\n"
                f"• *Liquidité :* `{liquidity:,.0f}$`\n"
                f"• *Holders :* `{holders}`\n"
                f"• *Tx (5 min) :* `{txs_5m}`\n"
                f"• *Prix Δ5min :* `{pc_5m:+.1f}%`\n"
                f"• *Top10 holders :* `{top10:.0f}%`\n"
                f"• *Temps création :* `{time_str}` ⏱️\n"
                f"• *Potentiel x100k :* `x{mult:.1f}`\n\n"
                f"✨ *Points forts :*\n{points_str}\n\n"
                "🔍 *Analyse :*\n"
                f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n"
                f"• [RugCheck](https://rugcheck.xyz/tokens/{mint})\n"
                f"• [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
                f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n\n"
                "📥 *CA :*\n"
                f"`{mint}`"
            )
            await send_telegram_alert(message)

        await asyncio.sleep(POLL_INTERVAL)


async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage Sniper 10K→100K ===")
    await send_telegram_alert(
        "🚀 *Sniper 10K→100K Actif*\n\n"
        f"💰 Cible MC : `{TARGET_MCAP_USD:,.0f}$`\n"
        f"👥 Min holders : `{MIN_HOLDERS}`\n"
        f"📊 Min volume : `{MIN_VOLUME_USD:,.0f}$`\n"
        f"💧 Min liquidité : `{MIN_LIQUIDITY_USD:,.0f}$`\n"
        f"⚡ Min tx/5min : `{MIN_TXS_5MIN}`\n"
        f"🔒 Max top10 : `{MAX_TOP10_PCT:.0f}%`\n"
        f"⏱️ Âge min : `{MIN_AGE_SECONDS//60} min` | max : `{MAX_AGE_SECONDS//60} min`\n\n"
        "🟢 Score ≥80 | 🟡 ≥60 | 🔴 <60",
        is_test=True
    )

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("[WEBSOCKET] ✅ Connecté.")
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                print("[WEBSOCKET] 📡 Abonné aux nouveaux tokens.")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    if data.get("txType") == "create":
                        register_new_token(data)

        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Reconnexion dans 3s...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Erreur : {e}")
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_task      = asyncio.create_task(solana_websocket_listener())
    monitor_task = asyncio.create_task(monitor_tokens())
    yield
    ws_task.cancel()
    monitor_task.cancel()
    for task in [ws_task, monitor_task]:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/")
def home():
    active = sum(1 for t in tracked_new_tokens.values() if not t["alerted"])
    return {
        "status":            "active",
        "target_mcap_usd":   TARGET_MCAP_USD,
        "min_holders":       MIN_HOLDERS,
        "min_volume_usd":    MIN_VOLUME_USD,
        "min_liquidity_usd": MIN_LIQUIDITY_USD,
        "min_txs_5min":      MIN_TXS_5MIN,
        "max_top10_pct":     MAX_TOP10_PCT,
        "min_age_seconds":   MIN_AGE_SECONDS,
        "tracked_tokens":    len(tracked_new_tokens),
        "active_tokens":     active
    }
