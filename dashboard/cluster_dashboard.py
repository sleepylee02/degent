#!/usr/bin/env python3
"""Compact POST replay dashboard.

This dashboard intentionally reads only dashboard_compact.sqlite. It does not
read or mutate production/history runtime stores.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POST_ROOT = REPO_ROOT / "outputs" / "post"
NOISE_LABEL = -1


@dataclass(frozen=True)
class CompactRun:
    label: str
    db_path: Path
    run_root: Path


def discover_compact_runs(root: Path = DEFAULT_POST_ROOT) -> list[CompactRun]:
    if not root.exists():
        return []
    candidates = sorted(
        root.glob("**/dashboard_compact/dashboard_compact.sqlite"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    runs: list[CompactRun] = []
    for db_path in candidates:
        run_root = db_path.parent.parent
        try:
            label = str(run_root.relative_to(REPO_ROOT))
        except ValueError:
            label = str(run_root)
        runs.append(CompactRun(label=label, db_path=db_path, run_root=run_root))
    return runs


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_sql(db_path: Path, query: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    with connect_readonly(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def load_schema_version(db_path: Path) -> str:
    try:
        frame = read_sql(
            db_path,
            "SELECT value FROM schema_meta WHERE key='schema_version' LIMIT 1",
        )
    except (sqlite3.Error, pd.errors.DatabaseError):
        return "unknown"
    if frame.empty:
        return "unknown"
    return str(frame.iloc[0]["value"])


@st.cache_data(show_spinner=False)
def load_user_summary(db_path_text: str) -> pd.DataFrame:
    db_path = Path(db_path_text)
    return read_sql(
        db_path,
        """
        SELECT
            user_id,
            COUNT(*) AS event_count,
            MIN(timestamp) AS first_timestamp,
            MAX(timestamp) AS last_timestamp,
            MAX(k_count) AS max_k,
            AVG(k_count) AS avg_k,
            SUM(CASE WHEN is_refit_triggered != 0 THEN 1 ELSE 0 END) AS refit_count
        FROM event_timeline
        GROUP BY user_id
        ORDER BY event_count DESC, user_id ASC
        """,
    )


@st.cache_data(show_spinner=False)
def load_timeline(db_path_text: str, user_id: int) -> pd.DataFrame:
    db_path = Path(db_path_text)
    frame = read_sql(
        db_path,
        """
        SELECT
            user_id,
            event_id,
            timestamp,
            movie_id,
            is_refit_triggered,
            refit_reason,
            k_count,
            noise_count
        FROM event_timeline
        WHERE user_id = ?
        ORDER BY event_id ASC
        """,
        (int(user_id),),
    )
    if not frame.empty:
        frame["event_label"] = frame["event_id"].astype(str)
        frame["timestamp_display"] = frame["timestamp"].astype(str)
        frame["refit"] = frame["is_refit_triggered"].astype(bool)
    return frame


@st.cache_data(show_spinner=False)
def load_points(db_path_text: str, user_id: int, event_id: int) -> pd.DataFrame:
    db_path = Path(db_path_text)
    frame = read_sql(
        db_path,
        """
        SELECT points_data
        FROM visualization_states
        WHERE user_id = ? AND event_id = ?
        LIMIT 1
        """,
        (int(user_id), int(event_id)),
    )
    if frame.empty:
        return pd.DataFrame(columns=["raw_event_id", "x", "y", "cluster", "cluster_label"])
    try:
        points = json.loads(str(frame.iloc[0]["points_data"]))
    except json.JSONDecodeError:
        points = []
    rows: list[dict[str, Any]] = []
    for point in points:
        if point.get("x") is None or point.get("y") is None:
            continue
        cluster = int(point.get("c", NOISE_LABEL))
        rows.append(
            {
                "raw_event_id": point.get("raw_event_id", point.get("event_id")),
                "x": float(point["x"]),
                "y": float(point["y"]),
                "cluster": cluster,
                "cluster_label": "Noise" if cluster == NOISE_LABEL else f"Cluster {cluster}",
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def load_cluster_snapshots(db_path_text: str, user_id: int, event_id: int) -> pd.DataFrame:
    db_path = Path(db_path_text)
    frame = read_sql(
        db_path,
        """
        SELECT cluster_id, size, top_genres
        FROM cluster_snapshots
        WHERE user_id = ? AND event_id = ?
        ORDER BY cluster_id ASC
        """,
        (int(user_id), int(event_id)),
    )
    if frame.empty:
        return frame
    frame["cluster_label"] = frame["cluster_id"].apply(
        lambda value: "Noise" if int(value) == NOISE_LABEL else f"Cluster {int(value)}"
    )
    frame["top_genres_display"] = frame["top_genres"].apply(format_json_list)
    return frame


@st.cache_data(show_spinner=False)
def load_recommendations(db_path_text: str, user_id: int, event_id: int) -> pd.DataFrame:
    db_path = Path(db_path_text)
    return read_sql(
        db_path,
        """
        SELECT rank, movie_id, score, src_cluster
        FROM recommendations
        WHERE user_id = ? AND event_id = ?
        ORDER BY rank ASC
        """,
        (int(user_id), int(event_id)),
    )


@st.cache_data(show_spinner=False)
def load_table_counts(db_path_text: str) -> pd.DataFrame:
    db_path = Path(db_path_text)
    tables = ["event_timeline", "visualization_states", "cluster_snapshots", "recommendations"]
    rows = []
    with connect_readonly(db_path) as conn:
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            rows.append({"table": table, "rows": int(count)})
    return pd.DataFrame(rows)


def format_json_list(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, list):
        return ", ".join(str(item) for item in parsed)
    return str(parsed)


def render_header() -> None:
    st.set_page_config(page_title="AMIRRec Compact Dashboard", layout="wide")
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 0.65rem 0.8rem;
        }
        div[data-testid="stExpander"] {
            border: 1px solid #e5e7eb;
            border-radius: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("AMIRRec POST Replay Dashboard")
    st.caption("Compact replay view powered only by dashboard_compact.sqlite")


def select_compact_db() -> Path | None:
    runs = discover_compact_runs()
    st.sidebar.header("Run")

    if runs:
        labels = [run.label for run in runs]
        selected_label = st.sidebar.selectbox("Compact run", labels, index=0)
        selected = runs[labels.index(selected_label)]
        manual = st.sidebar.text_input("Compact DB path", value=str(selected.db_path))
    else:
        manual = st.sidebar.text_input(
            "Compact DB path",
            value=str(DEFAULT_POST_ROOT / "<run_id>_history" / "dashboard_compact" / "dashboard_compact.sqlite"),
        )

    db_path = Path(manual).expanduser()
    if not db_path.is_absolute():
        db_path = (REPO_ROOT / db_path).resolve()
    if not db_path.exists():
        st.warning("No compact dashboard DB found at the selected path.")
        return None
    return db_path


def select_user(user_summary: pd.DataFrame) -> int | None:
    st.sidebar.header("User")
    if user_summary.empty:
        st.warning("event_timeline has no users.")
        return None
    user_ids = user_summary["user_id"].astype(int).tolist()
    selected = st.sidebar.selectbox("User ID", user_ids, index=0)
    return int(selected)


def select_event(timeline: pd.DataFrame) -> int | None:
    if timeline.empty:
        st.warning("Selected user has no event timeline rows.")
        return None
    event_ids = timeline["event_id"].astype(int).tolist()
    if len(event_ids) == 1:
        return int(event_ids[0])
    selected = st.select_slider("Event ID", options=event_ids, value=event_ids[-1])
    return int(selected)


def render_run_summary(db_path: Path, user_summary: pd.DataFrame) -> None:
    schema_version = load_schema_version(db_path)
    counts = load_table_counts(str(db_path))
    timeline_rows = int(counts.loc[counts["table"] == "event_timeline", "rows"].iloc[0])
    recommendation_rows = int(counts.loc[counts["table"] == "recommendations", "rows"].iloc[0])
    metric_cols = st.columns(4)
    metric_cols[0].metric("Users", f"{len(user_summary):,}")
    metric_cols[1].metric("Timeline rows", f"{timeline_rows:,}")
    metric_cols[2].metric("Recommendation rows", f"{recommendation_rows:,}")
    metric_cols[3].metric("Schema", schema_version)

    with st.expander("Compact table counts", expanded=False):
        st.dataframe(counts, use_container_width=True, hide_index=True)
        st.code(str(db_path), language="text")


def render_k_chart(timeline: pd.DataFrame, selected_event_id: int) -> None:
    st.subheader("K over time")
    if timeline.empty:
        st.info("No timeline rows for this user.")
        return

    chart = go.Figure()
    chart.add_trace(
        go.Scatter(
            x=timeline["event_id"],
            y=timeline["k_count"],
            mode="lines",
            name="K",
            line={"color": "#4f46e5", "width": 2},
            hovertemplate=(
                "event_id=%{x}<br>"
                "K=%{y}<br>"
                "movie_id=%{customdata[0]}<br>"
                "timestamp=%{customdata[1]}<br>"
                "refit=%{customdata[2]}<extra></extra>"
            ),
            customdata=timeline[["movie_id", "timestamp_display", "refit"]].to_numpy(),
        )
    )
    chart.add_trace(
        go.Scatter(
            x=timeline["event_id"],
            y=timeline["noise_count"],
            mode="lines",
            name="Noise points",
            line={"color": "#94a3b8", "width": 1.5, "dash": "dot"},
        )
    )
    refits = timeline[timeline["is_refit_triggered"] != 0]
    if not refits.empty:
        chart.add_trace(
            go.Scatter(
                x=refits["event_id"],
                y=refits["k_count"],
                mode="markers",
                name="Refit",
                marker={"color": "#f97316", "size": 9, "symbol": "diamond"},
                hovertemplate="refit event=%{x}<br>K=%{y}<extra></extra>",
            )
        )
    chart.add_vline(x=selected_event_id, line_color="#111827", line_dash="dash")
    chart.update_layout(
        height=320,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        xaxis_title="Event ID",
        yaxis_title="Count",
        legend_orientation="h",
        legend_yanchor="bottom",
        legend_y=1.02,
        legend_x=0,
    )
    st.plotly_chart(chart, use_container_width=True)


def cluster_label_sort_key(label: str) -> int:
    if label == "Noise":
        return 999999
    try:
        return int(label.split()[-1])
    except (ValueError, IndexError):
        return 999998


def render_cluster_scatter(points: pd.DataFrame) -> None:
    st.subheader("Cluster state")
    if points.empty:
        st.info("No cluster snapshot has been carried forward to this event yet.")
        return
    ordered = points.copy()
    ordered["cluster_sort"] = ordered["cluster"].apply(
        lambda value: 999999 if int(value) == NOISE_LABEL else int(value)
    )
    ordered = ordered.sort_values(["cluster_sort", "raw_event_id"])
    category_order = sorted(ordered["cluster_label"].unique(), key=cluster_label_sort_key)
    fig = px.scatter(
        ordered,
        x="x",
        y="y",
        color="cluster_label",
        category_orders={"cluster_label": category_order},
        hover_data=["raw_event_id"],
        height=420,
    )
    fig.update_traces(marker={"size": 7, "opacity": 0.82, "line": {"width": 0}})
    fig.update_layout(
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        legend_title_text="Cluster",
        xaxis_title="UMAP x",
        yaxis_title="UMAP y",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_cluster_snapshot(snapshot: pd.DataFrame) -> None:
    st.subheader("Cluster details")
    if snapshot.empty:
        st.info("No cluster rows for this event.")
        return
    display = snapshot[["cluster_label", "size", "top_genres_display"]].rename(
        columns={
            "cluster_label": "cluster",
            "size": "points",
            "top_genres_display": "top genres",
        }
    )
    st.dataframe(display, use_container_width=True, hide_index=True)


def render_event_context(timeline: pd.DataFrame, selected_event_id: int) -> None:
    row = timeline[timeline["event_id"] == selected_event_id]
    if row.empty:
        return
    event = row.iloc[0]
    cols = st.columns(4)
    cols[0].metric("Event", int(event["event_id"]))
    cols[1].metric("Input movie", int(event["movie_id"]))
    cols[2].metric("K", int(event["k_count"]))
    cols[3].metric("Noise", int(event["noise_count"]))
    if bool(event["is_refit_triggered"]):
        st.info(f"Refit triggered: {event['refit_reason'] or 'reason unavailable'}")


def render_recommendations(recommendations: pd.DataFrame) -> None:
    st.subheader("Recommendations")
    if recommendations.empty:
        st.info("No recommendation rows for this event.")
        return
    display = recommendations.copy()
    display["score"] = display["score"].map(lambda value: None if pd.isna(value) else round(float(value), 6))
    display["src_cluster"] = display["src_cluster"].map(
        lambda value: "" if pd.isna(value) else f"Cluster {int(value)}"
    )
    display = display.rename(
        columns={
            "rank": "rank",
            "movie_id": "movie_id",
            "score": "score",
            "src_cluster": "source",
        }
    )
    st.dataframe(display, use_container_width=True, hide_index=True)


def render_user_summary(user_summary: pd.DataFrame, selected_user: int) -> None:
    row = user_summary[user_summary["user_id"] == selected_user]
    if row.empty:
        return
    item = row.iloc[0]
    cols = st.sidebar.columns(2)
    cols[0].metric("Events", f"{int(item['event_count']):,}")
    cols[1].metric("Max K", f"{int(item['max_k']):,}")
    st.sidebar.caption(f"{item['first_timestamp']} -> {item['last_timestamp']}")


def main() -> None:
    render_header()
    db_path = select_compact_db()
    if db_path is None:
        return

    try:
        user_summary = load_user_summary(str(db_path))
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        st.error(f"Failed to read compact DB: {exc}")
        return

    render_run_summary(db_path, user_summary)
    selected_user = select_user(user_summary)
    if selected_user is None:
        return
    render_user_summary(user_summary, selected_user)

    timeline = load_timeline(str(db_path), selected_user)
    selected_event_id = select_event(timeline)
    if selected_event_id is None:
        return

    render_event_context(timeline, selected_event_id)

    points = load_points(str(db_path), selected_user, selected_event_id)
    snapshots = load_cluster_snapshots(str(db_path), selected_user, selected_event_id)
    recommendations = load_recommendations(str(db_path), selected_user, selected_event_id)

    left, right = st.columns([1.45, 1.0], gap="large")
    with left:
        render_cluster_scatter(points)
        render_k_chart(timeline, selected_event_id)
    with right:
        render_cluster_snapshot(snapshots)
        render_recommendations(recommendations)


if __name__ == "__main__":
    main()
