from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from scipy.stats import beta as beta_dist


@dataclass
class ParameterState:
    alpha: float
    beta: float

    def updated(self, compliant: bool, gamma: float) -> "ParameterState":
        """
        Decay old evidence by gamma, then add one observation.
        gamma in [0, 1]: 1.0 never forgets, lower values forget faster.
        compliant=True adds to alpha (reward), False adds to beta (penalty).
        """
        return ParameterState(
            alpha=gamma * self.alpha + (1.0 if compliant else 0.0),
            beta=gamma * self.beta + (0.0 if compliant else 1.0),
        )

    def conservative_score(self, credible_level: float) -> float:
        """
        A cautious estimate of the true compliance rate: the value below
        which the rate would fall only `credible_level` of the time.
        This is what makes 1 success out of 1 check score lower than
        19 successes out of 20: the engine is unsure about the first
        entity and says so, rather than taking its lone data point at
        face value.
        """
        return float(beta_dist.ppf(credible_level, self.alpha, self.beta))

    def mean(self) -> float:
        """Plain expected compliance rate, alpha / (alpha + beta). Not used
        for ranking (see conservative_score), but useful for display."""
        return self.alpha / (self.alpha + self.beta)


def initial_state(prior_alpha: float, prior_beta: float) -> ParameterState:
    return ParameterState(alpha=prior_alpha, beta=prior_beta)


def ewma_update(previous: Optional[float], raw: float, lam: float) -> float:
    """
    Blend a new raw score with the entity's running score.
    previous=None (first observation ever) returns raw unchanged.
    lam in (0, 1]: higher means the new evidence counts for more.
    """
    if previous is None:
        return raw
    return lam * raw + (1.0 - lam) * previous


@dataclass
class ScoringSettings:
    gamma: float
    credible_level: float
    lambda_ewma: float
    prior_alpha: float
    prior_beta: float
    recent_violations_window: int = 4

    @classmethod
    def from_dict(cls, data: dict) -> "ScoringSettings":
        return cls(
            gamma=data["gamma"],
            credible_level=data["credible_level"],
            lambda_ewma=data["lambda_ewma"],
            prior_alpha=data["prior_alpha"],
            prior_beta=data["prior_beta"],
            recent_violations_window=data.get("recent_violations_window", 4),
        )


@dataclass
class EntityScoringState:
    """Everything that needs to persist between one observation and the next."""
    parameter_states: dict[str, ParameterState] = field(default_factory=dict)
    ewma_score: Optional[float] = None

    def parameter_state(self, parameter_id: str, settings: ScoringSettings) -> ParameterState:
        if parameter_id not in self.parameter_states:
            self.parameter_states[parameter_id] = initial_state(settings.prior_alpha, settings.prior_beta)
        return self.parameter_states[parameter_id]


def score_one_observation(
    state: EntityScoringState,
    compliance: dict[str, dict],
    weights: dict[str, float],
    settings: ScoringSettings,
    gated: bool,
) -> tuple[EntityScoringState, dict]:
    parameter_details: dict[str, dict] = {}
    weighted_sum = 0.0
    total_weight = 0.0

    for parameter_id, entry in compliance.items():
        previous = state.parameter_state(parameter_id, settings)
        updated = previous.updated(entry["compliant"], settings.gamma)
        state.parameter_states[parameter_id] = updated

        q = updated.conservative_score(settings.credible_level)
        weight = weights[parameter_id]
        weighted_sum += weight * q
        total_weight += weight

        parameter_details[parameter_id] = {
            "compliant": entry["compliant"],
            "alpha": updated.alpha,
            "beta": updated.beta,
            "conservative_score": q,
        }

    raw_score = 0.0 if gated else (weighted_sum / total_weight if total_weight else 0.0)

    if gated:
        new_ewma = 0.0
    else:
        new_ewma = ewma_update(state.ewma_score, raw_score, settings.lambda_ewma)

    state.ewma_score = new_ewma

    details = {
        "parameters": parameter_details,
        "raw_score": raw_score,
        "ewma_score": new_ewma,
        "gated": gated,
    }
    return state, details
