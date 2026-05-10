"""
Модуль Warning — система штрафов с выдачей ролей.

Команды (подкоманды группы /warning):
  /warning add    @user [причина]       — выдать штраф
  /warning remove <id>                  — убрать штраф по ID
  /warning set    @role [@role ...]     — указать роли, выдающиеся при штрафе
  /warning list   [@user]               — список штрафов (всех или конкретного пользователя)
"""

from __future__ import annotations

import json
import re

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import config


async def _is_admin(interaction: discord.Interaction) -> bool:
    """Проверка: пользователь имеет одну из админ-ролей."""
    if interaction.user.guild_permissions.administrator:
        return True
    user_roles = {r.name for r in interaction.user.roles}
    return bool(user_roles & set(config.ADMIN_ROLES))


class WarningGroup(app_commands.Group, name="warning", description="Управление штрафами"):
    """Группа подкоманд /warning add | remove | set | list"""

    # ------------------------------------------------------------------
    # /warning add
    # ------------------------------------------------------------------

    @app_commands.command(name="add", description="Выдать штраф пользователю")
    @app_commands.describe(
        user="Пользователь, которому выдаётся штраф",
        reason="Причина штрафа",
    )
    async def warning_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "Причина не указана",
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        if user.bot:
            await interaction.response.send_message(
                "❌ Нельзя выдать штраф боту.", ephemeral=True
            )
            return

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
                    pass

        # Записываем в БД
        wid = await db.warning_add(
            user_id=user.id,
            guild_id=interaction.guild_id,  # type: ignore
            moderator_id=interaction.user.id,
            reason=reason,
            roles_given=roles_given,
        )

        count = await db.warning_count(interaction.guild_id, user.id)  # type: ignore

        embed = discord.Embed(title="⚠️ Штраф выдан", color=config.EMBED_COLOR)
        embed.add_field(name="Пользователь", value=user.mention, inline=True)
        embed.add_field(name="Модератор", value=interaction.user.mention, inline=True)
        embed.add_field(name="Причина", value=reason, inline=False)
        embed.add_field(name="ID штрафа", value=f"#{wid}", inline=True)
        embed.add_field(name="Всего штрафов", value=str(count), inline=True)

        if roles_given:
            role_mentions = " ".join(f"<@&{rid}>" for rid in roles_given)
            embed.add_field(name="Выданные роли", value=role_mentions, inline=False)

        if config.MAX_WARNINGS > 0 and count >= config.MAX_WARNINGS:
            embed.add_field(
                name="🚨 Достигнут лимит штрафов!",
                value=f"Пользователь набрал {count} штрафов (лимит: {config.MAX_WARNINGS}).",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /warning remove
    # ------------------------------------------------------------------

    @app_commands.command(name="remove", description="Убрать штраф по ID")
    @app_commands.describe(warning_id="ID штрафа для снятия")
    async def warning_remove(
        self,
        interaction: discord.Interaction,
        warning_id: int,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        # Получаем штраф, чтобы снять роли
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
            embed.add_field(name="Снятые роли", value=", ".join(roles_removed), inline=False)

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /warning set
    # ------------------------------------------------------------------

    @app_commands.command(name="set", description="Указать роли, выдающиеся при штрафе")
    @app_commands.describe(roles="Роли для выдачи при штрафе (упоминания @role)")
    async def warning_set(
        self,
        interaction: discord.Interaction,
        roles: str = "",
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        if not roles:
            # Показать текущие настройки
            role_ids = await db.warning_get_roles(interaction.guild_id)  # type: ignore
            if not role_ids:
                await interaction.response.send_message(
                    "📋 Роли для штрафов не настроены. Используйте: `/warning set @role1 @role2`",
                    ephemeral=True,
                )
                return

            mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
            await interaction.response.send_message(
                f"📋 Текущие роли при штрафе: {mentions}", ephemeral=True
            )
            return

        # Парсим роли (поддержка упоминаний и ID)
        role_ids: list[int] = []
        for match in re.findall(r"<@&(\d+)>", roles):
            role_ids.append(int(match))
        for part in roles.split():
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

    # ------------------------------------------------------------------
    # /warning list
    # ------------------------------------------------------------------

    @app_commands.command(name="list", description="Список штрафов")
    @app_commands.describe(user="Пользователь (необязательно, для фильтрации)")
    async def warning_list(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        user_id = user.id if user else None
        warnings = await db.warning_list(interaction.guild_id, user_id)  # type: ignore

        if not warnings:
            await interaction.response.send_message(
                "📋 Список штрафов пуст." if not user else f"📋 У {user.mention} нет штрафов.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="⚠️ Штрафы" + (f" — {user.display_name}" if user else ""),
            color=config.EMBED_COLOR,
        )

        lines: list[str] = []
        for w in warnings[:25]:
            member = interaction.guild.get_member(w["user_id"])  # type: ignore
            name = member.display_name if member else f"<@{w['user_id']}>"
            mod = interaction.guild.get_member(w["moderator_id"])  # type: ignore
            mod_name = mod.display_name if mod else f"<@{w['moderator_id']}>"
            roles_given = json.loads(w["roles_given"])
            roles_text = " ".join(f"<@&{rid}>" for rid in roles_given) if roles_given else "—"
            lines.append(
                f"**#{w['id']}** | {name} | Мод: {mod_name}\n"
                f"Причина: {w['reason']} | Роли: {roles_text}\n"
                f"Дата: {w['created_at']}"
            )

        embed.description = "\n\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class WarningCog(commands.Cog, name="Warning"):
    """Система штрафов с выдачей ролей."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.warning_group = WarningGroup()

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.warning_group)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WarningCog(bot))
