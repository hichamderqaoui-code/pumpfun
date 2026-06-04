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
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")
TARGET_MCAP_USD   = float(os.getenv("TARGET_MCAP_USD",  "2000"))
MAX_AGE_SECONDS   = int(os.getenv("MAX_AGE_SECONDS",    "1200"))
SOL_PRICE_USD     = float(os.getenv("SOL_PRICE_USD",    "75.33"))
MIN_VOLUME_USD    = float(os.getenv("MIN_VOLUME_USD",   "50"))
MIN_TXS_5MIN      = int(os.getenv("MIN_TXS_5MIN",       "1"))
MIN_AGE_SECONDS   = int(os.getenv("MIN_AGE_SECONDS",    "30"))
MIN_HOLDERS       = int(os.getenv("MIN_HOLDERS",         "3"))
POLL_INTERVAL     = 15

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
    token   = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()

    if not token or not chat_id:
        print(f"[TELEGRAM] ❌ MANQUANT → token='{token[:15]}' chat_id='{chat_id}'")
        return

    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    if not is_test:
        payload["parse_mode"]               = "Markdown"
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


async def check_token_pumpfun(mint: str) -> dict:
    try:
        url = f"https://frontend-api.pump.fun/coins/{mint}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return {}
            try:
                d = r.json()
            except Exception:
                return {}

            if not d or "mint" not in d:
                return {}

            mcap_usd    = float(d.get("usd_market_cap") or 0)
            holders     = int(d.get("holder_count") or 0)
            reply_count = int(d.get("reply_count") or 0)
            complete    = bool(d.get("complete", False))

            migration_target_usd = 69 * SOL_PRICE_USD * 1000
            bonding_pct = min((mcap_usd / migration_target_usd) * 100, 100) if migration_target_usd > 0 else 0

            king = bool(d.get("is_currently_live") or d.get("king_of_the_hill_timestamp"))

            return {
                "mcap_usd":    mcap_usd,
                "holders":     holders,
                "reply_count": reply_count,
                "complete":    complete,
                "bonding_pct": bonding_pct,
                "king":        king,
                "name":        d.get("name", ""),
                "symbol":      d.get("symbol", ""),
                "image_uri":   d.get("image_uri", ""),
            }
    except Exception as e:
        print(f"[PUMPFUN] ❌ {mint[:8]} : {e}")
        return {}


async def check_token_trades(mint: str) -> dict:
    try:
        url = f"https://frontend-api.pump.fun/trades/all/{mint}?limit=50&offset=0&minimumSize=0"
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return {"volume_usd": 0, "txs_recent": 0, "buys": 0, "sells": 0}
            trades = r.json()
            if not isinstance(trades, list):
                return {"volume_usd": 0, "txs_recent": 0, "buys": 0, "sells": 0}

            now          = time.time() * 1000
            five_min_ago = now - (5 * 60 * 1000)

            volume_5min = 0.0
            volume_usd  = 0.0
            txs_recent  = 0
            buys        = 0
            sells       = 0

            for t in trades:
                ts         = t.get("timestamp", 0)
                sol_amount = float(t.get("sol_amount") or 0) / 1e9
                usd_val    = sol_amount * SOL_PRICE_USD
                volume_usd += usd_val
                if ts >= five_min_ago:
                    txs_recent += 1
                    volume_5min += usd_val
                    if t.get("is_buy"):
                        buys += 1
                    else:
                        sells += 1

            return {
                "volume_usd":   volume_5min,   # ✅ volume sur 5 min uniquement
                "volume_total": volume_usd,
                "txs_recent":   txs_recent,
                "buys":         buys,
                "sells":        sells,
            }
    except Exception as e:
        print(f"[TRADES] ❌ {mint[:8]} : {e}")
        return {"volume_usd": 0, "txs_recent": 0, "buys": 0, "sells": 0}


def calculer_score(p: dict, t: dict, elapsed: float) -> tuple:
    score  = 0
    points = []

    mc = p["mcap_usd"]
    if mc >= 50000:   score += 20; points.append("💰 MC fort (50K$+)")
    elif mc >= 30000: score += 14; points.append("💰 MC solide (30K$+)")
    elif mc >= 10000: score += 8

    h = p["holders"]
    if h >= 200:   score += 20; points.append("👥 Holders solides (200+)")
    elif h >= 100: score += 14; points.append("👥 Bons holders (100+)")
    elif h >= 50:  score += 10; points.append("👥 Holders corrects (50+)")
    elif h >= 30:  score += 5

    v = t["volume_usd"]
    if v >= 20000:  score += 20; points.append("📊 Volume explosif (20K$+)")
    elif v >= 8000: score += 14; points.append("📊 Volume fort (8K$+)")
    elif v >= 3000: score += 8

    tx = t["txs_recent"]
    if tx >= 100:  score += 20; points.append("⚡ Activité explosive (100+ tx/5min)")
    elif tx >= 50: score += 14; points.append("⚡ Forte activité (50+ tx/5min)")
    elif tx >= 20: score += 8

    total_tx = t["buys"] + t["sells"]
    if total_tx > 0:
        buy_ratio = t["buys"] / total_tx
        if buy_ratio >= 0.75:   score += 10; points.append("🟢 Pression achat forte (75%+ buys)")
        elif buy_ratio >= 0.60: score += 6;  points.append("🟢 Majorité acheteurs (60%+)")
        elif buy_ratio >= 0.50: score += 3

    bp = p["bonding_pct"]
    if 10 <= bp <= 50:   score += 10; points.append(f"📉 Bonding curve idéale ({bp:.0f}%)")
    elif 50 < bp <= 80:  score += 6;  points.append(f"📉 Bonding curve avancée ({bp:.0f}%)")
    elif bp > 80:        score += 2;  points.append(f"⚠️ Proche migration ({bp:.0f}%)")

    if p["reply_count"] >= 20:  score += 5; points.append("💬 Communauté active")
    elif p["reply_count"] >= 5: score += 2

    mins = elapsed / 60
    if 2 <= mins <= 5:  score += 5; points.append(f"⏱️ Timing parfait ({mins:.0f} min)")
    elif mins <= 8:     score += 2

    return min(score, 100), points


def emoji_score(score: int) -> str:
    if score >= 75: return "🟢"
    if score >= 50: return "🟡"
    return "🔴"


async def monitor_tokens():
    print("[MONITOR] 🔍 Surveillance Pump.fun démarrée...")
    while True:
        now     = time.time()
        mints   = list(tracked_new_tokens.keys())
        checked = 0

        for mint in mints:
            token_info = tracked_new_tokens.get(mint)
            if not token_info or token_info["alerted"]:
                continue

            elapsed = now - token_info["created_at"]

            if elapsed > MAX_AGE_SECONDS:
                token_info["alerted"] = True
                continue

            if elapsed < MIN_AGE_SECONDS:
                continue

            checked += 1
            await asyncio.sleep(0.2)  # ✅ laisse la main à asyncio

            p = await check_token_pumpfun(mint)
            if not p:
                continue

            if p["complete"]:
                token_info["alerted"] = True
                print(f"[SKIP] {token_info['name']} déjà migré sur Raydium")
                continue

            t = await check_token_trades(mint)

            mcap_usd = p["mcap_usd"]
            holders  = p["holders"]
            volume   = t["volume_usd"]
            txs      = t["txs_recent"]
            buys     = t["buys"]
            sells    = t["sells"]
            bonding  = p["bonding_pct"]
            replies  = p["reply_count"]

            print(
                f"[POLL] {token_info['name'][:16]:<16} | "
                f"MC:{mcap_usd:>8,.0f}$ | "
                f"H:{holders:>4} | "
                f"Vol:{volume:>7,.0f}$ | "
                f"Tx5m:{txs:>4} | "
                f"B/S:{buys}/{sells} | "
                f"BC:{bonding:.0f}% | "
                f"{elapsed:.0f}s"
            )

            if mcap_usd < TARGET_MCAP_USD:
                print(f"[FILTRE] ❌ {token_info['name']} → MC {mcap_usd:,.0f}$<{TARGET_MCAP_USD:,.0f}$")
                continue

            reasons = []
            if holders < MIN_HOLDERS:    reasons.append(f"holders {holders}<{MIN_HOLDERS}")
            if volume  < MIN_VOLUME_USD: reasons.append(f"vol {volume:,.0f}$<{MIN_VOLUME_USD:,.0f}$")
            if txs     < MIN_TXS_5MIN:  reasons.append(f"tx5m {txs}<{MIN_TXS_5MIN}")
            total_tx = buys + sells
            if total_tx > 0 and buys / total_tx < 0.40:
                reasons.append(f"trop de sells ({sells}/{total_tx})")

            if reasons:
                print(f"[FILTRE] ❌ {token_info['name']} → {' | '.join(reasons)}")
                continue

            score, points = calculer_score(p, t, elapsed)
            token_info["alerted"] = True

            name     = token_info["name"]
            symbol   = token_info["symbol"]
            mins_e   = int(elapsed // 60)
            secs_e   = int(elapsed % 60)
            time_str = f"{mins_e}m {secs_e}s" if mins_e > 0 else f"{secs_e}s"
            mult     = 100000 / mcap_usd if mcap_usd > 0 else 10
            em       = emoji_score(score)
            pts_str  = "\n".join(f"  {pt}" for pt in points) if points else "  —"

            print(f"🎯 [ALERTE] {name} | Score:{score}/100 | MC:{mcap_usd:,.0f}$ | H:{holders} | BC:{bonding:.0f}% | {time_str}")

            message = (
                f"{em} *SIGNAL {score}/100 — PRÉ-MIGRATION*\n\n"
                f"• *Nom :* {name} ({symbol})\n"
                f"• *Market Cap :* `{mcap_usd:,.0f}$`\n"
                f"• *Holders :* `{holders}`\n"
                f"• *Volume récent :* `{volume:,.0f}$`\n"
                f"• *Tx 5min :* `{txs}` ({buys} buy / {sells} sell)\n"
                f"• *Bonding curve :* `{bonding:.0f}%` vers migration\n"
                f"• *Communauté :* `{replies}` replies\n"
                f"• *Temps création :* `{time_str}` ⏱️\n"
                f"• *Potentiel x100k :* `x{mult:.1f}`\n\n"
                f"✨ *Points forts :*\n{pts_str}\n\n"
                "🔍 *Analyse :*\n"
                f"• [Pump.fun](https://pump.fun/{mint})\n"
                f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n"
                f"• [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
                f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n\n"
                "📥 *CA :*\n"
                f"`{mint}`"
            )
            await send_telegram_alert(message)

        if checked > 0:
            print(f"[MONITOR] Cycle terminé — {checked} tokens vérifiés")

        await asyncio.sleep(POLL_INTERVAL)


async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage Sniper Pré-Migration ===")
    await send_telegram_alert(
        "🚀 *Sniper Pré-Migration Actif*\n\n"
        f"💰 Cible MC : `{TARGET_MCAP_USD:,.0f}$`\n"
        f"👥 Min holders : `{MIN_HOLDERS}`\n"
        f"📊 Min volume : `{MIN_VOLUME_USD:,.0f}$`\n"
        f"⚡ Min tx/5min : `{MIN_TXS_5MIN}`\n"
        f"⏱️ Fenêtre : `{MIN_AGE_SECONDS//60}-{MAX_AGE_SECONDS//60} min`\n"
        f"🔗 Source : Pump.fun natif\n\n"
        "🟢 Score ≥75 | 🟡 ≥50 | 🔴 <50",
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
                    await asyncio.sleep(0)  # ✅ FIX CRITIQUE : cède la main au monitor

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
        "status":          "active",
        "source":          "pump.fun natif",
        "target_mcap_usd": TARGET_MCAP_USD,
        "min_holders":     MIN_HOLDERS,
        "min_volume_usd":  MIN_VOLUME_USD,
        "min_txs_5min":    MIN_TXS_5MIN,
        "min_age_seconds": MIN_AGE_SECONDS,
        "tracked_tokens":  len(tracked_new_tokens),
        "active_tokens":   active
    }


@app.get("/test-telegram")
async def test_telegram():
    token   = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": "✅ TEST BOT OK"
            }, timeout=10.0)
            return {"status": r.status_code, "response": r.json(), "token_preview": token[:15]+"...", "chat_id": chat_id}
    except Exception as e:
        return {"error": str(e)}
