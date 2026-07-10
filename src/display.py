"""Layout/rendering for the home (departures) screen onto the physical
framebuf. Draws through the 90-degree portrait transform and stays inside
the calibrated safe rectangle -- see CLAUDE.md "Physical mounting &
drawable area". Never draws a border/outline around the drawable area --
that's an explicit anti-decision recorded there; the margins below are
silent layout bounds, not a rendered frame.

Design (settled 2026-07-09, see CLAUDE.md "Departures logic & stops" /
"Screen design"): this is a kitchen-counter, glance-from-across-the-room
display, not a reading surface. The single fact that matters most is "how
soon do I need to leave," so each stop gets a section: its name as a
label, then its next departure's countdown drawn huge and centered (a
"hero" -- the thing your eye lands on first), a small caption underneath
naming which bus it is, then its other departures as a compact list.
Departures are the first content source but the screen is kept sectioned
(see CLAUDE.md "Future direction") rather than hardcoded whole-screen, so
weather/Homey sections can be added later without reworking this module.
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

# ~5mm of breathing room around the main content, on top of the
# calibrated crop-safety margins above -- those are a hardware fact
# (physical dead space), this is a styling choice. ~0.204mm/px,
# approximate (the panel's published dot pitch) -- trust the physical
# result over this number.
CONTENT_MARGIN = 25

CONTENT_X0 = DRAW_X0 + CONTENT_MARGIN
CONTENT_Y0 = DRAW_Y0 + CONTENT_MARGIN
CONTENT_W = DRAW_W - 2 * CONTENT_MARGIN
CONTENT_H = DRAW_H - 2 * CONTENT_MARGIN

HEADER_SCALE = 3   # stop name, section label
HERO_SCALE = 7     # each stop's next departure's countdown -- the focal point of the whole screen
CAPTION_SCALE = 2  # "<line> <destination>" under the hero, identifies which bus
ROW_SCALE = 2      # each stop's other departures
FOOTER_SCALE = 2   # last-updated + current date/time -- bumped up from an earlier, too-small pass

RULE_HEIGHT = 3
HEADER_RULE_GAP = 8      # gap between stop name and the rule under it
HEADER_TO_HERO_GAP = 16  # gap between that rule and the hero countdown
HERO_TO_CAPTION_GAP = 4  # tight -- caption directly supports the hero above it
CAPTION_TO_ROWS_GAP = 16
ROW_GAP = 8              # between a stop's smaller departure rows
GROUP_GAP = 34           # around the separator between one stop's section and the next --
                         # deliberately generous ("separate the stops more" -- owner, 2026-07-09)
FOOTER_ROW_GAP = 4
FOOTER_MARGIN = 12       # smaller than CONTENT_MARGIN -- lets the footer sit lower/closer to the
                         # true crop-safe edge than the main content does ("move it lower" -- owner)


def _to_physical_rect(lx0, ly0, lw, lh):
    """physical.fill_rect(px0, py0, pw, ph) for a logical rect (lx0, ly0,
    lw, lh) -- see CLAUDE.md "Physical mounting & drawable area" for the
    derivation. Don't re-derive; use this."""
    return PHYS_W - ly0 - lh, lx0, lh, lw


def _fill_rect(fb, lx0, ly0, lw, lh, color):
    px0, py0, pw, ph = _to_physical_rect(lx0, ly0, lw, lh)
    fb.fill_rect(px0, py0, pw, ph, color)


def _text(fb, s, lx0, ly0, scale, color=1):
    """framebuf can't draw rotated glyphs directly, so render into a tiny
    scratch buffer sized just for the string, then blit each set pixel
    into the physical buffer through the same transform, scaled up (the
    built-in 8px font is unreadable at display distance -- see CLAUDE.md
    "Key library choices"; custom generated fonts are the declared future
    upgrade if this still isn't crisp enough). Scratch buffer here is a
    handful of bytes, nowhere near the two-BUF_SIZE-buffers MemoryError
    gotcha (see CLAUDE.md RAM notes)."""
    tw, th = 8 * len(s), 8
    scratch = bytearray(tw * th // 8 or 1)
    tfb = framebuf.FrameBuffer(scratch, tw, th, framebuf.MONO_HLSB)
    tfb.text(s, 0, 0, 1)
    for ty in range(th):
        for tx in range(tw):
            if tfb.pixel(tx, ty):
                _fill_rect(fb, lx0 + tx * scale, ly0 + ty * scale, scale, scale, color)


def _text_centered(fb, s, ly0, scale, color=1):
    """Like _text(), but horizontally centered within CONTENT_W. Only
    meaningful for unpadded strings (see departures.format_caption) --
    fixed-width padding would throw the centering math off."""
    tw = 8 * scale * len(s)
    lx0 = CONTENT_X0 + max(0, (CONTENT_W - tw) // 2)
    _text(fb, s, lx0, ly0, scale)


def stop_section(name, deps):
    """Pure: text content for one stop's section, no framebuf involved --
    a dict with the stop name, the hero departure's countdown + caption
    (or None if there are no departures), and the remaining departures as
    compact rows. "No departures" never gets the hero treatment -- there's
    nothing urgent to emphasize."""
    if not deps:
        return {"name": name, "hero": None, "caption": None, "rows": ["No departures"]}
    row_cols = CONTENT_W // (8 * ROW_SCALE)
    return {
        "name": name,
        "hero": deps[0]["display"],
        "caption": departures.format_caption(deps[0]),
        "rows": [departures.format_line(dep)[:row_cols] for dep in deps[1:]],
    }


def section_lines(section):
    """Flat list of every text string a section contains, in display
    order -- used for change-detection and serial logging."""
    lines = [section["name"]]
    if section["hero"] is not None:
        lines.append(section["hero"])
        lines.append(section["caption"])
    lines.extend(section["rows"])
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
    max_cols = CONTENT_W // (8 * FOOTER_SCALE)
    if len(text) <= max_cols:
        return [text]
    line2 = time_str + (" (stale)" if stale else "")
    return [date_str[:max_cols], line2[:max_cols]]


def draw_home(fb, sections, footer):
    """Draws each stop's section (from stop_section()) top-to-bottom, in
    order, with a generous rule + gap between sections ("separate the
    stops more" -- owner, 2026-07-09), then footer (from footer_lines())
    anchored near the bottom of the drawable area with its own smaller
    margin. Logs everything to serial (CLAUDE.md "make the code
    corroborate the screen")."""
    fb.fill(0)
    y = CONTENT_Y0
    logged = []

    for gi, section in enumerate(sections):
        if gi > 0:
            y += GROUP_GAP // 2
            _fill_rect(fb, CONTENT_X0, y, CONTENT_W, RULE_HEIGHT, 1)
            y += RULE_HEIGHT + GROUP_GAP // 2

        _text(fb, section["name"], CONTENT_X0, y, HEADER_SCALE)
        y += 8 * HEADER_SCALE + HEADER_RULE_GAP
        _fill_rect(fb, CONTENT_X0, y, CONTENT_W, RULE_HEIGHT, 1)
        y += RULE_HEIGHT + HEADER_TO_HERO_GAP
        logged.append(section["name"])

        if section["hero"] is not None:
            _text_centered(fb, section["hero"], y, HERO_SCALE)
            y += 8 * HERO_SCALE + HERO_TO_CAPTION_GAP
            _text_centered(fb, section["caption"], y, CAPTION_SCALE)
            y += 8 * CAPTION_SCALE + CAPTION_TO_ROWS_GAP
            logged.append(section["hero"])
            logged.append(section["caption"])

        for line in section["rows"]:
            _text(fb, line, CONTENT_X0, y, ROW_SCALE)
            y += 8 * ROW_SCALE + ROW_GAP
            logged.append(line)

    footer_h = len(footer) * (8 * FOOTER_SCALE) + max(0, len(footer) - 1) * FOOTER_ROW_GAP
    fy = DRAW_Y0 + DRAW_H - FOOTER_MARGIN - footer_h
    for line in footer:
        _text_centered(fb, line, fy, FOOTER_SCALE)
        fy += 8 * FOOTER_SCALE + FOOTER_ROW_GAP

    print("display: home screen -- " + " | ".join(logged) + " || " + " | ".join(footer))
