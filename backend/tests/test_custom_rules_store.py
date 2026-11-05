"""Storage of custom rules (app/custom_rules/store.py): the state machine, the change log, privacy."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import Database
from app.core.errors import ApiError
from app.custom_rules import store
from app.custom_rules.readback import readback
from app.custom_rules.spec import CustomRuleSpec
from tests.custom_rules_helpers import spec_of, t1, t3

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)


def at(minutes: int = 0) -> datetime:
    return T0 + timedelta(minutes=minutes)


def total_gt(n: str = "100000", severity: str = "low") -> CustomRuleSpec:
    return spec_of(t1("invoice.total", "gt", n), severity)


def make(
    db: Database,
    owner: str = "alice",
    spec: CustomRuleSpec | None = None,
    name: str = "Big invoices",
):  # noqa: ANN201
    spec = spec or total_gt()
    return store.create_rule(
        db,
        owner=owner,
        name=name,
        description="",
        spec=spec,
        readback=readback(spec),
        reason="loan desk asked",
        at=at(),
        draft_id="crd_aaaaaaaaaaaa",
    )


def code_of(call) -> str:  # noqa: ANN001
    with pytest.raises(ApiError) as caught:
        call()
    return caught.value.code


@pytest.fixture
def db(mem_db: Database) -> Database:
    return mem_db


# ------------------------------------------------------------------ create and read


def test_a_new_rule_is_private_enabled_and_at_version_one(db: Database) -> None:
    rule = make(db)
    assert store.RuleRow  # the dataclass is public
    assert rule.id.startswith("cus_") and rule.owner == "alice"
    assert (rule.current_version, rule.enabled, rule.visibility, rule.status) == (
        1,
        True,
        "private",
        "active",
    )
    assert rule.published_version is None and rule.retired_at is None
    assert store.get_rule(db, rule.id) == rule
    version = store.get_version(db, rule.id, 1)
    assert version is not None and version.spec == total_gt()
    assert (
        version.spec_hash == total_gt().spec_hash()
        and version.condition_hash == total_gt().condition_hash()
    )
    assert version.readback == readback(total_gt()) and version.created_by == "alice"
    assert (version.reason, version.draft_id) == ("loan desk asked", "crd_aaaaaaaaaaaa")
    assert (
        store.get_rule(db, "cus_ffffffffff") is None and store.get_version(db, rule.id, 9) is None
    )


def test_create_writes_a_log_row(db: Database) -> None:
    rule = make(db)
    rows = store.changes(db, viewer="alice", admin=False)
    assert [(r["action"], r["actor"], r["rule_id"], r["version"]) for r in rows] == [
        ("create", "alice", rule.id, 1)
    ]
    assert rows[0]["before"] is None and rows[0]["after"]["visibility"] == "private"
    assert rows[0]["reason"] == "loan desk asked"


def test_lists_and_counts(db: Database) -> None:
    a, b = make(db), make(db, spec=total_gt("5"), name="Small")
    other = make(db, owner="bob", spec=total_gt("7"))
    assert {r.id for r in store.list_owned(db, "alice")} == {a.id, b.id}
    assert store.count_active_owned(db, "alice") == 2
    store.retire(db, b.id, actor="alice", reason="not needed", at=at(1))
    assert store.count_active_owned(db, "alice") == 1
    assert [r.id for r in store.list_owned(db, "alice", include_retired=False)] == [a.id]
    assert {r.id for r in store.list_owned(db, "alice")} == {a.id, b.id}
    assert store.list_owned(db, "nobody") == []
    assert store.list_public(db) == []
    assert other.owner == "bob"


def test_rules_with_condition_finds_every_version(db: Database) -> None:
    rule = make(db)
    store.add_version(
        db,
        rule.id,
        actor="alice",
        spec=total_gt("5"),
        readback="x",
        reason="lower",
        at=at(1),
        draft_id=None,
    )
    hits = store.rules_with_condition(db, total_gt().condition_hash())
    assert [(r.id, v) for r, v in hits] == [(rule.id, 1)]
    assert [
        (r.id, v) for r, v in store.rules_with_condition(db, total_gt("5").condition_hash())
    ] == [(rule.id, 2)]
    assert store.rules_with_condition(db, "0" * 64) == []


# ------------------------------------------------------------------ versions


def test_a_new_version_moves_the_current_one_and_keeps_the_old(db: Database) -> None:
    rule = make(db)
    updated = store.add_version(
        db,
        rule.id,
        actor="alice",
        spec=total_gt("5", "medium"),
        readback="new readback",
        reason="tighten it",
        at=at(5),
        draft_id="crd_bbbbbbbbbbbb",
        name="Smaller invoices",
        description="below the old limit",
    )
    assert updated.current_version == 2 and updated.name == "Smaller invoices"
    assert updated.description == "below the old limit" and updated.updated_at > rule.updated_at
    versions = store.list_versions(db, rule.id)
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].spec == total_gt() and versions[1].spec == total_gt("5", "medium")
    kept = store.add_version(
        db,
        rule.id,
        actor="alice",
        spec=total_gt("6"),
        readback="r",
        reason="again",
        at=at(6),
        draft_id=None,
    )
    assert kept.name == "Smaller invoices" and kept.current_version == 3


def test_a_retired_rule_takes_no_new_version(db: Database) -> None:
    rule = make(db)
    store.retire(db, rule.id, actor="alice", reason="done with it", at=at(1))
    assert (
        code_of(
            lambda: store.add_version(
                db,
                rule.id,
                actor="alice",
                spec=total_gt("2"),
                readback="r",
                reason="x" * 5,
                at=at(2),
                draft_id=None,
            )
        )
        == "rule_retired"
    )
    assert (
        code_of(
            lambda: store.add_version(
                db,
                "cus_ffffffffff",
                actor="alice",
                spec=total_gt("2"),
                readback="r",
                reason="x" * 5,
                at=at(2),
                draft_id=None,
            )
        )
        == "not_found"
    )


def test_versions_are_immutable_and_never_deleted(db: Database) -> None:
    rule = make(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"), db.conn() as c:
        c.execute("UPDATE custom_rule_versions SET spec = '{}' WHERE rule_id = ?", (rule.id,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"), db.conn() as c:
        c.execute("DELETE FROM custom_rule_versions WHERE rule_id = ?", (rule.id,))
    assert store.get_version(db, rule.id, 1).spec == total_gt()  # type: ignore[union-attr]


def test_the_change_log_is_append_only(db: Database) -> None:
    make(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"), db.conn() as c:
        c.execute("UPDATE custom_rule_changes SET reason = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"), db.conn() as c:
        c.execute("DELETE FROM custom_rule_changes")


# ------------------------------------------------------------------ enable, publish, retire


def test_enable_and_disable_are_logged(db: Database) -> None:
    rule = make(db)
    off = store.set_enabled(db, rule.id, False, actor="alice", reason="pause it", at=at(1))
    assert off.enabled is False
    on = store.set_enabled(db, rule.id, True, actor="alice", reason="resume it", at=at(2))
    assert on.enabled is True
    actions = [r["action"] for r in store.changes(db, viewer="alice", admin=False)]
    assert actions == ["enable", "disable", "create"]


def test_publish_makes_the_current_version_public(db: Database) -> None:
    rule = make(db)
    pub = store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    assert (pub.visibility, pub.published_version, pub.published_at) == (
        "public",
        1,
        pub.updated_at,
    )
    assert pub.public and [r.id for r in store.list_public(db)] == [rule.id]
    assert (
        code_of(lambda: store.publish(db, rule.id, actor="alice", reason="again!", at=at(2)))
        == "already_public"
    )


def test_a_public_rule_gets_a_new_version_without_changing_what_was_published(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    new = store.add_version(
        db,
        rule.id,
        actor="alice",
        spec=total_gt("9"),
        readback="r",
        reason="edit it",
        at=at(2),
        draft_id=None,
    )
    assert (new.current_version, new.published_version, new.visibility) == (2, 1, "public")
    published = store.get_version(db, rule.id, 1)
    assert (
        published is not None and published.spec == total_gt()
    )  # the published version never changed
    again = store.publish(db, rule.id, actor="alice", reason="share v2", at=at(3))
    assert again.published_version == 2


def test_unpublish_takes_it_back_to_private(db: Database) -> None:
    rule = make(db)
    assert (
        code_of(
            lambda: store.unpublish(
                db, rule.id, actor="alice", reason="nothing", at=at(1), as_admin=False
            )
        )
        == "not_public"
    )
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    back = store.unpublish(
        db, rule.id, actor="alice", reason="changed my mind", at=at(2), as_admin=False
    )
    assert (back.visibility, back.published_version, back.published_at) == ("private", None, None)
    assert store.list_public(db) == []
    assert store.changes(db, viewer="alice", admin=False)[0]["action"] == "unpublish"


def test_an_admin_unpublish_is_logged_as_such(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.unpublish(db, rule.id, actor="root", reason="not appropriate", at=at(2), as_admin=True)
    row = store.changes(db, viewer="root", admin=True)[0]
    assert (row["action"], row["actor"]) == ("admin_unpublish", "root")


def test_the_owner_unpublishing_as_an_admin_is_an_ordinary_unpublish(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.unpublish(db, rule.id, actor="alice", reason="and back again", at=at(4), as_admin=True)
    assert store.changes(db, viewer="alice", admin=False)[0]["action"] == "unpublish"
    store.publish(db, rule.id, actor="alice", reason="share again", at=at(5))  # still allowed


def test_retire_keeps_every_version_and_stops_everything(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rule.id, "bob", at=at(2))
    gone = store.retire(db, rule.id, actor="alice", reason="obsolete now", at=at(3))
    assert (gone.status, gone.enabled, gone.visibility, gone.published_version) == (
        "retired",
        False,
        "private",
        None,
    )
    assert gone.retired_at is not None
    assert [v.version for v in store.list_versions(db, rule.id)] == [1]
    assert store.list_public(db) == []
    assert store.load_rule_set(db, "alice") == [] and store.load_rule_set(db, "bob") == []
    assert (
        code_of(lambda: store.retire(db, rule.id, actor="alice", reason="twice!", at=at(4)))
        == "rule_retired"
    )
    for call in (
        lambda: store.set_enabled(db, rule.id, True, actor="alice", reason="reopen", at=at(5)),
        lambda: store.publish(db, rule.id, actor="alice", reason="reshare", at=at(5)),
    ):
        assert code_of(call) == "rule_retired"
    assert code_of(lambda: store.adopt(db, rule.id, "carol", at=at(5))) == "rule_retired"


# ------------------------------------------------------------------ adopt


def test_adopt_pins_the_published_version(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    got = store.adopt(db, rule.id, "bob", at=at(2))
    assert (got.account, got.rule_id, got.version) == ("bob", rule.id, 1)
    assert store.list_adoptions(db, "bob") == [got] and store.list_adoptions(db, "carol") == []
    assert store.adoption_counts(db, [rule.id, "cus_ffffffffff"]) == {
        rule.id: 1,
        "cus_ffffffffff": 0,
    }
    assert store.adoption_counts(db, []) == {}
    assert code_of(lambda: store.adopt(db, rule.id, "bob", at=at(3))) == "already_adopted"


def test_adopt_conflicts(db: Database) -> None:
    rule = make(db)
    assert code_of(lambda: store.adopt(db, rule.id, "bob", at=at(1))) == "not_public"
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    assert code_of(lambda: store.adopt(db, rule.id, "alice", at=at(2))) == "own_rule"
    assert code_of(lambda: store.adopt(db, "cus_ffffffffff", "bob", at=at(2))) == "not_found"


def test_readopting_after_a_newer_publish_moves_the_pin(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rule.id, "bob", at=at(2))
    store.add_version(
        db,
        rule.id,
        actor="alice",
        spec=total_gt("9"),
        readback="r",
        reason="edit it",
        at=at(3),
        draft_id=None,
    )
    assert store.list_adoptions(db, "bob")[0].version == 1  # nothing changes for bob yet
    store.publish(db, rule.id, actor="alice", reason="share v2", at=at(4))
    assert store.list_adoptions(db, "bob")[0].version == 1
    moved = store.adopt(db, rule.id, "bob", at=at(5))
    assert moved.version == 2 and store.list_adoptions(db, "bob")[0].version == 2


def test_unadopt(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    assert code_of(lambda: store.unadopt(db, rule.id, "bob", at=at(2))) == "not_adopted"
    store.adopt(db, rule.id, "bob", at=at(2))
    store.unadopt(db, rule.id, "bob", at=at(3))
    assert store.list_adoptions(db, "bob") == []
    assert [r["action"] for r in store.changes(db, viewer="bob", admin=False)] == [
        "unadopt",
        "adopt",
    ]


# ------------------------------------------------------------------ permissions


def test_permission_predicates(db: Database) -> None:
    rule = make(db)
    admins = frozenset({"root"})
    assert (
        store.can_view(rule, "alice")
        and not store.can_view(rule, "bob")
        and not store.can_view(rule, "root")
    )
    assert (
        store.can_change(rule, "alice")
        and not store.can_change(rule, "bob")
        and not store.can_change(rule, "root")
    )
    assert not store.can_adopt(rule, "bob")  # private
    assert not store.can_unpublish(rule, "alice", admins)  # not public
    pub = store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    assert store.can_view(pub, "bob") and store.can_view(pub, "root")
    assert not store.can_change(pub, "bob") and not store.can_change(pub, "root")
    assert (
        store.can_adopt(pub, "bob")
        and store.can_adopt(pub, "root")
        and not store.can_adopt(pub, "alice")
    )
    assert store.can_unpublish(pub, "alice", admins) and store.can_unpublish(pub, "root", admins)
    assert not store.can_unpublish(pub, "bob", admins)
    retired = store.retire(db, rule.id, actor="alice", reason="obsolete now", at=at(2))
    assert store.can_view(retired, "alice") and not store.can_view(retired, "bob")
    assert not store.can_change(retired, "alice") and not store.can_adopt(retired, "bob")


# ------------------------------------------------------------------ change log visibility


def test_changes_visibility(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rule.id, "bob", at=at(2))
    bobs = make(db, owner="bob", spec=total_gt("3"))
    alice_view = [r["action"] for r in store.changes(db, viewer="alice", admin=False)]
    assert alice_view == ["publish", "create"]  # not who adopted her rule
    bob_view = store.changes(db, viewer="bob", admin=False)
    assert [(r["action"], r["rule_id"]) for r in bob_view] == [
        ("create", bobs.id),
        ("adopt", rule.id),
    ]
    assert store.changes(db, viewer="carol", admin=False) == []
    everything = store.changes(db, viewer="root", admin=True)
    assert len(everything) == 4
    only = store.changes(db, viewer="root", admin=True, rule_id=rule.id)
    assert {r["rule_id"] for r in only} == {rule.id}
    assert store.changes(db, viewer="root", admin=True, limit=1)[0]["id"] == everything[0]["id"]
    assert (
        len(store.changes(db, viewer="root", admin=True, limit=0)) == 1
    )  # limit is clamped to 1..200
    assert [r["id"] for r in everything] == sorted((r["id"] for r in everything), reverse=True)


# ------------------------------------------------------------------ what an analysis loads


def test_load_rule_set_own_enabled_current_version(db: Database) -> None:
    a = make(db)
    b = make(db, spec=spec_of(t3("receipt.receipt_number"), "info"), name="No receipt number")
    store.add_version(
        db,
        a.id,
        actor="alice",
        spec=total_gt("5"),
        readback="v2 readback",
        reason="lower",
        at=at(1),
        draft_id=None,
    )
    loaded = store.load_rule_set(db, "alice")
    assert [r.rule_id for r in loaded] == sorted([a.id, b.id])
    mine = next(r for r in loaded if r.rule_id == a.id)
    assert (mine.version, mine.origin, mine.author, mine.name) == (
        2,
        "own",
        "alice",
        "Big invoices",
    )
    assert mine.spec == total_gt("5") and mine.readback == "v2 readback"
    assert mine.spec_hash == total_gt("5").spec_hash()
    store.set_enabled(db, b.id, False, actor="alice", reason="pause it", at=at(2))
    assert [r.rule_id for r in store.load_rule_set(db, "alice")] == [a.id]


def test_another_accounts_private_rule_is_never_loaded(db: Database) -> None:
    rule = make(db)
    assert store.load_rule_set(db, "bob") == []
    assert store.load_rule_set(db, "root") == []
    assert store.load_rule_set(db, "") == []
    assert [r.rule_id for r in store.load_rule_set(db, "alice")] == [rule.id]


def test_public_rules_run_only_for_accounts_that_adopted_them(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    assert (
        store.load_rule_set(db, "bob") == []
    )  # nothing public changes anyone's results until they adopt
    store.adopt(db, rule.id, "bob", at=at(2))
    got = store.load_rule_set(db, "bob")
    assert [(r.rule_id, r.origin, r.author, r.version) for r in got] == [
        (rule.id, "adopted", "alice", 1)
    ]
    assert store.load_rule_set(db, "carol") == []


def test_adopters_run_the_pinned_version_while_the_owner_runs_the_current_one(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rule.id, "bob", at=at(2))
    store.add_version(
        db,
        rule.id,
        actor="alice",
        spec=total_gt("9"),
        readback="r",
        reason="edit it",
        at=at(3),
        draft_id=None,
    )
    assert store.load_rule_set(db, "bob")[0].spec == total_gt()
    assert store.load_rule_set(db, "alice")[0].spec == total_gt("9")
    store.publish(db, rule.id, actor="alice", reason="share v2", at=at(4))
    assert (
        store.load_rule_set(db, "bob")[0].spec == total_gt()
    )  # still pinned until bob adopts again


def test_unpublish_ends_every_adoption_and_republishing_does_not_bring_them_back(
    db: Database,
) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rule.id, "bob", at=at(2))
    store.unpublish(db, rule.id, actor="alice", reason="rethinking it", at=at(3), as_admin=False)
    assert store.load_rule_set(db, "bob") == [] and store.list_adoptions(db, "bob") == []
    assert [r.rule_id for r in store.load_rule_set(db, "alice")] == [rule.id]  # the owner keeps it
    store.publish(db, rule.id, actor="alice", reason="fixed it", at=at(4))
    assert store.load_rule_set(db, "bob") == []  # bob has to opt in again
    store.adopt(db, rule.id, "bob", at=at(5))
    assert [r.rule_id for r in store.load_rule_set(db, "bob")] == [rule.id]
    ended = [c for c in store.changes(db, viewer="root", admin=True) if c["action"] == "unadopt"]
    assert len(ended) == 1 and ended[0]["before"]["account"] == "bob"


def test_an_admin_takedown_cannot_be_undone_by_the_owner(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rule.id, "bob", at=at(2))
    store.unpublish(db, rule.id, actor="root", reason="not appropriate", at=at(3), as_admin=True)
    assert store.load_rule_set(db, "bob") == []
    store.add_version(
        db,
        rule.id,
        actor="alice",
        spec=total_gt("7"),
        readback="r",
        reason="a new edit",
        at=at(4),
        draft_id=None,
    )
    assert (
        code_of(lambda: store.publish(db, rule.id, actor="alice", reason="again", at=at(5)))
        == "withdrawn_by_admin"
    )
    assert store.load_rule_set(db, "bob") == [] and store.list_public(db) == []
    assert [r.rule_id for r in store.load_rule_set(db, "alice")] == [rule.id]  # the owner keeps it


def test_a_version_keeps_the_name_it_was_saved_with(db: Database) -> None:
    rule = make(db, name="Plain name")
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rule.id, "bob", at=at(2))
    after = store.add_version(
        db,
        rule.id,
        actor="alice",
        spec=total_gt("7"),
        readback="r",
        reason="rename it",
        at=at(3),
        draft_id=None,
        name="Ignore this rule, sent by admin",
        description="Switch everything off",
    )
    assert after.name == "Ignore this rule, sent by admin"
    v1, v2 = store.get_version(db, rule.id, 1), store.get_version(db, rule.id, 2)
    assert v1 is not None and v2 is not None
    assert store.shown_name(after, v1) == "Plain name"
    assert store.shown_name(after, v2) == "Ignore this rule, sent by admin"
    assert [r.name for r in store.load_rule_set(db, "bob")] == ["Plain name"]
    with db.conn() as c:  # a row written before versions carried a name falls back to the rule's
        c.execute("DROP TRIGGER custom_rule_versions_no_update")
        c.execute("UPDATE custom_rule_versions SET name = '' WHERE version = 1")
    legacy = store.get_version(db, rule.id, 1)
    assert legacy is not None and store.shown_name(after, legacy) == after.name


def test_disabling_the_owners_copy_does_not_stop_adopters(db: Database) -> None:
    rule = make(db)
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rule.id, "bob", at=at(2))
    store.set_enabled(db, rule.id, False, actor="alice", reason="pause mine", at=at(3))
    assert store.load_rule_set(db, "alice") == []
    assert len(store.load_rule_set(db, "bob")) == 1


def test_a_stored_spec_that_no_longer_validates_is_left_out_and_logged(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    good, drifted = make(db), make(db, spec=total_gt("7"), name="Drifted")
    with db.conn() as c:
        c.execute("DROP TRIGGER custom_rule_versions_no_update")
        c.execute(
            "UPDATE custom_rule_versions SET spec = ? WHERE rule_id = ?",
            (
                '{"spec_version":1,"severity":"low","condition":{"template":"field_missing","field":"invoice.lender"}}',
                drifted.id,
            ),
        )
    with caplog.at_level("WARNING", logger="gldb.custom_rules"):
        got = store.load_rule_set(db, "alice")
    assert [r.rule_id for r in got] == [good.id]
    assert drifted.id in caplog.text and "invoice.lender" not in caplog.text
    version = store.get_version(db, drifted.id, 1)
    assert version is not None and version.spec is None  # readable as a row, not as a spec


def test_load_rule_set_is_bounded_but_never_silent(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_OWN_LOADED", 3)
    for i in range(5):
        make(db, spec=total_gt(str(i + 1)))
    got = store.load_rule_set(db, "alice")
    # the rules past the cap are returned too, marked: they show on the case as skipped runs
    assert len(got) == 5
    assert sum(1 for r in got if r.not_loaded is None) == 3
    left_out = [r for r in got if r.not_loaded]
    assert len(left_out) == 2 and "at most 3" in left_out[0].not_loaded  # type: ignore[operator]


def test_owners_are_matched_exactly(db: Database) -> None:
    make(db, owner="alice")
    assert store.load_rule_set(db, "Alice") == []  # accounts are lower-cased before they get here


# ================================================================ second review: limits, takedown, retire


def test_adopting_past_the_loaded_cap_is_refused_not_dropped(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_ADOPTED_LOADED", 2)
    rules = [make(db, owner="alice", spec=total_gt(str(10 + i))) for i in range(3)]
    for r in rules:
        store.publish(db, r.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rules[0].id, "bob", at=at(2))
    store.adopt(db, rules[1].id, "bob", at=at(3))
    assert code_of(lambda: store.adopt(db, rules[2].id, "bob", at=at(4))) == "adoption_limit"
    assert len(store.list_adoptions(db, "bob")) == 2
    # moving the pin of a rule already adopted is not a new adoption
    store.add_version(
        db,
        rules[0].id,
        actor="alice",
        spec=total_gt("99"),
        readback="r",
        reason="newer",
        at=at(5),
        draft_id=None,
    )
    store.publish(db, rules[0].id, actor="alice", reason="newer", at=at(6))
    assert store.adopt(db, rules[0].id, "bob", at=at(7)).version == 2
    store.unadopt(db, rules[1].id, "bob", at=at(8))
    store.adopt(db, rules[2].id, "bob", at=at(9))  # room again


def test_every_adopted_rule_is_listed_even_past_the_cap(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules = [make(db, owner="alice", spec=total_gt(str(10 + i))) for i in range(3)]
    for r in rules:
        store.publish(db, r.id, actor="alice", reason="share it", at=at(1))
        store.adopt(db, r.id, "bob", at=at(2))
    monkeypatch.setattr(store, "MAX_ADOPTED_LOADED", 1)  # e.g. a hand-edited database
    got = store.load_rule_set(db, "bob")
    assert len(got) == 3 and sum(1 for r in got if r.not_loaded) == 2
    assert all("at most 1 adopted" in r.not_loaded for r in got if r.not_loaded)  # type: ignore[operator]


def test_the_account_limit_in_the_settings_cannot_exceed_what_an_analysis_loads() -> None:
    from app.core.config import Settings, SettingsError

    assert store.MAX_OWN_LOADED == 100
    Settings(_env_file=None, custom_rules_max_per_account=100).validate_startup()
    with pytest.raises(SettingsError, match="MAX_PER_ACCOUNT must be <= 100"):
        Settings(_env_file=None, custom_rules_max_per_account=101).validate_startup()


def test_an_admin_takedown_cannot_be_undone_by_saving_the_same_idea_as_a_new_rule(
    db: Database,
) -> None:
    first = make(db, owner="alice")
    store.publish(db, first.id, actor="alice", reason="share it", at=at(1))
    store.unpublish(db, first.id, actor="root", reason="not appropriate", at=at(2), as_admin=True)
    assert code_of(
        lambda: store.publish(db, first.id, actor="alice", reason="again", at=at(3))
    ) == ("withdrawn_by_admin")
    store.retire(db, first.id, actor="alice", reason="start over", at=at(4))
    # the same idea, saved again by the author or by anybody else, at any severity
    again = make(db, owner="alice", name="Same idea")
    other = make(db, owner="carol", spec=total_gt(severity="medium"), name="Carol's copy")
    for rule in (again, other):
        assert (
            code_of(
                lambda rule=rule: store.publish(
                    db, rule.id, actor=rule.owner, reason="sharing", at=at(5)
                )
            )
            == "withdrawn_by_admin"
        )
    # private use is untouched, and a different condition can still be shared
    store.set_enabled(db, again.id, True, actor="alice", reason="keep it on", at=at(6))
    assert [r.rule_id for r in store.load_rule_set(db, "alice")] == [again.id]
    different = make(db, owner="alice", spec=total_gt("5"), name="Different")
    store.publish(db, different.id, actor="alice", reason="sharing", at=at(7))


def test_an_unpublish_by_the_owner_does_not_bar_the_idea(db: Database) -> None:
    first = make(db, owner="alice")
    store.publish(db, first.id, actor="alice", reason="share it", at=at(1))
    store.unpublish(db, first.id, actor="alice", reason="back to private", at=at(2), as_admin=False)
    store.retire(db, first.id, actor="alice", reason="start over", at=at(3))
    again = make(db, owner="alice", name="Same idea")
    store.publish(db, again.id, actor="alice", reason="sharing", at=at(4))


def test_retire_ends_and_logs_every_adoption(db: Database) -> None:
    rule = make(db, owner="alice")
    store.publish(db, rule.id, actor="alice", reason="share it", at=at(1))
    store.adopt(db, rule.id, "bob", at=at(2))
    store.adopt(db, rule.id, "carol", at=at(3))
    assert store.adoption_counts(db, [rule.id]) == {rule.id: 2}
    store.retire(db, rule.id, actor="alice", reason="no longer needed", at=at(4))
    assert store.adoption_counts(db, [rule.id]) == {rule.id: 0}
    assert store.list_adoptions(db, "bob") == [] and store.load_rule_set(db, "bob") == []
    ended = [
        c
        for c in store.changes(db, viewer="root", admin=True, rule_id=rule.id)
        if c["action"] == "unadopt"
    ]
    assert sorted(c["before"]["account"] for c in ended) == ["bob", "carol"]
    assert all(c["actor"] == "alice" for c in ended)
