from __future__ import annotations


def is_compliant(score: float, interval: dict) -> bool:
    """True if lower <= score <= upper. Both boundaries count as inside."""
    return interval["lower"] <= score <= interval["upper"]


def check_all(config: dict, scores: dict[str, float]) -> dict[str, dict]:
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
