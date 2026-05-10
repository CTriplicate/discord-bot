"""
Генератор картинки сетки турнира (Single Elimination).
Использует Pillow для отрисовки турнирной brackets.
"""

from __future__ import annotations

import math
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Константы отрисовки
# ---------------------------------------------------------------------------

BG_COLOR = "#1E1F22"
BOX_COLOR = "#2B2D31"
BOX_BORDER = "#5865F2"
TEXT_COLOR = "#FFFFFF"
WINNER_COLOR = "#57F287"
LINE_COLOR = "#4F545C"
TITLE_COLOR = "#5865F2"

BOX_W = 220
BOX_H = 40
PADDING_X = 60
PADDING_Y = 16
MARGIN_TOP = 60
MARGIN_LEFT = 40
FONT_SIZE = 14
TITLE_SIZE = 20


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Пытаемся загрузить шрифт с кириллицей, fallback — default."""
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


def _truncate(text: str, max_chars: int = 18) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def generate_bracket(
    teams: list[dict],
    matches: list[dict],
    tournament_name: str = "Турнир",
) -> BytesIO:
    """
    Генерирует PNG-изображение турнирной сетки (Single Elimination).

    :param teams: список словарей команд (id, name, members)
    :param matches: список матчей (team1_id, team2_id, round, match_index, winner_id, status)
    :param tournament_name: название турнира
    :return: BytesIO с PNG-изображением
    """
    if not matches:
        # Нет матчей — просто список команд
        h = MARGIN_TOP + len(teams) * (BOX_H + PADDING_Y) + 20
        img = Image.new("RGB", (500, max(h, 200)), BG_COLOR)
        draw = ImageDraw.Draw(img)
        font = _get_font(FONT_SIZE)
        title_font = _get_font(TITLE_SIZE)
        draw.text((20, 16), tournament_name, fill=TITLE_COLOR, font=title_font)
        for i, t in enumerate(teams):
            y = MARGIN_TOP + i * (BOX_H + PADDING_Y)
            draw.rounded_rectangle(
                [MARGIN_LEFT, y, MARGIN_LEFT + BOX_W, y + BOX_H],
                radius=6,
                fill=BOX_COLOR,
                outline=BOX_BORDER,
            )
            draw.text(
                (MARGIN_LEFT + 10, y + 10),
                _truncate(t["name"]),
                fill=TEXT_COLOR,
                font=font,
            )
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    # Определяем количество раундов
    max_round = max(m["round"] for m in matches)
    team_map = {t["id"]: t for t in teams}

    # Считаем размеры
    img_w = MARGIN_LEFT * 2 + max_round * (BOX_W + PADDING_X)
    # Количество матчей в первом раунде
    first_round_matches = [m for m in matches if m["round"] == 1]
    first_round_count = len(first_round_matches)
    # Высота: количество матчей × высота блока × 2 (2 команды в матче)
    img_h = MARGIN_TOP + first_round_count * (BOX_H * 2 + PADDING_Y) + 40

    img = Image.new("RGB", (img_w, max(img_h, 300)), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = _get_font(FONT_SIZE)
    title_font = _get_font(TITLE_SIZE)

    # Заголовок
    draw.text((20, 16), tournament_name, fill=TITLE_COLOR, font=title_font)

    # Группируем матчи по раундам
    rounds: dict[int, list[dict]] = {}
    for m in matches:
        rounds.setdefault(m["round"], []).append(m)

    # Координаты боксов: box_pos[team_id] = (x, y)
    box_pos: dict[int, tuple[int, int]] = {}

    # Рисуем раунд за раундом
    for round_num in range(1, max_round + 1):
        round_matches = rounds.get(round_num, [])
        x = MARGIN_LEFT + (round_num - 1) * (BOX_W + PADDING_X)

        # Вертикальный отступ увеличивается с каждым раундом
        spacing = BOX_H * 2 + PADDING_Y  # базовый отступ для первого раунда
        if round_num > 1:
            spacing = int(spacing * (2 ** (round_num - 1)))

        for idx, m in enumerate(round_matches):
            if round_num == 1:
                y_offset = MARGIN_TOP + idx * spacing
            else:
                # Центрируем относительно матчей предыдущего раунда
                prev_matches = rounds.get(round_num - 1, [])
                pair_start_y = MARGIN_TOP + (idx * 2) * (BOX_H * 2 + PADDING_Y) * (2 ** (round_num - 2))
                # Берём среднюю точку между двумя боксами предыдущего раунда
                if idx < len(prev_matches):
                    # Используем позиции из box_pos
                    t1_id = prev_matches[idx * 2]["team1_id"] if idx * 2 < len(prev_matches) else 0
                    if t1_id in box_pos:
                        y_offset = box_pos[t1_id][1]
                    else:
                        y_offset = MARGIN_TOP + idx * spacing
                else:
                    y_offset = MARGIN_TOP + idx * spacing

            # Команда 1
            t1_name = _truncate(team_map.get(m["team1_id"], {}).get("name", "TBD"))
            t1_color = WINNER_COLOR if m.get("winner_id") == m["team1_id"] else TEXT_COLOR
            box_border1 = WINNER_COLOR if m.get("winner_id") == m["team1_id"] else BOX_BORDER

            y1 = y_offset
            draw.rounded_rectangle(
                [x, y1, x + BOX_W, y1 + BOX_H],
                radius=6,
                fill=BOX_COLOR,
                outline=box_border1,
            )
            draw.text((x + 10, y1 + 10), t1_name, fill=t1_color, font=font)
            if m["team1_id"]:
                box_pos[m["team1_id"]] = (x, y1)

            # Команда 2
            if m.get("team2_id"):
                t2_name = _truncate(team_map.get(m["team2_id"], {}).get("name", "TBD"))
            else:
                t2_name = "TBD"
            t2_color = WINNER_COLOR if m.get("winner_id") == m["team2_id"] else TEXT_COLOR
            box_border2 = WINNER_COLOR if m.get("winner_id") == m["team2_id"] else BOX_BORDER

            y2 = y1 + BOX_H + 4
            draw.rounded_rectangle(
                [x, y2, x + BOX_W, y2 + BOX_H],
                radius=6,
                fill=BOX_COLOR,
                outline=box_border2,
            )
            draw.text((x + 10, y2 + 10), t2_name, fill=t2_color, font=font)
            if m.get("team2_id"):
                box_pos[m["team2_id"]] = (x, y2)

            # Соединительная линия к следующему раунду
            if round_num < max_round:
                mid_y = y1 + BOX_H
                next_x = x + BOX_W + PADDING_X
                draw.line(
                    [(x + BOX_W, y1 + BOX_H // 2), (x + BOX_W + PADDING_X // 2, y1 + BOX_H // 2)],
                    fill=LINE_COLOR,
                    width=2,
                )
                draw.line(
                    [(x + BOX_W, y2 + BOX_H // 2), (x + BOX_W + PADDING_X // 2, y2 + BOX_H // 2)],
                    fill=LINE_COLOR,
                    width=2,
                )
                draw.line(
                    [
                        (x + BOX_W + PADDING_X // 2, y1 + BOX_H // 2),
                        (x + BOX_W + PADDING_X // 2, y2 + BOX_H // 2),
                    ],
                    fill=LINE_COLOR,
                    width=2,
                )

            # Статус матча
            status_text = ""
            if m.get("status") == "playing":
                status_text = "🎮 В процессе"
            elif m.get("status") == "completed":
                status_text = "✅ Завершён"
            if status_text:
                draw.text((x + BOX_W + 8, y1 + BOX_H // 2 - 6), status_text, fill=LINE_COLOR, font=font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_bracket_simple(
    teams: list[dict],
    tournament_name: str = "Турнир",
) -> BytesIO:
    """
    Упрощённая генерация сетки: просто список команд с порядковыми номерами.
    Используется, когда матчи ещё не созданы.
    """
    cols = min(4, max(1, (len(teams) + 7) // 8))
    rows_per_col = math.ceil(len(teams) / cols)

    img_w = MARGIN_LEFT * 2 + cols * (BOX_W + PADDING_X)
    img_h = MARGIN_TOP + rows_per_col * (BOX_H + PADDING_Y) + 20

    img = Image.new("RGB", (img_w, max(img_h, 200)), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = _get_font(FONT_SIZE)
    title_font = _get_font(TITLE_SIZE)

    draw.text((20, 16), tournament_name, fill=TITLE_COLOR, font=title_font)

    for i, t in enumerate(teams):
        col = i // rows_per_col
        row = i % rows_per_col
        x = MARGIN_LEFT + col * (BOX_W + PADDING_X)
        y = MARGIN_TOP + row * (BOX_H + PADDING_Y)

        draw.rounded_rectangle(
            [x, y, x + BOX_W, y + BOX_H],
            radius=6,
            fill=BOX_COLOR,
            outline=BOX_BORDER,
        )
        label = f"{i + 1}. {_truncate(t['name'])}"
        draw.text((x + 10, y + 10), label, fill=TEXT_COLOR, font=font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
