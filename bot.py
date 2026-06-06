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
SOL_PRICE_USD     = float(os.getenv("SOL_PRICE_USD",    "66.12"))
MIGRATION_USD     = float(os.getenv("MIGRATION_USD",    "27000"))
MAX_AGE_SECONDS   = int(os.getenv("MAX_AGE_SECONDS",    "600"))

# ── Seuils qualité (profil équilibré 5-10K$) ──
MIN_MCAP_USD      = float(os.getenv("MIN_MCAP_USD",     "5000"))   # Alerte dès 5K$
MAX_MCAP_USD      = float(os.getenv("MAX_MCAP_USD",     "10000"))  # Ignore au-dessus 10K$
MIN_UNIQUE_BUYERS = int(os.getenv("MIN_UNIQUE_BUYERS",  "10"))     # Min 10 acheteurs distincts
MIN_VOLUME_USD    = float(os.getenv("MIN_VOLUME_USD",   "500"))    # Min 500$ de volume
MIN_TX_COUNT      = int(os.getenv("MIN_TX_COUNT",       "15"))     # Min 15 transactions
MIN_BUY_RATIO     = float(os.getenv("MIN_BUY_RATIO",   "0.60"))   # 60%+ d'achats

tracked_tokens: OrderedDict = OrderedDict()
MAX_TRACKED = 1000
alert_queue: asyncio.Queue = None


def calculer_market_cap_usd(data: dict) -> float:
    mcap_sol = float(data.get("marketCapSol") or 0)
    if mcap_sol > 0:
        return mcap_sol * SOL_PRICE_USD
    v_tokens = float(data.get("vTokenReserves") or 0) / 1e6
    if v_tokens > 0:
        return (30 / v_tokens) * 1_000_000_000 * SOL_PRICE_USD
    return 0.0


def register_new_token(data: dict):
    mint = data.get("mint")
    if not mint or mint in tracked_tokens:
        return
    if len(tracked_tokens) >= MAX_TRACKED:
        tracked_tokens.popitem(last=False)
    tracked_tokens[mint] = {
        "name":       data.get("name", "Unknown"),
        "symbol":     data.get("symbol", "?"),
        "created_at": time.time(),
        "buyers":     set(),
        "sellers":    set(),
        "alerted":    False,
        "volume_usd": 0.0,
        "tx_count":   0,
        "buy_count":  0,
        "sell_count": 0,
    }
    print(f"🆕 [NEW] {data.get('name')} ({mint[:8]}...)")


async def send_telegram_alert(message: str, is_test: bool = False):
    token   = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not token or not chat_id:
        print(f"[TELEGRAM] ❌ MANQUANT")
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
                print(f"[TELEGRAM] ❌ {r.status_code} : {r.text}")
            else:
                print(f"[TELEGRAM] ✅ Alerte envoyée.")
    except Exception as e:
        print(f"[TELEGRAM] ❌ {e}")


async def telegram_worker():
    print("[TELEGRAM WORKER] ✅ Démarré.")
    while True:
        try:
            message = await asyncio.wait_for(alert_queue.get(), timeout=30.0)
            await send_telegram_alert(message)
            alert_queue.task_done()
            await asyncio.sleep(0.5)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"[TELEGRAM WORKER] ❌ {e}")


def calculer_score(token_info: dict, mcap_usd: float, elapsed: float) -> tuple:
    """Score qualité 0-100 basé sur les signaux on-chain"""
    score  = 0
    raisons = []

    unique_buyers = len(token_info["buyers"])
    buy_ratio     = token_info["buy_count"] / max(token_info["tx_count"], 1)
    bc_pct        = (mcap_usd / MIGRATION_USD) * 100

    # Holders uniques (max 30 pts)
    if unique_buyers >= 50:   score += 30; raisons.append("👥 Forte communauté (50+ acheteurs)")
    elif unique_buyers >= 30: score += 22; raisons.append("👥 Bonne communauté (30+ acheteurs)")
    elif unique_buyers >= 15: score += 14; raisons.append("👥 Communauté correcte (15+ acheteurs)")
    elif unique_buyers >= 10: score += 8

    # Pression achat (max 25 pts)
    if buy_ratio >= 0.80:   score += 25; raisons.append("🟢 Pression achat forte (80%+ buys)")
    elif buy_ratio >= 0.70: score += 18; raisons.append("🟢 Majorité acheteurs (70%+)")
    elif buy_ratio >= 0.60: score += 10; raisons.append("🟢 Bonne dynamique achat (60%+)")

    # Volume (max 20 pts)
    vol = token_info["volume_usd"]
    if vol >= 5000:   score += 20; raisons.append("📊 Volume explosif (5K$+)")
    elif vol >= 2000: score += 14; raisons.append("📊 Bon volume (2K$+)")
    elif vol >= 500:  score += 7

    # Activité transactions (max 15 pts)
    tx = token_info["tx_count"]
    if tx >= 100:  score += 15; raisons.append("⚡ Activité intense (100+ tx)")
    elif tx >= 50: score += 10; raisons.append("⚡ Bonne activité (50+ tx)")
    elif tx >= 15: score += 5

    # Position bonding curve (max 10 pts) — zone idéale 20-60%
    if 20 <= bc_pct <= 60:   score += 10; raisons.append(f"📉 Zone idéale bonding curve ({bc_pct:.0f}%)")
    elif 10 <= bc_pct < 20:  score += 5
    elif 60 < bc_pct <= 80:  score += 5;  raisons.append(f"⚠️ Bonding curve avancée ({bc_pct:.0f}%)")

    return min(score, 100), raisons


def analyser_trade_streaming(data: dict):
    mint = data.get("mint")
    if not mint or mint not in tracked_tokens:
        return

    token_info = tracked_tokens[mint]
    if token_info["alerted"]:
        return

    now     = time.time()
    elapsed = now - token_info["created_at"]

    if elapsed > MAX_AGE_SECONDS:
        token_info["alerted"] = True
        return

    # Mise à jour stats
    trader     = data.get("traderPublicKey")
    sol_amount = float(data.get("solAmount") or 0) / 1e9
    is_buy     = data.get("txType") == "buy"

    if trader:
        if is_buy:
            token_info["buyers"].add(trader)
        else:
            token_info["sellers"].add(trader)

    token_info["tx_count"]   += 1
    token_info["volume_usd"] += sol_amount * SOL_PRICE_USD
    if is_buy:
        token_info["buy_count"]  += 1
    else:
        token_info["sell_count"] += 1

    mcap_usd      = calculer_market_cap_usd(data)
    unique_buyers = len(token_info["buyers"])
    bc_pct        = min((mcap_usd / MIGRATION_USD) * 100, 100) if MIGRATION_USD > 0 else 0

    # Log toutes les 10 tx
    if token_info["tx_count"] % 10 == 0:
        buy_ratio = token_info["buy_count"] / max(token_info["tx_count"], 1)
        print(
            f"⚡ [STREAM] {token_info['name'][:14]:<14} | "
            f"MC: {mcap_usd:>7,.0f}$ | "
            f"BC: {bc_pct:>3.0f}% | "
            f"Buyers: {unique_buyers:>3} | "
            f"Buy%: {buy_ratio*100:.0f}% | "
            f"Vol: {token_info['volume_usd']:>6,.0f}$ | "
            f"{elapsed:.0f}s"
        )

    # ── Filtres d'entrée ──
    if mcap_usd < MIN_MCAP_USD or mcap_usd > MAX_MCAP_USD:
        return
    if unique_buyers < MIN_UNIQUE_BUYERS:
        return
    if token_info["volume_usd"] < MIN_VOLUME_USD:
        return
    if token_info["tx_count"] < MIN_TX_COUNT:
        return
    buy_ratio = token_info["buy_count"] / max(token_info["tx_count"], 1)
    if buy_ratio < MIN_BUY_RATIO:
        return
    if mcap_usd >= MIGRATION_USD:
        token_info["alerted"] = True
        return

    # ── Score qualité ──
    score, raisons = calculer_score(token_info, mcap_usd, elapsed)

    # Seuil minimum : score >= 40
    if score < 40:
        print(f"[SCORE] ❌ {token_info['name']} score {score}/100 — ignoré")
        return

    token_info["alerted"] = True

    name      = token_info["name"]
    symbol    = token_info["symbol"]
    time_str  = f"{int(elapsed//60)}m {int(elapsed%60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"
    mult_mig  = MIGRATION_USD / mcap_usd
    mult_100k = 100000 / mcap_usd
    reste_pct = 100 - bc_pct

    # Emoji selon score
    if score >= 75:   em = "🟢"
    elif score >= 55: em = "🟡"
    else:             em = "🔵"

    raisons_str = "\n".join(f"  {r}" for r in raisons) if raisons else "  —"

    print(f"🎯 [ALERTE {score}/100] {name} | MC: {mcap_usd:,.0f}$ | BC: {bc_pct:.0f}% | Buyers: {unique_buyers} | {time_str}")

    message = (
        f"{em} *SIGNAL {score}/100 — PRÉ-MIGRATION*\n\n"
        f"• *Nom :* {name} ({symbol})\n"
        f"• *Market Cap :* `{mcap_usd:,.0f}$`\n"
        f"• *Bonding Curve :* `{bc_pct:.0f}%` _(reste {reste_pct:.0f}% avant Raydium)_\n"
        f"• *Acheteurs uniques :* `{unique_buyers}`\n"
        f"• *Ratio buy/sell :* `{buy_ratio*100:.0f}%` achats\n"
        f"• *Volume :* `{token_info['volume_usd']:,.0f}$`\n"
        f"• *Transactions :* `{token_info['tx_count']}`\n"
        f"• *Âge :* `{time_str}` ⏱️\n\n"
        f"📈 *Objectifs :*\n"
        f"• x{mult_mig:.1f} → migration (27K$)\n"
        f"• x{mult_100k:.1f} → 100K$\n\n"
        f"✨ *Signaux positifs :*\n{raisons_str}\n\n"
        "🔍 *Liens :*\n"
        f"• [Pump.fun](https://pump.fun/{mint})\n"
        f"• [BullX](https://bullx.io/terminal?chain=solana&address={mint})\n"
        f"• [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
        f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
        "📥 *CA :*\n"
        f"`{mint}`"
    )

    if alert_queue:
        alert_queue.put_nowait(message)
    else:
        print("[TELEGRAM] ❌ Queue non initialisée !")


async def solana_websocket_listener():
    uri = "wss://pumpportal.fun/api/data"
    print("=== [BOT] Sniper Qualité Pré-Migration démarré ===")
    await send_telegram_alert(
        f"🚀 *Sniper Qualité en ligne*\n\n"
        f"💰 Zone cible : `{MIN_MCAP_USD:,.0f}$ — {MAX_MCAP_USD:,.0f}$` MC\n"
        f"🎯 Migration Raydium : `{MIGRATION_USD:,.0f}$`\n"
        f"👥 Min acheteurs : `{MIN_UNIQUE_BUYERS}`\n"
        f"📊 Min volume : `{MIN_VOLUME_USD:,.0f}$`\n"
        f"🟢 Min buy ratio : `{int(MIN_BUY_RATIO*100)}%`\n"
        f"💵 SOL : `{SOL_PRICE_USD}$`\n\n"
        "🎯 Stratégie : Entrée 5-10K$ → Sortie 50-100K$",
        is_test=True
    )

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                print("[WEBSOCKET] ✅ Connecté à pumpportal.fun")
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeAllTokenTrades"}))
                print("[WEBSOCKET] 📡 Streaming global actif.")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except Exception:
                        continue

                    tx_type = data.get("txType")
                    if tx_type == "create":
                        register_new_token(data)
                    elif tx_type in ["buy", "sell"] or "marketCapSol" in data:
                        analyser_trade_streaming(data)

        except websockets.exceptions.ConnectionClosed:
            print("[WEBSOCKET] ❌ Reconnexion dans 3s...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[WEBSOCKET] ❌ Erreur : {e}")
            await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global alert_queue
    alert_queue = asyncio.Queue()
    worker_task = asyncio.create_task(telegram_worker())
    ws_task     = asyncio.create_task(solana_websocket_listener())
    yield
    ws_task.cancel()
    worker_task.cancel()
    for task in [ws_task, worker_task]:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


@app.get("/")
def home():
    return {
        "status":            "active",
        "source":            "pumpportal.fun",
        "sol_price_usd":     SOL_PRICE_USD,
        "zone_mcap":         f"{MIN_MCAP_USD:,.0f}$ — {MAX_MCAP_USD:,.0f}$",
        "migration_usd":     MIGRATION_USD,
        "min_unique_buyers": MIN_UNIQUE_BUYERS,
        "min_volume_usd":    MIN_VOLUME_USD,
        "min_buy_ratio":     f"{int(MIN_BUY_RATIO*100)}%",
        "tracked_tokens":    len(tracked_tokens),
        "queue_size":        alert_queue.qsize() if alert_queue else 0,
    }


@app.get("/test-telegram")
async def test_telegram():
    await send_telegram_alert("✅ TEST OK — Bot connecté !", is_test=True)
    return {"status": "envoyé"}
