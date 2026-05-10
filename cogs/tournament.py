"""
Модуль Tournament — система турниров с анкетами и кнопочным управлением.

Регистрация (для участников):
  /1vs1           — записаться на 1v1 турнир
  /2vs2           — записаться на 2v2 турнир
  /3vs3           — записаться на 3v3 турнир
  /customlobby    — записаться на кастомный турнир (4v4 и больше)
  /bracket        — посмотреть сетку турнира (доступна всем)

Управление (для администрации):
  /tournament create            — создать турнир
  /tournament questions add     — добавить вопрос в анкету
  /tournament questions remove  — удалить вопрос из анкеты
  /tournament questions list    — список вопросов
  /tournament questions clear   — очистить все вопросы
  /tournament list              — список турниров сервера
  /tournament panel             — показать интерактивную панель
  /tournament delete            — удалить турнир
  /tournament logchannel        — установить канал для логов

Многошаговая регистрация:
  1v1:  Модалка (никнейм + до 4 вопросов) → кнопка «Продолжить» → ещё модалки по 5 вопросов → регистрация
  Командный: UserSelect (выбор участников) → Модалка (название + до 4 вопросов)
             → кнопка «Продолжить» → ещё модалки по 5 вопросов → регистрация
"""

from __future__ import annotations

import json
import math
import random
import re
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands

import database as db
import config
from utils.bracket import generate_bracket, generate_bracket_simple


# ===========================================================================
# HELPERS
# ===========================================================================

async def _is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    user_roles = {r.name for r in interaction.user.roles}
    return bool(user_roles & set(config.ADMIN_ROLES))


def _round_name(max_round: int, current_round: int) -> str:
    diff = max_round - current_round
    if diff == 0:
        return "Финал"
    if diff == 1:
        return "Полуфинал"
    if diff == 2:
        return "Четвертьфинал"
    return f"Раунд {current_round}"


def _format_str(team_size: int, is_team_dm: bool = False) -> str:
    if team_size == 1:
        return "1v1"
    s = f"{team_size}v{team_size}"
    if is_team_dm:
        s += " (Командное ДМ)"
    return s


async def _log_event(
    client: discord.Client,
    guild_id: int,
    title: str,
    description: str,
    color: int = 0x5865F2,
) -> None:
    """Отправляет embed в канал логов турнира (если настроен)."""
    cfg = await db.tournament_config_get(guild_id)
    if not cfg or not cfg.get("log_channel_id"):
        return
    channel = client.get_channel(cfg["log_channel_id"])
    if not channel or not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(title=title, description=description, color=color)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


# ===========================================================================
# SESSION — состояние многошаговой регистрации
# ===========================================================================

class RegistrationSession:
    """Хранит промежуточные данные между шагами регистрации."""

    def __init__(
        self,
        tournament_id: int,
        team_size: int,
        questions: list[dict],
        channel_id: int,
        panel_message_id: int,
    ) -> None:
        self.tournament_id = tournament_id
        self.team_size = team_size
        self.questions = questions
        self.channel_id = channel_id
        self.panel_message_id = panel_message_id
        self.team_name: str = ""
        self.members: list[int] = []
        self.answers: dict[str, str] = {}


# ===========================================================================
# ШАГ 1 (командный): ВЫБОР УЧАСТНИКОВ ЧЕРЕЗ UserSelect
# ===========================================================================

class MemberSelectView(discord.ui.View):
    """View с UserSelect для выбора участников команды."""

    def __init__(self, session: RegistrationSession) -> None:
        super().__init__(timeout=120)
        needed = session.team_size - 1
        self.add_item(MemberUserSelect(needed, session))
        self.add_item(CancelRegButton(session))


class MemberUserSelect(discord.ui.UserSelect):
    """Выпадающий список участников сервера для добавления в команду."""

    def __init__(self, needed: int, session: RegistrationSession) -> None:
        self.session = session
        super().__init__(
            placeholder=f"Выберите {needed} участников команды...",
            min_values=needed,
            max_values=needed,
            custom_id="member_user_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # Собираем выбранных участников
        selected_ids = [u.id for u in self.values]
        self.session.members = [interaction.user.id] + selected_ids

        # Проверяем пересечения с другими командами
        existing_teams = await db.team_list(self.session.tournament_id)
        for t in existing_teams:
            existing_members = json.loads(t["members"])
            overlap = set(self.session.members) & set(existing_members)
            if overlap:
                overlap_mentions = " ".join(f"<@{uid}>" for uid in overlap)
                await interaction.response.send_message(
                    f"⚠️ {overlap_mentions} уже в команде **{t['name']}**. Выберите других.",
                    ephemeral=True,
                )
                return

        # Следующий шаг — модалка с названием + вопросы
        await _show_first_modal(interaction, self.session)


class CancelRegButton(discord.ui.Button):
    """Кнопка отмены регистрации."""

    def __init__(self, session: RegistrationSession) -> None:
        self.session = session
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="❌ Отмена",
            custom_id="cancel_reg",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content="❌ Регистрация отменена.", view=None,
        )


# ===========================================================================
# КНОПКА ПРОДОЛЖЕНИЯ АНКЕТЫ (вместо цепочки модалок)
# ===========================================================================

class ContinueView(discord.ui.View):
    """View с кнопкой «Продолжить» для показа следующей порции вопросов."""

    def __init__(self, session: RegistrationSession) -> None:
        super().__init__(timeout=180)
        self.add_item(ContinueButton(session))
        self.add_item(CancelRegButton(session))


class ContinueButton(discord.ui.Button):
    """➡️ Продолжить — открывает следующую модалку с вопросами."""

    def __init__(self, session: RegistrationSession) -> None:
        self.session = session
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Продолжить ➡️",
            custom_id="continue_reg_btn",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # Считаем сколько вопросов осталось
        answered = len(self.session.answers)
        remaining = self.session.questions[answered:answered + 5]

        if not remaining:
            # Вопросов больше нет — финализируем
            await _finalize_registration(interaction, self.session)
            return

        modal = QuestionsOnlyModal(self.session, remaining)
        await interaction.response.send_modal(modal)


# ===========================================================================
# МОДАЛКИ — НАЗВАНИЕ + ВОПРОСЫ
# ===========================================================================

async def _show_first_modal(interaction: discord.Interaction, session: RegistrationSession) -> None:
    """Показывает первую модалку (название/никнейм + до 4 вопросов)."""
    # Discord лимит: максимум 5 TextInput на модалку
    # 1 поле = название/никнейм, остальные = вопросы
    max_q = 4  # 1 (название) + 4 (вопроса) = 5 полей

    remaining = session.questions[:max_q]
    modal = TeamNameQuestionsModal(session, remaining)
    await interaction.response.send_modal(modal)


async def _after_modal_submit(interaction: discord.Interaction, session: RegistrationSession) -> None:
    """Вызывается после сабмита модалки. Показывает «Продолжить» или финализирует."""
    answered = len(session.answers)
    remaining_total = len(session.questions) - answered

    if remaining_total <= 0:
        # Все вопросы отвечены — финализируем
        await _finalize_registration(interaction, session)
    else:
        # Есть ещё вопросы — показываем кнопку «Продолжить»
        view = ContinueView(session)
        embed = discord.Embed(
            title="📝 Анкета — продолжение",
            description=(
                f"Ответов записано: **{answered}/{len(session.questions)}**\n"
                f"Осталось вопросов: **{remaining_total}**\n\n"
                f"Нажмите **Продолжить**, чтобы ответить на оставшиеся вопросы."
            ),
            color=config.EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class TeamNameQuestionsModal(discord.ui.Modal, title="Регистрация на турнир"):
    """Первая модалка: название/никнейм + до 4 вопросов анкеты."""

    def __init__(self, session: RegistrationSession, questions: list[dict]) -> None:
        super().__init__()
        self.session = session
        self.questions = questions

        # Поле названия
        if session.team_size > 1:
            self.add_item(discord.ui.TextInput(
                label="Название команды",
                placeholder="Введите название вашей команды",
                max_length=50,
                required=True,
                custom_id="team_name",
            ))
        else:
            self.add_item(discord.ui.TextInput(
                label="Никнейм (или оставьте пустым)",
                placeholder="Будет использовано ваше имя в Discord",
                max_length=50,
                required=False,
                custom_id="team_name",
            ))

        # Вопросы (максимум 4 в первой модалке, чтобы всего было 5 полей)
        for i, q in enumerate(questions):
            self.add_item(discord.ui.TextInput(
                label=q["question_text"][:45],
                placeholder=q["question_text"],
                style=discord.TextStyle.paragraph,
                max_length=500,
                required=bool(q.get("required", 1)),
                custom_id=f"question_{i}",
            ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            # Собираем данные
            team_name_raw = ""
            for child in self.children:
                if not isinstance(child, discord.ui.TextInput):
                    continue
                if child.custom_id == "team_name":
                    team_name_raw = (child.value or "").strip()
                elif child.custom_id and child.custom_id.startswith("question_"):
                    self.session.answers[child.label] = child.value or ""

            # Сохраняем название
            if self.session.team_size > 1:
                if not team_name_raw:
                    await interaction.response.send_message(
                        "❌ Укажите название команды!", ephemeral=True
                    )
                    return
                self.session.team_name = team_name_raw
            else:
                self.session.team_name = team_name_raw or interaction.user.display_name
                self.session.members = [interaction.user.id]

            # Проверяем, что турнир ещё открыт
            tournament = await db.tournament_get(self.session.tournament_id)
            if not tournament or tournament["status"] != "open":
                await interaction.response.send_message(
                    "❌ Турнир закрыт для регистрации.", ephemeral=True
                )
                return

            # Следующий шаг: кнопка «Продолжить» или финализация
            await _after_modal_submit(interaction, self.session)
        except Exception as e:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"❌ Произошла ошибка при регистрации: {e}", ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"❌ Произошла ошибка при регистрации: {e}", ephemeral=True
                    )
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Обрабатывает ошибки модалки, чтобы Discord не зависал."""
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ Ошибка формы: {error}", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"❌ Ошибка формы: {error}", ephemeral=True
                )
        except Exception:
            pass


class QuestionsOnlyModal(discord.ui.Modal, title="Анкета — продолжение"):
    """Модалка с вопросами (по 5 штук). Вызывается по кнопке «Продолжить»."""

    def __init__(self, session: RegistrationSession, questions: list[dict]) -> None:
        super().__init__()
        self.session = session
        self.questions = questions

        remaining_count = len(session.questions) - len(session.answers)
        self.title = f"Анкета ({remaining_count} вопр. осталось)"

        for i, q in enumerate(questions):
            self.add_item(discord.ui.TextInput(
                label=q["question_text"][:45],
                placeholder=q["question_text"],
                style=discord.TextStyle.paragraph,
                max_length=500,
                required=bool(q.get("required", 1)),
                custom_id=f"question_{i}",
            ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            # Собираем ответы
            for child in self.children:
                if not isinstance(child, discord.ui.TextInput):
                    continue
                if child.custom_id and child.custom_id.startswith("question_"):
                    self.session.answers[child.label] = child.value or ""

            # Следующий шаг: кнопка «Продолжить» или финализация
            await _after_modal_submit(interaction, self.session)
        except Exception as e:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"❌ Произошла ошибка при регистрации: {e}", ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        f"❌ Произошла ошибка при регистрации: {e}", ephemeral=True
                    )
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Обрабатывает ошибки модалки, чтобы Discord не зависал."""
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ Ошибка формы: {error}", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"❌ Ошибка формы: {error}", ephemeral=True
                )
        except Exception:
            pass


# ===========================================================================
# ФИНАЛИЗАЦИЯ РЕГИСТРАЦИИ
# ===========================================================================

async def _finalize_registration(interaction: discord.Interaction, session: RegistrationSession) -> None:
    """Создаёт команду и анкету в БД, обновляет панель."""

    tournament = await db.tournament_get(session.tournament_id)
    if not tournament or tournament["status"] != "open":
        await interaction.response.send_message(
            "❌ Турнир закрыт для регистрации.", ephemeral=True
        )
        return

    # Проверяем лимит команд
    existing_teams = await db.team_list(session.tournament_id)
    if tournament["max_teams"] > 0 and len(existing_teams) >= tournament["max_teams"]:
        await interaction.response.send_message(
            "❌ Достигнут лимит команд.", ephemeral=True
        )
        return

    # Проверяем, не в команде ли уже
    for t in existing_teams:
        member_ids = json.loads(t["members"])
        if interaction.user.id in member_ids:
            await interaction.response.send_message(
                "⚠️ Вы уже состоите в команде на этом турнире.", ephemeral=True
            )
            return

    # Для 1v1 — автодобрение
    auto_approve = session.team_size == 1

    # Создаём команду
    tid = await db.team_create(session.tournament_id, session.team_name, session.members)
    if auto_approve:
        await db.team_set_approved(tid, True)

    # Сохраняем анкету
    if session.answers:
        await db.application_create(
            session.tournament_id, interaction.user.id, session.team_name, session.answers
        )

    # Ответ пользователю
    if auto_approve:
        await interaction.response.send_message(
            f"✅ Вы записаны как **{session.team_name}**!", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"✅ Команда **{session.team_name}** зарегистрирована! "
            f"Ожидайте одобрения от администрации.",
            ephemeral=True,
        )

    # Обновляем панель
    await _update_panel_by_tournament(interaction.client, session.tournament_id)  # type: ignore

    # Логируем регистрацию
    team_word = "Команда" if session.team_size > 1 else "Участник"
    team_ending = "а" if session.team_size > 1 else ""
    await _log_event(
        interaction.client, interaction.guild_id,
        "📝 Новая регистрация",
        f"{team_word} **{session.team_name}** зарегистрирован{team_ending} на турнир",
        color=config.EMBED_COLOR,
    )


# ===========================================================================
# ВЫБОР ТУРНИРА (dropdown при /1vs1, /2vs2 и т.д.)
# ===========================================================================

class TournamentSelectView(discord.ui.View):
    """View с dropdown для выбора турнира при регистрации."""

    def __init__(self, tournaments: list[dict], team_size: int) -> None:
        super().__init__(timeout=60)
        self.add_item(TournamentSelect(tournaments, team_size))


class TournamentSelect(discord.ui.Select):
    def __init__(self, tournaments: list[dict], team_size: int) -> None:
        self.team_size = team_size
        options = []
        for t in tournaments[:25]:
            fmt = _format_str(t["team_size"], t.get("is_team_dm", 0))
            label = f"{t['name']} (#{t['id']})"
            options.append(discord.SelectOption(
                label=label[:100],
                value=str(t["id"]),
                description=f"{fmt} | Статус: {t['status']}",
            ))
        super().__init__(
            placeholder="Выберите турнир...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="tournament_reg_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        tournament_id = int(self.values[0])
        tournament = await db.tournament_get(tournament_id)
        if not tournament or tournament["status"] != "open":
            await interaction.response.send_message(
                "❌ Турнир не найден или регистрация закрыта.", ephemeral=True
            )
            return

        await _start_registration(interaction, tournament)


async def _start_registration(interaction: discord.Interaction, tournament: dict) -> None:
    """Начинает процесс регистрации: UserSelect (для командных) или модалка (для 1v1)."""

    questions = await db.question_list(tournament["id"])

    session = RegistrationSession(
        tournament_id=tournament["id"],
        team_size=tournament["team_size"],
        questions=questions,
        channel_id=tournament.get("channel_id", 0),
        panel_message_id=tournament.get("panel_message_id", 0),
    )

    if tournament["team_size"] > 1:
        # Командный турнир — сначала выбор участников через UserSelect
        view = MemberSelectView(session)

        needed = tournament["team_size"] - 1
        embed = discord.Embed(
            title="👥 Выбор участников",
            description=(
                f"Турнир: **{tournament['name']}**\n"
                f"Формат: {_format_str(tournament['team_size'], tournament.get('is_team_dm', 0))}\n\n"
                f"Выберите **{needed} участников** из списка ниже.\n"
                f"Вы автоматически будете добавлены как капитан."
            ),
            color=config.EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    else:
        # 1v1 — сразу модалка
        await _show_first_modal(interaction, session)


# ===========================================================================
# КНОПКИ ПАНЕЛИ — ЭТАП OPEN (Регистрация)
# ===========================================================================

class OpenView(discord.ui.View):
    """Панель этапа OPEN — регистрация."""

    def __init__(self, tournament_id: int, team_size: int,
                 channel_id: int, panel_message_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(JoinButton(tournament_id, team_size, channel_id, panel_message_id))
        self.add_item(TeamsButton(tournament_id))
        self.add_item(AdminButton(tournament_id))


class JoinButton(discord.ui.Button):
    """📝 Записаться — начинает процесс регистрации."""

    def __init__(self, tournament_id: int, team_size: int,
                 channel_id: int, panel_message_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.success,
            label="📝 Записаться",
            custom_id=f"tjoin:{tournament_id}",
        )
        self.tournament_id = tournament_id
        self.team_size = team_size
        self.channel_id = channel_id
        self.panel_message_id = panel_message_id

    async def callback(self, interaction: discord.Interaction) -> None:
        tournament = await db.tournament_get(self.tournament_id)
        if not tournament or tournament["status"] != "open":
            await interaction.response.send_message(
                "❌ Набор закрыт.", ephemeral=True
            )
            return

        await _start_registration(interaction, tournament)


class TeamsButton(discord.ui.Button):
    """📋 Команды — показывает список команд."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="📋 Команды",
            custom_id=f"tteams:{tournament_id}",
        )
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        teams = await db.team_list(self.tournament_id)
        tournament = await db.tournament_get(self.tournament_id)
        if not tournament:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        if not teams:
            await interaction.response.send_message(
                "📋 Пока ни одной команды.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📋 Команды — {tournament['name']}",
            color=config.EMBED_COLOR,
        )

        lines: list[str] = []
        for t in teams:
            status = "✅" if t.get("approved") else "⏳"
            members = json.loads(t["members"])
            mentions = " ".join(f"<@{uid}>" for uid in members)
            lines.append(f"{status} **{t['name']}** (#{t['id']}) — {mentions}")

        embed.description = "\n".join(lines[:20])
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AdminButton(discord.ui.Button):
    """⚙️ Управление — открывает админ-панель."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="⚙️ Управление",
            custom_id=f"tadmin:{tournament_id}",
        )
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message(
                "❌ Только для администрации.", ephemeral=True
            )
            return

        tournament = await db.tournament_get(self.tournament_id)
        if not tournament:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        if tournament["status"] == "open":
            await _show_admin_open(interaction, self.tournament_id)
        elif tournament["status"] == "closed":
            await _show_admin_closed(interaction, self.tournament_id)
        elif tournament["status"] == "bracket":
            await _show_admin_bracket(interaction, self.tournament_id)
        elif tournament["status"] == "finished":
            await _show_admin_finished(interaction, self.tournament_id)


# ===========================================================================
# КНОПКИ ПАНЕЛИ — ЭТАП CLOSED
# ===========================================================================

class ClosedView(discord.ui.View):
    """Панель этапа CLOSED — набор закрыт, ожидание генерации сетки."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(AdminButton(tournament_id))


# ===========================================================================
# КНОПКИ ПАНЕЛИ — ЭТАП BRACKET
# ===========================================================================

class BracketView(discord.ui.View):
    """Панель этапа BRACKET — управление матчами."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(RefreshButton(tournament_id))
        self.add_item(StartMatchButton(tournament_id))
        self.add_item(SetWinnerButton(tournament_id))


class RefreshButton(discord.ui.Button):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="🔄 Обновить",
            custom_id=f"trefresh:{tournament_id}",
        )
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        tournament = await db.tournament_get(self.tournament_id)
        if not tournament:
            return
        embed, file, view = await _build_bracket_panel(self.tournament_id, tournament)
        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            embed=embed,
            attachments=[file],
            view=view,
        )


class StartMatchButton(discord.ui.Button):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.success,
            label="⚔️ Запустить",
            custom_id=f"tstart:{tournament_id}",
        )
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message("❌ Только для администрации.", ephemeral=True)
            return

        matches = await db.match_list(self.tournament_id)
        pending = [m for m in matches if m["status"] == "pending" and m["team1_id"] and m["team2_id"]]

        if not pending:
            await interaction.response.send_message("❌ Нет матчей для запуска.", ephemeral=True)
            return

        teams = await db.team_list(self.tournament_id)
        team_map = {t["id"]: t for t in teams}

        view = MatchSelectView(pending, team_map, "start")
        embed = discord.Embed(title="⚔️ Выберите матч для запуска", color=config.EMBED_COLOR)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class SetWinnerButton(discord.ui.Button):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="🏆 Победитель",
            custom_id=f"twinner:{tournament_id}",
        )
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message("❌ Только для администрации.", ephemeral=True)
            return

        matches = await db.match_list(self.tournament_id)
        playing = [m for m in matches if m["status"] == "playing"]

        if not playing:
            await interaction.response.send_message(
                "❌ Нет матчей в процессе. Сначала запустите матч.", ephemeral=True
            )
            return

        teams = await db.team_list(self.tournament_id)
        team_map = {t["id"]: t for t in teams}

        view = MatchSelectView(playing, team_map, "winner")
        embed = discord.Embed(title="🏆 Выберите матч для установки победителя", color=config.EMBED_COLOR)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ===========================================================================
# КНОПКИ ПАНЕЛИ — ЭТАП FINISHED
# ===========================================================================

class FinishedView(discord.ui.View):
    """Панель этапа FINISHED — турнир завершён."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(AdminButton(tournament_id))


# ===========================================================================
# АДМИН-ПАНЕЛЬ
# ===========================================================================

class AdminOpenView(discord.ui.View):
    """Админ-панель для этапа OPEN."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(timeout=120)
        self.tournament_id = tournament_id
        self.add_item(ApproveSelectButton(tournament_id))
        self.add_item(RejectSelectButton(tournament_id))
        self.add_item(ViewApplicationsButton(tournament_id))
        self.add_item(CloseRegButton(tournament_id))
        self.add_item(DeleteTournamentButton(tournament_id))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore


class AdminClosedView(discord.ui.View):
    """Админ-панель для этапа CLOSED."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(timeout=120)
        self.add_item(GenerateBracketButton(tournament_id))
        self.add_item(ReopenRegButton(tournament_id))
        self.add_item(DeleteTournamentButton(tournament_id))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore


class AdminBracketView(discord.ui.View):
    """Админ-панель для этапа BRACKET."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(timeout=120)
        self.add_item(DeleteTournamentButton(tournament_id))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore


class AdminFinishedView(discord.ui.View):
    """Админ-панель для этапа FINISHED."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(timeout=120)
        self.add_item(DeleteTournamentButton(tournament_id))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore


# --- Админ-кнопки ---

class ApproveSelectButton(discord.ui.Button):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(style=discord.ButtonStyle.success, label="✅ Одобрить", custom_id="admin_approve")
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        teams = await db.team_list(self.tournament_id)
        pending = [t for t in teams if not t["approved"]]

        if not pending:
            await interaction.response.send_message("✅ Все команды уже одобрены.", ephemeral=True)
            return

        options = [
            discord.SelectOption(label=t["name"][:100], value=str(t["id"]))
            for t in pending[:25]
        ]

        sel = discord.ui.Select(
            placeholder="Выберите команды для одобрения...",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
            custom_id="approve_team_select",
        )

        async def _approve_cb(sel_interaction: discord.Interaction) -> None:
            for val in sel.values:
                await db.team_set_approved(int(val), True)

            approved_names = []
            for val in sel.values:
                team = await db.team_get(int(val))
                if team:
                    approved_names.append(f"**{team['name']}**")

            await sel_interaction.response.edit_message(
                content=f"✅ Одобрены: {', '.join(approved_names)}",
                view=None,
            )
            await _update_panel_by_tournament(sel_interaction.client, self.tournament_id)  # type: ignore

            # Логируем одобрение
            await _log_event(
                sel_interaction.client, sel_interaction.guild_id,
                "✅ Команды одобрены",
                f"Администратор {sel_interaction.user.mention} одобрил: {', '.join(approved_names)}",
                color=0x57F287,
            )

        sel.callback = _approve_cb  # type: ignore
        view = discord.ui.View(timeout=60)
        view.add_item(sel)
        await interaction.response.send_message("Выберите команды:", view=view, ephemeral=True)


class RejectSelectButton(discord.ui.Button):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(style=discord.ButtonStyle.danger, label="❌ Отклонить", custom_id="admin_reject")
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        teams = await db.team_list(self.tournament_id)
        if not teams:
            await interaction.response.send_message("Нет команд.", ephemeral=True)
            return

        options = [
            discord.SelectOption(label=t["name"][:100], value=str(t["id"]))
            for t in teams[:25]
        ]

        sel = discord.ui.Select(
            placeholder="Выберите команду для удаления...",
            min_values=1,
            max_values=min(len(options), 25),
            options=options,
            custom_id="reject_team_select",
        )

        async def _reject_cb(sel_interaction: discord.Interaction) -> None:
            removed_names = []
            for val in sel.values:
                team = await db.team_get(int(val))
                if team:
                    removed_names.append(f"**{team['name']}**")
                await db.team_delete(int(val))

            await sel_interaction.response.edit_message(
                content=f"🗑 Удалены: {', '.join(removed_names)}",
                view=None,
            )
            await _update_panel_by_tournament(sel_interaction.client, self.tournament_id)  # type: ignore

            # Логируем отклонение
            await _log_event(
                sel_interaction.client, sel_interaction.guild_id,
                "❌ Команды отклонены",
                f"Администратор {sel_interaction.user.mention} отклонил: {', '.join(removed_names)}",
                color=0xED4245,
            )

        sel.callback = _reject_cb  # type: ignore
        view = discord.ui.View(timeout=60)
        view.add_item(sel)
        await interaction.response.send_message("Выберите команды для удаления:", view=view, ephemeral=True)


class ViewApplicationsButton(discord.ui.Button):
    """📋 Анкеты — просмотр анкет поданных команд."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="📋 Анкеты",
            custom_id="admin_apps",
        )
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        apps = await db.application_list(self.tournament_id)
        if not apps:
            await interaction.response.send_message(
                "📋 Нет заполненных анкет.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📋 Анкеты — турнир #{self.tournament_id}",
            color=config.EMBED_COLOR,
        )

        for app in apps[:10]:
            answers = json.loads(app["answers"])
            answer_lines = []
            for q, a in answers.items():
                answer_lines.append(f"**{q}:** {a[:100]}")

            status = "✅" if app["status"] == "approved" else "⏳"
            text = "\n".join(answer_lines) or "Нет ответов"
            embed.add_field(
                name=f"{status} {app['team_name']} (<@{app['user_id']}>)",
                value=text[:1024],
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


class CloseRegButton(discord.ui.Button):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="🔒 Закрыть набор", custom_id="admin_close")
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await db.tournament_set_status(self.tournament_id, "closed")
        await _update_panel_by_tournament(interaction.client, self.tournament_id)  # type: ignore
        await interaction.response.edit_message(
            content="🔒 Набор закрыт! Теперь можно сгенерировать сетку.",
            view=None,
        )


class ReopenRegButton(discord.ui.Button):
    """🔓 Открыть набор — вернуть турнир в статус OPEN."""

    def __init__(self, tournament_id: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="🔓 Открыть набор",
            custom_id="admin_reopen",
        )
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await db.tournament_set_status(self.tournament_id, "open")
        await _update_panel_by_tournament(interaction.client, self.tournament_id)  # type: ignore
        await interaction.response.edit_message(
            content="🔓 Набор снова открыт!",
            view=None,
        )


class GenerateBracketButton(discord.ui.Button):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(style=discord.ButtonStyle.success, label="🏆 Сгенерировать сетку", custom_id="admin_gen")
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        tournament = await db.tournament_get(self.tournament_id)
        if not tournament:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        teams = await db.team_list(self.tournament_id)
        approved = [t for t in teams if t["approved"]]

        if len(approved) < 2:
            await interaction.response.send_message(
                "❌ Нужно минимум 2 одобренные команды.", ephemeral=True
            )
            return

        await _generate_bracket(self.tournament_id)
        await db.tournament_set_status(self.tournament_id, "bracket")
        await _update_panel_by_tournament(interaction.client, self.tournament_id)  # type: ignore

        await interaction.response.edit_message(
            content=f"✅ Сетка сгенерирована! {len(approved)} команд.",
            view=None,
        )

        # Логируем генерацию сетки
        await _log_event(
            interaction.client, interaction.guild_id,
            "🏆 Сетка сгенерирована",
            f"Турнир **{tournament['name']}**: сетка с {len(approved)} командами",
            color=config.EMBED_COLOR,
        )


class DeleteTournamentButton(discord.ui.Button):
    def __init__(self, tournament_id: int) -> None:
        super().__init__(style=discord.ButtonStyle.danger, label="🗑 Удалить турнир", custom_id="admin_delete")
        self.tournament_id = tournament_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await db.tournament_delete(self.tournament_id)
        await interaction.response.edit_message(content="🗑 Турнир удалён.", view=None)

        tournament = await db.tournament_get(self.tournament_id)
        if tournament and tournament.get("panel_message_id"):
            try:
                channel = interaction.client.get_channel(tournament["channel_id"])
                if channel:
                    msg = await channel.fetch_message(tournament["panel_message_id"])  # type: ignore
                    await msg.delete()
            except Exception:
                pass


# ===========================================================================
# АДМИН-ПАНЕЛЬ — ПОКАЗАТЬ
# ===========================================================================

async def _show_admin_open(interaction: discord.Interaction, tournament_id: int) -> None:
    teams = await db.team_list(tournament_id)
    pending = [t for t in teams if not t["approved"]]
    approved = [t for t in teams if t["approved"]]
    questions = await db.question_list(tournament_id)

    embed = discord.Embed(title="⚙️ Управление турниром", color=config.EMBED_COLOR)
    embed.add_field(name="Ожидают одобрения", value=str(len(pending)), inline=True)
    embed.add_field(name="Одобрено", value=str(len(approved)), inline=True)
    embed.add_field(name="Вопросов в анкете", value=str(len(questions)), inline=True)

    if pending:
        names = "\n".join(f"⏳ {t['name']} (#{t['id']})" for t in pending[:10])
        embed.add_field(name="Неодобренные", value=names, inline=False)

    view = AdminOpenView(tournament_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def _show_admin_closed(interaction: discord.Interaction, tournament_id: int) -> None:
    teams = await db.team_list(tournament_id)
    approved = [t for t in teams if t["approved"]]

    embed = discord.Embed(title="⚙️ Набор закрыт", color=config.EMBED_COLOR)
    embed.add_field(name="Одобренных команд", value=str(len(approved)), inline=True)

    view = AdminClosedView(tournament_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def _show_admin_bracket(interaction: discord.Interaction, tournament_id: int) -> None:
    matches = await db.match_list(tournament_id)
    completed = sum(1 for m in matches if m["status"] == "completed")
    playing = sum(1 for m in matches if m["status"] == "playing")
    pending = sum(1 for m in matches if m["status"] == "pending")

    embed = discord.Embed(title="⚙️ Турнир идёт", color=config.EMBED_COLOR)
    embed.add_field(name="Матчей завершено", value=str(completed), inline=True)
    embed.add_field(name="В процессе", value=str(playing), inline=True)
    embed.add_field(name="Ожидает", value=str(pending), inline=True)

    view = AdminBracketView(tournament_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def _show_admin_finished(interaction: discord.Interaction, tournament_id: int) -> None:
    embed = discord.Embed(title="⚙️ Турнир завершён", color=config.EMBED_COLOR)

    matches = await db.match_list(tournament_id)
    if matches:
        final = max(matches, key=lambda m: m["round"])
        if final.get("winner_id"):
            champ = await db.team_get(final["winner_id"])
            if champ:
                embed.add_field(name="🏆 Чемпион", value=f"**{champ['name']}**", inline=False)

    view = AdminFinishedView(tournament_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ===========================================================================
# MATCH / WINNER DROPDOWNS
# ===========================================================================

class MatchSelect(discord.ui.Select):
    def __init__(self, matches: list[dict], team_map: dict, action: str) -> None:
        options = []
        for m in matches[:25]:
            t1 = team_map.get(m["team1_id"], {}).get("name", "TBD") if m["team1_id"] else "TBD"
            t2 = team_map.get(m["team2_id"], {}).get("name", "TBD") if m["team2_id"] else "TBD"
            label = f"#{m['id']} {t1} vs {t2}"
            rnd_name = _round_name(max(mm["round"] for mm in matches), m["round"]) if matches else f"Раунд {m['round']}"
            options.append(discord.SelectOption(
                label=label[:100], value=str(m["id"]),
                description=rnd_name,
            ))
        super().__init__(
            placeholder="Выберите матч...",
            min_values=1, max_values=1,
            options=options,
            custom_id=f"match_sel_{action}",
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        match_id = int(self.values[0])
        if self.action == "start":
            await _do_start_match(interaction, match_id)
        elif self.action == "winner":
            await _show_winner_dropdown(interaction, match_id)


class MatchSelectView(discord.ui.View):
    def __init__(self, matches: list[dict], team_map: dict, action: str) -> None:
        super().__init__(timeout=60)
        self.add_item(MatchSelect(matches, team_map, action))


class WinnerSelect(discord.ui.Select):
    def __init__(self, match_id: int, t1_id: int, t2_id: int, t1_name: str, t2_name: str) -> None:
        self.match_id = match_id
        options = [
            discord.SelectOption(label=t1_name[:100], value=str(t1_id)),
            discord.SelectOption(label=t2_name[:100], value=str(t2_id)),
        ]
        super().__init__(
            placeholder="Выберите победителя...",
            min_values=1, max_values=1,
            options=options, custom_id="winner_sel",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        winner_id = int(self.values[0])
        await _do_set_winner(interaction, self.match_id, winner_id)


class WinnerSelectView(discord.ui.View):
    def __init__(self, match_id: int, t1_id: int, t2_id: int, t1_name: str, t2_name: str) -> None:
        super().__init__(timeout=60)
        self.add_item(WinnerSelect(match_id, t1_id, t2_id, t1_name, t2_name))


# ===========================================================================
# MATCH ACTIONS
# ===========================================================================

async def _do_start_match(interaction: discord.Interaction, match_id: int) -> None:
    match_data = await db.match_get(match_id)
    if not match_data or match_data["status"] != "pending":
        await interaction.response.send_message("❌ Матч не найден или уже запущен.", ephemeral=True)
        return

    if not match_data["team1_id"] or not match_data["team2_id"]:
        await interaction.response.send_message("❌ В матче не хватает участников.", ephemeral=True)
        return

    await db.match_set_status(match_id, "playing")

    team1 = await db.team_get(match_data["team1_id"])
    team2 = await db.team_get(match_data["team2_id"])
    t1_name = team1["name"] if team1 else "???"
    t2_name = team2["name"] if team2 else "???"

    t1_mentions = " ".join(f"<@{uid}>" for uid in json.loads(team1["members"])) if team1 else "—"
    t2_mentions = " ".join(f"<@{uid}>" for uid in json.loads(team2["members"])) if team2 else "—"

    embed = discord.Embed(
        title="⚔️ Матч начат!",
        description=f"**{t1_name}** vs **{t2_name}**",
        color=config.EMBED_COLOR,
    )
    embed.add_field(name=t1_name, value=t1_mentions, inline=True)
    embed.add_field(name=t2_name, value=t2_mentions, inline=True)
    embed.add_field(name="Матч", value=f"#{match_id}", inline=True)

    await interaction.response.edit_message(content=None, embed=embed, view=None)

    await _update_panel_by_tournament(interaction.client, match_data["tournament_id"])  # type: ignore

    # Логируем старт матча
    await _log_event(
        interaction.client, interaction.guild_id,
        "⚔️ Матч начат",
        f"**{t1_name}** vs **{t2_name}** (матч #{match_id})",
        color=0xFEE75C,
    )


async def _show_winner_dropdown(interaction: discord.Interaction, match_id: int) -> None:
    match_data = await db.match_get(match_id)
    if not match_data or match_data["status"] != "playing":
        await interaction.response.send_message("❌ Матч должен быть запущен.", ephemeral=True)
        return

    team1 = await db.team_get(match_data["team1_id"])
    team2 = await db.team_get(match_data["team2_id"])
    t1_name = team1["name"] if team1 else "???"
    t2_name = team2["name"] if team2 else "???"

    view = WinnerSelectView(match_id, match_data["team1_id"], match_data["team2_id"], t1_name, t2_name)
    embed = discord.Embed(
        title="🏆 Выберите победителя",
        description=f"**{t1_name}** vs **{t2_name}**\nМатч #{match_id}",
        color=config.EMBED_COLOR,
    )
    await interaction.response.edit_message(content=None, embed=embed, view=view)


async def _do_set_winner(interaction: discord.Interaction, match_id: int, winner_id: int) -> None:
    match_data = await db.match_get(match_id)
    if not match_data:
        await interaction.response.send_message("❌ Матч не найден.", ephemeral=True)
        return

    await db.match_set_winner(match_id, winner_id)

    winner_team = await db.team_get(winner_id)
    loser_id = match_data["team2_id"] if winner_id == match_data["team1_id"] else match_data["team1_id"]
    loser_team = await db.team_get(loser_id)
    winner_name = winner_team["name"] if winner_team else "???"
    loser_name = loser_team["name"] if loser_team else "???"

    embed = discord.Embed(
        title="🏆 Победитель!",
        description=f"**{winner_name}** побеждает **{loser_name}**",
        color=config.EMBED_COLOR,
    )

    tournament_id = match_data["tournament_id"]
    next_round = match_data["round"] + 1
    next_match_idx = match_data["match_index"] // 2

    all_matches = await db.match_list(tournament_id)
    next_match = next(
        (m for m in all_matches if m["round"] == next_round and m["match_index"] == next_match_idx),
        None,
    )

    if next_match:
        slot = "team1_id" if match_data["match_index"] % 2 == 0 else "team2_id"
        await db.match_update_team(next_match["id"], slot, winner_id)
    else:
        max_round = max((m["round"] for m in all_matches), default=1)
        if match_data["round"] >= max_round:
            await db.tournament_set_status(tournament_id, "finished")
            embed.add_field(name="🎉 Турнир завершён!", value=f"**{winner_name}** — чемпион!", inline=False)

    await interaction.response.edit_message(content=None, embed=embed, view=None)

    await _update_panel_by_tournament(interaction.client, tournament_id)  # type: ignore

    # Логируем результат матча
    await _log_event(
        interaction.client, interaction.guild_id,
        "🏆 Победитель матча",
        f"**{winner_name}** побеждает **{loser_name}**",
        color=0x57F287,
    )


# ===========================================================================
# ГЕНЕРАЦИЯ СЕТКИ
# ===========================================================================

async def _generate_bracket(tournament_id: int) -> None:
    """Генерирует Single Elimination сетку для турнира."""
    teams = await db.team_list(tournament_id)
    approved = [t for t in teams if t["approved"]]

    if len(approved) < 2:
        return

    await db.match_delete_for_tournament(tournament_id)

    n = len(approved)
    bracket_size = 1
    while bracket_size < n:
        bracket_size *= 2

    num_rounds = int(math.log2(bracket_size))

    seeded = list(approved)
    random.shuffle(seeded)

    byes = bracket_size - n

    match_idx = 0
    team_idx = 0
    first_round_matches = bracket_size // 2

    for i in range(first_round_matches):
        t1_id = 0
        t2_id = 0

        if i < byes:
            if team_idx < n:
                t1_id = seeded[team_idx]["id"]
                team_idx += 1
            t2_id = 0
        else:
            if team_idx < n:
                t1_id = seeded[team_idx]["id"]
                team_idx += 1
            if team_idx < n:
                t2_id = seeded[team_idx]["id"]
                team_idx += 1

        await db.match_create(tournament_id, t1_id, t2_id, 1, match_idx)
        match_idx += 1

    # Создаём пустые матчи для последующих раундов
    for rnd in range(2, num_rounds + 1):
        matches_in_round = bracket_size // (2 ** rnd)
        for idx in range(matches_in_round):
            await db.match_create(tournament_id, 0, 0, rnd, idx)

    # Обрабатываем bye — автоматическое продвижение
    all_matches = await db.match_list(tournament_id)
    for rnd in range(1, num_rounds + 1):
        round_matches = [m for m in all_matches if m["round"] == rnd]
        next_round_matches = [m for m in all_matches if m["round"] == rnd + 1]

        for m in round_matches:
            if m["team1_id"] and not m["team2_id"] and m["status"] == "pending":
                # Bye — команда автоматически проходит дальше
                await db.match_set_winner(m["id"], m["team1_id"])

                if next_round_matches:
                    nm = next(
                        (nm for nm in next_round_matches if nm["match_index"] == m["match_index"] // 2),
                        None,
                    )
                    if nm:
                        slot = "team1_id" if m["match_index"] % 2 == 0 else "team2_id"
                        await db.match_update_team(nm["id"], slot, m["team1_id"])


# ===========================================================================
# ПАНЕЛЬ — СБОРКА И ОБНОВЛЕНИЕ
# ===========================================================================

async def _build_panel(tournament_id: int, tournament: dict) -> tuple:
    """Собирает embed + view для панели турнира (любой этап)."""

    status = tournament["status"]
    team_size = tournament["team_size"]
    is_dm = tournament.get("is_team_dm", 0)
    channel_id = tournament.get("channel_id", 0)
    panel_msg_id = tournament.get("panel_message_id", 0)
    fmt = _format_str(team_size, is_dm)
    max_str = str(tournament["max_teams"]) if tournament["max_teams"] > 0 else "∞"

    if status == "open":
        return await _build_registration_panel(tournament_id, tournament, fmt, max_str, channel_id, panel_msg_id)
    elif status == "closed":
        return await _build_closed_panel(tournament_id, tournament, fmt, max_str)
    elif status in ("bracket", "finished"):
        return await _build_bracket_panel(tournament_id, tournament, fmt)
    else:
        embed = discord.Embed(title=f"🏆 {tournament['name']}", color=config.EMBED_COLOR)
        view = discord.ui.View()
        return (embed, view)


async def _build_registration_panel(
    tournament_id: int, tournament: dict, fmt: str, max_str: str,
    channel_id: int, panel_msg_id: int,
) -> tuple:
    """Собирает embed + view для этапа OPEN."""
    teams = await db.team_list(tournament_id)
    approved = sum(1 for t in teams if t.get("approved"))
    pending = sum(1 for t in teams if not t.get("approved"))
    questions = await db.question_list(tournament_id)

    embed = discord.Embed(
        title=f"🏆 {tournament['name']}",
        description=tournament.get("description") or None,
        color=config.EMBED_COLOR,
    )
    embed.add_field(name="Формат", value=fmt, inline=True)
    embed.add_field(name="ID", value=f"#{tournament_id}", inline=True)
    embed.add_field(name="Статус", value="🟢 Открыт", inline=True)
    embed.add_field(name="Команды", value=f"{len(teams)}/{max_str}", inline=True)
    embed.add_field(name="Одобрено", value=f"✅ {approved}", inline=True)
    embed.add_field(name="Ожидают", value=f"⏳ {pending}", inline=True)

    if questions:
        q_list = "\n".join(f"• {q['question_text']}" for q in questions[:15])
        if len(questions) > 15:
            q_list += f"\n... и ещё {len(questions) - 15}"
        embed.add_field(name=f"📋 Анкета ({len(questions)} вопросов)", value=q_list, inline=False)

    if teams:
        team_lines = []
        for t in teams[:12]:
            status = "✅" if t.get("approved") else "⏳"
            team_lines.append(f"{status} {t['name']}")
        if len(teams) > 12:
            team_lines.append(f"... и ещё {len(teams) - 12}")
        embed.add_field(name="Участники", value="\n".join(team_lines), inline=False)

    embed.set_footer(text="Нажмите 📝 Записаться для регистрации!")

    view = OpenView(tournament_id, tournament["team_size"], channel_id, panel_msg_id)
    return (embed, view)


async def _build_closed_panel(tournament_id: int, tournament: dict, fmt: str, max_str: str) -> tuple:
    """Собирает embed + view для этапа CLOSED."""
    teams = await db.team_list(tournament_id)
    approved = sum(1 for t in teams if t.get("approved"))

    embed = discord.Embed(
        title=f"🏆 {tournament['name']}",
        description=tournament.get("description") or None,
        color=config.EMBED_COLOR,
    )
    embed.add_field(name="Формат", value=fmt, inline=True)
    embed.add_field(name="ID", value=f"#{tournament_id}", inline=True)
    embed.add_field(name="Статус", value="🔒 Набор закрыт", inline=True)
    embed.add_field(name="Команд", value=f"✅ {approved}", inline=True)
    embed.set_footer(text="Ожидание генерации сетки администрацией...")

    view = ClosedView(tournament_id)
    return (embed, view)


async def _build_bracket_panel(tournament_id: int, tournament: dict, fmt: str) -> tuple:
    """Собирает embed + file + view для этапа BRACKET / FINISHED."""
    teams = await db.team_list(tournament_id)
    matches = await db.match_list(tournament_id)

    if matches:
        buf = generate_bracket(teams, matches, tournament["name"])
    else:
        buf = generate_bracket_simple(teams, tournament["name"])

    file = discord.File(buf, filename="bracket.png")

    status_emoji = {"bracket": "⚔️", "finished": "🏆"}.get(tournament["status"], "❓")
    status_text = {"bracket": "Идёт", "finished": "Завершён"}.get(tournament["status"], tournament["status"])

    embed = discord.Embed(title=f"🏆 {tournament['name']}", color=config.EMBED_COLOR)
    embed.set_image(url="attachment://bracket.png")
    embed.add_field(name="Формат", value=fmt, inline=True)
    embed.add_field(name="Статус", value=f"{status_emoji} {status_text}", inline=True)

    if matches:
        completed = sum(1 for m in matches if m["status"] == "completed")
        playing = sum(1 for m in matches if m["status"] == "playing")
        embed.add_field(name="Матчи", value=f"✅ {completed} ⚔️ {playing} ⏳ {len(matches) - completed - playing}", inline=True)

    if tournament["status"] == "finished":
        final_match = next((m for m in matches if m["round"] == max(mm["round"] for mm in matches)), None)
        if final_match and final_match["winner_id"]:
            champ = await db.team_get(final_match["winner_id"])
            if champ:
                embed.add_field(name="🏆 Чемпион", value=f"**{champ['name']}**", inline=False)

    if tournament["status"] == "finished":
        view = FinishedView(tournament_id)
    else:
        view = BracketView(tournament_id)

    return (embed, file, view)


async def _update_panel_by_tournament(client: discord.Client, tournament_id: int) -> None:
    """Обновляет панель турнира по ID турнира."""
    tournament = await db.tournament_get(tournament_id)
    if not tournament:
        return

    channel_id = tournament.get("channel_id", 0)
    message_id = tournament.get("panel_message_id", 0)
    if not channel_id or not message_id:
        return

    channel = client.get_channel(channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)  # type: ignore
    except discord.NotFound:
        return

    status = tournament["status"]

    if status == "open" or status == "closed":
        result = await _build_panel(tournament_id, tournament)
        embed, view = result[0], result[1]
        await message.edit(embed=embed, view=view)
    elif status in ("bracket", "finished"):
        result = await _build_panel(tournament_id, tournament)
        embed, file, view = result[0], result[1], result[2]
        await message.edit(embed=embed, attachments=[file], view=view)


# ===========================================================================
# COG
# ===========================================================================

class TournamentCog(commands.Cog, name="Tournament"):
    """Система турниров с анкетами, кнопочным управлением и сеткой."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -----------------------------------------------------------------------
    # Autocomplete
    # -----------------------------------------------------------------------

    async def _tournament_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        tournaments = await db.tournament_list(interaction.guild_id)
        results = []
        for t in tournaments:
            label = f"{t['name']} (#{t['id']}) [{_format_str(t['team_size'], t.get('is_team_dm', 0))}]"
            if current.lower() in label.lower():
                results.append(app_commands.Choice(name=label[:100], value=str(t["id"])))
        return results[:25]

    async def _all_tournament_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete для выбора любого турнира (для /bracket)."""
        tournaments = await db.tournament_list(interaction.guild_id)
        results = []
        status_emoji = {"open": "🟢", "closed": "🔒", "bracket": "⚔️", "finished": "🏆"}
        for t in tournaments:
            fmt = _format_str(t["team_size"], t.get("is_team_dm", 0))
            emoji = status_emoji.get(t["status"], "❓")
            label = f"{emoji} {t['name']} (#{t['id']}) [{fmt}]"
            if current.lower() in label.lower():
                results.append(app_commands.Choice(name=label[:100], value=str(t["id"])))
        return results[:25]

    # -----------------------------------------------------------------------
    # /1vs1  /2vs2  /3vs3  /customlobby
    # -----------------------------------------------------------------------

    @app_commands.command(name="1vs1", description="Записаться на 1v1 турнир")
    async def register_1v1(self, interaction: discord.Interaction) -> None:
        await self._show_tournament_select(interaction, team_size=1)

    @app_commands.command(name="2vs2", description="Записаться на 2v2 турнир")
    async def register_2v2(self, interaction: discord.Interaction) -> None:
        await self._show_tournament_select(interaction, team_size=2)

    @app_commands.command(name="3vs3", description="Записаться на 3v3 турнир")
    async def register_3v3(self, interaction: discord.Interaction) -> None:
        await self._show_tournament_select(interaction, team_size=3)

    @app_commands.command(name="customlobby", description="Записаться на кастомный турнир (4v4+)")
    async def register_custom(self, interaction: discord.Interaction) -> None:
        await self._show_tournament_select(interaction, team_size=0)

    async def _show_tournament_select(self, interaction: discord.Interaction, team_size: int) -> None:
        """Показывает dropdown выбора турнира или сразу начинает регистрацию."""
        tournaments = await db.tournament_list_by_format(
            interaction.guild_id, team_size, status="open"
        )

        if not tournaments:
            fmt_name = "1v1" if team_size == 1 else f"{team_size}v{team_size}" if team_size else "кастомный"
            await interaction.response.send_message(
                f"❌ Нет открытых {fmt_name} турниров для регистрации.",
                ephemeral=True,
            )
            return

        if len(tournaments) == 1:
            # Один турнир — начинаем регистрацию сразу
            await _start_registration(interaction, tournaments[0])
        else:
            # Несколько турниров — dropdown
            view = TournamentSelectView(tournaments, team_size)
            fmt_name = "1v1" if team_size == 1 else f"{team_size}v{team_size}" if team_size else "кастомный"
            embed = discord.Embed(
                title=f"🏆 Выберите {fmt_name} турнир",
                description="Выберите турнир из списка для регистрации",
                color=config.EMBED_COLOR,
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # -----------------------------------------------------------------------
    # /bracket — посмотреть сетку (доступна всем)
    # -----------------------------------------------------------------------

    @app_commands.command(name="bracket", description="Посмотреть сетку турнира")
    @app_commands.describe(tournament="Выберите турнир")
    @app_commands.autocomplete(tournament=_all_tournament_autocomplete)
    async def view_bracket(self, interaction: discord.Interaction, tournament: str) -> None:
        try:
            tid = int(tournament)
        except ValueError:
            await interaction.response.send_message("❌ Неверный ID турнира.", ephemeral=True)
            return

        t = await db.tournament_get(tid)
        if not t or t["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        fmt = _format_str(t["team_size"], t.get("is_team_dm", 0))

        if t["status"] in ("bracket", "finished"):
            teams = await db.team_list(tid)
            matches = await db.match_list(tid)

            if matches:
                buf = generate_bracket(teams, matches, t["name"])
            else:
                buf = generate_bracket_simple(teams, t["name"])

            file = discord.File(buf, filename="bracket.png")

            status_emoji = {"bracket": "⚔️", "finished": "🏆"}.get(t["status"], "❓")
            status_text = {"bracket": "Идёт", "finished": "Завершён"}.get(t["status"], t["status"])

            embed = discord.Embed(title=f"🏆 {t['name']}", color=config.EMBED_COLOR)
            embed.set_image(url="attachment://bracket.png")
            embed.add_field(name="Формат", value=fmt, inline=True)
            embed.add_field(name="Статус", value=f"{status_emoji} {status_text}", inline=True)

            if matches:
                completed = sum(1 for m in matches if m["status"] == "completed")
                playing = sum(1 for m in matches if m["status"] == "playing")
                pending = len(matches) - completed - playing
                embed.add_field(
                    name="Матчи",
                    value=f"✅ {completed} ⚔️ {playing} ⏳ {pending}",
                    inline=True,
                )

            if t["status"] == "finished":
                final_match = next(
                    (m for m in matches if m["round"] == max(mm["round"] for mm in matches)),
                    None,
                )
                if final_match and final_match["winner_id"]:
                    champ = await db.team_get(final_match["winner_id"])
                    if champ:
                        embed.add_field(
                            name="🏆 Чемпион",
                            value=f"**{champ['name']}**",
                            inline=False,
                        )

            await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

        elif t["status"] in ("open", "closed"):
            teams = await db.team_list(tid)
            if not teams:
                await interaction.response.send_message(
                    "📋 Пока ни одной команды. Сетка ещё не сгенерирована.",
                    ephemeral=True,
                )
                return

            buf = generate_bracket_simple(teams, t["name"])
            file = discord.File(buf, filename="bracket.png")

            status_emoji = {"open": "🟢", "closed": "🔒"}.get(t["status"], "❓")
            status_text = {"open": "Регистрация открыта", "closed": "Набор закрыт"}.get(
                t["status"], t["status"]
            )

            embed = discord.Embed(title=f"🏆 {t['name']}", color=config.EMBED_COLOR)
            embed.set_image(url="attachment://bracket.png")
            embed.add_field(name="Формат", value=fmt, inline=True)
            embed.add_field(name="Статус", value=f"{status_emoji} {status_text}", inline=True)

            approved = sum(1 for tm in teams if tm.get("approved"))
            embed.add_field(name="Команд", value=f"{len(teams)} (✅ {approved})", inline=True)

            await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

    # -----------------------------------------------------------------------
    # /tournament — группа команд администратора
    # -----------------------------------------------------------------------

    tournament = app_commands.Group(
        name="tournament",
        description="Управление турнирами",
        default_permissions=discord.Permissions(administrator=True),
    )

    questions_grp = app_commands.Group(
        parent=tournament,
        name="questions",
        description="Управление анкетой турнира",
    )

    # --- /tournament create ---

    @tournament.command(name="create", description="Создать турнир")
    @app_commands.describe(
        name="Название турнира",
        format="Формат турнира",
        max_teams="Макс. команд (0 = без лимита)",
        description="Описание турнира",
        team_size="Размер команды (только для формата Custom)",
        team_dm="Командное ДМ (все против всех)",
    )
    @app_commands.choices(format=[
        app_commands.Choice(name="1v1", value=1),
        app_commands.Choice(name="2v2", value=2),
        app_commands.Choice(name="3v3", value=3),
        app_commands.Choice(name="Custom (укажите team_size)", value=0),
    ])
    async def tournament_create(
        self,
        interaction: discord.Interaction,
        name: str,
        format: int,
        max_teams: int = 0,
        description: str = "",
        team_size: int = 4,
        team_dm: bool = False,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message("❌ Только для администрации.", ephemeral=True)
            return

        if format == 0:
            actual_size = max(1, team_size)
        else:
            actual_size = format

        tid = await db.tournament_create(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            name=name,
            team_size=actual_size,
            is_team_dm=team_dm,
            max_teams=max_teams,
            description=description,
        )

        fmt_str = _format_str(actual_size, team_dm)
        embed = discord.Embed(
            title=f"🏆 Турнир создан!",
            description=f"**{name}** (#{tid})",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="Формат", value=fmt_str, inline=True)
        embed.add_field(name="Макс. команд", value=str(max_teams) if max_teams > 0 else "∞", inline=True)
        if description:
            embed.add_field(name="Описание", value=description[:200], inline=False)
        embed.set_footer(text="Добавьте вопросы через /tournament questions add")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /tournament questions add ---

    @questions_grp.command(name="add", description="Добавить вопрос в анкету турнира (без лимита)")
    @app_commands.describe(
        tournament="Турнир (введите ID или название)",
        question="Текст вопроса",
    )
    @app_commands.autocomplete(tournament=_tournament_autocomplete)
    async def questions_add(
        self,
        interaction: discord.Interaction,
        tournament: str,
        question: str,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message("❌ Только для администрации.", ephemeral=True)
            return

        try:
            tid = int(tournament)
        except ValueError:
            await interaction.response.send_message("❌ Неверный ID турнира.", ephemeral=True)
            return

        t = await db.tournament_get(tid)
        if not t or t["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        qid = await db.question_add(tid, question, required=True)
        current_count = await db.question_count(tid)

        await _update_panel_by_tournament(interaction.client, tid)

        embed = discord.Embed(
            title="✅ Вопрос добавлен",
            description=f"**{question}**",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="Турнир", value=f"{t['name']} (#{tid})", inline=True)
        embed.add_field(name="Всего вопросов", value=str(current_count), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /tournament questions remove ---

    @questions_grp.command(name="remove", description="Удалить вопрос из анкеты турнира")
    @app_commands.describe(tournament="Турнир")
    @app_commands.autocomplete(tournament=_tournament_autocomplete)
    async def questions_remove(
        self,
        interaction: discord.Interaction,
        tournament: str,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message("❌ Только для администрации.", ephemeral=True)
            return

        try:
            tid = int(tournament)
        except ValueError:
            await interaction.response.send_message("❌ Неверный ID турнира.", ephemeral=True)
            return

        t = await db.tournament_get(tid)
        if not t or t["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        questions = await db.question_list(tid)
        if not questions:
            await interaction.response.send_message("❌ Нет вопросов для удаления.", ephemeral=True)
            return

        options = [
            discord.SelectOption(
                label=f"#{q['position']+1}: {q['question_text'][:80]}",
                value=str(q["id"]),
            )
            for q in questions[:25]
        ]

        sel = discord.ui.Select(
            placeholder="Выберите вопрос для удаления...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="question_remove_select",
        )

        async def _remove_cb(sel_interaction: discord.Interaction) -> None:
            qid = int(sel.values[0])
            removed = await db.question_remove(qid)
            if removed:
                await _update_panel_by_tournament(sel_interaction.client, tid)  # type: ignore
                await sel_interaction.response.edit_message(
                    content="✅ Вопрос удалён.", view=None,
                )
            else:
                await sel_interaction.response.edit_message(
                    content="❌ Вопрос не найден.", view=None,
                )

        sel.callback = _remove_cb  # type: ignore
        view = discord.ui.View(timeout=60)
        view.add_item(sel)
        await interaction.response.send_message("Выберите вопрос:", view=view, ephemeral=True)

    # --- /tournament questions list ---

    @questions_grp.command(name="list", description="Показать список вопросов анкеты")
    @app_commands.describe(tournament="Турнир")
    @app_commands.autocomplete(tournament=_tournament_autocomplete)
    async def questions_list(
        self,
        interaction: discord.Interaction,
        tournament: str,
    ) -> None:
        try:
            tid = int(tournament)
        except ValueError:
            await interaction.response.send_message("❌ Неверный ID турнира.", ephemeral=True)
            return

        t = await db.tournament_get(tid)
        if not t or t["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        questions = await db.question_list(tid)

        embed = discord.Embed(
            title=f"📋 Анкета — {t['name']} (#{tid})",
            color=config.EMBED_COLOR,
        )

        if not questions:
            embed.description = "Анкета пуста. Добавьте вопросы через `/tournament questions add`."
        else:
            lines = []
            for q in questions:
                req = "обязательный" if q.get("required") else "необязательный"
                lines.append(f"**{q['position']+1}.** {q['question_text']} _({req})_")
            embed.description = "\n".join(lines)

        embed.set_footer(text=f"Вопросов: {len(questions)} (без лимита)")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /tournament questions clear ---

    @questions_grp.command(name="clear", description="Очистить все вопросы анкеты турнира")
    @app_commands.describe(tournament="Турнир")
    @app_commands.autocomplete(tournament=_tournament_autocomplete)
    async def questions_clear(
        self,
        interaction: discord.Interaction,
        tournament: str,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message("❌ Только для администрации.", ephemeral=True)
            return

        try:
            tid = int(tournament)
        except ValueError:
            await interaction.response.send_message("❌ Неверный ID турнира.", ephemeral=True)
            return

        t = await db.tournament_get(tid)
        if not t or t["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        count = await db.question_clear(tid)
        await _update_panel_by_tournament(interaction.client, tid)

        await interaction.response.send_message(
            f"✅ Удалено {count} вопросов из анкеты турнира **{t['name']}**.",
            ephemeral=True,
        )

    # --- /tournament list ---

    @tournament.command(name="list", description="Список турниров сервера")
    async def tournament_list(self, interaction: discord.Interaction) -> None:
        tournaments = await db.tournament_list(interaction.guild_id)

        if not tournaments:
            await interaction.response.send_message("📋 Нет турниров.", ephemeral=True)
            return

        embed = discord.Embed(title="🏆 Турниры сервера", color=config.EMBED_COLOR)

        status_emoji = {
            "open": "🟢", "closed": "🔒", "bracket": "⚔️", "finished": "🏆"
        }

        for t in tournaments[:10]:
            fmt = _format_str(t["team_size"], t.get("is_team_dm", 0))
            emoji = status_emoji.get(t["status"], "❓")
            max_str = str(t["max_teams"]) if t["max_teams"] > 0 else "∞"
            embed.add_field(
                name=f"{emoji} {t['name']} (#{t['id']})",
                value=f"Формат: {fmt} | Команд: {max_str} | Статус: {t['status']}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /tournament panel ---

    @tournament.command(name="panel", description="Показать интерактивную панель турнира")
    @app_commands.describe(tournament="Турнир")
    @app_commands.autocomplete(tournament=_tournament_autocomplete)
    async def tournament_panel(
        self,
        interaction: discord.Interaction,
        tournament: str,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message("❌ Только для администрации.", ephemeral=True)
            return

        try:
            tid = int(tournament)
        except ValueError:
            await interaction.response.send_message("❌ Неверный ID турнира.", ephemeral=True)
            return

        t = await db.tournament_get(tid)
        if not t or t["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        result = await _build_panel(tid, t)
        embed = result[0]

        if t["status"] in ("bracket", "finished") and len(result) == 3:
            file = result[1]
            view = result[2]
            msg = await interaction.channel.send(embed=embed, file=file, view=view)  # type: ignore
        else:
            view = result[1]
            msg = await interaction.channel.send(embed=embed, view=view)  # type: ignore

        await db.tournament_set_panel(tid, interaction.channel_id, msg.id)

        await interaction.response.send_message(
            f"✅ Панель турнира **{t['name']}** опубликована!", ephemeral=True
        )

    # --- /tournament delete ---

    @tournament.command(name="delete", description="Удалить турнир")
    @app_commands.describe(tournament="Турнир")
    @app_commands.autocomplete(tournament=_tournament_autocomplete)
    async def tournament_delete(
        self,
        interaction: discord.Interaction,
        tournament: str,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message("❌ Только для администрации.", ephemeral=True)
            return

        try:
            tid = int(tournament)
        except ValueError:
            await interaction.response.send_message("❌ Неверный ID турнира.", ephemeral=True)
            return

        t = await db.tournament_get(tid)
        if not t or t["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("❌ Турнир не найден.", ephemeral=True)
            return

        if t.get("panel_message_id"):
            try:
                channel = interaction.client.get_channel(t["channel_id"])
                if channel:
                    msg = await channel.fetch_message(t["panel_message_id"])  # type: ignore
                    await msg.delete()
            except Exception:
                pass

        await db.tournament_delete(tid)
        await interaction.response.send_message(
            f"🗑 Турнир **{t['name']}** удалён.", ephemeral=True
        )

    # --- /tournament logchannel ---

    @tournament.command(name="logchannel", description="Установить канал для логов турнира")
    @app_commands.describe(channel="Канал для логов (оставьте пустым чтобы сбросить)")
    async def tournament_logchannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await _is_admin(interaction):
            await interaction.response.send_message("❌ Только для администрации.", ephemeral=True)
            return

        if channel is None:
            await db.tournament_config_set_log_channel(interaction.guild_id, 0)
            await interaction.response.send_message(
                "✅ Канал логов сброшен. Логи больше не отправляются.",
                ephemeral=True,
            )
        else:
            await db.tournament_config_set_log_channel(interaction.guild_id, channel.id)
            await interaction.response.send_message(
                f"✅ Канал логов установлен: {channel.mention}\n"
                f"Теперь сюда будут отправляться: одобрения, отклонения, регистрации, старты матчей и результаты.",
                ephemeral=True,
            )

            # Отправляем тестовое сообщение в канал логов
            embed = discord.Embed(
                title="📋 Канал логов турнира",
                description=f"Настроен администратором {interaction.user.mention}\n\n"
                            f"Сюда будут логироваться:\n"
                            f"• 📝 Регистрации команд\n"
                            f"• ✅ Одобрения команд\n"
                            f"• ❌ Отклонения команд\n"
                            f"• 🏆 Генерация сетки\n"
                            f"• ⚔️ Старты матчей\n"
                            f"• 🏆 Результаты матчей",
                color=config.EMBED_COLOR,
            )
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TournamentCog(bot))
