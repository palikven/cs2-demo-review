#!/usr/bin/env python3
"""
CS2 Team Coach - local server (Stage 5)

Serves the viewer AND answers live questions about a specific moment:
pause anywhere, ask "should I peek here?", and get an answer grounded in the
actual state of that tick rather than general advice.

Why stdlib http.server and not FastAPI
--------------------------------------
This is one POST route. FastAPI + uvicorn would be two more dependencies on a
project that has four, for validation and async we don't use and an OpenAPI
schema nobody will read. run_viewer.bat already ran `python -m http.server`,
so this keeps the "no install step" property the project had before.

The API key lives here and never reaches the browser. The viewer talks to
/api/*, this process talks to Anthropic.

Usage:
    set ANTHROPIC_API_KEY=sk-ant-...
    python server/app.py            # http://localhost:8000/viewer/index.html
"""

import argparse
import json
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

from analyze import snapshot  # noqa: E402

MODEL = "claude-opus-5"
MAX_QUESTION = 500
MAX_BODY = 256 * 1024        # a conversation posts its own history

# ThreadingHTTPServer gives every browser tab its own thread, and every chat
# request is a paid call on the user's key. Without a cap, a stuck page that
# retries can quietly spend money in parallel.
MAX_CONCURRENT_CHATS = 3
_chat_slots = threading.Semaphore(MAX_CONCURRENT_CHATS)

SYSTEM = """You are a CS2 coach answering a question about one exact moment in a \
demo the player is reviewing.

You are given the state of that moment: the round, how far into it, where the \
player is standing (by callout), their health, where each living teammate is and \
how far away, how many enemies are alive, and what has already happened this round.

Two things you are deliberately NOT given, because the player didn't have them \
either: where the enemies are, and what happens next. Answer from the information \
in the snapshot only. If the honest answer depends on something you weren't told - \
utility, comms, what the enemy did last round - say which, briefly, instead of \
guessing.

Be direct and specific to the callouts involved. Two or three sentences. The player \
is mid-review and wants a read, not an essay."""


def load_match(name, _cache={}, _lock=threading.Lock()):
    """Match JSONs are megabytes; parse each one once per process."""
    with _lock:
        if name not in _cache:
            path = ROOT / "data" / f"{name}.json"
            # `name` comes off the wire - resolve it and refuse anything that
            # escapes data/, so a crafted match name can't read the filesystem.
            path = path.resolve()
            if not path.is_file() or (ROOT / "data").resolve() not in path.parents:
                return None
            # Shared with the CLI so the two cannot drift: without it the
            # chat's brief silently loses the comms section, and the only
            # symptom is a shorter sheet.
            import analyze as _analyze
            _cache[name] = _analyze.load_match(path)
        return _cache[name]


def describe(snap):
    """The snapshot as text. Callouts and counts only - never coordinates."""
    lines = [
        f"Round {snap['round']}, {snap['t']}s in"
        + (" (bomb is planted)" if snap["post_plant"] else ""),
        f"You are {snap['player']}, on {snap['my_side'].upper()}, "
        f"{snap['my_hp']} HP, standing in {snap['my_place'] or 'an unnamed area'}.",
        f"Alive: you + {snap['teammates_alive']} teammates vs "
        f"{snap['enemies_alive']} enemies.",
        "",
        "Your teammates:",
    ]
    if snap["teammates"]:
        for m in snap["teammates"]:
            lines.append(f"- {m['name']}: {m['place'] or 'unknown area'}, "
                         f"{m['hp']} HP, {int(m['dist'])} units from you"
                         if m.get("dist") is not None else
                         f"- {m['name']}: {m['place'] or 'unknown area'}, {m['hp']} HP")
    else:
        lines.append("- none, you are the last one alive")

    lines += ["", "You do not know where the enemies are.", "", "This round so far:"]
    if snap["round_so_far"]:
        for k in snap["round_so_far"]:
            who = "You lost" if k["mine"] else "You got"
            lines.append(f"- {k['t']}s: {who} a player - {k['victim']} died in "
                         f"{k['victim_place'] or '?'}, killed from "
                         f"{k['attacker_place'] or '?'}")
    else:
        lines.append("- nobody has died yet")
    return "\n".join(lines)


def ask_model(snap, question, api_key=None):
    import anthropic

    client = anthropic.Anthropic(api_key=api_key or None, timeout=90,
                                 max_retries=1)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        thinking={"type": "adaptive"},
        # medium, not high: this is interactive and the player is waiting.
        output_config={"effort": "medium"},
        system=[{"type": "text", "text": SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user",
                   "content": f"{describe(snap)}\n\nQuestion: {question}"}],
    )
    if resp.stop_reason == "refusal":
        return "(The model declined to answer that one.)"
    return "".join(b.text for b in resp.content if b.type == "text").strip()


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_range(self):
        """
        Byte-range support, so the browser can seek inside a voice track.

        SimpleHTTPRequestHandler answers every request with the whole file and
        no Accept-Ranges header. An <audio> element treats that as a stream of
        unknown length: duration reads back NaN and seeking to a point that
        isn't buffered yet silently does nothing. Voice playback is entirely
        seeking - every utterance jump is a seek - so it needs real 206s.

        Returns True if it handled the request.
        """
        rng = self.headers.get("Range")
        if not rng or not rng.startswith("bytes="):
            return False
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return False
        size = os.path.getsize(path)
        spec = rng[6:].split(",")[0].strip()
        try:
            first, _, last = spec.partition("-")
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            else:
                # suffix form: "bytes=-500" means the final 500 bytes
                start, end = max(0, size - int(last)), size - 1
        except ValueError:
            return False
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return True
        end = min(end, size - 1)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        return True

    def end_headers(self):
        # Advertise range support on ordinary 200s too, so the browser knows it
        # may seek without having to probe first.
        if self.path.startswith("/data/voice/"):
            self.send_header("Accept-Ranges", "bytes")
        # Parser output is rewritten in place by re-running the pipeline, at the
        # same URL and often within the same second. SimpleHTTPRequestHandler
        # sends Last-Modified and no Cache-Control, which lets the browser serve
        # its copy without revalidating - so you re-parse a demo, reload, and
        # study numbers that are one run out of date with nothing to indicate
        # it. The viewer and its assets are edited just as often; the whole
        # thing is a localhost dev server, so nothing here is worth caching.
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/api/job/"):
            import upload as up
            job = up.get_job(self.path.rsplit("/", 1)[-1])
            if job is None:
                return self._json(404, {"error": "no such job"})
            return self._json(200, job)
        if self.path == "/api/status":
            # Lets the viewer decide between "chat is ready", "chat needs a key
            # from you", and "there is no server at all" - three different UIs.
            from providers import anthropic_provider as provider
            return self._json(200, {
                "llm": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "chat": True,
                "model": MODEL,
                "models": [{"id": k, "label": v[0]}
                           for k, v in provider.MODELS.items()],
                # The viewer offers a .dem picker only when a server is there
                # to parse one - opening the page from file:// cannot.
                "upload": True,
            })
        try:
            if self._serve_range():
                return
        except (BrokenPipeError, ConnectionResetError):
            return          # the browser closed the stream mid-seek; normal
        return super().do_GET()

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return None
        return json.loads(self.rfile.read(length))

    def _api_key(self):
        """
        The user's key from the browser, else the server's own env var.

        Header first so a user who has connected their own key is billed on it
        even when the machine also has ANTHROPIC_API_KEY set - otherwise
        "connect your own LLM" would silently spend somebody else's credit.
        """
        return (self.headers.get("X-Api-Key") or "").strip() \
            or os.environ.get("ANTHROPIC_API_KEY") or ""

    def _sse_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse(self, event, payload):
        self.wfile.write(f"event: {event}\ndata: {json.dumps(payload)}\n\n"
                         .encode())
        self.wfile.flush()

    def do_POST(self):
        if self.path == "/api/chat":
            return self._chat()
        if self.path.split("?")[0] == "/api/upload":
            return self._upload()
        if self.path != "/api/ask":
            return self._json(404, {"error": "no such endpoint"})
        return self._ask()

    def _upload(self):
        """
        Take a .dem off the wire and run the pipeline on it.

        The body is the raw demo, not multipart: a 350 MB file wrapped in form
        encoding has to be re-parsed out of the stream, and the only field
        worth having travels fine in the query string.
        """
        import upload as up

        # Claimed before a single byte is read. Checking after the upload
        # means the person pushes 350 MB up the wire and is then told no.
        if not up.reserve():
            return self._json(409, {"error": "A demo is already being "
                                             "processed. One at a time - "
                                             "parsing pegs a core."})

        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        stem = up.unique_stem(up.safe_stem((q.get("name") or [""])[0]))
        pov = (q.get("pov") or [""])[0].strip() or None

        length = int(self.headers.get("Content-Length") or 0)
        jid = up.new_job(stem)
        dest = up.DEMO_DIR / f"{stem}.dem"
        try:
            ok, err = up.receive(self.rfile, length, dest)
        except Exception:                                  # noqa: BLE001
            up.release()
            raise
        if not ok:
            up.release()
            up._set(jid, state="failed", error=err)
            return self._json(400, {"error": err, "job": jid})

        up.start(jid, dest, pov)     # the thread releases the slot when done
        return self._json(202, {"job": jid, "match": stem})

    def _chat(self):
        import chat as chatmod
        from providers import anthropic_provider as provider

        key = self._api_key()
        if not key:
            return self._json(503, {"error": "No API key. Add one in the chat "
                                             "panel, or set ANTHROPIC_API_KEY "
                                             "on the server."})
        try:
            req = self._read_json()
        except Exception:                              # noqa: BLE001
            return self._json(400, {"error": "bad request body"})
        if req is None:
            return self._json(400, {"error": "bad request body"})

        question = (req.get("question") or "").strip()
        if not question:
            return self._json(400, {"error": "empty question"})
        if len(question) > chatmod.MAX_QUESTION:
            # Reject rather than truncate: silently cutting someone's question
            # in half and answering the stump is worse than saying no.
            return self._json(400, {"error": f"question is too long "
                                             f"(max {chatmod.MAX_QUESTION} characters)"})

        match_name = req.get("match") or ""
        match = load_match(match_name)
        if match is None:
            return self._json(404, {"error": "no such match"})

        if not _chat_slots.acquire(blocking=False):
            return self._json(429, {"error": "Too many questions in flight. "
                                             "Wait for the current one."})
        try:
            player = req.get("player") or match.get("pov")
            tick = req.get("tick")
            system_blocks, messages = chatmod.build_request(
                match, match_name, player=player, tick=tick, question=question,
                history=req.get("messages"), role=req.get("role"))

            self._sse_open()
            try:
                usage = provider.stream_chat(
                    api_key=key, model=req.get("model") or MODEL,
                    system_blocks=system_blocks, messages=messages,
                    on_delta=lambda t: self._sse("delta", {"text": t}))
            except provider.ProviderError as exc:
                # Already vetted as safe to show - see the provider module.
                return self._sse("error", {"error": str(exc)})
            except Exception as exc:                   # noqa: BLE001
                self.log_error("chat failed: %r", type(exc).__name__)
                return self._sse("error", {"error": "The request failed on the "
                                                    "server. Check its console."})

            snap_round = snap_t = None
            if tick is not None:
                snap = snapshot(match, int(tick), player)
                if snap:
                    snap_round, snap_t = snap["round"], snap["t"]
            self._sse("done", {"usage": usage, "round": snap_round, "t": snap_t})
        except (BrokenPipeError, ConnectionResetError):
            pass                                       # user navigated away
        finally:
            _chat_slots.release()

    def _ask(self):
        """The original single-shot endpoint, kept so older links still work."""
        key = self._api_key()
        if not key:
            return self._json(503, {"error": "No API key configured."})
        try:
            req = self._read_json()
            if req is None:
                return self._json(400, {"error": "bad request body"})
            question = (req.get("question") or "").strip()[:MAX_QUESTION]
            if not question:
                return self._json(400, {"error": "empty question"})

            match = load_match(req.get("match") or "")
            if match is None:
                return self._json(404, {"error": "no such match"})

            snap = snapshot(match, int(req.get("tick")), req.get("player"))
            if snap is None:
                return self._json(400, {"error": "no frame at that tick"})

            return self._json(200, {"answer": ask_model(snap, question, key),
                                    "round": snap["round"], "t": snap["t"]})
        except Exception as exc:                       # noqa: BLE001
            # Log the detail, return a generic message. The exception text from
            # an SDK auth failure can carry key fragments and request content,
            # and with a user-supplied key that is not ours to hand back.
            self.log_error("ask failed: %r", exc)
            return self._json(500, {"error": "The request failed on the server."})

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description="Serve the viewer + the live coach.")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[!] ANTHROPIC_API_KEY not set - the viewer will work, but live "
              "questions will be disabled.")

    srv = ThreadingHTTPServer(("127.0.0.1", args.port),
                              partial(Handler, directory=str(ROOT)))
    print(f"[+] http://localhost:{args.port}/viewer/index.html?match=2v5")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] bye")


if __name__ == "__main__":
    main()
