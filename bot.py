async def process_solana_transaction(result_data):
    """
    Analyse les données de transaction QuickNode en temps réel.
    Détecte les tokens dans la Stretch Zone AVANT la migration.
    """
    global TOTAL_TRANSACTIONS_PROCESSED, TOTAL_ALERTS
    TOTAL_TRANSACTIONS_PROCESSED += 1

    try:
        value = result_data.get("value", {})
        logs = value.get("logs", [])
        
        if not logs:
            return

        logs_str = "".join(logs)
        
        # On ne traite que les transactions d'achat ou de création sur Pump.fun
        if "Instruction: Buy" in logs_str or "Instruction: Create" in logs_str:
            # Récupération des informations sur les comptes modifiés (innerInstructions ou postTokenBalances)
            # QuickNode fournit les détails de la transaction au format jsonParsed
            transaction_info = value.get("transaction", {})
            meta = value.get("meta", {})
            
            if not meta:
                return

            # Étape A : Trouver le Mint (CA) du Token
            # On cherche un token dont le solde a bougé et qui finit par "pump"
            mint = None
            post_balances = meta.get("postTokenBalances", [])
            for balance in post_balances:
                token_address = balance.get("mint")
                if token_address and token_address.endswith("pump"):
                    mint = token_address
                    break
            
            if not mint or already_alerted(mint):
                return

            # Étape B : Estimer la Market Cap via la réserve de SOL de la bonding curve
            # Sur Pump.fun, la Bonding Curve commence à ~30k$ de MCAP et migre à ~69k$ (quand elle atteint 85 SOL).
            # Nous pouvons intercepter la progression en analysant les SOL envoyés au compte de la courbe.
            # Pour l'intégration exacte avec vos paliers (8k$ - 15k$), on vérifie le montant de SOL du trade :
            
            # Extraction basique du montant de SOL impliqué pour l'exemple
            # (Selon vos besoins, vous pouvez affiner en lisant le compte de la Bonding Curve)
            mcap_usd = 0.0
            
            # Simulation d'extraction de la mcap actuelle du token (ici calculée ou extraite)
            # Si le token correspond à vos critères de "Stretch" :
            if MIN_STRETCH_MCAP <= mcap_usd <= MAX_STRETCH_MCAP:
                mark_alerted(mint)
                TOTAL_ALERTS += 1
                
                # Configuration de l'alerte
                name = "Identifié via Stream"
                symbol = "PUMP"
                time_str = "Instantané (Stream)"
                
                logger.info(f"🔥 [DÉTECTION STREAM] {mint} en zone Stretch !")
                
                msg = (
                    f"⚡ <b>PAIRE EN EXPLOSION (STRETCH ZONE)</b> ⚡\n\n"
                    f"• <b>Token :</b> {name} <code>${symbol}</code>\n"
                    f"• <b>Market Cap :</b> <code>{mcap_usd:,.0f}$</code> 💰\n"
                    f"• <b>Mode :</b> <code>Temps Réel (QuickNode)</code> 🔥\n\n"
                    f"📊 <b>Outils :</b>\n"
                    f"• <a href='https://photon-sol.tinyastro.io/en/lp/{mint}'>Photon</a>\n"
                    f"• <a href='https://bullx.io/terminal?chain=solana&address={mint}'>BullX</a>\n\n"
                    f"📥 <b>CA :</b> <code>{mint}</code>"
                )
                asyncio.create_task(send_telegram_alert(msg))

    except Exception as e:
        logger.error(f"[PARSE ERROR] Erreur lors du décodage de la transaction : {e}")
