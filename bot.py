import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
from collections import OrderedDict
import time
 
# ─── CONFIGURATION ───
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
 
# 🎯 ZONE STRETCH
MIN_STRETCH_MCAP   = 8000.0
MAX_STRETCH_MCAP   = 15000.0
 
# ⏱️ Age max du token (10 min)
MAX_TOKEN_AGE_SEC  = 600
 
# ⏱️ Intervalle de polling (secondes)
POLL_INTERVAL      = 10
 
TOTAL_POLLS  = 0
TOTAL_ALERTS = 0
alerted_tokens = OrderedDict()
MAX_ALERTED    = 2000
 
def safe_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except:
        return 0.0
 
def already_alerted(mint: str) -> bool:
    return mint in alerted_tokens
 
def mark_alerted(mint: str):
    if len(alerted_tokens) >= MAX_ALERTED:
        alerted_tokens.popitem(last=False)
    alerted_tokens[mint] = True
 
async def send_telegram_alert(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG] Token ou Chat ID manquant", flush=True)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=4.0)
            print(f"[TG] Status={resp.status_code}", flush=True)
    except Exception as e:
        print(f"[TG ERROR] {e}", flush=True)
 
async def poller():
    global TOTAL_POLLS, TOTAL_ALERTS
 
    url = "https://frontend-api.pump.fun/coins?offset=0&limit=50&sort=last_trade_unix_timestamp&order=DESC&includeNsfw=true"
 
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
 
    print("[POLLER] Démarrage du polling Pump.fun...", flush=True)
 
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            TOTAL_POLLS += 1
 
            try:
                resp = await client.get(url)
 
                if resp.status_code != 200:
                    print(f"[POLLER] HTTP {resp.status_code}", flush=True)
                    continue
 
                coins = resp.json()
                if not isinstance(coins, list):
                    print(f"[POLLER] Réponse inattendue : {str(coins)[:100]}", flush=True)
                    continue
 
                now = time.time()
                print(f"[POLLER] {len(coins)} tokens reçus (poll #{TOTAL_POLLS})", flush=True)
 
                for coin in coins:
                    mint = coin.get("mint", "")
                    if not mint or already_alerted(mint):
                        continue
 
                    # Age du token
                    created_ts = safe_float(coin.get("created_timestamp", 0)) / 1000
                    if created_ts == 0:
                        continue
                    age = now - created_ts
                    if age > MAX_TOKEN_AGE_SEC or age < 0:
                        continue
 
                    # Market cap
                    mcap_usd = safe_float(coin.get("usd_market_cap"))
 
                    if mcap_usd > 0:
                        print(f"[MCAP] {mint[:12]}... = {mcap_usd:,.0f}$ (age={age:.0f}s)", flush=True)
 
                    # 🎯 STRETCH ZONE
                    if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
                        mark_alerted(mint)
                        TOTAL_ALERTS += 1
 
                        name   = coin.get("name", "???")
                        symbol = coin.get("symbol", "???")
                        time_str = f"{int(age // 60)}m {int(age % 60)}s" if age >= 60 else f"{age:.0f}s"
 
                        print(f"🔥 [DÉTECTION] {name} ({symbol}) {mint} → {mcap_usd:,.0f}$ en {time_str}", flush=True)
 
                        msg = (
                            f"⚡ <b>PAIRE EN EXPLOSION (STRETCH ZONE)</b> ⚡\n\n"
                            f"• <b>Token :</b> {name} <code>${symbol}</code>\n"
                            f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                            f"• <b>Âge au Stretch :</b> <code>{time_str}</code> 🔥\n\n"
                            f"📊 <b>Outils :</b>\n"
                            f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                            f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
                            f"📥 <b>CA :</b> <code>{mint}</code>"
                        )
                        asyncio.create_task(send_telegram_alert(msg))
 
            except Exception as e:
                print(f"[POLLER ERROR] {e}", flush=True)
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=== [BOT] Démarrage — Pump.fun API polling ===", flush=True)
    await send_telegram_alert("⚡ <b>Bot STRETCH démarré</b> — Polling Pump.fun actif...")
    task = asyncio.create_task(poller())
    yield
    task.cancel()
 
app = FastAPI(lifespan=lifespan)
 
@app.get("/")
def home():
    return {
        "status": "online",
        "total_polls": TOTAL_POLLS,
        "total_alerts": TOTAL_ALERTS,
        "alerted_tokens": len(alerted_tokens),
