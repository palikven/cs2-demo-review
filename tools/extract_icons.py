#!/usr/bin/env python3
"""
Extract the CS2 weapon icons the killfeed uses, from your local game install.

    pip install vpk scour
    python tools/extract_icons.py

Writes viewer/icons/*.svg. Re-run after a CS2 update if an icon looks stale or a
new weapon shows up as a missing icon in the feed.

How this works
--------------
CS2's UI icons live inside pak01_dir.vpk as `.vsvg_c` - Source 2 "compiled" SVG
resources. The compilation is only a resource header wrapped around the original
SVG text, so the actual markup can be sliced straight out between `<svg` and
`</svg>`. No decompiler needed.

LICENSING - READ THIS
---------------------
The output is Valve's artwork, pulled from the copy of CS2 already on this
machine. That is fine for using this tool locally. Do NOT commit viewer/icons/
to a public repo or ship it to anyone - add it to .gitignore and have each user
run this script against their own install instead.
"""

import os
import re
import sys
from pathlib import Path

try:
    import vpk
except ImportError:
    sys.exit("[!] pip install vpk")

try:
    from scour import scour
    HAVE_SCOUR = True
except ImportError:
    HAVE_SCOUR = False
    print("[*] scour not installed - writing unoptimised SVG (pip install scour)")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "viewer" / "icons"

# Icons named after the weapon string the demo parser emits, so the viewer can
# resolve `icons/<weapon>.svg` directly with almost no mapping table.
EQUIPMENT = """
ak47 aug awp bizon cz75a deagle elite famas fiveseven g3sg1 galilar glock hkp2000 p2000
m249 m4a1 m4a1_silencer mac10 mag7 mp5sd mp7 mp9 negev nova p90 p250 revolver sawedoff
scar20 sg556 ssg08 tec9 ump45 usp_silencer xm1014 knife knife_t bayonet taser hegrenade
flashbang smokegrenade decoy molotov incgrenade inferno firebomb c4 planted_c4 world
""".split()

# CS2's own killfeed decorations
DEATHNOTICE = "icon_headshot icon_suicide noscope penetrate blind_kill smoke_kill inairkill".split()

STEAM_GUESSES = [
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
    os.path.expanduser("~/.steam/steam"),
    os.path.expanduser("~/Library/Application Support/Steam"),
]


def find_pak():
    """Locate pak01_dir.vpk via the registry, then via the usual install paths."""
    roots = []
    try:                                    # Windows: ask Steam where it lives
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            roots.append(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        pass
    roots += STEAM_GUESSES

    for r in roots:
        # the library the game is in is not always the library Steam is in
        libs = [r]
        vdf = Path(r) / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            libs += re.findall(r'"path"\s*"([^"]+)"', vdf.read_text(errors="ignore"))
        for lib in libs:
            p = (Path(lib.replace("\\\\", "\\")) / "steamapps" / "common" /
                 "Counter-Strike Global Offensive" / "game" / "csgo" / "pak01_dir.vpk")
            if p.exists():
                return p
    return None


def optimise(svg):
    if not HAVE_SCOUR:
        return svg
    opts = scour.parse_args([])
    opts.remove_metadata = True
    opts.strip_comments = True
    opts.strip_ids = True
    opts.shorten_ids = True
    opts.indent_type = "none"
    opts.newlines = False
    opts.set_precision = 2
    opts.enable_viewboxing = False
    opts.strip_xml_prolog = True
    try:
        svg = scour.scourString(svg, opts)
    except Exception as e:
        print("   [!] scour failed, keeping raw:", e)
    return re.sub(r">\s+<", "><", svg).strip()


def main():
    pak_path = find_pak()
    if not pak_path:
        sys.exit("[!] Could not find pak01_dir.vpk - is CS2 installed? "
                 "Pass the path manually by editing find_pak().")
    print(f"[*] Reading {pak_path}")

    pak = vpk.open(str(pak_path))
    have = set(pak)
    OUT.mkdir(parents=True, exist_ok=True)

    wrote, missing, blank, total = 0, [], [], 0
    jobs = ([("panorama/images/icons/equipment/%s.vsvg_c" % w, w) for w in EQUIPMENT] +
            [("panorama/images/hud/deathnotice/%s.vsvg_c" % w, w) for w in DEATHNOTICE])

    for vpath, name in jobs:
        if vpath not in have:
            missing.append(name)
            continue
        raw = pak.get_file(vpath).read()
        i, j = raw.find(b"<svg"), raw.rfind(b"</svg>")
        if i < 0 or j < 0:
            missing.append(name + " (no svg payload)")
            continue
        svg = optimise(raw[i:j + 6].decode("utf-8", "replace"))
        # CS2 ships a few empty placeholders - world.svg is one. Flag rather than
        # write a file that renders as nothing.
        if not re.search(r"<(path|polygon|circle|rect|ellipse|polyline)", svg):
            blank.append(name)
        (OUT / (name + ".svg")).write_text(svg, encoding="utf-8")
        wrote += 1
        total += len(svg)

    print(f"[+] Wrote {wrote} icons to {OUT} ({total // 1024} KB)")
    if blank:
        print(f"[*] Empty in CS2 (the viewer aliases these): {', '.join(blank)}")
    if missing:
        print(f"[!] Not found in this build: {', '.join(missing)}")


if __name__ == "__main__":
    main()
