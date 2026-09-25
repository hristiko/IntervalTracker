import pytest

import normalize as nm


# ---------------- generic helpers ----------------
def test_get_fact_reads_nested_dot_paths():
    facts = {"financials": {"has_revenue": True, "growth": 2}}
    assert nm.get_fact(facts, "financials.has_revenue") is True
    assert nm.get_fact(facts, "financials.growth") == 2


def test_get_fact_missing_path_returns_none():
    assert nm.get_fact({"a": {"b": 1}}, "a.c") is None
    assert nm.get_fact({"a": 1}, "a.b") is None  # a is not a dict


@pytest.mark.parametrize("frm, to, expected", [
    ("2022-03-15", "2022-03-15", 0),
    ("2022-03-15", "2022-06-14", 2),   # hasn't reached the 15th yet -> 2, not 3
    ("2022-03-15", "2022-06-15", 3),
    ("2020-01-31", "2020-02-01", 0),   # Feb 1 hasn't reached the 31st equivalent
    ("2020-01-01", "2025-01-01", 60),
])
def test_months_between(frm, to, expected):
    assert nm.months_between(frm, to) == expected


def test_months_between_never_negative_for_future_dates_guarded_elsewhere():
    # normalize.py doesn't itself forbid to < from; intake already guarantees
    # incorporation_date <= checked_at, but the helper should not crash.
    assert nm.months_between("2025-01-01", "2020-01-01") == 0


# ---------------- banded_then_decay (p01, date of incorporation) ----------------
P01 = {
    "type": "banded_then_decay",
    "required_facts": ["incorporation_date"],
    "derived_inputs": {"age_months": {"op": "months_between", "from": "incorporation_date", "to": "checked_at"}},
    "on": "age_months",
    "bands": [[0, 12, 0.70], [12, 24, 0.80], [24, 36, 0.90], [36, 60, 1.00]],
    "after": {"start_at": 60, "base": 1.00, "step_size": 12, "per_step": -0.10, "floor": 0.0},
}


def _facts_with_age(age_months):
    return {"incorporation_date": "2020-01-15"}, f"2020-01-15", age_months  # placeholder, real test builds date below


def _checked_at_for_age(age_months):
    """2020-01-15 plus `age_months` whole months, staying on day 15."""
    year, month = 2020, 1
    month += age_months
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}-{month:02d}-15"


@pytest.mark.parametrize("age, expected", [
    (0, 0.70), (11, 0.70), (12, 0.80), (23, 0.80), (24, 0.90), (35, 0.90),
    (36, 1.00), (59, 1.00), (60, 0.90), (71, 0.90), (72, 0.80), (144, 0.20),
])
def test_banded_then_decay_all_boundaries(age, expected):
    facts = {"incorporation_date": "2020-01-15"}
    checked_at = _checked_at_for_age(age)
    assert nm.normalize_banded_then_decay(P01, facts, checked_at) == pytest.approx(expected)


def test_banded_then_decay_never_goes_below_floor():
    facts = {"incorporation_date": "2000-01-15"}
    score = nm.normalize_banded_then_decay(P01, facts, "2026-01-15")  # very old company
    assert score >= 0.0


def test_banded_then_decay_missing_input_raises():
    # incorporation_date itself is missing, so the derived-input step fails first
    # (a direct 'on' field with no derivation would instead say "missing input").
    with pytest.raises(nm.NormalizationError, match="missing"):
        nm.normalize_banded_then_decay(P01, {}, "2026-01-15")


# ---------------- base_plus_terms ----------------
P03_CONTRACTS = {
    "type": "base_plus_terms", "required_facts": ["contracts_count"],
    "base": 0.50, "terms": [{"fact": "contracts_count", "per_unit": 0.005}], "clip": [0, 1],
}


@pytest.mark.parametrize("count, expected", [(0, 0.50), (4, 0.52), (100, 1.00), (200, 1.00)])
def test_base_plus_terms_per_unit_contracts(count, expected):
    score = nm.normalize_base_plus_terms(P03_CONTRACTS, {"contracts_count": count})
    assert score == pytest.approx(expected)


P11_VALUE_PROP = {
    "type": "base_plus_terms",
    "required_facts": ["value_proposition.description", "value_proposition.market_analysis", "value_proposition.value_proposition"],
    "base": 1.0,
    "terms": [
        {"fact": "value_proposition.description", "equals": "weak", "add": -0.20},
        {"fact": "value_proposition.market_analysis", "equals": "weak", "add": -0.20},
        {"fact": "value_proposition.value_proposition", "equals": "weak", "add": -0.20},
    ],
    "clip": [0, 1],
}


@pytest.mark.parametrize("weak_count, expected", [(0, 1.00), (1, 0.80), (2, 0.60), (3, 0.40)])
def test_base_plus_terms_equals_weak_points(weak_count, expected):
    labels = ["good"] * (3 - weak_count) + ["weak"] * weak_count
    facts = {"value_proposition": {"description": labels[0], "market_analysis": labels[1], "value_proposition": labels[2]}}
    assert nm.normalize_base_plus_terms(P11_VALUE_PROP, facts) == pytest.approx(expected)


P02_LICENSES = {
    "type": "base_plus_terms", "required_facts": ["licenses"], "base": 0.50,
    "terms": [
        {"fact": "licenses", "contains": "ISO", "add": 0.20},
        {"fact": "licenses", "contains": "financial", "add": 0.20},
        {"fact": "licenses", "contains": "fintech", "add": 0.10},
        {"fact": "licenses", "contains": "other", "add": 0.10},
    ],
    "clip": [0, 1],
}


@pytest.mark.parametrize("licenses, expected", [
    ([], 0.50), (["ISO"], 0.70), (["ISO", "financial"], 0.90),
    (["ISO", "financial", "fintech", "other"], 1.00), (["fintech"], 0.60),
])
def test_base_plus_terms_contains_licenses(licenses, expected):
    assert nm.normalize_base_plus_terms(P02_LICENSES, {"licenses": licenses}) == pytest.approx(expected)


def test_base_plus_terms_clips_to_range():
    spec = {"type": "base_plus_terms", "required_facts": ["x"], "base": 0.95,
            "terms": [{"fact": "x", "per_unit": 0.1}], "clip": [0, 1]}
    assert nm.normalize_base_plus_terms(spec, {"x": 5}) == 1.0  # would be 1.45 uncapped


def test_base_plus_terms_missing_per_unit_fact_raises():
    with pytest.raises(nm.NormalizationError, match="missing"):
        nm.normalize_base_plus_terms(P03_CONTRACTS, {})


# ---------------- categorical ----------------
P09_DEBT = {"type": "categorical", "required_facts": ["debt"], "fact": "debt",
            "map": {"none": 1.0, "scheduled": 0.5, "unscheduled": 0.2}}


@pytest.mark.parametrize("debt, expected", [("none", 1.0), ("scheduled", 0.5), ("unscheduled", 0.2)])
def test_categorical_debt(debt, expected):
    assert nm.normalize_categorical(P09_DEBT, {"debt": debt}) == expected


def test_categorical_unknown_value_raises():
    with pytest.raises(nm.NormalizationError, match="not a known value"):
        nm.normalize_categorical(P09_DEBT, {"debt": "secured"})


P10_AUDIT = {
    "type": "categorical", "required_facts": ["audit", "financials.has_revenue"], "fact": "audit",
    "map": {"positive_legit": 1.0, "not_legit": 0.8, "negative": 0.3,
            "none": {"when": "financials.has_revenue", "if_true": 0.4, "if_false": 0.7}},
}


@pytest.mark.parametrize("audit, has_revenue, expected", [
    ("positive_legit", True, 1.0), ("not_legit", False, 0.8), ("negative", True, 0.3),
    ("none", True, 0.4), ("none", False, 0.7),
])
def test_categorical_conditional_audit(audit, has_revenue, expected):
    facts = {"audit": audit, "financials": {"has_revenue": has_revenue}}
    assert nm.normalize_categorical(P10_AUDIT, facts) == expected


def test_categorical_conditional_missing_condition_raises():
    with pytest.raises(nm.NormalizationError, match="missing"):
        nm.normalize_categorical(P10_AUDIT, {"audit": "none"})


# ---------------- boolean_map ----------------
P06_SANCTIONS = {"type": "boolean_map", "required_facts": ["sanctions_hit"], "fact": "sanctions_hit",
                  "if_true": 0.0, "if_false": 1.0, "gate": True}


def test_boolean_map_true_and_false():
    assert nm.normalize_boolean_map(P06_SANCTIONS, {"sanctions_hit": True}) == 0.0
    assert nm.normalize_boolean_map(P06_SANCTIONS, {"sanctions_hit": False}) == 1.0


def test_boolean_map_missing_raises():
    with pytest.raises(nm.NormalizationError, match="missing"):
        nm.normalize_boolean_map(P06_SANCTIONS, {})


# ---------------- dispatch ----------------
def test_normalize_parameter_dispatches_by_type():
    assert nm.normalize_parameter(P09_DEBT, {"debt": "none"}, "2026-01-01") == 1.0
    assert nm.normalize_parameter(P06_SANCTIONS, {"sanctions_hit": False}, "2026-01-01") == 1.0


def test_normalize_parameter_unknown_type_raises():
    with pytest.raises(nm.NormalizationError, match="unknown rule type"):
        nm.normalize_parameter({"type": "magic"}, {}, "2026-01-01")


# ---------------- integration with the real config ----------------
def test_normalize_all_runs_clean_on_a_real_observation(loaded_config):
    facts = {
        "incorporation_date": "2021-09-01", "licenses": ["ISO", "financial", "fintech"], "contracts_count": 36,
        "founder_experience": {"bio_experiences": 3, "track_records": 2, "prior_ventures": 2},
        "advisors": {"relevant": 5, "irrelevant": 0}, "sanctions_hit": False,
        "financials": {"has_revenue": True, "revenue_growth_years": 4, "revenue_decline_years": 0,
                        "has_ebitda": True, "ebitda_growth_years": 4, "ebitda_decline_years": 0},
        "debt": "none", "audit": "positive_legit",
        "value_proposition": {"description": "good", "market_analysis": "good", "value_proposition": "good"},
    }
    scores = nm.normalize_all(loaded_config, facts, "2025-09-30")
    assert set(scores) == {p["id"] for p in loaded_config["parameters"]}
    assert all(0.0 <= v <= 1.0 for v in scores.values())
    assert scores["p06"] == 1.0  # no sanctions hit
    assert scores["p09"] == 1.0  # no debt
