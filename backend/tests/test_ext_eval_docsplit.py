"""DocSplit topology evaluation (backend/scripts/ext_eval/docsplit_eval.py): reading the label
files, mapping packets to synthetic scanned statements, scoring the assembler's answer."""

from __future__ import annotations

import csv
import random
from pathlib import Path

from scripts.ext_eval import docsplit_eval as ds

HEADER = [
    "doc_type", "original_doc_name", "parent_doc_name", "local_doc_id", "page", "image_path",
    "text_path", "group_id", "local_doc_id_page_ordinal",
]  # fmt: skip


def write_csv(root: Path, strategy: str, packets: dict[str, list[tuple[str, int]]]) -> None:
    """packets: packet id -> [(local_doc_id, ordinal)] in upload order."""
    path = root / "small" / strategy
    path.mkdir(parents=True)
    pos = 0
    with (path / "test.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for pid, pages in packets.items():
            for doc, ordinal in pages:
                pos += 1
                w.writerow(["resume", doc, pid, doc, pos, "x.png", "x.md", "AA", ordinal])


def packet(pages: list[tuple[str, int]], pid: str = "p1") -> ds.Packet:
    return ds.Packet(
        "poly_rand", pid, tuple(ds.Page(i + 1, d, "resume", o) for i, (d, o) in enumerate(pages))
    )


def test_load_packets_and_topology(tmp_path: Path) -> None:
    write_csv(
        tmp_path,
        "poly_int",
        {
            "pB": [("a", 1), ("b", 1), ("a", 2), ("b", 2), ("c", 1)],
            "pA": [("a", 3), ("a", 1), ("a", 2)],
        },
    )
    packets = ds.load_packets(tmp_path, "poly_int")
    assert [p.packet_id for p in packets] == ["pA", "pB"]  # stable order
    assert [pg.position for pg in packets[1].pages] == [1, 2, 3, 4, 5]  # renumbered per packet
    stats = ds.topology_stats(packets)
    assert stats["packets"] == 2 and stats["pages"] == 8 and stats["documents"] == 4
    assert stats["single_page_documents"] == 1
    assert stats["documents_with_pages_out_of_order"] == 1
    assert stats["documents_whose_pages_are_not_contiguous"] == 2
    assert stats["packets_with_a_split_document"] == 1


def run(pk: ds.Packet, scenario: str, seed: int = 1) -> dict:
    up = ds.build_upload(pk, scenario, random.Random(seed))
    assert up is not None
    return ds.evaluate_upload(up)


def test_interleaved_and_shuffled_packet_is_recovered() -> None:
    pk = packet([("a", 2), ("b", 1), ("a", 3), ("b", 3), ("a", 1), ("b", 2), ("c", 1)])
    for scenario in ("distinct_accounts", "same_account_months", "no_markers"):
        r = run(pk, scenario)
        assert r["statements"] == 2 and r["recovered"] == 2 and r["wrong"] == 0, scenario
        assert r["pairs_fp"] == r["pairs_fn"] == 0


def test_single_page_documents_are_left_out() -> None:
    assert (
        ds.build_upload(packet([("a", 1), ("b", 1)]), "distinct_accounts", random.Random(1)) is None
    )


def test_a_missing_page_is_an_abstention_not_a_guess() -> None:
    pk = packet([("a", 1), ("a", 2), ("a", 3), ("a", 4)])
    r = run(pk, "page_missing")
    assert r["should_abstain"] == 1 and r["right_for_abstain"] == 1
    assert r["wrongly_assembled_when_should_abstain"] == 0 and r["recovered"] == 0


def test_a_duplicated_upload_is_not_merged_into_one_statement() -> None:
    pk = packet([("a", 1), ("a", 2), ("a", 3)])
    r = run(pk, "duplicate_upload")
    assert r["should_abstain"] == 2
    assert r["wrongly_assembled_when_should_abstain"] == 0
    assert r["pairs_fp"] == 0


def test_noisy_cues_never_produce_a_wrong_group_on_a_simple_packet() -> None:
    pk = packet([("a", i) for i in (3, 1, 2, 4)] + [("b", i) for i in (2, 1, 3)])
    for seed in range(8):
        r = run(pk, "noisy_cues", seed)
        assert r["wrong"] == 0
        assert r["recovered"] + r["abstained"] == r["should_assemble"]


def test_run_packet_and_aggregate_shape() -> None:
    pk = packet([("a", 2), ("a", 1), ("b", 2), ("b", 1)])
    results = [ds.run_packet({"packet": pk, "scenario": sc}) for sc in ds.SCENARIOS]
    assert all(not r.get("error") for r in results)
    stats = {s: ds.topology_stats([pk]) for s in ds.STRATEGIES}
    for r in results:
        r["strategy"] = "mono_rand"
    out = ds.aggregate(results, stats)
    row = out["table"]["mono_rand/distinct_accounts"]
    assert row["recovered_rate"]["k"] == 2 and row["packet_exact"]["k"] == 1
    assert out["n_errors"] == 0
    md = ds.render_markdown(out)
    assert "labels only" in md or "label files only" in md
    assert "not bank statements" in md


def test_topology_stats_of_an_empty_strategy_does_not_divide_by_zero() -> None:
    out = ds.topology_stats([])
    assert out["packets"] == 0 and out["documents_per_packet_mean"] == 0.0
