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
import traceback

import discord
from discord import app_commands
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
                logger.error(traceback.format_exc())

        # Синхронизация слэш-команд с Discord
        try:
            synced = await self.tree.sync()
            logger.info(f"Синхронизировано {len(synced)} слэш-команд:")
            for cmd in synced:
                logger.info(f"  /{cmd.name}")
        except Exception as exc:
            logger.error(f"Ошибка синхронизации команд: {exc}")
            logger.error(traceback.format_exc())

    async def on_ready(self) -> None:
        """Бот готов к работе."""
        logger.info(f"🤖 Бот запущен как {self.user} (ID: {self.user.id})")
        logger.info(f"   Гильдий: {len(self.guilds)}")
        for guild in self.guilds:
            logger.info(f"   - {guild.name} ({guild.id})")

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        """Глобальный обработчик ошибок префиксных команд."""
        if isinstance(error, commands.CommandNotFound):
            return
        logger.error(f"Ошибка команды: {error}", exc_info=error)
        if ctx.message:
            await ctx.send(f"❌ Произошла ошибка: {error}")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """
        Глобальный обработчик ошибок слэш-команд.
        Гарантирует, что бот ВСЕГДА ответит за 3 секунды,
        чтобы не было «Приложение не отвечает».
        """
        # Логируем полную ошибку
        logger.error(
            f"Ошибка слэш-команды /{interaction.command.name if interaction.command else '?'}: "
            f"{error}",
        )
        logger.error(traceback.format_exc())

        # Формируем текст ошибки для пользователя
        if isinstance(error, app_commands.CheckFailure):
            msg = "❌ У вас нет прав для использования этой команды."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Подождите {error.retry_after:.0f} секунд перед повторным использованием."
        elif isinstance(error, app_commands.MissingRole):
            msg = f"❌ Необходима роль: {error.missing_role}"
        elif isinstance(error, app_commands.BotMissingPermissions):
            perms = ", ".join(error.missing_permissions)
            msg = f"❌ Боту не хватает прав: {perms}"
        else:
            msg = f"❌ Произошла ошибка: {error}"

        # Пытаемся ответить (interaction мог быть уже обработан)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.InteractionResponded:
            # Уже ответили — шлём followup
            try:
                await interaction.followup.send(msg, ephemeral=True)
            except Exception:
                pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def main() -> None:
    token = config.TOKEN
    if token == "YOUR_BOT_TOKEN_HERE":
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
