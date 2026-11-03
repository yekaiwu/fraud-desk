"""Download the official sanctions lists and build ONE local snapshot for the screening check.

    uv run python backend/scripts/update_sanctions.py [--out DIR] [--only un,ofac] [--keep-raw DIR]

Lists (public downloads, no account): the UN Security Council consolidated list, the OFAC SDN list, the EU
consolidated financial sanctions list and the UK Sanctions List (which replaced the OFSI consolidated list).
The MAS page of targeted financial sanctions is recorded as a reference only: it links to the UN lists
(already in the snapshot) and offers no machine-readable list of its own. Not used, on purpose: OpenSanctions
bulk data (non-commercial licence).

The snapshot is ``<out>/snapshot-<UTC time>.json`` plus a copy as ``current.json`` (the file the check
reads), with for every source its URL, download time, sha256 and record count. A source that cannot be
downloaded is reported and recorded as failed; if the previous snapshot held it, that copy is carried over
with its OLD download time, so it goes stale on its own clock and the check says so. Exits 1 if any required
source is missing from the result.

Honours GLDB_PRIVATE_MODE (downloads nothing when it is on). Sends an honest user agent and nothing else.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import httpx  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.outbound import outbound_blocked  # noqa: E402
from app.sources.common import USER_AGENT  # noqa: E402
from app.sources.sanctions import SCHEMA, SOURCE_LABELS, SOURCE_ORDER  # noqa: E402
from scripts._sanctions.parsers import PARSERS  # noqa: E402

SOURCES: dict[str, dict[str, str]] = {
    "un": {"url": "https://scsanctions.un.org/resources/xml/en/consolidated.xml"},
    "ofac": {
        "url": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"
    },
    # the public token is the one printed in the EU's own documentation for the file service
    "eu": {
        "url": "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
    },
    "uk": {"url": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"},
}
MAS_PAGE = "https://www.mas.gov.sg/regulation/anti-money-laundering/targeted-financial-sanctions/lists-of-designated-individuals-and-entities"
MAX_BYTES = 150 * 1024 * 1024
TIMEOUT_S = 240.0
KEEP_VERSIONS = 5


def fetch(url: str, client: httpx.Client) -> bytes:
    """The body of ``url`` (at most MAX_BYTES); raises httpx.HTTPError or ValueError."""
    chunks: list[bytes] = []
    size = 0
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("download is larger than the limit")
            chunks.append(chunk)
    return b"".join(chunks)


def source_record(
    sid: str, data: bytes, entries: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    return {
        "id": sid,
        "name": SOURCE_LABELS[sid],
        "url": SOURCES[sid]["url"].split("?")[0],  # the EU token is public but not worth repeating
        "downloaded_at": now.isoformat(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "records": len(entries),
        "weak_aliases_not_used": sum(e.get("weak_aliases", 0) for e in entries),
        "status": "ok",
    }


def build_snapshot(
    results: dict[str, tuple[bytes, list[dict[str, Any]]] | str],
    previous: dict[str, Any] | None,
    now: datetime,
    mas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The snapshot as a dict. ``results``: source id -> (raw bytes, parsed entries) or an error text."""
    sources: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    old_sources = {s["id"]: s for s in (previous or {}).get("sources", [])}
    old_entries: dict[str, list[dict[str, Any]]] = {}
    for e in (previous or {}).get("entries", []):
        old_entries.setdefault(e["list"], []).append(e)
    for sid in SOURCE_ORDER:
        got = results.get(sid, "not requested")
        if not isinstance(got, str):
            data, parsed = got
            sources.append(source_record(sid, data, parsed, now))
            entries += parsed
            continue
        old = old_sources.get(sid)
        if old and old.get("status") in ("ok", "carried_over") and sid in old_entries:
            sources.append({**old, "status": "carried_over", "last_error": got})
            entries += old_entries[sid]
        else:
            sources.append(
                {
                    "id": sid,
                    "name": SOURCE_LABELS[sid],
                    "url": SOURCES[sid]["url"].split("?")[0],
                    "status": "failed",
                    "error": got,
                }
            )
    if mas is not None:
        sources.append(mas)
    body = json.dumps(entries, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "schema": SCHEMA,
        "built_at": now.isoformat(),
        "entries_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "sources": sources,
        "entries": entries,
    }


def mas_reference(client: httpx.Client, now: datetime) -> dict[str, Any]:
    base = {
        "id": "mas",
        "name": "MAS targeted financial sanctions (reference only)",
        "url": MAS_PAGE,
        "status": "reference_only",
        "note": "The MAS page links to the UN Security Council lists, which the snapshot holds; it offers "
        "no machine-readable list of its own. Domestic designations are not screened.",
    }
    try:
        data = fetch(MAS_PAGE, client)
    except (httpx.HTTPError, ValueError) as exc:
        return {**base, "fetch_error": type(exc).__name__}
    head = data[:20000].lower()
    note = {"maintenance": b"<title>maintenance" in head}
    return {
        **base,
        "downloaded_at": now.isoformat(),
        "sha256": hashlib.sha256(data).hexdigest(),
        **(
            {"fetch_note": "the page answered with a maintenance notice"}
            if note["maintenance"]
            else {}
        ),
    }


def write_snapshot(snapshot: dict[str, Any], out: Path, now: datetime) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    versioned = out / f"snapshot-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    versioned.write_text(payload, encoding="utf-8")
    tmp = out / "current.json.tmp"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, out / "current.json")
    for old in sorted(out.glob("snapshot-*.json"))[:-KEEP_VERSIONS]:
        old.unlink()
    return versioned


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", type=Path, help="directory (default <GLDB_DATA_DIR>/sanctions)")
    ap.add_argument(
        "--only", help="comma separated source ids to download (default all of un,ofac,eu,uk)"
    )
    ap.add_argument("--keep-raw", type=Path, help="also save each downloaded file here")
    args = ap.parse_args(argv)
    settings = Settings()
    reason = outbound_blocked(settings)
    if reason:
        print(f"nothing downloaded: {reason}", file=sys.stderr)
        return 2
    out = args.out or settings.data_dir / "sanctions"
    wanted = [s for s in (args.only.split(",") if args.only else SOURCE_ORDER) if s in SOURCES]
    now = datetime.now(UTC).replace(microsecond=0)
    previous = None
    with contextlib.suppress(OSError, ValueError):
        previous = json.loads((out / "current.json").read_text(encoding="utf-8"))
    results: dict[str, tuple[bytes, list[dict[str, Any]]] | str] = {}
    with httpx.Client(
        timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        for sid in wanted:
            try:
                data = fetch(SOURCES[sid]["url"], client)
                if args.keep_raw:
                    args.keep_raw.mkdir(parents=True, exist_ok=True)
                    (args.keep_raw / f"{sid}.xml").write_bytes(data)
                parsed = PARSERS[sid](data)
                if not parsed:
                    raise ValueError("the file held no entries")
                results[sid] = (data, parsed)
                print(f"{sid}: {len(parsed)} entries ({len(data) / 1e6:.1f} MB)")
            except Exception as exc:  # noqa: BLE001 - one list failing must not stop the others
                results[sid] = f"{type(exc).__name__}: {str(exc)[:100]}"
                print(f"{sid}: NOT FETCHED ({results[sid]})", file=sys.stderr)
        mas = mas_reference(client, now)
    snapshot = build_snapshot(results, previous, now, mas)
    path = write_snapshot(snapshot, out, now)
    missing = [
        s["id"] for s in snapshot["sources"] if s["id"] in SOURCE_ORDER and s["status"] == "failed"
    ]
    print(f"wrote {path} ({len(snapshot['entries'])} entries); current.json updated")
    if missing:
        print(f"required sources missing: {', '.join(missing)}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
