# 🎯 Solana Pump.fun Sniper Bot
> Entrée < $30K mcap | Sortie > $100K | Avant migration Raydium

## 🏗️ Architecture

```
Pump.fun WebSocket → Filtres → Score Signal → Telegram Alert
     ↓
Helius API (holders on-chain)
DexScreener API (mcap, volume, liquidité)
RugCheck API (sécurité token)
```

## ⚡ Setup Rapide (15 min)

### 1. Créer le bot Telegram
- Ouvre [@BotFather](https://t.me/BotFather) → `/newbot`
- Copie le **token**
- Crée un canal/groupe privé → ajoute le bot comme admin
- Récupère le **chat_id** : envoie un message puis visite  
  `https://api.telegram.org/bot<TOKEN>/getUpdates`

### 2. Clé Helius
- Inscris-toi sur [helius.dev](https://helius.dev)
- Free tier = **1M requêtes/mois** (largement suffisant)
- Copie ta clé API

### 3. Déployer sur Railway
```bash
# Clone le repo (après git push)
railway login
railway init
railway up

# Ajouter les variables dans Dashboard Railway :
# TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, HELIUS_API_KEY
```

### 4. GitHub → Railway auto-deploy
Dans Railway : Settings → Source → Connect GitHub repo  
Chaque `git push main` redéploie automatiquement.

---

## 📊 Filtres Actifs

| Filtre | Valeur | Rôle |
|--------|--------|------|
| MCap entrée | $5K – $30K | Entrer tôt avant x10 |
| Age token | < 10 min | Seulement les ultra-frais |
| Liquidité | > 10 SOL | Éviter les tokens sans fond |
| Volume 5min | > $2K | Preuve d'intérêt réel |
| Ratio B/S | > 1.5x | Plus d'acheteurs que vendeurs |
| Holders | > 20 wallets | Distribution minimale |
| Top 10 hold | < 60% | Pas de concentration extrême |
| RugCheck | > 70/100 | Sécurité anti-rug |
| Score signal | > 55/100 | Qualité globale du signal |

## 🎯 Logique de Scoring (0-100)

- **Momentum** (30 pts) : ratio B/S, volume 5min
- **MCap optimal** (25 pts) : zone $8K-$15K = max points
- **Liquidité** (15 pts) : > 30 SOL = full points
- **Distribution** (15 pts) : top10 < 40% = full points
- **RugCheck** (15 pts) : score > 85 = full points

## ⚠️ Seuils Migration Raydium

- Migration automatique à **~$69K** mcap (bonding curve à 85 SOL)
- **Entrée** : $8K-$30K mcap
- **TP1** : $50K mcap (avant migration)
- **TP2** : $100K+ mcap (après migration Raydium)
- **Stop-loss** : -40% depuis l'entrée

## 🔧 Modifier les filtres

Dans Railway Dashboard → Variables, tu peux changer :
- `MAX_MCAP_ENTRY` : augmenter pour être moins restrictif
- `RUGCHECK_MIN_SCORE` : baisser en 50 pour voir plus de tokens
- `MIN_VOLUME_5MIN_USD` : baisser en 1000 si trop peu d'alertes

---
⚠️ **DYOR — Not financial advice**
