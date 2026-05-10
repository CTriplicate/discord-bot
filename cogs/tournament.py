"""
Модуль Tournament — создание турниров, команд, анкет, сетки и матчей.

Команды:
  /createlobby   — создание турнира/группы (1v1, 2v2, 3v3, командное ДМ, и т.д.)
  /lobbylist     — список всех команд + картинка сетки (с кнопкой «Обновить»)
  /deleteteam    — удаление команды
  /setwinner     — установить победителя матча
  /startmatch    — запуск матча

Дополнительные (вспомогательные):
  /joinlobby     — присоединиться к турниру (заполнить анкету)
  /approveteam   — одобрить команду
  /generatebracket — сгенерировать сетку турнира
"""

from __future__ import annotations

import asyncio
import json
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import config
from utils.bracket import generate_bracket, generate_bracket_simple


# ---------------------------------------------------------------------------
# Кнопка «Обновить» для lobbylist
# ---------------------------------------------------------------------------

class RefreshBracketButton(discord.ui.Button):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="🔄 Обновить",
            custom_id=f"refresh_bracket:{tournament_id}",
        )
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await _send_bracket(interaction, self.tournament_id, edit=True)


class BracketView(discord.ui.View):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(RefreshBracketButton(tournament_id))


# ---------------------------------------------------------------------------
# Модальное окно анкеты
# ---------------------------------------------------------------------------

class ApplicationModal(discord.ui.Modal, title="Анкета для турнира"):
    def __init__(self, tournament_id: int, criteria: str, team_name: str = "") -> None:
        super().__init__()
        self.tournament_id = tournament_id
        self.criteria = criteria

        # Динамически создаём поля на основе критериев
        questions = [q.strip() for q in criteria.split("|") if q.strip()]
        if not questions:
            questions = ["Расскажите о себе"]

        for i, q in enumerate(questions[:5]):  # Максимум 5 полей
            self.add_item(
                discord.ui.TextInput(
                    label=q[:45],
                    placeholder=q,
                    max_length=500,
                    required=True,
                    custom_id=f"question_{i}",
                )
            )

        # Имя команды (если team_size > 1)
        self._team_name_input = None  # Будет добавлено ниже если нужно

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answers: dict[str, str] = {}
        for child in self.children:
            if isinstance(child, discord.ui.TextInput) and child.custom_id and child.custom_id.startswith("question_"):
                answers[child.label] = child.value

        # Проверяем, не подавал ли уже заявку
        existing = await db.application_list(self.tournament_id)
        if any(a["user_id"] == interaction.user.id and a["status"] == "pending" for a in existing):
            await interaction.response.send_message(
                "⚠️ Вы уже подали заявку на этот турнир.", ephemeral=True
            )
            return

        app_id = await db.application_create(
            tournament_id=self.tournament_id,
            user_id=interaction.user.id,
            answers=answers,
        )

        await interaction.response.send_message(
            f"✅ Заявка #{app_id} отправлена! Ожидайте одобрения от администрации.",
            ephemeral=True,
        )


class JoinLobbyButton(discord.ui.Button):
    def __init__(self, tournament_id: int, criteria: str) -> None:
        super().__init__(
            style=discord.ButtonStyle.success,
            label="📝 Подать заявку",
            custom_id=f"join_lobby:{tournament_id}",
        )
        self.tournament_id = tournament_id
        self.criteria = criteria

    async def callback(self, interaction: discord.Interaction) -> None:
        modal = ApplicationModal(self.tournament_id, self.criteria)
        await interaction.response.send_modal(modal)


class LobbyView(discord.ui.View):
    def __init__(self, tournament_id: int, criteria: str) -> None:
        super().__init__(timeout=None)
        self.add_item(JoinLobbyButton(tournament_id, criteria))


# ---------------------------------------------------------------------------
# Helper: отправка картинки сетки
# ---------------------------------------------------------------------------

async def _send_bracket(
    interaction: discord.Interaction,
    tournament_id: int,
    edit: bool = False,
) -> None:
    tournament = await db.tournament_get(tournament_id)
    if not tournament:
        msg = "❌ Турнир не найден."
        if edit:
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return

    teams = await db.team_list(tournament_id)
    matches = await db.match_list(tournament_id)

    # Генерируем картинку
    if matches:
        buf = generate_bracket(teams, matches, tournament["name"])
    else:
        buf = generate_bracket_simple(teams, tournament["name"])

    file = discord.File(buf, filename="bracket.png")

    embed = discord.Embed(
        title=f"🏆 {tournament['name']}",
        color=config.EMBED_COLOR,
    )
    embed.set_image(url="attachment://bracket.png")
    embed.add_field(name="Команд", value=str(len(teams)), inline=True)
    embed.add_field(name="Матчей", value=str(len(matches)), inline=True)
    embed.add_field(name="Статус", value=tournament["status"], inline=True)

    view = BracketView(tournament_id)

    if edit:
        await interaction.followup.edit_message(
            message_id=interaction.message.id,  # type: ignore
            embed=embed,
            attachments=[file],
            view=view,
        )
    else:
        await interaction.response.send_message(embed=embed, file=file, view=view)


# ---------------------------------------------------------------------------
# Helper: генерация матчей (Single Elimination bracket)
# ---------------------------------------------------------------------------

async def _generate_matches(tournament_id: int) -> None:
    """Генерирует матчи для Single Elimination на основе одобренных команд."""
    teams = await db.team_list(tournament_id)
    approved = [t for t in teams if t["approved"]]

    if len(approved) < 2:
        return

    # Очищаем старые матчи
    existing = await db.match_list(tournament_id)
    # (не удаляем завершённые матчи — только pending)

    # Для SE нужно количество команд = степень двойки.
    # Если не хватает — добавляем "bye" (пропуск).
    n = len(approved)
    import math
    next_pow2 = 2 ** math.ceil(math.log2(n))

    # Bye-команды получают автоматический пропуск в следующий раунд
    bye_count = next_pow2 - n

    # Жеребьёвка — перемешиваем команды
    import random
    seeds = list(range(n))
    random.shuffle(seeds)

    round1_match_count = next_pow2 // 2
    match_idx = 0

    for i in range(round1_match_count):
        seed1 = seeds[i * 2] if i * 2 < n else None
        seed2 = seeds[i * 2 + 1] if i * 2 + 1 < n else None

        team1_id = approved[seed1]["id"] if seed1 is not None else 0
        team2_id = approved[seed2]["id"] if seed2 is not None else 0

        if team1_id and not team2_id:
            # Bye — команда 1 автоматически проходит
            await db.match_create(
                tournament_id, team1_id, 0, 1, match_idx
            )
        elif not team1_id and team2_id:
            await db.match_create(
                tournament_id, team2_id, 0, 1, match_idx
            )
        elif team1_id and team2_id:
            await db.match_create(
                tournament_id, team1_id, team2_id, 1, match_idx
            )

        match_idx += 1

    # Создаём пустые слоты для следующих раундов
    rounds_count = int(math.log2(next_pow2))
    for r in range(2, rounds_count + 1):
        matches_in_round = next_pow2 // (2 ** r)
        for m in range(matches_in_round):
            await db.match_create(tournament_id, 0, 0, r, m)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class TournamentCog(commands.Cog, name="Tournament"):
    """Система турниров: создание, команды, анкеты, сетка, матчи."""

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
    # /createlobby
    # ------------------------------------------------------------------

    @app_commands.command(
        name="createlobby",
        description="Создать турнир/группу",
    )
    @app_commands.describe(
        name="Название турнира",
        team_size="Размер команды: 1 (1v1), 2 (2v2), 3 (3v3), и т.д.",
        is_team_dm="Командный дм? (true/false)",
        max_teams="Макс. кол-во команд (0 = без лимита)",
        criteria="Критерии/вопросы анкеты (через |)",
    )
    async def createlobby(
        self,
        interaction: discord.Interaction,
        name: str,
        team_size: int = 1,
        is_team_dm: bool = False,
        max_teams: int = 0,
        criteria: str = "",
    ) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        if team_size < 1:
            team_size = 1
        if team_size > 10:
            await interaction.response.send_message(
                "❌ Максимальный размер команды — 10.", ephemeral=True
            )
            return

        tid = await db.tournament_create(
            guild_id=interaction.guild_id,  # type: ignore
            channel_id=interaction.channel_id,
            name=name,
            team_size=team_size,
            is_team_dm=is_team_dm,
            max_teams=max_teams,
            criteria=criteria,
        )

        size_str = f"{team_size}v{team_size}" if team_size > 1 else "1v1"
        mode_str = " (Командное ДМ)" if is_team_dm else ""

        embed = discord.Embed(
            title=f"🏆 Турнир создан: {name}",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="Формат", value=f"{size_str}{mode_str}", inline=True)
        embed.add_field(name="ID турнира", value=f"#{tid}", inline=True)
        embed.add_field(
            name="Макс. команд",
            value=str(max_teams) if max_teams > 0 else "Без лимита",
            inline=True,
        )

        if criteria:
            embed.add_field(
                name="📋 Критерии / Вопросы анкеты",
                value=criteria.replace("|", "\n• "),
                inline=False,
            )

        # Добавляем кнопку «Подать заявку»
        view = LobbyView(tid, criteria)
        await interaction.response.send_message(embed=embed, view=view)

    # ------------------------------------------------------------------
    # /lobbylist
    # ------------------------------------------------------------------

    @app_commands.command(
        name="lobbylist",
        description="Список команд турнира + картинка сетки",
    )
    @app_commands.describe(
        tournament_id="ID турнира",
    )
    async def lobbylist(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
    ) -> None:
        await _send_bracket(interaction, tournament_id)

    # ------------------------------------------------------------------
    # /deleteteam
    # ------------------------------------------------------------------

    @app_commands.command(
        name="deleteteam",
        description="Удалить команду из турнира",
    )
    @app_commands.describe(
        team_id="ID команды для удаления",
    )
    async def deleteteam(
        self,
        interaction: discord.Interaction,
        team_id: int,
    ) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        team = await db.team_get(team_id)
        if not team:
            await interaction.response.send_message(
                f"❌ Команда #{team_id} не найдена.", ephemeral=True
            )
            return

        deleted = await db.team_delete(team_id)
        if deleted:
            embed = discord.Embed(
                title="🗑 Команда удалена",
                description=f"**{team['name']}** (#{team_id}) удалена из турнира.",
                color=config.EMBED_COLOR,
            )
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                f"❌ Не удалось удалить команду #{team_id}.", ephemeral=True
            )

    # ------------------------------------------------------------------
    # /setwinner
    # ------------------------------------------------------------------

    @app_commands.command(
        name="setwinner",
        description="Установить победителя матча",
    )
    @app_commands.describe(
        match_id="ID матча",
        winner_team_id="ID команды-победителя",
    )
    async def setwinner(
        self,
        interaction: discord.Interaction,
        match_id: int,
        winner_team_id: int,
    ) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        matches = await db.match_list(0)  # Получим все
        # Лучше искать напрямую
        from database import _connection, aiosqlite
        async with _connection() as db_conn:
            db_conn.row_factory = aiosqlite.Row
            rows = await db_conn.execute_fetchall(
                "SELECT * FROM matches WHERE id = ?", (match_id,)
            )

        if not rows:
            await interaction.response.send_message(
                f"❌ Матч #{match_id} не найден.", ephemeral=True
            )
            return

        match_data = dict(rows[0])

        # Проверяем, что winner — один из участников матча
        if winner_team_id not in (match_data["team1_id"], match_data["team2_id"]):
            await interaction.response.send_message(
                "❌ Указанная команда не участвует в этом матче.", ephemeral=True
            )
            return

        await db.match_set_winner(match_id, winner_team_id)

        winner_team = await db.team_get(winner_team_id)
        winner_name = winner_team["name"] if winner_team else "???"

        embed = discord.Embed(
            title="🏆 Победитель установлен",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="Матч", value=f"#{match_id}", inline=True)
        embed.add_field(name="Победитель", value=winner_name, inline=True)

        # Продвигаем победителя в следующий раунд
        tournament_id = match_data["tournament_id"]
        next_round = match_data["round"] + 1
        next_match_idx = match_data["match_index"] // 2

        # Ищем или создаём матч следующего раунда
        next_matches = await db.match_list(tournament_id)
        next_match = next(
            (m for m in next_matches if m["round"] == next_round and m["match_index"] == next_match_idx),
            None,
        )

        if next_match:
            # Ставим победителя в свободный слот
            if next_match["team1_id"] == 0:
                from database import _connection as _conn2
                async with _conn2() as db2:
                    await db2.execute(
                        "UPDATE matches SET team1_id = ? WHERE id = ?",
                        (winner_team_id, next_match["id"]),
                    )
                    await db2.commit()
            elif next_match["team2_id"] == 0:
                from database import _connection as _conn3
                async with _conn3() as db3:
                    await db3.execute(
                        "UPDATE matches SET team2_id = ? WHERE id = ?",
                        (winner_team_id, next_match["id"]),
                    )
                    await db3.commit()
        else:
            # Создаём матч следующего раунда
            await db.match_create(tournament_id, winner_team_id, 0, next_round, next_match_idx)

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /startmatch
    # ------------------------------------------------------------------

    @app_commands.command(
        name="startmatch",
        description="Запустить матч",
    )
    @app_commands.describe(
        match_id="ID матча для запуска",
    )
    async def startmatch(
        self,
        interaction: discord.Interaction,
        match_id: int,
    ) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        from database import _connection, aiosqlite
        async with _connection() as db_conn:
            db_conn.row_factory = aiosqlite.Row
            rows = await db_conn.execute_fetchall(
                "SELECT * FROM matches WHERE id = ?", (match_id,)
            )

        if not rows:
            await interaction.response.send_message(
                f"❌ Матч #{match_id} не найден.", ephemeral=True
            )
            return

        match_data = dict(rows[0])

        if match_data["status"] != "pending":
            await interaction.response.send_message(
                f"❌ Матч #{match_id} уже {'идёт' if match_data['status'] == 'playing' else 'завершён'}.",
                ephemeral=True,
            )
            return

        if not match_data["team1_id"] or not match_data["team2_id"]:
            await interaction.response.send_message(
                "❌ В матче не хватает участников (TBD).", ephemeral=True
            )
            return

        await db.match_set_status(match_id, "playing")

        team1 = await db.team_get(match_data["team1_id"])
        team2 = await db.team_get(match_data["team2_id"])

        t1_name = team1["name"] if team1 else "???"
        t2_name = team2["name"] if team2 else "???"

        # Упоминания участников
        t1_mentions = ""
        t2_mentions = ""
        if team1:
            members = json.loads(team1["members"])
            t1_mentions = " ".join(f"<@{uid}>" for uid in members)
        if team2:
            members = json.loads(team2["members"])
            t2_mentions = " ".join(f"<@{uid}>" for uid in members)

        embed = discord.Embed(
            title="⚔️ Матч начат!",
            description=f"**{t1_name}** vs **{t2_name}**",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name=t1_name, value=t1_mentions or "—", inline=True)
        embed.add_field(name=t2_name, value=t2_mentions or "—", inline=True)
        embed.add_field(name="Матч ID", value=f"#{match_id}", inline=True)

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /approveteam
    # ------------------------------------------------------------------

    @app_commands.command(
        name="approveteam",
        description="Одобрить команду для участия в турнире",
    )
    @app_commands.describe(
        team_id="ID команды",
    )
    async def approveteam(
        self,
        interaction: discord.Interaction,
        team_id: int,
    ) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        team = await db.team_get(team_id)
        if not team:
            await interaction.response.send_message(
                f"❌ Команда #{team_id} не найдена.", ephemeral=True
            )
            return

        await db.team_set_approved(team_id, True)

        embed = discord.Embed(
            title="✅ Команда одобрена",
            description=f"**{team['name']}** (#{team_id}) допущена к турниру!",
            color=config.EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /joinlobby
    # ------------------------------------------------------------------

    @app_commands.command(
        name="joinlobby",
        description="Присоединиться к турниру (подать заявку / создать команду)",
    )
    @app_commands.describe(
        tournament_id="ID турнира",
        team_name="Название команды (для 2v2+)",
        members="Участники команды через пробел (для 2v2+)",
    )
    async def joinlobby(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
        team_name: str = "",
        members: str = "",
    ) -> None:
        tournament = await db.tournament_get(tournament_id)
        if not tournament:
            await interaction.response.send_message(
                f"❌ Турнир #{tournament_id} не найден.", ephemeral=True
            )
            return

        if tournament["status"] != "open":
            await interaction.response.send_message(
                "❌ Набор на этот турнир закрыт.", ephemeral=True
            )
            return

        # Проверяем лимит команд
        existing_teams = await db.team_list(tournament_id)
        if tournament["max_teams"] > 0 and len(existing_teams) >= tournament["max_teams"]:
            await interaction.response.send_message(
                "❌ Достигнут лимит команд на этом турнире.", ephemeral=True
            )
            return

        team_size = tournament["team_size"]

        # Для solo (1v1) — автоматически создаём команду из одного участника
        if team_size == 1:
            # Проверяем, не состоит ли уже в команде
            for t in existing_teams:
                member_ids = json.loads(t["members"])
                if interaction.user.id in member_ids:
                    await interaction.response.send_message(
                        "⚠️ Вы уже состоите в команде на этом турнире.", ephemeral=True
                    )
                    return

            team_name_final = team_name or interaction.user.display_name
            tid = await db.team_create(tournament_id, team_name_final, [interaction.user.id])
            await db.team_set_approved(tid, True)  # Автоодобрение для 1v1

            await interaction.response.send_message(
                f"✅ Вы записаны на турнир **{tournament['name']}** как **{team_name_final}**!",
                ephemeral=True,
            )
        else:
            # Командный турнир — нужен список участников
            if not team_name:
                await interaction.response.send_message(
                    f"❌ Для {team_size}v{team_size} укажите название команды: "
                    f"`/joinlobby {tournament_id} <название> @участники`",
                    ephemeral=True,
                )
                return

            # Парсим участников
            import re
            member_ids: list[int] = [interaction.user.id]  # Создатель всегда в команде
            for match in re.findall(r"<@!?(\d+)>", members):
                member_ids.append(int(match))
            for part in members.split():
                if part.isdigit():
                    member_ids.append(int(part))

            # Убираем дубликаты
            member_ids = list(dict.fromkeys(member_ids))

            if len(member_ids) < team_size:
                await interaction.response.send_message(
                    f"❌ Для формата {team_size}v{team_size} нужно минимум {team_size} участников. "
                    f"Указано: {len(member_ids)}.",
                    ephemeral=True,
                )
                return

            # Обрезаем до нужного размера
            member_ids = member_ids[:team_size]

            # Проверяем, не состоит ли кто-то уже в команде
            for t in existing_teams:
                existing_members = json.loads(t["members"])
                overlap = set(member_ids) & set(existing_members)
                if overlap:
                    overlap_mentions = " ".join(f"<@{uid}>" for uid in overlap)
                    await interaction.response.send_message(
                        f"⚠️ {overlap_mentions} уже состоит в команде **{t['name']}** на этом турнире.",
                        ephemeral=True,
                    )
                    return

            tid = await db.team_create(tournament_id, team_name, member_ids)

            embed = discord.Embed(
                title="📋 Заявка на участие",
                description=f"**{team_name}** — ожидает одобрения администрации.",
                color=config.EMBED_COLOR,
            )
            embed.add_field(
                name="Участники",
                value=" ".join(f"<@{uid}>" for uid in member_ids),
                inline=False,
            )
            embed.add_field(name="ID команды", value=f"#{tid}", inline=True)
            await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /generatebracket
    # ------------------------------------------------------------------

    @app_commands.command(
        name="generatebracket",
        description="Сгенерировать турнирную сетку (Single Elimination)",
    )
    @app_commands.describe(
        tournament_id="ID турнира",
    )
    async def generatebracket(
        self,
        interaction: discord.Interaction,
        tournament_id: int,
    ) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.", ephemeral=True
            )
            return

        tournament = await db.tournament_get(tournament_id)
        if not tournament:
            await interaction.response.send_message(
                f"❌ Турнир #{tournament_id} не найден.", ephemeral=True
            )
            return

        teams = await db.team_list(tournament_id)
        approved = [t for t in teams if t["approved"]]

        if len(approved) < 2:
            await interaction.response.send_message(
                "❌ Нужно минимум 2 одобренные команды для генерации сетки.",
                ephemeral=True,
            )
            return

        await _generate_matches(tournament_id)
        await db.tournament_set_status(tournament_id, "closed")

        await interaction.response.send_message(
            f"✅ Сетка турнира **{tournament['name']}** сгенерирована! "
            f"Одобренных команд: {len(approved)}. Используйте `/lobbylist {tournament_id}` для просмотра."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TournamentCog(bot))
