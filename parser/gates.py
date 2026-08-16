#!/usr/bin/env python3
"""
CS2 Team Coach - reliability gates.

One idea, in one place, because it is the difference between advice and
confident nonsense: the parser cannot tell "this did not happen" from "this
demo did not record it", and a zero reads as a finding. Left ungated, the
model diagnoses a utility problem that is really a parsing gap, or a
positioning habit that is really one unlucky round.

Deliberately dependency-free (no polars, no awpy) so the narration and coaching
layers can import it without dragging the parser's stack along.
"""


def reliable(observed, total, min_count=3, min_frac=0.03, min_sources=2, sources=1):
    """
    Is a signal strong enough to say out loud?

    Two independent bars, both of which must clear:

      * an ABSOLUTE floor (min_count) - percentages of tiny samples lie. One
        death out of three is 33% and still means nothing.
      * a RELATIVE floor (min_frac) - three deaths in one spot out of eighty
        is noise dressed up as a pattern.

    `sources`/`min_sources` guard a different failure: a field that only ever
    fires for one player is usually a parsing artefact rather than a team
    habit. 2v5.dem records 1 flash assist across 151 kills and attackerblind
    on 0 of 159, which is not a real team's utility usage - it is a demo that
    did not record it.
    """
    if sources < min_sources:
        return False
    return observed >= max(min_count, min_frac * total)
