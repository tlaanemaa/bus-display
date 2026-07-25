import struct
from pathlib import Path

import pytest

import bitfont


FONT_DIR = Path(__file__).parents[1] / "src" / "fonts"
DEPLOYED_FONTS = {
    "bitter_head.fnt": set(chr(code) for code in range(0x20, 0x7F)) | {"\N{DEGREE SIGN}"},
    "bitter_hero.fnt": set("0123456789:Nu "),
    "bitter_row.fnt": set(chr(code) for code in range(0x20, 0x7F)) | {"\N{DEGREE SIGN}"},
}


def _font_index(path: Path):
    blob = path.read_bytes()
    magic, height, _baseline, count = struct.unpack_from(bitfont._HDR, blob)
    index_start = bitfont._HDR_SIZE
    bitmap_start = index_start + count * bitfont._IDX_SIZE
    entries = []
    for position in range(count):
        entry_start = index_start + position * bitfont._IDX_SIZE
        entries.append(struct.unpack_from(bitfont._IDX, blob, entry_start))
    return blob, magic, height, bitmap_start, entries


def test_deployed_fonts_have_safe_complete_streaming_indexes():
    for filename, required_chars in DEPLOYED_FONTS.items():
        blob, magic, height, bitmap_start, entries = _font_index(FONT_DIR / filename)

        assert magic == bitfont._MAGIC
        codepoints = [code for code, _width, _advance, _offset in entries]
        assert codepoints == sorted(codepoints)
        assert len(codepoints) == len(set(codepoints))
        assert required_chars <= {chr(code) for code in codepoints}

        for _code, width, _advance, offset in entries:
            bitmap_size = ((width + 7) >> 3) * height
            assert bitmap_size <= len(bitfont._GBUF)
            assert bitmap_start <= offset
            assert offset + bitmap_size <= len(blob)


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
