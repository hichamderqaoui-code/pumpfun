import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets
from collections import OrderedDict
import time
 
# ─── CONFIGURATION ───
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()
 
# 🎯 Zone cible
MIN_MCAP_USD        = 8000.0
MAX_MCAP_USD        = 15000.0
 
# ⏱️ Age max pour tracker un token (20 min)
MAX_TOKEN_AGE_SEC   = 1200
 
# 🔎 Filtre : achat initial minimum pour tracker
MIN_INITIAL_BUY_SOL = 0.3
 
# ⏱️ Intervalle polling (secondes)
POLL_INTERVAL       = 5
 
TOTAL_EVENTS = 0
TOTAL_ALERTS = 0
tracked_tokens = OrderedDict()
MAX_TRACKED = 500
 
def safe_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except:
        return 0.0
 
def register_token(mint: str):
    if mint not in tracked_tokens:
        if len(tracked_tokens) >= MAX_TRACKED:
            tracked_tokens.popitem(last=False)
        tracked_tokens[mint] = {"created_at": time.time(), "alerted": False}
 
async def send_telegram_alert(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
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
 
# ─── PUMPPORTAL : détecte les nouveaux tokens ───────────────────────────────
async def listener_new_tokens():
    global TOTAL_EVENTS
    uri = "wss://pumpportal.fun/api/data"
 
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10, max_size=10_000_000) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                print("[PUMPPORTAL] Connecté. Écoute active...", flush=True)
 
                async for raw in ws:
                    TOTAL_EVENTS += 1
                    try:
                        data = json.loads(raw if isinstance(raw, str) else raw.decode('utf-8', errors='ignore'))
                    except:
                        continue
 
                    if data.get("txType") != "create":
                        continue
 
                    mint = data.get("mint", "")
                    if not mint:
                        continue
 
                    initial_buy = safe_float(data.get("solAmount"))
                    if initial_buy < MIN_INITIAL_BUY_SOL:
                        continue
 
                    register_token(mint)
                    name   = data.get("name", "???")
                    symbol = data.get("symbol", "???")
                    print(f"[TRACK] {name} (${symbol}) {mint[:12]}... initialBuy={initial_buy:.3f} SOL", flush=True)
 
        except Exception as e:
            print(f"[PUMPPORTAL RETRY] {e} — reconnexion dans 4s...", flush=True)
            await asyncio.sleep(4)
 
# ─── POLLING : vérifie le mcap de chaque token tracké ───────────────────────
async def poller():
    global TOTAL_ALERTS
    print("[POLLER] Démarrage...", flush=True)
 
    async with httpx.AsyncClient(timeout=8.0) as client:
        while True:
            await asyncio.sleep(POLL_INTERVAL)
 
            now = time.time()
            active = [
                mint for mint, info in list(tracked_tokens.items())
                if not info["alerted"] and (now - info["created_at"]) < MAX_TOKEN_AGE_SEC
            ]
 
            if not active:
                print(f"[POLLER] Aucun token actif à surveiller.", flush=True)
                continue
 
            print(f"[POLLER] Vérification de {len(active)} token(s)...", flush=True)
 
            for mint in active:
                if mint not in tracked_tokens:
                    continue
                info = tracked_tokens[mint]
                if info["alerted"]:
                    continue
 
                try:
                    resp = await client.get(f"https://frontend-api.pump.fun/coins/{mint}")
                    if resp.status_code != 200:
                        continue
 
                    coin = resp.json()
                    mcap_usd = safe_float(coin.get("usd_market_cap"))
                    age = now - info["created_at"]
 
                    if mcap_usd > 0:
                        print(f"[MCAP] {mint[:12]}... = {mcap_usd:,.0f}$ (age={int(age)}s)", flush=True)
 
                    # 🎯 ZONE CIBLE ATTEINTE
                    if MIN_MCAP_USD <= mcap_usd <= MAX_MCAP_USD:
                        info["alerted"] = True
                        TOTAL_ALERTS += 1
 
                        name   = coin.get("name", "???")
                        symbol = coin.get("symbol", "???")
                        time_str = f"{int(age // 60)}m {int(age % 60)}s" if age >= 60 else f"{int(age)}s"
 
                        print(f"🔥 [DÉTECTION] {name} (${symbol}) → {mcap_usd:,.0f}$ en {time_str}", flush=True)
 
                        msg = (
                            f"🔥 <b>TOKEN EN ZONE 8K-15K$</b> 🔥\n\n"
                            f"• <b>Token :</b> {name} <code>${symbol}</code>\n"
                            f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                            f"• <b>Âge :</b> <code>{time_str}</code> ⏱️\n\n"
                            f"📊 <b>Outils :</b>\n"
                            f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                            f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n"
                            f"• <a href='https://pump.fun/{mint}'>Pump.fun</a>\n\n"
                            f"📥 <b>CA :</b> <code>{mint}</code>"
                        )
                        asyncio.create_task(send_telegram_alert(msg))
 
                    # Token trop haut → stop tracking
                    elif mcap_usd > MAX_MCAP_USD * 3:
                        info["alerted"] = True
 
                except Exception as e:
                    print(f"[POLL ERROR] {mint[:12]}... {e}", flush=True)
 
                await asyncio.sleep(0.1)
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=== [BOT] Démarrage — PumpPortal + Pump.fun polling ===", flush=True)
    await send_telegram_alert("⚡ <b>Bot démarré</b> — Détection zone 8K-15K$ active...")
    t1 = asyncio.create_task(listener_new_tokens())
    t2 = asyncio.create_task(poller())
    yield
    t1.cancel()
    t2.cancel()
 
app = FastAPI(lifespan=lifespan)
 
@app.get("/")
def home():
    now = time.time()
    active = sum(1 for info in tracked_tokens.values()
                 if not info["alerted"] and (now - info["created_at"]) < MAX_TOKEN_AGE_SEC)
    return {
        "status": "online",
        "events_processed": TOTAL_EVENTS,
        "alerts_sent": TOTAL_ALERTS,
        "tracked_tokens": len(tracked_tokens),
        "active_tokens": active,
    }
