"""Route inventory (compliance O2 and O9, docs/compliance.md section 6).

The HTTP surface must equal api.md section 2, so an applicant-facing or auto-decision route cannot
appear silently.
"""

from __future__ import annotations

import re

from app.main import create_app

# api.md section 2, hard-coded on purpose. The three /rules routes for the check catalogue were
# added deliberately (owner request: edit the check catalogue) and each needs a reviewer and reason. Path parameter names are normalised to ``{}``.
API_MD_SECTION_2: set[tuple[str, str]] = {
    ("GET", "/health"),
    ("POST", "/cases"),
    ("GET", "/cases"),
    ("GET", "/cases/{}"),
    ("GET", "/cases/{}/pages/{}"),
    ("POST", "/cases/{}/review"),
    ("GET", "/cases/{}/review"),
    ("GET", "/cases/{}/pack"),
    ("GET", "/rules/effectiveness"),
    ("GET", "/jobs/{}"),
    ("POST", "/cases/{}/files"),
    ("POST", "/cases/{}/rerun"),
    ("GET", "/cases/{}/versions"),
    ("GET", "/cases/{}/files/{}"),
    ("GET", "/cases/{}/documents/{}/words"),
    ("GET", "/cases/{}/audit"),
    ("GET", "/rules"),
    # check catalogue change control (api.md section 9a): edit, reset and read the change log
    ("PUT", "/rules/{}/override"),
    ("DELETE", "/rules/{}/override"),
    ("GET", "/rules/changes"),
    ("GET", "/explain/method"),
    ("DELETE", "/cases/{}"),
    ("POST", "/admin/retention/purge"),
}


def _registered(settings, db):  # noqa: ANN001, ANN202
    """(method, normalised path) pairs and operation ids from the OpenAPI schema (FastAPI keeps
    included routers lazy, so ``app.routes`` is not a flat list)."""
    schema = create_app(settings, db=db).openapi()
    pairs: set[tuple[str, str]] = set()
    ops: list[tuple[str, str]] = []
    for path, item in schema["paths"].items():
        for method, op in item.items():
            pairs.add((method.upper(), re.sub(r"\{[^}]+\}", "{}", path)))
            ops.append((path, op.get("operationId", "")))
    return ops, pairs


def test_registered_routes_equal_api_md_section_2(settings, db) -> None:  # noqa: ANN001
    _, pairs = _registered(settings, db)
    assert pairs == API_MD_SECTION_2


def test_no_route_is_named_or_pathed_auto(settings, db) -> None:  # noqa: ANN001
    ops, _ = _registered(settings, db)
    assert ops
    for path, op_id in ops:
        assert "auto" not in path.lower(), path
        assert "auto" not in op_id.lower(), op_id


def test_no_applicant_facing_or_decision_only_route(settings, db) -> None:  # noqa: ANN001
    ops, pairs = _registered(settings, db)
    banned = ("applicant", "approve", "reject", "decide", "public")
    for path, _ in ops:
        assert not any(w in path.lower() for w in banned), path
    # the only route that records a verdict is the review route, which needs a reviewer and reason
    assert [p for p in pairs if p[0] == "POST" and p[1].endswith("/review")] == [
        ("POST", "/cases/{}/review")
    ]


def test_catalogue_writes_are_the_only_put_routes_and_need_a_reviewer(settings, db) -> None:  # noqa: ANN001
    """The only PUT route is the check catalogue override; like the review route it records a
    named reviewer and a written reason (422 without them, see test_api_catalogue.py)."""
    _, pairs = _registered(settings, db)
    assert [p for p in pairs if p[0] == "PUT"] == [("PUT", "/rules/{}/override")]
    assert sorted(p for p in pairs if p[0] == "DELETE") == [
        ("DELETE", "/cases/{}"),
        ("DELETE", "/rules/{}/override"),
    ]
