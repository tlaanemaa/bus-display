import struct

import pytest

import bitfont


def make_oversized_font(tmp_path):
    header = struct.pack("<4sBBH", b"BFN1", 255, 200, 1)
    index = struct.pack("<HBBI", ord("A"), 40, 40, len(header) + 8)
    path = tmp_path / "oversized.fnt"
    path.write_bytes(header + index)
    return path


def test_draw_rejects_glyph_larger_than_shared_buffer(tmp_path):
    font_path = make_oversized_font(tmp_path)
    font = bitfont.Font(str(font_path))
    with pytest.raises(ValueError, match="glyph bitmap exceeds scratch buffer"):
        font.draw("A", 0, 0, 1, None, lambda *_args: None)
