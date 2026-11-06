#!/usr/bin/env python3
"""
Anthropic provider for the coach chat.

The ONLY place in the server that imports the anthropic SDK. Adding another
provider means a sibling file exposing the same stream_chat() signature, not
edits to chat.py.

Deliberately not abstracted further than that. A generic "LLM client" wrapper
would have to reduce every provider to a lowest common denominator, and the
things this project leans on - prompt caching on the stable prefix, adaptive
thinking, effort - do not have equivalents elsewhere. Better one honest file
per provider than one dishonest interface.
"""

MODELS = {
    # id -> (label, effort). Effort is per-model rather than global: Opus can
    # afford to think, Haiku is here because it is cheap and fast.
    "claude-opus-5":    ("Opus 5 · best", "medium"),
    "claude-sonnet-5":  ("Sonnet 5 · balanced", "medium"),
    "claude-haiku-4-5": ("Haiku 4.5 · cheapest", "low"),
}
DEFAULT_MODEL = "claude-opus-5"

# The player is waiting with the demo paused, so this is an interactive budget,
# not a batch one. Retries are capped low for the same reason - three silent
# retries on a slow call feels like a hang.
TIMEOUT_SECONDS = 90
MAX_RETRIES = 1
MAX_TOKENS = 1200


class ProviderError(Exception):
    """
    Anything the caller should show the user, already safe to display.

    `auth` marks the key itself as the problem, so the UI can reopen the key
    field instead of leaving the user staring at a sentence. A flag rather than
    the caller string-matching the message: the wording is copy, it will be
    reworded, and nothing would fail loudly when it is.
    """

    def __init__(self, message, auth=False):
        super().__init__(message)
        self.auth = auth


def stream_chat(*, api_key, model, system_blocks, messages, on_delta):
    """
    Stream one assistant turn.

    system_blocks : list of content blocks; the caller decides which carry
                    cache_control, because cache placement is a property of how
                    the prompt was assembled, not of the transport.
    messages      : the conversation, oldest first.
    on_delta      : called with each text chunk as it arrives.

    Returns a usage dict. Raises ProviderError with a message that is safe to
    put in front of a user - never the raw SDK exception, which can carry key
    fragments and provider internals.
    """
    try:
        import anthropic
    except ImportError as exc:                       # noqa: F841
        raise ProviderError("The anthropic package is not installed on the "
                            "server (pip install anthropic).") from None

    if model not in MODELS:
        model = DEFAULT_MODEL
    _, effort = MODELS[model]

    client = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS,
                                 max_retries=MAX_RETRIES)
    try:
        with client.messages.stream(
            model=model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            system=system_blocks,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                on_delta(text)
            final = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise ProviderError("That API key was rejected. Check it and try again.",
                            auth=True) from None
    except anthropic.PermissionDeniedError:
        # Also `auth`: the fix is a different key, same as a rejected one.
        raise ProviderError("That key doesn't have access to this model.",
                            auth=True) from None
    except anthropic.RateLimitError:
        raise ProviderError("Rate limited by Anthropic. Wait a moment and retry.") from None
    except anthropic.APITimeoutError:
        raise ProviderError(f"The model took longer than {TIMEOUT_SECONDS}s. "
                            "Try a cheaper model, or a narrower question.") from None
    except anthropic.APIConnectionError:
        raise ProviderError("Couldn't reach Anthropic. Check your connection.") from None
    except anthropic.APIStatusError as exc:
        # Status code only. The message body can echo request content back,
        # and with a user-supplied key that is not ours to forward.
        raise ProviderError(f"Anthropic returned an error (HTTP {exc.status_code}).") from None

    if final.stop_reason == "refusal":
        raise ProviderError("The model declined to answer that one.")

    u = final.usage
    return {
        "model": final.model,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        # The number that says whether the cached prefix is actually working.
        # Zero on a follow-up turn means every turn is paying full price.
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0),
    }
