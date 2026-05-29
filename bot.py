# Exemple d'adaptation de la boucle principale avec l'API Axiom
AXIOM_API_URL = "https://api.axiom.trade/v1/stream" # URL de leur documentation API
AXIOM_API_KEY = os.environ["AXIOM_API_KEY"]

async def connect_axiom_stream(session: aiohttp.ClientSession):
    headers = {"Authorization": f"Bearer {AXIOM_API_KEY}"}
    while True:
        try:
            # On se connecte directement au flux de données d'Axiom
            async with websockets.connect(AXIOM_API_URL, extra_headers=headers) as ws:
                log.info("✅ Connecté au flux Ultra-Rapide d'Axiom Pro !")
                
                # On peut parfois leur envoyer des filtres directement à la souscription 
                # pour que leur serveur travaille à notre place !
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "channel": "token_metrics_live"
                }))
                
                async for raw_msg in ws:
                    data = json.loads(raw_msg)
                    
                    # Ici, la liquidité et le Top 10 sont fournis DIRECTEMENT dans le message
                    liquidity = float(data.get("liquidityUsd", 0))
                    top10_pct = float(data.get("top10Percentage", 100))
                    
                    if liquidity >= 10000 and top10_pct <= 29:
                        # Alerte instantanée !
                        asyncio.create_task(send_telegram_alert(data))
        except Exception as e:
            log.error(f"Déconnexion flux Axiom : {e}")
            await asyncio.sleep(2)
