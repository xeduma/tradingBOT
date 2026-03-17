"""
Bot Telegram — alertes instantanées pour le système de trading.
Notifications : ordres, PnL, alertes risque, heartbeat.
"""

import asyncio
import httpx
from utils.logger import setup_logger

logger = setup_logger("telegram")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """
    Envoi de messages Telegram via l'API Bot.
    - Formatage Markdown
    - File d'attente asynchrone pour ne jamais bloquer le moteur
    - Rate limit : max 1 msg/seconde (limite API Telegram)
    """

    def __init__(self, token: str, chat_id: str) -> None:
        self.token   = token
        self.chat_id = chat_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._client = httpx.AsyncClient(timeout=5.0)
        self._enabled = bool(token and chat_id)

        if not self._enabled:
            logger.warning("Telegram non configuré (TOKEN ou CHAT_ID manquant).")

    async def send(self, message: str) -> None:
        """Ajoute un message à la file (non-bloquant)."""
        if not self._enabled:
            logger.info(f"[TELEGRAM] {message}")
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("File Telegram pleine — message ignoré.")

    async def _dispatch_loop(self) -> None:
        """Boucle interne — envoie les messages en respectant le rate limit."""
        while True:
            message = await self._queue.get()
            await self._send_now(message)
            await asyncio.sleep(1.1)  # ≤ 1 msg/s

    async def _send_now(self, text: str) -> None:
        url = TELEGRAM_API.format(token=self.token)
        try:
            resp = await self._client.post(url, json={
                "chat_id":    self.chat_id,
                "text":       text,
                "parse_mode": "Markdown",
            })
            if resp.status_code != 200:
                logger.error(f"Telegram erreur {resp.status_code} : {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Telegram send échoué : {e}")

    def start_dispatch(self) -> asyncio.Task:
        """Lance la boucle de dispatch en tâche de fond."""
        return asyncio.create_task(self._dispatch_loop())
