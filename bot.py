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
SOL_PRICE_USD       = float(os.getenv("SOL_PRICE_USD", "170.0"))
 
# 🎯 Seuil d'achat initial minimum pour alerter (SOL)
# 1.5 SOL ≈ 9,000$ mcap au create → stretch zone
MIN_INITIAL_BUY_SOL = 1.5
 
TOTAL_EVENTS = 0
TOTAL_ALERTS = 0
alerted_tokens = OrderedDict()
MAX_ALERTED = 2000
 
def already_alerted(mint: str) -> bool:
    return mint in alerted_tokens
 
def mark_alerted(mint: str):
    if len(alerted_tokens) >= MAX_ALERTED:
        alerted_tokens.popitem(last=False)
    alerted_tokens[mint] = True
 
def safe_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except:
        return 0.0
 
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
 
async def pumpportal_listener():
    global TOTAL_EVENTS, TOTAL_ALERTS
    uri = "wss://pumpportal.fun/api/data"
 
    print("=== [BOT] Connexion PumpPortal — alerte directe sur CREATE ===", flush=True)
    await send_telegram_alert("⚡ <b>Bot STRETCH démarré</b> — Alerte directe sur création...")
 
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
                    if not mint or already_alerted(mint):
                        continue
 
                    initial_buy_sol = safe_float(data.get("solAmount"))
                    name   = data.get("name", "???")
                    symbol = data.get("symbol", "???")
 
                    # Calcul mcap estimé
                    mcap_sol = safe_float(data.get("marketCapSol"))
                    mcap_usd = mcap_sol * SOL_PRICE_USD
                    if mcap_usd == 0:
                        mcap_usd = safe_float(data.get("usdMarketCap"))
 
                    print(f"[CREATE] {name} ({symbol}) initialBuy={initial_buy_sol:.3f} SOL mcap={mcap_usd:,.0f}$", flush=True)
 
                    # ✅ Alerte si achat initial suffisant
                    if initial_buy_sol >= MIN_INITIAL_BUY_SOL:
                        mark_alerted(mint)
                        TOTAL_ALERTS += 1
 
                        mcap_str = f"{mcap_usd:,.0f}$" if mcap_usd > 0 else f"~{initial_buy_sol * SOL_PRICE_USD * 5:,.0f}$ (estimé)"
 
                        print(f"🔥 [ALERTE] {name} ({symbol}) {mint} initialBuy={initial_buy_sol:.3f} SOL", flush=True)
 
                        msg = (
                            f"🔥 <b>NOUVEAU TOKEN — GROS ACHAT INITIAL</b> 🔥\n\n"
                            f"• <b>Token :</b> {name} <code>${symbol}</code>\n"
                            f"• <b>Achat initial :</b> <code>{initial_buy_sol:.2f} SOL</code> 💎\n"
                            f"• <b>Market Cap :</b> <code>{mcap_str}</code> 💰\n\n"
                            f"📊 <b>Outils :</b>\n"
                            f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                            f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n"
                            f"• <a href='https://pump.fun/{mint}'>Pump.fun</a>\n\n"
                            f"📥 <b>CA :</b> <code>{mint}</code>"
                        )
                        asyncio.create_task(send_telegram_alert(msg))
 
        except Exception as e:
            print(f"[RETRY] {e} — reconnexion dans 4s...", flush=True)
            await asyncio.sleep(4)
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(pumpportal_listener())
    yield
    task.cancel()
 
app = FastAPI(lifespan=lifespan)
 
@app.get("/")
def home():
    return {
        "status": "online",
        "events_processed": TOTAL_EVENTS,
        "alerts_sent": TOTAL_ALERTS,
    }
