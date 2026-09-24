"""Use one credential definition for every surface that returns external text to a caller.

Both adapters print text from elsewhere: provider SDK exception messages, tracebacks under
``--log``, log lines, and arguments quoted in refusals. An SDK may include a request header
or part of a key in an exception message, and the adapters cannot predict which message will.

**Where this applies.** Text this package writes *about* a call: an error message, a log
line, a traceback, and the request-side fields of a run report.

**Where it does not apply.** The records a fetch returns, and the ``meta`` fields
that describe them. This source returns arbitrary model output *as data*: a record value
that happens to look like a token is the answer, and masking it would be this package
corrupting the thing it was asked to fetch. The key-level diagnostics name keys that appear
in those records, so masking one there would publish a diagnostic that no longer matches the
record. The distinction is whether the text is narration or returned data.

**What counts as a credential** is settled four ways, strongest first. An exact value the
environment holds under a name that says credential catches key material of any shape,
including a provider's whose spelling nobody here has seen. The other three are shape:
a labelled assignment, a key literal a provider stamps with its own prefix, and an opaque
run long enough that nothing else is spelled that way.
"""

from __future__ import annotations

import os
import re

__all__ = ["MASK", "redact"]

MASK = "***"
"""What a masked span reads as, spelled the way the CLI already spells a hidden value."""

_SHORTEST_SECRET = 8
"""Shorter than this, an environment value is not treated as key material.

A variable holding ``1`` or ``true`` would otherwise blank out every digit and every such
word in the sentence, which is a worse message and not a safer one.
"""

_SECRET_VARIABLE = re.compile(
    r"(?:^|_)(?:API_?KEY|ACCESS_?KEY|SECRET_?KEY|PRIVATE_?KEY|SECRET|TOKEN"
    r"|PASSWORD|PASSWD|CREDENTIALS?|AUTH|AUTHORIZATION)(?:_|$)",
    re.IGNORECASE,
)
"""Which environment variable holds key material, read off the name.

The CLI identifies secrets by declaration, using profile parameters whose cast is
``secret``. The library cannot use that configuration engine because its copy will be
replaced by a shared one, so it must not import it. Instead, this module uses the variable
name. A CLI test checks that every parameter declared secret is recognised by this rule.

Bare ``KEY`` is left out. ``plan.key`` names a record column, and a column name treated as
key material would mask an ordinary word everywhere it appeared.
"""

_LABELLED = re.compile(
    r"""(?ix)
    (                                           # kept: the label and its separator
      \b (?: x- )?
      (?: api[-_]?key | access[-_]?token | refresh[-_]?token | auth[-_]?token
        | token | secret | password | passwd | credential | authorization )
      \b \s* [=:] \s* (?: bearer \s+ | basic \s+ )? ["']?
    )
    ( [^\s"',;)\]}]{4,} )                       # masked: the value it introduces
    """
)

_CARRIED = re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._~+/=-]{6,})")
"""A carried credential with no label in front of it, as an HTTP header quotes one."""

_STAMPED = re.compile(
    r"""(?x)
    \b (?:
        sk-(?: proj- | ant- )? [A-Za-z0-9_-]{6,}     # OpenAI, Anthropic
      | sk_(?: live | test )_ [A-Za-z0-9]{6,}
      | gh[pousr]_ [A-Za-z0-9]{10,}
      | glpat- [A-Za-z0-9_-]{10,}
      | xox[baprs]- [A-Za-z0-9-]{10,}
      | AIza [0-9A-Za-z_-]{10,}
      | AKIA [0-9A-Z]{10,}
      | hf_ [A-Za-z0-9]{10,}
      | ya29\. [A-Za-z0-9_-]{10,}
      | eyJ [A-Za-z0-9_-]{6,} \. [A-Za-z0-9_-]{6,} \. [A-Za-z0-9_-]{4,}
    )
    """
)
"""Key literals a provider stamps with its own prefix.

This list is not exhaustive: the adapter can reach any provider supported by the SDK. The
environment rule and opaque-run check also catch prefixes not listed here.
"""

_OPAQUE_RUN = re.compile(r"[A-Za-z0-9_-]{28,}")
"""A candidate unbroken run. Twenty-eight is under the shortest opaque key in common use —
a thirty-two character hex key — and over anything a sentence spells in one word.

The class stops at ``.`` and ``/`` so that a path, which is neither short nor a credential,
is measured one segment at a time.
"""


def _is_key_material(run: str) -> bool:
    """Whether a run that long reads as generated key material rather than as a name.

    A generated key is either heavy with digits or mixes cases. A long identifier a message
    quotes — an attribute name, a temporary directory named after the test that made it — is
    neither, and masking one would edit a message this package is trying to hand over whole.
    """
    digits = sum(1 for character in run if character.isdigit())
    if digits >= 4:
        return True
    return digits >= 1 and any(c.islower() for c in run) and any(c.isupper() for c in run)


def _environment_secrets() -> list[str]:
    """Every value the environment holds under a name that says credential, longest first.

    Read values on each call because the CLI writes resolved profile values into the
    environment while it runs; an import-time snapshot could miss the active key. Sort
    longest first so a shorter value does not mask only part of a longer one.
    """
    found = [
        value
        for name, value in os.environ.items()
        if len(value) >= _SHORTEST_SECRET and _SECRET_VARIABLE.search(name)
    ]
    return sorted(found, key=len, reverse=True)


def redact(text: str) -> str:
    """*text* with anything that could be a credential replaced by :data:`MASK`."""
    for value in _environment_secrets():
        text = text.replace(value, MASK)
    text = _LABELLED.sub(lambda found: found.group(1) + MASK, text)
    text = _CARRIED.sub(lambda found: f"{found.group(1)} {MASK}", text)
    text = _STAMPED.sub(MASK, text)
    return _OPAQUE_RUN.sub(
        lambda found: MASK if _is_key_material(found.group()) else found.group(), text
    )
