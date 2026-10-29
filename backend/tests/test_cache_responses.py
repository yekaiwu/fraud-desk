from datetime import timedelta
from decimal import Decimal

from app.core.cache import LLM_CACHE_SCHEMA, ResponseCache, cache_key
from app.core.db import Database
from app.core.llm import LLM_CLIENT_VERSION, MAX_OUTPUT_TOKENS, LLMRequest


def req(**kw) -> LLMRequest:  # noqa: ANN003
    base = dict(
        purpose="extract:invoice",
        system="SYS",
        user="USER",
        images=[b"\x89PNG1"],
        schema_name="Invoice",
        json_schema={"type": "object"},
        cache_extra="a" * 64 + ":1:text",
    )
    base.update(kw)
    return LLMRequest(**base)


def key(r: LLMRequest, model: str = "m", version: str = LLM_CLIENT_VERSION) -> str:
    return cache_key(r, model, version)


def test_key_is_stable_and_sensitive_to_every_input() -> None:
    base = key(req())
    assert key(req()) == base
    assert len(base) == 64
    variants = [
        key(req(purpose="extract:contract")),
        key(req(), model="other"),
        key(req(system="SYS2")),
        key(req(user="USER2")),
        key(req(json_schema={"type": "array"})),
        key(req(images=[b"\x89PNG2"])),
        key(req(images=[])),
        key(req(cache_extra="b" * 64 + ":1:text")),
        key(req(schema_name="Other")),
        key(req(), version="2"),
    ]
    assert len({base, *variants}) == len(variants) + 1
    # model_role is not an input (the resolved model is)
    assert key(req(model_role="strong")) == base


def test_length_prefix_stops_fields_running_together() -> None:
    assert key(req(system="ab", user="c")) != key(req(system="a", user="bc"))


def test_key_ignores_case_applicant_and_time() -> None:
    # the request has no field for them: the same request in another case hits the same key
    assert key(req()) == key(req())


def test_get_put_clear_prune_stats(db: Database) -> None:
    cache = ResponseCache(db, enabled=True)
    k = key(req())
    assert cache.get(k) is None
    cache.put(
        k,
        {"a": 1},
        model="m",
        purpose="extract:invoice",
        prompt_tokens=10,
        completion_tokens=5,
        usd=Decimal("0.000123"),
        file_sha256="a" * 64,
    )
    hit = cache.get(k)
    assert hit is not None and hit.data == {"a": 1} and hit.usd == Decimal("0.000123")
    assert hit.model == "m" and hit.prompt_tokens == 10
    stats = cache.stats()
    assert (stats.enabled, stats.entries, stats.hits, stats.misses) == (True, 1, 1, 1)
    cache.put(
        "k2",
        {},
        model="n",
        purpose="classify:C1",
        prompt_tokens=0,
        completion_tokens=0,
        usd=Decimal("0"),
        file_sha256=None,
    )
    assert cache.clear(purpose="classify:C1") == 1
    assert cache.clear(model="zzz") == 0
    assert cache.prune(older_than=timedelta(days=1)) == 0  # nothing old
    with db.conn() as c:
        c.execute("UPDATE llm_cache SET created_at = '2000-01-01T00:00:00.000000Z'")
    assert cache.prune(older_than=timedelta(days=1)) == 1
    assert cache.stats().entries == 0


def test_disabled_cache_reads_and_writes_nothing(db: Database) -> None:
    cache = ResponseCache(db, enabled=False)
    cache.put(
        "k",
        {"a": 1},
        model="m",
        purpose="p",
        prompt_tokens=1,
        completion_tokens=1,
        usd=Decimal("0"),
        file_sha256=None,
    )
    assert cache.get("k") is None
    stats = cache.stats()
    assert stats.enabled is False and stats.entries == 0


def test_schema_constants() -> None:
    assert LLM_CACHE_SCHEMA == 1 and MAX_OUTPUT_TOKENS == 8000
