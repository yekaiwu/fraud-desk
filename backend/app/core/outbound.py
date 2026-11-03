"""The one place that decides whether the application may call a service outside this machine.

Every adapter that opens a network connection to a verification source (the company register, GLEIF,
OneMap, IRAS, the sanctions list download) asks ``outbound_blocked(settings)`` first and does not
connect when it returns a reason. A blocked source is a skipped check with the code ``private_mode``:
never an error, never a flag, and outside the coverage ratio (it was switched off on purpose).

The switch itself is ``GLDB_PRIVATE_MODE`` (``on`` blocks every outbound call). It is read with
``getattr`` so this module works on a build where the setting does not exist yet: no setting means
nothing is blocked. Model backends are not routed through here; they have their own switches.
"""

from __future__ import annotations

PRIVATE_MODE_CODE = "private_mode"


def outbound_blocked(settings: object) -> str | None:
    """A reason (a sentence, no secrets) when outbound calls are blocked by private mode, else None."""
    if str(getattr(settings, "private_mode", "off")).lower() == "on":
        return "Private mode is on (GLDB_PRIVATE_MODE=on): no call leaves this machine."
    return None
