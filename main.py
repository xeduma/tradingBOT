"""
APEX TRADING — Système de trading automatisé crypto
Auteur  : Apex Trading System
Stack   : ccxt · TimescaleDB · Mistral AI · Telegram · Prometheus
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

from core.engine import TradingEngine
from core.config import Config
from monitoring.telegram_bot import TelegramNotifier
from monitoring.prometheus_exporter import PrometheusExporter
from utils.logger import setup_logger

logger = setup_logger("main")


async def shutdown(engine: TradingEngine, sig: signal.Signals) -> None:
    logger.warning(f"Signal reçu : {sig.name} — fermeture propre en cours...")
    await engine.emergency_close_all()
    await engine.stop()
    logger.info("Moteur arrêté. Au revoir.")


async def main() -> None:
    config = Config.from_env()

    logger.info("=" * 60)
    logger.info("  APEX TRADING ENGINE v3.1 — démarrage")
    logger.info(f"  Mode          : {config.mode}")
    logger.info(f"  Exchanges     : {', '.join(config.exchanges)}")
    logger.info(f"  Capital cible : {config.capital:,.0f} USD")
    logger.info(f"  Max positions : {config.max_positions}")
    logger.info("=" * 60)

    # Services
    notifier  = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    exporter  = PrometheusExporter(port=config.prometheus_port)

    # Moteur principal
    engine = TradingEngine(config, notifier, exporter)

    # Gestion des signaux système
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(engine, s)))

    await notifier.send("🟢 *APEX TRADING démarré* — moteur actif, surveillance en cours.")

    try:
        await engine.start()
    except Exception as e:
        logger.critical(f"Erreur fatale du moteur : {e}", exc_info=True)
        await notifier.send(f"🔴 *ERREUR FATALE* : `{e}` — système arrêté.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
