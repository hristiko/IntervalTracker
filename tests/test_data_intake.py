import copy

import pytest

import data_intake as di
from conftest import ENTITIES, RAW, read_json, write_json


def run(built_config, workdir, raw_records=None, now="2026-09-24T10:00:00+00:00"):
    raw_path = RAW
    if raw_records is not None:
        raw_path = workdir / "raw.json"
        write_json(raw_path, {"observations": raw_records})
    return di.run_intake(
        built_config, ENTITIES, raw_path, workdir / "validated.json", workdir / "rejected.json", now=now
    )


def good_record():
    return copy.deepcopy(read_json(RAW)["observations"][0])  # c01, first check


# ---------------- real fake data ----------------
def test_all_fake_observations_pass(built_config, workdir):
    summary = run(built_config, workdir)
    assert (summary["total"], summary["validated"], summary["rejected"]) == (62, 62, 0)
    assert summary["per_entity"]["c06"] == 2
    assert summary["per_entity"]["c01"] == 6


def test_validated_records_are_stamped_and_sorted(built_config, workdir):
    run(built_config, workdir)
    records = read_json(workdir / "validated.json")["observations"]
    assert all(r["ingested_at"] and r["config_version"] for r in records)
    keys = [(r["entity_id"], r["checked_at"]) for r in records]
    assert keys == sorted(keys)


def test_rerun_is_idempotent_and_keeps_original_timestamp(built_config, workdir):
    run(built_config, workdir, now="2026-01-01T00:00:00+00:00")
    run(built_config, workdir, now="2026-02-02T00:00:00+00:00")
    records = read_json(workdir / "validated.json")["observations"]
    assert len(records) == 62
    assert {r["ingested_at"] for r in records} == {"2026-01-01T00:00:00+00:00"}


def test_changed_facts_get_a_new_timestamp(built_config, workdir):
    raw = read_json(RAW)["observations"]
    run(built_config, workdir, raw, now="2026-01-01T00:00:00+00:00")
    raw[0]["facts"]["contracts_count"] += 1
    run(built_config, workdir, raw, now="2026-02-02T00:00:00+00:00")
    stamps = {(r["entity_id"], r["checked_at"]): r["ingested_at"]
              for r in read_json(workdir / "validated.json")["observations"]}
    assert stamps[("c01", "2025-03-31")] == "2026-02-02T00:00:00+00:00"
    assert stamps[("c01", "2025-06-30")] == "2026-01-01T00:00:00+00:00"


# ---------------- rejection cases ----------------
def mutate(fn):
    record = good_record()
    fn(record)
    return record


@pytest.mark.parametrize("fn, expected", [
    (lambda r: r["facts"].pop("debt"), "debt: required fact is missing"),
    (lambda r: r["facts"].update(debt="secured"), "debt: must be one of"),
    (lambda r: r["facts"].update(contracts_count=-1), "contracts_count: must be a whole number"),
    (lambda r: r["facts"].update(contracts_count=True), "contracts_count: must be a whole number"),
    (lambda r: r["facts"].update(contracts_count=2.5), "contracts_count: must be a whole number"),
    (lambda r: r["facts"].update(sanctions_hit="no"), "sanctions_hit: must be true or false"),
    (lambda r: r["facts"].update(licenses=["ISO", "ISO"]), "duplicates"),
    (lambda r: r["facts"].update(licenses=["gold"]), "invalid entries"),
    (lambda r: r["facts"].update(incorporation_date="15/03/2022"), "incorporation_date: must be a date"),
    (lambda r: r["facts"].update(incorporation_date="2030-01-01"), "after checked_at"),
    (lambda r: r["facts"].update(incorporation_date="2020-01-01"), "differs from the entities file"),
    (lambda r: r.update(checked_at="March 2025"), "checked_at must be a date"),
    (lambda r: r.update(entity_id="zzz"), "unknown entity"),
    (lambda r: r.pop("entity_id"), "entity_id is missing"),
    (lambda r: r.update(facts="oops"), "facts is missing or not an object"),
    (lambda r: r["facts"]["financials"].update(has_revenue=False), "must be 0 when financials.has_revenue is false"),
    (lambda r: r["facts"]["value_proposition"].update(description="great"), "value_proposition.description: must be one of"),
])
def test_bad_records_are_rejected_with_reason(built_config, workdir, fn, expected):
    summary = run(built_config, workdir, [mutate(fn)])
    assert summary["validated"] == 0 and summary["rejected"] == 1
    assert any(expected in error for error in summary["rejected_records"][0]["errors"]), \
        summary["rejected_records"][0]["errors"]


def test_duplicate_observation_is_rejected(built_config, workdir):
    summary = run(built_config, workdir, [good_record(), good_record()])
    assert (summary["validated"], summary["rejected"]) == (1, 1)
    assert "duplicate" in summary["rejected_records"][0]["errors"][0]


def test_unknown_fact_is_a_warning_not_a_rejection(built_config, workdir):
    record = mutate(lambda r: r["facts"].update(favourite_colour="blue"))
    summary = run(built_config, workdir, [record])
    assert summary["validated"] == 1 and summary["warnings"] == 1
    stored = read_json(workdir / "validated.json")["observations"][0]
    assert "favourite_colour" not in stored["facts"]


def test_rejected_file_is_written_with_reasons(built_config, workdir):
    run(built_config, workdir, [mutate(lambda r: r["facts"].pop("debt"))])
    rejected = read_json(workdir / "rejected.json")
    assert rejected["count"] == 1 and rejected["rejected"][0]["errors"]


# ---------------- config / schema compatibility ----------------
def test_config_requiring_unknown_fact_stops_intake(built_config, workdir):
    config = read_json(built_config)
    config["parameters"][2]["rule_spec"]["required_facts"].append("mystery_fact")
    from parser_config import _version_of
    config["config_version"] = _version_of(config["parameters"])
    write_json(built_config, config)
    with pytest.raises(di.IntakeError, match="mystery_fact"):
        run(built_config, workdir)


def test_config_using_value_intake_does_not_accept_stops_intake(built_config, workdir):
    config = read_json(built_config)
    config["parameters"][8]["rule_spec"]["map"]["secured"] = 0.9
    from parser_config import _version_of
    config["config_version"] = _version_of(config["parameters"])
    write_json(built_config, config)
    with pytest.raises(di.IntakeError, match="secured"):
        run(built_config, workdir)


def test_tampered_config_stops_intake(built_config, workdir):
    config = read_json(built_config)
    config["parameters"][0]["weight"] = 99
    write_json(built_config, config)
    with pytest.raises(di.IntakeError, match="Cannot use the config"):
        run(built_config, workdir)


def test_missing_raw_file_is_a_clear_error(built_config, workdir):
    with pytest.raises(di.IntakeError, match="Raw observations file not found"):
        di.run_intake(built_config, ENTITIES, workdir / "nope.json", workdir / "v.json", workdir / "r.json")


def test_cli_strict_exit_code(built_config, workdir):
    write_json(workdir / "raw.json", {"observations": [mutate(lambda r: r["facts"].pop("debt"))]})
    args = ["--config", str(built_config), "--entities", str(ENTITIES), "--raw", str(workdir / "raw.json"),
            "--validated", str(workdir / "v.json"), "--rejected", str(workdir / "r.json")]
    assert di.main(args) == 0
    assert di.main(args + ["--strict"]) == 2
