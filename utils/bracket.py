"""
Генератор текстовой турнирной сетки (Single Elimination).

Стиль: текстовая сетка с использованием Unicode box-drawing символов.
- Код-блок для моноширинного отображения в Discord
- Матчи с двумя слотами команд, связанные линиями
- Автоматическое масштабирование под количество участников
- Статусы матчей: ⏳ ожидание, ⚔️ идёт, ✅ завершён
"""

from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _round_label(max_round: int, current_round: int) -> str:
    diff = max_round - current_round
    if diff == 0:
        return "Финал"
    if diff == 1:
        return "Полуфинал"
    if diff == 2:
        return "Четвертьфинал"
    return f"Раунд {current_round}"


def _team_name(team_id: int, team_map: dict, winner_id: int = 0) -> str:
    """Возвращает отображаемое имя команды с форматированием."""
    if not team_id:
        return "TBD"

    team = team_map.get(team_id)
    if not team:
        return "???"

    name = team.get("name", "???")
    seed = team.get("seed", 0)

    # Добавляем посев (seed), если есть
    display = f"{seed}. {name}" if seed and seed > 0 else name

    has_winner = bool(winner_id)

    if winner_id and winner_id == team_id:
        # Победитель
        return f"**{display}**"
    elif has_winner:
        # Проигравший
        return f"~~{display}~~"
    else:
        return display


def _match_status_emoji(status: str) -> str:
    if status == "playing":
        return "⚔️"
    elif status == "completed":
        return "✅"
    return "⏳"


def _clip(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ---------------------------------------------------------------------------
# Главная функция — генерация текстовой сетки с матчами
# ---------------------------------------------------------------------------

def generate_bracket(
    teams: list[dict],
    matches: list[dict],
    tournament_name: str = "Турнир",
) -> str:
    """Генерирует текстовую турнирную сетку Single Elimination."""
    if not matches:
        return generate_bracket_simple(teams, tournament_name)

    team_map = {t["id"]: t for t in teams}
    max_round = max(m["round"] for m in matches)

    # Группируем матчи по раундам
    rounds: dict[int, list[dict]] = {}
    for m in matches:
        rounds.setdefault(m["round"], []).append(m)
    for r in rounds:
        rounds[r].sort(key=lambda m: m["match_index"])

    # --- Вычисляем ширину имён команд ---
    max_name_len = 14  # минимум
    for m in matches:
        for slot in ("team1_id", "team2_id"):
            tid = m.get(slot, 0)
            if tid and tid in team_map:
                name = team_map[tid].get("name", "")
                seed = team_map[tid].get("seed", 0)
                display = f"{seed}. {name}" if seed and seed > 0 else name
                max_name_len = max(max_name_len, len(display) + 4)  # запас на форматирование

    max_name_len = min(max_name_len, 28)  # ограничение для Discord

    # --- Строим текст ---
    lines: list[str] = []

    # Заголовок
    lines.append(f"🏆 {tournament_name}")
    lines.append("")

    # Для каждого раунда выводим матчи
    for rnd in range(1, max_round + 1):
        round_matches = rounds.get(rnd, [])
        if not round_matches:
            continue

        label = _round_label(max_round, rnd)
        lines.append(f"── {label} {'─' * max(1, 40 - len(label) - 4)}")

        for m in round_matches:
            t1_id = m.get("team1_id", 0)
            t2_id = m.get("team2_id", 0)
            winner_id = m.get("winner_id", 0) or 0
            status = m.get("status", "pending")
            score = m.get("score", "") or ""
            match_num = m.get("match_index", 0) + 1

            t1_name = _team_name(t1_id, team_map, winner_id)
            t2_name = _team_name(t2_id, team_map, winner_id)

            # Обрезаем имена
            t1_display = _clip(t1_name, max_name_len)
            t2_display = _clip(t2_name, max_name_len)

            emoji = _match_status_emoji(status)

            # Если Bye (одна команда без соперника)
            if t1_id and not t2_id:
                t1_display = _clip(t1_name, max_name_len)
                lines.append(f"  М{match_num:<2} {t1_display:<{max_name_len}}  (bye)  {emoji}")
            elif not t1_id and t2_id:
                t2_display = _clip(t2_name, max_name_len)
                lines.append(f"  М{match_num:<2} {t2_display:<{max_name_len}}  (bye)  {emoji}")
            else:
                # Обычный матч
                score_str = f"  {score}" if score else ""
                lines.append(
                    f"  М{match_num:<2} {t1_display:<{max_name_len}}  vs  "
                    f"{t2_display:<{max_name_len}}  {emoji}{score_str}"
                )

        lines.append("")

    # --- Итог: чемпион ---
    final_matches = rounds.get(max_round, [])
    if final_matches:
        fm = final_matches[0]
        if fm.get("winner_id") and fm["winner_id"] in team_map:
            champ = team_map[fm["winner_id"]]
            lines.append(f"🏆 Чемпион: **{champ.get('name', '???')}**")

    # Ограничиваем длину для Discord (embed description limit ~4096)
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3890] + "\n..."

    return text


# ---------------------------------------------------------------------------
# Простая сетка (превью — без матчей, только команды)
# ---------------------------------------------------------------------------

def generate_bracket_simple(
    teams: list[dict],
    tournament_name: str = "Турнир",
) -> str:
    """Превью сетки — показывает посев команд как в турнирной сетке."""
    if not teams:
        return f"🏆 {tournament_name}\n\nПока нет команд"

    approved = [t for t in teams if t.get("approved")]
    if not approved:
        return f"🏆 {tournament_name}\n\nНет одобренных команд"

    lines: list[str] = []
    lines.append(f"🏆 {tournament_name}")
    lines.append("")

    n = len(approved)
    bracket_size = 1
    while bracket_size < n:
        bracket_size *= 2

    num_rounds = int(math.log2(bracket_size))
    first_round_matches = bracket_size // 2
    byes = bracket_size - n

    # Seeding
    seeded = list(approved)
    seeded = _standard_seed_order(seeded)

    # Расставляем команды в матчи первого раунда
    r1_matchups: list[tuple] = []  # (team1_or_None, team2_or_None)
    team_idx = 0
    for i in range(first_round_matches):
        t1 = None
        t2 = None
        if i < byes:
            if team_idx < n:
                t1 = seeded[team_idx]
                team_idx += 1
            t2 = None  # Bye
        else:
            if team_idx < n:
                t1 = seeded[team_idx]
                team_idx += 1
            if team_idx < n:
                t2 = seeded[team_idx]
                team_idx += 1
        r1_matchups.append((t1, t2))

    # --- Ширина ---
    max_name_len = 14
    for t in approved:
        seed = t.get("seed", 0)
        display = f"{seed}. {t['name']}" if seed and seed > 0 else t["name"]
        max_name_len = max(max_name_len, len(display) + 2)
    max_name_len = min(max_name_len, 28)

    # Выводим первый раунд
    label = _round_label(num_rounds, 1)
    lines.append(f"── {label} {'─' * max(1, 40 - len(label) - 4)}")

    for idx, (t1, t2) in enumerate(r1_matchups):
        match_num = idx + 1
        if t1 and not t2:
            name1 = _clip(t1.get("name", "???"), max_name_len)
            lines.append(f"  М{match_num:<2} {name1:<{max_name_len}}  (bye)")
        elif t1 and t2:
            name1 = _clip(t1.get("name", "???"), max_name_len)
            name2 = _clip(t2.get("name", "???"), max_name_len)
            lines.append(f"  М{match_num:<2} {name1:<{max_name_len}}  vs  {name2:<{max_name_len}}")
        elif t1:
            name1 = _clip(t1.get("name", "???"), max_name_len)
            lines.append(f"  М{match_num:<2} {name1:<{max_name_len}}  (bye)")

    lines.append("")

    # Показываем структуру следующих раундов
    for rnd in range(2, num_rounds + 1):
        matches_in_round = bracket_size // (2 ** rnd)
        label = _round_label(num_rounds, rnd)
        lines.append(f"── {label} {'─' * max(1, 40 - len(label) - 4)}")
        for idx in range(matches_in_round):
            match_num = idx + 1
            prev1 = idx * 2 + 1
            prev2 = idx * 2 + 2
            lines.append(f"  М{match_num:<2} Победитель М{prev1}  vs  Победитель М{prev2}")
        lines.append("")

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3890] + "\n..."

    return text


# ---------------------------------------------------------------------------
# Хелперы посева
# ---------------------------------------------------------------------------

def _standard_seed_order(teams: list[dict]) -> list[dict]:
    """
    Расставляет команды по стандартному турнирному посеву.
    Для 8 команд: 1,8,4,5,3,6,2,7
    Это гарантирует, что 1 и 2 семя встретятся только в финале.
    """
    n = len(teams)
    if n <= 2:
        return teams

    bracket_size = 1
    while bracket_size < n:
        bracket_size *= 2

    order = _seed_positions(bracket_size)
    result = [None] * n
    pos = 0
    for seed_pos in order:
        if pos < n and seed_pos < n:
            result[seed_pos] = teams[pos]
            pos += 1

    # Заполняем None
    final = []
    used = set()
    for item in result:
        if item is not None:
            final.append(item)
            used.add(item["id"])
    for t in teams:
        if t["id"] not in used:
            final.append(t)
    return final


def _seed_positions(size: int) -> list[int]:
    """
    Возвращает массив позиций для стандартного турнирного посева.
    Для size=8: [0, 7, 3, 4, 1, 6, 2, 5]
    (1v8, 4v5, 3v6, 2v7)
    """
    if size == 1:
        return [0]
    if size == 2:
        return [0, 1]

    half = size // 2
    sub = _seed_positions(half)
    result = []
    for s in sub:
        result.append(s)
        result.append(size - 1 - s)
    return result
