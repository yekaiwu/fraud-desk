"""The review team (docs/agents.md): specialists, a challenger and a case officer.

Checks stay the only source of flags. The team adds two things on top of them, after the
cross-document stage and before scoring: a challenger that may lower a judgement flag when it can
quote the documents' own explanation for it, and a case officer that writes a short summary and the
next steps for the reviewer. Every step is recorded on the case (``Case.agents``).
"""

from app.agents.team import run_team

__all__ = ["run_team"]
