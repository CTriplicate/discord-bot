"""
Модуль Warning — система штрафов с выдачей ролей.

Команды:
  /warning add   @user [причина]       — выдать штраф
  /warning remove <id>                  — убрать штраф по ID
  /warning set   @role [@role ...]      — указать роли, выдающиеся при штрафе
  /warning list  [@user]                — список штрафов (всех или конкретного пользователя)
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import config


class WarningCog(commands.Cog, name="Warning"):
    """Система штрафов с выдачей ролей."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_admin(interaction: discord.Interaction) -> bool:
        """Проверка: пользователь имеет одну из админ-ролей."""
        if interaction.user.guild_permissions.administrator:
            return True
        user_roles = {r.name for r in interaction.user.roles}
        return bool(user_roles & set(config.ADMIN_ROLES))

    # ------------------------------------------------------------------
    # /warning add
    # ------------------------------------------------------------------

    @app_commands.command(
        name="warning",
        description="Управление штрафами",
    )
    @app_commands.describe(
        action="Действие: add, remove, set, list",
        user="Пользователь (для add / list)",
        warning_id="ID штрафа (для remove)",
        reason="Причина штрафа (для add)",
        roles="Роли для выдачи при штрафе (для set, через пробел)",
    )
    async def warning(
        self,
        interaction: discord.Interaction,
        action: str,
        user: discord.Member | None = None,
        warning_id: int | None = None,
        reason: str | None = None,
        roles: str | None = None,
    ) -> None:
        """Единая точка входа для подкоманд warning."""
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        action = action.lower().strip()
        if action == "add":
            await self._add(interaction, user, reason)
        elif action == "remove":
            await self._remove(interaction, warning_id)
        elif action == "set":
            await self._set_roles(interaction, roles)
        elif action == "list":
            await self._list(interaction, user)
        else:
            await interaction.response.send_message(
                "❌ Неизвестное действие. Используйте: `add`, `remove`, `set`, `list`.",
                ephemeral=True,
            )

    async def _add(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None,
        reason: str | None,
    ) -> None:
        if user is None:
            await interaction.response.send_message(
                "❌ Укажите пользователя: `/warning add @user [причина]`", ephemeral=True
            )
            return

        if user.bot:
            await interaction.response.send_message(
                "❌ Нельзя выдать штраф боту.", ephemeral=True
            )
            return

        reason = reason or "Причина не указана"

        # Получаем роли для штрафа из БД
        warn_role_ids = await db.warning_get_roles(interaction.guild_id)  # type: ignore
        roles_given: list[int] = []

        for role_id in warn_role_ids:
            role = interaction.guild.get_role(role_id)  # type: ignore
            if role and role not in user.roles:
                try:
                    await user.add_roles(role, reason=f"Штраф: {reason}")
                    roles_given.append(role_id)
                except discord.Forbidden:
                    pass  # Нет прав — пропускаем

        # Записываем в БД
        wid = await db.warning_add(
            user_id=user.id,
            guild_id=interaction.guild_id,  # type: ignore
            moderator_id=interaction.user.id,
            reason=reason,
            roles_given=roles_given,
        )

        count = await db.warning_count(interaction.guild_id, user.id)  # type: ignore

        embed = discord.Embed(
            title="⚠️ Штраф выдан",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="Пользователь", value=user.mention, inline=True)
        embed.add_field(name="Модератор", value=interaction.user.mention, inline=True)
        embed.add_field(name="Причина", value=reason, inline=False)
        embed.add_field(name="ID штрафа", value=f"#{wid}", inline=True)
        embed.add_field(name="Всего штрафов", value=str(count), inline=True)

        if roles_given:
            role_mentions = " ".join(
                f"<@&{rid}>" for rid in roles_given
            )
            embed.add_field(name="Выданные роли", value=role_mentions, inline=False)

        # Проверка лимита
        if config.MAX_WARNINGS > 0 and count >= config.MAX_WARNINGS:
            embed.add_field(
                name="🚨 Достигнут лимит штрафов!",
                value=f"Пользователь набрал {count} штрафов (лимит: {config.MAX_WARNINGS}).",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    async def _remove(
        self,
        interaction: discord.Interaction,
        warning_id: int | None,
    ) -> None:
        if warning_id is None:
            await interaction.response.send_message(
                "❌ Укажите ID штрафа: `/warning remove <id>`", ephemeral=True
            )
            return

        # Сначала получаем штраф, чтобы снять роли
        warnings = await db.warning_list(interaction.guild_id)  # type: ignore
        target = next((w for w in warnings if w["id"] == warning_id), None)

        if not target:
            await interaction.response.send_message(
                f"❌ Штраф #{warning_id} не найден.", ephemeral=True
            )
            return

        removed = await db.warning_remove(warning_id, interaction.guild_id)  # type: ignore
        if not removed:
            await interaction.response.send_message(
                f"❌ Не удалось удалить штраф #{warning_id}.", ephemeral=True
            )
            return

        # Снимаем роли, которые были выданы
        member = interaction.guild.get_member(target["user_id"])  # type: ignore
        roles_removed: list[str] = []
        if member:
            import json
            for role_id in json.loads(target["roles_given"]):
                role = interaction.guild.get_role(role_id)  # type: ignore
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Снятие штрафа")
                        roles_removed.append(role.name)
                    except discord.Forbidden:
                        pass

        embed = discord.Embed(
            title="✅ Штраф снят",
            description=f"Штраф #{warning_id} удалён.",
            color=config.EMBED_COLOR,
        )
        if roles_removed:
            embed.add_field(
                name="Снятые роли", value=", ".join(roles_removed), inline=False
            )

        await interaction.response.send_message(embed=embed)

    async def _set_roles(
        self,
        interaction: discord.Interaction,
        roles_str: str | None,
    ) -> None:
        if not roles_str:
            # Показать текущие настройки
            role_ids = await db.warning_get_roles(interaction.guild_id)  # type: ignore
            if not role_ids:
                await interaction.response.send_message(
                    "📋 Роли для штрафов не настроены. Используйте: "
                    "`/warning set @role1 @role2 ...`",
                    ephemeral=True,
                )
                return

            mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
            await interaction.response.send_message(
                f"📋 Текущие роли при штрафе: {mentions}", ephemeral=True
            )
            return

        # Парсим роли из строки (поддержка упоминаний и ID)
        import re
        role_ids: list[int] = []
        for match in re.findall(r"<@&(\d+)>", roles_str):
            role_ids.append(int(match))
        for part in roles_str.split():
            if part.isdigit():
                role_ids.append(int(part))

        # Валидация
        valid_ids: list[int] = []
        for rid in role_ids:
            role = interaction.guild.get_role(rid)  # type: ignore
            if role:
                valid_ids.append(rid)

        if not valid_ids:
            await interaction.response.send_message(
                "❌ Не удалось распознать ни одну роль. Используйте упоминания (@role) или ID.",
                ephemeral=True,
            )
            return

        await db.warning_set_roles(interaction.guild_id, valid_ids)  # type: ignore

        mentions = " ".join(f"<@&{rid}>" for rid in valid_ids)
        embed = discord.Embed(
            title="⚙️ Роли для штрафов обновлены",
            description=f"При выдаче штрафа будут назначены роли: {mentions}",
            color=config.EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    async def _list(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None,
    ) -> None:
        user_id = user.id if user else None
        warnings = await db.warning_list(interaction.guild_id, user_id)  # type: ignore

        if not warnings:
            await interaction.response.send_message(
                "📋 Список штрафов пуст." if not user else f"📋 У {user.mention} нет штрафов.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"⚠️ Штрафы" + (f" — {user.display_name}" if user else ""),
            color=config.EMBED_COLOR,
        )

        import json
        lines: list[str] = []
        for w in warnings[:25]:  # Лимит embed-полей
            member = interaction.guild.get_member(w["user_id"])  # type: ignore
            name = member.display_name if member else f"<@{w['user_id']}>"
            mod = interaction.guild.get_member(w["moderator_id"])  # type: ignore
            mod_name = mod.display_name if mod else f"<@{w['moderator_id']}>"
            roles_given = json.loads(w["roles_given"])
            roles_text = (
                " ".join(f"<@&{rid}>" for rid in roles_given) if roles_given else "—"
            )
            lines.append(
                f"**#{w['id']}** | {name} | Мод: {mod_name}\n"
                f"Причина: {w['reason']} | Роли: {roles_text}\n"
                f"Дата: {w['created_at']}"
            )

        embed.description = "\n\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WarningCog(bot))
