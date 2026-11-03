"""Sanctions screening: parsers of the four official formats, the snapshot, the matcher and the update
script (fake transport). Offline; the lists in these tests are tiny hand-written samples in the real
formats, with invented people."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.sources import sanctions as S
from scripts import update_sanctions as U
from scripts._sanctions import parsers as P

UN_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CONSOLIDATED_LIST dateGenerated="2026-11-02T23:00:04.371Z"><INDIVIDUALS>
<INDIVIDUAL><DATAID>1</DATAID><FIRST_NAME>TARIQ</FIRST_NAME><SECOND_NAME>MIKHAIL</SECOND_NAME><THIRD_NAME>AZIZ</THIRD_NAME>
<UN_LIST_TYPE>Iraq</UN_LIST_TYPE><REFERENCE_NUMBER>IQi.025</REFERENCE_NUMBER><LISTED_ON>2003-06-27</LISTED_ON>
<NATIONALITY><VALUE>Iraq</VALUE></NATIONALITY>
<INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>Tarek Aziz Mikhail</ALIAS_NAME></INDIVIDUAL_ALIAS>
<INDIVIDUAL_ALIAS><QUALITY>Low</QUALITY><ALIAS_NAME>Abu Weak</ALIAS_NAME></INDIVIDUAL_ALIAS>
<INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>EXACT</TYPE_OF_DATE><DATE>1936-07-01</DATE></INDIVIDUAL_DATE_OF_BIRTH>
<INDIVIDUAL_DOCUMENT><TYPE_OF_DOCUMENT>Passport</TYPE_OF_DOCUMENT><NUMBER>No34409/129</NUMBER></INDIVIDUAL_DOCUMENT>
</INDIVIDUAL></INDIVIDUALS>
<ENTITIES><ENTITY><DATAID>2</DATAID><FIRST_NAME>NORTHERN TRADING COMPANY</FIRST_NAME><UN_LIST_TYPE>DPRK</UN_LIST_TYPE>
<REFERENCE_NUMBER>KPe.001</REFERENCE_NUMBER><LISTED_ON>2009-04-24</LISTED_ON></ENTITY></ENTITIES></CONSOLIDATED_LIST>"""

OFAC_XML = b"""<?xml version="1.0" standalone="yes"?>
<sdnList xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML">
<sdnEntry><uid>2677</uid><firstName>Abboud Latif Hassan</firstName><lastName>AL-ZOMOR</lastName><sdnType>Individual</sdnType>
<programList><program>SDGT</program></programList>
<idList><id><uid>1</uid><idType>Passport</idType><idNumber>A1234567</idNumber></id>
<id><uid>2</uid><idType>Gender</idType><idNumber>Male</idNumber></id></idList>
<akaList><aka><uid>3</uid><type>a.k.a.</type><category>strong</category><lastName>ZUMAR</lastName><firstName>Abbud</firstName></aka>
<aka><uid>4</uid><type>a.k.a.</type><category>weak</category><lastName>Z</lastName></aka></akaList>
<nationalityList><nationality><uid>5</uid><country>Egypt</country></nationality></nationalityList>
<dateOfBirthList><dateOfBirthItem><uid>6</uid><dateOfBirth>19 Apr 1947</dateOfBirth></dateOfBirthItem>
<dateOfBirthItem><uid>7</uid><dateOfBirth>circa 1950</dateOfBirth></dateOfBirthItem></dateOfBirthList></sdnEntry>
<sdnEntry><uid>36</uid><lastName>AEROCARIBBEAN AIRLINES</lastName><sdnType>Entity</sdnType>
<programList><program>CUBA</program></programList></sdnEntry></sdnList>"""

EU_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<export xmlns="http://eu.europa.ec/fpi/fsd/export">
<sanctionEntity euReferenceNumber="EU.27.28" logicalId="13"><regulation programme="IRQ" publicationDate="2003-07-08"/>
<subjectType code="person"/><nameAlias firstName="Saddam" lastName="Hussein" wholeName="Saddam Hussein" strong="true"/>
<nameAlias wholeName="Weak Alias" strong="false"/>
<citizenship countryDescription="IRAQ"/><birthdate year="1937" monthOfYear="4" dayOfMonth="28" birthdate="1937-04-28"/>
<identification number="X99999" identificationTypeDescription="Passport" knownFalse="false"/></sanctionEntity></export>"""

UK_XML = """<?xml version="1.0" encoding="utf-8"?>
<Designations><DateGenerated>30/10/2026</DateGenerated><Designation><UniqueID>AFG0006</UniqueID>
<Names><Name><Name1>MOHAMMAD</Name1><Name2>HASSAN</Name2><Name6>AKHUND</Name6><NameType>Primary Name</NameType></Name>
<Name><Name6>Mullah Hasan</Name6><NameType>Alias</NameType><AliasStrength>Low quality a.k.a</AliasStrength></Name></Names>
<NonLatinNames><NonLatinName><NameNonLatinScript>محمد حسن آخوند</NameNonLatinScript></NonLatinName></NonLatinNames>
<RegimeName>The Afghanistan (Sanctions) Regulations</RegimeName><IndividualEntityShip>Individual</IndividualEntityShip>
<DateDesignated>25/01/2001</DateDesignated>
<IndividualDetails><Individual><DOBs><DOB>dd/mm/1945</DOB><DOB>dd/mm/1946</DOB></DOBs>
<PassportDetails><Passport><PassportNumber>P04581926</PassportNumber></Passport></PassportDetails>
<Nationalities><Nationality>Afghanistan</Nationality></Nationalities></Individual></IndividualDetails></Designation></Designations>""".encode()


# ------------------------------------------------------------------------------------ parsers


def test_un_parser_reads_names_aliases_dates_ids_and_drops_low_quality_aliases() -> None:
    person, entity = P.parse_un(UN_XML)
    assert person["id"] == "un:1" and person["ref"] == "IQi.025" and person["kind"] == "person"
    assert person["names"] == ["TARIQ MIKHAIL AZIZ", "Tarek Aziz Mikhail"]
    assert person["weak_aliases"] == 1 and person["dob"] == ["1936-07-01"]
    assert person["nationality"] == ["iraq"] and person["ids"][0]["number"] == "No34409/129"
    assert entity["kind"] == "entity" and entity["names"] == ["NORTHERN TRADING COMPANY"]


def test_ofac_parser_keeps_strong_aliases_real_ids_and_parses_dates() -> None:
    person, entity = P.parse_ofac(OFAC_XML)
    assert person["names"] == ["Abboud Latif Hassan AL-ZOMOR", "Abbud ZUMAR"]
    assert person["weak_aliases"] == 1
    assert [i["number"] for i in person["ids"]] == ["A1234567"]  # "Gender: Male" is not an id
    assert person["dob"] == ["1947-04-19", "1950"] and person["nationality"] == ["egypt"]
    assert entity["kind"] == "entity" and entity["programs"] == ["CUBA"]


def test_eu_parser() -> None:
    (e,) = P.parse_eu(EU_XML)
    assert e["id"] == "eu:13" and e["ref"] == "EU.27.28" and e["names"] == ["Saddam Hussein"]
    assert e["weak_aliases"] == 1 and e["dob"] == ["1937-04-28"] and e["nationality"] == ["iraq"]
    assert e["ids"] == [{"type": "Passport", "number": "X99999"}] and e["programs"] == ["IRQ"]


def test_uk_parser() -> None:
    (e,) = P.parse_uk(UK_XML)
    assert e["id"] == "uk:AFG0006" and e["names"][0] == "MOHAMMAD HASSAN AKHUND"
    assert "محمد حسن آخوند" in e["names"] and e["weak_aliases"] == 1
    assert e["dob"] == ["1945", "1946"] and e["ids"] == [
        {"type": "Passport", "number": "P04581926"}
    ]
    assert e["listed_on"] == "2001-01-25"


@pytest.mark.parametrize("parser", [P.parse_un, P.parse_ofac, P.parse_eu, P.parse_uk])
def test_a_file_with_a_dtd_or_entity_is_refused(parser) -> None:  # noqa: ANN001
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    with pytest.raises(P.UnsafeXml):
        parser(evil)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("19 Apr 1947", "1947-04-19"), ("Apr 1947", "1947-04"), ("1947", "1947"),
        ("circa 1954", "1954"), ("1947 to 1950", "1947/1950"), ("12/03/1970", "1970-03-12"),
        ("dd/mm/1945", "1945"), ("dd/03/1970", "1970-03"), ("1936-07-01", "1936-07-01"), ("soon", None),
    ],
)  # fmt: skip
def test_dates_of_birth_are_normalised(raw: str, want: str | None) -> None:
    assert P.parse_dob(raw) == want


# ----------------------------------------------------------------------------- the matcher


def snapshot(entries: list[dict], tmp: Path | None = None) -> S.Snapshot:
    now = datetime.now(UTC)
    srcs = [{"id": s, "status": "ok", "downloaded_at": now.isoformat()} for s in S.SOURCE_ORDER]
    return S.Snapshot(Path("x.json"), now, srcs, entries)


def entry(i: str, names: list[str], kind: str = "person", **kw) -> dict:  # noqa: ANN003
    return {"id": f"un:{i}", "list": "un", "ref": f"R{i}", "kind": kind, "names": names, "dob": [],
            "nationality": [], "ids": [], "programs": [], "listed_on": None, **kw}  # fmt: skip


@pytest.fixture(scope="module")
def screener() -> S.Screener:
    return S.Screener(
        snapshot(
            [
                entry("1", ["Tariq Mikhail Aziz"], dob=["1936-07-01"], nationality=["iraq"],
                      ids=[{"type": "Passport", "number": "No34409/129"}]),
                entry("2", ["Ahmad bin Ismail Hassan"]),
                entry("3", ["Northern Trading Company"], kind="entity"),
                entry("4", ["Suryadi"]),
                entry("5", ["Hussein Abdulrahman Al-Tikriti"], dob=["1950/1955"]),
                entry("6", ["Li Wei"]),
                entry("7", ["陈大文明"]),
                entry("8", ["Boat One"], kind="vessel"),
            ]
        )
    )  # fmt: skip


def hits(screener: S.Screener, name: str, kind: str = "person", **kw) -> list[S.Hit]:  # noqa: ANN003
    return screener.screen(S.Subject(name, kind, **kw))  # type: ignore[arg-type]


def test_exact_name_in_any_order_is_a_medium_possible_match_without_other_evidence(
    screener,
) -> None:  # noqa: ANN001
    (h,) = hits(screener, "Aziz Tariq Mikhail")
    assert (h.name_match, h.level, h.entry["id"]) == ("exact", "medium", "un:1")


def test_exact_name_plus_full_date_of_birth_is_high(screener) -> None:  # noqa: ANN001
    (h,) = hits(screener, "Tariq Mikhail Aziz", dob=date(1936, 7, 1))
    assert h.level == "high" and h.dob == "strong"


def test_exact_name_plus_matching_id_number_is_high(screener) -> None:  # noqa: ANN001
    (h,) = hits(screener, "Tariq M. Aziz", ids=("no34409/129",))
    assert h.name_match == "identifier" and h.level == "medium"  # the name is not the same: medium
    (h,) = hits(screener, "Tariq Mikhail Aziz", ids=("NO 34409/129",))
    assert h.level == "high" and h.id_match


def test_a_year_of_birth_needs_the_nationality_to_make_it_high(screener) -> None:  # noqa: ANN001
    (a,) = hits(screener, "Hussein Abdulrahman Al-Tikriti", dob=date(1952, 3, 3))
    assert a.dob == "weak" and a.level == "medium"
    s2 = S.Screener(
        snapshot(
            [entry("5", ["Hussein Abdulrahman Al-Tikriti"], dob=["1952"], nationality=["iraq"])]
        )
    )
    (b,) = hits(s2, "Hussein Abdulrahman Al-Tikriti", dob=date(1952, 3, 3), nationality="Iraqi")
    assert b.level == "high"


def test_exact_name_with_a_different_date_of_birth_is_low_not_hidden(screener) -> None:  # noqa: ANN001
    (h,) = hits(screener, "Tariq Mikhail Aziz", dob=date(1990, 1, 1))
    assert h.dob == "conflict" and h.level == "low"


def test_one_letter_slip_in_a_long_word_is_fuzzy_and_medium_never_high(screener) -> None:  # noqa: ANN001
    (h,) = hits(screener, "Tariq Mikhael Aziz", dob=date(1936, 7, 1), ids=("NO34409/129",))
    assert h.name_match == "fuzzy" and h.level == "medium"


def test_the_loose_verdicts_of_the_registry_matcher_are_not_used(screener) -> None:  # noqa: ANN001
    assert hits(screener, "Tariq Aziz") == []  # "the shorter name is contained in the longer"
    assert hits(screener, "T M Aziz") == []  # initials
    assert hits(screener, "Aziz") == []  # a single name
    assert hits(screener, "Suryadi") == []  # a mononym is not screened on the name alone
    assert hits(screener, "Li Wei Ming") == [] and hits(screener, "Wei Li")[0].entry["id"] == "un:6"


def test_father_and_son_are_not_one_person(screener) -> None:  # noqa: ANN001
    assert hits(screener, "Ismail bin Ahmad Hassan") == []
    assert hits(screener, "Ahmad bin Ismail Hassan")[0].entry["id"] == "un:2"


def test_companies_match_without_the_legal_form_and_never_match_people(screener) -> None:  # noqa: ANN001
    (h,) = hits(screener, "NORTHERN TRADING COMPANY PTE. LTD.", "entity")
    assert h.entry["id"] == "un:3" and h.name_match == "exact"
    assert hits(screener, "Northern Trading Company", "person") == []
    assert hits(screener, "Northern Shipping", "entity") == []
    assert hits(screener, "Boat One", "entity") == []  # vessels are not screened


def test_cjk_names_need_three_characters(screener) -> None:  # noqa: ANN001
    assert hits(screener, "陈大文明")[0].entry["id"] == "un:7"
    assert hits(screener, "陈大") == []


def test_an_identifier_alone_is_found_as_a_medium_possible_match(screener) -> None:  # noqa: ANN001
    (h,) = hits(screener, "Somebody Else Entirely", ids=("NO34409/129",))
    assert h.name_match == "identifier" and h.level == "medium"
    assert hits(screener, "Somebody Else Entirely", ids=("123",)) == []


def test_nationality_matches_country_and_demonym_only() -> None:
    assert S.nationality_matches("Iraqi", ["iraq"]) and S.nationality_matches(
        "Singaporean", ["singapore"]
    )
    assert not S.nationality_matches("Singaporean", ["iraq"]) and not S.nationality_matches(
        "", ["iraq"]
    )


def test_date_of_birth_relations() -> None:
    d = date(1952, 3, 3)
    assert S.dob_relation(d, ["1952-03-03"]) == "strong"
    assert (
        S.dob_relation(d, ["1952"])
        == S.dob_relation(d, ["1950/1955"])
        == S.dob_relation(d, ["1952-03"])
        == "weak"
    )
    assert (
        S.dob_relation(d, ["1960"]) == "conflict"
        and S.dob_relation(d, []) == S.dob_relation(None, ["1952"]) == "none"
    )


# ------------------------------------------------------------------------ snapshot and update


def test_snapshot_problems_name_a_missing_or_old_list() -> None:
    now = datetime(2026, 11, 3, tzinfo=UTC)
    srcs = [{"id": s, "status": "ok", "downloaded_at": now.isoformat()} for s in S.SOURCE_ORDER]
    assert S.Snapshot(Path("x"), now, srcs, []).problems(now, 7) == []
    srcs[1] = {"id": "ofac", "status": "failed"}
    srcs[2] = {"id": "eu", "status": "ok", "downloaded_at": (now - timedelta(days=9)).isoformat()}
    problems = S.Snapshot(Path("x"), now, srcs, []).problems(now, 7)
    assert any("OFAC" in p and "not in the snapshot" in p for p in problems)
    assert any("EU" in p and "9 days" in p for p in problems)


def test_load_snapshot_reads_the_file_and_refuses_a_bad_one(tmp_path: Path) -> None:
    with pytest.raises(S.SnapshotError):
        S.load_snapshot(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(S.SnapshotError):
        S.load_snapshot(bad)
    bad.write_text(json.dumps({"schema": "other"}))
    with pytest.raises(S.SnapshotError):
        S.load_snapshot(bad)


def results() -> dict:
    return {s: (b"raw-" + s.encode(), p(b)) for s, p, b in
            [("un", P.parse_un, UN_XML), ("ofac", P.parse_ofac, OFAC_XML), ("eu", P.parse_eu, EU_XML), ("uk", P.parse_uk, UK_XML)]}  # fmt: skip


def test_build_snapshot_records_url_time_and_sha_per_source() -> None:
    now = datetime(2026, 11, 3, tzinfo=UTC)
    snap = U.build_snapshot(results(), None, now)
    assert snap["schema"] == S.SCHEMA and len(snap["entries"]) == 6
    un = next(s for s in snap["sources"] if s["id"] == "un")
    assert (
        un["url"].startswith("https://scsanctions.un.org/")
        and un["downloaded_at"] == now.isoformat()
    )
    import hashlib

    assert un["sha256"] == hashlib.sha256(b"raw-un").hexdigest() and un["records"] == 2
    assert all("token=" not in s.get("url", "") for s in snap["sources"])


def test_a_source_that_fails_is_reported_and_an_old_copy_goes_stale_on_its_own_clock() -> None:
    then = datetime(2026, 10, 1, tzinfo=UTC)
    now = datetime(2026, 11, 3, tzinfo=UTC)
    old = U.build_snapshot(results(), None, then)
    res = results()
    res["eu"] = "ConnectError: boom"
    new = U.build_snapshot(res, old, now)
    eu = next(s for s in new["sources"] if s["id"] == "eu")
    assert (
        eu["status"] == "carried_over"
        and eu["downloaded_at"] == then.isoformat()
        and "boom" in eu["last_error"]
    )
    assert any(e["list"] == "eu" for e in new["entries"])
    loaded = S.Snapshot(Path("x"), now, new["sources"], new["entries"])
    assert any("EU" in p for p in loaded.problems(now, 7))
    # a failure with nothing to carry over is recorded as failed and screened as missing
    first = U.build_snapshot({**results(), "uk": "HTTPStatusError: 500"}, None, now)
    assert next(s for s in first["sources"] if s["id"] == "uk")["status"] == "failed"
    assert not any(e["list"] == "uk" for e in first["entries"])


def test_write_snapshot_versions_the_file_and_updates_current(tmp_path: Path) -> None:
    now = datetime(2026, 11, 3, 1, 2, 3, tzinfo=UTC)
    snap = U.build_snapshot(results(), None, now)
    path = U.write_snapshot(snap, tmp_path, now)
    assert path.name == "snapshot-20261103T010203Z.json"
    assert S.load_snapshot(tmp_path / "current.json").problems(now, 7) == []


def test_fetch_enforces_the_size_limit_and_sends_the_honest_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, content=b"x" * 100)

    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": U.USER_AGENT}
    )
    assert U.fetch("https://example.org/a", client) == b"x" * 100 and seen == [U.USER_AGENT]
    monkeypatch.setattr(U, "MAX_BYTES", 10)
    with pytest.raises(ValueError):
        U.fetch("https://example.org/a", client)


def test_the_update_script_downloads_nothing_in_private_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    class Private:
        private_mode = "on"
        data_dir = tmp_path

    monkeypatch.setattr(U, "Settings", Private)
    monkeypatch.setattr(U.httpx, "Client", lambda *a, **k: pytest.fail("a connection was opened"))
    assert U.main([]) == 2
    assert "Private mode" in capsys.readouterr().err
