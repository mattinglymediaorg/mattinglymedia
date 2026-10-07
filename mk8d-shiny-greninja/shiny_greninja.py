#!/usr/bin/env python3
"""
shiny_greninja.py - give a Mario Kart 8 Deluxe character mod the Shiny
Greninja colours (black body, red tongue-scarf) for an Atmosphere (LayeredFS)
modded Switch.

Greninja is not in the base game, so this is meant for a Greninja character
mod; any other character .szs works too and just gets the same palette.

The archive is
Yaz0-decompressed, every BNTX texture bank inside it is located (including the
ones embedded in .bfres models), the colour textures are recoloured in place,
and the result is re-compressed into a ready-to-copy LayeredFS folder:

    <out>/atmosphere/contents/0100152000022000/romfs/<same path as the original>

Nothing changes size or position inside the archive, so the model, skeleton,
animations and materials are untouched - only texture colours change.

Only the Python 3.8+ standard library is needed.

    python shiny_greninja.py list  romfs/Driver/SomeCharacter.szs
    python shiny_greninja.py make  romfs/Driver/SomeCharacter.szs [more.szs ...]
"""

import argparse
import colorsys
import os
import re
import struct
import sys
import time

MK8D_TITLE_ID = "0100152000022000"

# ---------------------------------------------------------------------------
# Yaz0
# ---------------------------------------------------------------------------


def yaz0_decompress(data):
    """Return (decompressed bytes, header alignment field)."""
    if data[:4] != b"Yaz0":
        raise ValueError("not a Yaz0 file")
    size, alignment = struct.unpack_from(">II", data, 4)
    out = bytearray()
    src = 16
    try:
        while len(out) < size:
            code = data[src]
            src += 1
            for bit in range(8):
                if len(out) >= size:
                    break
                if code & (0x80 >> bit):
                    out.append(data[src])
                    src += 1
                    continue
                b0, b1 = data[src], data[src + 1]
                src += 2
                dist = ((b0 & 0x0F) << 8 | b1) + 1
                length = b0 >> 4
                if length == 0:
                    length = data[src] + 0x12
                    src += 1
                else:
                    length += 2
                start = len(out) - dist
                if start < 0:
                    raise ValueError("corrupt Yaz0 data (bad back-reference)")
                # Overlapping copies repeat the last `dist` bytes.
                chunk = out[start:start + dist]
                out += (chunk * ((length + dist - 1) // dist))[:length]
    except IndexError:
        raise ValueError("corrupt Yaz0 data (truncated)") from None
    return bytes(out[:size]), alignment


def yaz0_compress(src, alignment=0):
    """Greedy longest-match Yaz0 compressor (4 KiB window, 273-byte matches)."""
    src = bytes(src)
    n = len(src)
    out = bytearray(b"Yaz0" + struct.pack(">II", n, alignment) + bytes(4))
    rfind = src.rfind
    pos = 0
    while pos < n:
        code_pos = len(out)
        out.append(0)
        code = 0
        for bit in range(8):
            if pos >= n:
                break
            window = pos - 0x1000 if pos > 0x1000 else 0
            max_len = min(n - pos, 0x111)
            best = 0
            if max_len >= 3:
                # A match may overlap the bytes being encoded, so the source
                # may start anywhere in [window, pos - 1].
                p = rfind(src[pos:pos + 3], window, pos + 2)
                if p >= 0:
                    lo, best_pos = 3, p
                    while True:  # gallop, then binary-search the longest match
                        trial = min(lo * 2, max_len)
                        if trial == lo:
                            hi = lo + 1
                            break
                        q = rfind(src[pos:pos + trial], window, pos - 1 + trial)
                        if q < 0:
                            hi = trial
                            break
                        lo, best_pos = trial, q
                    while hi - lo > 1:
                        mid = (lo + hi) // 2
                        q = rfind(src[pos:pos + mid], window, pos - 1 + mid)
                        if q >= 0:
                            lo, best_pos = mid, q
                        else:
                            hi = mid
                    best = lo
                    dist = pos - best_pos - 1
            if best >= 3:
                if best >= 0x12:
                    out += bytes((dist >> 8, dist & 0xFF, best - 0x12))
                else:
                    out += bytes((((best - 2) << 4) | (dist >> 8), dist & 0xFF))
                pos += best
            else:
                code |= 0x80 >> bit
                out.append(src[pos])
                pos += 1
        out[code_pos] = code
    return bytes(out)


# ---------------------------------------------------------------------------
# SARC
# ---------------------------------------------------------------------------


def sarc_files(buf):
    """Return [(name, start, end)] with absolute offsets, or None if not SARC."""
    if buf[:4] != b"SARC":
        return None
    e = "<" if buf[6:8] == b"\xff\xfe" else ">"
    (header_size,) = struct.unpack_from(e + "H", buf, 4)
    (data_offset,) = struct.unpack_from(e + "I", buf, 12)
    sfat = header_size
    if buf[sfat:sfat + 4] != b"SFAT":
        raise ValueError("SARC: missing SFAT section")
    sfat_header, count = struct.unpack_from(e + "HH", buf, sfat + 4)
    nodes = sfat + sfat_header
    sfnt = nodes + 16 * count
    if buf[sfnt:sfnt + 4] != b"SFNT":
        raise ValueError("SARC: missing SFNT section")
    (sfnt_header,) = struct.unpack_from(e + "H", buf, sfnt + 4)
    names = sfnt + sfnt_header
    files = []
    for i in range(count):
        name_hash, attr, start, end = struct.unpack_from(e + "IIII", buf, nodes + 16 * i)
        if attr >> 24:
            name_pos = names + (attr & 0xFFFFFF) * 4
            name = bytes(buf[name_pos:buf.index(b"\0", name_pos)]).decode("utf-8", "replace")
        else:
            name = "<hash %08X>" % name_hash
        files.append((name, data_offset + start, data_offset + end))
    return files


# ---------------------------------------------------------------------------
# BNTX
# ---------------------------------------------------------------------------

# Format type (high byte of the BNTX format code): (name, block size, bytes per block)
FORMATS = {
    0x0B: ("RGBA8", 1, 4),
    0x0C: ("BGRA8", 1, 4),
    0x1A: ("BC1", 4, 8),
    0x1B: ("BC2", 4, 16),
    0x1C: ("BC3", 4, 16),
    0x1D: ("BC4", 4, 8),
    0x1E: ("BC5", 4, 16),
    0x1F: ("BC6H", 4, 16),
    0x20: ("BC7", 4, 16),
}
RECOLOURABLE = {"RGBA8", "BGRA8", "BC1", "BC2", "BC3", "BC7"}
COLOUR_VARIANTS = {0x01, 0x06}  # UNORM, SRGB

# Name fragments that mark normal / specular / mask style maps.
NON_COLOUR_HINTS = {
    "nrm", "nml", "norm", "normal", "spm", "spc", "spec", "specular", "msk",
    "mask", "ao", "occ", "bake", "rgh", "rough", "mtl", "metal", "hgt",
    "height", "dpt", "depth", "trm", "lmp", "lightmap", "shadow", "shd",
}


class Texture:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    @property
    def kind(self):
        info = FORMATS.get(self.fmt >> 8)
        if info:
            return info[0]
        if 0x2D <= self.fmt >> 8 <= 0x3A:
            return "ASTC"
        return "0x%04X" % self.fmt

    @property
    def is_colour_mapping(self):
        """False when the channel swizzle says this is a greyscale/mask map."""
        sel = [(self.comp_sel >> (8 * i)) & 0xFF for i in range(4)]
        if self.comp_sel == 0 or any(s > 5 for s in sel):
            return True  # unset or unrecognised: assume ordinary colour
        return sorted(sel[:3]) == [2, 3, 4]


def _u16(buf, off):
    return struct.unpack_from("<H", buf, off)[0]


def _u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def _u64(buf, off):
    return struct.unpack_from("<Q", buf, off)[0]


def find_bntx(buf, start, end):
    """Yield the offsets of plausible BNTX files inside buf[start:end]."""
    i = buf.find(b"BNTX", start, end)
    while i != -1:
        size = 0
        if (i + 0x40 <= end and buf[i + 0x20:i + 0x24] == b"NX  "
                and buf[i + 0x0C:i + 0x0E] == b"\xff\xfe"):
            size = _u32(buf, i + 0x1C)
            if not 0x40 <= size <= end - i:
                size = 0
        if size:
            yield i, size
        i = buf.find(b"BNTX", i + max(size, 4), end)


def parse_bntx(buf, base, size):
    """Return the textures of the BNTX at `base`, validating every offset."""
    end = base + size

    def check(cond, what):
        if not cond:
            raise ValueError("BNTX at 0x%X: %s" % (base, what))

    count = _u32(buf, base + 0x24)
    info_array = base + _u64(buf, base + 0x28)
    check(0 < count < 10000, "implausible texture count %d" % count)
    check(info_array + 8 * count <= end, "texture table out of range")
    textures = []
    for k in range(count):
        brti = base + _u64(buf, info_array + 8 * k)
        check(brti + 0xA0 <= end and buf[brti:brti + 4] == b"BRTI",
              "texture %d has no BRTI header" % k)
        mip_count = _u16(buf, brti + 0x16)
        fmt = _u32(buf, brti + 0x1C)
        width, height, depth, layers = struct.unpack_from("<4I", buf, brti + 0x24)
        image_size = _u32(buf, brti + 0x50)
        comp_sel = _u32(buf, brti + 0x58)
        name_pos = base + _u64(buf, brti + 0x60)
        mip_table = base + _u64(buf, brti + 0x70)
        check(0 < mip_count <= 16 and mip_table + 8 * mip_count <= end,
              "texture %d has a bad mip table" % k)
        mips = [base + _u64(buf, mip_table + 8 * m) for m in range(mip_count)]
        data_start = mips[0]
        check(image_size > 0 and data_start + image_size <= end,
              "texture %d data out of range" % k)
        check(all(data_start <= m < data_start + image_size for m in mips),
              "texture %d mip levels outside its data" % k)

        name = "texture_%d" % k
        if name_pos + 2 <= end:
            length = _u16(buf, name_pos)
            if 0 < length < 256 and name_pos + 2 + length <= end:
                name = bytes(buf[name_pos + 2:name_pos + 2 + length]).decode("utf-8", "replace")

        tex = Texture(name=name, fmt=fmt, width=width, height=height,
                      depth=max(depth, 1), layers=max(layers, 1), mip_count=mip_count,
                      comp_sel=comp_sel, start=data_start, size=image_size)
        info = FORMATS.get(fmt >> 8)
        if info:
            _, block, block_bytes = info
            min_bytes = (-(-width // block)) * (-(-height // block)) * block_bytes
            check(image_size >= min_bytes and data_start % block_bytes == base % block_bytes,
                  "texture %r (%s %dx%d) is smaller than its format needs"
                  % (name, info[0], width, height))
        textures.append(tex)
    return textures


def load(path):
    """Read an .szs (or uncompressed SARC/BFRES/BNTX) file."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:4] == b"Yaz0":
        data, alignment = yaz0_decompress(raw)
        return bytearray(data), True, alignment
    return bytearray(raw), False, 0


def scan(buf):
    """Return every texture in the archive as (container name, Texture)."""
    regions = sarc_files(buf) or [("(file)", 0, len(buf))]
    found = []
    for container, start, end in regions:
        for base, size in find_bntx(buf, start, end):
            for tex in parse_bntx(buf, base, size):
                found.append((container, tex))
    return found


# ---------------------------------------------------------------------------
# Colour mapping
# ---------------------------------------------------------------------------


def _smoothstep(lo, hi, x):
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    t = (x - lo) / (hi - lo)
    return t * t * (3 - 2 * t)


def _band(deg, lo, hi, feather):
    """1 inside the hue range [lo, hi] degrees, fading to 0 over `feather`."""
    return max(_smoothstep(lo - feather, lo, d) * (1.0 - _smoothstep(hi, hi + feather, d))
               for d in (deg, deg + 360.0))


class Recolour:
    """Maps an 8-bit (r, g, b) colour to its shiny version.

    shiny - the Shiny Greninja palette (default): blues turn black, keeping
            their relative shading, and pinks turn red; white, yellow, grey
            and every other colour stay as they are
    shift - rotate every hue by --shift degrees
    """

    BLUE = (180, 260)  # hue ranges in degrees
    PINK = (295, 350)
    RED_HUE = 356 / 360.0

    def __init__(self, mode="shiny", shift=120, min_sat=0.2, darken=0.3, strength=1.0):
        self.mode = mode
        self.shift = (shift % 360) / 360.0
        self.min_sat = min_sat
        self.darken = max(0.0, min(1.0, darken))
        self.strength = max(0.0, min(1.0, strength))
        self._cache = {}
        self._lut565 = None

    def __call__(self, r, g, b):
        key = (r << 16) | (g << 8) | b
        hit = self._cache.get(key)
        if hit is None:
            hit = self._cache[key] = self._compute(r, g, b)
        return hit

    def _compute(self, r, g, b):
        rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
        h, s, v = colorsys.rgb_to_hsv(rf, gf, bf)
        if self.mode == "shift":
            weight = 1.0
            nr, ng, nb = colorsys.hsv_to_rgb((h + self.shift) % 1.0, s, v)
        else:  # shiny
            colourful = _smoothstep(self.min_sat * 0.5, self.min_sat, s) * _smoothstep(0.02, 0.06, v)
            blue = _band(h * 360, self.BLUE[0], self.BLUE[1], 15)
            pink = _band(h * 360, self.PINK[0], self.PINK[1], 15)
            if blue >= pink:
                weight = blue * colourful
                nr, ng, nb = colorsys.hsv_to_rgb(h, s * 0.15, v * self.darken)
            else:
                weight = pink * colourful
                nr, ng, nb = colorsys.hsv_to_rgb(self.RED_HUE, max(s, 0.85), v * 0.85)
        weight *= self.strength
        if weight <= 0.0:
            return r, g, b
        mix = lambda o, n: max(0, min(255, int(round((o + (n - o) * weight) * 255))))
        return mix(rf, nr), mix(gf, ng), mix(bf, nb)

    def table565(self):
        if self._lut565 is None:
            self._lut565 = lut565(self)
        return self._lut565


def lut565(colour_fn):
    """Lookup table of colour_fn applied to every RGB565 value."""
    table = [0] * 65536
    for c in range(65536):
        r5, g6, b5 = c >> 11, (c >> 5) & 0x3F, c & 0x1F
        r, g, b = colour_fn((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2))
        table[c] = ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | (b * 31 + 127) // 255
    return table


# ---------------------------------------------------------------------------
# Texture block recolouring
#
# All of these walk the raw (block-linear swizzled) texture data as a flat run
# of blocks. Swizzling only reorders whole 16-byte units, and the same colour
# change is applied to every block, so no unswizzling or re-encoding is needed.
# ---------------------------------------------------------------------------


def recolour_bc1_colour_blocks(buf, start, end, stride, lut, always_four_colour):
    """Recolour BC1-style colour blocks (BC1, and the colour half of BC2/BC3)."""
    unpack, pack = struct.unpack_from, struct.pack_into
    changed = 0
    for off in range(start, end, stride):
        c0, c1, idx = unpack("<HHI", buf, off)
        n0, n1 = lut[c0], lut[c1]
        if n0 == c0 and n1 == c1:
            continue
        if c0 > c1:
            # Four-colour block: keep c0 > c1 so it stays four-colour.
            if n0 < n1:
                n0, n1 = n1, n0
                idx ^= 0x55555555  # 0<->1, 2<->3
            elif n0 == n1:
                idx = 0  # every palette entry is now the same colour
        elif n0 > n1 and not always_four_colour:
            # BC1 three-colour / punch-through block: keep c0 <= c1.
            n0, n1 = n1, n0
            idx ^= ~(idx >> 1) & 0x55555555  # 0<->1, 2 and 3 unchanged
        pack("<HHI", buf, off, n0, n1, idx)
        changed += 1
    return changed


def recolour_rgba8(buf, start, end, colour_fn, bgra):
    ri, bi = (2, 0) if bgra else (0, 2)
    changed = 0
    for off in range(start, end, 4):
        r, g, b = buf[off + ri], buf[off + 1], buf[off + bi]
        nr, ng, nb = colour_fn(r, g, b)
        if (nr, ng, nb) != (r, g, b):
            buf[off + ri], buf[off + 1], buf[off + bi] = nr, ng, nb
            changed += 1
    return changed


# BC7 modes: subsets, partition bits, rotation bits, index-selection bits,
# colour bits, alpha bits, p-bits ("u" one per endpoint, "s" one per subset)
BC7_MODES = (
    (3, 4, 0, 0, 4, 0, "u"),
    (2, 6, 0, 0, 6, 0, "s"),
    (3, 6, 0, 0, 5, 0, None),
    (2, 6, 0, 0, 7, 0, "u"),
    (1, 0, 2, 1, 5, 6, None),
    (1, 0, 2, 0, 7, 8, None),
    (1, 0, 0, 0, 7, 7, "u"),
    (2, 6, 0, 0, 5, 5, "u"),
)
_quant_tables = {}


def _expand(v, bits):
    return (v << (8 - bits)) | (v >> (2 * bits - 8))


def _quant(bits, pbit):
    """For each 8-bit target: (best raw value, squared error), p-bit fixed."""
    key = (bits, pbit)
    table = _quant_tables.get(key)
    if table is None:
        if pbit is None:
            values = [_expand(raw, bits) for raw in range(1 << bits)]
        else:
            values = [_expand(raw << 1 | pbit, bits + 1) for raw in range(1 << bits)]
        table = []
        for target in range(256):
            raw = min(range(len(values)), key=lambda i: abs(values[i] - target))
            table.append((raw, (values[raw] - target) ** 2))
        _quant_tables[key] = table
    return table


def recolour_bc7_block(block, colour_fn):
    """Recolour one BC7 block (as a 128-bit int); returns the new int.

    Endpoints are decoded, recoloured and re-quantised in place, p-bits are
    re-chosen, and the indices are left alone.
    """
    low = block & 0xFF
    if not low:
        return block  # reserved mode
    mode = (low & -low).bit_length() - 1
    subsets, part_bits, rot_bits, sel_bits, cbits, abits, ptype = BC7_MODES[mode]
    pos = mode + 1 + part_bits
    rotation = (block >> pos) & 3 if rot_bits else 0
    pos += rot_bits + sel_bits
    ep_start = pos
    n = subsets * 2

    def take(width):
        nonlocal pos
        value = (block >> pos) & ((1 << width) - 1)
        pos += width
        return value

    colour = [[take(cbits) for _ in range(n)] for _ in range(3)]
    alpha = [take(abits) for _ in range(n)] if abits else None
    if ptype == "u":
        pbits = [take(1) for _ in range(n)]
    elif ptype == "s":
        shared = [take(1) for _ in range(subsets)]
        pbits = [shared[e // 2] for e in range(n)]
    else:
        pbits = None
    ep_end = pos

    def decode(raw, bits, e):
        if pbits is None:
            return _expand(raw, bits)
        return _expand(raw << 1 | pbits[e], bits + 1)

    # 8-bit targets for every endpoint: [r, g, b] and alpha
    targets, changed = [], False
    for e in range(n):
        rgb = [decode(colour[c][e], cbits, e) for c in range(3)]
        a = decode(alpha[e], abits, e) if abits else None
        if rotation:  # alpha is stored in a colour slot and vice versa
            rgb[rotation - 1], a = a, rgb[rotation - 1]
        new = list(colour_fn(*rgb))
        changed |= new != rgb
        if rotation:
            new[rotation - 1], a = a, new[rotation - 1]
        targets.append((new, a))
    if not changed:
        return block

    def error(e, p):
        new, a = targets[e]
        err = sum(_quant(cbits, p)[t][1] for t in new)
        if abits:
            err += _quant(abits, p)[a][1]
        return err

    if ptype == "u":
        new_p = [min((0, 1), key=lambda p: (error(e, p), p != pbits[e])) for e in range(n)]
    elif ptype == "s":
        new_shared = [min((0, 1), key=lambda p: (error(2 * s, p) + error(2 * s + 1, p), p != shared[s]))
                      for s in range(subsets)]
        new_p = [new_shared[e // 2] for e in range(n)]
    else:
        new_p = [None] * n

    fields, shift = 0, 0
    for c in range(3):
        for e in range(n):
            fields |= _quant(cbits, new_p[e])[targets[e][0][c]][0] << shift
            shift += cbits
    if abits:
        for e in range(n):
            fields |= _quant(abits, new_p[e])[targets[e][1]][0] << shift
            shift += abits
    if ptype == "u":
        for e in range(n):
            fields |= new_p[e] << shift
            shift += 1
    elif ptype == "s":
        for s in range(subsets):
            fields |= new_shared[s] << shift
            shift += 1
    assert shift == ep_end - ep_start
    mask = ((1 << shift) - 1) << ep_start
    return (block & ~mask) | (fields << ep_start)


def recolour_bc7(buf, start, end, colour_fn):
    cache, changed = {}, 0
    for off in range(start, end, 16):
        raw = bytes(buf[off:off + 16])
        new = cache.get(raw)
        if new is None:
            if len(cache) > 200000:
                cache.clear()
            new = cache[raw] = recolour_bc7_block(
                int.from_bytes(raw, "little"), colour_fn).to_bytes(16, "little")
        if new != raw:
            buf[off:off + 16] = new
            changed += 1
    return changed


def recolour_texture(buf, tex, recolour):
    """Recolour one texture in place; returns the number of blocks changed."""
    kind = tex.kind
    start, end = tex.start, tex.start + tex.size
    if kind == "BC1":
        return recolour_bc1_colour_blocks(buf, start, end - 7, 8, recolour.table565(), False)
    if kind in ("BC2", "BC3"):
        return recolour_bc1_colour_blocks(buf, start + 8, end - 7, 16, recolour.table565(), True)
    if kind == "BC7":
        return recolour_bc7(buf, start, end - 15, recolour)
    if kind in ("RGBA8", "BGRA8"):
        return recolour_rgba8(buf, start, end - 3, recolour, kind == "BGRA8")
    raise ValueError("cannot recolour %s textures" % kind)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _patterns(values):
    out = []
    for v in values or ():
        out += [p.strip().lower() for p in v.split(",") if p.strip()]
    return out


def decide(tex, args):
    """Return (recolour?, reason)."""
    kind = tex.kind
    name = tex.name.lower()
    only, skip = _patterns(args.only), _patterns(args.skip)
    if kind not in RECOLOURABLE:
        return False, "%s not supported" % kind
    if (tex.fmt & 0xFF) not in COLOUR_VARIANTS:
        return False, "not a colour format"
    if only:
        hit = any(p in name for p in only)
        return hit, "matches --only" if hit else "not in --only"
    if any(p in name for p in skip):
        return False, "matches --skip"
    if args.all:
        return True, "--all"
    if not tex.is_colour_mapping:
        return False, "greyscale/mask channel layout"
    tokens = {t.rstrip("0123456789") for t in re.split(r"[^a-z0-9]+", name)}
    if tokens & NON_COLOUR_HINTS:
        return False, "name looks like a normal/spec/mask map"
    return True, "colour texture"


def romfs_relative(path):
    """Path of `path` below its romfs/ folder, or None if it isn't in one."""
    parts = os.path.normpath(os.path.abspath(path)).split(os.sep)
    lower = [p.lower() for p in parts]
    if "romfs" in lower[:-1]:
        i = len(lower) - 1 - lower[::-1].index("romfs")
        return "/".join(parts[i + 1:])
    return None


def cmd_list(args):
    for path in args.files:
        buf, compressed, _ = load(path)
        textures = scan(buf)
        print("%s  (%s, %d textures)" % (path, "Yaz0" if compressed else "uncompressed", len(textures)))
        for container, tex in textures:
            ok, why = decide(tex, args)
            print("  %-4s %-40s %-6s %5dx%-5d mips=%-2d  %s  [%s]" % (
                "yes" if ok else "-", tex.name, tex.kind, tex.width, tex.height,
                tex.mip_count, container, why))
        print()
    return 0


def cmd_make(args):
    recolour = Recolour(args.mode, args.shift, args.min_sat, args.darken, args.strength)
    romfs_out = os.path.join(args.out, "atmosphere", "contents", MK8D_TITLE_ID, "romfs")
    written = []
    for path in args.files:
        t0 = time.time()
        print("== %s" % path)
        buf, compressed, alignment = load(path)
        textures = scan(buf)
        if not textures:
            print("   no BNTX textures found - nothing to recolour, skipping")
            continue
        done = 0
        for container, tex in textures:
            ok, why = decide(tex, args)
            if not ok:
                print("   skip  %-40s %-6s (%s)" % (tex.name, tex.kind, why))
                continue
            blocks = recolour_texture(buf, tex, recolour)
            done += 1
            print("   shiny %-40s %-6s %d blocks changed" % (tex.name, tex.kind, blocks))
        if not done:
            print("   no textures were recoloured, skipping (see `list`, --all, --only)")
            continue

        rel = args.romfs_path if args.romfs_path and len(args.files) == 1 else romfs_relative(path)
        if not rel:
            rel = "Driver/" + os.path.basename(path)
            print("   note: %s is not inside a romfs/ folder; assuming romfs/%s" % (path, rel))
        dest = os.path.join(romfs_out, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        out = bytes(buf)
        if compressed:
            print("   compressing...")
            out = yaz0_compress(out, alignment)
            if yaz0_decompress(out)[0] != bytes(buf):
                raise RuntimeError("Yaz0 self-check failed; output not written")
        with open(dest, "wb") as f:
            f.write(out)
        written.append(dest)
        print("   wrote %s (%.1fs)" % (dest, time.time() - t0))

    if not written:
        print("\nNothing written.")
        return 1
    print("\nDone. Copy the `atmosphere` folder inside %s to the root of your SD card."
          % os.path.abspath(args.out))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Give Mario Kart 8 Deluxe character textures the Shiny Greninja colours "
                    "(LayeredFS mod).")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_filters(p):
        p.add_argument("files", nargs="+", help=".szs files from your romfs dump")
        p.add_argument("--only", action="append", metavar="TEXT",
                       help="recolour only textures whose name contains TEXT (comma list ok)")
        p.add_argument("--skip", action="append", metavar="TEXT",
                       help="never recolour textures whose name contains TEXT (e.g. face,eye)")
        p.add_argument("--all", action="store_true",
                       help="recolour every supported texture, even ones that look like masks")

    p_list = sub.add_parser("list", help="show the textures in an .szs and what would be recoloured")
    add_filters(p_list)
    p_list.set_defaults(func=cmd_list)

    p_make = sub.add_parser("make", help="build the shiny mod")
    add_filters(p_make)
    p_make.add_argument("--out", default="ShinyGreninjaMod",
                        help="output folder (default: ShinyGreninjaMod)")
    p_make.add_argument("--mode", choices=("shiny", "shift"), default="shiny",
                        help="shiny: blues turn black and pinks turn red, like Shiny Greninja "
                             "(default); shift: rotate every hue by --shift degrees")
    p_make.add_argument("--darken", type=float, default=0.3,
                        help="shiny mode: brightness kept by the blues that turn black "
                             "(default 0.3; lower is blacker)")
    p_make.add_argument("--shift", type=float, default=120, help="degrees to rotate hues in shift mode")
    p_make.add_argument("--min-sat", type=float, default=0.2,
                        help="shiny mode: colours less saturated than this are kept (default 0.2)")
    p_make.add_argument("--strength", type=float, default=1.0,
                        help="blend between original (0) and full effect (1)")
    p_make.add_argument("--romfs-path", metavar="PATH",
                        help="romfs-relative destination, e.g. Driver/Foo.szs (single input only)")
    p_make.set_defaults(func=cmd_make)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
