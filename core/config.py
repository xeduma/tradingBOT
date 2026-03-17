"""
Configuration centrale — Squeeze Momentum Strategy
Chargee depuis les variables d'environnement (.env).
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:

    # ── Exchanges ─────────────────────────────────────────────────────────────
    exchanges: List[str] = field(default_factory=lambda: ["binance", "kraken"])
    binance_api_key: str = ""
    binance_api_secret: str = ""
    kraken_api_key: str = ""
    kraken_api_secret: str = ""

    # ── Timeframes ────────────────────────────────────────────────────────────
    timeframe: str = "10m"          # timeframe principal du signal
    timeframe_confirm: str = "4h"   # timeframe de confirmation

    # ── Paires surveillees ────────────────────────────────────────────────────
    symbols: List[str] = field(default_factory=lambda: [
        "BTC/USDT", "ETH/USDT", "SOL/USDT",
        "BNB/USDT", "XRP/USDT", "AVAX/USDT",
        "ADA/USDT", "DOT/USDT", "LINK/USDT",
    ])

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_period: int = 20             # periode BB (standard)
    bb_std: float = 2.0             # multiplicateur ecart-type

    # ── Keltner Channel ───────────────────────────────────────────────────────
    kc_period: int = 20             # periode KC (meme que BB pour comparaison)
    kc_atr_mult: float = 1.5        # multiplicateur ATR pour KC

    # ── Momentum ──────────────────────────────────────────────────────────────
    momentum_period: int = 12       # periode du calcul de momentum (ROC lisse)

    # ── RSI ───────────────────────────────────────────────────────────────────
    rsi_period: int = 14            # periode RSI
    rsi_buy_min: float = 50.0       # RSI min pour BUY (momentum actif)
    rsi_buy_max: float = 70.0       # RSI max pour BUY (pas en surachat)
    rsi_sell_min: float = 30.0      # RSI min pour SELL (pas en survente)
    rsi_sell_max: float = 50.0      # RSI max pour SELL (momentum baissier)

    # ── EMA ───────────────────────────────────────────────────────────────────
    ema_fast_squeeze: int = 50      # EMA rapide — contexte moyen terme
    ema_slow: int = 200             # EMA lente — tendance de fond

    # ── ATR ───────────────────────────────────────────────────────────────────
    atr_period: int = 14            # periode ATR

    # ── Volume ────────────────────────────────────────────────────────────────
    volume_avg_period: int = 20     # periode moyenne volume
    volume_multiplier: float = 1.2  # seuil volume = moy x 1.2

    # ── Capital et risque ─────────────────────────────────────────────────────
    capital: float = 100_000.0
    position_size_min: float = 0.01         # 1% du capital minimum
    position_size_max: float = 0.05         # 5% du capital maximum
    position_size_strong: float = 0.04      # 4% si signal fort (score >= 5)
    position_size_exceptional: float = 0.05 # 5% si signal exceptionnel (score 6)
    max_positions: int = 10
    daily_loss_limit_pct: float = 0.05      # stop si -5% sur la journee

    # ── Levier ────────────────────────────────────────────────────────────────
    leverage_default: int = 3
    leverage_max: int = 5
    leverage_vix_threshold: int = 25        # VIX > 25 -> levier x1

    # ── Stop-Loss ─────────────────────────────────────────────────────────────
    stop_loss_atr_mult: float = 1.3         # SL = BB_mid - ATR x 1.3

    # ── Take-Profit ───────────────────────────────────────────────────────────
    take_profit_1_pct: float = 0.02         # TP1 = +2%  (50% ferme)
    take_profit_2_pct: float = 0.045        # TP2 = +4.5% (30% ferme)
    trailing_stop_trigger_pct: float = 0.02 # trailing active apres +2%
    trailing_stop_atr_mult: float = 2.0     # trailing suit ATR x 2

    # ── Gestion du temps ──────────────────────────────────────────────────────
    auto_close_hours: int = 2               # fermeture auto apres 2h (squeeze court)

    # ── Mistral AI ────────────────────────────────────────────────────────────
    mistral_api_key: str = ""
    mistral_model: str = "mistral-large-latest"
    mistral_confidence_threshold: int = 70
    mistral_cache_seconds: int = 300
    mistral_enabled: bool = False           # desactive par defaut
    mistral_required: bool = False

    # ── Infrastructure ────────────────────────────────────────────────────────
    db_url: str = "postgresql://apex:apex@localhost:5432/trading"
    prometheus_port: int = 8000
    telegram_token: str = ""
    telegram_chat_id: str = ""
    mode: str = "paper"

    @classmethod
    def from_env(cls):
        cfg = cls()
        env_map = {
            # Exchanges
            "BINANCE_API_KEY":              ("binance_api_key",              str),
            "BINANCE_API_SECRET":           ("binance_api_secret",           str),
            "KRAKEN_API_KEY":               ("kraken_api_key",               str),
            "KRAKEN_API_SECRET":            ("kraken_api_secret",            str),
            # Mistral
            "MISTRAL_API_KEY":              ("mistral_api_key",              str),
            "MISTRAL_ENABLED":              ("mistral_enabled",  lambda x: x.lower() == "true"),
            "MISTRAL_REQUIRED":             ("mistral_required", lambda x: x.lower() == "true"),
            "MISTRAL_CONFIDENCE_THRESHOLD": ("mistral_confidence_threshold",  int),
            # Infra
            "TELEGRAM_TOKEN":               ("telegram_token",               str),
            "TELEGRAM_CHAT_ID":             ("telegram_chat_id",             str),
            "DB_URL":                       ("db_url",                       str),
            "CAPITAL":                      ("capital",                      float),
            "MAX_POSITIONS":                ("max_positions",                int),
            "MODE":                         ("mode",                         str),
            "PROMETHEUS_PORT":              ("prometheus_port",               int),
            # Timeframes
            "TIMEFRAME":                    ("timeframe",                    str),
            "TIMEFRAME_CONFIRM":            ("timeframe_confirm",            str),
            # Squeeze params (modifiables sans rebuild)
            "BB_PERIOD":                    ("bb_period",                    int),
            "BB_STD":                       ("bb_std",                       float),
            "KC_PERIOD":                    ("kc_period",                    int),
            "KC_ATR_MULT":                  ("kc_atr_mult",                  float),
            "MOMENTUM_PERIOD":              ("momentum_period",              int),
            "RSI_PERIOD":                   ("rsi_period",                   int),
            "RSI_BUY_MIN":                  ("rsi_buy_min",                  float),
            "RSI_BUY_MAX":                  ("rsi_buy_max",                  float),
            "LEVERAGE_DEFAULT":             ("leverage_default",              int),
            "LEVERAGE_MAX":                 ("leverage_max",                  int),
            "AUTO_CLOSE_HOURS":             ("auto_close_hours",              int),
            "TP1_PCT":                      ("take_profit_1_pct",            float),
            "TP2_PCT":                      ("take_profit_2_pct",            float),
        }
        for env_key, (attr, cast) in env_map.items():
            val = os.getenv(env_key)
            if val is not None:
                setattr(cfg, attr, cast(val))
        return cfg
