from __future__ import annotations

from typing import Optional


def _rank_positions(scores: dict[str, float]) -> dict[str, int]:
    """1-based rank, best (highest score) first."""
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return {entity_id: position + 1 for position, (entity_id, _) in enumerate(ordered)}


def recent_violation_count(records: list[dict], window: int) -> int:
    return sum(1 for record in records[-window:] if record["status"] == "Violated")


def build_leaderboard(
    history: dict[str, list[dict]],
    entity_names: dict[str, str],
    recent_window: int = 4,
) -> dict:
    current_scores = {eid: records[-1]["ewma_score"] for eid, records in history.items() if records}
    previous_scores = {
        eid: records[-2]["ewma_score"] for eid, records in history.items() if len(records) >= 2
    }

    current_ranks = _rank_positions(current_scores)
    previous_ranks = _rank_positions(previous_scores)

    rows = []
    for entity_id, records in history.items():
        if not records:
            continue
        latest = records[-1]
        trend: Optional[int] = None
        if entity_id in previous_ranks:
            trend = previous_ranks[entity_id] - current_ranks[entity_id]  # positive = moved up

        rows.append({
            "entity_id": entity_id,
            "name": entity_names.get(entity_id, entity_id),
            "score": latest["ewma_score"],
            "status": latest["status"],
            "as_of": latest["checked_at"],
            "trend": trend,
            "observations": len(records),
            "recent_violations": recent_violation_count(records, recent_window),
        })

    rows.sort(key=lambda row: (-row["score"], -row["observations"], row["recent_violations"], row["entity_id"]))
    for position, row in enumerate(rows, start=1):
        row["rank"] = position

    return {
        "count": len(rows),
        "compliant_count": sum(1 for row in rows if row["status"] == "Compliant"),
        "ranking": rows,
    }
