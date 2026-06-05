import asyncio
import os
import json
import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import websockets
from collections import OrderedDict
import time

# ─── CONFIGURATION VIA VARIABLES D'ENVIRONNEMENT ───
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
SOL_PRICE_USD      = float(os.getenv("SOL_PRICE_USD", "65.00"))  
HELIUS_API_KEY     = os.getenv("HELIUS_API_KEY", "")  # Mets ta clé Helius ici

# 🎯 CRITÈRES NOUVELLES PAIRES À POTENTIEL (Axiom Stretch)
MIN_STRETCH_MCAP   = 8000.0   
MAX_STRETCH_MCAP   = 15000.0  
MAX_TOKEN_AGE_SEC  = 900.0    # 15 minutes max pour attraper uniquement le l'explosion de départ

TOTAL_MESSAGES_RECEIVED = 0
tracked_tokens = OrderedDict()
MAX_TRACKED = 5000  

def safe_float(value, default=0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def register_new_token(mint: str, name: str, symbol: str):
    if not mint or mint in tracked_tokens:
        return
    if len(tracked_tokens) >= MAX_TRACKED:
        tracked_tokens.popitem(last=False)
        
    tracked_tokens[mint] = {
        "name": name,
        "symbol": symbol,
        "created_at": time.time(),
        "alerted": False
    }

async def send_telegram_alert(message: str):
    token = str(TELEGRAM_TOKEN).strip()
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=5.0)
    except Exception as e:
        print(f"[TELEGRAM ERREUR] -> {e}")

def analyser_helius_transaction(tx_info: dict):
    # Extraction des données spécifiques au programme Pump.fun via le log Helius
    meta = tx_info.get("transaction", {}).get("meta", {})
    if meta and meta.get("err") is not None:
        return  # On ignore les transactions échouées

    # Recherche des informations de token et de bonding curve dans les innerInstructions ou les logs
    # Note : Le calcul du Market Cap se base sur les soldes de jetons (postTokenBalances)
    post_balances = meta.get("postTokenBalances", [])
    
    mint = None
    for balance in post_balances:
        if balance.get("owner") == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5AMX787Nz": # Compte global Pump.fun
            mint = balance.get("mint")
            break
            
    if not mint:
        return

    now = time.time()

    # Si c'est une création brute détectée dans les balances
    if mint not in tracked_tokens:
        register_new_token(mint, name="Nouveau Jeton", symbol="PUMP")

    token_info = tracked_tokens[mint]
    
    # 🛑 FILTRE ULTRA-CIBLÉ : Si le token a plus de 15 minutes, il n'est plus considéré comme "nouveau"
    if (now - token_info["created_at"]) > MAX_TOKEN_AGE_SEC:
        return

    if token_info["alerted"]:
        return

    # Simulation / Extraction de l'état de la bonding curve pour le calcul du MCAP en SOL
    # Sur Helius, on peut estimer le mcap par rapport au ratio de jetons restants dans la courbe
    for balance in post_balances:
        if balance.get("mint") == mint and balance.get("owner") == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5AMX787Nz":
            ui_amount = safe_float(balance.get("uiTokenAmount", {}).get("uiAmount"))
            if ui_amount > 0:
                # Formule d'estimation du prix de la bonding curve Pump.fun standard
                mcap_sol = (1000000000 - ui_amount) * 0.00000003 + 30 
                mcap_usd = mcap_sol * SOL_PRICE_USD

                # 🎯 SEUIL EXPLOSION DE LA COLONNE STRETCH
                if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
                    token_info["alerted"] = True
                    elapsed = now - token_info["created_at"]
                    time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"

                    print(f"🔥 [EXPLOSION DETECTED] Nouvelle paire active : {mint} à {mcap_usd:,.0f}$ !")

                    message = (
                        f"⚡ <b>NOUVELLE PAIRE EN EXPLOSION</b> ⚡\n\n"
                        f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                        f"• <b>Temps depuis naissance :</b> <code>{time_str}</code> 🔥\n\n"
                        f"📊 <b>Sniper Direct :</b>\n"
                        f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                        f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
                        f"📥 <b>CA :</b> <code>{mint}</code>"
                    )
                    asyncio.create_task(send_telegram_alert(message))

async def solana_websocket_listener():
    global TOTAL_MESSAGES_RECEIVED
    if not HELIUS_API_KEY.strip():
        print("[ERREUR] HELIUS_API_KEY manquante dans tes variables d'environnement !")
        return

    uri = f"wss://atlas.helius-rpc.com?api-key={HELIUS_API_KEY}"
    print("=== [BOT] Connexion au RPC Helius (Mode Explosion Nouvelle Paire) ===")
    
    await asyncio.sleep(2)
    asyncio.create_task(send_telegram_alert("⚡ <b>Sniper Helius Actif</b> — Recherche exclusive de nouvelles paires explosives..."))

    while True:
        try:
            async with websockets.connect(uri, ping_interval=25, ping_timeout=15) as websocket:
                # On s'abonne directement au flux de transactions du programme Pump.fun
                subscribe_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "transactionSubscribe",
                    "params": [
                        {
                            "failed": False,
                            "accountRequired": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5AMX787Nz"] # Pump.fun Program ID
                        },
                        {
                            "commitment": "confirmed",
                            "encoding": "json",
                            "transactionDetails": "full"
                        }
                    ]
                }
                await websocket.send(json.dumps(subscribe_payload))
                print("[HELIUS] 📡 WebSocket connecté. Écoute directe de la Blockchain Solana en cours.")

                async for message in websocket:
                    TOTAL_MESSAGES_RECEIVED += 1
                    
                    # Tu vas voir que le compteur va grimper à toute vitesse !
                    if TOTAL_MESSAGES_RECEIVED % 200 == 0:
                        print(f"[RPC LIVE] Flux Helius : {TOTAL_MESSAGES_RECEIVED} transactions lues en direct...")

                    try:
                        data = json.loads(message)
                        params = data.get("params", {})
                        result = params.get("result", {})
                        if result:
                            analyser_helius_transaction(result)
                    except Exception:
                        continue

        except websockets.exceptions.ConnectionClosed:
            print("[RPC] Connexion perdue avec Helius, reconnexion dans 3s...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[RPC ERREUR] -> {e}")
            await asyncio.sleep(3)

@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_task = asyncio.create_task(solana_websocket_listener())
    yield
    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)

@app.get("/")
def health_check():
    return {
        "status": "online",
        "source": "Solana RPC via Helius",
        "total_transactions_scanned": TOTAL_MESSAGES_RECEIVED,
        "tracked_recent_tokens": len(tracked_tokens)
    }
