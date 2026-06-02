def analyser_et_alerter(trade_data: dict):
    """
    Analyse chaque transaction en temps réel pour traquer l'évolution du Market Cap
    """
    mint = trade_data.get("mint")
    if not mint or mint in alerted_tokens:
        return

    name = trade_data.get("name", "Unknown Token")
    symbol = trade_data.get("symbol", "MEME")
    
    # Récupération et calcul du Market Cap en USD
    mcap_sol = trade_data.get("marketCapSol", 0)
    mcap_usd = mcap_sol * 150  # Ajuste le prix du SOL si nécessaire

    # Ligne de log systématique pour voir que TOUT fonctionne en direct
    print(f"[STREAM] Token: {name} ({symbol}) | MCap actuel : {mcap_usd:,.0f}$")

    # Vérification de ta stratégie (Fenêtre autour des 10k$)
    if TARGET_MIN_MCAP <= mcap_usd <= TARGET_MAX_MCAP:
        print(f"[BOT] 🎯 CRITÈRE VALIDÉ : {name} entre dans la zone cible ! ({mcap_usd:,.0f}$)")
        
        # Ajout au set pour éviter les doublons d'alertes
        alerted_tokens.add(mint)
        
        # Calcul du potentiel restant
        multiplier_to_100k = 100000 / mcap_usd if mcap_usd > 0 else 10

        # Construction du message Telegram
        message = (
            f"🎯 *MEMECOIN EN PLEIN PUMP (CIBLE ~10k$)*\n\n"
            f"• *Nom :* {name} ({symbol})\n"
            f"• *Market Cap Détecté :* `{mcap_usd:,.0f}$`\n"
            f"• *Potentiel objectif (100k$) :* `x{multiplier_to_100k:.1f}` (+{multiplier_to_100k*100:.0f}%)\n\n"
            f"📈 *Liens de Sniping direct :*\n"
            f"• [Photon Solana](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
            f"• [BullX Terminal](https://bullx.io/terminal?chain=solana&address={mint})\n"
            f"• [Dexscreener](https://dexscreener.com/solana/{mint})\n\n"
            f"📥 *Adresse de contrat (CA) :*\n`{mint}`"
        )
        
        asyncio.create_task(send_telegram_alert(message))
