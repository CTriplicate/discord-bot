"""
Генератор картинки турнирной сетки (Single Elimination).

Алгоритм:
  1. Каждый матч состоит из двух боксов (team1, team2) и соединительных линий.
  2. Позиции вычисляются по рекурсивной формуле:
     - Раунд 1: боксы размещаются равномерно сверху вниз.
     - Каждый следующий раунд: матч центрируется между двумя
       матчами предыдущего раунда, которые в него «кормят».
  3. Соединительные линии рисуются от середины правого края каждого бокса
     к середине левого края бокса следующего раунда.
"""

from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Цвета (Discord dark theme)
# ---------------------------------------------------------------------------

BG_COLOR = "#1E1F22"
BOX_COLOR = "#2B2D31"
BOX_BORDER = "#5865F2"
TEXT_COLOR = "#FFFFFF"
WINNER_COLOR = "#57F287"
TBD_COLOR = "#4F545C"
LINE_COLOR = "#4F545C"
TITLE_COLOR = "#5865F2"
PENDING_BORDER = "#5865F2"
PLAYING_BORDER = "#FEE75C"
COMPLETED_BORDER = "#57F287"

# ---------------------------------------------------------------------------
# Размеры
# ---------------------------------------------------------------------------

BOX_W = 200
BOX_H = 32
GAP_Y = 6           # между team1 и team2 внутри матча
MATCH_GAP_Y = 24    # между матчами в первом раунде
ROUND_GAP_X = 80    # между раундами по горизонтали
MARGIN_TOP = 60
MARGIN_LEFT = 30
FONT_SIZE = 13
TITLE_SIZE = 18


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _truncate(text: str, max_chars: int = 16) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


# ---------------------------------------------------------------------------
# Публичные функции
# ---------------------------------------------------------------------------

def generate_bracket(
    teams: list[dict],
    matches: list[dict],
    tournament_name: str = "Турнир",
) -> BytesIO:
    """
    Генерирует PNG турнирной сетки Single Elimination.

    :param teams: [{id, name, members, ...}, ...]
    :param matches: [{id, team1_id, team2_id, round, match_index,
                      winner_id, status, score}, ...]
    :param tournament_name: заголовок
    :return: BytesIO с PNG
    """
    if not matches:
        return _draw_team_list(teams, tournament_name)

    team_map = {t["id"]: t for t in teams}

    max_round = max(m["round"] for m in matches)
    rounds: dict[int, list[dict]] = {}
    for m in matches:
        rounds.setdefault(m["round"], []).append(m)
    for r in rounds:
        rounds[r].sort(key=lambda m: m["match_index"])

    # --- Вычисляем позиции каждого матча ---
    # match_pos[match_id] = (center_y, x_left)
    # «center_y» — вертикальный центр матча (между team1 и team2)
    match_pos: dict[int, tuple[int, int]] = {}

    # Высота одного матча (2 бокса + промежуток)
    match_h = BOX_H * 2 + GAP_Y

    # Шаг между центрами матчей в первом раунде
    step_r1 = match_h + MATCH_GAP_Y

    # Раунд 1 — равномерно сверху
    for idx, m in enumerate(rounds.get(1, [])):
        cy = MARGIN_TOP + idx * step_r1 + match_h // 2
        x = MARGIN_LEFT
        match_pos[m["id"]] = (cy, x)

    # Последующие раунды — центр между двумя «кормящими» матчами
    for rnd in range(2, max_round + 1):
        prev = rounds.get(rnd - 1, [])
        for idx, m in enumerate(rounds.get(rnd, [])):
            i1 = idx * 2
            i2 = idx * 2 + 1
            if i1 < len(prev) and i2 < len(prev):
                cy1 = match_pos[prev[i1]["id"]][0]
                cy2 = match_pos[prev[i2]["id"]][0]
                cy = (cy1 + cy2) // 2
            elif i1 < len(prev):
                cy = match_pos[prev[i1]["id"]][0]
            else:
                cy = MARGIN_TOP + idx * step_r1 * 2
            x = MARGIN_LEFT + (rnd - 1) * (BOX_W + ROUND_GAP_X)
            match_pos[m["id"]] = (cy, x)

    # --- Размеры картинки ---
    img_w = MARGIN_LEFT + max_round * (BOX_W + ROUND_GAP_X) + 40
    all_cy = [p[0] for p in match_pos.values()]
    img_h = max(all_cy) + match_h // 2 + MARGIN_TOP + 40
    img_h = max(img_h, 200)

    img = Image.new("RGB", (img_w, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = _get_font(FONT_SIZE)
    title_font = _get_font(TITLE_SIZE)

    # Заголовок
    draw.text((MARGIN_LEFT, 14), tournament_name, fill=TITLE_COLOR, font=title_font)

    # --- Рисуем матчи ---
    for rnd in range(1, max_round + 1):
        for m in rounds.get(rnd, []):
            cy, x = match_pos[m["id"]]
            y1 = cy - match_h // 2
            y2 = y1 + BOX_H + GAP_Y

            # Определяем стили
            border_color = PENDING_BORDER
            if m["status"] == "playing":
                border_color = PLAYING_BORDER
            elif m["status"] == "completed":
                border_color = COMPLETED_BORDER

            # --- Team 1 ---
            t1_id = m.get("team1_id", 0)
            t1_name = _get_team_name(team_map, t1_id)
            t1_text_color = TEXT_COLOR
            t1_border = border_color
            if m.get("winner_id") and m["winner_id"] == t1_id:
                t1_text_color = WINNER_COLOR
                t1_border = WINNER_COLOR
            if not t1_id:
                t1_name = "TBD"
                t1_text_color = TBD_COLOR
                t1_border = TBD_COLOR

            draw.rounded_rectangle(
                [x, y1, x + BOX_W, y1 + BOX_H],
                radius=5, fill=BOX_COLOR, outline=t1_border,
            )
            draw.text((x + 8, y1 + 7), _truncate(t1_name), fill=t1_text_color, font=font)

            # --- Team 2 ---
            t2_id = m.get("team2_id", 0)
            t2_name = _get_team_name(team_map, t2_id)
            t2_text_color = TEXT_COLOR
            t2_border = border_color
            if m.get("winner_id") and m["winner_id"] == t2_id:
                t2_text_color = WINNER_COLOR
                t2_border = WINNER_COLOR
            if not t2_id:
                t2_name = "TBD"
                t2_text_color = TBD_COLOR
                t2_border = TBD_COLOR

            draw.rounded_rectangle(
                [x, y2, x + BOX_W, y2 + BOX_H],
                radius=5, fill=BOX_COLOR, outline=t2_border,
            )
            draw.text((x + 8, y2 + 7), _truncate(t2_name), fill=t2_text_color, font=font)

            # --- Score ---
            if m.get("score"):
                score_x = x + BOX_W - 40
                draw.text((score_x, y1 + 7), m["score"], fill=TBD_COLOR, font=font)

            # --- Соединительные линии к следующему раунду ---
            if rnd < max_round:
                next_rnd_matches = rounds.get(rnd + 1, [])
                next_idx = m["match_index"] // 2
                if next_idx < len(next_rnd_matches):
                    nm = next_rnd_matches[next_idx]
                    ncy, nx = match_pos[nm["id"]]
                    ny1 = ncy - match_h // 2
                    ny2 = ny1 + BOX_H + GAP_Y

                    # Определяем, в какой слот ставить (team1 или team2)
                    slot_y = ny1 + BOX_H // 2 if m["match_index"] % 2 == 0 else ny2 + BOX_H // 2

                    # Горизонтальная линия от правого края бокса
                    mid_x = x + BOX_W + ROUND_GAP_X // 2
                    out_y = cy

                    draw.line(
                        [(x + BOX_W, out_y), (mid_x, out_y)],
                        fill=LINE_COLOR, width=2,
                    )
                    draw.line(
                        [(mid_x, out_y), (mid_x, slot_y)],
                        fill=LINE_COLOR, width=2,
                    )
                    draw.line(
                        [(mid_x, slot_y), (nx, slot_y)],
                        fill=LINE_COLOR, width=2,
                    )

            # --- Метка раунда ---
            if rnd == 1:
                round_label = _round_label(max_round, rnd)
                draw.text((x, y1 - 16), round_label, fill=TBD_COLOR, font=font)

    # Метки раундов (только для раундов > 1, первый уже подписан)
    for rnd in range(2, max_round + 1):
        x = MARGIN_LEFT + (rnd - 1) * (BOX_W + ROUND_GAP_X)
        round_label = _round_label(max_round, rnd)
        first_match = rounds.get(rnd, [])[0] if rounds.get(rnd) else None
        if first_match:
            fy = match_pos[first_match["id"]][0] - match_h // 2
            draw.text((x, fy - 16), round_label, fill=TBD_COLOR, font=font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_bracket_simple(
    teams: list[dict],
    tournament_name: str = "Турнир",
) -> BytesIO:
    """Список команд (без матчей)."""
    if not teams:
        img = Image.new("RGB", (400, 120), BG_COLOR)
        draw = ImageDraw.Draw(img)
        title_font = _get_font(TITLE_SIZE)
        font = _get_font(FONT_SIZE)
        draw.text((20, 14), tournament_name, fill=TITLE_COLOR, font=title_font)
        draw.text((20, 50), "Пока нет команд", fill=TBD_COLOR, font=font)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    cols = min(4, max(1, (len(teams) + 7) // 8))
    rows_per_col = math.ceil(len(teams) / cols)

    img_w = MARGIN_LEFT * 2 + cols * (BOX_W + 40)
    img_h = MARGIN_TOP + rows_per_col * (BOX_H + 12) + 20

    img = Image.new("RGB", (img_w, max(img_h, 200)), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = _get_font(FONT_SIZE)
    title_font = _get_font(TITLE_SIZE)

    draw.text((MARGIN_LEFT, 14), tournament_name, fill=TITLE_COLOR, font=title_font)

    for i, t in enumerate(teams):
        col = i // rows_per_col
        row = i % rows_per_col
        x = MARGIN_LEFT + col * (BOX_W + 40)
        y = MARGIN_TOP + row * (BOX_H + 12)

        border = WINNER_COLOR if t.get("approved") else BOX_BORDER
        draw.rounded_rectangle(
            [x, y, x + BOX_W, y + BOX_H],
            radius=5, fill=BOX_COLOR, outline=border,
        )
        status = "✓ " if t.get("approved") else "○ "
        label = f"{status}{_truncate(t['name'])}"
        draw.text((x + 8, y + 7), label, fill=TEXT_COLOR, font=font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Приватные хелперы
# ---------------------------------------------------------------------------

def _get_team_name(team_map: dict, team_id: int) -> str:
    if not team_id or team_id not in team_map:
        return "TBD"
    return team_map[team_id].get("name", "???")


def _round_label(max_round: int, current_round: int) -> str:
    """Человекочитаемое название раунда."""
    diff = max_round - current_round
    if diff == 0:
        return "Финал"
    if diff == 1:
        return "Полуфинал"
    if diff == 2:
        return "Четвертьфинал"
    return f"Раунд {current_round}"
