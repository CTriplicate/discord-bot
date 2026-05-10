"""
Генератор картинки турнирной сетки (Single Elimination).

Особенности:
  - Discord dark theme дизайн с градиентами и тенями
  - Поддержка кириллицы (DejaVu Sans + Noto Sans SC fallback)
  - Адаптивный размер картинки
  - Цветовое кодирование статусов матчей
"""

from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Цвета (Discord dark theme + акценты)
# ---------------------------------------------------------------------------

BG_COLOR = "#1E1F22"
BG_GRADIENT_TOP = "#2B2D31"
BG_GRADIENT_BOT = "#1E1F22"
BOX_COLOR = "#2B2D31"
BOX_HOVER = "#313338"
TEXT_COLOR = "#FFFFFF"
SUBTEXT_COLOR = "#B5BAC1"
TBD_COLOR = "#6D6F78"
LINE_COLOR = "#4F545C"
LINE_GLOW = "#5865F2"

TITLE_COLOR = "#5865F2"
TITLE_GLOW = "#4752C4"

WINNER_COLOR = "#57F287"
WINNER_BG = "#1A3A2A"
LOSER_COLOR = "#ED4245"

PENDING_BORDER = "#5865F2"
PENDING_BG = "#232538"
PLAYING_BORDER = "#FEE75C"
PLAYING_BG = "#33322A"
COMPLETED_BORDER = "#57F287"
COMPLETED_BG = "#1E3A2A"

BYE_COLOR = "#4F545C"
BYE_BG = "#232428"

SEED_COLOR = "#5865F2"
SCORE_COLOR = "#B5BAC1"

ROUND_LABEL_COLOR = "#5865F2"
ROUND_BG = "#232538"

# ---------------------------------------------------------------------------
# Размеры
# ---------------------------------------------------------------------------

BOX_W = 220
BOX_H = 36
GAP_Y = 4           # между team1 и team2 внутри матча
MATCH_GAP_Y = 28    # между матчами в первом раунде
ROUND_GAP_X = 90    # между раундами по горизонтали
MARGIN_TOP = 70
MARGIN_LEFT = 40
FONT_SIZE = 13
SEED_FONT_SIZE = 10
TITLE_SIZE = 20
ROUND_LABEL_H = 24
SHADOW_OFFSET = 2


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.append("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    candidates.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _truncate(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
              max_width: int) -> str:
    """Обрезает текст, чтобы он помещался в max_width пикселей."""
    if font.getlength(text) <= max_width:
        return text
    while len(text) > 1 and font.getlength(text + "...") > max_width:
        text = text[:-1]
    return text + "..."


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
        return generate_bracket_simple(teams, tournament_name)

    team_map = {t["id"]: t for t in teams}

    max_round = max(m["round"] for m in matches)
    rounds: dict[int, list[dict]] = {}
    for m in matches:
        rounds.setdefault(m["round"], []).append(m)
    for r in rounds:
        rounds[r].sort(key=lambda m: m["match_index"])

    # --- Вычисляем позиции каждого матча ---
    match_pos: dict[int, tuple[int, int]] = {}

    match_h = BOX_H * 2 + GAP_Y
    step_r1 = match_h + MATCH_GAP_Y

    # Раунд 1 — равномерно сверху
    for idx, m in enumerate(rounds.get(1, [])):
        cy = MARGIN_TOP + ROUND_LABEL_H + idx * step_r1 + match_h // 2
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
                cy = MARGIN_TOP + ROUND_LABEL_H + idx * step_r1 * 2
            x = MARGIN_LEFT + (rnd - 1) * (BOX_W + ROUND_GAP_X)
            match_pos[m["id"]] = (cy, x)

    # --- Размеры картинки ---
    img_w = MARGIN_LEFT + max_round * (BOX_W + ROUND_GAP_X) + 40
    all_cy = [p[0] for p in match_pos.values()]
    img_h = max(all_cy) + match_h // 2 + MARGIN_TOP + 20
    img_h = max(img_h, 200)

    img = Image.new("RGB", (img_w, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = _get_font(FONT_SIZE)
    font_bold = _get_font(FONT_SIZE, bold=True)
    seed_font = _get_font(SEED_FONT_SIZE)
    title_font = _get_font(TITLE_SIZE, bold=True)
    round_font = _get_font(SEED_FONT_SIZE + 1, bold=True)

    # --- Градиентный фон ---
    for y in range(img_h):
        r1, g1, b1 = int(BG_GRADIENT_TOP[1:3], 16), int(BG_GRADIENT_TOP[3:5], 16), int(BG_GRADIENT_TOP[5:7], 16)
        r2, g2, b2 = int(BG_GRADIENT_BOT[1:3], 16), int(BG_GRADIENT_BOT[3:5], 16), int(BG_GRADIENT_BOT[5:7], 16)
        t = y / max(img_h, 1)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        draw.line([(0, y), (img_w, y)], fill=(r, g, b))

    # --- Заголовок ---
    title_y = 16
    # Тень заголовка
    draw.text((MARGIN_LEFT + 1, title_y + 1), tournament_name, fill="#000000", font=title_font)
    draw.text((MARGIN_LEFT, title_y), tournament_name, fill=TITLE_COLOR, font=title_font)

    # --- Метки раундов ---
    for rnd in range(1, max_round + 1):
        x = MARGIN_LEFT + (rnd - 1) * (BOX_W + ROUND_GAP_X)
        label = _round_label(max_round, rnd)
        # Фон метки раунда
        label_w = max(BOX_W, round_font.getlength(label) + 16)
        first_match = rounds.get(rnd, [])[0] if rounds.get(rnd) else None
        if first_match:
            fy = match_pos[first_match["id"]][0] - match_h // 2 - ROUND_LABEL_H - 4
        else:
            fy = MARGIN_TOP
        draw.rounded_rectangle(
            [x, fy, x + label_w, fy + ROUND_LABEL_H],
            radius=4, fill=ROUND_BG,
        )
        draw.text((x + 8, fy + 5), label, fill=ROUND_LABEL_COLOR, font=round_font)

    # --- Рисуем матчи ---
    for rnd in range(1, max_round + 1):
        for m in rounds.get(rnd, []):
            cy, x = match_pos[m["id"]]
            y1 = cy - match_h // 2
            y2 = y1 + BOX_H + GAP_Y

            # Определяем стили матча
            border_color = PENDING_BORDER
            match_bg = PENDING_BG
            if m["status"] == "playing":
                border_color = PLAYING_BORDER
                match_bg = PLAYING_BG
            elif m["status"] == "completed":
                border_color = COMPLETED_BORDER
                match_bg = COMPLETED_BG

            # --- Team 1 ---
            t1_id = m.get("team1_id", 0)
            t1_name = _get_team_name(team_map, t1_id)
            t1_text_color = TEXT_COLOR
            t1_border = border_color
            t1_bg = BOX_COLOR
            t1_is_winner = False
            if m.get("winner_id") and m["winner_id"] == t1_id:
                t1_text_color = WINNER_COLOR
                t1_border = WINNER_COLOR
                t1_bg = WINNER_BG
                t1_is_winner = True
            if not t1_id:
                t1_name = "TBD"
                t1_text_color = TBD_COLOR
                t1_border = TBD_COLOR
                t1_bg = BYE_BG

            # Тень бокса
            draw.rounded_rectangle(
                [x + SHADOW_OFFSET, y1 + SHADOW_OFFSET, x + BOX_W + SHADOW_OFFSET, y1 + BOX_H + SHADOW_OFFSET],
                radius=6, fill="#111214",
            )
            # Бокс
            draw.rounded_rectangle(
                [x, y1, x + BOX_W, y1 + BOX_H],
                radius=6, fill=t1_bg, outline=t1_border, width=2,
            )
            # Текст с обрезкой
            text_x = x + 10
            max_text_w = BOX_W - 60  # запас для счёта
            display_name = _truncate(t1_name, font_bold if t1_is_winner else font, max_text_w)
            use_font = font_bold if t1_is_winner else font
            draw.text((text_x, y1 + 9), display_name, fill=t1_text_color, font=use_font)

            # Сид (если есть)
            if t1_id and t1_id in team_map:
                seed = team_map[t1_id].get("seed", 0)
                if seed and seed > 0:
                    seed_str = str(seed)
                    seed_w = seed_font.getlength(seed_str) + 8
                    draw.rounded_rectangle(
                        [x + BOX_W - seed_w - 6, y1 + 3, x + BOX_W - 6, y1 + 3 + 16],
                        radius=3, fill="#1E1F22",
                    )
                    draw.text((x + BOX_W - seed_w - 2, y1 + 5), seed_str, fill=SEED_COLOR, font=seed_font)

            # --- Team 2 ---
            t2_id = m.get("team2_id", 0)
            t2_name = _get_team_name(team_map, t2_id)
            t2_text_color = TEXT_COLOR
            t2_border = border_color
            t2_bg = BOX_COLOR
            t2_is_winner = False
            if m.get("winner_id") and m["winner_id"] == t2_id:
                t2_text_color = WINNER_COLOR
                t2_border = WINNER_COLOR
                t2_bg = WINNER_BG
                t2_is_winner = True
            if not t2_id:
                t2_name = "TBD"
                t2_text_color = TBD_COLOR
                t2_border = TBD_COLOR
                t2_bg = BYE_BG

            # Тень бокса
            draw.rounded_rectangle(
                [x + SHADOW_OFFSET, y2 + SHADOW_OFFSET, x + BOX_W + SHADOW_OFFSET, y2 + BOX_H + SHADOW_OFFSET],
                radius=6, fill="#111214",
            )
            # Бокс
            draw.rounded_rectangle(
                [x, y2, x + BOX_W, y2 + BOX_H],
                radius=6, fill=t2_bg, outline=t2_border, width=2,
            )
            display_name2 = _truncate(t2_name, font_bold if t2_is_winner else font, max_text_w)
            use_font2 = font_bold if t2_is_winner else font
            draw.text((text_x, y2 + 9), display_name2, fill=t2_text_color, font=use_font2)

            # Сид для team2
            if t2_id and t2_id in team_map:
                seed2 = team_map[t2_id].get("seed", 0)
                if seed2 and seed2 > 0:
                    seed_str2 = str(seed2)
                    seed_w2 = seed_font.getlength(seed_str2) + 8
                    draw.rounded_rectangle(
                        [x + BOX_W - seed_w2 - 6, y2 + 3, x + BOX_W - 6, y2 + 3 + 16],
                        radius=3, fill="#1E1F22",
                    )
                    draw.text((x + BOX_W - seed_w2 - 2, y2 + 5), seed_str2, fill=SEED_COLOR, font=seed_font)

            # --- Score ---
            if m.get("score"):
                score_x = x + BOX_W - 50
                draw.text((score_x, y1 + 9), m["score"], fill=SCORE_COLOR, font=font)

            # --- Соединительные линии к следующему раунду ---
            if rnd < max_round:
                next_rnd_matches = rounds.get(rnd + 1, [])
                next_idx = m["match_index"] // 2
                if next_idx < len(next_rnd_matches):
                    nm = next_rnd_matches[next_idx]
                    ncy, nx = match_pos[nm["id"]]
                    ny1 = ncy - match_h // 2
                    ny2 = ny1 + BOX_H + GAP_Y

                    slot_y = ny1 + BOX_H // 2 if m["match_index"] % 2 == 0 else ny2 + BOX_H // 2

                    mid_x = x + BOX_W + ROUND_GAP_X // 2
                    out_y = cy

                    # Свечение линии (толстая полупрозрачная)
                    draw.line(
                        [(x + BOX_W, out_y), (mid_x, out_y)],
                        fill=LINE_GLOW, width=3,
                    )
                    draw.line(
                        [(mid_x, out_y), (mid_x, slot_y)],
                        fill=LINE_GLOW, width=3,
                    )
                    draw.line(
                        [(mid_x, slot_y), (nx, slot_y)],
                        fill=LINE_GLOW, width=3,
                    )

                    # Основная линия
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

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_bracket_simple(
    teams: list[dict],
    tournament_name: str = "Турнир",
) -> BytesIO:
    """Список команд (без матчей) — красивая карточка."""
    font = _get_font(FONT_SIZE)
    font_bold = _get_font(FONT_SIZE, bold=True)
    title_font = _get_font(TITLE_SIZE, bold=True)
    seed_font = _get_font(SEED_FONT_SIZE)
    num_font = _get_font(SEED_FONT_SIZE + 1, bold=True)

    if not teams:
        img_w, img_h = 460, 140
        img = Image.new("RGB", (img_w, img_h), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # Градиент
        for y in range(img_h):
            t = y / max(img_h, 1)
            r = int(0x2B + (0x1E - 0x2B) * t)
            g = int(0x2D + (0x1F - 0x2D) * t)
            b = int(0x31 + (0x22 - 0x31) * t)
            draw.line([(0, y), (img_w, y)], fill=(r, g, b))

        draw.text((MARGIN_LEFT + 1, 17), tournament_name, fill="#000000", font=title_font)
        draw.text((MARGIN_LEFT, 16), tournament_name, fill=TITLE_COLOR, font=title_font)
        draw.text((MARGIN_LEFT, 56), "Пока нет команд", fill=TBD_COLOR, font=font)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    cols = min(4, max(1, (len(teams) + 7) // 8))
    rows_per_col = math.ceil(len(teams) / cols)

    card_w = 260
    card_h = 40
    col_gap = 20
    row_gap = 8

    img_w = MARGIN_LEFT * 2 + cols * card_w + (cols - 1) * col_gap
    img_h = MARGIN_TOP + rows_per_col * (card_h + row_gap) + 20

    img = Image.new("RGB", (img_w, max(img_h, 200)), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Градиент
    for y in range(img_h):
        t = y / max(img_h, 1)
        r = int(0x2B + (0x1E - 0x2B) * t)
        g = int(0x2D + (0x1F - 0x2D) * t)
        b = int(0x31 + (0x22 - 0x31) * t)
        draw.line([(0, y), (img_w, y)], fill=(r, g, b))

    # Заголовок
    draw.text((MARGIN_LEFT + 1, 17), tournament_name, fill="#000000", font=title_font)
    draw.text((MARGIN_LEFT, 16), tournament_name, fill=TITLE_COLOR, font=title_font)

    for i, t in enumerate(teams):
        col = i // rows_per_col
        row = i % rows_per_col
        x = MARGIN_LEFT + col * (card_w + col_gap)
        y = MARGIN_TOP + row * (card_h + row_gap)

        approved = t.get("approved")
        border = WINNER_COLOR if approved else PENDING_BORDER
        bg = WINNER_BG if approved else PENDING_BG

        # Номер
        num_str = f"#{i + 1}"
        num_w = num_font.getlength(num_str) + 12

        # Тень
        draw.rounded_rectangle(
            [x + SHADOW_OFFSET, y + SHADOW_OFFSET,
             x + card_w + SHADOW_OFFSET, y + card_h + SHADOW_OFFSET],
            radius=6, fill="#111214",
        )

        # Карточка
        draw.rounded_rectangle(
            [x, y, x + card_w, y + card_h],
            radius=6, fill=bg, outline=border, width=2,
        )

        # Номер (маленький бейдж)
        draw.rounded_rectangle(
            [x + 4, y + 4, x + 4 + num_w, y + 4 + 18],
            radius=3, fill="#1E1F22",
        )
        draw.text((x + 10, y + 6), num_str, fill=SEED_COLOR, font=num_font)

        # Статус
        status_icon = "+" if approved else "o"
        status_color = WINNER_COLOR if approved else TBD_COLOR

        # Имя команды
        name_x = x + num_w + 10
        max_name_w = card_w - num_w - 40
        display = _truncate(t["name"], font_bold, max_name_w)
        draw.text((name_x, y + 10), display, fill=TEXT_COLOR, font=font_bold)

        # Иконка статуса справа
        draw.text((x + card_w - 18, y + 10), status_icon, fill=status_color, font=font_bold)

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
