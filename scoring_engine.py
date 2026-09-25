from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from beta_ewma import EntityScoringState, ScoringSettings, score_one_observation
from interval_check import any_gate_violation, check_all
from normalize import NormalizationError, normalize_all
from parser_config import ConfigError, load_config
from rank import build_leaderboard

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config" / "parameters_config.json"
DEFAULT_SETTINGS = BASE_DIR / "config" / "scoring_config.json"
DEFAULT_ENTITIES = BASE_DIR / "data" / "entities.json"
DEFAULT_VALIDATED = BASE_DIR / "data" / "observations_validated.json"
DEFAULT_HISTORY = BASE_DIR / "data" / "scores_history.json"
DEFAULT_LEADERBOARD = BASE_DIR / "data" / "leaderboard.json"

REQUIRED_SETTINGS = ("gamma", "credible_level", "lambda_ewma", "prior_alpha", "prior_beta")


class ScoringError(Exception):
    """Raised when the engine cannot run at all (bad inputs, bad settings)."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, what: str):
    path = Path(path)
    if not path.exists():
        raise ScoringError(f"{what} not found: {path}. Run parser_config.py and data_intake.py first.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScoringError(f"{what} is not valid JSON ({path}): {exc}") from exc


def load_settings(path: Path | str = DEFAULT_SETTINGS) -> ScoringSettings:
    data = _read_json(Path(path), "Scoring settings file")
    missing = [key for key in REQUIRED_SETTINGS if key not in data]
    if missing:
        raise ScoringError(f"Scoring settings file is missing: {', '.join(missing)}")
    if not (0 < data["gamma"] <= 1):
        raise ScoringError("gamma must be in (0, 1]")
    if not (0 < data["credible_level"] < 1):
        raise ScoringError("credible_level must be in (0, 1)")
    if not (0 < data["lambda_ewma"] <= 1):
        raise ScoringError("lambda_ewma must be in (0, 1]")
    if data["prior_alpha"] <= 0 or data["prior_beta"] <= 0:
        raise ScoringError("prior_alpha and prior_beta must be positive")
    return ScoringSettings.from_dict(data)


def load_entity_names(path: Path | str = DEFAULT_ENTITIES) -> dict[str, str]:
    data = _read_json(Path(path), "Entities file")
    items = data.get("entities") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ScoringError("Entities file must look like {\"entities\": [ ... ]}")
    return {item["id"]: item.get("name", item["id"]) for item in items}


def group_observations(validated_path: Path | str) -> dict[str, list[dict]]:
    data = _read_json(Path(validated_path), "Validated observations file")
    records = data.get("observations") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise ScoringError("Validated observations file must look like {\"observations\": [ ... ]}")
    if not records:
        raise ScoringError("No validated observations to score. Run data_intake.py first.")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[record["entity_id"]].append(record)
    for entity_id in grouped:
        grouped[entity_id].sort(key=lambda r: r["checked_at"])
    return dict(grouped)


def score_entity(
    config: dict,
    observations: list[dict],
    settings: ScoringSettings,
) -> list[dict]:
    weights = {p["id"]: p["weight"] for p in config["parameters"]}
    state = EntityScoringState()
    records: list[dict] = []

    for observation in observations:
        facts = observation["facts"]
        checked_at = observation["checked_at"]

        try:
            scores = normalize_all(config, facts, checked_at)
        except NormalizationError as exc:
            raise ScoringError(
                f"{observation['entity_id']} on {checked_at}: {exc} "
                "(a valid record should never fail normalization; check rule_specs.json "
                "against the FACT_SCHEMA in data_intake.py)"
            ) from exc

        compliance = check_all(config, scores)
        gated = any_gate_violation(compliance)
        all_compliant = all(entry["compliant"] for entry in compliance.values())
        status = "Violated" if (gated or not all_compliant) else "Compliant"

        state, details = score_one_observation(state, compliance, weights, settings, gated)

        records.append({
            "checked_at": checked_at,
            "status": status,
            "normalized_scores": scores,
            **details,
        })

    return records


def run_scoring(
    config_path: Path | str = DEFAULT_CONFIG,
    settings_path: Path | str = DEFAULT_SETTINGS,
    entities_path: Path | str = DEFAULT_ENTITIES,
    validated_path: Path | str = DEFAULT_VALIDATED,
    history_path: Path | str = DEFAULT_HISTORY,
    leaderboard_path: Path | str = DEFAULT_LEADERBOARD,
    now: Optional[str] = None,
) -> dict:
    now = now or _utc_now()

    try:
        config = load_config(config_path, require_complete_intervals=True)
    except ConfigError as exc:
        raise ScoringError(f"Cannot use the config: {exc}") from exc

    settings = load_settings(settings_path)
    entity_names = load_entity_names(entities_path)
    grouped = group_observations(validated_path)

    unknown = sorted(set(grouped) - set(entity_names))
    if unknown:
        raise ScoringError(f"Observations exist for entities not in the entities file: {', '.join(unknown)}")

    history: dict[str, list[dict]] = {}
    for entity_id, observations in grouped.items():
        history[entity_id] = score_entity(config, observations, settings)

    history_payload = {
        "generated_at": now,
        "config_version": config["config_version"],
        "settings": {
            "gamma": settings.gamma, "credible_level": settings.credible_level,
            "lambda_ewma": settings.lambda_ewma,
            "prior_alpha": settings.prior_alpha, "prior_beta": settings.prior_beta,
        },
        "entities": history,
    }
    Path(history_path).parent.mkdir(parents=True, exist_ok=True)
    Path(history_path).write_text(json.dumps(history_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    leaderboard = build_leaderboard(history, entity_names, recent_window=settings.recent_violations_window)
    leaderboard_payload = {"generated_at": now, "config_version": config["config_version"], **leaderboard}
    Path(leaderboard_path).write_text(json.dumps(leaderboard_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "config_version": config["config_version"],
        "entities_scored": len(history),
        "history_path": str(history_path),
        "leaderboard_path": str(leaderboard_path),
        "leaderboard": leaderboard,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scoring engine on validated observations.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--settings", default=str(DEFAULT_SETTINGS))
    parser.add_argument("--entities", default=str(DEFAULT_ENTITIES))
    parser.add_argument("--validated", default=str(DEFAULT_VALIDATED))
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    parser.add_argument("--leaderboard", default=str(DEFAULT_LEADERBOARD))
    args = parser.parse_args(argv)

    try:
        result = run_scoring(
            args.config, args.settings, args.entities, args.validated, args.history, args.leaderboard
        )
    except ScoringError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Config version   : {result['config_version']}")
    print(f"Entities scored  : {result['entities_scored']}")
    print(f"History written  : {result['history_path']}")
    print(f"Leaderboard      : {result['leaderboard_path']}")
    print()
    print(f"{'Rank':<5}{'Entity':<24}{'Score':<8}{'Status':<11}{'Trend':<7}Obs")
    for row in result["leaderboard"]["ranking"]:
        trend = "-" if row["trend"] is None else (f"+{row['trend']}" if row["trend"] > 0 else str(row["trend"]))
        print(f"{row['rank']:<5}{row['name'][:22]:<24}{row['score']:.3f}   {row['status']:<11}{trend:<7}{row['observations']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
