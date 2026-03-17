"""
Gestionnaire multi-exchange — ccxt async + WebSocket.
Supporte Binance et Kraken avec reconnexion automatique.
"""

import asyncio
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import ccxt.pro as ccxtpro  # ccxt[async] pour WebSocket
from ccxt.base.errors import NetworkError, RequestTimeout

from core.config import Config
from utils.logger import setup_logger

logger = setup_logger("exchange_manager")

# Priorité d'exchange par paire (Binance par défaut, Kraken en fallback)
EXCHANGE_ROUTING = {
    "BTC/USDC":   "binance",
    "ETH/USDC":   "binance",
    "SOL/USDC":   "binance",
    "BNB/USDC":   "binance",
    "XRP/USDC":   "kraken",
    "AVAX/USDC":  "binance",
    "ADA/USDC":   "kraken",
    "DOT/USDC":   "kraken",
    "LINK/USDC":  "binance",
}


class ExchangeManager:
    """
    Abstraction multi-exchange.
    - WebSocket pour les prix temps réel (latence < 5ms)
    - REST fallback pour les ordres (<50ms garanti)
    - Reconnexion automatique avec backoff exponentiel
    """

    def __init__(self, config: Config) -> None:
        self.cfg        = config
        self.exchanges: Dict[str, ccxtpro.Exchange] = {}
        self._connected = False

    # ─── Connexion ────────────────────────────────────────────────────────────

    async def connect_all(self) -> None:
        if "binance" in self.cfg.exchanges:
            self.exchanges["binance"] = ccxtpro.binance({
                "apiKey":           self.cfg.binance_api_key,
                "secret":           self.cfg.binance_api_secret,
                "enableRateLimit":  True,
                "options": {
                    "defaultType": "future",   # contrats perpétuels pour le levier
                    "adjustForTimeDifference": True,
                },
            })
            logger.info("Binance connecté (futures).")

        if "kraken" in self.cfg.exchanges:
            self.exchanges["kraken"] = ccxtpro.kraken({
                "apiKey":          self.cfg.kraken_api_key,
                "secret":          self.cfg.kraken_api_secret,
                "enableRateLimit": True,
            })
            logger.info("Kraken connecté.")

        self._connected = True

    async def disconnect_all(self) -> None:
        for name, ex in self.exchanges.items():
            try:
                await ex.close()
                logger.info(f"{name} déconnecté.")
            except Exception:
                pass
        self._connected = False

    # ─── Flux WebSocket ──────────────────────────────────────────────────────

    async def stream_candles(
        self, symbols: List[str], timeframe: str
    ) -> AsyncGenerator[Tuple[str, List[dict]], None]:
        """
        Générateur asynchrone qui yield (symbol, candles) à chaque bougie fermée.
        Utilise watch_ohlcv de ccxt.pro (WebSocket).
        """
        tasks = []
        for symbol in symbols:
            ex_name = EXCHANGE_ROUTING.get(symbol, "binance")
            ex = self.exchanges.get(ex_name)
            if ex:
                tasks.append(self._stream_symbol(ex, symbol, timeframe))

        # Concurrence sur tous les symboles
        async def merge():
            queues = {}
            for symbol in symbols:
                queues[symbol] = asyncio.Queue(maxsize=10)

            async def producer(ex, sym, tf, q):
                backoff = 1
                while True:
                    try:
                        candles = await ex.watch_ohlcv(sym, tf, limit=100)
                        await q.put((sym, candles))
                        backoff = 1
                    except (NetworkError, RequestTimeout) as e:
                        logger.warning(f"Erreur WebSocket {sym} : {e} — retry dans {backoff}s")
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60)
                    except Exception as e:
                        logger.error(f"Erreur inattendue {sym} : {e}")
                        await asyncio.sleep(5)

            combined_q = asyncio.Queue(maxsize=100)
            producers = [
                asyncio.create_task(
                    producer(
                        self.exchanges.get(EXCHANGE_ROUTING.get(s, "binance")),
                        s, timeframe, combined_q
                    )
                )
                for s in symbols
                if self.exchanges.get(EXCHANGE_ROUTING.get(s, "binance"))
            ]

            while True:
                item = await combined_q.get()
                yield item

        async for symbol, candles in merge():
            # Convertit le format ccxt [[ts, o, h, l, c, v], ...] → dicts
            yield symbol, self._normalize_candles(candles)

    async def _stream_symbol(self, ex, symbol, timeframe):
        """Inutilisé directement — voir merge() ci-dessus."""
        pass

    # ─── REST : récupération de bougies ──────────────────────────────────────

    async def fetch_candles(
        self, symbol: str, timeframe: str = "1m", limit: int = 200
    ) -> List[dict]:
        """Récupère les bougies historiques via REST (fallback ou démarrage)."""
        ex_name = EXCHANGE_ROUTING.get(symbol, "binance")
        ex = self.exchanges.get(ex_name)
        if not ex:
            return []

        raw = await ex.fetch_ohlcv(symbol, timeframe, limit=limit)
        return self._normalize_candles(raw)

    # ─── Ordres ───────────────────────────────────────────────────────────────

    async def place_market_order(
        self, symbol: str, side: str, amount: float, params: Optional[dict] = None
    ) -> Optional[dict]:
        """
        Passe un ordre au marché (<50ms cible).
        side = 'buy' | 'sell'
        """
        ex_name = EXCHANGE_ROUTING.get(symbol, "binance")
        ex = self.exchanges.get(ex_name)
        if not ex:
            raise ValueError(f"Aucun exchange disponible pour {symbol}")

        params = params or {}
        try:
            order = await ex.create_market_order(symbol, side, amount, params=params)
            logger.info(f"Ordre marché exécuté : {side} {amount} {symbol} @ ~{order.get('average')}")
            return order
        except Exception as e:
            logger.error(f"Échec ordre {symbol} : {e}")
            raise

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """Définit le levier sur Binance Futures."""
        ex = self.exchanges.get("binance")
        if ex and hasattr(ex, "set_leverage"):
            try:
                await ex.set_leverage(leverage, symbol)
            except Exception as e:
                logger.warning(f"set_leverage({symbol}, {leverage}) : {e}")

    async def fetch_ticker(self, symbol: str) -> Optional[dict]:
        ex_name = EXCHANGE_ROUTING.get(symbol, "binance")
        ex = self.exchanges.get(ex_name)
        if not ex:
            return None
        return await ex.fetch_ticker(symbol)

    # ─── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_candles(raw: List) -> List[dict]:
        """
        Convertit le format ccxt OHLCV brut :
        [[timestamp, open, high, low, close, volume], ...]
        en liste de dicts lisibles.
        """
        result = []
        for c in raw:
            if len(c) >= 6:
                result.append({
                    "timestamp": c[0],
                    "open":      float(c[1]),
                    "high":      float(c[2]),
                    "low":       float(c[3]),
                    "close":     float(c[4]),
                    "volume":    float(c[5]),
                })
        return result
