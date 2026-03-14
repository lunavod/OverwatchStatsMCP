"""Overwatch-style scoreboard image renderer.

Generates scoreboard and hero stats images from match data dicts.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = PROJECT_ROOT / "assets" / "fonts"

# Overwatch color palette
COLORS = {
    "bg": (30, 32, 44),
    "bg_header": (22, 24, 34),
    "ally_bg": (36, 52, 74),
    "ally_bg_alt": (32, 46, 66),
    "enemy_bg": (66, 34, 36),
    "enemy_bg_alt": (58, 30, 32),
    "self_highlight": (44, 75, 110),
    "ally_accent": (70, 150, 255),
    "ally_label_bg": (35, 55, 85),
    "enemy_accent": (255, 75, 75),
    "enemy_label_bg": (85, 35, 35),
    "victory": (255, 190, 50),
    "defeat": (200, 70, 70),
    "unknown": (160, 160, 170),
    "white": (255, 255, 255),
    "text": (220, 222, 228),
    "text_dim": (170, 175, 192),
    "text_stat": (215, 220, 232),
    "separator": (50, 55, 68),
    "role_tank": (240, 185, 55),
    "role_dps": (210, 65, 65),
    "role_support": (75, 195, 115),
    "gold": (255, 210, 80),
    "death_worst": (255, 90, 90),
}

STAT_COLS = ["eliminations", "assists", "deaths", "damage", "healing", "mitigation"]
STAT_LABELS = ["ELIM", "ASST", "DEATH", "DMG", "HEAL", "MIT"]

ROLE_ORDER = {"TANK": 0, "DPS": 1, "SUPPORT": 2}
ROLE_SYMBOL = {"TANK": "\u25C6", "DPS": "\u2605", "SUPPORT": "\u271A"}

# Layout (2x resolution)
DEFAULT_WIDTH = 1160
PADDING = 28
ROW_HEIGHT = 88
HEADER_HEIGHT = 152
TEAM_LABEL_HEIGHT = 64
STAT_HEADER_HEIGHT = 52
DEFAULT_NAME_COL_W = 380
ROLE_ICON_SIZE = 32
NAME_LEFT_OFFSET = PADDING + ROLE_ICON_SIZE + 16  # where text starts
NAME_RIGHT_PAD = 16  # breathing room before stat columns
STAT_AREA_W = DEFAULT_WIDTH - DEFAULT_NAME_COL_W - PADDING  # fixed stat column width
HERO_STAT_ROW_HEIGHT = 100
HERO_STAT_HEADER_HEIGHT = 72
HERO_STAT_COLS = 2


# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

_SYSTEM_FONTS = {
    "title": ["C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf"],
    "body": ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arialbd.ttf"],
    "body_regular": ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"],
    "symbol": ["C:/Windows/Fonts/seguisym.ttf", "C:/Windows/Fonts/segoeui.ttf"],
}

_BUNDLED_FONTS = {
    "title": "big_noodle_titling_oblique",
    "title_regular": "big_noodle_titling",
    "body": "segoeuib",
    "symbol": "seguisym",
}


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    bundled_name = _BUNDLED_FONTS.get(name)
    if bundled_name:
        for ext in ("ttf", "otf"):
            path = FONT_DIR / f"{bundled_name}.{ext}"
            if path.exists():
                return ImageFont.truetype(str(path), size)

    for ext in ("ttf", "otf"):
        path = FONT_DIR / f"{name}.{ext}"
        if path.exists():
            return ImageFont.truetype(str(path), size)

    for path_str in _SYSTEM_FONTS.get(name, _SYSTEM_FONTS["body"]):
        if Path(path_str).exists():
            return ImageFont.truetype(path_str, size)

    return ImageFont.load_default()


FONT_TITLE = _load_font("title", 72)
FONT_TEAM_LABEL = _load_font("title", 40)
FONT_STAT_HEADER = _load_font("body", 24)
FONT_PLAYER_NAME = _load_font("body", 28)
FONT_HERO = _load_font("body", 26)
FONT_STAT = _load_font("body", 30)
FONT_META = _load_font("body", 28)
FONT_ROLE = _load_font("symbol", 30)
FONT_HERO_STAT_LABEL = _load_font("body", 26)
FONT_HERO_STAT_VALUE = _load_font("body", 32)
FONT_HERO_SECTION_TITLE = _load_font("title", 36)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _format_stat(value: int | None) -> str:
    if value is None:
        return "-"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def _role_color(role: str) -> tuple:
    return COLORS.get(f"role_{role.lower()}", COLORS["text_dim"])


def _result_color(result: str) -> tuple:
    return COLORS.get(result.lower(), COLORS["unknown"])


def _find_stat_leaders(players: list[dict]) -> dict[str, list[str]]:
    """Best value per stat. For deaths, lowest (non-None) is best."""
    leaders = {}
    for stat in STAT_COLS:
        best_val = None
        best_ids = []
        for p in players:
            v = p.get(stat)
            if v is None:
                continue
            if stat == "deaths":
                if best_val is None or v < best_val:
                    best_val = v
                    best_ids = [p["id"]]
                elif v == best_val:
                    best_ids.append(p["id"])
            else:
                if best_val is None or v > best_val:
                    best_val = v
                    best_ids = [p["id"]]
                elif v == best_val:
                    best_ids.append(p["id"])
        if best_ids:
            leaders[stat] = best_ids
    return leaders


def _find_stat_worst(players: list[dict]) -> dict[str, list[str]]:
    """Worst deaths (highest). Used to highlight in red."""
    worst = {}
    best_val = None
    best_ids = []
    for p in players:
        v = p.get("deaths")
        if v is None:
            continue
        if best_val is None or v > best_val:
            best_val = v
            best_ids = [p["id"]]
        elif v == best_val:
            best_ids.append(p["id"])
    if best_ids:
        worst["deaths"] = best_ids
    return worst


def _measure_name_col(players: list[dict]) -> int:
    """Measure the minimum name column width needed to fit all player texts."""
    # Use a dummy image for text measurement
    dummy = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(dummy)

    max_text_w: float = 0
    for p in players:
        name = p.get("player_name", "???")
        hero = p.get("hero") or ""
        title = p.get("title") or ""

        hero_parts = []
        if hero:
            hero_parts.append(hero)
        if title:
            hero_parts.append(f"({title})")
        hero_line = " ".join(hero_parts)

        name_bbox = draw.textbbox((0, 0), name, font=FONT_PLAYER_NAME)
        name_w = name_bbox[2] - name_bbox[0]
        max_text_w = max(max_text_w, name_w)

        if hero_line:
            hero_bbox = draw.textbbox((0, 0), hero_line, font=FONT_HERO)
            hero_w = hero_bbox[2] - hero_bbox[0]
            max_text_w = max(max_text_w, hero_w)

    required = int(NAME_LEFT_OFFSET + max_text_w + NAME_RIGHT_PAD)
    return max(DEFAULT_NAME_COL_W, required)


# ---------------------------------------------------------------------------
# Main rendering
# ---------------------------------------------------------------------------

def render_scoreboard(match_data: dict, output_path: str = "scoreboard.png") -> list[Path]:
    """Render scoreboard and hero stats as separate images.

    Returns a list of saved file paths (1 or 2 images).
    """
    players = match_data["player_stats"]
    allies = sorted(
        [p for p in players if p["team"] == "ALLY"],
        key=lambda p: ROLE_ORDER.get(p["role"], 9),
    )
    enemies = sorted(
        [p for p in players if p["team"] == "ENEMY"],
        key=lambda p: ROLE_ORDER.get(p["role"], 9),
    )

    stat_leaders = _find_stat_leaders(players)
    stat_worst = _find_stat_worst(players)

    self_player = next((p for p in players if p.get("is_self")), None)
    hero_stats = None
    if self_player:
        # Find the primary hero's stats (first hero with non-empty values)
        for h in self_player.get("heroes", []):
            if h.get("values"):
                hero_stats = h
                break

    # Dynamic width: measure text, expand name column if needed
    name_col_w = _measure_name_col(players)
    width = name_col_w + STAT_AREA_W + PADDING

    # --- Scoreboard image ---
    n_allies = len(allies)
    n_enemies = len(enemies)
    height = (
        HEADER_HEIGHT
        + TEAM_LABEL_HEIGHT + STAT_HEADER_HEIGHT + n_allies * ROW_HEIGHT
        + 20
        + TEAM_LABEL_HEIGHT + STAT_HEADER_HEIGHT + n_enemies * ROW_HEIGHT
        + PADDING
    )

    img = Image.new("RGB", (width, height), COLORS["bg"])
    draw = ImageDraw.Draw(img)

    # Header
    draw.rectangle([0, 0, width, HEADER_HEIGHT], fill=COLORS["bg_header"])
    result = match_data.get("result", "UNKNOWN")
    result_color = _result_color(result)
    draw.rectangle([0, HEADER_HEIGHT - 6, width, HEADER_HEIGHT], fill=result_color)
    draw.text(
        (width // 2, 20), result, fill=result_color, font=FONT_TITLE, anchor="mt"
    )

    map_name = match_data.get("map_name", "")
    mode = match_data.get("mode", "")
    duration = match_data.get("duration", "")
    meta_parts = [p for p in [map_name, mode, duration] if p]
    meta_text = "  \u00B7  ".join(meta_parts)
    draw.text(
        (width // 2, 96), meta_text, fill=COLORS["text_dim"], font=FONT_META, anchor="mt"
    )

    # Teams
    y = HEADER_HEIGHT
    y = _draw_team_block(
        draw, y, "YOUR TEAM", allies,
        accent=COLORS["ally_accent"],
        label_bg=COLORS["ally_label_bg"],
        row_bg=COLORS["ally_bg"],
        row_bg_alt=COLORS["ally_bg_alt"],
        self_bg=COLORS["self_highlight"],
        stat_leaders=stat_leaders,
        stat_worst=stat_worst,
        highlight_self=True,
        width=width, name_col_w=name_col_w,
    )
    y += 20
    y = _draw_team_block(
        draw, y, "ENEMY TEAM", enemies,
        accent=COLORS["enemy_accent"],
        label_bg=COLORS["enemy_label_bg"],
        row_bg=COLORS["enemy_bg"],
        row_bg_alt=COLORS["enemy_bg_alt"],
        self_bg=None,
        stat_leaders=stat_leaders,
        stat_worst=stat_worst,
        highlight_self=False,
        width=width, name_col_w=name_col_w,
    )

    out_path = Path(output_path)
    img.save(out_path, "PNG")
    outputs = [out_path]

    # --- Hero stats image (separate) ---
    if hero_stats and hero_stats.get("values"):
        assert self_player is not None
        hero_path = out_path.with_name(out_path.stem + "_hero" + out_path.suffix)
        hero_img = _render_hero_stats_image(self_player, hero_stats, width=width)
        hero_img.save(hero_path, "PNG")
        outputs.append(hero_path)

    return outputs


def _render_hero_stats_image(player: dict, hero_stats: dict, width: int = DEFAULT_WIDTH) -> Image.Image:
    """Render hero stats as a standalone image."""
    values = hero_stats.get("values", [])
    hero_name = hero_stats.get("hero_name", "")

    featured = [v for v in values if v.get("is_featured")]
    rest = [v for v in values if not v.get("is_featured")]
    sorted_vals = featured + rest

    n_vals = len(sorted_vals)
    n_rows = (n_vals + HERO_STAT_COLS - 1) // HERO_STAT_COLS

    height = HERO_STAT_HEADER_HEIGHT + n_rows * HERO_STAT_ROW_HEIGHT + PADDING

    img = Image.new("RGB", (width, height), COLORS["bg"])
    draw = ImageDraw.Draw(img)

    # Section header
    title = f"HERO STATS  \u2014  {hero_name.upper()}"
    draw.rectangle([0, 0, width, HERO_STAT_HEADER_HEIGHT], fill=COLORS["bg_header"])
    draw.rectangle([0, 0, 8, HERO_STAT_HEADER_HEIGHT], fill=COLORS["ally_accent"])
    draw.text(
        (PADDING + 8, HERO_STAT_HEADER_HEIGHT // 2),
        title, fill=COLORS["ally_accent"], font=FONT_TEAM_LABEL, anchor="lm",
    )

    y = HERO_STAT_HEADER_HEIGHT
    col_w = (width - PADDING * 2) // HERO_STAT_COLS

    for i, val in enumerate(sorted_vals):
        col = i % HERO_STAT_COLS
        row = i // HERO_STAT_COLS
        ry = y + row * HERO_STAT_ROW_HEIGHT

        if col == 0:
            bg = COLORS["ally_bg"] if row % 2 == 0 else COLORS["ally_bg_alt"]
            draw.rectangle([0, ry, width, ry + HERO_STAT_ROW_HEIGHT], fill=bg)

        cx = PADDING + col * col_w
        is_feat = val.get("is_featured", False)

        label = val["label"]
        value = val["value"]
        label_color = COLORS["gold"] if is_feat else COLORS["text_dim"]
        value_color = COLORS["white"] if is_feat else COLORS["text"]

        label_bbox = draw.textbbox((0, 0), label, font=FONT_HERO_STAT_LABEL)
        value_bbox = draw.textbbox((0, 0), value, font=FONT_HERO_STAT_VALUE)
        label_ink_h = label_bbox[3] - label_bbox[1]
        value_ink_h = value_bbox[3] - value_bbox[1]
        gap = 8
        total_ink = label_ink_h + gap + value_ink_h
        block_top = ry + (HERO_STAT_ROW_HEIGHT - total_ink) // 2
        draw.text((cx + 12, block_top - label_bbox[1]), label, fill=label_color, font=FONT_HERO_STAT_LABEL)
        draw.text((cx + 12, block_top + label_ink_h + gap - value_bbox[1]), value, fill=value_color, font=FONT_HERO_STAT_VALUE)

    return img


def _draw_team_block(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    players: list[dict],
    accent: tuple,
    label_bg: tuple,
    row_bg: tuple,
    row_bg_alt: tuple,
    self_bg: tuple | None,
    stat_leaders: dict,
    stat_worst: dict,
    highlight_self: bool,
    width: int = DEFAULT_WIDTH,
    name_col_w: int = DEFAULT_NAME_COL_W,
) -> int:
    # Team label bar
    draw.rectangle([0, y, width, y + TEAM_LABEL_HEIGHT], fill=label_bg)
    draw.rectangle([0, y, 8, y + TEAM_LABEL_HEIGHT], fill=accent)
    draw.text(
        (PADDING + 8, y + TEAM_LABEL_HEIGHT // 2),
        label, fill=accent, font=FONT_TEAM_LABEL, anchor="lm",
    )
    y += TEAM_LABEL_HEIGHT

    # Stat column headers
    draw.rectangle([0, y, width, y + STAT_HEADER_HEIGHT], fill=COLORS["bg_header"])
    _draw_stat_header_row(draw, y, width=width, name_col_w=name_col_w)
    y += STAT_HEADER_HEIGHT

    # Player rows
    for i, p in enumerate(players):
        is_self = p.get("is_self", False) and highlight_self
        if is_self and self_bg:
            bg = self_bg
        else:
            bg = row_bg if i % 2 == 0 else row_bg_alt
        _draw_player_row(draw, y, p, bg, is_self, accent, stat_leaders, stat_worst,
                         width=width, name_col_w=name_col_w)
        y += ROW_HEIGHT
        if i < len(players) - 1:
            draw.line([PADDING, y, width - PADDING, y], fill=COLORS["separator"], width=1)

    return y


def _draw_stat_header_row(draw: ImageDraw.ImageDraw, y: int, width: int = DEFAULT_WIDTH, name_col_w: int = DEFAULT_NAME_COL_W):
    stat_area_start = name_col_w
    col_w = STAT_AREA_W // len(STAT_LABELS)

    for i, label in enumerate(STAT_LABELS):
        cx = stat_area_start + i * col_w + col_w // 2
        draw.text(
            (cx, y + STAT_HEADER_HEIGHT // 2),
            label, fill=COLORS["text_stat"], font=FONT_STAT_HEADER, anchor="mm",
        )


def _draw_player_row(
    draw: ImageDraw.ImageDraw,
    y: int,
    player: dict,
    bg_color: tuple,
    is_self: bool,
    accent_color: tuple,
    stat_leaders: dict,
    stat_worst: dict,
    width: int = DEFAULT_WIDTH,
    name_col_w: int = DEFAULT_NAME_COL_W,
):
    draw.rectangle([0, y, width, y + ROW_HEIGHT], fill=bg_color)

    if is_self:
        draw.rectangle([0, y, 6, y + ROW_HEIGHT], fill=COLORS["ally_accent"])

    stat_area_start = name_col_w
    col_w = STAT_AREA_W // len(STAT_COLS)

    # --- Role icon (colored unicode symbol, no circle) ---
    role = player.get("role", "")
    role_sym = ROLE_SYMBOL.get(role, "?")
    role_col = _role_color(role)
    icon_cx = PADDING + ROLE_ICON_SIZE // 2 + 2
    icon_cy = y + ROW_HEIGHT // 2
    # Use bbox to center the symbol precisely
    sym_bbox = draw.textbbox((0, 0), role_sym, font=FONT_ROLE)
    sym_w = sym_bbox[2] - sym_bbox[0]
    sym_h = sym_bbox[3] - sym_bbox[1]
    draw.text(
        (icon_cx - sym_bbox[0] - sym_w // 2, icon_cy - sym_bbox[1] - sym_h // 2),
        role_sym, fill=role_col, font=FONT_ROLE,
    )

    # --- Player name + hero (vertically centered as a pair) ---
    name = player.get("player_name", "???")
    hero = player.get("hero") or ""
    title = player.get("title") or ""

    hero_parts = []
    if hero:
        hero_parts.append(hero)
    if title:
        hero_parts.append(f"({title})")
    hero_line = " ".join(hero_parts)

    name_color = COLORS["white"] if is_self else COLORS["text"]
    name_x = PADDING + ROLE_ICON_SIZE + 16

    # Use ink bounding boxes for tight visual centering
    name_bbox = draw.textbbox((0, 0), name, font=FONT_PLAYER_NAME)
    name_ink_h = name_bbox[3] - name_bbox[1]  # actual ink height
    name_ink_top = name_bbox[1]  # offset from y=0 to ink top

    if hero_line:
        hero_bbox = draw.textbbox((0, 0), hero_line, font=FONT_HERO)
        hero_ink_h = hero_bbox[3] - hero_bbox[1]
        hero_ink_top = hero_bbox[1]
        gap = 8  # visual gap between ink bottoms/tops
        total_ink = name_ink_h + gap + hero_ink_h
        block_top = y + (ROW_HEIGHT - total_ink) // 2
        # Position so ink tops align to calculated positions
        draw.text((name_x, block_top - name_ink_top), name, fill=name_color, font=FONT_PLAYER_NAME)
        draw.text((name_x, block_top + name_ink_h + gap - hero_ink_top), hero_line, fill=COLORS["text_dim"], font=FONT_HERO)
    else:
        block_top = y + (ROW_HEIGHT - name_ink_h) // 2
        draw.text((name_x, block_top - name_ink_top), name, fill=name_color, font=FONT_PLAYER_NAME)

    # --- Stats ---
    pid = player.get("id", "")
    for i, stat in enumerate(STAT_COLS):
        val = player.get(stat)
        text = _format_stat(val)
        cx = stat_area_start + i * col_w + col_w // 2

        is_leader = pid in stat_leaders.get(stat, [])
        is_worst_deaths = stat == "deaths" and pid in stat_worst.get("deaths", [])

        if is_leader and val is not None and val > 0:
            stat_color = COLORS["gold"]
        elif is_worst_deaths and val is not None and val > 0:
            stat_color = COLORS["death_worst"]
        else:
            stat_color = COLORS["text_stat"]

        draw.text((cx, y + ROW_HEIGHT // 2), text, fill=stat_color, font=FONT_STAT, anchor="mm")
