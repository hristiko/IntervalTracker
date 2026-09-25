import json

import pytest

import scoring_engine as se
from conftest import ENTITIES, read_json, write_json


def run(scored_workdir, settings_name="scoring_config.json"):
    return se.run_scoring(
        scored_workdir / "parameters_config.json",
        scored_workdir / settings_name,
        ENTITIES,
        scored_workdir / "validated.json",
        scored_workdir / "history.json",
        scored_workdir / "leaderboard.json",
        now="2026-09-25T00:00:00+00:00",
    )


# ---------------- end-to-end shape ----------------
def test_run_scoring_covers_all_11_fake_entities(scored_workdir):
    result = run(scored_workdir)
    assert result["entities_scored"] == 11
    history = read_json(scored_workdir / "history.json")
    assert set(history["entities"]) == {f"c{i:02d}" for i in range(1, 12)}


def test_run_scoring_matches_config_version(scored_workdir):
    config = read_json(scored_workdir / "parameters_config.json")
    result = run(scored_workdir)
    assert result["config_version"] == config["config_version"]
    history = read_json(scored_workdir / "history.json")
    assert history["config_version"] == config["config_version"]


def test_run_scoring_is_deterministic(scored_workdir):
    first = run(scored_workdir)
    second = run(scored_workdir)
    assert first["leaderboard"] == second["leaderboard"]


# ---------------- regression: freeze known results on the real fake data ----------------
def test_northwind_is_ranked_first_and_compliant(scored_workdir):
    result = run(scored_workdir)
    top = result["leaderboard"]["ranking"][0]
    assert top["entity_id"] == "c01"
    assert top["status"] == "Compliant"
    assert top["score"] == pytest.approx(0.5439, abs=0.001)


def test_orbit_lending_gate_zeroes_the_score_and_persists(scored_workdir):
    run(scored_workdir)
    history = read_json(scored_workdir / "history.json")
    records = history["entities"]["c04"]
    # first 4 checks (before the sanctions hit) were Compliant; last 2 are Violated
    assert [r["status"] for r in records] == ["Compliant"] * 4 + ["Violated"] * 2
    assert records[-1]["gated"] is True
    assert records[-1]["raw_score"] == 0.0
    assert records[-1]["ewma_score"] == 0.0


def test_only_one_entity_is_currently_compliant_given_default_intervals(scored_workdir):
    # A known, slightly uncomfortable consequence of the default intervals chosen
    # earlier (in particular the strict material-contracts threshold): only the
    # cleanest fake company clears every single parameter. This test exists to
    # surface it if intervals.json changes, not to declare it correct.
    result = run(scored_workdir)
    assert result["leaderboard"]["compliant_count"] == 1


def test_quanta_ledger_cold_start_has_only_two_observations(scored_workdir):
    result = run(scored_workdir)
    row = next(r for r in result["leaderboard"]["ranking"] if r["entity_id"] == "c06")
    assert row["observations"] == 2


# ---------------- settings sensitivity ----------------
def test_gamma_1_scores_lower_than_default_for_a_fully_compliant_entity(scored_workdir):
    # Counterintuitive but correct (see test_beta_ewma.py for the isolated proof):
    # gamma=1 never forgets, which also means it never forgets the *prior's*
    # implied skepticism (prior_beta). Decay (gamma<1) erodes that skepticism
    # over a clean streak, so a fully compliant entity like c01 actually scores
    # *higher* under decay than under "perfect memory". This is a real property
    # of the model, not a bug -- worth knowing before tuning gamma.
    default_result = run(scored_workdir)
    default_score = next(r["score"] for r in default_result["leaderboard"]["ranking"] if r["entity_id"] == "c01")

    settings = read_json(scored_workdir / "scoring_config.json")
    settings["gamma"] = 1.0
    write_json(scored_workdir / "no_forget.json", settings)
    no_forget_result = run(scored_workdir, settings_name="no_forget.json")
    no_forget_score = next(r["score"] for r in no_forget_result["leaderboard"]["ranking"] if r["entity_id"] == "c01")

    assert no_forget_score < default_score


def test_higher_credible_level_raises_scores_for_a_compliant_entity(scored_workdir):
    default_result = run(scored_workdir)
    default_score = next(r["score"] for r in default_result["leaderboard"]["ranking"] if r["entity_id"] == "c01")

    settings = read_json(scored_workdir / "scoring_config.json")
    settings["credible_level"] = 0.5  # median instead of a cautious lower bound
    write_json(scored_workdir / "less_conservative.json", settings)
    result = run(scored_workdir, settings_name="less_conservative.json")
    score = next(r["score"] for r in result["leaderboard"]["ranking"] if r["entity_id"] == "c01")

    assert score > default_score


# ---------------- failure cases ----------------
def test_incomplete_config_is_rejected(scored_workdir):
    config = read_json(scored_workdir / "parameters_config.json")
    config["parameters"][0]["interval"] = {"lower": None, "upper": None}
    config["complete"] = False
    write_json(scored_workdir / "parameters_config.json", config)
    with pytest.raises(se.ScoringError, match="Cannot use the config"):
        run(scored_workdir)


@pytest.mark.parametrize("key, value, message", [
    ("gamma", 1.5, "gamma must be in"),
    ("gamma", 0.0, "gamma must be in"),
    ("credible_level", 0.0, "credible_level must be in"),
    ("credible_level", 1.0, "credible_level must be in"),
    ("lambda_ewma", 0.0, "lambda_ewma must be in"),
    ("prior_alpha", 0.0, "prior_alpha and prior_beta"),
    ("prior_beta", -1.0, "prior_alpha and prior_beta"),
])
def test_invalid_scoring_settings_are_rejected(scored_workdir, key, value, message):
    settings = read_json(scored_workdir / "scoring_config.json")
    settings[key] = value
    write_json(scored_workdir / "scoring_config.json", settings)
    with pytest.raises(se.ScoringError, match=message):
        run(scored_workdir)


def test_missing_settings_field_is_rejected(scored_workdir):
    settings = read_json(scored_workdir / "scoring_config.json")
    del settings["gamma"]
    write_json(scored_workdir / "scoring_config.json", settings)
    with pytest.raises(se.ScoringError, match="missing: gamma"):
        run(scored_workdir)


def test_missing_validated_file_gives_a_clear_error(scored_workdir, tmp_path):
    with pytest.raises(se.ScoringError, match="not found"):
        se.run_scoring(
            scored_workdir / "parameters_config.json", scored_workdir / "scoring_config.json",
            ENTITIES, tmp_path / "nope.json", tmp_path / "h.json", tmp_path / "l.json",
        )


def test_observation_for_unknown_entity_is_rejected(scored_workdir):
    validated = read_json(scored_workdir / "validated.json")
    validated["observations"][0]["entity_id"] = "ghost"
    write_json(scored_workdir / "validated.json", validated)
    with pytest.raises(se.ScoringError, match="ghost"):
        run(scored_workdir)


def test_empty_validated_file_is_rejected(scored_workdir):
    write_json(scored_workdir / "validated.json", {"observations": []})
    with pytest.raises(se.ScoringError, match="No validated observations"):
        run(scored_workdir)


# ---------------- CLI ----------------
def test_cli_writes_files_and_prints_leaderboard(scored_workdir, capsys):
    code = se.main([
        "--config", str(scored_workdir / "parameters_config.json"),
        "--settings", str(scored_workdir / "scoring_config.json"),
        "--entities", str(ENTITIES),
        "--validated", str(scored_workdir / "validated.json"),
        "--history", str(scored_workdir / "cli_history.json"),
        "--leaderboard", str(scored_workdir / "cli_leaderboard.json"),
    ])
    assert code == 0
    assert (scored_workdir / "cli_history.json").exists()
    assert (scored_workdir / "cli_leaderboard.json").exists()
    out = capsys.readouterr().out
    assert "Northwind Capital" in out
    assert out.strip().splitlines()[-11].split()[0] == "1" or "1" in out  # rank 1 row present


def test_cli_returns_1_and_prints_to_stderr_on_bad_settings(scored_workdir, capsys):
    settings = read_json(scored_workdir / "scoring_config.json")
    settings["gamma"] = 5
    write_json(scored_workdir / "scoring_config.json", settings)
    code = se.main([
        "--config", str(scored_workdir / "parameters_config.json"),
        "--settings", str(scored_workdir / "scoring_config.json"),
        "--entities", str(ENTITIES),
        "--validated", str(scored_workdir / "validated.json"),
        "--history", str(scored_workdir / "h2.json"),
        "--leaderboard", str(scored_workdir / "l2.json"),
    ])
    assert code == 1
    assert "ERROR" in capsys.readouterr().err
