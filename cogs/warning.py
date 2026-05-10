"""
Модуль Warning — система штрафов с выбором роли из списка.

Логика:
  1. /warning set @role  — добавить/убрать роль из списка доступных ролей штрафа
  2. /warning add @user  — показывает dropdown с ролями из списка,
     после выбора роли — штраф + роль выдаётся пользователю

Команды (подкоманды группы /warning):
  /warning set    @role                   — настроить список ролей штрафа
  /warning add    @user [причина]         — выдать штраф (выбор роли из dropdown)
  /warning remove <id>                    — убрать штраф по ID
  /warning list   [@user]                 — список штрафов
"""

from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import config


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _is_admin(interaction: discord.Interaction) -> bool:
    """Проверка: пользователь имеет одну из админ-ролей."""
    if interaction.user.guild_permissions.administrator:
        return True
    user_roles = {r.name for r in interaction.user.roles}
    return bool(user_roles & set(config.ADMIN_ROLES))


# ---------------------------------------------------------------------------
# Select-меню выбора роли при /warning add
# ---------------------------------------------------------------------------

class WarningRoleSelect(discord.ui.Select):
    """Dropdown для выбора роли штрафа из настроенного списка."""

    def __init__(
        self,
        roles: list[discord.Role],
        target_user: discord.Member,
        moderator: discord.Member,
        reason: str,
    ) -> None:
        options = [
            discord.SelectOption(
                label=role.name,
                value=str(role.id),
                description=f"Выдать роль {role.name}",
            )
            for role in roles[:25]  # Discord лимит — 25 опций
        ]

        super().__init__(
            placeholder="Выберите роль штрафа...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="warning_role_select",
        )
        self.target_user = target_user
        self.moderator = moderator
        self.reason = reason
        self.available_roles = roles

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_role_id = int(self.values[0])
        role = interaction.guild.get_role(selected_role_id)  # type: ignore

        if not role:
            await interaction.response.send_message(
                "❌ Роль не найдена на сервере.", ephemeral=True
            )
            return

        # Выдаём роль пользователю
        roles_given: list[int] = []
        if role not in self.target_user.roles:
            try:
                await self.target_user.add_roles(role, reason=f"Штраф: {self.reason}")
                roles_given.append(role.id)
            except discord.Forbidden:
                await interaction.response.send_message(
                    f"❌ У бота нет прав выдать роль {role.mention}. "
                    "Проверьте, что роль бота выше этой роли.",
                    ephemeral=True,
                )
                return

        # Записываем штраф в БД
        wid = await db.warning_add(
            user_id=self.target_user.id,
            guild_id=interaction.guild_id,  # type: ignore
            moderator_id=self.moderator.id,
            reason=self.reason,
            roles_given=roles_given,
        )

        count = await db.warning_count(interaction.guild_id, self.target_user.id)  # type: ignore

        embed = discord.Embed(title="⚠️ Штраф выдан", color=config.EMBED_COLOR)
        embed.add_field(name="Пользователь", value=self.target_user.mention, inline=True)
        embed.add_field(name="Модератор", value=self.moderator.mention, inline=True)
        embed.add_field(name="Роль штрафа", value=role.mention, inline=True)
        embed.add_field(name="Причина", value=self.reason, inline=False)
        embed.add_field(name="ID штрафа", value=f"#{wid}", inline=True)
        embed.add_field(name="Всего штрафов", value=str(count), inline=True)

        if config.MAX_WARNINGS > 0 and count >= config.MAX_WARNINGS:
            embed.add_field(
                name="🚨 Достигнут лимит штрафов!",
                value=f"Пользователь набрал {count} штрафов (лимит: {config.MAX_WARNINGS}).",
                inline=False,
            )

        # Отвечаем на interaction select-меню
        await interaction.response.edit_message(
            content=None, embed=embed, view=None
        )


class WarningRoleSelectView(discord.ui.View):
    """View с dropdown-меню выбора роли."""

    def __init__(
        self,
        roles: list[discord.Role],
        target_user: discord.Member,
        moderator: discord.Member,
        reason: str,
    ) -> None:
        super().__init__(timeout=60)
        self.add_item(WarningRoleSelect(roles, target_user, moderator, reason))

    async def on_timeout(self) -> None:
        """Отключаем просроченное меню."""
        for item in self.children:
            item.disabled = True  # type: ignore


# ---------------------------------------------------------------------------
# Группа подкоманд /warning
# ---------------------------------------------------------------------------

class WarningGroup(app_commands.Group, name="warning", description="Управление штрафами"):
    """Группа подкоманд /warning set | add | remove | list"""

    # ------------------------------------------------------------------
    # /warning set @role
    # ------------------------------------------------------------------

    @app_commands.command(name="set", description="Добавить/убрать роль из списка ролей штрафа")
    @app_commands.describe(role="Роль для добавления или удаления из списка")
    async def warning_set(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        # Получаем текущий список ролей
        current_ids = await db.warning_get_roles(interaction.guild_id)  # type: ignore

        if role.id in current_ids:
            # Убираем роль из списка
            current_ids.remove(role.id)
            await db.warning_set_roles(interaction.guild_id, current_ids)  # type: ignore
            embed = discord.Embed(
                title="🗑 Роль убрана из списка",
                description=f"{role.mention} удалена из списка ролей штрафа.",
                color=config.EMBED_COLOR,
            )
        else:
            # Добавляем роль в список
            current_ids.append(role.id)
            await db.warning_set_roles(interaction.guild_id, current_ids)  # type: ignore
            embed = discord.Embed(
                title="✅ Роль добавлена в список",
                description=f"{role.mention} добавлена в список ролей штрафа.",
                color=config.EMBED_COLOR,
            )

        # Показываем текущий список
        if current_ids:
            mentions = " ".join(f"<@&{rid}>" for rid in current_ids)
            embed.add_field(name="📋 Текущий список ролей", value=mentions, inline=False)
        else:
            embed.add_field(
                name="📋 Список пуст",
                value="Добавьте роли: `/warning set @role`",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /warning add @user [причина]
    # ------------------------------------------------------------------

    @app_commands.command(name="add", description="Выдать штраф пользователю (выбор роли из списка)")
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

        # Получаем список настроенных ролей
        warn_role_ids = await db.warning_get_roles(interaction.guild_id)  # type: ignore

        if not warn_role_ids:
            await interaction.response.send_message(
                "❌ Список ролей штрафа пуст! Сначала настройте роли: `/warning set @role`",
                ephemeral=True,
            )
            return

        # Фильтруем — оставляем только существующие на сервере роли
        available_roles: list[discord.Role] = []
        for rid in warn_role_ids:
            role = interaction.guild.get_role(rid)  # type: ignore
            if role:
                available_roles.append(role)

        if not available_roles:
            await interaction.response.send_message(
                "❌ Ни одна из настроенных ролей не найдена на сервере. "
                "Обновите список: `/warning set @role`",
                ephemeral=True,
            )
            return

        # Показываем dropdown с ролями
        view = WarningRoleSelectView(available_roles, user, interaction.user, reason)

        embed = discord.Embed(
            title="⚠️ Выберите роль штрафа",
            description=(
                f"**Пользователь:** {user.mention}\n"
                f"**Причина:** {reason}\n\n"
                "Выберите роль из списка ниже:"
            ),
            color=config.EMBED_COLOR,
        )

        await interaction.response.send_message(embed=embed, view=view)

    # ------------------------------------------------------------------
    # /warning remove <id>
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
    # /warning list [@user]
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


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class WarningCog(commands.Cog, name="Warning"):
    """Система штрафов с выдачей ролей."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.warning_group = WarningGroup()

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.warning_group)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WarningCog(bot))
