#!/usr/bin/env python3
"""
Mutation runner.

Applies one deliberate break at a time and reports whether the suite notices.
A test that cannot fail is worse than no test, and the only way to know is to
break the code under it.

RESTORES ON INTERRUPT, WHICH IS THE WHOLE REASON THIS IS A FILE
---------------------------------------------------------------
This started as a throwaway script. A run was killed by a timeout partway
through and left a mutation in the working tree; the NEXT run then read the
mutated file as its baseline and faithfully restored the corruption after every
mutation. The result was a module with a silently broken gate and a suite that
passed, which is exactly the failure mutation testing exists to catch.

So: originals are captured up front, restoration happens in a finally, and
SIGINT/SIGTERM are handled. Anything that kills the process short of SIGKILL
puts the tree back.
"""

import pathlib
import signal
import subprocess
import sys


def run(mutations, tests, timeout=600):
    """
    mutations: [(path, label, find, replace), ...]
    tests:     pytest target(s)
    """
    paths = {pathlib.Path(m[0]) for m in mutations}
    originals = {p: p.read_text(encoding="utf-8") for p in paths}

    def restore(*_):
        for p, text in originals.items():
            p.write_text(text, encoding="utf-8")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: (restore(), sys.exit(130)))
        except (ValueError, OSError):
            pass                      # not on the main thread; finally covers it

    survived = []
    try:
        for path, label, find, replace in mutations:
            p = pathlib.Path(path)
            orig = originals[p]
            if find not in orig:
                print(f"  ?? PATTERN MISSING  {label}")
                continue
            p.write_text(orig.replace(find, replace, 1), encoding="utf-8")
            r = subprocess.run(
                [sys.executable, "-m", "pytest", *tests, "-q", "--no-header",
                 "-x", "--tb=no"],
                capture_output=True, text=True, timeout=timeout)
            p.write_text(orig, encoding="utf-8")
            if r.returncode:
                print(f"  CAUGHT    {label}")
            else:
                print(f"  SURVIVED  {label}")
                survived.append(label)
    finally:
        restore()

    print(f"\n{len(mutations) - len(survived)}/{len(mutations)} caught")
    return survived


if __name__ == "__main__":
    print(__doc__)
