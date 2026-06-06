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
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SOL_PRICE_USD      = float(os.getenv("SOL_PRICE_USD", "65.00"))  

# 🎯 ZONE STRETCH AXIOM PRO
MIN_STRETCH_MCAP   = 8000.0   
MAX_STRETCH_MCAP   = 15000.0  

TOTAL_EVENTS = 0
tracked_tokens = OrderedDict()
MAX_TRACKED = 3000

def register_token(mint: str):
    if mint not in tracked_tokens:
        if len(tracked_tokens) >= MAX_TRACKED:
            tracked_tokens.popitem(last=False)
        tracked_tokens[mint] = {"created_at": time.time(), "alerted": False}

async def send_telegram_alert(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=4.0)
    except Exception as e:
        print(f"[TG ERROR] {e}")

async def pumpportal_listener():
    global TOTAL_EVENTS
    uri = "wss://pumpportal.fun/api/data"
    
    print("=== [BOT] Connexion au flux direct PumpPortal ===")
    await asyncio.sleep(1)
    await send_telegram_alert("⚡ <b>Flux STRETCH connecté (PumpPortal)</b> — Écoute du marché en cours...")

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as websocket:
                # On s'abonne aux créations ET aux trades pour voir le prix monter
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                await websocket.send(json.dumps({"method": "subscribeTokenTrade"}))
                print("[PUMPPORTAL] Flux connecté. Écoute active...")

                async for message in websocket:
                    TOTAL_EVENTS += 1
                    data = json.loads(message)
                    
                    # On récupère l'adresse (mint) du token
                    mint = data.get("mint")
                    if not mint:
                        continue

                    # On enregistre le token s'il vient de naître
                    if data.get("txType") == "create":
                        register_token(mint)
                        continue

                    # Si c'est un Trade, on extrait directement le Market Cap fourni par PumpPortal
                    if data.get("txType") in ["buy", "sell"]:
                        register_token(mint)
                        token_info = tracked_tokens[mint]
                        
                        if token_info["alerted"]:
                            continue

                        # Calcul direct du Market Cap en USD via les données de PumpPortal
                        mcap_sol = safe_float(data.get("marketCapSol"))
                        mcap_usd = mcap_sol * SOL_PRICE_USD

                        # Si le mcap calculé est à 0, on utilise leur fallback en USD direct
                        if mcap_usd == 0:
                            mcap_usd = safe_float(data.get("usdMarketCap"))

                        # On vérifie si le token vient de franchir la zone Stretch !
                        if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
                            token_info["alerted"] = True
                            elapsed = time.time() - token_info["created_at"]
                            time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"

                            print(f"🔥 [DÉTECTION] Token {mint} est entré dans la zone Stretch : {mcap_usd:,.0f}$")

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
            print(f"[RETRY] Déconnexion flux ({e}). Réexpédition dans 4s...")
            await asyncio.sleep(4)

def safe_float(value) -> float:
    try: return float(value) if value else 0.0
    except: return 0.0

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(pumpportal_listener())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {"status": "online", "events_processed": TOTAL_EVENTS, "tracked": len(tracked_tokens)}
