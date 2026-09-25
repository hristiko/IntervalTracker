from __future__ import annotations

from datetime import date
from typing import Any, Optional


class NormalizationError(Exception):
    """Raised when facts cannot be normalized with the given rule_spec."""


def get_fact(facts: dict, path: str) -> Any:
    """Read a dot-notation fact, e.g. 'financials.has_revenue'. Missing -> None."""
    current: Any = facts
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def months_between(from_date: str, to_date: str) -> int:
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def _clip(value: float, bounds: list[float]) -> float:
    lower, upper = bounds
    return max(lower, min(upper, value))


def _derived_inputs(rule_spec: dict, facts: dict, checked_at: str) -> dict[str, Any]:
    derived: dict[str, Any] = {}
    for name, definition in rule_spec.get("derived_inputs", {}).items():
        if definition["op"] == "months_between":
            from_value = get_fact(facts, definition["from"])
            to_value = checked_at if definition["to"] == "checked_at" else get_fact(facts, definition["to"])
            if from_value is None or to_value is None:
                raise NormalizationError(f"cannot compute '{name}': missing date")
            derived[name] = months_between(from_value, to_value)
        else:
            raise NormalizationError(f"unknown derived op '{definition['op']}'")
    return derived


def _lookup(name: str, facts: dict, derived: dict[str, Any]) -> Any:
    return derived[name] if name in derived else get_fact(facts, name)


def normalize_banded_then_decay(rule_spec: dict, facts: dict, checked_at: str) -> float:
    derived = _derived_inputs(rule_spec, facts, checked_at)
    x = _lookup(rule_spec["on"], facts, derived)
    if x is None:
        raise NormalizationError(f"missing input '{rule_spec['on']}'")

    for low, high, value in rule_spec["bands"]:
        if low <= x < high:
            return value

    after = rule_spec["after"]
    if x >= after["start_at"]:
        steps = (x - after["start_at"]) // after["step_size"] + 1
        return max(after["floor"], after["base"] + steps * after["per_step"])

    raise NormalizationError(f"value {x} is not covered by any band (bands must start at 0)")


def normalize_base_plus_terms(rule_spec: dict, facts: dict) -> float:
    value = rule_spec["base"]
    for term in rule_spec["terms"]:
        fact_value = get_fact(facts, term["fact"])
        if "per_unit" in term:
            if fact_value is None:
                raise NormalizationError(f"missing '{term['fact']}'")
            value += fact_value * term["per_unit"]
        elif "contains" in term:
            if fact_value is not None and term["contains"] in fact_value:
                value += term["add"]
        elif "equals" in term:
            if fact_value == term["equals"]:
                value += term["add"]
    return _clip(value, rule_spec.get("clip", [0, 1]))


def normalize_categorical(rule_spec: dict, facts: dict) -> float:
    fact_value = get_fact(facts, rule_spec["fact"])
    entry = rule_spec["map"].get(fact_value)
    if entry is None:
        raise NormalizationError(f"'{fact_value}' is not a known value for '{rule_spec['fact']}'")
    if isinstance(entry, dict):
        condition = get_fact(facts, entry["when"])
        if condition is None:
            raise NormalizationError(f"missing '{entry['when']}' needed to resolve '{rule_spec['fact']}'")
        return entry["if_true"] if condition else entry["if_false"]
    return entry


def normalize_boolean_map(rule_spec: dict, facts: dict) -> float:
    fact_value = get_fact(facts, rule_spec["fact"])
    if fact_value is None:
        raise NormalizationError(f"missing '{rule_spec['fact']}'")
    return rule_spec["if_true"] if fact_value else rule_spec["if_false"]


_DISPATCH = {
    "banded_then_decay": lambda spec, facts, checked_at: normalize_banded_then_decay(spec, facts, checked_at),
    "base_plus_terms": lambda spec, facts, checked_at: normalize_base_plus_terms(spec, facts),
    "categorical": lambda spec, facts, checked_at: normalize_categorical(spec, facts),
    "boolean_map": lambda spec, facts, checked_at: normalize_boolean_map(spec, facts),
}


def normalize_parameter(rule_spec: dict, facts: dict, checked_at: str) -> float:
    """Dispatch to the right rule type and return a score in [0, 1]."""
    handler = _DISPATCH.get(rule_spec["type"])
    if handler is None:
        raise NormalizationError(f"unknown rule type '{rule_spec['type']}'")
    score = handler(rule_spec, facts, checked_at)
    if not (0.0 <= score <= 1.0):
        raise NormalizationError(f"rule produced an out-of-range score: {score}")
    return score


def normalize_all(config: dict, facts: dict, checked_at: str) -> dict[str, float]:
    """Normalize every parameter in the config for one observation. Returns {id: score}."""
    return {
        p["id"]: normalize_parameter(p["rule_spec"], facts, checked_at)
        for p in config["parameters"]
    }
