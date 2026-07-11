# Source fonts (host build inputs — not deployed to the device)

`Bitter-var.ttf` is the [Bitter](https://github.com/google/fonts/tree/main/ofl/bitter)
variable font (a slab serif designed for e-reader/screen reading), fetched from
Google Fonts:

    https://github.com/google/fonts/raw/main/ofl/bitter/Bitter%5Bwght%5D.ttf

Licensed under the SIL Open Font License 1.1 (OFL) — see the `ofl/bitter/OFL.txt`
in that repo. Redistributable; keep this attribution.

## Regenerating the deployed bitmap fonts

The device streams `src/fonts/*.fnt` (see `src/bitfont.py`). Regenerate them from
this TTF with `tools/gen_font.py`:

    python tools/gen_font.py tools/fonts/Bitter-var.ttf src/fonts/bitter_hero.fnt --size 118 --weight 800 --charset "0123456789:Nu "
    python tools/gen_font.py tools/fonts/Bitter-var.ttf src/fonts/bitter_head.fnt --size 31  --weight 700
    python tools/gen_font.py tools/fonts/Bitter-var.ttf src/fonts/bitter_row.fnt  --size 23  --weight 500

The head/row fonts use `gen_font.DEFAULT_CHARSET` (printable ASCII **+ `°`**, needed for the
weather footer's temperatures). The hero font passes an explicit digits-only charset.

Then just run `deploy.bat` — it recompiles every module (including `bitfont.mpy`) with
`mpy-cross` and copies the new `.fnt` files to the device.
