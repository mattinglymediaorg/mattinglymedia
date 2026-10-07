"""Tests for shiny_greninja.py.

    python -m unittest test_shiny_greninja

The BC1/BC3/BC7 tests cross-check against an independent decoder and the Yaz0
tests against oead when those are installed (pip install texture2ddecoder oead);
otherwise they are skipped.
"""

import colorsys
import os
import random
import struct
import tempfile
import unittest

import shiny_greninja as gn

try:
    import texture2ddecoder
except ImportError:
    texture2ddecoder = None
try:
    import oead
except ImportError:
    oead = None


def swap_rb(r, g, b):
    return b, g, r


# ---------------------------------------------------------------------------
# Fixture builders (layouts mirror what shiny_greninja.py reads)
# ---------------------------------------------------------------------------

def build_bntx(textures):
    """textures: [(name, fmt, width, height, data bytes)] -> BNTX bytes."""
    count = len(textures)
    info_array = 0x60
    pos = info_array + 8 * count
    names = []
    for name, *_ in textures:
        names.append(pos)
        enc = name.encode()
        pos += 2 + len(enc) + 1
        pos = (pos + 7) & ~7
    brtis = []
    for _ in textures:
        brtis.append(pos)
        pos += 0xA0
        brtis[-1] = (brtis[-1], pos)  # (BRTI, mip table)
        pos += 8
    pos = (pos + 0x1FF) & ~0x1FF
    data_block = pos
    pos += 0x10
    starts = []
    for *_, data in textures:
        pos = (pos + 0x1FF) & ~0x1FF
        starts.append(pos)
        pos += len(data)
    size = (pos + 0xF) & ~0xF

    buf = bytearray(size)
    buf[0:4] = b"BNTX"
    struct.pack_into("<I", buf, 8, 0x00040001)
    buf[0x0C:0x0E] = b"\xff\xfe"
    struct.pack_into("<I", buf, 0x1C, size)
    buf[0x20:0x24] = b"NX  "
    struct.pack_into("<IQQ", buf, 0x24, count, info_array, data_block)
    buf[data_block:data_block + 4] = b"BRTD"
    for k, (name, fmt, w, h, data) in enumerate(textures):
        brti, mip_table = brtis[k]
        struct.pack_into("<Q", buf, info_array + 8 * k, brti)
        enc = name.encode()
        struct.pack_into("<H", buf, names[k], len(enc))
        buf[names[k] + 2:names[k] + 2 + len(enc)] = enc
        buf[brti:brti + 4] = b"BRTI"
        struct.pack_into("<H", buf, brti + 0x16, 1)
        struct.pack_into("<I", buf, brti + 0x1C, fmt)
        struct.pack_into("<4I", buf, brti + 0x24, w, h, 1, 1)
        struct.pack_into("<I", buf, brti + 0x50, len(data))
        struct.pack_into("<I", buf, brti + 0x58, 0x05040302)
        struct.pack_into("<QQQ", buf, brti + 0x60, names[k], 0, mip_table)
        struct.pack_into("<Q", buf, mip_table, starts[k])
        buf[starts[k]:starts[k] + len(data)] = data
    return bytes(buf)


def build_sarc(files):
    """files: {name: bytes} -> little-endian SARC bytes (names sorted by hash)."""
    def name_hash(name):
        h = 0
        for ch in name.encode():
            h = (h * 0x65 + ch) & 0xFFFFFFFF
        return h

    items = sorted(files.items(), key=lambda kv: name_hash(kv[0]))
    names = bytearray()
    name_offsets = []
    for name, _ in items:
        name_offsets.append(len(names))
        names += name.encode() + b"\0"
        names += bytes(-len(names) % 4)
    header_len = 0x14 + 0x0C + 16 * len(items) + 8 + len(names)
    data_offset = (header_len + 0x1FF) & ~0x1FF
    data = bytearray()
    nodes = bytearray()
    for (name, blob), name_off in zip(items, name_offsets):
        data += bytes(-len(data) % 0x200)
        nodes += struct.pack("<IIII", name_hash(name), 0x01000000 | (name_off // 4),
                             len(data), len(data) + len(blob))
        data += blob
    total = data_offset + len(data)
    out = bytearray(b"SARC" + struct.pack("<HHIIHH", 0x14, 0xFEFF, total, data_offset, 0x100, 0))
    out += b"SFAT" + struct.pack("<HHI", 0x0C, len(items), 0x65) + nodes
    out += b"SFNT" + struct.pack("<HH", 8, 0) + names
    out += bytes(data_offset - len(out))
    return bytes(out + data)


def bc1_solid(r, g, b, blocks):
    c = ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | (b * 31 + 127) // 255
    shade = max(0, c - 0x0841)
    return struct.pack("<HHI", c, shade, 0x1B1B1B1B) * blocks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class Yaz0Tests(unittest.TestCase):
    samples = [
        b"",
        b"a",
        b"abcabcabcabcabcabc" * 50,
        bytes(70000),
        bytes(random.Random(1).getrandbits(8) for _ in range(50000)),
        b"".join(bytes([i % 7]) * (i % 300) for i in range(400)),
    ]

    def test_roundtrip(self):
        for data in self.samples:
            packed = gn.yaz0_compress(data, alignment=0x80)
            self.assertEqual(struct.unpack_from(">I", packed, 8)[0], 0x80)
            self.assertEqual(gn.yaz0_decompress(packed)[0], data)

    @unittest.skipUnless(oead, "oead not installed")
    def test_against_oead(self):
        for data in self.samples:
            self.assertEqual(bytes(oead.yaz0.decompress(gn.yaz0_compress(data))), data)
            if data:
                self.assertEqual(gn.yaz0_decompress(bytes(oead.yaz0.compress(data)))[0], data)


class SarcTests(unittest.TestCase):
    @unittest.skipUnless(oead, "oead not installed")
    def test_reads_oead_archive(self):
        writer = oead.SarcWriter(oead.Endianness.Little)
        writer.files["Driver/model.bfres"] = b"FRES" * 100
        writer.files["other.bin"] = b"xyz"
        _, blob = writer.write()
        blob = bytes(blob)
        found = {name: blob[s:e] for name, s, e in gn.sarc_files(blob)}
        self.assertEqual(found, {"Driver/model.bfres": b"FRES" * 100, "other.bin": b"xyz"})

    def test_reads_built_archive(self):
        blob = build_sarc({"a.bfres": b"123", "b/c.bntx": b"4567"})
        found = {name: blob[s:e] for name, s, e in gn.sarc_files(blob)}
        self.assertEqual(found, {"a.bfres": b"123", "b/c.bntx": b"4567"})


@unittest.skipUnless(texture2ddecoder, "texture2ddecoder not installed")
class BlockTests(unittest.TestCase):
    """Swapping red and blue is exactly representable, so the recoloured
    blocks must decode to exactly the R/B-swapped original pixels."""

    W = H = 64

    def assert_swapped(self, before, after):
        px_before = memoryview(before).cast("B")
        px_after = memoryview(after).cast("B")
        for i in range(0, len(px_before), 4):  # BGRA
            self.assertEqual(
                (px_after[i], px_after[i + 1], px_after[i + 2], px_after[i + 3]),
                (px_before[i + 2], px_before[i + 1], px_before[i], px_before[i + 3]),
                "pixel %d" % (i // 4))

    def random_blocks(self, size, seed):
        rng = random.Random(seed)
        count = (self.W // 4) * (self.H // 4)
        return bytearray(rng.getrandbits(8) for _ in range(size * count))

    def test_bc1_both_modes(self):
        data = self.random_blocks(8, 2)  # random blocks hit both c0>c1 and c0<=c1
        before = texture2ddecoder.decode_bc1(bytes(data), self.W, self.H)
        gn.recolour_bc1_colour_blocks(data, 0, len(data) - 7, 8, gn.lut565(swap_rb), False)
        self.assert_swapped(before, texture2ddecoder.decode_bc1(bytes(data), self.W, self.H))

    def test_bc3(self):
        # texture2ddecoder decodes BC3 colour blocks with c0 <= c1 the legacy
        # three-colour way, so only compare blocks where both rules agree.
        data = self.random_blocks(16, 3)
        for off in range(8, len(data), 16):
            c0, c1 = struct.unpack_from("<HH", data, off)
            if c0 <= c1:
                struct.pack_into("<HH", data, off, max(c0, c1) | 1, min(c0, c1) & ~1)
        before = texture2ddecoder.decode_bc3(bytes(data), self.W, self.H)
        gn.recolour_bc1_colour_blocks(data, 8, len(data) - 7, 16, gn.lut565(swap_rb), True)
        self.assert_swapped(before, texture2ddecoder.decode_bc3(bytes(data), self.W, self.H))

    def test_bc3_always_four_colour(self):
        """BC2/BC3 colour blocks are four-colour whatever the endpoint order."""
        def decode(block):
            c0, c1, idx = struct.unpack("<HHI", block)
            ends = [((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)
                    for c in (c0, c1)]
            pal = ends + [tuple((2 * a + b) // 3 for a, b in zip(*ends)),
                          tuple((a + 2 * b) // 3 for a, b in zip(*ends))]
            return [pal[(idx >> (2 * i)) & 3] for i in range(16)]

        data = self.random_blocks(16, 7)
        before = [decode(data[off:off + 8]) for off in range(8, len(data), 16)]
        gn.recolour_bc1_colour_blocks(data, 8, len(data) - 7, 16, gn.lut565(swap_rb), True)
        after = [decode(data[off:off + 8]) for off in range(8, len(data), 16)]
        self.assertEqual(after, [[(b, g, r) for r, g, b in blk] for blk in before])

    def test_bc7_all_modes(self):
        rng = random.Random(4)
        blocks = []
        while len(blocks) < (self.W // 4) * (self.H // 4):
            block = rng.getrandbits(128)
            mode = (block & -block).bit_length() - 1 if block & 0xFF else 8
            if mode in (4, 5) and (block >> (mode + 1)) & 3 in (1, 3):
                continue  # R or B rotated into alpha: swap not exactly representable
            blocks.append(block.to_bytes(16, "little"))
        data = bytearray(b"".join(blocks))
        modes = {(b[0] & -b[0]).bit_length() - 1 for b in blocks if b[0]}
        self.assertEqual(modes, set(range(8)))
        before = texture2ddecoder.decode_bc7(bytes(data), self.W, self.H)
        gn.recolour_bc7(data, 0, len(data) - 15, swap_rb)
        self.assert_swapped(before, texture2ddecoder.decode_bc7(bytes(data), self.W, self.H))

    def test_bc7_identity_is_lossless(self):
        rng = random.Random(5)
        for _ in range(3000):
            block = rng.getrandbits(128)
            self.assertEqual(gn.recolour_bc7_block(block, lambda r, g, b: (r, g, b)), block)

    def test_bc7_rotated_blocks_turn_black(self):
        """Mode 4/5 blocks of blue shading, in every rotation, decode near-black."""
        recolour = gn.Recolour()
        rng = random.Random(6)
        for mode in (4, 5):
            cbits, abits = (5, 6) if mode == 4 else (7, 8)
            for rotation in range(4):
                for _ in range(50):
                    real = [(rng.randint(20, 50), rng.randint(50, 80), rng.randint(160, 220)),
                            (rng.randint(10, 30), rng.randint(20, 45), rng.randint(100, 130))]
                    slots = [list(c) + [255] for c in real]
                    if rotation:
                        for s in slots:
                            s[rotation - 1], s[3] = s[3], s[rotation - 1]
                    block = 1 << mode
                    pos = mode + 1
                    block |= rotation << pos
                    pos += 2 + (1 if mode == 4 else 0)
                    for ch in range(4):
                        bits = cbits if ch < 3 else abits
                        for s in slots:
                            block |= ((s[ch] * ((1 << bits) - 1) + 127) // 255) << pos
                            pos += bits
                    block |= rng.getrandbits(128 - pos) << pos
                    before = texture2ddecoder.decode_bc7(block.to_bytes(16, "little"), 4, 4)
                    out = gn.recolour_bc7_block(block, recolour).to_bytes(16, "little")
                    px = texture2ddecoder.decode_bc7(out, 4, 4)
                    for i in range(0, 64, 4):
                        self.assertGreater(before[i], max(before[i + 1], before[i + 2]))
                        self.assertLessEqual(max(px[i:i + 3]), 80, "mode %d rot %d" % (mode, rotation))


class RecolourTests(unittest.TestCase):
    def test_shiny_mode(self):
        rc = gn.Recolour()
        self.assertLessEqual(max(rc(30, 50, 120)), 45)  # navy body -> black
        r, g, b = rc(120, 170, 230)  # light blue -> dark grey
        self.assertLessEqual(max(r, g, b), 80)
        self.assertLessEqual(max(r, g, b) - min(r, g, b), 15)
        r, g, b = rc(240, 150, 190)  # pink tongue-scarf -> red
        self.assertGreater(r, 180)
        self.assertLess(max(g, b), 60)
        for kept in [(255, 255, 255), (40, 40, 40), (0, 0, 0), (230, 200, 40)]:
            self.assertEqual(rc(*kept), kept)  # white, grey, black, yellow

    def test_shift_mode(self):
        r, g, b = gn.Recolour(mode="shift", shift=120)(220, 30, 30)
        self.assertGreater(g, max(r, b))

    def test_strength_zero_is_identity(self):
        rc = gn.Recolour(strength=0)
        self.assertEqual(rc(200, 10, 90), (200, 10, 90))


class EndToEndTests(unittest.TestCase):
    def make_archive(self, folder):
        blue_bc1 = bc1_solid(40, 60, 160, 256)  # 64x64
        textures = [
            ("Body_Alb", 0x1A06, 64, 64, blue_bc1),
            ("Body_Nrm", 0x1A01, 64, 64, blue_bc1),
            ("Mask_Alb", 0x1D01, 64, 64, bytes(range(256)) * 8),
            ("Tongue_Alb", 0x0B06, 8, 8, bytes([240, 150, 190, 255]) * 64),
        ]
        bntx = build_bntx(textures)
        fres = b"FRES    " + bytes(0x1F8) + bntx + b"tail" * 16
        sarc = build_sarc({"Fake.bfres": fres, "readme.txt": b"BNTX appears in text"})
        path = os.path.join(folder, "dump", "romfs", "Driver", "Fake.szs")
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(gn.yaz0_compress(sarc, alignment=0x80))
        return path, sarc

    def test_make(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, original = self.make_archive(tmp)
            out_dir = os.path.join(tmp, "mod")
            self.assertEqual(gn.main(["make", src, "--out", out_dir]), 0)

            dest = os.path.join(out_dir, "atmosphere", "contents", gn.MK8D_TITLE_ID,
                                "romfs", "Driver", "Fake.szs")
            with open(dest, "rb") as f:
                packed = f.read()
            self.assertEqual(struct.unpack_from(">I", packed, 8)[0], 0x80)
            result, _ = gn.yaz0_decompress(packed)
            self.assertEqual(len(result), len(original))

            textures = {t.name: t for _, t in gn.scan(bytearray(result))}
            self.assertEqual(set(textures), {"Body_Alb", "Body_Nrm", "Mask_Alb", "Tongue_Alb"})
            changed = [i for i in range(len(original)) if original[i] != result[i]]
            body, tongue = textures["Body_Alb"], textures["Tongue_Alb"]
            self.assertTrue(changed)
            for i in changed:  # only the two colour textures may change
                self.assertTrue(body.start <= i < body.start + body.size
                                or tongue.start <= i < tongue.start + tongue.size, hex(i))

            for c in struct.unpack_from("<HH", result, body.start):  # body is now black
                self.assertLessEqual(max((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63,
                                         (c & 31) * 255 // 31), 60)
            r, g, b = result[tongue.start:tongue.start + 3]  # tongue is now red
            self.assertGreater(r, 2 * max(g, b))

    def test_list_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, _ = self.make_archive(tmp)
            self.assertEqual(gn.main(["list", src]), 0)

    def test_corrupt_bntx_is_rejected(self):
        bntx = bytearray(build_bntx([("Body_Alb", 0x1A06, 64, 64, bc1_solid(1, 2, 3, 256))]))
        brti = struct.unpack_from("<Q", bntx, 0x60)[0]
        struct.pack_into("<I", bntx, brti + 0x50, 0xFFFFFF)  # image size past end of file
        with self.assertRaises(ValueError):
            gn.scan(bytearray(bntx))


if __name__ == "__main__":
    unittest.main()
