"""
Risk Manager — Squeeze Momentum Strategy
Integre le score de force du signal pour ajuster la taille de position.
"""

from typing import Optional, Tuple
from core.config import Config
from utils.logger import setup_logger

logger = setup_logger("risk_manager")


class RiskManager:

    def __init__(self, config: Config):
        self.cfg = config

    def validate_trade(
        self,
        symbol: str,
        signal: str,
        price: float,
        open_positions: int,
        daily_pnl: float,
        indicators: dict,
    ) -> Tuple[bool, str]:
        """Retourne (True, '') si le trade peut s'ouvrir."""

        if open_positions >= self.cfg.max_positions:
            return False, f"Max positions atteint ({self.cfg.max_positions})"

        loss_limit = self.cfg.capital * self.cfg.daily_loss_limit_pct
        if daily_pnl <= -loss_limit:
            return False, f"Perte journaliere limite ({daily_pnl:.0f}$)"

        # Verifier que le squeeze s'est bien relache
        if not indicators.get("squeeze_fired", False):
            return False, "Pas de squeeze release"

        if price <= 0:
            return False, "Prix invalide"

        vol     = indicators.get("volume_current", 0)
        vol_avg = indicators.get("volume_avg", 1)
        if vol_avg > 0 and vol < vol_avg * self.cfg.volume_multiplier:
            return False, f"Volume insuffisant ({vol:.0f} < {vol_avg * self.cfg.volume_multiplier:.0f})"

        return True, ""

    def compute_position(
        self,
        symbol: str,
        price: float,
        atr: float,
        signal: str,
        signal_strength: int = 3,
        vix: float = 0.0,
    ) -> dict:
        """
        Calcule taille, levier, SL et TP.

        signal_strength (0-6) ajuste la taille :
        - score 3-4 : taille standard (1-2% du capital)
        - score 5   : taille forte    (3-4%)
        - score 6   : taille max      (5%)
        """

        # ── Levier ────────────────────────────────────────────────────────────
        if vix > self.cfg.leverage_vix_threshold:
            leverage = 1
        else:
            atr_pct = atr / price if price > 0 else 0.01
            if atr_pct < 0.004:
                leverage = self.cfg.leverage_max
            elif atr_pct < 0.008:
                leverage = 3
            elif atr_pct < 0.015:
                leverage = 2
            else:
                leverage = 1
        leverage = min(leverage, self.cfg.leverage_max)

        # ── Taille selon force du signal ──────────────────────────────────────
        if signal_strength >= 6:
            size_pct = self.cfg.position_size_exceptional   # 5%
        elif signal_strength >= 5:
            size_pct = self.cfg.position_size_strong         # 4%
        else:
            size_pct = 0.02                                  # 2% standard

        notional = self.cfg.capital * size_pct
        # Clamp dans les limites
        notional = max(self.cfg.capital * self.cfg.position_size_min, notional)
        notional = min(self.cfg.capital * self.cfg.position_size_max, notional)
        quantity = notional / price if price > 0 else 0

        # ── SL : sous BB_mid - ATR × 1.3 ─────────────────────────────────────
        sl_distance = atr * self.cfg.stop_loss_atr_mult

        if signal == "buy":
            stop_loss = price - sl_distance
            tp1       = price * (1 + self.cfg.take_profit_1_pct)
            tp2       = price * (1 + self.cfg.take_profit_2_pct)
        else:
            stop_loss = price + sl_distance
            tp1       = price * (1 - self.cfg.take_profit_1_pct)
            tp2       = price * (1 - self.cfg.take_profit_2_pct)

        trailing_dist = atr * self.cfg.trailing_stop_atr_mult

        result = {
            "quantity":             round(quantity, 6),
            "notional":             round(notional, 2),
            "leverage":             leverage,
            "stop_loss":            round(stop_loss, 6),
            "tp1":                  round(tp1, 6),
            "tp2":                  round(tp2, 6),
            "trailing_trigger_pct": self.cfg.trailing_stop_trigger_pct,
            "trailing_distance":    round(trailing_dist, 6),
        }

        logger.debug(
            f"{symbol} [{signal.upper()}] score={signal_strength}/6 | "
            f"qty={result['quantity']} lev=x{leverage} | "
            f"SL={result['stop_loss']:.4f} TP1={result['tp1']:.4f} TP2={result['tp2']:.4f} | "
            f"notional={notional:.0f}$"
        )
        return result
