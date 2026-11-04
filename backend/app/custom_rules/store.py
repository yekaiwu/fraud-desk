"""Storage of custom rules: rules, versions, adoptions and the change log (docs/custom-rules.md).

A rule belongs to the account that created it and is private by default. The owner can make it public;
every other signed-in account can then see it and ADOPT it (opt-in), which pins the version it adopted.
Nothing public changes anyone's results until they adopt. Public versions are immutable (a new version is
a new row; ``custom_rule_versions`` is append-only by trigger), and rules are never deleted: retiring
keeps every version, so a past run stays explainable from the rule id, version and spec hash on it.

Every write is one transaction together with its ``custom_rule_changes`` row (append-only, like
``rule_changes``). Permission checks are the router's job, using the ``can_*`` predicates; write
functions raise ``ApiError`` for state conflicts only (retired, already public, own rule ...).
``load_rule_set`` is what an analysis reads: the rules of the account that started it and nobody else's.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.db import Database, ts
from app.core.errors import ApiError
from app.custom_rules.engine import LoadedCustomRule
from app.custom_rules.spec import RULE_ID_RE, CustomRuleSpec, new_rule_id, parse_spec

log = logging.getLogger("gldb.custom_rules")

_lock = threading.Lock()  # one process: serialises a read-check-write of a rule
MAX_OWN_LOADED = 100
MAX_ADOPTED_LOADED = 50
CHANGES_DEFAULT_LIMIT, CHANGES_MAX_LIMIT = 50, 200
_ADOPTION_ACTIONS = ("adopt", "unadopt")


@dataclass(frozen=True)
class RuleRow:
    id: str
    owner: str
    name: str
    description: str
    current_version: int
    enabled: bool
    visibility: str  # private | public
    published_version: int | None
    published_at: str | None
    status: str  # active | retired
    created_at: str
    updated_at: str
    retired_at: str | None

    @property
    def public(self) -> bool:
        return self.visibility == "public" and self.status == "active"


@dataclass(frozen=True)
class VersionRow:
    rule_id: str
    version: int
    spec: CustomRuleSpec | None  # None when the stored spec no longer validates
    spec_hash: str
    condition_hash: str
    readback: str
    created_at: str
    created_by: str
    reason: str
    draft_id: str | None
    # the name and description this version was saved with: another account sees these with the
    # version it adopted or the published one, never the owner's later wording (empty: older rows)
    name: str = ""
    description: str = ""


@dataclass(frozen=True)
class AdoptionRow:
    account: str
    rule_id: str
    version: int
    adopted_at: str


# ---------------------------------------------------------------- row mapping


def _rule(r: sqlite3.Row) -> RuleRow:
    return RuleRow(
        id=r["id"],
        owner=r["owner"],
        name=r["name"],
        description=r["description"],
        current_version=r["current_version"],
        enabled=bool(r["enabled"]),
        visibility=r["visibility"],
        published_version=r["published_version"],
        published_at=r["published_at"],
        status=r["status"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        retired_at=r["retired_at"],
    )


def _version(r: sqlite3.Row) -> VersionRow:
    try:
        raw = json.loads(r["spec"])
    except ValueError:
        raw = None
    spec, _ = parse_spec(raw)
    return VersionRow(
        rule_id=r["rule_id"],
        version=r["version"],
        spec=spec,
        spec_hash=r["spec_hash"],
        condition_hash=r["condition_hash"],
        readback=r["readback"],
        created_at=r["created_at"],
        created_by=r["created_by"],
        reason=r["reason"],
        draft_id=r["draft_id"],
        name=r["name"],
        description=r["description"],
    )


def shown_name(rule: RuleRow, version: VersionRow) -> str:
    return version.name or rule.name


def shown_description(rule: RuleRow, version: VersionRow) -> str:
    return version.description if version.name else rule.description


def _adoption(r: sqlite3.Row) -> AdoptionRow:
    return AdoptionRow(
        account=r["account"], rule_id=r["rule_id"], version=r["version"], adopted_at=r["adopted_at"]
    )


def _spec_text(spec: CustomRuleSpec) -> str:
    return json.dumps(spec.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


def _state(rule: RuleRow | None, version: int | None = None) -> dict[str, Any] | None:
    """The part of a rule's state a change-log row records (no spec: that is in the version row)."""
    if rule is None:
        return None
    return {
        "name": rule.name,
        "description": rule.description,
        "owner": rule.owner,
        "enabled": rule.enabled,
        "visibility": rule.visibility,
        "status": rule.status,
        "current_version": rule.current_version,
        "published_version": rule.published_version,
        "version": version,
    }


# ---------------------------------------------------------------- reads


def _fetch_rule(c: sqlite3.Connection, rule_id: str) -> RuleRow | None:
    row = c.execute("SELECT * FROM custom_rules WHERE id = ?", (rule_id,)).fetchone()
    return None if row is None else _rule(row)


def get_rule(db: Database, rule_id: str) -> RuleRow | None:
    with db.conn() as c:
        return _fetch_rule(c, rule_id)


def get_version(db: Database, rule_id: str, version: int) -> VersionRow | None:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM custom_rule_versions WHERE rule_id = ? AND version = ?",
            (rule_id, version),
        ).fetchone()
    return None if row is None else _version(row)


def list_versions(db: Database, rule_id: str) -> list[VersionRow]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM custom_rule_versions WHERE rule_id = ? ORDER BY version", (rule_id,)
        ).fetchall()
    return [_version(r) for r in rows]


def list_owned(db: Database, owner: str, *, include_retired: bool = True) -> list[RuleRow]:
    sql = "SELECT * FROM custom_rules WHERE owner = ?"
    if not include_retired:
        sql += " AND status = 'active'"
    with db.conn() as c:
        rows = c.execute(sql + " ORDER BY created_at, id", (owner,)).fetchall()
    return [_rule(r) for r in rows]


def list_public(db: Database) -> list[RuleRow]:
    """Public rules that are active, oldest first (the library every signed-in account may read)."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM custom_rules WHERE visibility = 'public' AND status = 'active' "
            "ORDER BY published_at, id"
        ).fetchall()
    return [_rule(r) for r in rows]


def list_adoptions(db: Database, account: str) -> list[AdoptionRow]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM custom_rule_adoptions WHERE account = ? ORDER BY rule_id", (account,)
        ).fetchall()
    return [_adoption(r) for r in rows]


def adoption_counts(db: Database, rule_ids: Sequence[str]) -> dict[str, int]:
    if not rule_ids:
        return {}
    marks = ",".join("?" for _ in rule_ids)
    with db.conn() as c:
        rows = c.execute(
            f"SELECT rule_id, COUNT(*) AS n FROM custom_rule_adoptions WHERE rule_id IN ({marks}) "
            "GROUP BY rule_id",
            list(rule_ids),
        ).fetchall()
    found = {r["rule_id"]: r["n"] for r in rows}
    return {rid: found.get(rid, 0) for rid in rule_ids}


def count_active_owned(db: Database, owner: str) -> int:
    with db.conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM custom_rules WHERE owner = ? AND status = 'active'", (owner,)
        ).fetchone()[0]


def rules_with_condition(db: Database, condition_hash: str) -> list[tuple[RuleRow, int]]:
    """Every ``(rule, version)`` whose version has this condition hash, any status. Callers pick the
    version that is current, published or adopted and ignore what they may not see."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT r.*, v.version AS matched FROM custom_rule_versions v "
            "JOIN custom_rules r ON r.id = v.rule_id WHERE v.condition_hash = ? "
            "ORDER BY r.created_at, r.id, v.version",
            (condition_hash,),
        ).fetchall()
    return [(_rule(r), r["matched"]) for r in rows]


# ---------------------------------------------------------------- permissions (the router applies them)


def can_view(rule: RuleRow, viewer: str) -> bool:
    """The owner (any status), or any account when the rule is public and active."""
    return rule.owner == viewer or rule.public


def can_change(rule: RuleRow, viewer: str) -> bool:
    """New version, enable or disable, publish, retire: the owner of an active rule."""
    return rule.owner == viewer and rule.status == "active"


def can_unpublish(rule: RuleRow, viewer: str, admins: frozenset[str]) -> bool:
    """The owner, or an administrator, of a rule that is public."""
    return rule.visibility == "public" and (rule.owner == viewer or viewer in admins)


def can_adopt(rule: RuleRow, viewer: str) -> bool:
    return rule.owner != viewer and rule.public


# ---------------------------------------------------------------- writes


def _log(
    c: sqlite3.Connection,
    *,
    rule_id: str,
    at: str,
    actor: str,
    reason: str,
    action: str,
    version: int | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    c.execute(
        'INSERT INTO custom_rule_changes (rule_id, at, actor, reason, action, version, "before", "after") '
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            rule_id,
            at,
            actor,
            reason,
            action,
            version,
            json.dumps(before, ensure_ascii=False),
            json.dumps(after, ensure_ascii=False),
        ),
    )


def _not_found(rule_id: str) -> ApiError:
    return ApiError(
        404, "not_found", "Custom rule not found.", {"kind": "custom_rule", "id": rule_id}
    )


def _retired(rule_id: str) -> ApiError:
    return ApiError(409, "rule_retired", "This custom rule has been retired.", {"rule_id": rule_id})


def _must_exist(c: sqlite3.Connection, rule_id: str) -> RuleRow:
    rule = _fetch_rule(c, rule_id)
    if rule is None:
        raise _not_found(rule_id)
    return rule


def _insert_version(
    c: sqlite3.Connection,
    rule_id: str,
    version: int,
    *,
    spec: CustomRuleSpec,
    readback: str,
    actor: str,
    reason: str,
    at: str,
    draft_id: str | None,
    name: str,
    description: str,
) -> None:
    c.execute(
        "INSERT INTO custom_rule_versions (rule_id, version, spec, spec_hash, condition_hash, "
        "readback, created_at, created_by, reason, draft_id, name, description) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rule_id,
            version,
            _spec_text(spec),
            spec.spec_hash(),
            spec.condition_hash(),
            readback,
            at,
            actor,
            reason,
            draft_id,
            name,
            description,
        ),
    )


def create_rule(
    db: Database,
    *,
    owner: str,
    name: str,
    description: str,
    spec: CustomRuleSpec,
    readback: str,
    reason: str,
    at: datetime,
    draft_id: str | None,
) -> RuleRow:
    """A new private rule at version 1, enabled."""
    stamp = ts(at)
    rule_id = new_rule_id()
    with _lock, db.conn() as c:
        c.execute(
            "INSERT INTO custom_rules (id, owner, name, description, current_version, enabled, "
            "visibility, status, created_at, updated_at) VALUES (?,?,?,?,1,1,'private','active',?,?)",
            (rule_id, owner, name, description, stamp, stamp),
        )
        _insert_version(
            c,
            rule_id,
            1,
            spec=spec,
            readback=readback,
            actor=owner,
            reason=reason,
            at=stamp,
            draft_id=draft_id,
            name=name,
            description=description,
        )
        rule = _must_exist(c, rule_id)
        _log(
            c,
            rule_id=rule_id,
            at=stamp,
            actor=owner,
            reason=reason,
            action="create",
            version=1,
            before=None,
            after=_state(rule, 1),
        )
    return rule


def add_version(
    db: Database,
    rule_id: str,
    *,
    actor: str,
    spec: CustomRuleSpec,
    readback: str,
    reason: str,
    at: datetime,
    draft_id: str | None,
    name: str | None = None,
    description: str | None = None,
) -> RuleRow:
    """A new version of the rule. It is private to its owner whatever the previous version was: a
    public version is never changed, and the published version stays what it was until the owner
    publishes again."""
    stamp = ts(at)
    with _lock, db.conn() as c:
        before = _must_exist(c, rule_id)
        if before.status != "active":
            raise _retired(rule_id)
        version = before.current_version + 1
        new_name = before.name if name is None else name
        new_description = before.description if description is None else description
        _insert_version(
            c,
            rule_id,
            version,
            spec=spec,
            readback=readback,
            actor=actor,
            reason=reason,
            at=stamp,
            draft_id=draft_id,
            name=new_name,
            description=new_description,
        )
        c.execute(
            "UPDATE custom_rules SET current_version = ?, name = ?, description = ?, updated_at = ? "
            "WHERE id = ?",
            (
                version,
                new_name,
                new_description,
                stamp,
                rule_id,
            ),
        )
        after = _must_exist(c, rule_id)
        _log(
            c,
            rule_id=rule_id,
            at=stamp,
            actor=actor,
            reason=reason,
            action="new_version",
            version=version,
            before=_state(before, before.current_version),
            after=_state(after, version),
        )
    return after


def _update(
    db: Database,
    rule_id: str,
    *,
    actor: str,
    reason: str,
    at: datetime,
    change: Callable[[RuleRow, sqlite3.Connection], tuple[str, dict[str, Any]]],
) -> RuleRow:
    """Read the rule, let ``change(rule, connection)`` refuse a state conflict (or make a related write
    in the same transaction) or return ``(action, columns to set)``, apply it and log it, all in one
    transaction."""
    stamp = ts(at)
    with _lock, db.conn() as c:
        before = _must_exist(c, rule_id)
        action, sets = change(before, c)
        values = {**sets, "updated_at": stamp}
        assignments = ", ".join(
            f"{k} = ?" for k in values
        )  # column names are literals of this module
        c.execute(
            f"UPDATE custom_rules SET {assignments} WHERE id = ?", [*values.values(), rule_id]
        )  # noqa: S608
        after = _must_exist(c, rule_id)
        _log(
            c,
            rule_id=rule_id,
            at=stamp,
            actor=actor,
            reason=reason,
            action=action,
            version=after.current_version,
            before=_state(before, before.current_version),
            after=_state(after, after.current_version),
        )
    return after


def set_enabled(
    db: Database, rule_id: str, enabled: bool, *, actor: str, reason: str, at: datetime
) -> RuleRow:
    def change(rule: RuleRow, c: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
        if rule.status != "active":
            raise _retired(rule_id)
        return ("enable" if enabled else "disable"), {"enabled": int(enabled)}

    return _update(db, rule_id, actor=actor, reason=reason, at=at, change=change)


def publish(db: Database, rule_id: str, *, actor: str, reason: str, at: datetime) -> RuleRow:
    """Make the current version public (published_version = current_version)."""
    stamp = ts(at)

    def change(rule: RuleRow, c: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
        if rule.status != "active":
            raise _retired(rule_id)
        if c.execute(
            "SELECT 1 FROM custom_rule_changes WHERE rule_id = ? AND action = 'admin_unpublish' LIMIT 1",
            (rule_id,),
        ).fetchone():
            raise ApiError(
                409,
                "withdrawn_by_admin",
                "An administrator took this rule back from the public list, so it cannot be made "
                "public again. It still runs for you, and you can save the idea as a new rule.",
                {"rule_id": rule_id},
            )
        if rule.visibility == "public" and rule.published_version == rule.current_version:
            raise ApiError(
                409,
                "already_public",
                "This version of the rule is already public.",
                {"rule_id": rule_id, "version": rule.current_version},
            )
        return "publish", {
            "visibility": "public",
            "published_version": rule.current_version,
            "published_at": stamp,
        }

    return _update(db, rule_id, actor=actor, reason=reason, at=at, change=change)


def unpublish(
    db: Database, rule_id: str, *, actor: str, reason: str, at: datetime, as_admin: bool
) -> RuleRow:
    """Take the rule back to private. Every adoption ends (each is logged ``unadopt``), so nobody runs
    it again without opting in again: nothing public changes anyone's results until they adopt. Logged
    ``admin_unpublish`` when an administrator who is not the owner does it; the owner cannot then make
    the rule public again."""
    stamp = ts(at)

    def change(rule: RuleRow, c: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
        if rule.visibility != "public":
            raise ApiError(
                409, "not_public", "This custom rule is not public.", {"rule_id": rule_id}
            )
        action = "admin_unpublish" if as_admin and actor != rule.owner else "unpublish"
        for a in c.execute(
            "SELECT * FROM custom_rule_adoptions WHERE rule_id = ?", (rule_id,)
        ).fetchall():
            _log(
                c,
                rule_id=rule_id,
                at=stamp,
                actor=actor,
                reason="the rule was taken back, so this adoption ended",
                action="unadopt",
                version=a["version"],
                before={"version": a["version"], "account": a["account"]},
                after=None,
            )
        c.execute("DELETE FROM custom_rule_adoptions WHERE rule_id = ?", (rule_id,))
        return action, {"visibility": "private", "published_version": None, "published_at": None}

    return _update(db, rule_id, actor=actor, reason=reason, at=at, change=change)


def retire(db: Database, rule_id: str, *, actor: str, reason: str, at: datetime) -> RuleRow:
    """Retire the rule: it stops running for everybody, stays listed to its owner and keeps every
    version, so a past run is still explainable."""
    stamp = ts(at)

    def change(rule: RuleRow, c: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
        if rule.status != "active":
            raise _retired(rule_id)
        return "retire", {
            "status": "retired",
            "enabled": 0,
            "visibility": "private",
            "published_version": None,
            "published_at": None,
            "retired_at": stamp,
        }

    return _update(db, rule_id, actor=actor, reason=reason, at=at, change=change)


def adopt(db: Database, rule_id: str, account: str, *, at: datetime) -> AdoptionRow:
    """Opt in to a public rule, pinned to its published version. Adopting again after the owner
    published a newer version moves the pin."""
    stamp = ts(at)
    with _lock, db.conn() as c:
        rule = _must_exist(c, rule_id)
        if rule.status != "active":
            raise _retired(rule_id)
        if rule.visibility != "public" or rule.published_version is None:
            raise ApiError(
                409, "not_public", "This custom rule is not public.", {"rule_id": rule_id}
            )
        if rule.owner == account:
            raise ApiError(
                409,
                "own_rule",
                "You cannot adopt your own rule; it already runs.",
                {"rule_id": rule_id},
            )
        existing = c.execute(
            "SELECT * FROM custom_rule_adoptions WHERE account = ? AND rule_id = ?",
            (account, rule_id),
        ).fetchone()
        if existing is not None and existing["version"] == rule.published_version:
            raise ApiError(
                409,
                "already_adopted",
                "You have already adopted this version of the rule.",
                {"rule_id": rule_id, "version": existing["version"]},
            )
        c.execute(
            "INSERT OR REPLACE INTO custom_rule_adoptions (account, rule_id, version, adopted_at) "
            "VALUES (?,?,?,?)",
            (account, rule_id, rule.published_version, stamp),
        )
        _log(
            c,
            rule_id=rule_id,
            at=stamp,
            actor=account,
            reason="adopted" if existing is None else "adopted the newer version",
            action="adopt",
            version=rule.published_version,
            before=None if existing is None else {"version": existing["version"]},
            after={"version": rule.published_version},
        )
        return AdoptionRow(account, rule_id, rule.published_version, stamp)


def unadopt(db: Database, rule_id: str, account: str, *, at: datetime) -> None:
    stamp = ts(at)
    with _lock, db.conn() as c:
        existing = c.execute(
            "SELECT * FROM custom_rule_adoptions WHERE account = ? AND rule_id = ?",
            (account, rule_id),
        ).fetchone()
        if existing is None:
            raise ApiError(
                409, "not_adopted", "You have not adopted this rule.", {"rule_id": rule_id}
            )
        c.execute(
            "DELETE FROM custom_rule_adoptions WHERE account = ? AND rule_id = ?",
            (account, rule_id),
        )
        _log(
            c,
            rule_id=rule_id,
            at=stamp,
            actor=account,
            reason="stopped using it",
            action="unadopt",
            version=existing["version"],
            before={"version": existing["version"]},
            after=None,
        )


def changes(
    db: Database,
    *,
    viewer: str,
    admin: bool,
    rule_id: str | None = None,
    limit: int = CHANGES_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """The change log, newest first. An account sees the rows of the rules it owns (but not who adopted
    them) and its own adoptions; an administrator sees all."""
    limit = max(1, min(limit, CHANGES_MAX_LIMIT))
    where: list[str] = []
    args: list[Any] = []
    if not admin:
        marks = ",".join("?" for _ in _ADOPTION_ACTIONS)
        where.append(
            "((rule_id IN (SELECT id FROM custom_rules WHERE owner = ?) "
            f"AND action NOT IN ({marks})) OR actor = ?)"
        )
        args += [viewer, *_ADOPTION_ACTIONS, viewer]
    if rule_id is not None:
        where.append("rule_id = ?")
        args.append(rule_id)
    sql = 'SELECT id, rule_id, at, actor, reason, action, version, "before", "after" FROM custom_rule_changes'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db.conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [
        {
            "id": r["id"],
            "rule_id": r["rule_id"],
            "at": r["at"],
            "actor": r["actor"],
            "reason": r["reason"],
            "action": r["action"],
            "version": r["version"],
            "before": json.loads(r["before"]),
            "after": json.loads(r["after"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------- what an analysis runs


def _loaded(rule: RuleRow, version: VersionRow, origin: str) -> LoadedCustomRule | None:
    if not RULE_ID_RE.match(rule.id):  # a hand-edited row must not give a flag a built-in rule id
        log.warning("a stored custom rule has an id outside cus_<10 hex>; not loaded")
        return None
    if version.spec is None:
        log.warning("custom rule %s v%s no longer validates; not loaded", rule.id, version.version)
        return None
    return LoadedCustomRule(
        rule_id=rule.id,
        version=version.version,
        spec=version.spec,
        spec_hash=version.spec_hash,
        name=shown_name(rule, version),
        author=rule.owner,
        origin=origin,  # type: ignore[arg-type]
        readback=version.readback,
    )


def load_rule_set(db: Database, account: str) -> list[LoadedCustomRule]:
    """The custom rules an analysis started by ``account`` runs: its own enabled, active rules at their
    current version, plus the public rules it adopted, at the version it adopted. Sorted by rule id.
    A stored spec that no longer validates is left out (logged by id and version only). Another
    account's private rule is never loaded."""
    picked: list[tuple[RuleRow, int, str]] = []
    with db.conn() as c:
        own = c.execute(
            "SELECT * FROM custom_rules WHERE owner = ? AND status = 'active' AND enabled = 1 "
            "ORDER BY id LIMIT ?",
            (account, MAX_OWN_LOADED),
        ).fetchall()
        picked += [(_rule(r), r["current_version"], "own") for r in own]
        adopted = c.execute(
            "SELECT r.*, a.version AS pinned FROM custom_rule_adoptions a "
            "JOIN custom_rules r ON r.id = a.rule_id "
            "WHERE a.account = ? AND r.visibility = 'public' AND r.status = 'active' "
            "AND r.owner != ? ORDER BY r.id LIMIT ?",
            (account, account, MAX_ADOPTED_LOADED),
        ).fetchall()
        picked += [(_rule(r), r["pinned"], "adopted") for r in adopted]
        versions = {
            (rule.id, version): c.execute(
                "SELECT * FROM custom_rule_versions WHERE rule_id = ? AND version = ?",
                (rule.id, version),
            ).fetchone()
            for rule, version, _ in picked
        }
    loaded: list[LoadedCustomRule] = []
    for rule, version, origin in picked:
        row = versions[(rule.id, version)]
        if row is None:
            log.warning("custom rule %s v%s has no stored version; not loaded", rule.id, version)
            continue
        item = _loaded(rule, _version(row), origin)
        if item is not None:
            loaded.append(item)
    return sorted(loaded, key=lambda r: r.rule_id)
