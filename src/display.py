"""Layout/rendering for the home (departures) screen. Draws through the
90-degree portrait transform and stays inside the calibrated safe
rectangle -- see CLAUDE.md "Physical mounting & drawable area". Never
draws a border/outline around the drawable area -- that's an explicit
anti-decision recorded there; the margins below are silent layout
bounds, not a rendered frame.

Design (redesigned 2026-07-11, see CLAUDE.md "Screen design"): a
kitchen-counter, glance-from-across-the-room display, not a reading
surface. Top-aligned, filling the drawable area -- an earlier vertically
-centered pass left a large dead band above the content and read as
wasteful. Each stop gets a section: a small letter-spaced label, its
next departure's countdown drawn huge (the "hero" -- the thing your eye
lands on first) with a smaller trailing unit ("min"), that departure's
line + destination as an inverted route badge (like a real bus blind) +
text, then its other departures as compact badge rows.

One type system, not two: everything -- the hero countdown, labels,
badges, destinations, rows, footer -- uses framebuf's built-in 8px font,
scaled up. Custom generated fonts (font_to_py) were tried and reverted
(CLAUDE.md "Key library choices" / "RAM-vs-HTTPS conflict"): even the
smallest tested config (a ~15KB hero-only font, tiny charset) reliably
crashed the live fetch/render loop with a MemoryError allocating the
48KB framebuffer, confirmed on hardware 2026-07-11 -- isolated RAM
probes that didn't replicate the real Wi-Fi+NTP+TLS-fetch sequence
first had measured it as safe, but the real loop's heap is already more
fragmented than that by the time a font would load, and this board has
no PSRAM to give it headroom. Not a tuning problem -- a hardware
ceiling. Built-in font scaling is free (zero extra resident RAM) by
comparison. Requires `import framebuf` for the scaled text's scratch
buffer (see _scaled_text()).
"""
import framebuf

import departures


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
# choice). Cut from an earlier 25px after the redesign needed the room
# (owner, 2026-07-11: "the space usage is wasteful").
CONTENT_MARGIN = 14

CONTENT_X0 = DRAW_X0 + CONTENT_MARGIN
CONTENT_Y0 = DRAW_Y0 + CONTENT_MARGIN
CONTENT_W = DRAW_W - 2 * CONTENT_MARGIN

FOOTER_MARGIN = 8  # smaller than CONTENT_MARGIN -- footer sits closer to the true crop-safe edge

# Built-in framebuf font is a fixed 8x8px cell; SCALE multiplies it. This
# scaling is free (zero extra resident RAM) -- these are a pure
# width/legibility trade-off against how many destination characters fit
# per line, not a RAM one; see stop_section()'s truncation math.
HERO_SCALE = 10.5      # the countdown number itself -- the thing your eye lands on first. Capped
                       # by the worst case content: a far-out departure's `display` is a bare
                       # clock time ("14:43", 5 chars, no unit to split off -- see
                       # departures.split_hero_display), which must still fit CONTENT_W.
LABEL_SCALE = 2.8      # stop name eyebrow label
HEADLINE_SCALE = 2.8   # hero's unit word ("min"), and the headline departure's route badge + destination
ROW_SCALE = 2        # secondary departure rows (badge + destination + right-aligned time all share one row's width)
FOOTER_SCALE = 2     # footer clock

LABEL_TRACKING = 3      # extra px between glyphs for the letter-spaced stop-name label
GAP_LABEL_RULE = 8
RULE_HEIGHT = 4
GAP_RULE_HERO = 14
GAP_HERO_LINE = 12       # gap between the hero row and the badge+destination line under it
GAP_LINE_ROWS = 14       # gap between that line and the secondary departures list
GAP_ROW = 10             # between a stop's smaller departure rows
GROUP_GAP = 24           # around the divider between one stop's section and the next
DIVIDER_HEIGHT = 1

BADGE_PAD_X_HEADLINE, BADGE_PAD_Y_HEADLINE = 10, 6
BADGE_PAD_X_ROW, BADGE_PAD_Y_ROW = 6, 4

GAP_BADGE_DEST = 14   # gap between a route badge and the destination text after it
GAP_DEST_TIME = 10    # gap between a row's destination and its right-aligned time


def _scaled_cell(scale):
    """Pixel size of one scaled 8px font cell, rounded -- `scale` need
    not be an integer (see _scaled_text). Used both as a text row's
    height and as one character's advance width, since the built-in
    font's cell is square."""
    return round(8 * scale)


def _scaled_w(s, scale, tracking=0):
    """Advance width, in px, of `s` drawn by _scaled_text at `scale` --
    the built-in font is a fixed 8px monospace cell. Matches
    _scaled_text's own returned width exactly (same rounding, computed
    the same way), so right-alignment/centering/truncation math lines up
    with what's actually drawn."""
    if not s:
        return 0
    return round(8 * len(s) * scale) + tracking * (len(s) - 1)


def _scaled_badge_w(s, scale, pad_x):
    return _scaled_w(s, scale) + 2 * pad_x


def _truncate_to_width(s, scale, max_w):
    """Longest prefix of `s` whose actual drawn width (_scaled_w, the same
    rounding _scaled_text uses) fits in max_w px. A per-character estimate
    is off for fractional scales -- round(8*scale) undercounts vs the
    round(8*n*scale) a whole string actually draws to -- so fit against
    the real width. Keeps at least one char (a too-narrow column is a
    layout bug to notice, not silently blank)."""
    n = len(s)
    while n > 1 and _scaled_w(s[:n], scale) > max_w:
        n -= 1
    return s[:n]


def _to_physical_rect(lx0, ly0, lw, lh):
    """physical.fill_rect(px0, py0, pw, ph) for a logical rect (lx0, ly0,
    lw, lh) -- see CLAUDE.md "Physical mounting & drawable area" for the
    derivation. Don't re-derive; use this."""
    return PHYS_W - ly0 - lh, lx0, lh, lw


def _fill_rect(fb, lx0, ly0, lw, lh, color):
    px0, py0, pw, ph = _to_physical_rect(lx0, ly0, lw, lh)
    fb.fill_rect(px0, py0, pw, ph, color)


def _scaled_text(fb, s, lx0, ly0, scale, color=1, tracking=0):
    """framebuf can't draw rotated glyphs directly, so render into a tiny
    scratch buffer sized just for the string using framebuf's built-in
    8px font, then blit each set pixel into the physical buffer through
    the same transform, scaled up. Scratch buffer here is a handful of
    bytes -- nowhere near the two-BUF_SIZE-buffers MemoryError gotcha
    (CLAUDE.md RAM notes).

    `scale` can be fractional (e.g. 2.8): block boundaries are computed
    from round(i * scale) rather than a fixed scale x scale square, so
    consecutive blocks differ by at most 1px and average out to the
    requested scale overall. Cost is identical to an integer scale --
    still exactly one fill_rect per set source pixel -- it's the block
    size that varies, not the number of blocks.

    `tracking` adds that many px of extra spacing between glyphs (each
    8px source column belongs to glyph tx//8), for the letter-spaced
    label. Returns the total advance width, tracking included, so it
    matches _scaled_w(s, scale, tracking) exactly."""
    tw, th = 8 * len(s), 8
    scratch = bytearray(tw * th // 8 or 1)
    tfb = framebuf.FrameBuffer(scratch, tw, th, framebuf.MONO_HLSB)
    tfb.text(s, 0, 0, 1)
    xb = [round(x * scale) for x in range(tw + 1)]
    yb = [round(y * scale) for y in range(th + 1)]
    for ty in range(th):
        for tx in range(tw):
            if tfb.pixel(tx, ty):
                _fill_rect(fb, lx0 + xb[tx] + tracking * (tx // 8), ly0 + yb[ty],
                           xb[tx + 1] - xb[tx], yb[ty + 1] - yb[ty], color)
    return xb[tw] + tracking * max(0, len(s) - 1)


def _scaled_text_centered(fb, s, ly0, scale, color=1):
    tw = _scaled_w(s, scale)
    lx0 = CONTENT_X0 + max(0, (CONTENT_W - tw) // 2)
    _scaled_text(fb, s, lx0, ly0, scale, color)


def _scaled_text_right(fb, s, lx_right, ly0, scale, color=1):
    """Draw `s` so its right edge lands on lx_right (right-aligned times)."""
    tw = _scaled_w(s, scale)
    _scaled_text(fb, s, lx_right - tw, ly0, scale, color)


def _scaled_badge(fb, s, lx0, ly0, scale, pad_x, pad_y, radius=True):
    """A line-number badge: filled black pill with the number knocked out
    in white (color 0) -- reads like a bus blind and, crucially, gives
    hierarchy without any gray (the panel is 1-bit; see CLAUDE.md "Screen
    design"). Returns (bw, bh) so callers can lay out what follows it.
    `radius` clears the 4 corner pixels so they blend into the white
    background for a hint of rounding."""
    bw = _scaled_badge_w(s, scale, pad_x)
    bh = _scaled_cell(scale) + 2 * pad_y
    _fill_rect(fb, lx0, ly0, bw, bh, 1)
    if radius:
        for cx, cy in ((0, 0), (bw - 1, 0), (0, bh - 1), (bw - 1, bh - 1)):
            fb.pixel(PHYS_W - 1 - (ly0 + cy), lx0 + cx, 0)
    _scaled_text(fb, s, lx0 + pad_x, ly0 + pad_y, scale, color=0)
    return bw, bh


def stop_section(name, deps):
    """Pure: content for one stop's section (no drawing) -- the hero
    departure split into (main, unit) for the two-size hero treatment,
    its route badge + destination (truncated to fit at HEADLINE_SCALE),
    and the remaining departures as (line, destination, display) rows
    (truncated to fit at ROW_SCALE, accounting for that row's own badge
    and right-aligned time width). "No departures" never gets the hero
    treatment -- there's nothing urgent to emphasize."""
    if not deps:
        return {"name": name, "hero_main": None, "hero_unit": None,
                "badge_line": None, "dest": "No departures", "rows": []}

    hero = deps[0]
    hero_main, hero_unit = departures.split_hero_display(hero["display"])
    badge_line = hero["line"]
    dest_max_w = CONTENT_W - _scaled_badge_w(badge_line, HEADLINE_SCALE, BADGE_PAD_X_HEADLINE) - GAP_BADGE_DEST
    dest = _truncate_to_width(hero["destination"], HEADLINE_SCALE, dest_max_w)

    rows = []
    for dep in deps[1:]:
        bw = _scaled_badge_w(dep["line"], ROW_SCALE, BADGE_PAD_X_ROW)
        time_w = _scaled_w(dep["display"], ROW_SCALE)
        dest_max_w = CONTENT_W - bw - GAP_BADGE_DEST - time_w - GAP_DEST_TIME
        rows.append((dep["line"], _truncate_to_width(dep["destination"], ROW_SCALE, dest_max_w), dep["display"]))

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
    """Pure: footnote text -- just the current local date/time. Used to
    show a separate "last updated" time too, but with poll and refresh
    both on a ~60s cadence that was almost always identical to "now" and
    added no information (owner, 2026-07-09: "remove ... it appears to
    always be the same as the current date and time"). Staleness is still
    surfaced -- via the "(stale)" suffix, which appears whenever the last
    fetch cycle didn't succeed for every configured stop -- just without a
    second timestamp to prove it. One row if it fits at FOOTER_SCALE, else
    two."""
    text = "%s %s" % (date_str, time_str)
    if stale:
        text += " (stale)"
    max_chars = CONTENT_W // _scaled_cell(FOOTER_SCALE)
    if len(text) <= max_chars:
        return [text]
    line2 = time_str + (" (stale)" if stale else "")
    return [date_str[:max_chars], line2[:max_chars]]


def draw_home(fb, sections, footer):
    """Draws each stop's section (from stop_section()) top-to-bottom, in
    order, with a divider between sections, then footer (from
    footer_lines()) anchored near the bottom of the drawable area with
    its own smaller margin. Logs everything to serial (CLAUDE.md "make
    the code corroborate the screen"). Returns (content_bottom,
    footer_top) logical-y coordinates so callers/tests can check the
    content didn't grow into the footer band."""
    fb.fill(0)
    y = CONTENT_Y0
    logged = []

    for gi, section in enumerate(sections):
        if gi > 0:
            y += GROUP_GAP
            _fill_rect(fb, CONTENT_X0, y, CONTENT_W, DIVIDER_HEIGHT, 1)
            y += DIVIDER_HEIGHT + GROUP_GAP

        _scaled_text(fb, section["name"].upper(), CONTENT_X0, y, LABEL_SCALE, tracking=LABEL_TRACKING)
        y += _scaled_cell(LABEL_SCALE) + GAP_LABEL_RULE
        _fill_rect(fb, CONTENT_X0, y, CONTENT_W, RULE_HEIGHT, 1)
        y += RULE_HEIGHT + GAP_RULE_HERO
        logged.append(section["name"])

        if section["hero_main"] is not None:
            hw = _scaled_text(fb, section["hero_main"], CONTENT_X0, y, HERO_SCALE)
            if section["hero_unit"]:
                _scaled_text(fb, section["hero_unit"], CONTENT_X0 + hw + GAP_BADGE_DEST,
                             y + _scaled_cell(HERO_SCALE) - _scaled_cell(HEADLINE_SCALE), HEADLINE_SCALE)
            y += _scaled_cell(HERO_SCALE) + GAP_HERO_LINE

            bw, bh = _scaled_badge(fb, section["badge_line"], CONTENT_X0, y, HEADLINE_SCALE,
                                    BADGE_PAD_X_HEADLINE, BADGE_PAD_Y_HEADLINE)
            _scaled_text(fb, section["dest"], CONTENT_X0 + bw + GAP_BADGE_DEST,
                         y + (bh - _scaled_cell(HEADLINE_SCALE)) // 2, HEADLINE_SCALE)
            y += bh
            logged.append(section["hero_main"] + ((" " + section["hero_unit"]) if section["hero_unit"] else ""))
            logged.append("%s  %s" % (section["badge_line"], section["dest"]))
        else:
            _scaled_text(fb, section["dest"], CONTENT_X0, y, HEADLINE_SCALE)
            y += _scaled_cell(HEADLINE_SCALE)
            logged.append(section["dest"])

        if section["rows"]:
            y += GAP_LINE_ROWS
            for line, dest, disp in section["rows"]:
                bw, bh = _scaled_badge(fb, line, CONTENT_X0, y, ROW_SCALE, BADGE_PAD_X_ROW, BADGE_PAD_Y_ROW)
                ty = y + (bh - _scaled_cell(ROW_SCALE)) // 2
                _scaled_text(fb, dest, CONTENT_X0 + bw + GAP_BADGE_DEST, ty, ROW_SCALE)
                _scaled_text_right(fb, disp, CONTENT_X0 + CONTENT_W, ty, ROW_SCALE)
                y += bh + GAP_ROW
                logged.append("%s  %s  %s" % (line, dest, disp))
            y -= GAP_ROW

    content_bottom = y  # logical y just past the last section, before the footer

    footer_h = len(footer) * _scaled_cell(FOOTER_SCALE) + max(0, len(footer) - 1) * GAP_ROW
    footer_top = DRAW_Y0 + DRAW_H - FOOTER_MARGIN - footer_h
    fy = footer_top
    for line in footer:
        _scaled_text_centered(fb, line, fy, FOOTER_SCALE)
        fy += _scaled_cell(FOOTER_SCALE) + GAP_ROW

    print("display: home screen -- " + " | ".join(logged) + " || " + " | ".join(footer))
    # Returned so callers/tests can assert content didn't run into the footer
    # band (the FakeFB in tests only guards the physical buffer bounds).
    return content_bottom, footer_top
