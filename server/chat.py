#!/usr/bin/env python3
"""
CS2 Coach - the conversation layer.

Builds the context for a multi-turn coach chat and hands it to a provider.
app.py stays a router; everything about what the model sees lives here.

WHY THE PROMPT IS SPLIT THE WAY IT IS
-------------------------------------
Two blocks, and the order is load-bearing:

  STABLE   system prompt + a brief on the whole match - the player's rating and
           swing, every death with its callouts, the team scoreboard, the round
           results. Same bytes on every turn of a conversation, so it carries
           cache_control and is read back at ~0.1x after the first turn.

  VOLATILE the tick the player is paused at, then the conversation so far, then
           the new question. Different every turn.

Caching is a prefix match: one byte of drift in the stable half and everything
after it is re-billed. That is why the brief is built once per (match, player)
and memoised rather than reassembled per request - see build_brief().

WHAT THE MODEL IS AND IS NOT TOLD
---------------------------------
It gets what the player knew at that tick (their own position by callout, their
teammates, alive counts) plus what the demo proves happened afterwards (where
they died, what it cost). It never gets live enemy positions - snapshot() in
analyze.py does not even construct them. That invariant is the whole reason the
answers are worth reading, and it is easy to break by "helpfully" widening the
context, so there is a test pinning it.

ROLES - designed for, not built
-------------------------------
A future version will ask whether this was a FACEIT pug (no roles) or a team
match, and in the team case let the user assign IGL / AWPer / Entry / Support /
Anchor and the positions each played. The plumbing is here: `role` rides through
the request, the context payload and into the system prompt via ROLE_GUIDANCE,
which ships EMPTY on purpose. Writing per-role coaching rules needs its own
design pass - what actually differs in the advice - and guessing now would bake
in the wrong answer.
"""

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import analyze  # noqa: E402
import coach as coachmod  # noqa: E402

MAX_QUESTION = 2000          # a pasted round description is a fair question
MAX_HISTORY_TURNS = 24       # 12 exchanges; older ones are dropped oldest-first
MAX_HISTORY_CHARS = 24000

SYSTEM = """You are a CS2 coach talking to a player who is reviewing their own \
demo. They can see the map, the scoreboard and the playhead; you can see the \
numbers the demo proves.

You are given, once: a brief on the whole match - their rating, KAST, ADR and \
round swing, every death with the callout they died in and the callout they were \
killed from, where they repeatedly die, and the team scoreboard. Then, each turn: \
the exact moment they are paused at.

Ground every claim in that material. Quote the actual numbers and callouts - "you \
died in Main five times, four of them untraded" is worth saying; "work on your \
positioning" is not. If a question needs something you were not given - utility, \
comms, what the enemy was thinking, what happens after the current tick - say so \
in a clause and answer what you can.

You do NOT know where the enemies were at any given moment. The player did not \
either. Do not invent an enemy position to justify an answer.

Answer short. Two to four sentences unless they ask for detail. They are mid- \
review with the demo paused, so give them the read and let them ask again."""

# Empty on purpose - see the module docstring. Keys will be role names.
ROLE_GUIDANCE = {}


def _clip(text, limit):
    return text if len(text) <= limit else text[:limit] + "\n[...truncated]"


def team_scoreboard(match):
    """Every player's headline numbers, best rating first."""
    per = (match.get("stats") or {}).get("per_player") or {}
    rows = sorted(per.items(), key=lambda kv: -(kv[1].get("rating") or 0))
    out = ["TEAM SCOREBOARD (rating 1.0; 1.00 is an average player)"]
    for name, s in rows:
        out.append(
            f"- {name}: rating {s.get('rating')}, KAST {s.get('kast')}%, "
            f"ADR {s.get('adr')}, {s.get('kills')}K/{s.get('deaths')}D/"
            f"{s.get('assists')}A")
    return "\n".join(out)


def round_results(match):
    """How each round ended - context for 'what happened in round 12'."""
    out = ["ROUND RESULTS"]
    for r in match.get("rounds") or []:
        n = r.get("round_num")
        if n is None:
            continue
        out.append(f"- round {n}: {str(r.get('winner') or '?').upper()} won "
                   f"({str(r.get('reason') or 'unknown').replace('_', ' ')})")
    return "\n".join(out)


_brief_cache, _brief_lock = {}, threading.Lock()


def build_brief(match, match_name, player, role=None):
    """
    The stable half of the prompt.

    Memoised per (match, player, role) because it must be byte-identical across
    the turns of a conversation or the cache misses and every follow-up pays
    full price for ~4k tokens.
    """
    key = (match_name, player, role)
    with _brief_lock:
        if key in _brief_cache:
            return _brief_cache[key]

    a = analyze.analyze(match, player)
    parts = [
        coachmod.build_facts(a, match),
        "",
        team_scoreboard(match),
        "",
        round_results(match),
    ]
    guidance = ROLE_GUIDANCE.get(role)
    if guidance:
        parts += ["", f"THIS PLAYER'S ROLE: {role}", guidance]

    brief = "\n".join(parts)
    with _brief_lock:
        _brief_cache[key] = brief
    return brief


def moment(match, tick, player):
    """The volatile half: what the player is looking at right now."""
    if tick is None:
        return "The player has not paused at a particular moment."

    # snapshot() answers with the NEAREST frame, which is right for a playhead
    # sitting between 32-tick samples and wrong for a tick that isn't in the
    # match at all. Unchecked, a bogus tick from the client yields a confident
    # "Round 5, 15624478s in, 0 HP" and the model reasons about it as fact.
    frames = match.get("frames") or []
    if not frames:
        return "This match has no position data."
    lo, hi = frames[0]["tick"], frames[-1]["tick"]
    if not (lo <= int(tick) <= hi):
        return "The player is paused outside the recorded part of the match."

    snap = analyze.snapshot(match, int(tick), player)
    if snap is None:
        return "The player is paused outside any round."
    # describe() lives in app.py today; imported lazily to avoid a cycle.
    from app import describe
    return ("THE MOMENT THEY ARE PAUSED AT\n" + describe(snap)
            + "\n\n(They can move the playhead; later questions may be about a "
              "different moment, which will be restated.)")


def clamp_history(messages):
    """
    Keep a conversation from growing without bound.

    Dropped oldest-first, and only ever whole turns - a dangling assistant reply
    with no question in front of it reads as the model talking to itself.
    """
    clean = []
    for m in messages or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            clean.append({"role": role, "content": _clip(content, MAX_QUESTION)})
    clean = clean[-MAX_HISTORY_TURNS:]
    while clean and clean[0]["role"] != "user":
        clean.pop(0)

    total = sum(len(m["content"]) for m in clean)
    while clean and total > MAX_HISTORY_CHARS:
        dropped = clean.pop(0)
        total -= len(dropped["content"])
        while clean and clean[0]["role"] != "user":
            total -= len(clean.pop(0)["content"])
    return clean


def build_request(match, match_name, *, player, tick, question, history=None,
                  role=None):
    """
    Assemble (system_blocks, messages) for the provider.

    Pure - no network, no SDK - so the thing that decides what the coach knows
    is directly testable.
    """
    brief = build_brief(match, match_name, player, role)
    system_blocks = [
        {"type": "text", "text": SYSTEM},
        # The cache breakpoint. Everything above is identical on every turn of
        # every conversation about this match; everything below varies.
        {"type": "text", "text": brief,
         "cache_control": {"type": "ephemeral"}},
    ]

    messages = clamp_history(history)
    messages.append({
        "role": "user",
        "content": f"{moment(match, tick, player)}\n\nQuestion: "
                   f"{_clip(question.strip(), MAX_QUESTION)}",
    })
    return system_blocks, messages


def suggested_questions(analysis, tick):
    """
    Seeds for the chat's chips. Built from the player's own data so they point
    at something real rather than being decorative.
    """
    out = ["What should I change about how I'm playing this map?"]
    if not analysis:
        return out
    near = None
    for d in analysis.get("deaths") or []:
        if tick is not None and abs(d.get("tick", 0) - tick) < 640:
            near = d
            break
    if near:
        out.insert(0, "What did I do wrong here?")
        if near.get("swing_cost_pct", 0) >= 15:
            out.insert(1, f"Why did this death cost {near['swing_cost_pct']:.0f}%?")
        if near.get("victim_place"):
            out.append(f"Where should I have peeked instead of {near['victim_place']}?")
    hot = [h for h in (analysis.get("death_places") or []) if h.get("notable")]
    if hot:
        out.append(f"Why do I keep dying in {hot[0]['place']}?")
    return out[:4]
