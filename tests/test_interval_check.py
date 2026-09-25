import pytest

import interval_check as ic


# ---------------- is_compliant ----------------
@pytest.mark.parametrize("score, lower, upper, expected", [
    (0.5, 0.5, 1.0, True),    # exactly on the lower edge counts as inside
    (1.0, 0.5, 1.0, True),    # exactly on the upper edge counts as inside
    (0.49, 0.5, 1.0, False),
    (0.75, 0.5, 1.0, True),
    (1.0, 1.0, 1.0, True),    # a knockout-style single-point interval
    (0.99, 1.0, 1.0, False),
])
def test_is_compliant(score, lower, upper, expected):
    assert ic.is_compliant(score, {"lower": lower, "upper": upper}) is expected


# ---------------- check_all / any_gate_violation, against the real config ----------------
def test_check_all_covers_every_parameter(loaded_config):
    scores = {p["id"]: 1.0 for p in loaded_config["parameters"]}
    result = ic.check_all(loaded_config, scores)
    assert set(result) == {p["id"] for p in loaded_config["parameters"]}
    assert all(entry["compliant"] for entry in result.values())


def test_check_all_flags_the_sanctions_parameter_as_a_gate(loaded_config):
    scores = {p["id"]: 1.0 for p in loaded_config["parameters"]}
    result = ic.check_all(loaded_config, scores)
    assert result["p06"]["gate"] is True
    non_gates = [pid for pid, entry in result.items() if pid != "p06"]
    assert all(not result[pid]["gate"] for pid in non_gates)


def test_check_all_missing_score_raises(loaded_config):
    scores = {p["id"]: 1.0 for p in loaded_config["parameters"] if p["id"] != "p01"}
    with pytest.raises(KeyError, match="p01"):
        ic.check_all(loaded_config, scores)


def test_any_gate_violation_true_only_when_gate_parameter_fails(loaded_config):
    all_ok = {p["id"]: 1.0 for p in loaded_config["parameters"]}
    result_ok = ic.check_all(loaded_config, all_ok)
    assert ic.any_gate_violation(result_ok) is False

    sanctioned = dict(all_ok, p06=0.0)  # sanctions hit -> score 0, interval is [1, 1]
    result_bad = ic.check_all(loaded_config, sanctioned)
    assert ic.any_gate_violation(result_bad) is True


def test_any_gate_violation_false_when_a_non_gate_parameter_fails(loaded_config):
    scores = {p["id"]: 1.0 for p in loaded_config["parameters"]}
    scores["p05"] = 0.0  # advisory board, not a gate parameter
    result = ic.check_all(loaded_config, scores)
    assert result["p05"]["compliant"] is False
    assert ic.any_gate_violation(result) is False
