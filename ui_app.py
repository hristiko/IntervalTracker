from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import data_intake
import parser_config
import scoring_engine


# =============================================================================
# PAGE SETUP
# =============================================================================

st.set_page_config(
    page_title="Entity Scoring",
    page_icon="📊",
    layout="wide",
)

STATUS_COLORS = {
    "Compliant": "#2E7D32",
    "Violated": "#D32F2F",
    "Knockout": "#7F0000",
}

CARD_STYLES = {
    "blue": ("#EAF3FF", "#0D47A1", "#90CAF9"),
    "green": ("#EAF7EE", "#1B5E20", "#A5D6A7"),
    "red": ("#FDECEC", "#B71C1C", "#EF9A9A"),
    "darkred": ("#FBE9E9", "#7F0000", "#D88888"),
    "orange": ("#FFF3E0", "#E65100", "#FFCC80"),
    "gray": ("#F1F3F5", "#37474F", "#CFD8DC"),
}

st.markdown(
    """
<style>
.block-container {
    padding-top: 1.6rem;
    padding-bottom: 3rem;
    max-width: 1500px;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    line-height: 1.35;
}
div[data-testid="stFileUploader"] {
    border-radius: 12px;
}
.small-muted {
    opacity: 0.72;
    font-size: 0.88rem;
}
</style>
""",
    unsafe_allow_html=True,
)


# =============================================================================
# ERRORS / JSON HELPERS
# =============================================================================


class UIImportError(Exception):
    """Raised when data uploaded through the UI cannot safely be imported."""


def read_json(path: Path | str) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def write_json_atomic(path: Path | str, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temp_path.replace(path)


def parse_uploaded_json(uploaded_file, label: str) -> dict:
    try:
        text = uploaded_file.getvalue().decode("utf-8-sig")
        payload = json.loads(text)
    except UnicodeDecodeError as exc:
        raise UIImportError(f"{label} must be UTF-8 JSON.") from exc
    except json.JSONDecodeError as exc:
        raise UIImportError(f"{label} is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise UIImportError(f"{label} must contain a JSON object at the top level.")
    return payload


# =============================================================================
# DISPLAY HELPERS
# =============================================================================


def score_percent(score: float | None) -> float | None:
    if score is None:
        return None
    return round(float(score) * 100.0, 1)


def trend_label(trend: int | None) -> str:
    if trend is None:
        return "—"
    if trend > 0:
        return f"↑ {trend}"
    if trend < 0:
        return f"↓ {abs(trend)}"
    return "→ 0"


def status_label(status: str) -> str:
    return {
        "Compliant": "🟢 Compliant",
        "Violated": "🔴 Violated",
        "Knockout": "⛔ Knockout",
    }.get(status, status)


def interval_label(interval: dict) -> str:
    lower = interval.get("lower")
    upper = interval.get("upper")
    if lower is None or upper is None:
        return "Not configured"
    return f"[{float(lower):.2f}, {float(upper):.2f}]"


def metric_card(label: str, value: str | int, tone: str, note: str = "") -> None:
    bg, fg, border = CARD_STYLES[tone]
    # Keep the HTML on one physical line so Markdown never interprets indentation as code.
    html = (
        f'<div style="background:{bg};color:{fg};border:1px solid {border};'
        'border-radius:14px;padding:16px 18px;min-height:112px;">'
        f'<div style="font-size:0.86rem;font-weight:700;margin-bottom:6px;">{label}</div>'
        f'<div style="font-size:2rem;font-weight:800;line-height:1.05;">{value}</div>'
        f'<div style="font-size:0.80rem;margin-top:8px;opacity:0.78;">{note}</div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# =============================================================================
# EXISTING PIPELINE
# =============================================================================


def current_excel_path() -> Path:
    default = Path(parser_config.DEFAULT_EXCEL)
    if default.exists():
        return default

    fallback = Path(parser_config.CONFIG_DIR) / "Due Dilligence Weighed Parameters.xlsx"
    if fallback.exists():
        return fallback

    return default


def load_current_config() -> dict | None:
    try:
        return parser_config.load_config(parser_config.DEFAULT_OUTPUT)
    except (parser_config.ConfigError, FileNotFoundError):
        return None


def rebuild_parameter_config() -> dict:
    config = parser_config.build_config(
        excel_path=current_excel_path(),
        rule_specs_path=parser_config.DEFAULT_RULE_SPECS,
        intervals_path=parser_config.DEFAULT_INTERVALS,
    )
    parser_config.save_config(
        config=config,
        output_path=parser_config.DEFAULT_OUTPUT,
        versions_dir=parser_config.DEFAULT_VERSIONS_DIR,
    )
    return config


def run_complete_pipeline() -> tuple[dict, dict]:
    intake_result = data_intake.run_intake(
        config_path=data_intake.DEFAULT_CONFIG,
        entities_path=data_intake.DEFAULT_ENTITIES,
        raw_path=data_intake.DEFAULT_RAW,
        validated_path=data_intake.DEFAULT_VALIDATED,
        rejected_path=data_intake.DEFAULT_REJECTED,
    )

    scoring_result = scoring_engine.run_scoring(
        config_path=scoring_engine.DEFAULT_CONFIG,
        settings_path=scoring_engine.DEFAULT_SETTINGS,
        entities_path=scoring_engine.DEFAULT_ENTITIES,
        validated_path=scoring_engine.DEFAULT_VALIDATED,
        history_path=scoring_engine.DEFAULT_HISTORY,
        leaderboard_path=scoring_engine.DEFAULT_LEADERBOARD,
    )
    return intake_result, scoring_result


def load_result_files() -> tuple[dict | None, dict | None]:
    history_path = Path(scoring_engine.DEFAULT_HISTORY)
    leaderboard_path = Path(scoring_engine.DEFAULT_LEADERBOARD)

    history = read_json(history_path) if history_path.exists() else None
    leaderboard = read_json(leaderboard_path) if leaderboard_path.exists() else None
    return history, leaderboard


# =============================================================================
# SCORING RESULT HELPERS
# =============================================================================


def parameter_name_map(config: dict) -> dict[str, str]:
    return {p["id"]: p["parameter"] for p in config["parameters"]}


def violated_parameters_for_latest(entity_id: str, history: dict, config: dict) -> list[str]:
    records = history.get("entities", {}).get(entity_id, [])
    if not records:
        return []

    names = parameter_name_map(config)
    latest = records[-1]
    violations = []

    for parameter_id, detail in latest.get("parameters", {}).items():
        if not detail.get("compliant", False):
            violations.append(names.get(parameter_id, parameter_id))

    return violations


def latest_is_gated(entity_id: str, history: dict) -> bool:
    records = history.get("entities", {}).get(entity_id, [])
    return bool(records and records[-1].get("gated", False))


def visual_status(entity_id: str, leaderboard_row: dict, history: dict) -> str:
    if latest_is_gated(entity_id, history):
        return "Knockout"
    return leaderboard_row.get("status", "Unknown")


def current_snapshot(entity_ids: set[str]) -> dict[str, dict]:
    config = load_current_config()
    history, leaderboard = load_result_files()

    if config is None or history is None or leaderboard is None:
        return {}

    by_id = {row["entity_id"]: row for row in leaderboard.get("ranking", [])}
    result = {}

    for entity_id in entity_ids:
        row = by_id.get(entity_id)
        if row is None:
            result[entity_id] = {
                "entity_id": entity_id,
                "name": entity_id,
                "score": None,
                "rank": None,
                "status": None,
                "violated": [],
            }
            continue

        result[entity_id] = {
            "entity_id": entity_id,
            "name": row.get("name", entity_id),
            "score": row.get("score"),
            "rank": row.get("rank"),
            "status": visual_status(entity_id, row, history),
            "violated": violated_parameters_for_latest(entity_id, history, config),
        }

    return result


# =============================================================================
# IMPORT / MERGE LOGIC
# =============================================================================


def validate_entities_payload(payload: dict) -> list[dict]:
    entities = payload.get("entities")
    if not isinstance(entities, list):
        raise UIImportError('Entity file must look like {"entities": [ ... ]}.')

    seen = set()
    for index, entity in enumerate(entities, start=1):
        if not isinstance(entity, dict):
            raise UIImportError(f"Entity #{index} is not a JSON object.")

        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise UIImportError(f"Entity #{index} needs a non-empty text id.")

        if entity_id in seen:
            raise UIImportError(f"Duplicate entity id in uploaded file: {entity_id}")
        seen.add(entity_id)

    return entities


def validate_observations_payload(payload: dict) -> list[dict]:
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise UIImportError('Observation file must look like {"observations": [ ... ]}.')

    seen = set()
    for index, observation in enumerate(observations, start=1):
        if not isinstance(observation, dict):
            raise UIImportError(f"Observation #{index} is not a JSON object.")

        entity_id = observation.get("entity_id")
        checked_at = observation.get("checked_at")
        facts = observation.get("facts")

        if not isinstance(entity_id, str) or not entity_id:
            raise UIImportError(f"Observation #{index} is missing entity_id.")
        if not isinstance(checked_at, str) or not checked_at:
            raise UIImportError(f"Observation #{index} is missing checked_at.")
        if not isinstance(facts, dict):
            raise UIImportError(f"Observation #{index} is missing a facts object.")

        key = (entity_id, checked_at)
        if key in seen:
            raise UIImportError(
                f"Uploaded file contains the same observation twice: {entity_id} on {checked_at}."
            )
        seen.add(key)

    return observations


def merge_entities(current_payload: dict, incoming_payload: dict | None):
    current_items = current_payload.get("entities", [])
    if not isinstance(current_items, list):
        raise UIImportError("Current entities.json is not valid.")

    if incoming_payload is None:
        return current_payload, {"added": 0, "updated": 0, "unchanged": 0}, set()

    incoming_items = validate_entities_payload(incoming_payload)
    by_id = {item["id"]: dict(item) for item in current_items}
    order = [item["id"] for item in current_items]

    stats = {"added": 0, "updated": 0, "unchanged": 0}
    affected = set()

    for item in incoming_items:
        entity_id = item["id"]
        affected.add(entity_id)

        if entity_id in by_id:
            merged = {**by_id[entity_id], **item}
            if merged == by_id[entity_id]:
                stats["unchanged"] += 1
            else:
                stats["updated"] += 1
            by_id[entity_id] = merged
        else:
            if not isinstance(item.get("name"), str) or not item.get("name"):
                raise UIImportError(f"New entity {entity_id} needs a non-empty name.")
            if not isinstance(item.get("incorporation_date"), str) or not item.get("incorporation_date"):
                raise UIImportError(f"New entity {entity_id} needs incorporation_date.")

            by_id[entity_id] = dict(item)
            order.append(entity_id)
            stats["added"] += 1

    return {"entities": [by_id[eid] for eid in order]}, stats, affected


def merge_observations(current_payload: dict, incoming_payload: dict | None):
    current_items = current_payload.get("observations", [])
    if not isinstance(current_items, list):
        raise UIImportError("Current observations_raw.json is not valid.")

    if incoming_payload is None:
        return current_payload, {"added": 0, "replaced": 0, "unchanged": 0}, set()

    incoming_items = validate_observations_payload(incoming_payload)
    by_key = {(item["entity_id"], item["checked_at"]): dict(item) for item in current_items}

    stats = {"added": 0, "replaced": 0, "unchanged": 0}
    affected = set()

    for item in incoming_items:
        key = (item["entity_id"], item["checked_at"])
        affected.add(item["entity_id"])

        if key in by_key:
            if by_key[key] == item:
                stats["unchanged"] += 1
            else:
                stats["replaced"] += 1
        else:
            stats["added"] += 1

        by_key[key] = dict(item)

    merged = sorted(by_key.values(), key=lambda x: (x["entity_id"], x["checked_at"]))
    return {"observations": merged}, stats, affected


def stage_import(entities_payload: dict, observations_payload: dict) -> None:
    """Validate and score the merged dataset in temporary files before committing it."""
    with tempfile.TemporaryDirectory(prefix="entity_scoring_ui_") as temp_dir:
        temp = Path(temp_dir)
        entities_path = temp / "entities.json"
        raw_path = temp / "observations_raw.json"
        validated_path = temp / "observations_validated.json"
        rejected_path = temp / "observations_rejected.json"
        history_path = temp / "scores_history.json"
        leaderboard_path = temp / "leaderboard.json"

        write_json_atomic(entities_path, entities_payload)
        write_json_atomic(raw_path, observations_payload)

        intake = data_intake.run_intake(
            config_path=data_intake.DEFAULT_CONFIG,
            entities_path=entities_path,
            raw_path=raw_path,
            validated_path=validated_path,
            rejected_path=rejected_path,
        )

        if intake["rejected"]:
            rejected_records = intake.get("rejected_records", [])
            messages = []
            for rejected in rejected_records[:6]:
                record = rejected.get("record") if isinstance(rejected, dict) else None
                entity_id = record.get("entity_id", "unknown") if isinstance(record, dict) else "unknown"
                checked_at = record.get("checked_at", "unknown") if isinstance(record, dict) else "unknown"
                errors = "; ".join(rejected.get("errors", [])) if isinstance(rejected, dict) else "Rejected"
                messages.append(f"{entity_id} on {checked_at}: {errors}")

            extra = intake["rejected"] - len(messages)
            suffix = f"\n... and {extra} more rejected record(s)." if extra > 0 else ""
            raise UIImportError(
                "Import was not applied because validation rejected record(s):\n- "
                + "\n- ".join(messages)
                + suffix
            )

        scoring_engine.run_scoring(
            config_path=scoring_engine.DEFAULT_CONFIG,
            settings_path=scoring_engine.DEFAULT_SETTINGS,
            entities_path=entities_path,
            validated_path=validated_path,
            history_path=history_path,
            leaderboard_path=leaderboard_path,
        )


def import_and_recalculate(
    incoming_entities: dict | None,
    incoming_observations: dict | None,
) -> dict:
    current_entities = read_json(data_intake.DEFAULT_ENTITIES)
    current_observations = read_json(data_intake.DEFAULT_RAW)

    merged_entities, entity_stats, entity_ids = merge_entities(current_entities, incoming_entities)
    merged_observations, observation_stats, observation_ids = merge_observations(
        current_observations, incoming_observations
    )

    affected_ids = entity_ids | observation_ids
    if not affected_ids:
        raise UIImportError("Nothing new was supplied to import.")

    before = current_snapshot(affected_ids)

    # Test the full merged dataset before writing to the real source files.
    stage_import(merged_entities, merged_observations)

    try:
        write_json_atomic(data_intake.DEFAULT_ENTITIES, merged_entities)
        write_json_atomic(data_intake.DEFAULT_RAW, merged_observations)
        intake_result, scoring_result = run_complete_pipeline()
    except Exception:
        # Restore the source files if the real run unexpectedly fails.
        write_json_atomic(data_intake.DEFAULT_ENTITIES, current_entities)
        write_json_atomic(data_intake.DEFAULT_RAW, current_observations)
        try:
            run_complete_pipeline()
        except Exception:
            pass
        raise

    if intake_result["rejected"]:
        raise UIImportError(
            "The committed data unexpectedly produced rejected observations. "
            "Check observations_rejected.json."
        )

    after = current_snapshot(affected_ids)

    return {
        "entity_stats": entity_stats,
        "observation_stats": observation_stats,
        "affected_ids": sorted(affected_ids),
        "before": before,
        "after": after,
        "validated": intake_result["validated"],
        "rejected": intake_result["rejected"],
        "entities_scored": scoring_result["entities_scored"],
    }


# =============================================================================
# DASHBOARD VISUALS
# =============================================================================


def build_dashboard_dataframe(ranking: list[dict], history: dict, config: dict) -> pd.DataFrame:
    rows = []
    for row in ranking:
        entity_id = row["entity_id"]
        violations = violated_parameters_for_latest(entity_id, history, config)
        status = visual_status(entity_id, row, history)
        rows.append(
            {
                "Rank": row["rank"],
                "Entity": row["name"],
                "ID": entity_id,
                "Score": score_percent(row["score"]),
                "Status": status_label(status),
                "Violated parameters": ", ".join(violations) if violations else "—",
                "Trend": trend_label(row.get("trend")),
                "Observations": row["observations"],
                "Recent violations": row["recent_violations"],
                "As of": row["as_of"],
            }
        )
    return pd.DataFrame(rows)


def render_company_scatter(ranking: list[dict], history: dict, config: dict) -> None:
    points = []

    for row in ranking:
        entity_id = row["entity_id"]
        violations = violated_parameters_for_latest(entity_id, history, config)
        status = visual_status(entity_id, row, history)
        violation_text = "<br>".join(f"• {name}" for name in violations) if violations else "None"

        points.append(
            {
                "Entity": row["name"],
                "Entity ID": entity_id,
                "Score": float(row["score"]) * 100.0,
                "Violated parameters": len(violations),
                "Status": status,
                "Rank": row["rank"],
                "Observations": row["observations"],
                "Recent violations": row["recent_violations"],
                "Trend": trend_label(row.get("trend")),
                "As of": row["as_of"],
                "Violation details": violation_text,
            }
        )

    frame = pd.DataFrame(points)

    fig = px.scatter(
        frame,
        x="Score",
        y="Violated parameters",
        size="Observations",
        color="Status",
        hover_name="Entity",
        custom_data=[
            "Rank",
            "Status",
            "Trend",
            "Observations",
            "Recent violations",
            "As of",
            "Violation details",
            "Entity ID",
        ],
        color_discrete_map=STATUS_COLORS,
        category_orders={"Status": ["Compliant", "Violated", "Knockout"]},
        size_max=30,
        template="plotly_white",
    )

    fig.update_traces(
        marker={"line": {"width": 1.2, "color": "white"}, "opacity": 0.88},
        hovertemplate=(
            "<b>%{hovertext}</b><br>"
            "ID: %{customdata[7]}<br><br>"
            "Score: %{x:.1f}%<br>"
            "Rank: #%{customdata[0]}<br>"
            "Status: %{customdata[1]}<br>"
            "Trend: %{customdata[2]}<br>"
            "Observations: %{customdata[3]}<br>"
            "Recent violated observations: %{customdata[4]}<br>"
            "As of: %{customdata[5]}<br><br>"
            "<b>Currently violated parameters</b><br>%{customdata[6]}"
            "<extra></extra>"
        ),
    )

    max_violations = max(int(frame["Violated parameters"].max()), 1)

    fig.update_layout(
        height=500,
        xaxis_title="Current EWMA score (%)",
        yaxis_title="Currently violated parameters",
        legend_title="Status",
        hoverlabel={"align": "left"},
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    fig.update_xaxes(range=[0, 100], dtick=10, showgrid=True, gridcolor="#E8EDF3")
    fig.update_yaxes(
        range=[-0.5, max_violations + 0.75],
        dtick=1,
        showgrid=True,
        gridcolor="#E8EDF3",
    )

    st.plotly_chart(fig, use_container_width=True)


# =============================================================================
# DASHBOARD
# =============================================================================


def render_dashboard() -> None:
    st.title("Dashboard")
    st.caption("Current ranking, score, compliance state, and the parameters causing violations.")

    config = load_current_config()
    history, leaderboard = load_result_files()

    if config is None or history is None or leaderboard is None:
        st.info("No complete scoring result is available yet. Use 'Recalculate scores' in the sidebar.")
        return

    ranking = leaderboard.get("ranking", [])
    if not ranking:
        st.info("No ranked entities are available.")
        return

    total = leaderboard.get("count", len(ranking))
    compliant = leaderboard.get("compliant_count", 0)
    violated = total - compliant
    knockout_count = sum(1 for row in ranking if latest_is_gated(row["entity_id"], history))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Entities", total, "blue", "Entities with score history")
    with c2:
        metric_card("Compliant", compliant, "green", "All parameters currently inside their intervals")
    with c3:
        metric_card("Violated", violated, "red", "At least one parameter outside its interval")
    with c4:
        metric_card("Knockout", knockout_count, "darkred", "A gate violation forces the aggregate score to 0")

    st.write("")
    st.subheader("Entity overview")
    st.caption(
        "Each dot is a company. Hover over a dot to see its rank, score, and all parameters currently causing a violation."
    )
    render_company_scatter(ranking, history, config)

    st.subheader("Ranked leaderboard")
    left, right = st.columns([2, 1])
    search = left.text_input("Search", placeholder="Company name or entity ID")
    status_filter = right.selectbox("Status", ["All", "Compliant", "Violated", "Knockout"])

    frame = build_dashboard_dataframe(ranking, history, config)

    if search:
        term = search.lower().strip()
        frame = frame[
            frame.apply(lambda r: term in f"{r['Entity']} {r['ID']}".lower(), axis=1)
        ]

    if status_filter != "All":
        expected = status_label(status_filter)
        frame = frame[frame["Status"] == expected]

    if frame.empty:
        st.info("No entities match the selected filters.")
    else:
        st.dataframe(
            frame,
            hide_index=True,
            use_container_width=True,
            height=430,
            column_config={
                "Rank": st.column_config.NumberColumn("Rank", format="%d"),
                "Score": st.column_config.ProgressColumn(
                    "Score",
                    format="%.1f%%",
                    min_value=0,
                    max_value=100,
                ),
            },
        )

    st.caption(
        f"Generated: {leaderboard.get('generated_at', 'unknown')}  ·  "
        f"Config: {leaderboard.get('config_version', 'unknown')}"
    )


# =============================================================================
# IMPORT PAGE
# =============================================================================


def entity_preview_dataframe(payload: dict, current: dict) -> pd.DataFrame:
    current_ids = {e["id"] for e in current.get("entities", [])}
    rows = []
    for entity in validate_entities_payload(payload):
        rows.append(
            {
                "Action": "Update" if entity["id"] in current_ids else "Add",
                "ID": entity["id"],
                "Name": entity.get("name", ""),
                "Sector": entity.get("sector", ""),
                "Country": entity.get("country", ""),
                "Incorporation date": entity.get("incorporation_date", ""),
            }
        )
    return pd.DataFrame(rows)


def observation_preview_dataframe(payload: dict, current: dict) -> pd.DataFrame:
    existing = {
        (item["entity_id"], item["checked_at"])
        for item in current.get("observations", [])
    }
    rows = []
    for observation in validate_observations_payload(payload):
        key = (observation["entity_id"], observation["checked_at"])
        rows.append(
            {
                "Action": "Replace" if key in existing else "Add",
                "Entity ID": observation["entity_id"],
                "Checked at": observation["checked_at"],
            }
        )
    return pd.DataFrame(rows)


def comparison_dataframe(result: dict) -> pd.DataFrame:
    rows = []
    for entity_id in result["affected_ids"]:
        before = result["before"].get(entity_id, {})
        after = result["after"].get(entity_id, {})

        before_score = score_percent(before.get("score"))
        after_score = score_percent(after.get("score"))
        delta = None
        if before_score is not None and after_score is not None:
            delta = round(after_score - before_score, 1)

        rows.append(
            {
                "Entity": after.get("name") or before.get("name") or entity_id,
                "ID": entity_id,
                "Score before": before_score,
                "Score after": after_score,
                "Δ score": delta,
                "Rank before": before.get("rank"),
                "Rank after": after.get("rank"),
                "Status before": status_label(before.get("status")) if before.get("status") else "—",
                "Status after": status_label(after.get("status")) if after.get("status") else "No observations",
                "Currently violated": ", ".join(after.get("violated", [])) or "—",
            }
        )

    return pd.DataFrame(rows)


def render_import_result() -> None:
    result = st.session_state.get("last_import_result")
    if not result:
        return

    st.divider()
    st.subheader("Last import result")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Entities added", result["entity_stats"]["added"], "green")
    with c2:
        metric_card("Entities updated", result["entity_stats"]["updated"], "blue")
    with c3:
        metric_card("Observations added", result["observation_stats"]["added"], "green")
    with c4:
        metric_card("Observations replaced", result["observation_stats"]["replaced"], "orange")

    st.markdown("#### Before → after")
    comparison = comparison_dataframe(result)
    st.dataframe(
        comparison,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Score before": st.column_config.NumberColumn("Score before", format="%.1f%%"),
            "Score after": st.column_config.NumberColumn("Score after", format="%.1f%%"),
            "Δ score": st.column_config.NumberColumn("Δ score", format="%+.1f pp"),
        },
    )


def render_import_data() -> None:
    st.title("Import data")
    st.caption(
        "Add or update entities and observations. The merged dataset is validated and rescored before anything is saved."
    )

    current_entities = read_json(data_intake.DEFAULT_ENTITIES)
    current_observations = read_json(data_intake.DEFAULT_RAW)

    entity_upload = None
    observation_upload = None

    left, right = st.columns(2)
    with left:
        st.subheader("Entity data")
        st.caption('JSON format: {"entities": [ ... ]}')
        entity_upload = st.file_uploader(
            "Upload entity JSON",
            type=["json"],
            key="entity_json_upload",
        )

    with right:
        st.subheader("Observation data")
        st.caption('JSON format: {"observations": [ ... ]}')
        observation_upload = st.file_uploader(
            "Upload observation JSON",
            type=["json"],
            key="observation_json_upload",
        )

    incoming_entities = None
    incoming_observations = None
    error = None

    if entity_upload is not None:
        try:
            incoming_entities = parse_uploaded_json(entity_upload, "Entity file")
            st.markdown("#### Entity preview")
            st.dataframe(
                entity_preview_dataframe(incoming_entities, current_entities),
                hide_index=True,
                use_container_width=True,
            )
        except UIImportError as exc:
            error = str(exc)
            st.error(error)

    if observation_upload is not None:
        try:
            incoming_observations = parse_uploaded_json(observation_upload, "Observation file")
            st.markdown("#### Observation preview")
            st.dataframe(
                observation_preview_dataframe(incoming_observations, current_observations),
                hide_index=True,
                use_container_width=True,
            )
        except UIImportError as exc:
            error = str(exc)
            st.error(error)

    nothing_uploaded = entity_upload is None and observation_upload is None

    if nothing_uploaded:
        st.info("Upload an entity file, an observation file, or both.")

    if st.button(
        "Import and recalculate",
        type="primary",
        use_container_width=True,
        disabled=nothing_uploaded or error is not None,
    ):
        try:
            with st.spinner("Validating data and recalculating the ranking..."):
                result = import_and_recalculate(incoming_entities, incoming_observations)
            st.session_state["last_import_result"] = result
            st.success("Import completed. Scores and ranking were recalculated.")
            st.rerun()
        except (
            UIImportError,
            data_intake.IntakeError,
            scoring_engine.ScoringError,
            parser_config.ConfigError,
        ) as exc:
            st.error(str(exc))

    render_import_result()


# =============================================================================
# CONFIGURATION
# =============================================================================


def render_configuration() -> None:
    st.title("Configuration")
    st.caption("Source rulebook, parameter weights, rules, and allowed intervals.")

    config = load_current_config()
    if config is None:
        st.warning("parameters_config.json is not currently available.")
        if st.button("Build configuration from current files"):
            try:
                rebuild_parameter_config()
                st.success("Configuration generated.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        metric_card("Parameters", len(config["parameters"]), "blue")
    with c2:
        metric_card("Total weight", config.get("total_weight", 0), "gray")
    with c3:
        if config.get("complete"):
            metric_card("Configuration", "Complete", "green")
        else:
            metric_card("Configuration", "Incomplete", "orange")

    st.write("")
    st.subheader("Allowed intervals")

    editor_rows = []
    for parameter in config["parameters"]:
        editor_rows.append(
            {
                "ID": parameter["id"],
                "Parameter": parameter["parameter"],
                "Weight": parameter["weight"],
                "Lower": parameter["interval"].get("lower"),
                "Upper": parameter["interval"].get("upper"),
                "Rule": parameter["rule"],
            }
        )

    edited = st.data_editor(
        pd.DataFrame(editor_rows),
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        disabled=["ID", "Parameter", "Weight", "Rule"],
        column_config={
            "Lower": st.column_config.NumberColumn(
                "Lower", min_value=0.0, max_value=1.0, step=0.01, format="%.2f"
            ),
            "Upper": st.column_config.NumberColumn(
                "Upper", min_value=0.0, max_value=1.0, step=0.01, format="%.2f"
            ),
        },
    )

    if st.button("Save intervals and recalculate", type="primary"):
        payload = {}
        problems = []

        for row in edited.to_dict(orient="records"):
            lower = None if pd.isna(row["Lower"]) else float(row["Lower"])
            upper = None if pd.isna(row["Upper"]) else float(row["Upper"])

            if (lower is None) != (upper is None):
                problems.append(f"{row['ID']}: provide both lower and upper.")
            elif lower is not None and upper is not None and lower > upper:
                problems.append(f"{row['ID']}: lower cannot be greater than upper.")

            payload[row["ID"]] = {
                "parameter": row["Parameter"],
                "lower": lower,
                "upper": upper,
            }

        if problems:
            for problem in problems:
                st.error(problem)
        else:
            try:
                write_json_atomic(parser_config.DEFAULT_INTERVALS, payload)
                updated = rebuild_parameter_config()

                if not updated.get("complete"):
                    st.warning("Intervals were saved, but the configuration is still incomplete.")
                else:
                    with st.spinner("Recalculating scores..."):
                        run_complete_pipeline()
                    st.success("Intervals saved and scores recalculated.")
                    st.rerun()
            except (
                parser_config.ConfigError,
                data_intake.IntakeError,
                scoring_engine.ScoringError,
            ) as exc:
                st.error(str(exc))

    with st.expander("View parameter definitions"):
        for parameter in config["parameters"]:
            st.markdown(f"#### {parameter['id']} — {parameter['parameter']}")
            st.write(f"**Field:** {parameter['field']}")
            st.write(f"**Weight:** {parameter['weight']}")
            st.write(f"**Allowed interval:** {interval_label(parameter['interval'])}")
            st.write(f"**Context:** {parameter['context']}")
            st.write(f"**Rule:** {parameter['rule']}")
            st.divider()

    with st.expander("Current scoring settings"):
        if Path(scoring_engine.DEFAULT_SETTINGS).exists():
            st.json(read_json(scoring_engine.DEFAULT_SETTINGS))
        else:
            st.warning("scoring_config.json was not found.")


# =============================================================================
# ENTITY DETAIL
# =============================================================================


def render_entity_detail() -> None:
    st.title("Entity detail")

    config = load_current_config()
    history, leaderboard = load_result_files()

    if config is None or history is None or leaderboard is None:
        st.info("No complete scoring results are available yet.")
        return

    ranking = leaderboard.get("ranking", [])
    if not ranking:
        st.info("No entities are available.")
        return

    options = {
        f"#{row['rank']}  {row['name']} ({row['entity_id']})": row["entity_id"]
        for row in ranking
    }
    selected = st.selectbox("Entity", list(options.keys()))
    entity_id = options[selected]
    row = next(item for item in ranking if item["entity_id"] == entity_id)
    records = history.get("entities", {}).get(entity_id, [])

    if not records:
        st.info("This entity has no score history.")
        return

    latest = records[-1]
    status = visual_status(entity_id, row, history)
    violations = violated_parameters_for_latest(entity_id, history, config)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Rank", f"#{row['rank']}", "blue", f"Trend {trend_label(row.get('trend'))}")
    with c2:
        metric_card("Current score", f"{score_percent(row['score']):.1f}%", "gray")
    with c3:
        tone = "green" if status == "Compliant" else "darkred" if status == "Knockout" else "red"
        metric_card("Status", status, tone)
    with c4:
        metric_card("Observations", row["observations"], "gray")

    if status == "Knockout":
        st.error("Knockout gate is active. The aggregate score is forced to 0.")
    elif violations:
        st.warning("Currently violated: " + "; ".join(violations))
    else:
        st.success("All configured parameters are currently inside their allowed intervals.")

    st.subheader("Score history")
    chart = pd.DataFrame(
        [
            {
                "Date": pd.to_datetime(record["checked_at"]),
                "Raw score": score_percent(record["raw_score"]),
                "EWMA score": score_percent(record["ewma_score"]),
            }
            for record in records
        ]
    )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=chart["Date"],
            y=chart["Raw score"],
            mode="lines+markers",
            name="Raw score",
            line={"color": "#90A4AE", "width": 2},
            marker={"size": 7},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=chart["Date"],
            y=chart["EWMA score"],
            mode="lines+markers",
            name="EWMA score",
            line={"color": "#1565C0", "width": 3},
            marker={"size": 8},
        )
    )
    fig.update_layout(
        template="plotly_white",
        height=380,
        yaxis_title="Score (%)",
        xaxis_title="",
        hovermode="x unified",
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    fig.update_yaxes(range=[0, 100])
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Current parameter breakdown")
    names = parameter_name_map(config)
    parameter_rows = []

    for parameter in config["parameters"]:
        pid = parameter["id"]
        detail = latest["parameters"][pid]
        gate = bool(parameter["rule_spec"].get("gate", False))

        if detail["compliant"]:
            parameter_status = "Compliant"
        elif gate:
            parameter_status = "Knockout"
        else:
            parameter_status = "Violated"

        parameter_rows.append(
            {
                "ID": pid,
                "Parameter": names[pid],
                "Weight": parameter["weight"],
                "Normalized": latest["normalized_scores"][pid],
                "Interval": interval_label(parameter["interval"]),
                "Status": status_label(parameter_status),
                "Alpha": detail["alpha"],
                "Beta": detail["beta"],
                "Beta score": detail["conservative_score"],
            }
        )

    st.dataframe(
        pd.DataFrame(parameter_rows),
        hide_index=True,
        use_container_width=True,
        height=440,
        column_config={
            "Normalized": st.column_config.NumberColumn("Normalized", format="%.3f"),
            "Alpha": st.column_config.NumberColumn("Alpha", format="%.3f"),
            "Beta": st.column_config.NumberColumn("Beta", format="%.3f"),
            "Beta score": st.column_config.NumberColumn("Beta score", format="%.3f"),
        },
    )

    st.subheader("Compliance log")
    log_rows = []
    for record in reversed(records):
        row_status = "Knockout" if record.get("gated") else record["status"]
        record_violations = []
        for pid, detail in record.get("parameters", {}).items():
            if not detail.get("compliant", False):
                record_violations.append(names.get(pid, pid))

        log_rows.append(
            {
                "Date": record["checked_at"],
                "Status": status_label(row_status),
                "Raw score": score_percent(record["raw_score"]),
                "EWMA score": score_percent(record["ewma_score"]),
                "Violated parameters": ", ".join(record_violations) if record_violations else "—",
            }
        )

    st.dataframe(
        pd.DataFrame(log_rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Raw score": st.column_config.NumberColumn("Raw score", format="%.1f%%"),
            "EWMA score": st.column_config.NumberColumn("EWMA score", format="%.1f%%"),
        },
    )


# =============================================================================
# SIDEBAR / ROUTING
# =============================================================================

st.sidebar.title("Entity Scoring")
page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Import data", "Configuration", "Entity detail"],
)

st.sidebar.divider()

if st.sidebar.button("Recalculate scores", use_container_width=True):
    try:
        with st.spinner("Validating data and recalculating scores..."):
            intake_result, scoring_result = run_complete_pipeline()

        if intake_result["rejected"]:
            st.sidebar.warning(
                f"Scoring completed, but {intake_result['rejected']} observation(s) were rejected."
            )
        else:
            st.sidebar.success(f"{scoring_result['entities_scored']} entities scored.")
        st.rerun()
    except (
        parser_config.ConfigError,
        data_intake.IntakeError,
        scoring_engine.ScoringError,
    ) as exc:
        st.sidebar.error(str(exc))

if page == "Dashboard":
    render_dashboard()
elif page == "Import data":
    render_import_data()
elif page == "Configuration":
    render_configuration()
elif page == "Entity detail":
    render_entity_detail()
