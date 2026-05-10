"""
Модуль Roll — розыгрыш приза среди участников.

Команды:
  /roll start <приз> <время_сек>   — начать розыгрыш (30–172800 сек)
  /roll emergency <roll_id>         — аварийный ролл (сразу определить победителя)
  /roll delete <roll_id>            — удалить розыгрыш

Механика:
  - При создании ролла бот отправляет embed с кнопкой «Участвовать».
  - По истечении времени бот случайным образом выбирает победителя (как в казино,
    без предугадания — используется secrets.choice).
  - Аварийный ролл позволяет завершить розыгрыш досрочно.
"""

from __future__ import annotations

import asyncio
import secrets
import time

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import config


# ---------------------------------------------------------------------------
# Кнопка «Участвовать»
# ---------------------------------------------------------------------------

class ParticipateButton(discord.ui.Button):
    """Кнопка для участия в розыгрыше."""

    def __init__(self, roll_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.success,
            label="🎉 Участвовать",
            custom_id=f"roll_participate:{roll_id}",
        )
        self.roll_id = roll_id

    async def callback(self, interaction: discord.Interaction) -> None:
        # Проверяем, активен ли ещё ролл
        roll = await db.roll_get(self.roll_id)
        if not roll or not roll["active"]:
            await interaction.response.send_message(
                "❌ Этот розыгрыш уже завершён или удалён.", ephemeral=True
            )
            self.disabled = True
            return

        added = await db.roll_participant_add(self.roll_id, interaction.user.id)
        if added:
            count = len(await db.roll_participants_list(self.roll_id))
            await interaction.response.send_message(
                f"✅ Вы участвуете в розыгрыше! Участников: {count}", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "⚠️ Вы уже участвуете в этом розыгрыше.", ephemeral=True
            )


class LeaveButton(discord.ui.Button):
    """Кнопка для выхода из розыгрыша."""

    def __init__(self, roll_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="❌ Отмена",
            custom_id=f"roll_leave:{roll_id}",
        )
        self.roll_id = roll_id

    async def callback(self, interaction: discord.Interaction) -> None:
        removed = await db.roll_participant_remove(self.roll_id, interaction.user.id)
        if removed:
            count = len(await db.roll_participants_list(self.roll_id))
            await interaction.response.send_message(
                f"✅ Вы вышли из розыгрыша. Участников: {count}", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "⚠️ Вы не участвуете в этом розыгрыше.", ephemeral=True
            )


class RollView(discord.ui.View):
    """View с кнопками участия и выхода."""

    def __init__(self, roll_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(ParticipateButton(roll_id))
        self.add_item(LeaveButton(roll_id))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class RollCog(commands.Cog, name="Roll"):
    """Розыгрыш призов среди участников."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._tasks: dict[int, asyncio.Task] = {}  # roll_id -> Task

    async def cog_load(self) -> None:
        """Восстанавливаем незавершённые роллы при перезапуске бота."""
        # Проходим по всем гильдиям и ищем активные роллы
        # (нужно, чтобы бот не «забыл» о роллах после рестарта)
        for guild in self.bot.guilds:
            active_rolls = await db.roll_get_active(guild.id)
            for roll in active_rolls:
                remaining = roll["end_time"] - time.time()
                if remaining <= 0:
                    # Время уже вышло — сразу роллим
                    asyncio.create_task(self._conclude_roll(roll["id"]))
                else:
                    # Планируем завершение
                    self._schedule_roll(roll["id"], remaining)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_admin(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        user_roles = {r.name for r in interaction.user.roles}
        return bool(user_roles & set(config.ADMIN_ROLES))

    def _schedule_roll(self, roll_id: int, delay: float) -> None:
        """Планирует задачу на завершение ролла."""
        if roll_id in self._tasks:
            self._tasks[roll_id].cancel()
        self._tasks[roll_id] = asyncio.create_task(self._roll_worker(roll_id, delay))

    async def _roll_worker(self, roll_id: int, delay: float) -> None:
        """Ждёт указанное время и завершает ролл."""
        try:
            await asyncio.sleep(delay)
            await self._conclude_roll(roll_id)
        except asyncio.CancelledError:
            pass

    async def _conclude_roll(self, roll_id: int) -> None:
        """Определяет победителя и отправляет результат."""
        roll = await db.roll_get(roll_id)
        if not roll or not roll["active"]:
            return

        await db.roll_finish(roll_id)

        participants = await db.roll_participants_list(roll_id)
        channel = self.bot.get_channel(roll["channel_id"])
        if not channel:
            return

        # Формируем embed результата
        embed = discord.Embed(
            title="🎉 Розыгрыш завершён!",
            description=f"**Приз:** {roll['prize_text']}",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="Участников", value=str(len(participants)), inline=True)

        if not participants:
            embed.add_field(
                name="Результат",
                value="❌ Никто не участвовал — приз не разыгран.",
                inline=False,
            )
        else:
            # Казино-ролл: абсолютно случайный выбор через secrets
            winner_id = secrets.choice(participants)
            embed.add_field(
                name="🏆 Победитель",
                value=f"<@{winner_id}>",
                inline=False,
            )

        # Обновляем исходное сообщение
        if roll["message_id"]:
            try:
                msg = await channel.fetch_message(roll["message_id"])
                # Отключаем кнопки
                view = RollView(roll_id)
                for item in view.children:
                    item.disabled = True
                await msg.edit(view=view)
            except discord.NotFound:
                pass

        await channel.send(embed=embed)

        # Очистка задачи
        self._tasks.pop(roll_id, None)

    # ------------------------------------------------------------------
    # /roll start
    # ------------------------------------------------------------------

    @app_commands.command(name="roll", description="Розыгрыш призов")
    @app_commands.describe(
        action="Действие: start, emergency, delete",
        prize="Описание приза",
        time_seconds="Время до результата (30–172800 сек)",
        roll_id="ID розыгрыша (для emergency / delete)",
    )
    async def roll(
        self,
        interaction: discord.Interaction,
        action: str,
        prize: str | None = None,
        time_seconds: int | None = None,
        roll_id: int | None = None,
    ) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        action = action.lower().strip()
        if action == "start":
            await self._start(interaction, prize, time_seconds)
        elif action == "emergency":
            await self._emergency(interaction, roll_id)
        elif action == "delete":
            await self._delete(interaction, roll_id)
        else:
            await interaction.response.send_message(
                "❌ Неизвестное действие. Используйте: `start`, `emergency`, `delete`.",
                ephemeral=True,
            )

    async def _start(
        self,
        interaction: discord.Interaction,
        prize: str | None,
        time_seconds: int | None,
    ) -> None:
        if not prize:
            await interaction.response.send_message(
                "❌ Укажите приз: `/roll start <приз> <время_сек>`", ephemeral=True
            )
            return

        if time_seconds is None or time_seconds < 30 or time_seconds > 172800:
            await interaction.response.send_message(
                "❌ Время должно быть от 30 до 172800 секунд (48 часов).",
                ephemeral=True,
            )
            return

        end_time = time.time() + time_seconds

        # Создаём ролл в БД
        rid = await db.roll_create(
            guild_id=interaction.guild_id,  # type: ignore
            channel_id=interaction.channel_id,
            prize_text=prize,
            end_time=end_time,
        )

        # Формируем красивый embed
        hours = time_seconds // 3600
        minutes = (time_seconds % 3600) // 60
        secs = time_seconds % 60
        time_str = ""
        if hours:
            time_str += f"{hours}ч "
        if minutes:
            time_str += f"{minutes}м "
        if secs:
            time_str += f"{secs}с"

        embed = discord.Embed(
            title="🎉 Розыгрыш приза!",
            description=f"**Приз:** {prize}",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="⏱ Время", value=time_str.strip(), inline=True)
        embed.add_field(name="ID розыгрыша", value=f"#{rid}", inline=True)
        embed.set_footer(text="Нажмите кнопку ниже, чтобы участвовать!")

        view = RollView(rid)
        await interaction.response.send_message(embed=embed, view=view)

        # Сохраняем message_id
        msg = await interaction.original_response()
        await db.roll_set_message(rid, msg.id)

        # Планируем завершение
        self._schedule_roll(rid, float(time_seconds))

    async def _emergency(
        self,
        interaction: discord.Interaction,
        roll_id: int | None,
    ) -> None:
        if roll_id is None:
            # Если ID не указан — ищем активный ролл в канале
            active = await db.roll_get_active(interaction.guild_id)  # type: ignore
            channel_rolls = [r for r in active if r["channel_id"] == interaction.channel_id]
            if not channel_rolls:
                await interaction.response.send_message(
                    "❌ Нет активных розыгрышей в этом канале. Укажите ID: "
                    "`/roll emergency <roll_id>`",
                    ephemeral=True,
                )
                return
            roll_id = channel_rolls[0]["id"]

        roll = await db.roll_get(roll_id)
        if not roll or not roll["active"]:
            await interaction.response.send_message(
                f"❌ Розыгрыш #{roll_id} не найден или уже завершён.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"⚡ Аварийный ролл розыгрыша #{roll_id}! Определяем победителя...",
        )

        # Отменяем запланированную задачу
        if roll_id in self._tasks:
            self._tasks[roll_id].cancel()
            self._tasks.pop(roll_id, None)

        await self._conclude_roll(roll_id)

    async def _delete(
        self,
        interaction: discord.Interaction,
        roll_id: int | None,
    ) -> None:
        if roll_id is None:
            await interaction.response.send_message(
                "❌ Укажите ID розыгрыша: `/roll delete <roll_id>`", ephemeral=True
            )
            return

        roll = await db.roll_get(roll_id)
        if not roll:
            await interaction.response.send_message(
                f"❌ Розыгрыш #{roll_id} не найден.", ephemeral=True
            )
            return

        # Отменяем задачу
        if roll_id in self._tasks:
            self._tasks[roll_id].cancel()
            self._tasks.pop(roll_id, None)

        # Пытаемся удалить сообщение
        if roll["message_id"]:
            channel = self.bot.get_channel(roll["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(roll["message_id"])
                    await msg.delete()
                except discord.NotFound:
                    pass

        deleted = await db.roll_delete(roll_id)
        if deleted:
            await interaction.response.send_message(
                f"✅ Розыгрыш #{roll_id} удалён."
            )
        else:
            await interaction.response.send_message(
                f"❌ Не удалось удалить розыгрыш #{roll_id}.", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RollCog(bot))
