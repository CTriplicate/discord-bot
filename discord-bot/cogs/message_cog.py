"""
Модуль Message — массовая рассылка в ЛС пользователям с определённой ролью.

Команда:
  /message @role <текст>   — отправить сообщение всем участникам с указанной ролью

Особенности:
  - Соблюдение rate limit Discord (1 сообщение в секунду для ЛС).
  - Отчёт о доставке: сколько отправлено / сколько не удалось.
  - Защита от злоупотреблений — только администраторы.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import config


class MessageCog(commands.Cog, name="Message"):
    """Массовая рассылка сообщений в ЛС."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_admin(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        user_roles = {r.name for r in interaction.user.roles}
        return bool(user_roles & set(config.ADMIN_ROLES))

    # ------------------------------------------------------------------
    # /message
    # ------------------------------------------------------------------

    @app_commands.command(
        name="message",
        description="Отправить сообщение в ЛС всем с указанной ролью",
    )
    @app_commands.describe(
        role="Роль — получатели сообщения",
        text="Текст сообщения",
    )
    async def message(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        text: str,
    ) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        if not text.strip():
            await interaction.response.send_message(
                "❌ Текст сообщения не может быть пустым.", ephemeral=True
            )
            return

        # Собираем всех участников с указанной ролью (исключаем ботов)
        members = [m for m in role.members if not m.bot]

        if not members:
            await interaction.response.send_message(
                f"❌ Нет участников с ролью {role.mention} (или только боты).",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"📨 Начинаю рассылку {len(members)} участникам с ролью {role.mention}...",
        )

        success = 0
        failed = 0

        for member in members:
            try:
                await member.send(text)
                success += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
            # Rate limit: ~1 сообщение/сек для DM
            await asyncio.sleep(1.05)

        embed = discord.Embed(
            title="📨 Рассылка завершена",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="Роль", value=role.mention, inline=True)
        embed.add_field(name="Доставлено", value=f"✅ {success}", inline=True)
        embed.add_field(name="Не доставлено", value=f"❌ {failed}", inline=True)
        embed.add_field(
            name="Причина недоставки",
            value="ЛС закрыты или пользователь заблокировал бота",
            inline=False,
        )

        # Обновляем исходное сообщение
        await interaction.edit_original_response(
            content=None, embed=embed
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageCog(bot))
