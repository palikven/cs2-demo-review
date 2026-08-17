#!/usr/bin/env python3
"""
CS2 Coach - how each duel was approached.

Every other layer describes a duel by its outcome: who died, where, whether it
was traded, what it cost. None of them say what the two players were *doing* in
the seconds before it. That is the gap this fills, and it is the closest the
demo gets to the question this whole project started from - "when should I not
have peeked?"

WHAT IS ACTUALLY MEASURED
-------------------------
Frames are sampled every 32 ticks (0.5s at 64-tick), so for each duel we can
take the player's position at ~0.5s intervals leading up to it and compute:

  speed     how fast they were moving, in units/second
  closure   how much of the gap each side closed, measured against the OTHER
            player's starting position

That second one is the useful decomposition. Comparing "gap before" to "gap
after" only tells you the gap shrank; it does not say who shrank it. Measuring
each player's movement against the other's *original* position separates them:
if the victim closed 188 units on where the attacker already was, and the
attacker closed nothing, the victim walked into that duel.

WHAT IS NOT MEASURED, AND WHY THE NAMING AVOIDS IT
--------------------------------------------------
Not "who peeked". Peeking is about crossing into a sightline, which is a
visibility question, and there is no occlusion data here - no smokes-as-geometry,
no walls, no view cones. A player can close 300 units while staying behind cover
the whole way, and a player can peek a hard angle without moving 20 units. So
these functions say `closed_units`, which is arithmetic, rather than `peeked`,
which would be a guess wearing a fact's clothing.

Two more honest limits, both stated in the output:

  * the sample lags. The last frame before a kill is 0-0.5s stale (mean 0.30s on
    the bundled demo). "Speed before the duel" means the half-second approaching
    it, not the instant of death - and a player who stopped to shoot 0.2s early
    reads as stationary.
  * duels near a round boundary must be dropped, not measured. Consecutive
    frames across a round change are 32 ticks apart like any other pair, but the
    players teleport to spawn between them: ignoring that yields an apparent
    8100 u/s on the bundled demo. Every window here is checked for contiguity
    and for staying inside one round.

PER-DUEL FACTS ALWAYS; AGGREGATE CLAIMS ONLY WHEN EARNED
--------------------------------------------------------
"In round 18 you covered 222 units and closed 188 of the 578-unit gap while they
held still" needs no statistics - it is one measurement of one event.

"You die standing still" is a claim about a distribution, and on a single match
there is not enough of one. On the bundled demo the POV dies at 39 u/s against
a lobby median of 72, which looks like a finding and is not: a permutation test
puts it at p=0.09-0.26 depending on the window, and the direction of the POV's
own kill-vs-death comparison flips with it. So `profile()` runs the test and
reports `claimable: False` with the p-value attached, and the fact sheet says
there is no pattern rather than inventing one. It will earn the claim across a
season of demos, which is where this is going.
"""

import bisect
import math
import random
import statistics

from gates import reliable

WINDOW_SECONDS = 1.5        # how far back to decompose the approach
SPEED_SECONDS = 0.5         # the shortest window the sampling supports

# CS2 movement, rifle in hand: ~215 u/s running, ~130 walking, ~85 crouched.
# Above RUN you cannot shoot accurately; below STATIC you were planted.
RUN_SPEED = 140.0
STATIC_SPEED = 40.0

# A closure counts as real only if it is both a meaningful absolute distance and
# a meaningful share of the gap. 100 units across a 900-unit gap is a step.
MIN_CLOSE_UNITS = 100.0
MIN_CLOSE_FRAC = 0.15
MIN_GAP_UNITS = 1.0         # two players standing on each other: no bearing

MIN_DUELS_FOR_CLAIM = 12    # below this the permutation test cannot resolve much
PERMUTATIONS = 20000
PERM_SEED = 20250817        # fixed: the same demo must give the same p-value
P_THRESHOLD = 0.05


def _d(a, b):
    """Ground distance. Height is deliberately ignored - see analyze.dist2d."""
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


class Frames:
    """
    Position lookups by tick.

    Built once per match; every duel reuses it. The per-frame name->player dicts
    are the whole point - without them each lookup is a linear scan of ten
    players across four thousand frames.
    """

    def __init__(self, match):
        self.tickrate = match.get("tickrate") or 64
        # parse_demo.py records the sampling interval it used. Deriving it from
        # the frames instead would be self-confirming: a demo with a gap in it
        # would define its own gap as normal spacing.
        self.sample_every = match.get("sample_every") or 32
        self.frames = match.get("frames") or []
        self.ticks = [f["tick"] for f in self.frames]
        self.by_name = [{p["n"]: p for p in f.get("players", [])}
                        for f in self.frames]
        self.rounds = [f.get("round") for f in self.frames]

    def index_before(self, tick):
        """The last frame sampled strictly before `tick`, or None."""
        i = bisect.bisect_left(self.ticks, tick) - 1
        return i if i >= 0 else None

    def contiguous(self, j, i):
        """
        True if frames j..i can be treated as one continuous stretch of play.

        Both conditions matter. The tick spacing catches demo gaps; the round
        check catches the respawn teleport, which hides inside a perfectly
        normal 32-tick spacing and is worth 8000 u/s if you miss it.
        """
        if j < 0 or i >= len(self.frames) or j > i:
            return False
        limit = self.max_gap()
        for k in range(j, i):
            if self.ticks[k + 1] - self.ticks[k] > limit:
                return False
        return self.rounds[j] == self.rounds[i]

    def alive(self, i, name):
        """The player's record at frame i, only if they were alive in it."""
        p = self.by_name[i].get(name)
        return p if p and p.get("a") else None

    def steps_for(self, seconds):
        """How many samples span `seconds`. At least one."""
        return max(1, round(seconds * self.tickrate / self.sample_every))

    def max_gap(self):
        """
        The largest tick spacing still counted as continuous play.

        One and a quarter sample intervals: enough slack for a tick of jitter,
        not enough to swallow a skip. The bundled match is 4060 pairs at exactly
        32 plus two gaps of 1952 and 3328, so the bar is not delicate.
        """
        return int(self.sample_every * 1.25)


def speed(fr, name, tick, seconds=SPEED_SECONDS):
    """
    Average ground speed over the sampled window ending just before `tick`.

    None when the window is unusable - the player was dead, the frames are not
    contiguous, or it straddles a round change. None means "not measured", and
    callers must not read it as zero.
    """
    i = fr.index_before(tick)
    if i is None:
        return None
    j = i - fr.steps_for(seconds)
    if not fr.contiguous(j, i):
        return None
    a, b = fr.alive(j, name), fr.alive(i, name)
    if not a or not b:
        return None
    dt = (fr.ticks[i] - fr.ticks[j]) / fr.tickrate
    if dt <= 0:
        return None
    return _d(a, b) / dt


def motion(units_per_second):
    """Coarse bucket. None in, None out - an unmeasured window is not 'static'."""
    if units_per_second is None:
        return None
    if units_per_second >= RUN_SPEED:
        return "running"
    if units_per_second <= STATIC_SPEED:
        return "static"
    return "walking"


def approach(fr, victim, attacker, tick, seconds=WINDOW_SECONDS):
    """
    Who closed the distance in the window before this duel.

    Each side's closure is measured against the other's position at the START of
    the window, which is what separates their contribution from the other's.
    Negative means they moved away.
    """
    i = fr.index_before(tick)
    if i is None:
        return None
    j = i - fr.steps_for(seconds)
    if not fr.contiguous(j, i):
        return None
    v0, v1 = fr.alive(j, victim), fr.alive(i, victim)
    a0, a1 = fr.alive(j, attacker), fr.alive(i, attacker)
    if not all((v0, v1, a0, a1)):
        return None

    gap = _d(v0, a0)
    if gap < MIN_GAP_UNITS:
        return None

    v_closed = gap - _d(v1, a0)
    a_closed = gap - _d(v0, a1)

    def real(c):
        return c >= MIN_CLOSE_UNITS and c >= MIN_CLOSE_FRAC * gap

    v_real, a_real = real(v_closed), real(a_closed)
    if v_real and a_real:
        who = "mutual"
    elif v_real:
        who = "victim"
    elif a_real:
        who = "attacker"
    else:
        # Neither crossed meaningful ground: the duel happened across a sightline
        # they were both already in. On the bundled demo this is 68% of all
        # deaths, which is itself the answer to "was the peek the problem" -
        # usually the position was.
        who = "neither"

    return {
        "window_s": round((fr.ticks[i] - fr.ticks[j]) / fr.tickrate, 2),
        "lag_s": round((tick - fr.ticks[i]) / fr.tickrate, 2),
        "gap_start": round(gap, 0),
        "victim_moved": round(_d(v0, v1), 0),
        "attacker_moved": round(_d(a0, a1), 0),
        "victim_closed": round(v_closed, 0),
        "attacker_closed": round(a_closed, 0),
        "closed_by": who,
    }


def duel(match_or_frames, kill, subject=None):
    """
    Everything measurable about how one duel was approached.

    `subject` names whose speed is reported as `speed`; the other side's is
    `speed_other`. Defaults to the victim, since deaths are what get reviewed.
    """
    fr = (match_or_frames if isinstance(match_or_frames, Frames)
          else Frames(match_or_frames))
    victim, attacker = kill.get("victim"), kill.get("attacker")
    if not victim or not attacker or victim == attacker:
        return None

    subject = subject or victim
    other = attacker if subject == victim else victim
    tick = kill["tick"]

    s_self = speed(fr, subject, tick)
    s_other = speed(fr, other, tick)
    out = {
        "tick": tick,
        "round": kill.get("round"),
        "subject": subject,
        "speed": None if s_self is None else round(s_self, 0),
        "speed_other": None if s_other is None else round(s_other, 0),
        "motion": motion(s_self),
        "motion_other": motion(s_other),
    }
    app = approach(fr, victim, attacker, tick)
    if app:
        out.update(app)
        # Restate the closure from the subject's point of view so callers that
        # only read `closed_by` do not have to know whether the subject died.
        if subject == attacker and out["closed_by"] in ("victim", "attacker"):
            out["closed_by_subject"] = out["closed_by"] == "attacker"
        elif subject == victim and out["closed_by"] in ("victim", "attacker"):
            out["closed_by_subject"] = out["closed_by"] == "victim"
    return out


def permutation_p(a, b, n=PERMUTATIONS, seed=PERM_SEED):
    """
    Two-sided permutation test on the difference of medians.

    Dependency-free on purpose - gates.py sets the precedent, and adding scipy
    to ship one p-value is not a trade worth making. Fixed seed so the same
    demo always reports the same number; a p-value that moves between runs is
    not something anyone can act on.
    """
    a = [x for x in a if x is not None]
    b = [x for x in b if x is not None]
    if len(a) < 2 or len(b) < 2:
        return None
    observed = abs(statistics.median(a) - statistics.median(b))
    pool = a + b
    k = len(a)
    rng = random.Random(seed)
    hits = 0
    for _ in range(n):
        rng.shuffle(pool)
        if abs(statistics.median(pool[:k]) - statistics.median(pool[k:])) >= observed:
            hits += 1
    # +1 on both sides: the observed arrangement is itself one of the
    # permutations, and a test that can return exactly 0 overstates its case.
    return (hits + 1) / (n + 1)


def profile(match, player):
    """
    The aggregate, with the evidence for it attached.

    Reports the player's speed distribution at their deaths and kills against
    the rest of the lobby in the same match - the baseline has to come from the
    same demo, because 39 u/s is meaningless without knowing that everyone else
    dies at 72.

    `claimable` is the only field a coaching layer should branch on. It is False
    unless the sample clears gates.reliable AND the permutation test clears
    P_THRESHOLD, which on one match it generally will not.
    """
    fr = Frames(match)
    kills = match.get("kills") or []

    mine_deaths, mine_kills, lobby_deaths = [], [], []
    closed_into_death = 0
    measured_deaths = 0
    for k in kills:
        if not k.get("attacker") or k["attacker"] == k.get("victim"):
            continue
        if k.get("victim") == player:
            s = speed(fr, player, k["tick"])
            if s is not None:
                mine_deaths.append(s)
            app = approach(fr, k["victim"], k["attacker"], k["tick"])
            if app:
                measured_deaths += 1
                if app["closed_by"] in ("victim", "mutual"):
                    closed_into_death += 1
        else:
            s = speed(fr, k["victim"], k["tick"])
            if s is not None:
                lobby_deaths.append(s)
        if k.get("attacker") == player:
            s = speed(fr, player, k["tick"])
            if s is not None:
                mine_kills.append(s)

    p_vs_lobby = permutation_p(mine_deaths, lobby_deaths)
    p_self = permutation_p(mine_deaths, mine_kills)

    enough = reliable(len(mine_deaths), len(mine_deaths) + len(lobby_deaths),
                      min_count=MIN_DUELS_FOR_CLAIM, min_frac=0.0,
                      min_sources=1, sources=1)
    claimable = bool(enough and p_vs_lobby is not None
                     and p_vs_lobby < P_THRESHOLD)

    def med(xs):
        return round(statistics.median(xs), 0) if xs else None

    return {
        "player": player,
        "deaths_measured": len(mine_deaths),
        "kills_measured": len(mine_kills),
        "lobby_deaths_measured": len(lobby_deaths),
        "speed_at_death": med(mine_deaths),
        "speed_at_kill": med(mine_kills),
        "lobby_speed_at_death": med(lobby_deaths),
        "p_vs_lobby": None if p_vs_lobby is None else round(p_vs_lobby, 4),
        "p_death_vs_kill": None if p_self is None else round(p_self, 4),
        "closed_into_death": closed_into_death,
        "approaches_measured": measured_deaths,
        "claimable": claimable,
        # Spelled out because this is the field most likely to be misread by
        # whatever reads it next, including a language model.
        "note": ("The speed difference is significant at p<%.2f." % P_THRESHOLD
                 if claimable else
                 "Not enough evidence to claim a movement habit from this "
                 "match. State the individual duels, not a pattern."),
    }


def annotate(match, deaths, kills, player):
    """
    Attach approach facts to an existing review in place.

    Deliberately mutating: analyze.py already builds these lists, and rebuilding
    them here to return copies would leave two death records to keep in sync.
    """
    fr = Frames(match)
    by_tick = {}
    for k in match.get("kills") or []:
        by_tick.setdefault(k["tick"], []).append(k)

    def find(rec, as_victim):
        for k in by_tick.get(rec["tick"], []):
            if as_victim and k.get("victim") == player:
                return k
            if not as_victim and k.get("attacker") == player:
                return k
        return None

    for rec, as_victim in [(d, True) for d in deaths] + [(c, False) for c in kills]:
        k = find(rec, as_victim)
        if not k:
            continue
        facts = duel(fr, k, subject=player)
        if not facts:
            continue
        for key in ("speed", "motion", "speed_other", "motion_other",
                    "gap_start", "victim_moved", "attacker_moved",
                    "victim_closed", "attacker_closed", "closed_by", "lag_s"):
            if facts.get(key) is not None:
                rec[key] = facts[key]
    return deaths, kills


def main():
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: duels.py data/match.json [player]")
        return 1
    match = json.load(open(sys.argv[1], encoding="utf-8"))
    player = sys.argv[2] if len(sys.argv) > 2 else match.get("pov")
    prof = profile(match, player)
    print(json.dumps(prof, indent=2))

    fr = Frames(match)
    print(f"\nEvery duel {player} lost:\n")
    for k in match.get("kills") or []:
        if k.get("victim") != player or not k.get("attacker"):
            continue
        f = duel(fr, k)
        if not f:
            continue
        print(f"  R{f['round']:>2} {str(k.get('victim_place') or '?'):<14}"
              f" <- {str(k.get('attacker_place') or '?'):<14}"
              f" you {str(f['motion'] or '?'):<7} ({f['speed']:>4.0f} u/s)"
              f"  them {str(f['motion_other'] or '?'):<7}"
              f"  gap {f.get('gap_start', 0):>5.0f}"
              f"  closed by {f.get('closed_by', '?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
