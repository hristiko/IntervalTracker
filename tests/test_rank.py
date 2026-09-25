import pytest

import rank


def rec(checked_at, score, status="Compliant"):
    return {"checked_at": checked_at, "ewma_score": score, "status": status}


# ---------------- recent_violation_count ----------------
def test_recent_violation_count_within_window():
    records = [rec("2026-01-01", 0.5, "Violated"), rec("2026-02-01", 0.5, "Compliant"),
               rec("2026-03-01", 0.5, "Violated"), rec("2026-04-01", 0.5, "Violated")]
    assert rank.recent_violation_count(records, window=2) == 2  # last 2: Violated, Violated
    assert rank.recent_violation_count(records, window=4) == 3


def test_recent_violation_count_window_larger_than_history():
    records = [rec("2026-01-01", 0.5, "Violated")]
    assert rank.recent_violation_count(records, window=10) == 1


# ---------------- build_leaderboard: sorting ----------------
def test_leaderboard_sorts_by_score_descending():
    history = {
        "a": [rec("2026-01-01", 0.3)],
        "b": [rec("2026-01-01", 0.9)],
        "c": [rec("2026-01-01", 0.6)],
    }
    board = rank.build_leaderboard(history, {"a": "A", "b": "B", "c": "C"})
    assert [row["entity_id"] for row in board["ranking"]] == ["b", "c", "a"]
    assert [row["rank"] for row in board["ranking"]] == [1, 2, 3]


def test_leaderboard_tie_break_by_more_observations_then_fewer_violations_then_id():
    history = {
        "z": [rec("2026-01-01", 0.5), rec("2026-02-01", 0.5)],                       # 2 obs, 0 violations
        "y": [rec("2026-01-01", 0.5, "Violated"), rec("2026-02-01", 0.5, "Violated")],  # 2 obs, 2 violations
        "x": [rec("2026-01-01", 0.5)],                                               # 1 obs
    }
    board = rank.build_leaderboard(history, {"z": "Z", "y": "Y", "x": "X"})
    # same score everywhere: more observations wins first (z, y tie there), then fewer violations (z before y), then x last
    assert [row["entity_id"] for row in board["ranking"]] == ["z", "y", "x"]


def test_leaderboard_skips_entities_with_no_records():
    history = {"a": [rec("2026-01-01", 0.5)], "b": []}
    board = rank.build_leaderboard(history, {"a": "A", "b": "B"})
    assert [row["entity_id"] for row in board["ranking"]] == ["a"]
    assert board["count"] == 1


def test_leaderboard_uses_entity_id_as_name_fallback():
    history = {"a": [rec("2026-01-01", 0.5)]}
    board = rank.build_leaderboard(history, {})  # no names given
    assert board["ranking"][0]["name"] == "a"


def test_leaderboard_compliant_count():
    history = {
        "a": [rec("2026-01-01", 0.9, "Compliant")],
        "b": [rec("2026-01-01", 0.5, "Violated")],
        "c": [rec("2026-01-01", 0.7, "Compliant")],
    }
    board = rank.build_leaderboard(history, {"a": "A", "b": "B", "c": "C"})
    assert board["compliant_count"] == 2


# ---------------- trend ----------------
def test_trend_is_none_with_only_one_observation():
    history = {"a": [rec("2026-01-01", 0.5)]}
    board = rank.build_leaderboard(history, {"a": "A"})
    assert board["ranking"][0]["trend"] is None


def test_trend_positive_when_entity_moved_up():
    # 'a' was behind 'b' last period (a=0.3, b=0.9) but overtakes this period (a=0.9, b=0.3)
    history = {
        "a": [rec("2026-01-01", 0.3), rec("2026-02-01", 0.9)],
        "b": [rec("2026-01-01", 0.9), rec("2026-02-01", 0.3)],
    }
    board = rank.build_leaderboard(history, {"a": "A", "b": "B"})
    by_id = {row["entity_id"]: row for row in board["ranking"]}
    assert by_id["a"]["rank"] == 1
    assert by_id["a"]["trend"] == 1   # moved from rank 2 to rank 1
    assert by_id["b"]["trend"] == -1  # moved from rank 1 to rank 2


def test_trend_zero_when_rank_unchanged():
    history = {
        "a": [rec("2026-01-01", 0.9), rec("2026-02-01", 0.95)],
        "b": [rec("2026-01-01", 0.1), rec("2026-02-01", 0.15)],
    }
    board = rank.build_leaderboard(history, {"a": "A", "b": "B"})
    by_id = {row["entity_id"]: row for row in board["ranking"]}
    assert by_id["a"]["trend"] == 0
    assert by_id["b"]["trend"] == 0
