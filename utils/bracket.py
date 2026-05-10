"""
Генератор картинки турнирной сетки (Single Elimination).

Стиль: классическая турнирная сетка слева направо.
- Белый фон, тонкие линии соединений
- Матчи — прямоугольники с двумя слотами команд
- L-образные соединительные линии между раундами
- Автоматическое масштабирование под количество участников
- Поддержка кириллицы (DejaVuSans)
"""

from __future__ import annotations

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Цвета
# ---------------------------------------------------------------------------

BG_COLOR = (255, 255, 255)

# Бокс матча
BOX_BG = (255, 255, 255)
BOX_BG_WINNER = (240, 255, 240)       # светло-зелёный для победителя
BOX_BG_LOSER = (250, 250, 250)        # чуть темнее для проигравшего
BOX_BG_BYE = (245, 245, 245)          # для Bye/TBD слотов
BOX_BORDER = (190, 195, 200)          # серая рамка
BOX_BORDER_PLAYING = (255, 193, 7)    # жёлтая — матч идёт
BOX_BORDER_COMPLETED = (76, 175, 80)  # зелёная — матч завершён
DIVIDER_COLOR = (210, 215, 220)       # разделитель внутри матча

# Текст
TEXT_COLOR = (40, 40, 50)
TBD_COLOR = (170, 175, 180)
WINNER_COLOR = (33, 120, 50)
LOSER_COLOR = (140, 145, 150)
SEED_COLOR = (100, 130, 200)
SCORE_COLOR = (120, 125, 130)

# Линии соединений
LINE_COLOR = (170, 175, 185)

# Заголовки
TITLE_COLOR = (50, 55, 65)
ROUND_LABEL_COLOR = (130, 135, 145)

# ---------------------------------------------------------------------------
# Размеры (базовые — масштабируются автоматически)
# ---------------------------------------------------------------------------

BASE_BOX_W = 200
BASE_TEAM_H = 26          # высота одного слота команды
BASE_BOX_GAP = 0          # между team1 и team2 внутри матча (разделитель)
BASE_MATCH_GAP_V = 18     # вертикальный зазор между матчами в раунде 1
BASE_ROUND_GAP = 64       # горизонтальный зазор между раундами
BASE_MARGIN_X = 40
BASE_MARGIN_Y = 50
BASE_FONT = 12
BASE_TITLE_FONT = 18
BASE_ROUND_FONT = 11


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


def _round_label(max_round: int, current_round: int) -> str:
    diff = max_round - current_round
    if diff == 0:
        return "Финал"
    if diff == 1:
        return "Полуфинал"
    if diff == 2:
        return "Четвертьфинал"
    return f"Раунд {current_round}"


# ---------------------------------------------------------------------------
# Главная функция — генерация сетки с матчами
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

    # --- Автомасштабирование ---
    approved_count = len(teams)
    s = _calc_scale(approved_count)

    BOX_W = int(BASE_BOX_W * s)
    TEAM_H = int(BASE_TEAM_H * s)
    MATCH_GAP_V = int(BASE_MATCH_GAP_V * s)
    ROUND_GAP = int(BASE_ROUND_GAP * s)
    MARGIN_X = int(BASE_MARGIN_X * s)
    MARGIN_Y = int(BASE_MARGIN_Y * s)
    FONT_SZ = max(8, int(BASE_FONT * s))
    TITLE_SZ = max(11, int(BASE_TITLE_FONT * s))
    ROUND_SZ = max(7, int(BASE_ROUND_FONT * s))

    match_h = TEAM_H * 2  # высота одного матча (2 слота)

    # Группируем матчи по раундам
    rounds: dict[int, list[dict]] = {}
    for m in matches:
        rounds.setdefault(m["round"], []).append(m)
    for r in rounds:
        rounds[r].sort(key=lambda m: m["match_index"])

    # --- Вычисляем позиции матчей ---
    # Позиция каждого матча: (center_y, left_x)
    # center_y — вертикальный центр матча
    # left_x — левый край бокса

    match_cy: dict[int, float] = {}  # match_id -> center_y
    match_lx: dict[int, int] = {}    # match_id -> left_x

    # Раунд 1: равномерно с зазорами
    r1_matches = rounds.get(1, [])
    for idx, m in enumerate(r1_matches):
        cy = MARGIN_Y + idx * (match_h + MATCH_GAP_V) + match_h / 2
        match_cy[m["id"]] = cy
        match_lx[m["id"]] = MARGIN_X

    # Раунды 2+: центр между двумя фидерами
    for rnd in range(2, max_round + 1):
        lx = MARGIN_X + (rnd - 1) * (BOX_W + ROUND_GAP)
        prev = rounds.get(rnd - 1, [])
        cur = rounds.get(rnd, [])
        for idx, m in enumerate(cur):
            # Фидеры — матчи 2*idx и 2*idx+1 из предыдущего раунда
            i1, i2 = idx * 2, idx * 2 + 1
            if i1 < len(prev) and i2 < len(prev):
                cy = (match_cy[prev[i1]["id"]] + match_cy[prev[i2]["id"]]) / 2
            elif i1 < len(prev):
                cy = match_cy[prev[i1]["id"]]
            else:
                cy = MARGIN_Y + idx * (match_h + MATCH_GAP_V) * 2 + match_h / 2
            match_cy[m["id"]] = cy
            match_lx[m["id"]] = lx

    # --- Размеры изображения ---
    rightmost_x = MARGIN_X + max_round * (BOX_W + ROUND_GAP) - ROUND_GAP + BOX_W
    all_cy = list(match_cy.values())
    min_cy = min(all_cy) if all_cy else MARGIN_Y
    max_cy = max(all_cy) if all_cy else MARGIN_Y
    img_w = rightmost_x + MARGIN_X
    img_h = int(max_cy + match_h / 2 + MARGIN_Y)
    img_h = max(img_h, 160)

    # Место для заголовка
    title_h = int(TITLE_SZ * 2.2)
    img_h += title_h
    # Сдвигаем все Y вниз на title_h
    for mid in match_cy:
        match_cy[mid] += title_h

    img = Image.new("RGB", (img_w, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = _font(FONT_SZ)
    font_bold = _font(FONT_SZ, bold=True)
    title_font = _font(TITLE_SZ, bold=True)
    round_font = _font(ROUND_SZ, bold=True)

    # --- Заголовок ---
    draw.text((MARGIN_X, int(title_h * 0.3)), tournament_name, fill=TITLE_COLOR, font=title_font)

    # --- Метки раундов ---
    for rnd in range(1, max_round + 1):
        first = rounds.get(rnd, [])[0] if rounds.get(rnd) else None
        if not first:
            continue
        lx = match_lx[first["id"]]
        label = _round_label(max_round, rnd)
        draw.text((lx, int(title_h * 0.3) + TITLE_SZ + 4), label, fill=ROUND_LABEL_COLOR, font=round_font)

    # --- Соединительные линии ---
    for rnd in range(1, max_round):
        cur = rounds.get(rnd, [])
        nxt = rounds.get(rnd + 1, [])
        for m in cur:
            ni = m["match_index"] // 2
            if ni >= len(nxt):
                continue

            # Выход: правый центр текущего матча
            out_x = match_lx[m["id"]] + BOX_W
            out_y = int(match_cy[m["id"]])

            # Вход: левая сторона следующего матча, нужный слот
            nm = nxt[ni]
            in_x = match_lx[nm["id"]]
            # Если match_index чётный → team1 (верхний слот), иначе team2 (нижний слот)
            if m["match_index"] % 2 == 0:
                in_y = int(match_cy[nm["id"]]) - TEAM_H // 2
            else:
                in_y = int(match_cy[nm["id"]]) + TEAM_H // 2

            # Рисуем Z-образную линию:
            # горизонталь вправо → вертикаль к нужному Y → горизонталь до входа
            mid_x = out_x + ROUND_GAP // 2

            line_w = max(1, int(1.5 * s))
            draw.line([(out_x, out_y), (mid_x, out_y)], fill=LINE_COLOR, width=line_w)
            draw.line([(mid_x, out_y), (mid_x, in_y)], fill=LINE_COLOR, width=line_w)
            draw.line([(mid_x, in_y), (in_x, in_y)], fill=LINE_COLOR, width=line_w)

    # --- Боксы матчей ---
    for rnd in range(1, max_round + 1):
        for m in rounds.get(rnd, []):
            cx = match_lx[m["id"]]
            cy = match_cy[m["id"]]
            ty = int(cy - match_h / 2)  # верхний край матча

            # Стиль рамки по статусу
            border = BOX_BORDER
            if m.get("status") == "playing":
                border = BOX_BORDER_PLAYING
            elif m.get("status") == "completed":
                border = BOX_BORDER_COMPLETED

            winner_id = m.get("winner_id", 0) or 0

            # Рисуем весь бокс матча (контур)
            draw.rectangle(
                [cx, ty, cx + BOX_W, ty + match_h],
                fill=BOX_BG, outline=border, width=max(1, int(1.5 * s)),
            )

            # Разделитель между слотами
            draw.line(
                [(cx, ty + TEAM_H), (cx + BOX_W, ty + TEAM_H)],
                fill=DIVIDER_COLOR, width=1,
            )

            # Team 1 (верхний слот)
            _draw_team_slot(
                draw, cx, ty, BOX_W, TEAM_H,
                m.get("team1_id", 0), team_map,
                winner_id, border, m.get("score"),
                font, font_bold, s,
            )

            # Team 2 (нижний слот)
            _draw_team_slot(
                draw, cx, ty + TEAM_H, BOX_W, TEAM_H,
                m.get("team2_id", 0), team_map,
                winner_id, border, None,
                font, font_bold, s,
            )

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Отрисовка одного слота команды внутри матча
# ---------------------------------------------------------------------------

def _draw_team_slot(
    draw: ImageDraw.Draw,
    x: int, y: int, w: int, h: int,
    team_id: int,
    team_map: dict,
    winner_id: int,
    match_border: tuple,
    score: str | None,
    font, font_bold, s: float,
) -> None:
    is_winner = bool(winner_id and winner_id == team_id)
    is_bye = not team_id
    has_winner = bool(winner_id)

    # Фон слота
    if is_bye:
        draw.rectangle([x + 1, y + 1, x + w - 1, y + h - 1], fill=BOX_BG_BYE)
    elif is_winner:
        draw.rectangle([x + 1, y + 1, x + w - 1, y + h - 1], fill=BOX_BG_WINNER)
    elif has_winner:
        draw.rectangle([x + 1, y + 1, x + w - 1, y + h - 1], fill=BOX_BG_LOSER)

    # Определяем текст и стиль
    if is_bye:
        name = "TBD"
        txt_color = TBD_COLOR
        uf = font
    elif is_winner:
        name = team_map[team_id].get("name", "???") if team_id in team_map else "???"
        txt_color = WINNER_COLOR
        uf = font_bold
    else:
        name = team_map[team_id].get("name", "???") if team_id in team_map else "???"
        txt_color = TEXT_COLOR if not has_winner else LOSER_COLOR
        uf = font

    # Сид (seed)
    seed_str = ""
    if team_id and team_id in team_map:
        seed = team_map[team_id].get("seed", 0)
        if seed and seed > 0:
            seed_str = str(seed)

    # Отступы
    pad_x = int(6 * s)
    # Вычисляем pad_y исходя из высоты текста
    try:
        bbox = font.getbbox("Ay")
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_h = int(12 * s)
    pad_y = max(1, (h - text_h) // 2)

    # Рисуем сид
    if seed_str:
        sw = int(font.getlength(seed_str))
        seed_x = x + pad_x
        draw.text((seed_x, y + pad_y), seed_str, fill=SEED_COLOR, font=font)
        name_x = seed_x + sw + int(4 * s)
    else:
        name_x = x + pad_x

    # Имя команды (с обрезкой)
    score_area = int(40 * s) if score else int(10 * s)
    max_name_w = w - (name_x - x) - score_area
    display = _clip(name, uf, max_name_w)
    draw.text((name_x, y + pad_y), display, fill=txt_color, font=uf)

    # Счёт
    if score and not is_bye:
        score_x = x + w - int(36 * s)
        draw.text((score_x, y + pad_y), score, fill=SCORE_COLOR, font=font)

    # Зелёная полоска слева для победителя
    if is_winner:
        bar_w = max(2, int(3 * s))
        draw.rectangle([x, y, x + bar_w, y + h], fill=WINNER_COLOR)


# ---------------------------------------------------------------------------
# Простая сетка (превью — без матчей, только команды)
# ---------------------------------------------------------------------------

def generate_bracket_simple(
    teams: list[dict],
    tournament_name: str = "Турнир",
) -> BytesIO:
    """Превью сетки — показывает посев команд как в турнирной сетке."""
    f = _font(BASE_FONT)
    fb = _font(BASE_FONT, bold=True)
    ft = _font(BASE_TITLE_FONT, bold=True)
    fn = _font(10, bold=True)

    if not teams:
        img = Image.new("RGB", (350, 100), BG_COLOR)
        d = ImageDraw.Draw(img)
        d.text((BASE_MARGIN_X, 8), tournament_name, fill=TITLE_COLOR, font=ft)
        d.text((BASE_MARGIN_X, 40), "Пока нет команд", fill=TBD_COLOR, font=f)
        buf = BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf

    approved = [t for t in teams if t.get("approved")]
    if not approved:
        img = Image.new("RGB", (350, 100), BG_COLOR)
        d = ImageDraw.Draw(img)
        d.text((BASE_MARGIN_X, 8), tournament_name, fill=TITLE_COLOR, font=ft)
        d.text((BASE_MARGIN_X, 40), "Нет одобренных команд", fill=TBD_COLOR, font=f)
        buf = BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf

    # Генерируем сетку-превью: показываем как команды распределены в 1-м раунде
    n = len(approved)
    bracket_size = 1
    while bracket_size < n:
        bracket_size *= 2

    num_rounds = int(math.log2(bracket_size))
    first_round_matches = bracket_size // 2
    byes = bracket_size - n

    s = _calc_scale(n)
    BOX_W = int(BASE_BOX_W * s)
    TEAM_H = int(BASE_TEAM_H * s)
    MATCH_GAP_V = int(BASE_MATCH_GAP_V * s)
    ROUND_GAP = int(BASE_ROUND_GAP * s)
    MARGIN_X = int(BASE_MARGIN_X * s)
    MARGIN_Y = int(BASE_MARGIN_Y * s)
    FONT_SZ = max(8, int(BASE_FONT * s))
    TITLE_SZ = max(11, int(BASE_TITLE_FONT * s))
    ROUND_SZ = max(7, int(BASE_ROUND_FONT * s))

    font = _font(FONT_SZ)
    font_bold = _font(FONT_SZ, bold=True)
    title_font = _font(TITLE_SZ, bold=True)
    round_font = _font(ROUND_SZ, bold=True)

    match_h = TEAM_H * 2

    # Seeding: 1v8, 4v5, 3v6, 2v7 (standard bracket seeding)
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

    # Вычисляем позиции матчей для всей сетки
    # (даже для пустых раундов, чтобы нарисовать линии)
    match_cy: list[list[float]] = []  # match_cy[round-1][match_index] = center_y
    match_lx_list: list[int] = []

    # Раунд 1
    r1_cy = []
    for idx in range(first_round_matches):
        cy = MARGIN_Y + idx * (match_h + MATCH_GAP_V) + match_h / 2
        r1_cy.append(cy)
    match_cy.append(r1_cy)
    match_lx_list.append(MARGIN_X)

    # Раунды 2+
    for rnd in range(2, num_rounds + 1):
        matches_in_round = bracket_size // (2 ** rnd)
        lx = MARGIN_X + (rnd - 1) * (BOX_W + ROUND_GAP)
        prev_cy = match_cy[rnd - 2]
        cur_cy = []
        for idx in range(matches_in_round):
            i1, i2 = idx * 2, idx * 2 + 1
            if i1 < len(prev_cy) and i2 < len(prev_cy):
                cy = (prev_cy[i1] + prev_cy[i2]) / 2
            elif i1 < len(prev_cy):
                cy = prev_cy[i1]
            else:
                cy = MARGIN_Y + idx * (match_h + MATCH_GAP_V) * 2 + match_h / 2
            cur_cy.append(cy)
        match_cy.append(cur_cy)
        match_lx_list.append(lx)

    # Размеры изображения
    rightmost_x = MARGIN_X + num_rounds * (BOX_W + ROUND_GAP) - ROUND_GAP + BOX_W
    all_cy = [cy for rnd_cy in match_cy for cy in rnd_cy]
    max_cy = max(all_cy) if all_cy else MARGIN_Y
    img_w = rightmost_x + MARGIN_X
    img_h = int(max_cy + match_h / 2 + MARGIN_Y)
    img_h = max(img_h, 160)

    title_h = int(TITLE_SZ * 2.2)
    img_h += title_h
    # Сдвигаем все Y вниз
    for rnd_idx in range(len(match_cy)):
        match_cy[rnd_idx] = [cy + title_h for cy in match_cy[rnd_idx]]

    img = Image.new("RGB", (img_w, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Заголовок
    draw.text((MARGIN_X, int(title_h * 0.3)), tournament_name, fill=TITLE_COLOR, font=title_font)

    # Метки раундов
    for rnd in range(1, num_rounds + 1):
        lx = match_lx_list[rnd - 1]
        label = _round_label(num_rounds, rnd)
        draw.text((lx, int(title_h * 0.3) + TITLE_SZ + 4), label, fill=ROUND_LABEL_COLOR, font=round_font)

    # Соединительные линии
    for rnd in range(1, num_rounds):
        prev_cy = match_cy[rnd - 1]
        cur_cy = match_cy[rnd]
        prev_lx = match_lx_list[rnd - 1]
        cur_lx = match_lx_list[rnd]

        for idx, cy in enumerate(prev_cy):
            ni = idx // 2
            if ni >= len(cur_cy):
                continue

            out_x = prev_lx + BOX_W
            out_y = int(cy)

            ncy = cur_cy[ni]
            if idx % 2 == 0:
                in_y = int(ncy) - TEAM_H // 2
            else:
                in_y = int(ncy) + TEAM_H // 2
            in_x = cur_lx

            mid_x = out_x + ROUND_GAP // 2
            line_w = max(1, int(1.5 * s))
            draw.line([(out_x, out_y), (mid_x, out_y)], fill=LINE_COLOR, width=line_w)
            draw.line([(mid_x, out_y), (mid_x, in_y)], fill=LINE_COLOR, width=line_w)
            draw.line([(mid_x, in_y), (in_x, in_y)], fill=LINE_COLOR, width=line_w)

    # Боксы матчей первого раунда (с командами)
    for idx, (t1, t2) in enumerate(r1_matchups):
        cx = match_lx_list[0]
        cy = match_cy[0][idx]
        ty = int(cy - match_h / 2)

        # Рамка
        draw.rectangle(
            [cx, ty, cx + BOX_W, ty + match_h],
            fill=BOX_BG, outline=BOX_BORDER, width=max(1, int(1.5 * s)),
        )
        # Разделитель
        draw.line(
            [(cx, ty + TEAM_H), (cx + BOX_W, ty + TEAM_H)],
            fill=DIVIDER_COLOR, width=1,
        )

        # Team 1
        if t1:
            _draw_team_slot(
                draw, cx, ty, BOX_W, TEAM_H,
                t1["id"], {t["id"]: t for t in approved},
                0, BOX_BORDER, None,
                font, font_bold, s,
            )
        else:
            _draw_team_slot(
                draw, cx, ty, BOX_W, TEAM_H,
                0, {}, 0, BOX_BORDER, None,
                font, font_bold, s,
            )

        # Team 2
        if t2:
            _draw_team_slot(
                draw, cx, ty + TEAM_H, BOX_W, TEAM_H,
                t2["id"], {t["id"]: t for t in approved},
                0, BOX_BORDER, None,
                font, font_bold, s,
            )
        else:
            # Bye или TBD
            _draw_team_slot(
                draw, cx, ty + TEAM_H, BOX_W, TEAM_H,
                0, {}, 0, BOX_BORDER, None,
                font, font_bold, s,
            )

    # Пустые боксы для раундов 2+
    for rnd in range(2, num_rounds + 1):
        lx = match_lx_list[rnd - 1]
        for cy in match_cy[rnd - 1]:
            ty = int(cy - match_h / 2)
            draw.rectangle(
                [lx, ty, lx + BOX_W, ty + match_h],
                fill=BOX_BG, outline=BOX_BORDER, width=max(1, int(1.5 * s)),
            )
            draw.line(
                [(lx, ty + TEAM_H), (lx + BOX_W, ty + TEAM_H)],
                fill=DIVIDER_COLOR, width=1,
            )
            # TBD слоты
            _draw_team_slot(
                draw, lx, ty, BOX_W, TEAM_H,
                0, {}, 0, BOX_BORDER, None,
                font, font_bold, s,
            )
            _draw_team_slot(
                draw, lx, ty + TEAM_H, BOX_W, TEAM_H,
                0, {}, 0, BOX_BORDER, None,
                font, font_bold, s,
            )

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _calc_scale(num_teams: int) -> float:
    """Вычисляет масштаб в зависимости от количества команд."""
    if num_teams <= 4:
        return 1.0
    elif num_teams <= 8:
        return 0.92
    elif num_teams <= 16:
        return 0.82
    elif num_teams <= 32:
        return 0.7
    else:
        return 0.6


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

    # Генерируем стандартный порядок посева
    order = _seed_positions(bracket_size)
    # Обрезаем до количества команд и мапим
    result = [None] * n
    pos = 0
    for seed_pos in order:
        if pos < n and seed_pos < n:
            result[seed_pos] = teams[pos]
            pos += 1

    # Заполняем None (если алгоритм не покрыл все позиции)
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

    # Рекурсивно строим посев
    half = size // 2
    sub = _seed_positions(half)
    result = []
    for s in sub:
        result.append(s)
        result.append(size - 1 - s)
    return result
