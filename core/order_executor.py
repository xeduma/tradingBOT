"""
Exécuteur d'ordres — cible <50ms par ordre.
Mode paper trading inclus pour les tests sans capital réel.
"""

import time
import uuid
from typing import Optional

from core.config import Config
from core.exchange_manager import ExchangeManager
from utils.logger import setup_logger

logger = setup_logger("order_executor")


class OrderExecutor:
    """
    Responsable du passage et de la fermeture des ordres.

    Deux modes :
    - "paper" : simule l'exécution sans toucher l'exchange
    - "live"  : ordres réels via ExchangeManager (ccxt)

    Les paramètres de risque (SL, TP, levier) calculés par RiskManager
    sont transmis via le dict params et stockés dans le retour de l'ordre
    pour que PositionManager puisse les suivre.
    """

    def __init__(self, config: Config, exchange_mgr: ExchangeManager) -> None:
        self.cfg     = config
        self.exc     = exchange_mgr
        self.is_live = config.mode == "live"

    # ─── Ouverture ────────────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        notional: float,
        leverage: int,
        stop_loss: float,
        tp1: float,
        tp2: float,
        trailing_trigger_pct: float,
        trailing_distance: float,
    ) -> Optional[dict]:
        """
        Passe un ordre au marché.
        Retourne le dict ordre enrichi (price, id, …) ou None si échec.
        """
        t0 = time.monotonic()

        try:
            if self.is_live:
                order = await self._live_order(symbol, side, quantity, leverage)
            else:
                order = await self._paper_order(symbol, side, quantity)

            latency_ms = (time.monotonic() - t0) * 1000
            if latency_ms > 50:
                logger.warning(f"Latence élevée pour {symbol} : {latency_ms:.1f}ms > 50ms")
            else:
                logger.debug(f"Ordre {symbol} exécuté en {latency_ms:.1f}ms")

            # Enrichissement avec les paramètres de risque
            order.update({
                "stop_loss":             stop_loss,
                "tp1":                   tp1,
                "tp2":                   tp2,
                "trailing_trigger_pct":  trailing_trigger_pct,
                "trailing_distance":     trailing_distance,
                "leverage":              leverage,
                "latency_ms":            latency_ms,
            })
            return order

        except Exception as e:
            logger.error(f"Échec place_order {symbol} [{side}] : {e}", exc_info=True)
            return None

    async def _live_order(
        self, symbol: str, side: str, quantity: float, leverage: int
    ) -> dict:
        """Ordre réel via ccxt."""
        # Définir le levier avant l'ordre (Binance Futures)
        await self.exc.set_leverage(symbol, leverage)
        order = await self.exc.place_market_order(symbol, side, quantity)
        return {
            "id":     order["id"],
            "symbol": symbol,
            "side":   side,
            "amount": float(order["amount"]),
            "average": float(order.get("average") or order.get("price", 0)),
            "cost":   float(order.get("cost", 0)),
            "status": order.get("status", "closed"),
        }

    async def _paper_order(
        self, symbol: str, side: str, quantity: float
    ) -> dict:
        """Simulation d'ordre — récupère le prix actuel via REST."""
        ticker = await self.exc.fetch_ticker(symbol)
        price  = float(ticker["last"]) if ticker else 0.0

        order = {
            "id":      str(uuid.uuid4())[:8],
            "symbol":  symbol,
            "side":    side,
            "amount":  quantity,
            "average": price,
            "cost":    quantity * price,
            "status":  "closed",
        }
        logger.info(f"[PAPER] {side.upper()} {quantity:.6f} {symbol} @ {price:.4f}")
        return order

    # ─── Fermeture ────────────────────────────────────────────────────────────

    async def close_position(self, position: dict) -> Optional[dict]:
        """
        Ferme une position existante (ordre inverse).
        """
        symbol   = position["symbol"]
        side     = position["side"]
        quantity = position["quantity"]
        close_side = "sell" if side == "buy" else "buy"

        t0 = time.monotonic()
        try:
            if self.is_live:
                order = await self._live_order(symbol, close_side, quantity, leverage=1)
            else:
                order = await self._paper_order(symbol, close_side, quantity)

            latency_ms = (time.monotonic() - t0) * 1000
            order["close_latency_ms"] = latency_ms
            logger.info(f"Position {symbol} fermée en {latency_ms:.1f}ms @ {order['average']:.4f}")
            return order

        except Exception as e:
            logger.error(f"Échec close_position {symbol} : {e}", exc_info=True)
            return None
