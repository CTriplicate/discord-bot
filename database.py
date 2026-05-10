"""
Асинхронный слой работы с SQLite.
Все таблицы создаются автоматически при первом запуске.
"""

import aiosqlite
import json
from pathlib import Path

from config import DATABASE

DB_PATH = Path(__file__).parent / DATABASE

# ---------------------------------------------------------------------------
# Инициализация
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS warnings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    moderator_id    INTEGER NOT NULL,
    reason          TEXT DEFAULT '',
    roles_given     TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS warning_config (
    guild_id        INTEGER PRIMARY KEY,
    roles           TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS rolls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    message_id      INTEGER DEFAULT 0,
    prize_text      TEXT DEFAULT '',
    end_time        REAL NOT NULL,
    active          INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS roll_participants (
    roll_id         INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    PRIMARY KEY (roll_id, user_id)
);

CREATE TABLE IF NOT EXISTS tournaments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    name            TEXT NOT NULL,
    team_size       INTEGER DEFAULT 1,
    is_team_dm      INTEGER DEFAULT 0,
    max_teams       INTEGER DEFAULT 0,
    criteria        TEXT DEFAULT '',
    status          TEXT DEFAULT 'open',
    description     TEXT DEFAULT '',
    panel_message_id INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS teams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    name            TEXT NOT NULL,
    members         TEXT DEFAULT '[]',
    approved        INTEGER DEFAULT 0,
    seed            INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    team1_id        INTEGER DEFAULT 0,
    team2_id        INTEGER DEFAULT 0,
    round           INTEGER DEFAULT 1,
    match_index     INTEGER DEFAULT 0,
    winner_id       INTEGER DEFAULT 0,
    score           TEXT DEFAULT '',
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    team_name       TEXT DEFAULT '',
    answers         TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tournament_questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    question_text   TEXT NOT NULL,
    position        INTEGER DEFAULT 0,
    required        INTEGER DEFAULT 1
);
"""

# Миграции для существующих баз
MIGRATIONS = [
    "ALTER TABLE tournaments ADD COLUMN panel_message_id INTEGER DEFAULT 0",
]


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет. Применяет миграции."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()

    # Миграции (безопасные — игнорируем, если колонка уже есть)
    for migration in MIGRATIONS:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(migration)
                await db.commit()
        except aiosqlite.OperationalError:
            pass  # Колонка уже существует


def _connection() -> aiosqlite.Connection:
    return aiosqlite.connect(DB_PATH)


# ===========================================================================
# WARNING
# ===========================================================================

async def warning_add(
    user_id: int, guild_id: int, moderator_id: int, reason: str, roles_given: list[int]
) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO warnings (user_id, guild_id, moderator_id, reason, roles_given) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, guild_id, moderator_id, reason, json.dumps(roles_given)),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def warning_remove(warning_id: int, guild_id: int) -> bool:
    async with _connection() as db:
        cursor = await db.execute(
            "DELETE FROM warnings WHERE id = ? AND guild_id = ?", (warning_id, guild_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def warning_list(guild_id: int, user_id: int | None = None) -> list[dict]:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        if user_id:
            rows = await db.execute_fetchall(
                "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC",
                (guild_id, user_id),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM warnings WHERE guild_id = ? ORDER BY created_at DESC",
                (guild_id,),
            )
    return [dict(r) for r in rows]


async def warning_get_roles(guild_id: int) -> list[int]:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute_fetchall(
            "SELECT roles FROM warning_config WHERE guild_id = ?", (guild_id,)
        )
    if not row:
        return []
    return json.loads(row[0]["roles"])


async def warning_set_roles(guild_id: int, roles: list[int]) -> None:
    async with _connection() as db:
        await db.execute(
            "INSERT INTO warning_config (guild_id, roles) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET roles = excluded.roles",
            (guild_id, json.dumps(roles)),
        )
        await db.commit()


async def warning_count(guild_id: int, user_id: int) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
    return row[0]  # type: ignore


# ===========================================================================
# ROLL
# ===========================================================================

async def roll_create(
    guild_id: int, channel_id: int, prize_text: str, end_time: float
) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO rolls (guild_id, channel_id, prize_text, end_time, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (guild_id, channel_id, prize_text, end_time),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def roll_set_message(roll_id: int, message_id: int) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE rolls SET message_id = ? WHERE id = ?", (message_id, roll_id)
        )
        await db.commit()


async def roll_get(roll_id: int) -> dict | None:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM rolls WHERE id = ?", (roll_id,))
    return dict(rows[0]) if rows else None


async def roll_get_active(guild_id: int) -> list[dict]:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM rolls WHERE guild_id = ? AND active = 1", (guild_id,)
        )
    return [dict(r) for r in rows]


async def roll_participant_add(roll_id: int, user_id: int) -> bool:
    async with _connection() as db:
        try:
            await db.execute(
                "INSERT INTO roll_participants (roll_id, user_id) VALUES (?, ?)",
                (roll_id, user_id),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def roll_participant_remove(roll_id: int, user_id: int) -> bool:
    async with _connection() as db:
        cursor = await db.execute(
            "DELETE FROM roll_participants WHERE roll_id = ? AND user_id = ?",
            (roll_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def roll_participants_list(roll_id: int) -> list[int]:
    async with _connection() as db:
        rows = await db.execute_fetchall(
            "SELECT user_id FROM roll_participants WHERE roll_id = ?", (roll_id,)
        )
    return [r[0] for r in rows]


async def roll_finish(roll_id: int) -> None:
    async with _connection() as db:
        await db.execute("UPDATE rolls SET active = 0 WHERE id = ?", (roll_id,))
        await db.commit()


async def roll_delete(roll_id: int) -> bool:
    async with _connection() as db:
        await db.execute("DELETE FROM roll_participants WHERE roll_id = ?", (roll_id,))
        cursor = await db.execute("DELETE FROM rolls WHERE id = ?", (roll_id,))
        await db.commit()
        return cursor.rowcount > 0


# ===========================================================================
# TOURNAMENT
# ===========================================================================

async def tournament_create(
    guild_id: int, channel_id: int, name: str, team_size: int,
    is_team_dm: bool, max_teams: int, description: str,
) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO tournaments (guild_id, channel_id, name, team_size, "
            "is_team_dm, max_teams, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, name, team_size, int(is_team_dm), max_teams, description),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def tournament_get(tournament_id: int) -> dict | None:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
        )
    return dict(rows[0]) if rows else None


async def tournament_list(guild_id: int) -> list[dict]:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM tournaments WHERE guild_id = ? ORDER BY created_at DESC",
            (guild_id,),
        )
    return [dict(r) for r in rows]


async def tournament_list_by_format(guild_id: int, team_size: int, status: str = "open") -> list[dict]:
    """Возвращает турниры по формату (team_size) и статусу."""
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        if team_size == 0:
            # custom = team_size >= 4
            rows = await db.execute_fetchall(
                "SELECT * FROM tournaments WHERE guild_id = ? AND status = ? AND team_size >= 4 ORDER BY created_at DESC",
                (guild_id, status),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM tournaments WHERE guild_id = ? AND status = ? AND team_size = ? ORDER BY created_at DESC",
                (guild_id, status, team_size),
            )
    return [dict(r) for r in rows]


async def tournament_set_status(tournament_id: int, status: str) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE tournaments SET status = ? WHERE id = ?", (status, tournament_id)
        )
        await db.commit()


async def tournament_set_panel(tournament_id: int, channel_id: int, message_id: int) -> None:
    """Сохраняет ID сообщения-панели турнира."""
    async with _connection() as db:
        await db.execute(
            "UPDATE tournaments SET channel_id = ?, panel_message_id = ? WHERE id = ?",
            (channel_id, message_id, tournament_id),
        )
        await db.commit()


async def tournament_delete(tournament_id: int) -> bool:
    async with _connection() as db:
        await db.execute("DELETE FROM tournament_questions WHERE tournament_id = ?", (tournament_id,))
        await db.execute("DELETE FROM applications WHERE tournament_id = ?", (tournament_id,))
        await db.execute("DELETE FROM matches WHERE tournament_id = ?", (tournament_id,))
        await db.execute("DELETE FROM teams WHERE tournament_id = ?", (tournament_id,))
        cursor = await db.execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))
        await db.commit()
        return cursor.rowcount > 0


# ===========================================================================
# TOURNAMENT QUESTIONS
# ===========================================================================

async def question_add(tournament_id: int, question_text: str, required: bool = True) -> int:
    """Добавляет вопрос в анкету турнира. Позиция = max+1."""
    async with _connection() as db:
        # Получаем максимальную позицию
        cursor = await db.execute(
            "SELECT COALESCE(MAX(position), -1) FROM tournament_questions WHERE tournament_id = ?",
            (tournament_id,),
        )
        row = await cursor.fetchone()
        max_pos = row[0] + 1  # type: ignore

        cursor = await db.execute(
            "INSERT INTO tournament_questions (tournament_id, question_text, position, required) "
            "VALUES (?, ?, ?, ?)",
            (tournament_id, question_text, max_pos, int(required)),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def question_remove(question_id: int) -> bool:
    async with _connection() as db:
        cursor = await db.execute(
            "DELETE FROM tournament_questions WHERE id = ?", (question_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def question_list(tournament_id: int) -> list[dict]:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM tournament_questions WHERE tournament_id = ? ORDER BY position",
            (tournament_id,),
        )
    return [dict(r) for r in rows]


async def question_clear(tournament_id: int) -> int:
    """Удаляет все вопросы турнира. Возвращает количество удалённых."""
    async with _connection() as db:
        cursor = await db.execute(
            "DELETE FROM tournament_questions WHERE tournament_id = ?", (tournament_id,)
        )
        await db.commit()
        return cursor.rowcount  # type: ignore


async def question_count(tournament_id: int) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM tournament_questions WHERE tournament_id = ?",
            (tournament_id,),
        )
        row = await cursor.fetchone()
    return row[0]  # type: ignore


# ===========================================================================
# TEAMS
# ===========================================================================

async def team_create(tournament_id: int, name: str, members: list[int], seed: int = 0) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO teams (tournament_id, name, members, seed) VALUES (?, ?, ?, ?)",
            (tournament_id, name, json.dumps(members), seed),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def team_get(team_id: int) -> dict | None:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM teams WHERE id = ?", (team_id,))
    return dict(rows[0]) if rows else None


async def team_list(tournament_id: int) -> list[dict]:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM teams WHERE tournament_id = ? ORDER BY seed, created_at",
            (tournament_id,),
        )
    return [dict(r) for r in rows]


async def team_delete(team_id: int) -> bool:
    async with _connection() as db:
        cursor = await db.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        await db.commit()
        return cursor.rowcount > 0


async def team_set_approved(team_id: int, approved: bool) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE teams SET approved = ? WHERE id = ?", (int(approved), team_id)
        )
        await db.commit()


async def team_set_seed(team_id: int, seed: int) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE teams SET seed = ? WHERE id = ?", (seed, team_id)
        )
        await db.commit()


# ===========================================================================
# MATCHES
# ===========================================================================

async def match_create(
    tournament_id: int, team1_id: int, team2_id: int,
    round_num: int, match_index: int,
) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO matches (tournament_id, team1_id, team2_id, round, match_index, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (tournament_id, team1_id, team2_id, round_num, match_index),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def match_get(match_id: int) -> dict | None:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM matches WHERE id = ?", (match_id,))
    return dict(rows[0]) if rows else None


async def match_list(tournament_id: int) -> list[dict]:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM matches WHERE tournament_id = ? ORDER BY round, match_index",
            (tournament_id,),
        )
    return [dict(r) for r in rows]


async def match_set_status(match_id: int, status: str) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE matches SET status = ? WHERE id = ?", (status, match_id)
        )
        await db.commit()


async def match_set_winner(match_id: int, winner_id: int) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE matches SET winner_id = ?, status = 'completed' WHERE id = ?",
            (winner_id, match_id),
        )
        await db.commit()


async def match_set_score(match_id: int, score: str) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE matches SET score = ? WHERE id = ?", (score, match_id)
        )
        await db.commit()


async def match_update_team(match_id: int, slot: str, team_id: int) -> None:
    """Обновляет team1_id или team2_id в матче."""
    if slot not in ("team1_id", "team2_id"):
        return
    async with _connection() as db:
        await db.execute(
            f"UPDATE matches SET {slot} = ? WHERE id = ?", (team_id, match_id)
        )
        await db.commit()


async def match_delete_for_tournament(tournament_id: int) -> None:
    async with _connection() as db:
        await db.execute("DELETE FROM matches WHERE tournament_id = ?", (tournament_id,))
        await db.commit()


# ===========================================================================
# APPLICATIONS
# ===========================================================================

async def application_create(
    tournament_id: int, user_id: int, team_name: str, answers: dict
) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO applications (tournament_id, user_id, team_name, answers) VALUES (?, ?, ?, ?)",
            (tournament_id, user_id, team_name, json.dumps(answers, ensure_ascii=False)),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def application_list(tournament_id: int, status: str | None = None) -> list[dict]:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        if status:
            rows = await db.execute_fetchall(
                "SELECT * FROM applications WHERE tournament_id = ? AND status = ?",
                (tournament_id, status),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM applications WHERE tournament_id = ?", (tournament_id,)
            )
    return [dict(r) for r in rows]


async def application_set_status(app_id: int, status: str) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE applications SET status = ? WHERE id = ?", (status, app_id)
        )
        await db.commit()


async def application_get(app_id: int) -> dict | None:
    async with _connection() as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM applications WHERE id = ?", (app_id,))
    return dict(rows[0]) if rows else None
