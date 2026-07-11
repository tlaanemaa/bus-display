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
    ascii_all = "".join(chr(c) for c in range(0x20, 0x7F))
    f["head"].warm(ascii_all)
    f["row"].warm(ascii_all)
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


def draw_home(fb, sections, footer):
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

    footer_h = len(footer) * row_f.height + max(0, len(footer) - 1) * GAP_ROW
    footer_top = DRAW_Y0 + DRAW_H - FOOTER_MARGIN - footer_h
    fy = footer_top
    for line in footer:
        _text_centered(fb, row_f, line, fy)
        fy += row_f.height + GAP_ROW

    print("display: home screen -- " + " | ".join(logged) + " || " + " | ".join(footer))
    # Returned so callers/tests can assert content didn't run into the footer
    # band (the FakeFB in tests only guards the physical buffer bounds).
    return content_bottom, footer_top
