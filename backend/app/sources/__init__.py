"""External verification sources (docs/verification-sources.md).

Each adapter follows ``app/registry_live.py``: an answer is a result, ``None`` (the source says there
is no such thing) or ``SourceUnavailable`` (could not ask). Unavailable never becomes a flag: the check
is skipped and the case is marked partial. Answers are cached on disk, every call has a timeout and a
share of one total time budget, and nothing secret is logged. ``app.core.outbound.outbound_blocked``
is asked before any connection is opened.
"""
