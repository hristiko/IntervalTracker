import pytest
from scipy.stats import beta as beta_dist

import beta_ewma as be


# ---------------- ParameterState.updated ----------------
def test_updated_compliant_matches_worked_example():
    # From the earlier explanation: alpha=4.2, beta=1.1, gamma=0.9, compliant.
    state = be.ParameterState(alpha=4.2, beta=1.1)
    new_state = state.updated(compliant=True, gamma=0.9)
    assert new_state.alpha == pytest.approx(4.78)
    assert new_state.beta == pytest.approx(0.99)


def test_updated_non_compliant_matches_worked_example():
    state = be.ParameterState(alpha=4.2, beta=1.1)
    new_state = state.updated(compliant=False, gamma=0.9)
    assert new_state.alpha == pytest.approx(3.78)
    assert new_state.beta == pytest.approx(1.99)


def test_updated_with_gamma_1_never_forgets():
    state = be.ParameterState(alpha=1.0, beta=1.0)
    for _ in range(5):
        state = state.updated(compliant=True, gamma=1.0)
    assert state.alpha == pytest.approx(6.0)  # 1 + 5 successes, no decay
    assert state.beta == pytest.approx(1.0)


def test_conservative_score_matches_scipy_directly():
    state = be.ParameterState(alpha=5.748, beta=1.063)
    assert state.conservative_score(0.10) == pytest.approx(beta_dist.ppf(0.10, 5.748, 1.063))


def test_mean_matches_alpha_over_alpha_plus_beta():
    state = be.ParameterState(alpha=3.0, beta=1.0)
    assert state.mean() == pytest.approx(0.75)


def test_more_evidence_of_same_ratio_raises_the_conservative_score():
    # 1 success / 1 check should be less trusted than 19 successes / 20 checks,
    # even though both have a 100% raw success rate.
    thin_evidence = be.ParameterState(alpha=1.0 + 1, beta=1.0)          # prior(1,1) + 1 success
    thick_evidence = be.ParameterState(alpha=1.0 + 19, beta=1.0)        # prior(1,1) + 19 successes
    assert thick_evidence.conservative_score(0.10) > thin_evidence.conservative_score(0.10)


# ---------------- ewma_update ----------------
def test_ewma_first_observation_returns_raw_unchanged():
    assert be.ewma_update(None, 0.42, lam=0.3) == 0.42


def test_ewma_matches_worked_example():
    # From the earlier explanation: previous S=0.72, R_t=0.60, lambda=0.3 -> 0.684
    assert be.ewma_update(0.72, 0.60, lam=0.3) == pytest.approx(0.684)


def test_ewma_lambda_1_ignores_history():
    assert be.ewma_update(0.9, 0.1, lam=1.0) == pytest.approx(0.1)


# ---------------- ScoringSettings ----------------
def test_scoring_settings_from_dict_defaults_recent_window():
    settings = be.ScoringSettings.from_dict(
        {"gamma": 0.9, "credible_level": 0.1, "lambda_ewma": 0.3, "prior_alpha": 2, "prior_beta": 2}
    )
    assert settings.recent_violations_window == 4


def test_scoring_settings_from_dict_honors_explicit_window():
    settings = be.ScoringSettings.from_dict(
        {"gamma": 0.9, "credible_level": 0.1, "lambda_ewma": 0.3, "prior_alpha": 2, "prior_beta": 2,
         "recent_violations_window": 8}
    )
    assert settings.recent_violations_window == 8


# ---------------- score_one_observation ----------------
SETTINGS = be.ScoringSettings(gamma=0.9, credible_level=0.10, lambda_ewma=0.3, prior_alpha=2.0, prior_beta=2.0)


def _compliance(flags: dict[str, bool], gate_id: str = "p06") -> dict:
    return {pid: {"compliant": ok, "gate": pid == gate_id} for pid, ok in flags.items()}


def test_score_one_observation_first_period_equals_raw_score():
    state = be.EntityScoringState()
    compliance = _compliance({"p01": True, "p06": True})
    weights = {"p01": 6, "p06": 1}
    state, details = be.score_one_observation(state, compliance, weights, SETTINGS, gated=False)
    assert details["raw_score"] == pytest.approx(details["ewma_score"])  # no history to blend with yet
    assert details["gated"] is False


def test_score_one_observation_weighted_average_across_parameters():
    state = be.EntityScoringState()
    compliance = _compliance({"p01": True, "p02": False})
    weights = {"p01": 3, "p02": 1}  # p01 should dominate
    state, details = be.score_one_observation(state, compliance, weights, SETTINGS, gated=False)
    q01 = details["parameters"]["p01"]["conservative_score"]
    q02 = details["parameters"]["p02"]["conservative_score"]
    expected = (3 * q01 + 1 * q02) / 4
    assert details["raw_score"] == pytest.approx(expected)
    assert q01 > q02  # p01 was compliant, p02 was not


def test_score_one_observation_gate_zeroes_everything_regardless_of_other_params():
    state = be.EntityScoringState()
    compliance = _compliance({"p01": True, "p02": True, "p06": False})  # everything fine except the gate
    weights = {"p01": 6, "p02": 8, "p06": 1}
    state, details = be.score_one_observation(state, compliance, weights, SETTINGS, gated=True)
    assert details["raw_score"] == 0.0
    assert details["ewma_score"] == 0.0
    assert details["gated"] is True
    # the per-parameter Beta states still update normally -- only the aggregate is zeroed
    assert details["parameters"]["p01"]["compliant"] is True


def test_score_one_observation_gate_resets_ewma_even_with_strong_history():
    state = be.EntityScoringState()
    weights = {"p01": 1, "p06": 1}
    for _ in range(5):
        state, _ = be.score_one_observation(state, _compliance({"p01": True, "p06": True}), weights, SETTINGS, gated=False)
    assert state.ewma_score > 0.4  # a real, positive score built up from 5 clean periods
    state, details = be.score_one_observation(state, _compliance({"p01": True, "p06": False}), weights, SETTINGS, gated=True)
    assert details["ewma_score"] == 0.0
    assert state.ewma_score == 0.0


def test_decay_lets_an_old_violation_fade_but_gamma_1_keeps_it_forever():
    """
    Counterintuitive but correct: with gamma=1 (no forgetting), a single early
    violation's contribution to beta never shrinks, so it permanently caps how
    confident the conservative score can become. With gamma<1, that one bad
    check's weight decays away, and a long run of later compliance can push
    the score back up close to 1. This is why the scoring engine's earlier
    "no-forgetting scores at least as high" intuition doesn't hold in general:
    decay doesn't just forget good history, it also forgets old skepticism.
    """
    def score_after_one_violation_then_compliance(gamma, periods_of_compliance):
        state = be.ParameterState(alpha=SETTINGS.prior_alpha, beta=SETTINGS.prior_beta)
        state = state.updated(compliant=False, gamma=gamma)
        for _ in range(periods_of_compliance):
            state = state.updated(compliant=True, gamma=gamma)
        return state.conservative_score(SETTINGS.credible_level)

    with_decay = score_after_one_violation_then_compliance(gamma=0.5, periods_of_compliance=20)
    without_decay = score_after_one_violation_then_compliance(gamma=1.0, periods_of_compliance=20)
    assert with_decay > without_decay


def test_score_one_observation_ewma_blends_with_previous_state():
    state = be.EntityScoringState()
    weights = {"p01": 1}
    state, first = be.score_one_observation(state, _compliance({"p01": True}), weights, SETTINGS, gated=False)
    state, second = be.score_one_observation(state, _compliance({"p01": True}), weights, SETTINGS, gated=False)
    expected = be.ewma_update(first["ewma_score"], second["raw_score"], SETTINGS.lambda_ewma)
    assert second["ewma_score"] == pytest.approx(expected)


def test_entity_scoring_state_reuses_parameter_state_across_calls():
    state = be.EntityScoringState()
    first = state.parameter_state("p01", SETTINGS)
    assert first.alpha == SETTINGS.prior_alpha
    first.alpha = 999.0  # mutate in place, as score_one_observation would after an update
    second = state.parameter_state("p01", SETTINGS)
    assert second.alpha == 999.0  # same object, not re-created from the prior
