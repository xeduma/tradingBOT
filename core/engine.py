"""
Moteur principal — Squeeze Momentum Strategy
Gere les timeframes 10min (signal) et 4h (confirmation).
"""

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List

from core.config import Config
from core.exchange_manager import ExchangeManager
from core.position_manager import PositionManager
from core.order_executor import OrderExecutor
from core.risk_manager import RiskManager
from strategies.signal_engine import SignalEngine
from data.db import Database
from monitoring.telegram_bot import TelegramNotifier
from monitoring.prometheus_exporter import PrometheusExporter
from utils.logger import setup_logger

logger = setup_logger("engine")


class TradingEngine:

    def __init__(self, config, notifier, exporter):
        self.cfg = config
        self.notifier = notifier
        self.exporter = exporter
        self._running = False
        self.db = Database(config.db_url)
        self.exchange_mgr = ExchangeManager(config)
        self.position_mgr = PositionManager(config)
        self.risk_mgr = RiskManager(config)
        self.executor = OrderExecutor(config, self.exchange_mgr)
        self.signal_engine = SignalEngine(config)
        self.daily_pnl = 0.0
        self.session_start = datetime.now(timezone.utc)
        self.last_candles: Dict = defaultdict(list)
        self.last_signal_time: Dict = {}

    # ── Demarrage ────────────────────────────────────────────────────────────

    async def start(self):
        logger.info("Initialisation des connexions...")
        await self.db.connect()
        await self.exchange_mgr.connect_all()
        await self.exporter.start()
        self.exporter.set_mistral_threshold(self.cfg.mistral_confidence_threshold)
        self.exporter.set_mistral_enabled(self.cfg.mistral_enabled)

        self._running = True
        logger.info(
            f"Moteur demarre | Strategie: Squeeze Momentum | "
            f"TF: {self.cfg.timeframe} + {self.cfg.timeframe_confirm} | "
            f"Auto-close: {self.cfg.auto_close_hours}h"
        )

        # Charger les bougies 4h au demarrage
        await self._refresh_4h_candles()

        await asyncio.gather(
            self._price_feed_loop(),
            self._trading_loop(),
            self._position_monitor_loop(),
            self._heartbeat_loop(),
            self._refresh_4h_loop(),
        )

    async def stop(self):
        self._running = False
        await self.exchange_mgr.disconnect_all()
        await self.db.disconnect()
        logger.info("Moteur arrete proprement.")

    async def emergency_close_all(self):
        logger.warning("FERMETURE D'URGENCE...")
        positions = self.position_mgr.get_open_positions()
        tasks = [self._close_position(pos, reason="EMERGENCY") for pos in positions]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.notifier.send(f"URGENCE : {len(tasks)} position(s) fermee(s).")

    # ── Boucles ──────────────────────────────────────────────────────────────

    async def _price_feed_loop(self):
        """Flux WebSocket des bougies 10min."""
        while self._running:
            try:
                async for symbol, candles in self.exchange_mgr.stream_candles(
                    self.cfg.symbols, self.cfg.timeframe
                ):
                    self.last_candles[symbol] = candles
                    self.exporter.update_price(symbol, candles[-1]["close"])
            except Exception as e:
                logger.error(f"Erreur flux prix : {e} — reconnexion dans 5s")
                await asyncio.sleep(5)

    async def _trading_loop(self):
        """Cycle principal de generation de signaux — toutes les 10min."""
        while self._running:
            cycle_start = time.monotonic()

            # Limite journaliere
            if self.daily_pnl <= -(self.cfg.capital * self.cfg.daily_loss_limit_pct):
                logger.warning(f"Limite perte journaliere ({self.daily_pnl:.0f}$) — pause 1h.")
                await self.notifier.send("Limite -5% atteinte — trading suspendu 1h.")
                await asyncio.sleep(3600)
                continue

            for symbol in self.cfg.symbols:
                candles = self.last_candles.get(symbol)
                if not candles or len(candles) < 220:
                    continue
                try:
                    await self._process_symbol(symbol, candles)
                except Exception as e:
                    logger.error(f"Erreur {symbol} : {e}", exc_info=True)

            elapsed = time.monotonic() - cycle_start
            self.exporter.set_cycle_duration(elapsed)
            # Cycle de 10min (600s)
            await asyncio.sleep(max(0, 600 - elapsed))

    async def _process_symbol(self, symbol, candles):
        """Analyse un actif et execute si signal valide."""

        # Position deja ouverte -> gestion
        open_pos = self.position_mgr.get_position(symbol)
        if open_pos:
            await self._manage_open_position(symbol, open_pos, candles[-1])
            return

        # Calcul des indicateurs 10min
        indicators = self.signal_engine.compute_indicators(candles)
        if not indicators:
            return

        # Signal technique
        raw_signal = self.signal_engine.generate_signal(indicators, symbol)
        if raw_signal is None:
            return

        # Score de force du signal (0-6) — ajuste la taille de position
        signal_strength = self.signal_engine.get_signal_strength(indicators, symbol)

        # Mistral AI (optionnel)
        mistral_score = await self.signal_engine.get_mistral_score(symbol, self.exporter)
        if self.cfg.mistral_enabled and self.cfg.mistral_required:
            if mistral_score < self.cfg.mistral_confidence_threshold:
                logger.debug(f"{symbol} rejete Mistral ({mistral_score}/100)")
                return

        # Validation risque
        risk_ok, reason = self.risk_mgr.validate_trade(
            symbol=symbol,
            signal=raw_signal,
            price=candles[-1]["close"],
            open_positions=self.position_mgr.count_open(),
            daily_pnl=self.daily_pnl,
            indicators=indicators,
        )
        if not risk_ok:
            logger.debug(f"{symbol} rejete RiskManager : {reason}")
            return

        # Calcul position — taille ajustee selon la force du signal
        position_params = self.risk_mgr.compute_position(
            symbol=symbol,
            price=candles[-1]["close"],
            atr=indicators["atr"],
            signal=raw_signal,
            signal_strength=signal_strength,
        )

        # Execution
        t0 = time.monotonic()
        order = await self.executor.place_order(
            symbol=symbol,
            side=raw_signal,
            **position_params,
        )
        latency_ms = (time.monotonic() - t0) * 1000
        self.exporter.record_order_latency(latency_ms)

        if order:
            self.position_mgr.add_position(symbol, order, indicators)
            await self.db.save_trade(order)
            self.exporter.increment_trades(raw_signal)

            squeeze_info = (
                f"SqFired={indicators.get('squeeze_fired')} | "
                f"Mom={indicators.get('momentum', 0):.4f} | "
                f"RSI={indicators.get('rsi', 0):.1f} | "
                f"Score={signal_strength}/6"
            )
            msg = (
                f"{raw_signal.upper()} {symbol} @ {order.get('average', 0):.4f} | "
                f"SL:{position_params['stop_loss']:.4f} | "
                f"TP1:{position_params['tp1']:.4f} | "
                f"TP2:{position_params['tp2']:.4f} | "
                f"x{position_params['leverage']} | "
                f"{squeeze_info} | {latency_ms:.1f}ms"
            )
            await self.notifier.send(msg)
            logger.info(f"Ordre execute : {msg}")

    async def _manage_open_position(self, symbol, position, last_candle):
        current_price = last_candle["close"]
        candles = self.last_candles.get(symbol, [])
        indicators = self.signal_engine.compute_indicators(candles) if candles else {}
        action = self.position_mgr.update_position(symbol, current_price, indicators)
        if action in ("TP1", "TP2", "SL", "TRAILING", "AUTO_CLOSE", "SIGNAL_EXIT"):
            pnl = self.position_mgr.get_pnl(symbol, current_price)
            await self._close_position(position, reason=action, pnl=pnl)
            self.daily_pnl += pnl
            self.exporter.update_pnl(self.daily_pnl)
            await self.notifier.send(
                f"CLOSE {symbol} | {action} | PnL:{pnl:+.2f}$"
            )

    async def _close_position(self, position, reason="MANUAL", pnl=0.0):
        order = await self.executor.close_position(position)
        if order:
            self.position_mgr.remove_position(position["symbol"])
            await self.db.update_trade(order, close_reason=reason, pnl=pnl)

    async def _position_monitor_loop(self):
        """Verifie les stops toutes les 30s (pas 10s car TF=10min)."""
        while self._running:
            await asyncio.sleep(30)
            for symbol, pos in list(self.position_mgr.positions.items()):
                candles = self.last_candles.get(symbol)
                if not candles:
                    continue
                current_price = candles[-1]["close"]
                indicators = self.signal_engine.compute_indicators(candles)
                action = self.position_mgr.update_position(symbol, current_price, indicators)
                if action:
                    pnl = self.position_mgr.get_pnl(symbol, current_price)
                    await self._close_position(pos, reason=action, pnl=pnl)
                    self.daily_pnl += pnl
                    self.exporter.update_pnl(self.daily_pnl)

    async def _refresh_4h_loop(self):
        """
        Rafraichit les bougies 4h toutes les 30 minutes.
        Les bougies 4h ne changent pas souvent — inutile de les recharger
        a chaque cycle de 10min.
        """
        while self._running:
            await asyncio.sleep(1800)  # 30 minutes
            await self._refresh_4h_candles()

    async def _refresh_4h_candles(self):
        """Charge les bougies 4h pour chaque symbole via REST."""
        logger.info("Chargement des bougies 4h (confirmation multi-timeframe)...")
        for symbol in self.cfg.symbols:
            try:
                candles_4h = await self.exchange_mgr.fetch_candles(
                    symbol, self.cfg.timeframe_confirm, limit=100
                )
                if candles_4h:
                    self.signal_engine.store_candles_4h(symbol, candles_4h)
                    logger.debug(f"4h OK : {symbol} — {len(candles_4h)} bougies")
            except Exception as e:
                logger.warning(f"Impossible de charger 4h pour {symbol} : {e}")

    async def _heartbeat_loop(self):
        while self._running:
            await asyncio.sleep(3600)
            n_pos  = self.position_mgr.count_open()
            uptime = (datetime.now(timezone.utc) - self.session_start).seconds // 60
            await self.notifier.send(
                f"Heartbeat | {uptime}min uptime | "
                f"{n_pos}/{self.cfg.max_positions} positions | "
                f"PnL:{self.daily_pnl:+.0f}$ | "
                f"TF:{self.cfg.timeframe}+{self.cfg.timeframe_confirm}"
            )
            self.exporter.set_open_positions(n_pos)
