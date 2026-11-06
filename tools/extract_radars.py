#!/usr/bin/env python3
"""
Extract the radar images and map calibration for the pool maps from your local
CS2 install.

    pip install vpk pillow
    python tools/extract_radars.py

Writes radars/<map>.png, plus radars/<map>_lower.png for a map with a second
level, and updates viewer/map_calibration.json. Re-run after a CS2 update that
changes a map; a radar whose pixels have not changed is left alone, so git only
sees real changes.

Where the data comes from
-------------------------
resource/overviews/<map>.txt   pos_x / pos_y / scale, and the altitude ranges
                               that split a map into levels (verticalsections)
panorama/images/overheadmaps/  the radar textures: Source 2 .vtex_c, RGBA pixels
                               inside a raw LZ4 block after the resource header

LICENSING - READ THIS
---------------------
The radars are Valve's artwork, pulled from the copy of CS2 on this machine. Fine
for local use. Do not redistribute them beyond what the repo already ships.
"""

import json
import re
import struct
import sys
from io import BytesIO
from pathlib import Path

try:
    import vpk
    from PIL import Image
except ImportError:
    sys.exit("[!] pip install vpk pillow")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_icons import find_pak  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RADARS = ROOT / "radars"
CALIBRATION = ROOT / "viewer" / "map_calibration.json"

# Active Duty is `mg_active` in gamemodes.txt: inferno, train, mirage, nuke,
# dust2, overpass, vertigo. Ancient and Anubis were supported before this tool
# existed, so they stay in and nothing that already works breaks.
POOL = ["de_inferno", "de_train", "de_mirage", "de_nuke", "de_dust2",
        "de_overpass", "de_vertigo", "de_ancient", "de_anubis"]

# The byte after the texture's depth field. Every radar in the pool uses this
# one format: 1024x1024 RGBA stored as BGRA. Anything else is refused rather
# than decoded wrongly.
TEXTURE_FORMAT_BGRA = 28


def parse_kv(text):
    """Valve KeyValues -> nested dicts. Enough for overview files: no escapes."""
    text = re.sub(r"//[^\n]*", "", text)
    toks = re.findall(r'"[^"]*"|[{}]', text)
    pos = 0

    def block():
        nonlocal pos
        out = {}
        while pos < len(toks) and toks[pos] != "}":
            key = toks[pos].strip('"')
            pos += 1
            if toks[pos] == "{":
                pos += 1
                out[key] = block()
                pos += 1                        # the closing brace
            else:
                out[key] = toks[pos].strip('"')
                pos += 1
        return out

    return block()


def lz4_block(src):
    """A raw LZ4 block (no frame header) -> bytes."""
    dst = bytearray()
    i = 0
    while i < len(src):
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]
        i += lit
        if i >= len(src):
            break                               # the last sequence is literals only
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            raise ValueError("not a raw LZ4 block")
        mlen = token & 15
        if mlen == 15:
            while True:
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        # an overlapping match repeats the last `offset` bytes; copy them in one slice
        seg = dst[-offset:][:min(offset, mlen)]
        dst += (seg * (mlen // len(seg) + 1))[:mlen]
    return bytes(dst)


def read_vtex(raw):
    """A Source 2 .vtex_c radar -> an RGBA PIL image."""
    file_size, _, _, boff, bcount = struct.unpack_from("<IHHII", raw, 0)
    blocks = {}
    for i in range(bcount):
        p = 8 + boff + i * 12                   # entry; its offset field sits 4 bytes in
        blocks[raw[p:p + 4]] = p + 4 + struct.unpack_from("<I", raw, p + 4)[0]
    d = blocks[b"DATA"]
    w, h = struct.unpack_from("<HH", raw, d + 20)
    fmt = raw[d + 26]
    if fmt != TEXTURE_FORMAT_BGRA:
        raise ValueError(f"texture format {fmt} is not one this tool decodes")
    pixels = lz4_block(raw[file_size:])          # the payload follows the resource header
    if len(pixels) != w * h * 4:
        raise ValueError(f"decoded {len(pixels)} bytes, expected {w * h * 4}")
    b, g, r, a = Image.frombytes("RGBA", (w, h), pixels).split()   # bytes are B,G,R,A
    return Image.merge("RGBA", (r, g, b, a))


def number(text):
    v = float(text)
    return int(v) if v.is_integer() else v


def picture(im):
    """What a radar looks like: composited over black, so the colour hidden under
    fully transparent pixels (which nobody sees) does not count as a difference."""
    return Image.alpha_composite(Image.new("RGBA", im.size, (0, 0, 0, 255)), im).tobytes()


def save_if_changed(im, path):
    """Write the radar only if it would look different, so re-runs do not churn git."""
    if path.exists() and picture(Image.open(path).convert("RGBA")) == picture(im):
        return "kept"
    im.save(path)
    return "written"


def main():
    pak = vpk.open(str(find_pak()))
    calibration = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    RADARS.mkdir(exist_ok=True)

    for name in POOL:
        ov = parse_kv(pak.get_file(f"resource/overviews/{name}.txt")
                      .read().decode("utf-8", "replace"))[name]
        entry = calibration.setdefault(name, {})
        before = {k: entry.get(k) for k in ("pos_x", "pos_y", "scale")}
        entry.update(pos_x=number(ov["pos_x"]), pos_y=number(ov["pos_y"]),
                     scale=number(ov["scale"]))
        moved = {k: (before[k], entry[k]) for k in before if before[k] != entry[k]}

        levels = ov.get("verticalsections") or {}
        if levels:
            floors = []
            for level, sec in levels.items():
                tag = "" if level == "default" else f"_{level}"
                img = f"{name}{tag}.png"
                tex = pak.get_file(f"panorama/images/overheadmaps/{name}{tag}_radar_psd.vtex_c")
                state = save_if_changed(read_vtex(tex.read()), RADARS / img)
                floors.append({"name": "upper" if level == "default" else level,
                               "img": img,
                               "min": number(sec["AltitudeMin"]),
                               "max": number(sec["AltitudeMax"])})
                print(f"{name:12} {img:22} {state}")
            entry["floors"] = floors
        else:
            tex = pak.get_file(f"panorama/images/overheadmaps/{name}_radar_psd.vtex_c")
            state = save_if_changed(read_vtex(tex.read()), RADARS / f"{name}.png")
            entry.pop("floors", None)
            print(f"{name:12} {name + '.png':22} {state}")
        if moved:
            print(f"{'':12} calibration changed: {moved}")

    CALIBRATION.write_text(json.dumps(calibration, indent=1) + "\n", encoding="utf-8")
    print(f"[+] {CALIBRATION.relative_to(ROOT)} updated")


if __name__ == "__main__":
    main()
