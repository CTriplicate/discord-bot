"""
Генератор картинки турнирной сетки (Single Elimination).

Стиль: светлая тема, как на классических турнирных сетках.
- Белый фон, тонкие серые рамки
- Команды расположены друг под другом в каждом матче
- L-образные соединительные линии
- Автоматическое масштабирование под количество участников
"""

from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Цвета (светлая тема — классический стиль)
# ---------------------------------------------------------------------------

BG_COLOR = (255, 255, 255)
BOX_BG = (255, 255, 255)
BOX_BORDER = (200, 200, 200)
BOX_BORDER_PLAYING = (255, 200, 50)
BOX_BORDER_COMPLETED = (80, 180, 80)

TEXT_COLOR = (50, 50, 50)
TBD_COLOR = (170, 170, 170)
WINNER_COLOR = (40, 140, 40)
LOSER_COLOR = (140, 140, 140)

SCORE_COLOR = (120, 120, 120)
SEED_COLOR = (100, 120, 200)

LINE_COLOR = (180, 180, 180)
LINE_CONNECT = (140, 140, 140)

TITLE_COLOR = (60, 60, 60)
ROUND_LABEL_COLOR = (130, 130, 130)

BYE_BG = (245, 245, 245)
BYE_BORDER = (210, 210, 210)

# ---------------------------------------------------------------------------
# Размеры (базовые — масштабируются автоматически)
# ---------------------------------------------------------------------------

BASE_BOX_W = 180
BASE_BOX_H = 28
BASE_BOX_GAP = 2        # между team1 и team2 внутри матча
BASE_MATCH_GAP = 16      # между матчами в раунде 1
BASE_ROUND_GAP = 50      # между раундами по горизонтали
BASE_MARGIN = 30
BASE_FONT = 12
BASE_TITLE_FONT = 16


def _font(size: int, bold: bool = False):
    paths = []
    if bold:
        paths.append("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    paths += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _clip(text: str, font, max_w: int) -> str:
    if font.getlength(text) <= max_w:
        return text
    while len(text) > 1 and font.getlength(text + "...") > max_w:
        text = text[:-1]
    return text + "..."


def _calc_scale(num_teams: int) -> float:
    """Вычисляет масштаб в зависимости от количества команд."""
    if num_teams <= 4:
        return 1.0
    elif num_teams <= 8:
        return 0.95
    elif num_teams <= 16:
        return 0.85
    elif num_teams <= 32:
        return 0.72
    else:
        return 0.6


# ---------------------------------------------------------------------------
# Публичные функции
# ---------------------------------------------------------------------------

def generate_bracket(
    teams: list[dict],
    matches: list[dict],
    tournament_name: str = "Турнир",
) -> BytesIO:
    """Генерирует PNG турнирной сетки Single Elimination."""
    if not matches:
        return generate_bracket_simple(teams, tournament_name)

    team_map = {t["id"]: t for t in teams}
    max_round = max(m["round"] for m in matches)

    # Автомасштабирование
    approved_count = len(teams)
    s = _calc_scale(approved_count)

    BOX_W = int(BASE_BOX_W * s)
    BOX_H = int(BASE_BOX_H * s)
    BOX_GAP = max(1, int(BASE_BOX_GAP * s))
    MATCH_GAP = int(BASE_MATCH_GAP * s)
    ROUND_GAP = int(BASE_ROUND_GAP * s)
    MARGIN = int(BASE_MARGIN * s)
    FONT_SZ = max(9, int(BASE_FONT * s))
    TITLE_SZ = max(12, int(BASE_TITLE_FONT * s))

    match_h = BOX_H * 2 + BOX_GAP  # высота одного матча

    # Группируем матчи по раундам
    rounds: dict[int, list[dict]] = {}
    for m in matches:
        rounds.setdefault(m["round"], []).append(m)
    for r in rounds:
        rounds[r].sort(key=lambda m: m["match_index"])

    # --- Позиции матчей: top_y, left_x ---
    match_pos: dict[int, tuple[int, int]] = {}
    step_r1 = match_h + MATCH_GAP

    for idx, m in enumerate(rounds.get(1, [])):
        top_y = MARGIN + idx * step_r1
        match_pos[m["id"]] = (top_y, MARGIN)

    for rnd in range(2, max_round + 1):
        prev = rounds.get(rnd - 1, [])
        for idx, m in enumerate(rounds.get(rnd, [])):
            i1, i2 = idx * 2, idx * 2 + 1
            if i1 < len(prev) and i2 < len(prev):
                cy1 = match_pos[prev[i1]["id"]][0] + match_h // 2
                cy2 = match_pos[prev[i2]["id"]][0] + match_h // 2
                top_y = (cy1 + cy2) // 2 - match_h // 2
            elif i1 < len(prev):
                top_y = match_pos[prev[i1]["id"]][0]
            else:
                top_y = MARGIN + idx * step_r1 * 2
            left_x = MARGIN + (rnd - 1) * (BOX_W + ROUND_GAP)
            match_pos[m["id"]] = (top_y, left_x)

    # --- Размеры картинки ---
    img_w = MARGIN + max_round * (BOX_W + ROUND_GAP) + MARGIN
    all_y = [ty + match_h for ty, _ in match_pos.values()]
    img_h = max(all_y) + MARGIN
    img_h = max(img_h, 150)

    img = Image.new("RGB", (img_w, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = _font(FONT_SZ)
    font_bold = _font(FONT_SZ, bold=True)
    title_font = _font(TITLE_SZ, bold=True)
    round_font = _font(max(8, FONT_SZ - 2))

    # --- Заголовок ---
    draw.text((MARGIN, 8), tournament_name, fill=TITLE_COLOR, font=title_font)

    # --- Метки раундов (над первым матчем каждого раунда) ---
    for rnd in range(1, max_round + 1):
        first = rounds.get(rnd, [])[0] if rounds.get(rnd) else None
        if not first:
            continue
        ty, lx = match_pos[first["id"]]
        label = _round_label(max_round, rnd)
        draw.text((lx, ty - int(14 * s)), label, fill=ROUND_LABEL_COLOR, font=round_font)

    # --- Соединительные линии ---
    for rnd in range(1, max_round):
        for m in rounds.get(rnd, []):
            ni = m["match_index"] // 2
            nxt = rounds.get(rnd + 1, [])
            if ni >= len(nxt):
                continue

            ty, lx = match_pos[m["id"]]
            nty, nlx = match_pos[nxt[ni]["id"]]

            # Выход: правый центр матча-источника
            out_x = lx + BOX_W
            out_y = ty + match_h // 2

            # Вход: левый центр нужного слота
            if m["match_index"] % 2 == 0:
                in_y = nty + BOX_H // 2
            else:
                in_y = nty + BOX_H + BOX_GAP + BOX_H // 2
            in_x = nlx

            # L-образная линия: горизонталь → вертикаль → горизонталь
            mid_x = (out_x + in_x) // 2

            draw.line([(out_x, out_y), (mid_x, out_y)], fill=LINE_CONNECT, width=2)
            draw.line([(mid_x, out_y), (mid_x, in_y)], fill=LINE_CONNECT, width=2)
            draw.line([(mid_x, in_y), (in_x, in_y)], fill=LINE_CONNECT, width=2)

    # --- Боксы матчей ---
    for rnd in range(1, max_round + 1):
        for m in rounds.get(rnd, []):
            ty, lx = match_pos[m["id"]]

            # Определяем стиль рамки по статусу матча
            border = BOX_BORDER
            if m["status"] == "playing":
                border = BOX_BORDER_PLAYING
            elif m["status"] == "completed":
                border = BOX_BORDER_COMPLETED

            # Team 1 (верхний бокс)
            _draw_team(
                draw, lx, ty, BOX_W, BOX_H,
                m.get("team1_id", 0), team_map,
                m.get("winner_id", 0), border,
                m.get("score"), font, font_bold, s,
            )

            # Team 2 (нижний бокс)
            _draw_team(
                draw, lx, ty + BOX_H + BOX_GAP, BOX_W, BOX_H,
                m.get("team2_id", 0), team_map,
                m.get("winner_id", 0), border,
                None, font, font_bold, s,
            )

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _draw_team(
    draw: ImageDraw.Draw,
    x: int, y: int, w: int, h: int,
    team_id: int,
    team_map: dict,
    winner_id: int,
    match_border: tuple,
    score: str | None,
    font, font_bold, s: float,
) -> None:
    """Рисует один бокс команды."""
    is_winner = bool(winner_id and winner_id == team_id)
    is_bye = not team_id

    # Фон и рамка
    if is_bye:
        bg, border, txt_color = BYE_BG, BYE_BORDER, TBD_COLOR
        name, uf = "TBD", font
    elif is_winner:
        bg, border, txt_color = BOX_BG, match_border, WINNER_COLOR
        name = team_map[team_id].get("name", "???") if team_id in team_map else "???"
        uf = font_bold
    else:
        bg, border, txt_color = BOX_BG, match_border, TEXT_COLOR
        name = team_map[team_id].get("name", "???") if team_id in team_map else "???"
        uf = font

    # Рамка бокса
    draw.rectangle([x, y, x + w, y + h], fill=bg, outline=border, width=1)

    # Разделительная полоса сверху (1px)
    if y > 0:
        draw.line([(x, y), (x + w, y)], fill=border, width=1)

    # Текст
    text_x = x + int(6 * s)
    max_text_w = w - int(50 * s)
    display = _clip(name, uf, max_text_w)
    draw.text((text_x, y + int(7 * s)), display, fill=txt_color, font=uf)

    # Сид
    if team_id and team_id in team_map:
        seed = team_map[team_id].get("seed", 0)
        if seed and seed > 0:
            seed_str = str(seed)
            draw.text((x + w - int(36 * s), y + int(7 * s)), seed_str, fill=SEED_COLOR, font=font)

    # Счёт
    if score and not is_bye:
        draw.text((x + w - int(50 * s), y + int(7 * s)), score, fill=SCORE_COLOR, font=font)


def generate_bracket_simple(
    teams: list[dict],
    tournament_name: str = "Турнир",
) -> BytesIO:
    """Список команд (без матчей)."""
    f = _font(BASE_FONT)
    fb = _font(BASE_FONT, bold=True)
    ft = _font(BASE_TITLE_FONT, bold=True)
    fn = _font(10, bold=True)

    if not teams:
        img = Image.new("RGB", (350, 100), BG_COLOR)
        d = ImageDraw.Draw(img)
        d.text((BASE_MARGIN, 8), tournament_name, fill=TITLE_COLOR, font=ft)
        d.text((BASE_MARGIN, 40), "Пока нет команд", fill=TBD_COLOR, font=f)
        buf = BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf

    cols = min(4, max(1, (len(teams) + 7) // 8))
    rows = math.ceil(len(teams) / cols)
    cw, ch, cg, rg = 200, 30, 12, 4
    iw = BASE_MARGIN * 2 + cols * cw + (cols - 1) * cg
    ih = BASE_MARGIN + 10 + rows * (ch + rg) + 8

    img = Image.new("RGB", (iw, max(ih, 120)), BG_COLOR)
    d = ImageDraw.Draw(img)
    d.text((BASE_MARGIN, 8), tournament_name, fill=TITLE_COLOR, font=ft)

    for i, t in enumerate(teams):
        col, row = i // rows, i % rows
        x = BASE_MARGIN + col * (cw + cg)
        y = BASE_MARGIN + 10 + row * (ch + rg)
        approved = t.get("approved")
        border = BOX_BORDER_COMPLETED if approved else BOX_BORDER
        d.rectangle([x, y, x + cw, y + ch], fill=BOX_BG, outline=border, width=1)
        ns = f"#{i + 1}"
        d.text((x + 4, y + 7), ns, fill=SEED_COLOR, font=fn)
        d.text((x + 30, y + 7), _clip(t["name"], fb, cw - 50), fill=TEXT_COLOR, font=fb)
        icon = "+" if approved else "o"
        d.text((x + cw - 14, y + 7), icon, fill=WINNER_COLOR if approved else TBD_COLOR, font=fb)

    buf = BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _round_label(max_round: int, current_round: int) -> str:
    diff = max_round - current_round
    if diff == 0: return "Финал"
    if diff == 1: return "Полуфинал"
    if diff == 2: return "Четвертьфинал"
    return f"Раунд {current_round}"
