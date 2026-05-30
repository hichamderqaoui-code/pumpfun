# ══════════════════════════════════════════════════════════════
# RECEPTION DU FLUX ET INTEGRATION DANS LE CHRONO
# ══════════════════════════════════════════════════════════════

async def connect_pumpfun_websocket(session: aiohttp.ClientSession):
    while True:
        try:
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20) as ws:
                log.info("✅ Système v14.0 en ligne — Traitement strict des critères en cours.")
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                        if isinstance(data, dict) and data.get("txType") == "create":
                            mint = data.get("mint")
                            if mint not in monitored_tokens:
                                monitored_tokens[mint] = True
                                # On envoie le token en incubation pendant 10 minutes
                                asyncio.create_task(evaluate_token_after_delay(session, mint, data.get("symbol", "?"), data.get("name", "?")))
                    except Exception:
                        pass
        except Exception as e:
            log.error(f"Erreur WebSocket : {e}")
            await asyncio.sleep(3)

@app.on_event("startup")
async def startup_event():
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⚙️ *Mise à jour v14.0 déployée avec succès. Configuration : MCAP >= 6K$, Volume >= 6M$, Minimum 50 Holders. Traitement en cours.*")
    except Exception:
        pass
    asyncio.create_task(run_bot_logic())

async def run_bot_logic():
    async with aiohttp.ClientSession() as session:
        await connect_pumpfun_websocket(session)

@app.get("/")
async def root():
    return {"status": "online"}
