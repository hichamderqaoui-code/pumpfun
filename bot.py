        log.info(f" Alerte Telegram envoyée pour {clean_symbol} (MCap: ${mcap:,.0f})")
    except Exception as e:
        log.error(f"❌ Erreur lors de l'envoi Telegram pour {mint}: {e}")

# ══════════════════════════════════════════════════════════════
# ANALYSE ET FILTRAGE DU FLUX DE TRADES EN DIRECT
# ══════════════════════════════════════════════════════════════

async def process_trade_event(event: dict):
    """
    Analyse chaque transaction en direct sur Pump.fun.
    Calcule le volume cumulé, le nombre de trades et vérifie le Market Cap.
    """
    try:
        mint = event.get("mint")
        if not mint or mint in alerted_tokens:
            return

        # Extraction des données de prix/sol du trade
        sol_amount = float(event.get("solAmount", 0)) / 1e9  # Converti de Lamports en SOL
        # Approximation du prix du SOL à ~150$ pour un calcul rapide du volume en USD (ajustable)
        trade_vol_usd = sol_amount * 150.0 

        # Simulation du Market Cap basé sur le marketCapSol fourni par l'API
        mcap_sol = float(event.get("marketCapSol", 0))
        mcap_usd = mcap_sol * 150.0

        # Initialisation ou mise à jour des stats du token
        if mint not in token_stats:
            token_stats[mint] = {
                "symbol": event.get("symbol", "UNKNOWN"),
                "name": event.get("name", "Unknown Token"),
                "total_vol": 0.0,
                "tx_count": 0
            }

        stats = token_stats[mint]
        stats["total_vol"] += trade_vol_usd
        stats["tx_count"] += 1

        # Vérification des critères de déclenchement (10K+ Market Cap & volume organique)
        if mcap_usd >= TARGET_MCAP_PUMP and stats["tx_count"] >= MIN_TRADES_COUNT:
            alerted_tokens.add(mint)
            asyncio.create_task(
                send_insider_alert(
                    mint=mint,
                    symbol=stats["symbol"],
                    name=stats["name"],
                    mcap=mcap_usd,
                    total_vol=stats["total_vol"],
                    total_txs=stats["tx_count"]
                )
            )
            # Nettoyage de la mémoire vive pour ce token
            token_stats.pop(mint, None)

    except Exception as e:
        log.error(f"❌ Erreur lors du traitement du trade event : {e}")

# ══════════════════════════════════════════════════════════════
# CONNEXION WEBSOCKET ET GESTION DU FLUX (PUMP PORTAL)
# ══════════════════════════════════════════════════════════════

async def monitor_pump_fun():
    """
    Se connecte au WebSocket de Pump Portal, s'abonne aux trades
    et gère les reconnexions automatiques.
    """
    while True:
        try:
            log.info(f" Connexion au WebSocket Pump Portal : {PUMPFUN_WS_PRIMARY}")
            async with websockets.connect(PUMPFUN_WS_PRIMARY, ping_interval=20, ping_timeout=10) as ws:
                
                # Abonnement au flux de tous les trades de la plateforme
                subscribe_payload = {
                    "method": "subscribeAllTokenTrades"
                }
                await ws.send(json.dumps(subscribe_payload))
                log.info(" Abonnés avec succès au flux global des trades Pump.fun.")

                async for message in ws:
                    data = json.loads(message)
                    
                    # Ignorer les messages de confirmation d'abonnement
                    if "message" in data and "subscribed" in data.get("message", ""):
                        continue
                        
                    await process_trade_event(data)

        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"⚠️ Connexion WebSocket perdue ({e}). Reconnexion dans 5 secondes...")
            await asyncio.sleep(5)
        except Exception as e:
            log.error(f"❌ Erreur critique dans la boucle principale : {e}. Nouvelle tentative...")
            await asyncio.sleep(5)

# ══════════════════════════════════════════════════════════════
# CYCLE DE VIE FASTAPI (LANCEMENT SUR RAILWAY)
# ══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """Déclenche le bot en tâche de fond dès que Railway démarre Uvicorn."""
    log.info("🚀 Lancement du Worker Pump.fun Sniper Bot en tâche de fond...")
    asyncio.create_task(monitor_pump_fun())

@app.get("/")
async def root():
    """Route de Healthcheck pour que Railway sache que le conteneur est vivant."""
    return {
        "status": "online",
        "bot": "Solana Pump.fun Sniper v17.1",
        "tracked_tokens_in_memory": len(token_stats),
        "alerts_sent_session": len(alerted_tokens)
    }
