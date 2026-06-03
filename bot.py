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
TARGET_MCAP_USD     = float(os.getenv("TARGET_MCAP_USD",   "10000"))
MAX_AGE_SECONDS     = int(os.getenv("MAX_AGE_SECONDS",     "600"))
SOL_PRICE_USD       = float(os.getenv("SOL_PRICE_USD",     "75.33"))
MIN_VOLUME_USD      = float(os.getenv("MIN_VOLUME_USD",    "3000"))
MIN_TXS_5MIN        = int(os.getenv("MIN_TXS_5MIN",        "20"))
MIN_AGE_SECONDS     = int(os.getenv("MIN_AGE_SECONDS",     "120"))
POLL_INTERVAL       = 15

# ─── Holders/liquidité désactivés (données non fiables pré-migration Raydium) ───
# MIN_HOLDERS et MIN_LIQUIDITY_USD volontairement retirés

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


async def check_token_dexscreener(mint: str) -> dict:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return {}
            ct = r.headers.get("content-type", "")
            if "application/json" not in ct:
                return {}
            try:
                data = r.json()
            except Exception:
                return {}

            pairs = data.get("pairs")
            if not pairs:
                return {}

            p = pairs[0]

            txns   = p.get("txns") or {}
            m5     = txns.get("m5") or {}
            txs_5m = int(m5.get("buys", 0)) + int(m5.get("sells", 0))

            price_change_5m = float((p.get("priceChange") or {}).get("m5") or 0)
            volume_5m       = float((p.get("volume") or {}).get("m5") or 0)
            volume_h1       = float((p.get("volume") or {}).get("h1") or 0)
            volume_h24      = float((p.get("volume") or {}).get("h24") or 0)

            # Volume effectif = le plus pertinent disponible
            volume_usd = volume_h1 if volume_h1 > 0 else volume_h24

            return {
                "mcap_usd":        float(p.get("fdv") or p.get("marketCap") or 0),
                "volume_usd":      volume_usd,
                "volume_5m":       volume_5m,
                "txs_5m":          txs_5m,
                "price_change_5m": price_change_5m,
                "price_usd":       float(p.get("priceUsd") or 0),
            }
    except Exception as e:
        print(f"[DEXSCREENER] ❌ {mint[:8]} : {e}")
        return {}


def calculer_score(d: dict, elapsed: float) -> tuple:
    score  = 0
    points = []

    # Volume H1 (max 30 pts)
    v = d["volume_usd"]
    if v >= 50000:   score += 30; points.append("📊 Volume explosif (50K$+/h)")
    elif v >= 20000: score += 22; points.append("📊 Volume fort (20K$+/h)")
    elif v >= 10000: score += 15; points.append("📊 Volume correct (10K$+/h)")
    elif v >= 3000:  score += 8

    # Transactions 5 min (max 25 pts)
    tx = d["txs_5m"]
    if tx >= 150:  score += 25; points.append("⚡ Activité explosive (150+ tx/5min)")
    elif tx >= 80: score += 18; points.append("⚡ Forte activité (80+ tx/5min)")
    elif tx >= 40: score += 12; points.append("⚡ Bonne activité (40+ tx/5min)")
    elif tx >= 20: score += 6

    # Momentum prix 5 min (max 25 pts)
    pc = d["price_change_5m"]
    if pc >= 50:   score += 25; points.append("🚀 Pump violent (+50%/5min)")
    elif pc >= 20: score += 18; points.append("📈 Momentum fort (+20%/5min)")
    elif pc >= 10: score += 12; points.append("📈 Momentum positif (+10%/5min)")
    elif pc >= 5:  score += 7;  points.append("📈 Légère hausse (+5%/5min)")
    elif pc > 0:   score += 3

    # Timing création (max 20 pts)
    mins = elapsed / 60
    if 2 <= mins <= 5:   score += 20; points.append(f"⏱️ Timing parfait ({mins:.0f} min)")
    elif 5 < mins <= 7:  score += 14; points.append(f"⏱️ Bon timing ({mins:.0f} min)")
    elif 7 < mins <= 10: score += 7

    return min(score, 100), points


def emoji_score(score: int) -> str:
    if score >= 75: return "🟢"
    if score >= 50: return "🟡"
    return "🔴"


async def monitor_tokens():
    print("[MONITOR] 🔍 Surveillance DexScreener démarrée...")
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
            await asyncio.sleep(0.5)

            d = await check_token_dexscreener(mint)
            if not d:
                continue

            mcap_usd  = d["mcap_usd"]
            volume    = d["volume_usd"]
            vol_5m    = d["volume_5m"]
            txs_5m    = d["txs_5m"]
            pc_5m     = d["price_change_5m"]

            print(
                f"[POLL] {token_info['name'][:18]:<18} | "
                f"MC:{mcap_usd:>8,.0f}$ | "
                f"Vol1h:{volume:>7,.0f}$ | "
                f"Vol5m:{vol_5m:>6,.0f}$ | "
                f"Tx5m:{txs_5m:>4} | "
                f"Δ:{pc_5m:>+6.1f}% | "
                f"{elapsed:.0f}s"
            )

            # ── Filtre MC minimum ──
            if mcap_usd < TARGET_MCAP_USD:
                continue

            # ── Filtres fiables pré-migration ──
            reasons = []
            if volume  < MIN_VOLUME_USD: reasons.append(f"vol {volume:,.0f}$<{MIN_VOLUME_USD:,.0f}$")
            if txs_5m  < MIN_TXS_5MIN:  reasons.append(f"tx5m {txs_5m}<{MIN_TXS_5MIN}")
            if pc_5m   <= 0:            reasons.append(f"prix baisse ({pc_5m:+.1f}%)")

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
            points_str = "\n".join(f"  {p}" for p in points) if points else "  —"

            print(f"🎯 [ALERTE] {name} | Score:{score}/100 | MC:{mcap_usd:,.0f}$ | Vol:{volume:,.0f}$ | Tx:{txs_5m} | {time_str}")

            message = (
                f"{em} *SIGNAL {score}/100 — ANALYSE AVANT ENTRÉE*\n\n"
                f"• *Nom :* {name} ({symbol})\n"
                f"• *Market Cap :* `{mcap_usd:,.0f}$`\n"
                f"• *Volume 1h :* `{volume:,.0f}$`\n"
                f"• *Volume 5min :* `{vol_5m:,.0f}$`\n"
                f"• *Tx (5 min) :* `{txs_5m}`\n"
                f"• *Prix Δ5min :* `{pc_5m:+.1f}%`\n"
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

        if checked > 0:
            print(f"[MONITOR] Cycle terminé — {checked} tokens vérifiés")

        await asyncio.sleep(POLL_INTERVAL)


async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Démarrage Sniper 10K→100K ===")
    await send_telegram_alert(
        "🚀 *Sniper 10K→100K Actif*\n\n"
        f"💰 Cible MC : `{TARGET_MCAP_USD:,.0f}$`\n"
        f"📊 Min volume 1h : `{MIN_VOLUME_USD:,.0f}$`\n"
        f"⚡ Min tx/5min : `{MIN_TXS_5MIN}`\n"
        f"📈 Prix : hausse uniquement\n"
        f"⏱️ Fenêtre : `{MIN_AGE_SECONDS//60}-{MAX_AGE_SECONDS//60} min`\n\n"
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
        "target_mcap_usd": TARGET_MCAP_USD,
        "min_volume_usd":  MIN_VOLUME_USD,
        "min_txs_5min":    MIN_TXS_5MIN,
        "min_age_seconds": MIN_AGE_SECONDS,
        "tracked_tokens":  len(tracked_new_tokens),
        "active_tokens":   active
    }
