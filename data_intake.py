from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from parser_config import ConfigError, load_config

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config" / "parameters_config.json"
DEFAULT_ENTITIES = BASE_DIR / "data" / "entities.json"
DEFAULT_RAW = BASE_DIR / "data" / "observations_raw.json"
DEFAULT_VALIDATED = BASE_DIR / "data" / "observations_validated.json"
DEFAULT_REJECTED = BASE_DIR / "data" / "observations_rejected.json"


class IntakeError(Exception):
    """Raised when intake cannot run at all (bad files or config/schema mismatch)."""

_COUNT = {"type": "count"}
_BOOL = {"type": "bool"}
_RATING = {"type": "enum", "allowed": ["good", "weak"]}

FACT_SCHEMA: dict[str, dict] = {
    "incorporation_date": {"type": "date"},
    "licenses": {"type": "list", "allowed": ["ISO", "financial", "fintech", "other"]},
    "contracts_count": _COUNT,
    "founder_experience.bio_experiences": _COUNT,
    "founder_experience.track_records": _COUNT,
    "founder_experience.prior_ventures": _COUNT,
    "advisors.relevant": _COUNT,
    "advisors.irrelevant": _COUNT,
    "sanctions_hit": _BOOL,
    "financials.has_revenue": _BOOL,
    "financials.revenue_growth_years": _COUNT,
    "financials.revenue_decline_years": _COUNT,
    "financials.has_ebitda": _BOOL,
    "financials.ebitda_growth_years": _COUNT,
    "financials.ebitda_decline_years": _COUNT,
    "debt": {"type": "enum", "allowed": ["none", "scheduled", "unscheduled"]},
    "audit": {"type": "enum", "allowed": ["positive_legit", "not_legit", "negative", "none"]},
    "value_proposition.description": _RATING,
    "value_proposition.market_analysis": _RATING,
    "value_proposition.value_proposition": _RATING,
}

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MISSING = object()

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _parse_date(value: Any) -> Optional[date]:
    """Strict YYYY-MM-DD only."""
    if not isinstance(value, str) or not _DATE_PATTERN.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None

def _get(data: dict, path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current

def _set(data: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value

def _flatten(data: dict, prefix: str = "") -> Iterator[tuple[str, Any]]:
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from _flatten(value, path + ".")
        else:
            yield path, value

def _check_value(value: Any, rule: dict) -> Optional[str]:
    """Return a problem description, or None if the value is valid."""
    kind = rule["type"]
    if kind == "date":
        return None if _parse_date(value) else "must be a date written as YYYY-MM-DD"
    if kind == "count":
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        return None if ok else "must be a whole number, 0 or more"
    if kind == "bool":
        return None if isinstance(value, bool) else "must be true or false"
    if kind == "enum":
        ok = isinstance(value, str) and value in rule["allowed"]
        return None if ok else "must be one of: " + ", ".join(rule["allowed"])
    if kind == "list":
        if not isinstance(value, list):
            return "must be a list"
        bad = [item for item in value if not isinstance(item, str) or item not in rule["allowed"]]
        if bad:
            return f"contains invalid entries {bad}; allowed: " + ", ".join(rule["allowed"])
        if len(set(value)) != len(value):
            return "contains duplicates"
        return None
    raise IntakeError(f"Unknown schema type '{kind}'")

def required_facts(config: dict) -> list[str]:
    facts: set[str] = set()
    for parameter in config["parameters"]:
        facts.update(parameter["rule_spec"]["required_facts"])
    return sorted(facts)

def _spec_value_references(spec: dict) -> Iterator[tuple[str, str]]:
    """Yield (fact, literal value) pairs that a rule compares against."""
    if spec["type"] == "categorical":
        for key in spec["map"]:
            yield spec["fact"], key
    elif spec["type"] == "base_plus_terms":
        for term in spec["terms"]:
            for kind in ("contains", "equals"):
                if kind in term:
                    yield term["fact"], term[kind]

def check_config_against_schema(config: dict) -> None:
    """Fail early if the rulebook needs a fact (or a value) that intake does not know."""
    problems: list[str] = []
    for fact in required_facts(config):
        if fact not in FACT_SCHEMA:
            problems.append(f"Config requires fact '{fact}', which intake does not know (add it to FACT_SCHEMA)")
    for parameter in config["parameters"]:
        for fact, value in _spec_value_references(parameter["rule_spec"]):
            allowed = FACT_SCHEMA.get(fact, {}).get("allowed")
            if allowed is not None and value not in allowed:
                problems.append(
                    f"{parameter['id']}: rule uses value '{value}' for '{fact}', "
                    f"but intake only accepts: {', '.join(allowed)}"
                )
    if problems:
        raise IntakeError("Config and intake disagree:\n  - " + "\n  - ".join(problems))

def _load_json(path: Path, what: str) -> Any:
    path = Path(path)
    if not path.exists():
        raise IntakeError(f"{what} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IntakeError(f"{what} is not valid JSON ({path}): {exc}") from exc

def _load_entities(path: Path) -> dict[str, dict]:
    data = _load_json(path, "Entities file")
    items = data.get("entities") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise IntakeError("Entities file must look like {\"entities\": [ ... ]}")
    entities: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise IntakeError(f"Every entity needs a text 'id': {item!r}")
        if item["id"] in entities:
            raise IntakeError(f"Duplicate entity id in entities file: {item['id']}")
        entities[item["id"]] = item
    return entities

def _load_raw(path: Path) -> list:
    data = _load_json(path, "Raw observations file")
    items = data.get("observations") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise IntakeError("Raw observations file must look like {\"observations\": [ ... ]}")
    return items

def _load_previous(path: Path) -> dict[tuple[str, str], dict]:
    """Earlier validated records, so re-running intake keeps their original ingested_at."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {(r["entity_id"], r["checked_at"]): r for r in data["observations"]}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}

def validate_record(
    record: Any,
    entities: dict[str, dict],
    required: set[str],
    seen: set[tuple[str, str]],
) -> tuple[Optional[dict], list[str], list[str]]:

    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(record, dict):
        return None, ["record is not a JSON object"], warnings

    # --- 1. Match to a known company -------------------------------------
    entity_id = record.get("entity_id")
    entity = None
    if not isinstance(entity_id, str) or not entity_id:
        errors.append("entity_id is missing")
    elif entity_id not in entities:
        errors.append(f"unknown entity '{entity_id}' (not in the entities file)")
    else:
        entity = entities[entity_id]

    checked_raw = record.get("checked_at")
    checked = _parse_date(checked_raw)
    if checked is None:
        errors.append("checked_at must be a date written as YYYY-MM-DD")

    facts = record.get("facts")
    if not isinstance(facts, dict):
        errors.append("facts is missing or not an object")
        return None, errors, warnings

    # --- 2. Check every fact against the schema ---------------------------
    clean_facts: dict = {}
    for path, rule in FACT_SCHEMA.items():
        value = _get(facts, path)
        if value is _MISSING:
            if path in required:
                errors.append(f"{path}: required fact is missing")
            continue
        problem = _check_value(value, rule)
        if problem:
            errors.append(f"{path}: {problem} (got {value!r})")
        else:
            _set(clean_facts, path, value)

    for path, _ in _flatten(facts):
        if path not in FACT_SCHEMA:
            warnings.append(f"{path}: unknown fact ignored")

    # --- 3. Facts must make sense together --------------------------------
    incorporation = _parse_date(clean_facts.get("incorporation_date"))
    if incorporation and checked and incorporation > checked:
        errors.append("incorporation_date is after checked_at")
    if incorporation and entity and entity.get("incorporation_date"):
        if entity["incorporation_date"] != clean_facts["incorporation_date"]:
            errors.append(
                f"incorporation_date {clean_facts['incorporation_date']} differs from the entities file "
                f"({entity['incorporation_date']})"
            )

    financials = clean_facts.get("financials", {})
    for prefix in ("revenue", "ebitda"):
        if financials.get(f"has_{prefix}") is False:
            for kind in ("growth", "decline"):
                key = f"{prefix}_{kind}_years"
                if financials.get(key, 0) != 0:
                    errors.append(f"financials.{key} must be 0 when financials.has_{prefix} is false")

    if not errors:
        key = (entity_id, checked_raw)
        if key in seen:
            errors.append(f"duplicate observation for {entity_id} on {checked_raw}")
        else:
            seen.add(key)

    if errors:
        return None, errors, warnings
    return {"entity_id": entity_id, "checked_at": checked_raw, "facts": clean_facts}, [], warnings

def run_intake(
    config_path: Path | str = DEFAULT_CONFIG,
    entities_path: Path | str = DEFAULT_ENTITIES,
    raw_path: Path | str = DEFAULT_RAW,
    validated_path: Path | str = DEFAULT_VALIDATED,
    rejected_path: Path | str = DEFAULT_REJECTED,
    now: Optional[str] = None,
) -> dict:
    now = now or _utc_now()

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        raise IntakeError(f"Cannot use the config: {exc}") from exc
    check_config_against_schema(config)
    required = set(required_facts(config))

    entities = _load_entities(Path(entities_path))
    raw_records = _load_raw(Path(raw_path))
    previous = _load_previous(Path(validated_path))

    validated: list[dict] = []
    rejected: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for index, record in enumerate(raw_records):
        clean, errors, warnings = validate_record(record, entities, required, seen)
        if errors:
            rejected.append({"index": index, "record": record, "errors": errors, "rejected_at": now})
            continue
        old = previous.get((clean["entity_id"], clean["checked_at"]))
        unchanged = old is not None and old.get("facts") == clean["facts"]
        validated.append({
            **clean,
            "ingested_at": old["ingested_at"] if unchanged and "ingested_at" in old else now,
            "config_version": config["config_version"],
            "warnings": warnings,
        })

    validated.sort(key=lambda r: (r["entity_id"], r["checked_at"]))

    for path, payload in (
        (Path(validated_path), {
            "generated_at": now, "config_version": config["config_version"],
            "count": len(validated), "observations": validated,
        }),
        (Path(rejected_path), {"generated_at": now, "count": len(rejected), "rejected": rejected}),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    per_entity: dict[str, int] = {}
    for record in validated:
        per_entity[record["entity_id"]] = per_entity.get(record["entity_id"], 0) + 1

    return {
        "config_version": config["config_version"],
        "total": len(raw_records),
        "validated": len(validated),
        "rejected": len(rejected),
        "warnings": sum(len(r["warnings"]) for r in validated),
        "per_entity": per_entity,
        "rejected_records": rejected,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate raw entity observations.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--entities", default=str(DEFAULT_ENTITIES))
    parser.add_argument("--raw", default=str(DEFAULT_RAW))
    parser.add_argument("--validated", default=str(DEFAULT_VALIDATED))
    parser.add_argument("--rejected", default=str(DEFAULT_REJECTED))
    parser.add_argument("--strict", action="store_true", help="Exit with code 2 if any record was rejected")
    args = parser.parse_args(argv)

    try:
        summary = run_intake(args.config, args.entities, args.raw, args.validated, args.rejected)
    except IntakeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Config version : {summary['config_version']}")
    print(f"Records read   : {summary['total']}")
    print(f"Validated      : {summary['validated']}")
    print(f"Rejected       : {summary['rejected']}")
    print(f"Warnings       : {summary['warnings']}")
    for entity_id, count in summary["per_entity"].items():
        print(f"  {entity_id}: {count} observation(s)")
    for item in summary["rejected_records"]:
        print(f"\nREJECTED record #{item['index']}:")
        for error in item["errors"]:
            print(f"  - {error}")
    return 2 if (args.strict and summary["rejected"]) else 0


if __name__ == "__main__":
    sys.exit(main())
