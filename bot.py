import asyncio
import os
import httpx  # Assure-toi d'avoir 'httpx' dans ton requirements.txt
from fastapi import FastAPI
from contextlib import asynccontextmanager

# Récupération des identifiants (à configurer dans l'onglet Variables de Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "TON_TOKEN_BOT")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "TON_CHAT_ID")

# Fonction asynchrone pour envoyer les alertes
async def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                print(f"[TELEGRAM] ❌ Erreur API: {response.status_code} - {response.text}")
            else:
                print("[TELEGRAM] ✅ Message envoyé avec succès !")
    except Exception as e:
        print(f"[TELEGRAM] ❌ Erreur de connexion : {e}")

# Boucle principale de ton monitoring Solana
async def run_solana_monitor():
    print("=== [BOT] Initialisation du monitoring Solana ===")
    
    # ─── TEST D'ENVOI AU DÉMARRAGE ───
    print("[BOT] Envoi du message de test à Telegram...")
    await send_telegram_alert("🚀 *Le bot Solana est en ligne sur Railway !* \nLes alertes de tokens vont commencer.")
    # ─────────────────────────────────

    while True:
        try:
            # Insère ici ta logique de détection (Pump.fun / Axiom)
            # Exemple quand un token valide tes critères :
            # await send_telegram_alert(f"💎 *Nouveau Token Détecté !*\nAdresse: {token_address}")
            
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            print("=== [BOT] Arrêt du monitoring ===")
            break
        except Exception as e:
            print(f"[BOT] Erreur rencontrée : {e}")
            await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(run_solana_monitor())
    yield
    bot_task.cancel()
    await bot_task

app = FastAPI(lifespan=lifespan)

@app.get("/")
def read_root():
    return {"status": "online", "bot_running": True}
