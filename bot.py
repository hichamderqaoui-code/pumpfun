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
SOL_PRICE_USD       = float(os.getenv("SOL_PRICE_USD", "61.73"))
 
# 🎯 ZONE STRETCH AXIOM PRO
MIN_STRETCH_MCAP    = 8000.0
MAX_STRETCH_MCAP    = 15000.0
 
# ⏱️ Ignorer les tokens trop vieux (> 10 min)
MAX_TOKEN_AGE_SEC   = 600
 
# 🔎 Filtre création : achat initial minimum en SOL
MIN_INITIAL_BUY_SOL = 0.3
 
TOTAL_EVENTS  = 0
tracked_tokens = OrderedDict()
MAX_TRACKED   = 2000
 
# File partagée entre les deux connexions WebSocket
pending_subscriptions: asyncio.Queue = None
 
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
 
# ─── CONNEXION 1 : écoute des nouveaux tokens ───────────────────────────────
async def listener_new_tokens():
    global TOTAL_EVENTS
    uri = "wss://pumpportal.fun/api/data"
 
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10, max_size=10_000_000) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                print("[NEW_TOKENS] Connexion active — écoute des créations...", flush=True)
 
                async for raw in ws:
                    TOTAL_EVENTS += 1
                    try:
                        data = json.loads(raw if isinstance(raw, str) else raw.decode('utf-8', errors='ignore'))
                    except:
                        continue
 
                    if data.get("txType") != "create":
                        continue
 
                    mint = data.get("mint")
                    if not mint:
                        continue
 
                    initial_buy = safe_float(data.get("solAmount"))
 
                    if initial_buy < MIN_INITIAL_BUY_SOL:
                        print(f"[SKIP] {mint[:12]}... initialBuy={initial_buy:.3f} SOL", flush=True)
                        continue
 
                    register_token(mint)
                    await pending_subscriptions.put(mint)
                    print(f"[CREATE] ✅ {mint[:12]}... initialBuy={initial_buy:.3f} SOL → en file", flush=True)
 
        except Exception as e:
            print(f"[NEW_TOKENS RETRY] {e} — reconnexion dans 4s...", flush=True)
            await asyncio.sleep(4)
 
# ─── CONNEXION 2 : écoute des trades des tokens filtrés ─────────────────────
async def listener_trades():
    global TOTAL_EVENTS
    uri = "wss://pumpportal.fun/api/data"
 
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10, max_size=10_000_000) as ws:
                print("[TRADES] Connexion active — en attente de tokens à tracker...", flush=True)
 
                async def subscribe_loop():
                    while True:
                        mint = await pending_subscriptions.get()
                        await ws.send(json.dumps({
                            "method": "subscribeTokenTrade",
                            "keys": [mint]
                        }))
                        print(f"[TRADES] Subscribed → {mint[:12]}...", flush=True)
 
                asyncio.create_task(subscribe_loop())
 
                async for raw in ws:
                    TOTAL_EVENTS += 1
                    try:
                        data = json.loads(raw if isinstance(raw, str) else raw.decode('utf-8', errors='ignore'))
                    except:
                        continue
 
                    tx_type = data.get("txType")
                    if tx_type not in ["buy", "sell"]:
                        continue
 
                    mint = data.get("mint")
                    if not mint or mint not in tracked_tokens:
                        continue
 
                    token_info = tracked_tokens[mint]
 
                    if token_info["alerted"]:
                        continue
 
                    age = time.time() - token_info["created_at"]
                    if age > MAX_TOKEN_AGE_SEC:
                        continue
 
                    mcap_sol = safe_float(data.get("marketCapSol"))
                    mcap_usd = mcap_sol * SOL_PRICE_USD
                    if mcap_usd == 0:
                        mcap_usd = safe_float(data.get("usdMarketCap"))
 
                    if mcap_usd > 0:
                        print(f"[MCAP] {mint[:12]}... = {mcap_usd:,.0f}$ (age={age:.0f}s {tx_type})", flush=True)
 
                    # 🎯 STRETCH ZONE
                    if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
                        token_info["alerted"] = True
                        time_str = f"{int(age // 60)}m {int(age % 60)}s" if age >= 60 else f"{age:.0f}s"
 
                        print(f"🔥 [DÉTECTION] {mint} → {mcap_usd:,.0f}$ en {time_str}", flush=True)
 
                        msg = (
                            f"⚡ <b>PAIRE EN EXPLOSION (STRETCH ZONE)</b> ⚡\n\n"
                            f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                            f"• <b>Âge au Stretch :</b> <code>{time_str}</code> 🔥\n\n"
                            f"📊 <b>Outils :</b>\n"
                            f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                            f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
                            f"📥 <b>CA :</b> <code>{mint}</code>"
                        )
                        asyncio.create_task(send_telegram_alert(msg))
 
        except Exception as e:
            print(f"[TRADES RETRY] {e} — reconnexion dans 4s...", flush=True)
            await asyncio.sleep(4)
 
# ─── STARTUP ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global pending_subscriptions
    pending_subscriptions = asyncio.Queue()
 
    print("=== [BOT] Démarrage — 2 connexions PumpPortal ===", flush=True)
    await send_telegram_alert("⚡ <b>Flux STRETCH connecté (PumpPortal)</b> — Écoute du marché en cours...")
 
    t1 = asyncio.create_task(listener_new_tokens())
    t2 = asyncio.create_task(listener_trades())
    yield
    t1.cancel()
    t2.cancel()
 
app = FastAPI(lifespan=lifespan)
 
@app.get("/")
def home():
    return {
        "status": "online",
        "events_processed": TOTAL_EVENTS,
        "tracked_tokens": len(tracked_tokens),
        "pending_subs": pending_subscriptions.qsize() if pending_subscriptions else 0
    }
