# Shiny Greninja kit — Mario Kart 8 Deluxe (Switch, v3.0.3)

Greninja isn't in Mario Kart 8 Deluxe, so a Greninja `.szs` has to start from a
**Greninja character mod** (a community-made model that replaces one of the
racers). `shiny_greninja.py` turns that mod into **Shiny Greninja**:

- blue body → black (shading kept, so the details still show)
- pink tongue-scarf → red
- white, yellow, grey and everything else unchanged

Only texture colours change. The model, skeleton and animations are untouched,
so the file loads exactly like the mod it came from. Run on any other character
`.szs`, it just applies the same black-and-red palette.

## Make the shiny .szs

Python 3.8+ is all you need.

```
python shiny_greninja.py list path/to/Greninja-mod/romfs/Driver/SomeFile.szs
python shiny_greninja.py make path/to/Greninja-mod/romfs/Driver/SomeFile.szs
```

`list` shows the textures and marks with `yes` the ones that will be
recoloured. Normal maps and masks are skipped automatically. `make` writes
`ShinyGreninjaMod/atmosphere/contents/0100152000022000/romfs/…`, keeping the
file's path from its `romfs/` folder. Pass every `.szs` the mod includes
(e.g. low-detail or menu variants) so Greninja is shiny everywhere.

## Install

Copy the `atmosphere` folder from `ShinyGreninjaMod` to the root of your SD card
and start the game. To remove it, delete the files you added under
`atmosphere/contents/0100152000022000/romfs/`.

Play modded games offline. Nintendo bans consoles running custom firmware
online.

## Options

| Option | Effect |
| --- | --- |
| `--darken 0.3` | How much brightness the black keeps: `0.15` is jet black, `0.5` is charcoal. |
| `--min-sat 0.2` | Colours less saturated than this are left alone. |
| `--strength 1.0` | Blend between original (0) and full effect (1). |
| `--skip eye,face` | Never touch textures whose names contain these words. |
| `--only body` | Recolour only matching textures. |
| `--all` | Also recolour textures that look like masks. |
| `--mode shift --shift 120` | Rotate every hue instead of the shiny palette. |
| `--out DIR` | Output folder (default `ShinyGreninjaMod`). |

BC1/BC2/BC3/BC7 and RGBA8 textures are recoloured directly in their compressed,
swizzled form. BC4/BC5, BC6H and ASTC are left as they are.

## Tests

```
python -m unittest test_shiny_greninja
```

With `pip install texture2ddecoder oead`, the tests also cross-check the texture
and Yaz0 code against those independent decoders.
