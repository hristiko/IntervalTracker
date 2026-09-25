"""
Interval check
==============
For one normalized score, asks a single yes/no question: is it inside the
allowed interval? Magnitude is deliberately ignored, as per the project's
own rule: being inside or outside the interval is what matters, not how
far off a value is.

Also flags the "gate" parameters (currently only the sanctions/criminal
check): when a gate parameter is out of its interval, the whole entity's
due-diligence is meant to fail immediately, not just lose weighted points.
"""
from __future__ import annotations


def is_compliant(score: float, interval: dict) -> bool:
    """True if lower <= score <= upper. Both boundaries count as inside."""
    return interval["lower"] <= score <= interval["upper"]


def check_all(config: dict, scores: dict[str, float]) -> dict[str, dict]:
    """
    scores: {parameter_id: normalized_score}, e.g. from normalize.normalize_all.

    Returns {parameter_id: {"compliant": bool, "gate": bool}} for every
    parameter in the config. "gate" marks parameters whose rule_spec has
    gate=true (see rule_specs.json); a gate parameter that is not
    compliant should force the whole entity's score to 0 (handled by the
    scoring engine, not here).
    """
    result = {}
    for parameter in config["parameters"]:
        pid = parameter["id"]
        if pid not in scores:
            raise KeyError(f"no normalized score given for {pid}")
        result[pid] = {
            "compliant": is_compliant(scores[pid], parameter["interval"]),
            "gate": bool(parameter["rule_spec"].get("gate", False)),
        }
    return result


def any_gate_violation(compliance: dict[str, dict]) -> bool:
    """True if any gate parameter is not compliant (the knockout condition)."""
    return any(entry["gate"] and not entry["compliant"] for entry in compliance.values())
