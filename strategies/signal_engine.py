"""
Signal Engine — Squeeze Momentum
Strategie : Bollinger Bands + Keltner Channel + RSI + EMA + Volume
Timeframe principal : 10min
Confirmation       : 4h
"""

import json
import time
from typing import Dict, List, Optional, Tuple

import httpx
import numpy as np

from core.config import Config
from utils.logger import setup_logger

logger = setup_logger("signal_engine")


class SignalEngine:
    """
    Strategie Squeeze Momentum

    PRINCIPE
    --------
    Le "squeeze" se produit quand les Bollinger Bands (BB) sont a l'interieur
    du Keltner Channel (KC). Cela indique une compression de volatilite —
    une explosion de prix est imminente.

    Quand les BB sortent du KC (squeeze release), on entre dans la direction
    du momentum (indicateur de momentum = ROC lisse).

    ENTREE BUY (toutes conditions requises)
    ----------------------------------------
    1. Squeeze vient de se relacher : BB sort du KC vers le haut
    2. Momentum(12) passe de negatif a positif ET croissant
    3. RSI(14) entre 50 et 70 (momentum actif, pas en surachat)
    4. Prix > EMA50 ET EMA200 (contexte haussier)
    5. Volume > moyenne(20) x 1.2
    6. [4h] Prix > EMA50 4h ET RSI(14) 4h > 50

    SORTIE
    ------
    - TP1 : +2%  (50% ferme)
    - TP2 : +4.5% (30% ferme)
    - TP3 : trailing ATR x 2 (20% restant)
    - SL  : sous BB mediane - ATR(14) x 1.3
    - Auto-close : 2h
    """

    def __init__(self, config: Config):
        self.cfg = config
        self._mistral_cache: Dict[str, tuple] = {}
        self._candles_4h: Dict[str, List[dict]] = {}
        self._http_client: Optional[httpx.AsyncClient] = None

    # ─── Interface principale ────────────────────────────────────────────────

    def compute_indicators(self, candles: List[dict]) -> dict:
        """
        Calcule tous les indicateurs sur le timeframe principal (10min).
        Retourne un dict vide si pas assez de donnees.
        """
        min_candles = max(
            self.cfg.bb_period,
            self.cfg.kc_period,
            self.cfg.ema_slow,
            self.cfg.momentum_period + 2,
        ) + 10

        if len(candles) < min_candles:
            return {}

        closes  = np.array([c["close"]  for c in candles], dtype=np.float64)
        highs   = np.array([c["high"]   for c in candles], dtype=np.float64)
        lows    = np.array([c["low"]    for c in candles], dtype=np.float64)
        volumes = np.array([c["volume"] for c in candles], dtype=np.float64)

        # Moyennes mobiles
        ema50  = self._ema(closes, self.cfg.ema_fast_squeeze)
        ema200 = self._ema(closes, self.cfg.ema_slow)

        # Bollinger Bands
        bb_mid, bb_upper, bb_lower = self._bollinger(closes, self.cfg.bb_period, self.cfg.bb_std)

        # Keltner Channel
        kc_mid, kc_upper, kc_lower = self._keltner(
            highs, lows, closes, self.cfg.kc_period, self.cfg.kc_atr_mult
        )

        # Squeeze detection
        squeeze_on, squeeze_fired = self._detect_squeeze(
            bb_upper, bb_lower, kc_upper, kc_lower
        )

        # Momentum (ROC lisse)
        momentum_val, momentum_prev = self._momentum(closes, self.cfg.momentum_period)

        # RSI
        rsi_val = self._rsi(closes, self.cfg.rsi_period)

        # ATR
        atr_val = self._atr(highs, lows, closes, self.cfg.atr_period)

        # Volume
        vol_avg = float(np.mean(volumes[-self.cfg.volume_avg_period:]))

        return {
            "close":          float(closes[-1]),
            "ema50":          float(ema50),
            "ema200":         float(ema200),
            "bb_mid":         float(bb_mid),
            "bb_upper":       float(bb_upper),
            "bb_lower":       float(bb_lower),
            "kc_mid":         float(kc_mid),
            "kc_upper":       float(kc_upper),
            "kc_lower":       float(kc_lower),
            "squeeze_on":     squeeze_on,
            "squeeze_fired":  squeeze_fired,
            "momentum":       float(momentum_val),
            "momentum_prev":  float(momentum_prev),
            "rsi":            float(rsi_val),
            "atr":            float(atr_val),
            "volume_current": float(volumes[-1]),
            "volume_avg":     float(vol_avg),
        }

    def compute_indicators_4h(self, candles_4h: List[dict]) -> dict:
        """
        Calcule les indicateurs de confirmation sur le timeframe 4h.
        """
        if len(candles_4h) < self.cfg.ema_fast_squeeze + 5:
            return {}

        closes = np.array([c["close"] for c in candles_4h], dtype=np.float64)
        highs  = np.array([c["high"]  for c in candles_4h], dtype=np.float64)
        lows   = np.array([c["low"]   for c in candles_4h], dtype=np.float64)

        return {
            "close":   float(closes[-1]),
            "ema50":   float(self._ema(closes, self.cfg.ema_fast_squeeze)),
            "ema200":  float(self._ema(closes, self.cfg.ema_slow)),
            "rsi":     float(self._rsi(closes, self.cfg.rsi_period)),
            "bb_mid":  float(self._bollinger(closes, self.cfg.bb_period, self.cfg.bb_std)[0]),
        }

    def store_candles_4h(self, symbol: str, candles: List[dict]) -> None:
        """Stocke les bougies 4h pour la confirmation multi-timeframe."""
        self._candles_4h[symbol] = candles

    def generate_signal(self, indicators: dict, symbol: str = "") -> Optional[str]:
        """
        Genere un signal BUY / SELL / None selon la strategie Squeeze Momentum.
        """
        if not indicators:
            return None

        price    = indicators["close"]
        ema50    = indicators["ema50"]
        ema200   = indicators["ema200"]
        rsi      = indicators["rsi"]
        mom      = indicators["momentum"]
        mom_prev = indicators["momentum_prev"]
        vol      = indicators["volume_current"]
        vol_avg  = indicators["volume_avg"]
        sq_fired = indicators["squeeze_fired"]

        # ── SIGNAL BUY ────────────────────────────────────────────────────────

        # Condition 1 : squeeze vient de se relacher (hausse)
        cond1_buy = sq_fired and mom > 0

        # Condition 2 : momentum passe de negatif a positif ET croissant
        cond2_buy = mom > 0 and mom_prev <= 0 and mom > mom_prev

        # Condition 3 : RSI dans la zone de momentum actif (50-70)
        cond3_buy = self.cfg.rsi_buy_min <= rsi <= self.cfg.rsi_buy_max

        # Condition 4 : contexte haussier (prix > EMA50 et EMA200)
        cond4_buy = price > ema50 and price > ema200

        # Condition 5 : volume confirme
        cond5_buy = vol > vol_avg * self.cfg.volume_multiplier

        # Confirmation 4h
        conf_4h = self._check_4h_confirmation(symbol, direction="buy")

        if cond1_buy and cond2_buy and cond3_buy and cond4_buy and cond5_buy and conf_4h:
            score = sum([cond1_buy, cond2_buy, cond3_buy, cond4_buy, cond5_buy, conf_4h])
            logger.info(
                f"[SQUEEZE BUY] {symbol} | score={score}/6 | "
                f"RSI={rsi:.1f} | Mom={mom:.4f} | Vol={vol:.0f}/{vol_avg:.0f} | "
                f"SqFired={sq_fired}"
            )
            return "buy"

        # ── SIGNAL SELL (SHORT) ───────────────────────────────────────────────

        cond1_sell = sq_fired and mom < 0
        cond2_sell = mom < 0 and mom_prev >= 0 and mom < mom_prev
        cond3_sell = self.cfg.rsi_sell_min <= rsi <= self.cfg.rsi_sell_max
        cond4_sell = price < ema50 and price < ema200
        cond5_sell = vol > vol_avg * self.cfg.volume_multiplier
        conf_4h_sell = self._check_4h_confirmation(symbol, direction="sell")

        if cond1_sell and cond2_sell and cond3_sell and cond4_sell and cond5_sell and conf_4h_sell:
            logger.info(
                f"[SQUEEZE SELL] {symbol} | "
                f"RSI={rsi:.1f} | Mom={mom:.4f}"
            )
            return "sell"

        return None

    def get_signal_strength(self, indicators: dict, symbol: str = "") -> int:
        """
        Retourne un score de force du signal (0-6).
        Utile pour ajuster la taille de position.
        """
        if not indicators:
            return 0

        price    = indicators["close"]
        rsi      = indicators["rsi"]
        mom      = indicators["momentum"]
        mom_prev = indicators["momentum_prev"]
        vol      = indicators["volume_current"]
        vol_avg  = indicators["volume_avg"]
        sq_fired = indicators["squeeze_fired"]

        score = 0
        if sq_fired and mom > 0:
            score += 1
        if mom > 0 and mom_prev <= 0:
            score += 1
        if self.cfg.rsi_buy_min <= rsi <= self.cfg.rsi_buy_max:
            score += 1
        if price > indicators["ema50"] and price > indicators["ema200"]:
            score += 1
        if vol > vol_avg * self.cfg.volume_multiplier:
            score += 1
        if self._check_4h_confirmation(symbol, "buy"):
            score += 1

        return score

    # ─── Confirmation 4h ─────────────────────────────────────────────────────

    def _check_4h_confirmation(self, symbol: str, direction: str) -> bool:
        """
        Verifie la tendance sur le timeframe 4h.
        Retourne True si la confirmation est favorable.
        Retourne True par defaut si pas de donnees 4h (non-bloquant).
        """
        candles_4h = self._candles_4h.get(symbol)
        if not candles_4h or len(candles_4h) < self.cfg.ema_fast_squeeze + 5:
            return True  # fail-open : pas de donnees 4h = on ne bloque pas

        ind4h = self.compute_indicators_4h(candles_4h)
        if not ind4h:
            return True

        if direction == "buy":
            price_ok = ind4h["close"] > ind4h["ema50"]
            rsi_ok   = ind4h["rsi"] > 50
            return price_ok and rsi_ok
        else:
            price_ok = ind4h["close"] < ind4h["ema50"]
            rsi_ok   = ind4h["rsi"] < 50
            return price_ok and rsi_ok

    # ─── Mistral AI ──────────────────────────────────────────────────────────

    async def get_mistral_score(self, symbol: str, exporter=None) -> int:
        """
        Score Mistral 0-100. Retourne 100 si desactive ou en erreur (fail-open).
        """
        if not self.cfg.mistral_enabled:
            return 100

        cached = self._mistral_cache.get(symbol)
        if cached:
            score, ts = cached
            if time.time() - ts < self.cfg.mistral_cache_seconds:
                if exporter:
                    exporter.update_mistral_score(symbol, score)
                return score

        if not self.cfg.mistral_api_key:
            return 100

        prompt = (
            f"Analyse crypto {symbol} — actualites, sentiment, momentum.\n"
            f"Reponds UNIQUEMENT en JSON : "
            f'{{ "score": <0-100>, "sentiment": "BULLISH|BEARISH|NEUTRAL", '
            f'"resume": "<15 mots max>" }}\n'
            f"0=tres baissier, 50=neutre, 100=tres haussier."
        )

        try:
            if not self._http_client:
                self._http_client = httpx.AsyncClient(timeout=8.0)

            resp = await self._http_client.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.cfg.mistral_api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       self.cfg.mistral_model,
                    "max_tokens":  100,
                    "temperature": 0.1,
                    "messages":    [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            result  = json.loads(content)
            score   = max(0, min(100, int(result.get("score", 50))))

            self._mistral_cache[symbol] = (score, time.time())
            if exporter:
                exporter.update_mistral_score(symbol, score)

            logger.info(
                f"Mistral [{symbol}] score={score}/100 "
                f"sentiment={result.get('sentiment')} "
                f"resume='{result.get('resume', '')}'"
            )
            return score

        except Exception as e:
            logger.error(f"Erreur Mistral {symbol} : {e}")
            return 100  # fail-open

    # ─── Indicateurs mathematiques ───────────────────────────────────────────

    @staticmethod
    def _ema(closes: np.ndarray, period: int) -> float:
        """Exponential Moving Average."""
        if len(closes) < period:
            return float(closes[-1])
        k   = 2.0 / (period + 1)
        ema = float(np.mean(closes[:period]))
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    @staticmethod
    def _sma(closes: np.ndarray, period: int) -> float:
        if len(closes) < period:
            return float(closes[-1])
        return float(np.mean(closes[-period:]))

    @classmethod
    def _bollinger(
        cls, closes: np.ndarray, period: int, std_mult: float
    ) -> Tuple[float, float, float]:
        """Bollinger Bands : (mid, upper, lower)."""
        if len(closes) < period:
            c = float(closes[-1])
            return c, c, c
        window = closes[-period:]
        mid    = float(np.mean(window))
        std    = float(np.std(window, ddof=1))
        return mid, mid + std_mult * std, mid - std_mult * std

    @classmethod
    def _keltner(
        cls,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int,
        atr_mult: float,
    ) -> Tuple[float, float, float]:
        """Keltner Channel : (mid EMA, upper, lower)."""
        if len(closes) < period + 1:
            c = float(closes[-1])
            return c, c, c
        mid = cls._ema(closes, period)
        atr = cls._atr(highs, lows, closes, period)
        return mid, mid + atr_mult * atr, mid - atr_mult * atr

    @staticmethod
    def _detect_squeeze(
        bb_upper: float,
        bb_lower: float,
        kc_upper: float,
        kc_lower: float,
    ) -> Tuple[bool, bool]:
        """
        squeeze_on   = BB est a l'interieur du KC (compression).
        squeeze_fired = BB vient de sortir du KC (explosion imminente).
        """
        squeeze_on   = bb_upper < kc_upper and bb_lower > kc_lower
        squeeze_fired = bb_upper >= kc_upper or bb_lower <= kc_lower
        return squeeze_on, squeeze_fired

    @staticmethod
    def _momentum(closes: np.ndarray, period: int) -> Tuple[float, float]:
        """
        Momentum = close - moyenne(close il y a N periodes).
        Retourne (valeur actuelle, valeur precedente).
        """
        if len(closes) < period + 2:
            return 0.0, 0.0
        # Valeur actuelle : close[-1] - mean(closes[-period-1:-1])
        mom_now  = float(closes[-1] - np.mean(closes[-period - 1:-1]))
        # Valeur precedente : close[-2] - mean(closes[-period-2:-2])
        mom_prev = float(closes[-2] - np.mean(closes[-period - 2:-2]))
        return mom_now, mom_prev

    @staticmethod
    def _rsi(closes: np.ndarray, period: int = 14) -> float:
        """RSI de Wilder."""
        if len(closes) < period + 1:
            return 50.0
        deltas   = np.diff(closes[-(period + 1):])
        gains    = np.where(deltas > 0, deltas, 0.0)
        losses   = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        return float(100 - 100 / (1 + avg_gain / avg_loss))

    @staticmethod
    def _atr(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int = 14,
    ) -> float:
        """Average True Range."""
        if len(closes) < period + 1:
            return float(highs[-1] - lows[-1])
        prev   = closes[-(period + 1):-1]
        h      = highs[-period:]
        l      = lows[-period:]
        tr     = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
        return float(np.mean(tr))
