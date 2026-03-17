"""
Backtesting — VectorBT + Pandas sur 10 ans de données.
Usage : python -m data.backtest --symbol BTC/USDT --timeframe 1h --years 10

Stratégie testée :
  - MM5 > MM20 > MM50 (tendance)
  - RSI(21) < 30 OU > 50 + volume
  - SL ATR(21)×1.5 | TP1 +2% | TP2 +4%
  - Score Mistral simulé (distribution réaliste)
"""

import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import vectorbt as vbt
from utils.logger import setup_logger

logger = setup_logger("backtest")


def load_data(symbol: str, timeframe: str, years: int = 10) -> pd.DataFrame:
    """
    Charge les données historiques depuis ccxt (ou fichier CSV local).
    Pour un backtesting réel, utilisez des données Binance via ccxt.
    """
    try:
        import ccxt
        exchange = ccxt.binance({"enableRateLimit": True})
        since    = int((datetime.utcnow() - timedelta(days=years * 365)).timestamp() * 1000)
        all_ohlcv = []
        while True:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            if len(ohlcv) < 1000:
                break

        df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df.drop_duplicates(inplace=True)
        logger.info(f"Données chargées : {len(df)} bougies ({symbol} {timeframe})")
        return df

    except Exception as e:
        logger.error(f"Erreur chargement données : {e}")
        raise


def generate_signals(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Calcule tous les indicateurs et génère les signaux d'entrée/sortie.
    """
    # Moyennes mobiles
    df["mm5"]  = df["close"].rolling(cfg["mm_fast"]).mean()
    df["mm20"] = df["close"].rolling(cfg["mm_mid"]).mean()
    df["mm50"] = df["close"].rolling(cfg["mm_slow"]).mean()

    # RSI de Wilder
    delta  = df["close"].diff()
    gain   = delta.clip(lower=0).rolling(cfg["rsi_period"]).mean()
    loss   = (-delta.clip(upper=0)).rolling(cfg["rsi_period"]).mean()
    rs     = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)

    # ATR
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift()).abs()
    lcp = (df["low"]  - df["close"].shift()).abs()
    df["atr"] = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).rolling(cfg["atr_period"]).mean()

    # Volume moyen
    df["vol_avg"] = df["volume"].rolling(cfg["volume_avg_days"]).mean()

    # Simulation du score Mistral (distribution log-normale réaliste)
    np.random.seed(42)
    df["mistral_score"] = np.clip(
        np.random.normal(loc=65, scale=18, size=len(df)), 0, 100
    ).astype(int)

    # ── Signal d'entrée (LONG) ────────────────────────────────────────────────
    trend_up     = (df["mm5"] > df["mm20"]) & (df["mm20"] > df["mm50"])
    oversold     = df["rsi"] < 30
    momentum     = (df["rsi"] > 50) & (df["volume"] > df["vol_avg"] * cfg["volume_mult"])
    mistral_ok   = df["mistral_score"] > cfg["mistral_threshold"]

    df["entry"] = trend_up & (oversold | momentum) & mistral_ok

    # ── Signal de sortie ──────────────────────────────────────────────────────
    df["exit"] = (df["mm5"] < df["mm20"]) | (df["rsi"] > 70)

    # ── Stop-Loss dynamique (ATR × 1.5) ──────────────────────────────────────
    df["sl_price"] = df["close"] - df["atr"] * cfg["sl_atr_mult"]

    return df.dropna()


def run_backtest(df: pd.DataFrame, cfg: dict) -> vbt.Portfolio:
    """
    Exécute le backtest avec VectorBT.
    Simule le TP1 à +2% et le SL dynamique ATR.
    """
    entries = df["entry"]
    exits   = df["exit"]

    # Stop-loss en % (approximation du SL ATR)
    sl_pct = cfg["sl_atr_mult"] * df["atr"].mean() / df["close"].mean()
    tp_pct = cfg["tp1_pct"]

    portfolio = vbt.Portfolio.from_signals(
        close          = df["close"],
        entries        = entries,
        exits          = exits,
        sl_stop        = sl_pct,
        tp_stop        = tp_pct,
        size           = cfg["position_pct"],    # fraction du capital
        fees           = cfg["commission"],
        slippage       = 0.0005,                 # 0.05 % de slippage
        init_cash      = cfg["capital"],
        freq           = cfg["timeframe"],
    )
    return portfolio


def print_results(pf: vbt.Portfolio, symbol: str) -> None:
    stats = pf.stats()
    print("\n" + "=" * 55)
    print(f"  RÉSULTATS BACKTESTING — {symbol}")
    print("=" * 55)
    print(f"  Période           : {pf.wrapper.index[0].date()} → {pf.wrapper.index[-1].date()}")
    print(f"  Capital final     : {pf.final_value():.0f} $")
    print(f"  Rendement total   : {pf.total_return() * 100:.1f} %")
    print(f"  CAGR              : {stats.get('Annualized Return [%]', 0):.1f} %")
    print(f"  Sharpe Ratio      : {pf.sharpe_ratio():.2f}")
    print(f"  Sortino Ratio     : {pf.sortino_ratio():.2f}")
    print(f"  Max Drawdown      : {pf.max_drawdown() * 100:.1f} %")
    print(f"  Win Rate          : {pf.trades.win_rate() * 100:.1f} %")
    print(f"  Profit Factor     : {pf.trades.profit_factor():.2f}")
    print(f"  Trades totaux     : {pf.trades.count()}")
    print(f"  Durée moyenne     : {pf.trades.duration.mean()}")
    print("=" * 55)


def main():
    parser = argparse.ArgumentParser(description="Backtesting APEX TRADING")
    parser.add_argument("--symbol",    default="BTC/USDT", help="Paire de trading")
    parser.add_argument("--timeframe", default="1h",       help="Timeframe (1m, 5m, 1h...)")
    parser.add_argument("--years",     type=int, default=5, help="Années d'historique")
    parser.add_argument("--capital",   type=float, default=100_000, help="Capital initial USD")
    args = parser.parse_args()

    cfg = {
        "mm_fast":            5,
        "mm_mid":             20,
        "mm_slow":            50,
        "rsi_period":         21,
        "atr_period":         21,
        "volume_avg_days":    30,
        "volume_mult":        1.5,
        "mistral_threshold":  70,
        "sl_atr_mult":        1.5,
        "tp1_pct":            0.02,
        "tp2_pct":            0.04,
        "position_pct":       0.03,    # 3 % du capital par trade
        "commission":         0.001,   # 0.1 % Binance
        "capital":            args.capital,
        "timeframe":          args.timeframe,
    }

    logger.info(f"Démarrage backtest : {args.symbol} {args.timeframe} ({args.years} ans)")
    df       = load_data(args.symbol, args.timeframe, args.years)
    df       = generate_signals(df, cfg)
    portfolio = run_backtest(df, cfg)
    print_results(portfolio, args.symbol)

    # Sauvegarde du rapport HTML
    output_path = f"backtest_{args.symbol.replace('/', '_')}_{args.timeframe}.html"
    portfolio.plot().write_html(output_path)
    logger.info(f"Rapport VectorBT sauvegardé : {output_path}")


if __name__ == "__main__":
    main()
