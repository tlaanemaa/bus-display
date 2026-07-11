"""Layout/rendering for the home (departures) screen. Draws through the
90-degree portrait transform and stays inside the calibrated safe
rectangle -- see CLAUDE.md "Physical mounting & drawable area". Never
draws a border/outline around the drawable area -- that's an explicit
anti-decision recorded there; the margins below are silent layout
bounds, not a rendered frame.

Design (see CLAUDE.md "Screen design"): a kitchen-counter, glance-from-
across-the-room display, not a reading surface. Top-aligned, filling the
drawable area. Each stop gets a section: a small letter-spaced label, its
next departure's countdown drawn huge (the "hero" -- the thing your eye
lands on first) with a smaller trailing unit ("min") sharing its
baseline, that departure's line + destination as an inverted route badge
(like a real bus blind) + text, then its other departures as compact
badge rows.

Type: a real print-like face (Bitter, a slab serif) STREAMED from flash
glyph-by-glyph via bitfont.py, at three fixed sizes -- hero (big
countdown), head (labels / headline / the "min" unit) and row (secondary
departures / footer). This replaced the old built-in-8x8-font-scaled-10x
"Minecraft" look; the panel is 1-bit (no anti-aliasing) so smoothness
comes purely from rendering each glyph at its true size. The earlier
concern that a nicer font is RAM-unviable was specific to font_to_py's
RESIDENT glyph module (CLAUDE.md "RAM-vs-HTTPS conflict"); streaming from
flash sidesteps it -- resident cost is ~one glyph, and the draw window
never overlaps the TLS fetch. Font files live in fonts/ (device) /
src/fonts/ (host); regenerate with tools/gen_font.py.

Fixed pixel sizes (not the old arbitrary fractional scale) mean vertical
rhythm is expressed as gaps between font cell heights (font.height),
which are the ink-cropped cell heights baked into each .fnt.
"""
import bitfont


PHYS_W = 800  # native physical buffer, fixed by the panel
PHYS_H = 480

LW = 480  # logical portrait canvas, swapped from the physical buffer
LH = 800

MARGIN_LEFT = 7
MARGIN_TOP = 33
MARGIN_RIGHT = 0
MARGIN_BOTTOM = 43

DRAW_X0 = MARGIN_LEFT
DRAW_Y0 = MARGIN_TOP
DRAW_W = LW - MARGIN_LEFT - MARGIN_RIGHT
DRAW_H = LH - MARGIN_TOP - MARGIN_BOTTOM

# Breathing room around the main content, on top of the calibrated
# crop-safety margins above (those are a hardware fact, this is a styling
# choice).
CONTENT_MARGIN = 14

CONTENT_X0 = DRAW_X0 + CONTENT_MARGIN
CONTENT_Y0 = DRAW_Y0 + CONTENT_MARGIN
CONTENT_W = DRAW_W - 2 * CONTENT_MARGIN

FOOTER_MARGIN = 8  # smaller than CONTENT_MARGIN -- footer sits closer to the true crop-safe edge

LABEL_TRACKING = 3      # extra px between glyphs for the letter-spaced stop-name label
GAP_LABEL_RULE = 6
RULE_HEIGHT = 4
GAP_RULE_HERO = 12
GAP_HERO_LINE = 10       # gap between the hero row and the badge+destination line under it
GAP_LINE_ROWS = 12       # gap between that line and the secondary departures list
GAP_ROW = 8              # between a stop's smaller departure rows
GROUP_GAP = 20           # around the divider between one stop's section and the next
DIVIDER_HEIGHT = 1

BADGE_PAD_X_HEADLINE, BADGE_PAD_Y_HEADLINE = 10, 5
BADGE_PAD_X_ROW, BADGE_PAD_Y_ROW = 6, 3

GAP_BADGE_DEST = 14   # gap between a route badge and the destination text after it
GAP_DEST_TIME = 10    # gap between a row's destination and its right-aligned time

# Footer weather row (drawn above the clock line -- see draw_home / the
# weather glyphs above). The icon box is a touch taller than the temps so
# it reads as the anchor of the row.
WEATHER_ICON_PAD = 6      # icon box height = head font height + this
GAP_WEATHER_ICON = 12     # icon -> temperature
GAP_TEMP_PRECIP = 18      # temperature -> precip cue
GAP_DROP_PRECIP = 6       # droplet -> its percentage
GAP_WEATHER_CLOCK = 10    # weather row -> clock line below it


# --- fonts: streamed from flash, opened lazily on first use (never at
# import -- keeps the eager-import RAM discipline, CLAUDE.md). Cached so
# the three files open once and stay open (fds are cheap; bitmap data is
# never held resident -- that's bitfont's whole point).
_FONTS = {}
_FONT_FILES = {"hero": "bitter_hero.fnt", "head": "bitter_head.fnt", "row": "bitter_row.fnt"}


def _fonts():
    if not _FONTS:
        for role, name in _FONT_FILES.items():
            # Device runs from / with fonts at fonts/; host tests run from
            # the repo root with them under src/fonts/. Try both.
            for d in ("fonts", "src/fonts"):
                try:
                    _FONTS[role] = bitfont.Font(d + "/" + name)
                    break
                except OSError:
                    pass
            else:
                raise OSError("font not found: " + name)
    return _FONTS


def warm_fonts():
    """Open the fonts and fully populate their advance caches BEFORE the
    fetch/render loop starts (main.py calls this once). On a clean boot
    heap this is free; the point is that no font state then gets allocated
    during a live draw -- which, while the 48KB framebuffer is alive,
    would strand objects into the region the next SL TLS handshake needs
    (CLAUDE.md "RAM-vs-HTTPS conflict"; bitfont.py module docstring)."""
    f = _fonts()
    # Printable ASCII + the degree sign (weather temps) -- any char measured
    # or drawn without being warmed here would open the font file mid-draw,
    # allocating into the live-framebuffer region (see bitfont.py docstring).
    head_row = "".join(chr(c) for c in range(0x20, 0x7F)) + "°"
    f["head"].warm(head_row)
    f["row"].warm(head_row)
    f["hero"].warm("0123456789:Nu ")


def _plot_run(fb, lx, ly, n, color):
    """Fill one horizontal logical run of `n` px at (lx, ly) onto the
    physical panel -- the transform of _to_physical_rect(lx, ly, n, 1)
    inlined (no tuple allocation) and hoisted to module level (no per-draw
    closure), because bitfont calls this once per glyph run and the draw
    path must not churn the heap. See bitfont.Font.draw."""
    fb.fill_rect(PHYS_W - ly - 1, lx, 1, n, color)


def _to_physical_rect(lx0, ly0, lw, lh):
    """physical.fill_rect(px0, py0, pw, ph) for a logical rect (lx0, ly0,
    lw, lh) -- see CLAUDE.md "Physical mounting & drawable area" for the
    derivation. Don't re-derive; use this."""
    return PHYS_W - ly0 - lh, lx0, lh, lw


def _fill_rect(fb, lx0, ly0, lw, lh, color):
    px0, py0, pw, ph = _to_physical_rect(lx0, ly0, lw, lh)
    fb.fill_rect(px0, py0, pw, ph, color)


def _text(fb, font, s, lx0, ly0, color=1, tracking=0):
    """Draw `s` at logical (lx0, ly0) in `font`, streaming glyphs from
    flash. framebuf can't draw rotated glyphs, so bitfont hands us each
    glyph as horizontal runs and we map every run onto the physical panel
    via the module-level _plot_run (NOT a per-call lambda -- see its
    docstring). Returns the advance width (== font.measure(s, tracking))."""
    return font.draw(s, lx0, ly0, color, fb, _plot_run, tracking)


def _text_centered(fb, font, s, ly0, color=1):
    lx0 = CONTENT_X0 + max(0, (CONTENT_W - font.measure(s)) // 2)
    _text(fb, font, s, lx0, ly0, color)


def _text_right(fb, font, s, lx_right, ly0, color=1):
    """Draw `s` so its right edge lands on lx_right (right-aligned times)."""
    _text(fb, font, s, lx_right - font.measure(s), ly0, color)


def _badge_w(font, s, pad_x):
    return font.measure(s) + 2 * pad_x


def _truncate_to_width(font, s, max_w):
    """Longest prefix of `s` whose drawn width fits in max_w px (measured
    with the same font, so it matches the ink). Keeps at least one char --
    a too-narrow column is a layout bug to notice, not silently blank."""
    n = len(s)
    while n > 1 and font.measure(s[:n]) > max_w:
        n -= 1
    return s[:n]


def _badge(fb, font, s, lx0, ly0, pad_x, pad_y, radius=True):
    """A line-number badge: filled black pill with the number knocked out
    in white (color 0) -- reads like a bus blind and gives hierarchy
    without any gray (the panel is 1-bit). Returns (bw, bh) so callers
    can lay out what follows it. `radius` clears the 4 corner pixels so
    they blend into the white background for a hint of rounding."""
    bw = _badge_w(font, s, pad_x)
    bh = font.height + 2 * pad_y
    _fill_rect(fb, lx0, ly0, bw, bh, 1)
    if radius:
        for cx, cy in ((0, 0), (bw - 1, 0), (0, bh - 1), (bw - 1, bh - 1)):
            fb.pixel(PHYS_W - 1 - (ly0 + cy), lx0 + cx, 0)
    _text(fb, font, s, lx0 + pad_x, ly0 + pad_y, color=0)
    return bw, bh


# --- weather glyphs: small procedural 1-bit icons drawn straight through
# the 90deg mount transform, ALLOCATION-FREE (integer math + the
# module-level _plot_run only -- no tuples, floats, or closures per draw).
# They run inside the live-framebuffer draw window, so they obey the same
# no-heap-churn discipline as the streamed font path (see bitfont.py): any
# object stranded while the 48KB buffer is alive can starve the next SL TLS
# handshake. Condition strings match weather.py's constants (loose coupling
# by string, kept in sync there). Each drawer fills a square (x, y, s, s)
# logical box; s is tuned to sit on the footer next to the temperature.

def _box(fb, lx, ly, lw, lh, color):
    """Filled logical rect via per-row _plot_run -- allocation-free, unlike
    _fill_rect (which builds a tuple). For the glyph drawers only."""
    if lw <= 0 or lh <= 0:
        return
    for i in range(lh):
        _plot_run(fb, lx, ly + i, lw, color)


def _disc(fb, cx, cy, r, color):
    """Filled circle by integer scanline (no float, no alloc): each row's
    half-width is the largest dx with dx*dx + dy*dy <= r*r."""
    rr = r * r
    dy = -r
    while dy <= r:
        dx = 0
        while (dx + 1) * (dx + 1) + dy * dy <= rr:
            dx += 1
        _plot_run(fb, cx - dx, cy + dy, 2 * dx + 1, color)
        dy += 1


def _seg(fb, x0, y0, x1, y1, t, color):
    """Thick short segment as a run of t-by-t boxes stepped along the line
    (integer Lerp; fine for the few px these glyphs need)."""
    dx = x1 - x0
    dy = y1 - y0
    steps = abs(dx) if abs(dx) > abs(dy) else abs(dy)
    if steps < 1:
        steps = 1
    h = t // 2
    for i in range(steps + 1):
        px = x0 + dx * i // steps
        py = y0 + dy * i // steps
        _box(fb, px - h, py - h, t, t, color)


def _cloud(fb, x, y, w, h, color):
    """Lumpy cloud with a flattish bottom: three bumps (discs) over a base
    slab, all bottoms landing on one line so the underside reads straight."""
    bottom = y + (h * 82) // 100
    rL = (h * 26) // 100
    rC = (h * 34) // 100
    rR = (h * 28) // 100
    cxL = x + (w * 32) // 100
    cxC = x + (w * 52) // 100
    cxR = x + (w * 72) // 100
    _disc(fb, cxL, bottom - rL, rL, color)
    _disc(fb, cxC, bottom - rC, rC, color)
    _disc(fb, cxR, bottom - rR, rR, color)
    slab_top = bottom - rL
    _box(fb, cxL, slab_top, cxR - cxL, bottom - slab_top, color)


def _draw_sun(fb, x, y, s, color=1):
    cx = x + s // 2
    cy = y + s // 2
    r = (s * 24) // 100
    _disc(fb, cx, cy, r, color)
    t = max(2, (s * 8) // 100)
    r1 = r + (s * 9) // 100
    r2 = r + (s * 22) // 100
    d1 = (r1 * 7) // 10
    d2 = (r2 * 7) // 10
    _seg(fb, cx + r1, cy, cx + r2, cy, t, color)
    _seg(fb, cx - r1, cy, cx - r2, cy, t, color)
    _seg(fb, cx, cy + r1, cx, cy + r2, t, color)
    _seg(fb, cx, cy - r1, cx, cy - r2, t, color)
    _seg(fb, cx + d1, cy + d1, cx + d2, cy + d2, t, color)
    _seg(fb, cx + d1, cy - d1, cx + d2, cy - d2, t, color)
    _seg(fb, cx - d1, cy + d1, cx - d2, cy + d2, t, color)
    _seg(fb, cx - d1, cy - d1, cx - d2, cy - d2, t, color)


def _draw_partly(fb, x, y, s, color=1):
    _draw_sun(fb, x - (s * 4) // 100, y - (s * 10) // 100, (s * 60) // 100, color)
    _cloud(fb, x + (s * 18) // 100, y + (s * 30) // 100, (s * 82) // 100, (s * 60) // 100, color)


def _draw_cloudy(fb, x, y, s, color=1):
    _cloud(fb, x, y + (s * 8) // 100, s, (s * 78) // 100, color)


def _draw_fog(fb, x, y, s, color=1):
    _cloud(fb, x, y - (s * 6) // 100, s, (s * 58) // 100, color)
    t = max(2, (s * 7) // 100)
    for i, fy in enumerate((66, 82, 98)):
        yy = y + (s * fy) // 100
        inset = (s * (10 + 8 * (i % 2))) // 100
        _box(fb, x + inset, yy, s - 2 * inset, t, color)


def _draw_drizzle(fb, x, y, s, color=1):
    """Light rain: a scatter of small dots -- reads as spitting/drizzle,
    clearly lighter than the streaks of _draw_rain."""
    _cloud(fb, x, y, s, (s * 62) // 100, color)
    r = max(1, (s * 4) // 100)
    for fx, fy in ((30, 74), (52, 74), (72, 74), (40, 92), (62, 92)):
        _disc(fb, x + (s * fx) // 100, y + (s * fy) // 100, r, color)


def _draw_rain(fb, x, y, s, color=1):
    _cloud(fb, x, y, s, (s * 62) // 100, color)
    t = max(2, (s * 7) // 100)
    top = y + (s * 66) // 100
    bot = y + (s * 96) // 100
    for fx in (30, 50, 70):
        sx = x + (s * fx) // 100
        _seg(fb, sx, top, sx - (s * 9) // 100, bot, t, color)


def _draw_rain_heavy(fb, x, y, s, color=1):
    """Heavy rain: four longer, thicker streaks packed tighter -- the
    'bucketing down, take the umbrella' state."""
    _cloud(fb, x, y, s, (s * 60) // 100, color)
    t = max(3, (s * 9) // 100)
    top = y + (s * 62) // 100
    bot = y + (s * 100) // 100
    for fx in (24, 42, 60, 78):
        sx = x + (s * fx) // 100
        _seg(fb, sx, top, sx - (s * 11) // 100, bot, t, color)


def _draw_snow(fb, x, y, s, color=1):
    _cloud(fb, x, y, s, (s * 62) // 100, color)
    t = max(2, (s * 4) // 100)   # thin arms so the star stays open, not a blob
    a = (s * 7) // 100
    for fx in (24, 50, 76):      # wide spacing -> three distinct flakes
        cx = x + (s * fx) // 100
        cy = y + (s * 82) // 100
        _seg(fb, cx - a, cy, cx + a, cy, t, color)
        _seg(fb, cx, cy - a, cx, cy + a, t, color)
        _seg(fb, cx - a, cy - a, cx + a, cy + a, t, color)
        _seg(fb, cx - a, cy + a, cx + a, cy - a, t, color)


def _draw_thunder(fb, x, y, s, color=1):
    _cloud(fb, x, y, s, (s * 62) // 100, color)
    t = max(3, (s * 9) // 100)
    x0 = x + (s * 56) // 100
    y0 = y + (s * 62) // 100
    xm = x + (s * 40) // 100
    ym = y + (s * 82) // 100
    x1 = x + (s * 58) // 100
    y1 = y + (s * 82) // 100
    x2 = x + (s * 38) // 100
    y2 = y + (s * 100) // 100
    _seg(fb, x0, y0, xm, ym, t, color)
    _seg(fb, xm, ym, x1, y1, t, color)
    _seg(fb, x1, y1, x2, y2, t, color)


def _draw_drop(fb, x, y, s, color=1):
    """Small teardrop for the precipitation cue: a round belly with the
    point well ABOVE it (a smaller disc set low so the taper stays visible)."""
    r = (s * 34) // 100
    cx = x + s // 2
    cy = y + s - r
    _disc(fb, cx, cy, r, color)
    h = cy - y
    for i in range(h):
        half = (r * i) // h
        _plot_run(fb, cx - half, y + i, 2 * half + 1, color)


_WEATHER_DRAWERS = {
    "clear": _draw_sun,
    "partly": _draw_partly,
    "cloudy": _draw_cloudy,
    "fog": _draw_fog,
    "drizzle": _draw_drizzle,
    "rain": _draw_rain,
    "rain_heavy": _draw_rain_heavy,
    "snow": _draw_snow,
    "thunder": _draw_thunder,
}


def draw_weather_glyph(fb, condition, x, y, s, color=1):
    """Draw the icon for a weather condition string (weather.py's
    constants) in the (x, y, s, s) logical box. Unknown -> cloudy."""
    _WEATHER_DRAWERS.get(condition, _draw_cloudy)(fb, x, y, s, color)


def stop_section(name, deps):
    """Pure: content for one stop's section (no drawing) -- the hero
    departure split into (main, unit) for the two-size hero treatment,
    its route badge + destination (truncated to fit at head size), and
    the remaining departures as (line, destination, display) rows
    (truncated to fit at row size, accounting for that row's own badge
    and right-aligned time width). "No departures" never gets the hero
    treatment -- there's nothing urgent to emphasize.

    Not framebuffer-pure: it measures with the real fonts (opening them
    from flash) so truncation matches the ink. That's fine on host too --
    the .fnt files are plain data both places."""
    import departures
    if not deps:
        return {"name": name, "hero_main": None, "hero_unit": None,
                "badge_line": None, "dest": "No departures", "rows": []}

    f = _fonts()
    head, row = f["head"], f["row"]

    hero = deps[0]
    hero_main, hero_unit = departures.split_hero_display(hero["display"])
    badge_line = hero["line"]
    dest_max_w = CONTENT_W - _badge_w(head, badge_line, BADGE_PAD_X_HEADLINE) - GAP_BADGE_DEST
    dest = _truncate_to_width(head, hero["destination"], dest_max_w)

    rows = []
    for dep in deps[1:]:
        bw = _badge_w(row, dep["line"], BADGE_PAD_X_ROW)
        time_w = row.measure(dep["display"])
        dest_max_w = CONTENT_W - bw - GAP_BADGE_DEST - time_w - GAP_DEST_TIME
        rows.append((dep["line"], _truncate_to_width(row, dep["destination"], dest_max_w), dep["display"]))

    return {"name": name, "hero_main": hero_main, "hero_unit": hero_unit,
            "badge_line": badge_line, "dest": dest, "rows": rows}


def section_lines(section):
    """Flat list of every text string a section contains, in display
    order -- used for change-detection and serial logging."""
    lines = [section["name"]]
    if section["hero_main"] is not None:
        lines.append(section["hero_main"] + (" " + section["hero_unit"] if section["hero_unit"] else ""))
        lines.append("%s  %s" % (section["badge_line"], section["dest"]))
    else:
        lines.append(section["dest"])
    lines.extend("%s  %s  %s" % row for row in section["rows"])
    return lines


def footer_lines(date_str, time_str, stale=False):
    """Pure: footnote text -- just the current local date/time, with a
    "(stale)" suffix when the last fetch cycle didn't succeed for every
    configured stop. One row if it fits at row size, else two."""
    f = _fonts()["row"]
    text = "%s %s" % (date_str, time_str)
    if stale:
        text += " (stale)"
    if f.measure(text) <= CONTENT_W:
        return [text]
    line2 = time_str + (" (stale)" if stale else "")
    return [date_str, line2]


def _weather_row_height():
    return _fonts()["head"].height + WEATHER_ICON_PAD


def _draw_weather_row(fb, weather, ly):
    """Draw the centered "today" weather row at logical y `ly`: condition
    icon + high/low temperature, plus a droplet + precipitation-chance cue
    when it's high enough to matter (weather.format_precip). All one
    centered group, vertically centered on the icon-box height. weather is
    the dict from weather.parse_weather()."""
    import weather as wx  # pure module (no hardware imports); lazy like departures
    head_f = _fonts()["head"]
    row_f = _fonts()["row"]
    wi = _weather_row_height()

    temps = wx.format_temps(weather)
    precip = wx.format_precip(weather)
    tw = head_f.measure(temps)
    total = wi + GAP_WEATHER_ICON + tw
    if precip:
        pw = row_f.measure(precip)
        total += GAP_TEMP_PRECIP + wi // 2 + GAP_DROP_PRECIP + pw

    x = CONTENT_X0 + max(0, (CONTENT_W - total) // 2)
    draw_weather_glyph(fb, weather["condition"], x, ly, wi)
    x += wi + GAP_WEATHER_ICON
    _text(fb, head_f, temps, x, ly + (wi - head_f.height) // 2)
    x += tw
    if precip:
        x += GAP_TEMP_PRECIP
        dw = wi // 2
        _draw_drop(fb, x, ly + (wi - dw) // 2, dw)
        x += dw + GAP_DROP_PRECIP
        _text(fb, row_f, precip, x, ly + (wi - row_f.height) // 2)


def draw_home(fb, sections, footer, weather=None):
    """Draws each stop's section (from stop_section()) top-to-bottom, in
    order, with a divider between sections, then footer (from
    footer_lines()) anchored near the bottom of the drawable area with
    its own smaller margin. Logs everything to serial (CLAUDE.md "make
    the code corroborate the screen"). Returns (content_bottom,
    footer_top) logical-y coordinates so callers/tests can check the
    content didn't grow into the footer band."""
    f = _fonts()
    hero_f, head_f, row_f = f["hero"], f["head"], f["row"]

    fb.fill(0)
    y = CONTENT_Y0
    logged = []

    for gi, section in enumerate(sections):
        if gi > 0:
            y += GROUP_GAP
            _fill_rect(fb, CONTENT_X0, y, CONTENT_W, DIVIDER_HEIGHT, 1)
            y += DIVIDER_HEIGHT + GROUP_GAP

        _text(fb, head_f, section["name"].upper(), CONTENT_X0, y, tracking=LABEL_TRACKING)
        y += head_f.height + GAP_LABEL_RULE
        _fill_rect(fb, CONTENT_X0, y, CONTENT_W, RULE_HEIGHT, 1)
        y += RULE_HEIGHT + GAP_RULE_HERO
        logged.append(section["name"])

        if section["hero_main"] is not None:
            hw = _text(fb, hero_f, section["hero_main"], CONTENT_X0, y)
            if section["hero_unit"]:
                # Align the small unit's baseline to the hero's baseline.
                _text(fb, head_f, section["hero_unit"], CONTENT_X0 + hw + GAP_BADGE_DEST,
                      y + hero_f.baseline - head_f.baseline)
            y += hero_f.height + GAP_HERO_LINE

            bw, bh = _badge(fb, head_f, section["badge_line"], CONTENT_X0, y,
                            BADGE_PAD_X_HEADLINE, BADGE_PAD_Y_HEADLINE)
            _text(fb, head_f, section["dest"], CONTENT_X0 + bw + GAP_BADGE_DEST,
                  y + (bh - head_f.height) // 2)
            y += bh
            logged.append(section["hero_main"] + ((" " + section["hero_unit"]) if section["hero_unit"] else ""))
            logged.append("%s  %s" % (section["badge_line"], section["dest"]))
        else:
            _text(fb, head_f, section["dest"], CONTENT_X0, y)
            y += head_f.height
            logged.append(section["dest"])

        if section["rows"]:
            y += GAP_LINE_ROWS
            for line, dest, disp in section["rows"]:
                bw, bh = _badge(fb, row_f, line, CONTENT_X0, y, BADGE_PAD_X_ROW, BADGE_PAD_Y_ROW)
                ty = y + (bh - row_f.height) // 2
                _text(fb, row_f, dest, CONTENT_X0 + bw + GAP_BADGE_DEST, ty)
                _text_right(fb, row_f, disp, CONTENT_X0 + CONTENT_W, ty)
                y += bh + GAP_ROW
                logged.append("%s  %s  %s" % (line, dest, disp))
            y -= GAP_ROW

    content_bottom = y  # logical y just past the last section, before the footer

    # Footer band, bottom-anchored: an optional weather row sits above the
    # clock line(s), both inside the same band (see _draw_weather_row).
    text_h = len(footer) * row_f.height + max(0, len(footer) - 1) * GAP_ROW
    weather_h = (_weather_row_height() + GAP_WEATHER_CLOCK) if weather else 0
    footer_top = DRAW_Y0 + DRAW_H - FOOTER_MARGIN - text_h - weather_h
    fy = footer_top
    if weather:
        _draw_weather_row(fb, weather, fy)
        fy += _weather_row_height() + GAP_WEATHER_CLOCK
    for line in footer:
        _text_centered(fb, row_f, line, fy)
        fy += row_f.height + GAP_ROW

    import weather as wx
    print("display: home screen -- " + " | ".join(logged) + " || "
          + " | ".join(footer) + " || " + wx.summary_text(weather))
    # Returned so callers/tests can assert content didn't run into the footer
    # band (the FakeFB in tests only guards the physical buffer bounds).
    return content_bottom, footer_top
