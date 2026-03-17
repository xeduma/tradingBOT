"""
Gestionnaire de positions — état en mémoire des trades ouverts.
Calcule le trailing stop et les conditions de sortie en temps réel.
"""

import time
from typing import Dict, List, Optional

from core.config import Config
from utils.logger import setup_logger

logger = setup_logger("position_manager")


class PositionManager:
    """
    Conserve l'état de toutes les positions ouvertes en RAM.
    Persiste aussi en base via Database (appelé depuis engine.py).

    Structure d'une position :
    {
        "symbol":            "BTC/USDC",
        "side":              "buy" | "sell",
        "entry_price":       67420.0,
        "quantity":          0.5,
        "leverage":          3,
        "stop_loss":         65450.0,
        "tp1":               68140.0,
        "tp2":               69600.0,
        "tp1_hit":           False,
        "trailing_trigger":  0.01,        # +1 %
        "trailing_distance": 340.0,       # ATR×2 en $
        "trailing_active":   False,
        "trailing_stop":     None,
        "open_time":         1714000000,  # timestamp UNIX
        "auto_close_hours":  4,
    }
    """

    def __init__(self, config: Config) -> None:
        self.cfg       = config
        self.positions: Dict[str, dict] = {}

    # ─── CRUD positions ───────────────────────────────────────────────────────

    def add_position(self, symbol: str, order: dict, indicators: dict) -> None:
        self.positions[symbol] = {
            "symbol":            symbol,
            "side":              order["side"],
            "entry_price":       float(order.get("average") or order.get("price", 0)),
            "quantity":          float(order.get("amount", 0)),
            "leverage":          order.get("leverage", 1),
            "stop_loss":         order["stop_loss"],
            "tp1":               order["tp1"],
            "tp2":               order["tp2"],
            "tp1_hit":           False,
            "trailing_trigger":  order.get("trailing_trigger_pct", 0.01),
            "trailing_distance": order.get("trailing_distance", 0),
            "trailing_active":   False,
            "trailing_stop":     None,
            "open_time":         time.time(),
            "auto_close_hours":  self.cfg.auto_close_hours,
            "order_id":          order.get("id"),
        }
        logger.info(f"Position ouverte : {symbol} [{order['side'].upper()}] "
                    f"@ {self.positions[symbol]['entry_price']:.4f} x{self.positions[symbol]['leverage']}")

    def get_position(self, symbol: str) -> Optional[dict]:
        return self.positions.get(symbol)

    def remove_position(self, symbol: str) -> None:
        self.positions.pop(symbol, None)

    def get_open_positions(self) -> List[dict]:
        return list(self.positions.values())

    def count_open(self) -> int:
        return len(self.positions)

    # ─── Mise à jour temps réel ───────────────────────────────────────────────

    def update_position(
        self, symbol: str, current_price: float, indicators: dict
    ) -> Optional[str]:
        """
        Vérifie toutes les conditions de sortie pour une position.
        Retourne l'action déclenchée : "TP1" | "TP2" | "SL" | "TRAILING" | "AUTO_CLOSE" | None
        """
        pos = self.positions.get(symbol)
        if not pos:
            return None

        side         = pos["side"]
        entry        = pos["entry_price"]
        current_pct  = (current_price - entry) / entry

        # ── Auto-close (4h) ───────────────────────────────────────────────────
        hours_open = (time.time() - pos["open_time"]) / 3600
        if hours_open >= pos["auto_close_hours"]:
            logger.info(f"{symbol} — AUTO_CLOSE ({hours_open:.1f}h)")
            return "AUTO_CLOSE"

        if side == "buy":
            unrealized_pct = (current_price - entry) / entry

            # Trailing stop : activation
            if not pos["trailing_active"] and unrealized_pct >= pos["trailing_trigger"]:
                pos["trailing_active"] = True
                pos["trailing_stop"]   = current_price - pos["trailing_distance"]
                logger.info(f"{symbol} — Trailing stop activé @ {pos['trailing_stop']:.4f}")

            # Trailing stop : mise à jour
            if pos["trailing_active"]:
                new_ts = current_price - pos["trailing_distance"]
                if new_ts > (pos["trailing_stop"] or 0):
                    pos["trailing_stop"] = new_ts

            # Vérification TP2
            if current_price >= pos["tp2"]:
                logger.info(f"{symbol} — TP2 atteint @ {current_price:.4f}")
                return "TP2"

            # Vérification TP1 (première cible, fermeture partielle — logique simplifiée ici)
            if not pos["tp1_hit"] and current_price >= pos["tp1"]:
                pos["tp1_hit"] = True
                logger.info(f"{symbol} — TP1 atteint @ {current_price:.4f} (50 % fermé)")
                return "TP1"

            # Stop-loss dynamique
            if current_price <= pos["stop_loss"]:
                logger.info(f"{symbol} — SL déclenché @ {current_price:.4f}")
                return "SL"

            # Trailing stop déclenché
            if pos["trailing_active"] and current_price <= pos["trailing_stop"]:
                logger.info(f"{symbol} — TRAILING STOP @ {current_price:.4f}")
                return "TRAILING"

            # Conditions de sortie techniques
            rsi = indicators.get("rsi", 50)
            mm5 = indicators.get("mm5", 1)
            mm20 = indicators.get("mm20", 1)
            if mm5 < mm20 or rsi > 70:
                logger.info(f"{symbol} — Signal de vente technique (MM5<MM20 ou RSI>{rsi:.0f})")
                return "SIGNAL_EXIT"

        else:  # short
            unrealized_pct = (entry - current_price) / entry

            if not pos["trailing_active"] and unrealized_pct >= pos["trailing_trigger"]:
                pos["trailing_active"] = True
                pos["trailing_stop"]   = current_price + pos["trailing_distance"]

            if pos["trailing_active"]:
                new_ts = current_price + pos["trailing_distance"]
                if new_ts < (pos["trailing_stop"] or float("inf")):
                    pos["trailing_stop"] = new_ts

            if current_price <= pos["tp2"]:
                return "TP2"
            if not pos["tp1_hit"] and current_price <= pos["tp1"]:
                pos["tp1_hit"] = True
                return "TP1"
            if current_price >= pos["stop_loss"]:
                return "SL"
            if pos["trailing_active"] and current_price >= pos["trailing_stop"]:
                return "TRAILING"

        return None

    # ─── PnL ─────────────────────────────────────────────────────────────────

    def get_pnl(self, symbol: str, current_price: float) -> float:
        """Retourne le PnL en USD pour une position ouverte."""
        pos = self.positions.get(symbol)
        if not pos:
            return 0.0
        entry = pos["entry_price"]
        qty   = pos["quantity"]
        lev   = pos["leverage"]
        if pos["side"] == "buy":
            return (current_price - entry) * qty * lev
        else:
            return (entry - current_price) * qty * lev

    def total_pnl(self, prices: Dict[str, float]) -> float:
        return sum(self.get_pnl(sym, price) for sym, price in prices.items())
