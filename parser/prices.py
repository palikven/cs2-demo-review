#!/usr/bin/env python3
"""
CS2 Coach - the buy-menu price table.

Its own module because economy.py needs it and parse_demo.py imports polars and
awpy at module scope: reaching into the parser for a dict of integers drags half
a second and the whole parsing stack behind it. gates.py is dependency-free for
the same reason.

One table, imported by both, so the parser and the economy layer can never
disagree about what an AK costs.
"""

# HLTV's Rating 3.0 eco adjustment is defined as "the price of a player's armor
# plus their most expensive weapon", so that is what this computes rather than
# the game's own current_equip_value (which also counts the pistol and every
# grenade, and so rates a full-utility rifler well above a bare AWPer).
#
# CS2 buy-menu prices. Only the weapons that can be a player's most expensive
# are worth listing; anything unknown scores 0 and falls back to the armour,
# which is the safe direction - it under-rates rather than inventing value.
WEAPON_PRICES = {
    # rifles and snipers
    "AK-47": 2700, "M4A4": 3000, "M4A1-S": 2900, "AWP": 4750,
    "Galil AR": 1800, "FAMAS": 2050, "AUG": 3300, "SG 553": 3000,
    "SSG 08": 1700, "G3SG1": 5000, "SCAR-20": 5000,
    # SMGs
    "MAC-10": 1050, "MP9": 1250, "MP7": 1500, "MP5-SD": 1500,
    "UMP-45": 1200, "P90": 2350, "PP-Bizon": 1400,
    # heavy
    "Nova": 1050, "XM1014": 2000, "MAG-7": 1300, "Sawed-Off": 1100,
    "M249": 5200, "Negev": 1700,
    # pistols
    "Glock-18": 200, "USP-S": 200, "P2000": 200, "P250": 300,
    "Five-SeveN": 500, "Tec-9": 500, "CZ75-Auto": 500,
    "Dual Berettas": 300, "Desert Eagle": 700, "R8 Revolver": 600,
    "Zeus x27": 200,
}
ARMOR_PRICE = 650          # kevlar
HELMET_EXTRA = 350         # kevlar + helmet is 1000 total
FULL_BUY_VALUE = 3700      # rifle + kevlar&helmet, the reference "full buy"
