"""
Discord Bot — Главный файл запуска.

Запуск:
    python main.py

Переменные окружения (опционально):
    BOT_TOKEN — токен бота (переопределяет config.json)
"""

from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord.ext import commands

import database as db
import config

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot")

# ---------------------------------------------------------------------------
# Настройка Intents
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.members = True       # Нужен для работы с ролями и списками участников
intents.message_content = True  # Для будущих текстовых команд
intents.dm_messages = True   # Для рассылки в ЛС


# ---------------------------------------------------------------------------
# Класс бота
# ---------------------------------------------------------------------------

class TournamentBot(commands.Bot):
    """Основной класс бота с автоматической загрузкой когов."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
        )

    async def setup_hook(self) -> None:
        """Вызывается при запуске — регистрируем коги и слэш-команды."""
        logger.info("Инициализация базы данных...")
        await db.init_db()

        logger.info("Загрузка когов...")
        extensions = [
            "cogs.warning",
            "cogs.roll",
            "cogs.message_cog",
            "cogs.tournament",
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                logger.info(f"  ✅ {ext}")
            except Exception as exc:
                logger.error(f"  ❌ {ext}: {exc}")

        # Синхронизация слэш-команд с Discord
        # В продакшене рекомендуется синхронизировать только для конкретной гильдии
        # для мгновенного обновления. Глобальная синхронизация может занимать до часа.
        try:
            synced = await self.tree.sync()
            logger.info(f"Синхронизировано {len(synced)} слэш-команд.")
        except Exception as exc:
            logger.error(f"Ошибка синхронизации команд: {exc}")

    async def on_ready(self) -> None:
        """Бот готов к работе."""
        logger.info(f"🤖 Бот запущен как {self.user} (ID: {self.user.id})")
        logger.info(f"   Гильдий: {len(self.guilds)}")
        for guild in self.guilds:
            logger.info(f"   - {guild.name} ({guild.id})")

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        """Глобальный обработчик ошибок."""
        if isinstance(error, commands.CommandNotFound):
            return
        logger.error(f"Ошибка команды: {error}", exc_info=error)
        await ctx.send(f"❌ Произошла ошибка: {error}")


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def main() -> None:
    token = config.TOKEN
    if token == "YOUR_BOT_TOKEN_HERE":
        # Проверяем переменную окружения
        import os
        token = os.environ.get("BOT_TOKEN", "")

    if not token or token == "YOUR_BOT_TOKEN_HERE":
        logger.error(
            "Токен бота не указан! Откройте config.json и вставьте ваш токен "
            "в поле \"token\", либо установите переменную окружения BOT_TOKEN."
        )
        sys.exit(1)

    async with TournamentBot() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
