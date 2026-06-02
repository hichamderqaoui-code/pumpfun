import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager

# 1. Définir la fonction de ton bot qui tourne en boucle
async def run_solana_monitor():
    print("=== [BOT] Initialisation du monitoring Solana ===")
    # Insère ici la logique de ton bot (écoute des paires, filtres liquidité/volume, etc.)
    while True:
        try:
            # Exemple de boucle d'écoute
            # print("[BOT] Recherche de nouveaux tokens sur Pump.fun...")
            await asyncio.sleep(1) # Ne pas supprimer pour éviter de saturer le CPU
        except asyncio.CancelledError:
            print("=== [BOT] Arrêt du monitoring ===")
            break
        except Exception as e:
            print(f"[BOT] Erreur rencontrée : {e}")
            await asyncio.sleep(5) # Pause avant reconnexon en cas de crash

# 2. Configurer le gestionnaire de cycle de vie (lifespan)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tout ce qui est écrit ici s'exécute au DÉMARRAGE du conteneur
    bot_task = asyncio.create_task(run_solana_monitor())
    
    yield  # Le serveur Uvicorn tourne pendant ce temps
    
    # Tout ce qui est écrit ici s'exécute à l'ARRÊT du conteneur
    bot_task.cancel()
    await bot_task

# 3. Initialiser FastAPI avec le lifespan
app = FastAPI(lifespan=lifespan)

@app.get("/")
def read_root():
    return {"status": "online", "bot_running": True}
