# APEX TRADING — Système de trading crypto automatisé

> Stack : Python 3.12 · ccxt · TimescaleDB · Mistral AI · Prometheus · Grafana · Telegram

---

## Architecture

```
apex_trading/
├── main.py                      # Point d'entrée — démarre le moteur
├── core/
│   ├── config.py                # Configuration centralisée (.env)
│   ├── engine.py                # Boucle principale de trading
│   ├── exchange_manager.py      # ccxt WebSocket + REST (Binance/Kraken)
│   ├── order_executor.py        # Passage d'ordres <50ms (paper / live)
│   ├── position_manager.py      # Suivi des positions + trailing stop
│   └── risk_manager.py          # Validation + calcul taille/levier/SL/TP
├── strategies/
│   └── signal_engine.py         # MM5/MM20/MM50 · RSI(21) · ATR · Mistral AI
├── data/
│   ├── db.py                    # TimescaleDB async (asyncpg)
│   └── backtest.py              # VectorBT sur 10 ans d'historique
├── monitoring/
│   ├── telegram_bot.py          # Alertes Telegram (file async)
│   └── prometheus_exporter.py   # Métriques Prometheus → Grafana
├── utils/
│   └── logger.py                # Logger structuré
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Démarrage rapide

### 1. Prérequis

```bash
# Ubuntu 22.04 / 24.04
sudo apt update && sudo apt install -y python3.12 python3.12-venv docker.io docker-compose-plugin
```

### 2. Configuration

```bash
git clone https://github.com/vous/apex-trading
cd apex-trading

cp .env.example .env
nano .env   # Remplissez vos clés API
```

### 3. Lancement via Docker (recommandé)

```bash
# Démarrage complet (TimescaleDB + moteur + Prometheus + Grafana)
docker compose up -d

# Logs en temps réel
docker compose logs -f trading-engine

# Arrêt propre (ferme les positions)
docker compose down
```

### 4. Lancement local (développement)

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Mode paper trading (sans capital réel)
MODE=paper python main.py
```

---

## Backtesting

```bash
# BTC/USDT sur 5 ans en timeframe 1h
python -m data.backtest --symbol BTC/USDT --timeframe 1h --years 5

# ETH/USDT sur 10 ans en timeframe 15min
python -m data.backtest --symbol ETH/USDT --timeframe 15m --years 10 --capital 50000
```

Le rapport HTML interactif VectorBT est généré dans le répertoire courant.

---

## Interfaces

| Service      | URL                        | Credentials            |
|--------------|----------------------------|------------------------|
| Grafana      | http://localhost:3000       | admin / (GRAFANA_PASSWORD) |
| Prometheus   | http://localhost:9090       | —                      |
| Métriques    | http://localhost:8000/metrics | —                    |

---

## Règles de trading

| Paramètre        | Valeur                        |
|------------------|-------------------------------|
| Stop-Loss        | ATR(21) × 1.5 (dynamique)    |
| TP1              | +2 % (fermeture 50 %)         |
| TP2              | +4 % (fermeture 50 % restant) |
| Trailing Stop    | Activé à +1 %, suit ATR × 2  |
| Taille position  | 1–5 % du capital              |
| Max positions    | 10 simultanées                |
| Levier           | x1–x5 (x1 si VIX > 25)       |
| Auto-close       | 4h sans TP/SL → fermeture     |
| Score Mistral    | > 70/100 requis               |

---

## Sécurité

- Démarrez **toujours** en mode `MODE=paper` avant le live
- Vérifiez les résultats de backtest (Sharpe > 2.0 requis)
- Les clés API Binance doivent avoir uniquement les droits **Trading** (pas de retrait)
- Activez la **whitelist IP** sur Binance/Kraken pour vos clés

---

## ⚠️ Avertissement

Ce système est fourni à titre éducatif.  
Le trading de cryptomonnaies comporte des risques importants de perte en capital.  
Testez toujours en paper trading avant d'engager des fonds réels.  
Les performances passées ne garantissent pas les performances futures.
