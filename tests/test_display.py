"""Host-side tests for display.py. A tiny fake buffer stands in for the
physical framebuf.FrameBuffer main.py would pass in (display.py only
calls fb.fill()/fill_rect()/pixel() on it); conftest.py separately fakes
the `framebuf` module itself, since display.py's scaled built-in font
needs one for its own internal scratch buffer (CLAUDE.md "Testability
rule")."""
import departures
import display


class FakeFB:
    """Records every set pixel; that's all these tests need to check
    (a) nothing draws outside the physical buffer and (b) something
    was actually drawn."""

    def __init__(self, w, h):
        self.width = w
        self.height = h
        self.set_pixels = set()

    def fill(self, color):
        self.set_pixels.clear()

    def fill_rect(self, x, y, w, h, color):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                assert 0 <= xx < self.width and 0 <= yy < self.height, "drew outside physical buffer"
                if color:
                    self.set_pixels.add((xx, yy))
                else:
                    self.set_pixels.discard((xx, yy))

    def pixel(self, x, y, color=None):
        assert 0 <= x < self.width and 0 <= y < self.height, "drew outside physical buffer"
        if color is None:
            return 1 if (x, y) in self.set_pixels else 0
        if color:
            self.set_pixels.add((x, y))
        else:
            self.set_pixels.discard((x, y))


def _deps(*rows):
    """rows: (line, destination, display) tuples, already in `expected` order."""
    raw = {"departures": [
        {"line": {"designation": line}, "destination": dest, "display": disp,
         "expected": "2026-07-10T%02d:00:00" % i}
        for i, (line, dest, disp) in enumerate(rows)
    ]}
    return departures.parse_departures(raw)


def test_stop_section_splits_hero_and_truncates_destination():
    deps = _deps(("474", "Slussen", "4 min"), ("440", "Slussen", "12 min"), ("425", "Nacka", "19 min"))
    section = display.stop_section("Mölnvik", deps)
    assert section["hero_main"] == "4"
    assert section["hero_unit"] == "min"
    assert section["badge_line"] == "474"
    assert section["dest"] == "Slussen"
    assert section["rows"] == [("440", "Slussen", "12 min"), ("425", "Nacka", "19 min")]


def test_stop_section_no_departures_skips_hero():
    section = display.stop_section("Mölnvik", [])
    assert section["hero_main"] is None
    assert section["dest"] == "No departures"
    assert section["rows"] == []


def test_stop_section_truncates_long_destination_to_fit():
    deps = _deps(("430X", "Gustavsbergs centrum via some very long way", "Nu"))
    section = display.stop_section("Stop", deps)
    max_w = display.CONTENT_W - display._scaled_badge_w(
        "430X", display.HEADLINE_SCALE, display.BADGE_PAD_X_HEADLINE) - display.GAP_BADGE_DEST
    assert display._scaled_w(section["dest"], display.HEADLINE_SCALE) <= max_w
    assert len(section["dest"]) < len(deps[0]["destination"])


def test_footer_lines_single_row_when_it_fits():
    assert display.footer_lines("Fre 10 jul", "14:32") == ["Fre 10 jul 14:32"]


def test_footer_lines_marks_stale():
    lines = display.footer_lines("Fre 10 jul", "14:32", stale=True)
    assert "(stale)" in lines[-1]


def test_section_lines_flattens_for_change_detection():
    deps = _deps(("474", "Slussen", "4 min"), ("440", "Slussen", "12 min"))
    section = display.stop_section("Mölnvik", deps)
    lines = display.section_lines(section)
    assert lines[0] == "Mölnvik"
    assert "4 min" in lines
    assert any("474" in l and "Slussen" in l for l in lines)


def test_draw_home_stays_inside_physical_buffer_and_two_full_sections_fit():
    """Regression guard for the "content ran into the footer" scare --
    two stops x 3 departures each (the max the owner wants) must not
    overlap the footer or draw outside the panel."""
    deps1 = _deps(("474", "Slussen", "4 min"), ("440", "Slussen", "12 min"), ("425", "Nacka", "19 min"))
    deps2 = _deps(("471", "Slussen", "7 min"), ("474", "Slussen", "16 min"), ("469", "Ålstäket", "24 min"))
    sections = [display.stop_section("Mölnvik", deps1), display.stop_section("Grisslinge", deps2)]
    footer = display.footer_lines("Fre 10 jul", "14:32")

    fb = FakeFB(display.PHYS_W, display.PHYS_H)
    # FakeFB's asserts raise if anything draws outside the physical buffer;
    # the returned coords let us also check content stayed above the footer.
    content_bottom, footer_top = display.draw_home(fb, sections, footer)
    assert fb.set_pixels  # something was actually drawn
    assert content_bottom <= footer_top, "content ran into the footer"
