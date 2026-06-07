async def pumpportal_listener():
    global TOTAL_EVENTS
    uri = "wss://pumpportal.fun/api/data"
    
    print("=== [BOT] Connexion au flux direct PumpPortal ===")
    await send_telegram_alert("⚡ <b>Flux STRETCH connecté (PumpPortal)</b> — Écoute active...")

    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10, max_size=10_000_000) as websocket:
                
                # ✅ Seulement subscribeNewToken — c'est suffisant
                await websocket.send(json.dumps({"method": "subscribeNewToken"}))
                print("[PUMPPORTAL] Flux connecté. Écoute active...")

                async for raw_message in websocket:
                    TOTAL_EVENTS += 1
                    
                    if isinstance(raw_message, bytes):
                        message_str = raw_message.decode('utf-8', errors='ignore')
                    else:
                        message_str = raw_message

                    try:
                        data = json.loads(message_str)
                    except json.JSONDecodeError:
                        continue

                    # ✅ Log brut pour confirmer que les données arrivent
                    print(f"[RAW] txType={data.get('txType')} mint={str(data.get('mint',''))[:12]} keys={list(data.keys())[:6]}", flush=True)

                    mint = data.get("mint")
                    if not mint:
                        continue

                    tx_type = data.get("txType")

                    # ✅ On analyse AUSSI les create (ils ont déjà un mcap initial)
                    if tx_type in ["create", "buy", "sell"]:
                        register_token(mint)
                        token_info = tracked_tokens[mint]

                        if token_info["alerted"]:
                            continue

                        mcap_sol = safe_float(data.get("marketCapSol"))
                        mcap_usd = mcap_sol * SOL_PRICE_USD

                        if mcap_usd == 0:
                            mcap_usd = safe_float(data.get("usdMarketCap"))

                        # ✅ Log pour voir les mcap reçus même hors zone
                        if mcap_usd > 0:
                            print(f"[MCAP] {mint[:12]} mcap={mcap_usd:,.0f}$ txType={tx_type}", flush=True)

                        if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
                            token_info["alerted"] = True
                            elapsed = time.time() - token_info["created_at"]
                            time_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{elapsed:.0f}s"

                            print(f"🔥 [DÉTECTION] Token {mint} -> {mcap_usd:,.0f}$")

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
            print(f"[PUMPPORTAL RETRY] Erreur de flux : {e}. Réexpédition dans 4s...")
            await asyncio.sleep(4)
