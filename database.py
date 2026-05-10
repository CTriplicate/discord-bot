"""
Асинхронный слой работы с SQLite.
Все таблицы создаются автоматически при первом запуске.
"""

import aiosqlite
import json
from datetime import datetime
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
    roles_given     TEXT DEFAULT '[]',      -- JSON: список role_id
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS warning_config (
    guild_id        INTEGER PRIMARY KEY,
    roles           TEXT DEFAULT '[]'       -- JSON: список role_id
);

CREATE TABLE IF NOT EXISTS rolls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    message_id      INTEGER DEFAULT 0,
    prize_text      TEXT DEFAULT '',
    end_time        REAL NOT NULL,          -- Unix timestamp
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
    team_size       INTEGER DEFAULT 1,       -- 1=solo, 2=duo, 3=trio и т.д.
    is_team_dm      INTEGER DEFAULT 0,       -- 0=нет, 1=да (командное дм)
    max_teams       INTEGER DEFAULT 0,       -- 0=без лимита
    criteria        TEXT DEFAULT '',          -- критерии прохождения
    status          TEXT DEFAULT 'open',      -- open / closed / finished
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS teams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    name            TEXT NOT NULL,
    members         TEXT DEFAULT '[]',        -- JSON: список user_id
    approved        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    team1_id        INTEGER NOT NULL,
    team2_id        INTEGER DEFAULT 0,        -- 0 = пока нет соперника (bye)
    round           INTEGER DEFAULT 1,
    match_index     INTEGER DEFAULT 0,
    winner_id       INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'pending',   -- pending / playing / completed
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    answers         TEXT DEFAULT '{}',        -- JSON: {вопрос: ответ}
    status          TEXT DEFAULT 'pending',   -- pending / approved / rejected
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


def _connection() -> aiosqlite.Connection:
    return aiosqlite.connect(DB_PATH)


# ===========================================================================
# WARNING
# ===========================================================================

async def warning_add(
    user_id: int,
    guild_id: int,
    moderator_id: int,
    reason: str,
    roles_given: list[int],
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
    """Возвращает True если участник добавлен, False если уже был."""
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
# TOURNAMENT / TEAMS / MATCHES / APPLICATIONS
# ===========================================================================

async def tournament_create(
    guild_id: int,
    channel_id: int,
    name: str,
    team_size: int,
    is_team_dm: bool,
    max_teams: int,
    criteria: str,
) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO tournaments (guild_id, channel_id, name, team_size, "
            "is_team_dm, max_teams, criteria) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, name, team_size, int(is_team_dm), max_teams, criteria),
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


async def tournament_set_status(tournament_id: int, status: str) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE tournaments SET status = ? WHERE id = ?", (status, tournament_id)
        )
        await db.commit()


async def team_create(tournament_id: int, name: str, members: list[int]) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO teams (tournament_id, name, members) VALUES (?, ?, ?)",
            (tournament_id, name, json.dumps(members)),
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
            "SELECT * FROM teams WHERE tournament_id = ? ORDER BY created_at",
            (tournament_id,),
        )
    return [dict(r) for r in rows]


async def team_delete(team_id: int) -> bool:
    async with _connection() as db:
        # Удаляем матчи с этой командой
        await db.execute(
            "DELETE FROM matches WHERE team1_id = ? OR team2_id = ?", (team_id, team_id)
        )
        cursor = await db.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        await db.commit()
        return cursor.rowcount > 0


async def team_set_approved(team_id: int, approved: bool) -> None:
    async with _connection() as db:
        await db.execute(
            "UPDATE teams SET approved = ? WHERE id = ?", (int(approved), team_id)
        )
        await db.commit()


async def match_create(
    tournament_id: int,
    team1_id: int,
    team2_id: int,
    round_num: int,
    match_index: int,
) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO matches (tournament_id, team1_id, team2_id, round, match_index, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (tournament_id, team1_id, team2_id, round_num, match_index),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


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


async def application_create(
    tournament_id: int, user_id: int, answers: dict
) -> int:
    async with _connection() as db:
        cursor = await db.execute(
            "INSERT INTO applications (tournament_id, user_id, answers) VALUES (?, ?, ?)",
            (tournament_id, user_id, json.dumps(answers, ensure_ascii=False)),
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
