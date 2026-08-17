#!/usr/bin/env python3
"""
CS2 Coach - comms around deaths.

voice.py already extracts who spoke and when, as utterances on the game clock.
Joining that to the death review answers two questions no stats site asks:

  DID YOU CALL AFTER YOU DIED?  You watched the duel. You know what killed you,
  from where, and what they had. The six seconds after your death are the most
  information-dense moment you will have all round, and going quiet wastes it.
  On the bundled match this runs from 27% to 69% across ten players.

  WAS ANYONE TALKING BEFORE YOU DIED?  Walking into a fight nobody discussed is
  different from walking into one your team set up.

THE HARD LIMIT, AND IT IS A BIG ONE
-----------------------------------
This measures WHETHER THE MIC WAS OPEN, not what was said. A player swearing at
their monitor scores exactly the same as one calling "two A, one low". Every
number here is a ceiling on communication, never a measure of it, and the note
that ships with the profile says so in those words - because a model handed
"50% call rate" will otherwise write "you communicate half the time", which this
cannot support.

Transcription would fix that. It is a much heavier dependency and the comms in
this match are not in English, so it is deliberately not attempted here.

WHAT IS EXCLUDED
----------------
Deaths in the last five seconds of a round. The round is over, there is nothing
left to call, and counting them would mark a player down for the rounds where
silence was correct.
"""

import collections

# How long after dying still counts as calling what happened.
AFTER_DEATH_SECONDS = 6.0

# How long before a death counts as "the team was talking".
BEFORE_DEATH_SECONDS = 5.0

# A death this close to the end of the round has nothing left to call.
ROUND_TAIL_SECONDS = 5.0


def _utterances(voice):
    return {p["name"]: p.get("utterances") or []
            for p in (voice or {}).get("players") or []}


def _spoke(utterances, name, start, end):
    """Any speech overlapping [start, end]."""
    for u in utterances.get(name) or []:
        if u["e"] >= start and u["s"] <= end:
            return True
    return False


def _round_end(match, rnd):
    for r in match.get("rounds") or []:
        if r.get("round_num") == rnd:
            return r.get("official_end") or r.get("end")
    return None


def _sides_at(match):
    """Side lookup by (name, tick) from the frames - sides swap at half time."""
    import bisect
    frames = match.get("frames") or []
    ticks = [f["tick"] for f in frames]

    def side(name, tick):
        if not frames:
            return None
        i = min(bisect.bisect_left(ticks, tick), len(frames) - 1)
        for j in (i, i - 1, i + 1):
            if 0 <= j < len(frames):
                for p in frames[j].get("players") or []:
                    if p.get("n") == name:
                        return p.get("t")
        return None
    return side


def after_death_table(match, voice):
    """
    Per player: deaths with something left to call, and how many they spoke on.

    Not a communication score. See the module docstring.
    """
    utt = _utterances(voice)
    tickrate = (voice or {}).get("tickrate") or match.get("tickrate") or 64
    window = int(AFTER_DEATH_SECONDS * tickrate)
    tail = int(ROUND_TAIL_SECONDS * tickrate)

    per = collections.defaultdict(lambda: {"deaths": 0, "spoke": 0})
    for k in match.get("kills") or []:
        victim = k.get("victim")
        if not victim:
            continue
        end = _round_end(match, k.get("round"))
        if end is None or end - k["tick"] < tail:
            continue
        row = per[victim]
        row["deaths"] += 1
        if _spoke(utt, victim, k["tick"], min(k["tick"] + window, end)):
            row["spoke"] += 1

    return {n: dict(r, share=round(r["spoke"] / r["deaths"], 3))
            for n, r in per.items() if r["deaths"]}


def before_deaths(match, voice, player):
    """
    Per death: who on their side was on comms in the seconds before it.

    Returned per death rather than as a total, because "nobody was talking" is
    a fact about that round and a rate would flatten it into a personality.
    """
    utt = _utterances(voice)
    tickrate = (voice or {}).get("tickrate") or match.get("tickrate") or 64
    window = int(BEFORE_DEATH_SECONDS * tickrate)
    side = _sides_at(match)

    out = []
    for k in match.get("kills") or []:
        if k.get("victim") != player:
            continue
        tick = k["tick"]
        mine = side(player, tick)
        talkers = [n for n in utt
                   if side(n, tick) == mine and _spoke(utt, n, tick - window, tick)]
        out.append({
            "round": k.get("round"), "tick": tick,
            "you": player in talkers,
            "teammates": sorted(n for n in talkers if n != player),
        })
    return out


def profile(match, voice, player):
    if not voice or not (voice.get("players")):
        return {}
    table = after_death_table(match, voice)
    mine = table.get(player)
    if not mine:
        return {}

    ranked = sorted(table.items(), key=lambda kv: -kv[1]["share"])
    rank = next((i + 1 for i, (n, _) in enumerate(ranked) if n == player), None)
    before = before_deaths(match, voice, player)
    silent = [b for b in before if not b["you"] and not b["teammates"]]

    return {
        "player": player,
        "deaths_with_time_to_call": mine["deaths"],
        "called_after": mine["spoke"],
        "share": mine["share"],
        "rank": rank, "of": len(ranked),
        "table": {n: r["share"] for n, r in ranked},
        "silent_before": [{"round": b["round"], "tick": b["tick"]}
                          for b in silent],
        "before": before,
        "note": ("This measures whether the MIC WAS OPEN, not what was said - "
                 "someone swearing at their monitor scores the same as someone "
                 "calling 'two A, one low'. Treat every number as a ceiling on "
                 "communication, never a measure of it, and never say the "
                 "player 'communicated' or 'called' anything specific. Deaths "
                 "in the last 5s of a round are excluded: nothing was left to "
                 "call."),
    }


def main():
    import json
    import sys
    from pathlib import Path
    if len(sys.argv) < 2:
        print("usage: comms.py data/match.json [player]")
        return 1
    path = Path(sys.argv[1])
    match = json.loads(path.read_text(encoding="utf-8"))
    player = sys.argv[2] if len(sys.argv) > 2 else match.get("pov")

    vpath = path.with_name(path.stem + "_voice.json")
    if not vpath.exists():
        print(f"No voice index at {vpath}. Run: python parser/voice.py your.dem")
        return 1
    voice = json.loads(vpath.read_text(encoding="utf-8"))

    print("SPOKE WITHIN 6s OF DYING (mic open - NOT proof anything useful was "
          "said)\n")
    table = after_death_table(match, voice)
    print(f"  {'player':<14}{'deaths':>7}{'spoke':>7}{'share':>7}")
    for name, r in sorted(table.items(), key=lambda kv: -kv[1]["share"]):
        mark = "  <- you" if name == player else ""
        print(f"  {name:<14}{r['deaths']:>7}{r['spoke']:>7}"
              f"{r['share'] * 100:>6.0f}%{mark}")

    p = profile(match, voice, player)
    if not p:
        return 0
    print(f"\n{player}: spoke after {p['called_after']} of "
          f"{p['deaths_with_time_to_call']} deaths that had time left "
          f"({p['share'] * 100:.0f}%), rank {p['rank']} of {p['of']}.")
    if p["silent_before"]:
        rounds = ", ".join(str(b["round"]) for b in p["silent_before"])
        print(f"\n  Nobody on their side had the mic open in the 5s before "
              f"{len(p['silent_before'])} of their deaths (rounds {rounds}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
