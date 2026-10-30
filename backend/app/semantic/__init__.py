"""Semantic signals: model-derived, evidence-bound checks (docs/semantic-signals.md).

Off by default (``GLDB_SEMANTIC=off``). Import the submodules you need; this package imports nothing
heavy on purpose, because ``app.checks.registry`` and ``app.queue`` import ``app.semantic.policy``.

* ``policy``   - constants and tiny helpers (rule prefix, severity cap, the on/off switch)
* ``claims``   - the claim schema and the evidence verifier
* ``render``   - documents as numbered, citable, untrusted-wrapped prompt text
* ``call``     - the model call wrapper and ``SemanticNotRun``
* ``flags``    - verified claim -> Flag
* ``registry`` - ``@semantic_check`` and discovery of ``app/semantic/checks/*.py``
"""
