#!/usr/bin/env python3
"""Streamlit dashboard for clustering results and streaming replay artifacts."""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import polars as pl
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLUSTER_PATH = REPO_ROOT / "data" / "clustering" / "user_clusters.parquet"
DEFAULT_REPLAY_ROOT = REPO_ROOT / "outputs" / "stream" / "replay_demo"
DEFAULT_REPLAY_SUMMARY_PATH = DEFAULT_REPLAY_ROOT / "replay_summary.json"
REQUIRED_COLUMNS = {"userId", "clusterLabel", "x", "y"}
NOISE_LABEL = -1
REPLAY_PATH_KEYS = {
    "replayEvents": "replay_events.jsonl",
    "onlineEmbeddings": "online_embeddings.npz",
    "interestAssignments": "interest_assignments.jsonl",
    "refitRequests": "refit_requests.jsonl",
    "refitEvents": "refit_events.jsonl",
    "interestStateDir": "interest_states",
}

MOVIE_METADATA_PATH = REPO_ROOT / "data" / "movies_processed.csv"
DEFAULT_WINDOW_SIZE = 120


def parse_list_value(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item is not None]
        except (ValueError, TypeError):
            pass
        return [text]
    return [str(value)]


def top_n_items(values: list[str], n: int = 5) -> list[str]:
    if not values:
        return []
    counts = Counter(values)
    return [item for item, _ in counts.most_common(n)]


def load_movie_metadata() -> pd.DataFrame | None:
    if not MOVIE_METADATA_PATH.exists():
        return None
    try:
        movies = pd.read_csv(MOVIE_METADATA_PATH)
        if "movieId" in movies.columns:
            movies["movieId"] = pd.to_numeric(movies["movieId"], errors="coerce").astype("Int64")
        return movies
    except Exception:
        return None


def enrich_frame_with_movie_metadata(frame: pd.DataFrame, movies: pd.DataFrame | None) -> pd.DataFrame:
    if movies is None or "movieId" not in frame.columns:
        return frame
    result = frame.copy()
    movie_index = movies.set_index("movieId")
    if "title" not in result.columns and "title" in movie_index.columns:
        result["title"] = result["movieId"].map(movie_index["title"])
    if "genres" not in result.columns and "genres" in movie_index.columns:
        result["genres"] = result["movieId"].map(movie_index["genres"])
    if "tag" not in result.columns and "tag" in movie_index.columns:
        result["tag"] = result["movieId"].map(movie_index["tag"])
    if "ratingAvg" not in result.columns and "ratingAvg" in movie_index.columns:
        result["ratingAvg"] = result["movieId"].map(movie_index["ratingAvg"])
    return result


def build_time_series(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "timepoint" not in frame.columns:
        return pd.DataFrame()
    series = (
        frame.groupby("timepoint", dropna=False)
        .agg(
            activeClusters=("clusterLabel", lambda values: int(values[values != NOISE_LABEL].nunique())),
            pointCount=("userId", "count"),
        )
        .reset_index()
        .sort_values("timepoint")
    )
    return series


def render_user_control_panel(frame: pd.DataFrame) -> tuple[pd.DataFrame, int | None, int | None, int | None]:
    st.sidebar.header("User control panel")
    selected_user = None
    filtered_frame = frame
    if "userId" in frame.columns:
        user_ids = sorted(frame["userId"].dropna().astype(str).unique(), key=lambda value: int(value) if value.isdigit() else value)
        user_choice = st.sidebar.selectbox("User", ["All users"] + user_ids, index=0)
        if user_choice != "All users":
            selected_user = int(user_choice) if user_choice.isdigit() else user_choice
            filtered_frame = frame[frame["userId"] == selected_user]

    timepoint_exists = "timepoint" in filtered_frame.columns
    current_time = None
    window_size = None
    if timepoint_exists and not filtered_frame.empty:
        min_time = int(filtered_frame["timepoint"].min())
        max_time = int(filtered_frame["timepoint"].max())
        if "dashboard_timepoint" not in st.session_state:
            st.session_state.dashboard_timepoint = min_time
        if "dashboard_playing" not in st.session_state:
            st.session_state.dashboard_playing = False

        control_left, control_right = st.sidebar.columns([1, 1])
        if control_left.button("◀ Previous"):
            st.session_state.dashboard_timepoint = max(min_time, st.session_state.dashboard_timepoint - 1)
        if control_right.button("Next ▶"):
            st.session_state.dashboard_timepoint = min(max_time, st.session_state.dashboard_timepoint + 1)

        play_toggle = st.sidebar.checkbox("Stream time", value=st.session_state.dashboard_playing)
        st.session_state.dashboard_playing = play_toggle
        current_time = st.sidebar.slider("Timepoint", min_time, max_time, value=st.session_state.dashboard_timepoint, step=1)
        st.session_state.dashboard_timepoint = current_time
        window_size = st.sidebar.slider(
            "Window size",
            min_value=1,
            max_value=max(max_time - min_time + 1, 1),
            value=min(DEFAULT_WINDOW_SIZE, max(max_time - min_time + 1, 1)),
            step=1,
        )

        if st.session_state.dashboard_playing and current_time < max_time:
            time.sleep(0.18)
            st.session_state.dashboard_timepoint = current_time + 1
            st.experimental_rerun()
    else:
        st.sidebar.info("No timepoint data available for streaming controls.")

    return filtered_frame, selected_user, current_time, window_size


def render_interest_metadata_panel(frame: pd.DataFrame, movies: pd.DataFrame | None) -> None:
    st.subheader("Interest metadata")
    if frame.empty:
        st.info("No active interest points in the current selection.")
        return

    active_clusters = sorted(frame[frame["clusterLabel"] != NOISE_LABEL]["clusterLabel"].unique())
    genre_values = []
    tag_values = []
    if "genres" in frame.columns:
        for value in frame["genres"].dropna().tolist():
            genre_values.extend(parse_list_value(value))
    if "tag" in frame.columns:
        for value in frame["tag"].dropna().tolist():
            tag_values.extend(parse_list_value(value))

    top_genres = top_n_items(genre_values, n=6)
    top_tags = top_n_items(tag_values, n=8)

    col1, col2 = st.columns(2)
    col1.metric("Active interests (K)", len(active_clusters))
    col2.metric("Active points", len(frame))

    if top_genres:
        st.markdown("**Representative genres**")
        st.write(", ".join(top_genres))
    if top_tags:
        st.markdown("**Representative tags**")
        st.write(", ".join(top_tags))

    if "movieId" in frame.columns:
        recent_movies = frame.sort_values("timepoint" if "timepoint" in frame.columns else "userId", ascending=False)
        recent_movies = recent_movies.dropna(subset=["movieId"])
        recent_ids = recent_movies["movieId"].drop_duplicates().head(5).tolist()
        if movies is not None and recent_ids:
            movie_rows = movies[movies["movieId"].isin(recent_ids)].copy()
            movie_rows["genres"] = movie_rows["genres"].fillna("[]")
            st.markdown("**Recent interaction movies**")
            for _, row in movie_rows.iterrows():
                st.write(f"- {row['title']} ({row['releaseYear'] if 'releaseYear' in row else 'N/A'}) — {row['genres']}")
        elif recent_ids:
            st.markdown("**Recent interaction movie IDs**")
            st.write(", ".join(str(mid) for mid in recent_ids))


def render_recommendation_panel(frame: pd.DataFrame, movies: pd.DataFrame | None) -> None:
    st.subheader("Recommendation panel")
    if frame.empty:
        st.info("No items available for recommendation preview.")
        return

    if "recommendedMovieId" in frame.columns:
        candidate_ids = frame["recommendedMovieId"].dropna().astype(int).astype(object).tolist()
    elif "movieId" in frame.columns:
        candidate_ids = frame["movieId"].dropna().astype(int).value_counts().head(6).index.tolist()
    else:
        st.info("No recommendation item IDs found in the current dataset.")
        return

    if not candidate_ids:
        st.info("No recommendation candidates available.")
        return

    if movies is not None:
        movie_rows = movies[movies["movieId"].isin(candidate_ids)].copy()
        if not movie_rows.empty:
            movie_rows = movie_rows.drop_duplicates(subset=["movieId"]).head(6)
            for _, row in movie_rows.iterrows():
                title = row.get("title", str(row.get("movieId", "unknown")))
                genres = row.get("genres", "[]")
                st.markdown(f"**{title}**")
                st.write(f"Genres: {genres}")
                if "ratingAvg" in row:
                    st.write(f"Average rating: {row.get('ratingAvg', '-')}")
                st.write("---")
            return

    st.markdown("**Recommendation candidate IDs**")
    st.write(", ".join(str(movie_id) for movie_id in candidate_ids[:6]))


def render_cluster_evolution_chart(frame: pd.DataFrame) -> None:
    if frame.empty or "timepoint" not in frame.columns:
        return
    series = build_time_series(frame)
    if series.empty:
        return
    figure = px.line(series, x="timepoint", y="activeClusters", title="Interest count over time")
    figure.update_layout(height=320, xaxis_title="Timepoint", yaxis_title="Cluster count")
    st.sidebar.plotly_chart(figure, use_container_width=True)


def infer_numeric_columns(frame: pd.DataFrame) -> list[str]:
    numeric_columns: list[str] = []
    for column in frame.columns:
        if pd.api.types.is_numeric_dtype(frame[column]):
            numeric_columns.append(column)
    return numeric_columns


def load_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pl.read_csv(path)
    elif suffix == ".parquet":
        frame = pl.read_parquet(path)
    elif suffix in {".jsonl", ".ndjson"}:
        frame = pl.read_ndjson(path)
    else:
        raise ValueError(f"Unsupported file format: {suffix}")
    return frame.to_pandas()


def resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(str(path_value)).expanduser()
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def default_replay_paths() -> dict[str, Path]:
    return {key: DEFAULT_REPLAY_ROOT / relative_path for key, relative_path in REPLAY_PATH_KEYS.items()}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if isinstance(record, dict):
                records.append(record)
            else:
                raise ValueError(f"JSONL record must be an object at {path}:{line_number}")
    return records


def records_to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    return pd.json_normalize(records)


def replay_paths_from_summary(summary: dict[str, Any]) -> dict[str, Path]:
    paths = default_replay_paths()
    summary_paths = summary.get("paths", {})
    if not isinstance(summary_paths, dict):
        return paths

    for key in REPLAY_PATH_KEYS:
        path_value = summary_paths.get(key)
        if path_value:
            paths[key] = resolve_repo_path(str(path_value))
    return paths


def path_status_rows(paths: dict[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, path in paths.items():
        exists = path.exists()
        is_dir = path.is_dir()
        rows.append({
            "artifact": key,
            "path": str(path),
            "exists": exists,
            "kind": "directory" if is_dir else "file",
            "sizeBytes": None if (not exists or is_dir) else path.stat().st_size,
        })
    return rows


def compact_columns(frame: pd.DataFrame, preferred_columns: list[str], *, max_columns: int = 18) -> pd.DataFrame:
    if frame.empty:
        return frame

    preferred = [column for column in preferred_columns if column in frame.columns]
    remaining = [column for column in frame.columns if column not in preferred]
    selected = [*preferred, *remaining[: max(0, max_columns - len(preferred))]]
    return frame[selected]


def status_counts_frame(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame.empty or column not in frame.columns:
        return pd.DataFrame(columns=[column, "count"])
    counts = frame[column].fillna("missing").astype(str).value_counts().reset_index()
    counts.columns = [column, "count"]
    return counts


def format_count(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def format_seconds(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}s"
    except (TypeError, ValueError):
        return str(value)


def generate_demo_frame(
    cluster_count: int = 6,
    samples_per_cluster: int = 220,
    noise_samples: int = 140,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, float | int]] = []
    user_id = 1

    for cluster_label in range(cluster_count):
        center = rng.normal(loc=0.0, scale=6.0, size=3)
        scale = rng.uniform(0.35, 1.2, size=3)
        sample_count = int(samples_per_cluster + rng.integers(-40, 40))

        for _ in range(sample_count):
            point = rng.normal(loc=center, scale=scale)
            probability = float(np.clip(rng.normal(0.88, 0.08), 0.45, 1.0))
            outlier_score = float(np.clip(1.0 - probability + rng.normal(0.04, 0.03), 0.0, 1.0))
            sequence_length = int(np.clip(rng.normal(55 + cluster_label * 9, 18), 5, 200))
            embedding_norm = float(np.clip(rng.normal(10.0 + cluster_label * 0.35, 1.1), 6.0, 16.0))
            timepoint = int(rng.integers(1, 21))  # Add timepoint for animation
            rows.append({
                "userId": user_id,
                "clusterLabel": cluster_label,
                "x": float(point[0]),
                "y": float(point[1]),
                "z": float(point[2]),
                "timepoint": timepoint,
                "clusterProbability": probability,
                "outlierScore": outlier_score,
                "sequenceLength": sequence_length,
                "embeddingNorm": embedding_norm,
            })
            user_id += 1

    for _ in range(noise_samples):
        point = rng.normal(loc=0.0, scale=11.0, size=3)
        probability = float(np.clip(rng.normal(0.24, 0.10), 0.0, 0.55))
        outlier_score = float(np.clip(rng.normal(0.81, 0.11), 0.2, 1.0))
        sequence_length = int(np.clip(rng.normal(18, 10), 1, 100))
        embedding_norm = float(np.clip(rng.normal(8.7, 1.4), 4.0, 14.0))
        timepoint = int(rng.integers(1, 21))  # Add timepoint for animation
        rows.append({
            "userId": user_id,
            "clusterLabel": NOISE_LABEL,
            "x": float(point[0]),
            "y": float(point[1]),
            "z": float(point[2]),
            "timepoint": timepoint,
            "clusterProbability": probability,
            "outlierScore": outlier_score,
            "sequenceLength": sequence_length,
            "embeddingNorm": embedding_norm,
        })
        user_id += 1

    return pd.DataFrame(rows)


def validate_frame(frame: pd.DataFrame) -> None:
    missing_columns = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing_columns:
        missing_text = ", ".join(missing_columns)
        raise ValueError(f"Missing required columns: {missing_text}")


def add_display_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["clusterLabel"] = result["clusterLabel"].astype(int)
    result["clusterDisplay"] = result["clusterLabel"].map(
        lambda value: "noise (-1)" if value == NOISE_LABEL else f"cluster {value}"
    )
    result["isNoise"] = result["clusterLabel"] == NOISE_LABEL
    return result


def filter_frame(frame: pd.DataFrame, include_noise: bool, selected_clusters: list[int]) -> pd.DataFrame:
    result = frame.copy()
    if not include_noise:
        result = result[result["clusterLabel"] != NOISE_LABEL]
    if selected_clusters:
        result = result[result["clusterLabel"].isin(selected_clusters)]
    return result


def build_cluster_summary(frame: pd.DataFrame) -> pd.DataFrame:
    summary = (
        frame.groupby(["clusterLabel", "clusterDisplay", "isNoise"], dropna=False)
        .agg(
            userCount=("userId", "count"),
            xMean=("x", "mean"),
            yMean=("y", "mean"),
        )
        .reset_index()
        .sort_values(["isNoise", "userCount", "clusterLabel"], ascending=[True, False, True])
    )

    optional_metrics = ["clusterProbability", "outlierScore", "sequenceLength", "embeddingNorm"]
    for column in optional_metrics:
        if column in frame.columns:
            metric = frame.groupby("clusterLabel")[column].mean().rename(f"{column}Mean")
            summary = summary.merge(metric, on="clusterLabel", how="left")

    return summary


def render_metric_cards(frame: pd.DataFrame) -> None:
    total_users = len(frame)
    non_noise = frame[frame["clusterLabel"] != NOISE_LABEL]
    cluster_count = int(non_noise["clusterLabel"].nunique())
    noise_count = int((frame["clusterLabel"] == NOISE_LABEL).sum())
    noise_ratio = (noise_count / total_users) if total_users else 0.0

    largest_cluster = 0
    if len(non_noise):
        largest_cluster = int(non_noise["clusterLabel"].value_counts().max())

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Users", f"{total_users:,}")
    col2.metric("Clusters", f"{cluster_count:,}")
    col3.metric("Noise ratio", f"{noise_ratio:.1%}")
    col4.metric("Largest cluster", f"{largest_cluster:,}")


def render_projection_chart(frame: pd.DataFrame, color_column: str) -> None:
    if frame.empty:
        st.info("No points match the current filters.")
        return

    has_3d = "z" in frame.columns
    has_time = "timepoint" in frame.columns
    hover_data = [column for column in ["userId", "clusterLabel", "clusterProbability", "outlierScore", "sequenceLength"] if column in frame.columns]

    if has_3d:
        figure = px.scatter_3d(
            frame,
            x="x",
            y="y",
            z="z",
            color=color_column,
            hover_data=hover_data,
            opacity=0.72,
            title="Reduced user-state embedding",
            animation_frame="timepoint" if has_time else None,
        )
    else:
        figure = px.scatter(
            frame,
            x="x",
            y="y",
            color=color_column,
            hover_data=hover_data,
            opacity=0.72,
            title="Reduced user-state embedding",
            animation_frame="timepoint" if has_time else None,
        )

    figure.update_layout(height=640, legend_title_text=color_column)
    st.plotly_chart(figure, use_container_width=True)


def render_cluster_size_chart(summary: pd.DataFrame) -> None:
    if summary.empty:
        st.info("No clusters to summarize.")
        return

    figure = px.bar(
        summary.sort_values("userCount", ascending=False),
        x="clusterDisplay",
        y="userCount",
        color="isNoise",
        color_discrete_map={False: "#0f766e", True: "#dc2626"},
        title="Users per cluster",
    )
    figure.update_layout(height=420, xaxis_title="", yaxis_title="Users", showlegend=False)
    st.plotly_chart(figure, use_container_width=True)


def render_distribution_chart(frame: pd.DataFrame, metric_column: str) -> None:
    if frame.empty:
        return

    figure = px.box(
        frame,
        x="clusterDisplay",
        y=metric_column,
        color="isNoise",
        color_discrete_map={False: "#2563eb", True: "#dc2626"},
        points="outliers",
        title=f"{metric_column} distribution by cluster",
    )
    figure.update_layout(height=420, xaxis_title="", showlegend=False)
    st.plotly_chart(figure, use_container_width=True)


def render_cluster_dashboard() -> None:
    st.subheader("Cluster Explorer")
    st.caption("User-state embeddings reduced for density-based clustering exploration.")
    with st.sidebar:
        st.header("Cluster Data")
        default_path = st.text_input("Cluster result path", value=str(DEFAULT_CLUSTER_PATH))
        use_demo_data = st.toggle("Use demo data", value=not Path(default_path).exists())

    if use_demo_data:
        frame = generate_demo_frame()
        data_source = "synthetic demo data"
    else:
        cluster_path = Path(default_path).expanduser()
        try:
            frame = load_frame(cluster_path)
        except Exception as exc:
            st.error(f"Failed to load cluster results: {exc}")
            st.stop()
        data_source = str(cluster_path)

    try:
        validate_frame(frame)
    except Exception as exc:
        st.error(f"Invalid cluster result schema: {exc}")
        st.stop()

    frame = add_display_columns(frame)
    movies = load_movie_metadata()
    frame = enrich_frame_with_movie_metadata(frame, movies)

    user_frame, selected_user, current_time, window_size = render_user_control_panel(frame)
    render_cluster_evolution_chart(user_frame)
    if current_time is not None and window_size is not None and "timepoint" in user_frame.columns:
        min_time = int(user_frame["timepoint"].min())
        window_start = max(min_time, current_time - window_size + 1)
        user_frame = user_frame[(user_frame["timepoint"] >= window_start) & (user_frame["timepoint"] <= current_time)]
        st.caption(f"Data source: `{data_source}` | User: {selected_user or 'All'} | Time window: {window_start} - {current_time}")
    else:
        st.caption(f"Data source: `{data_source}` | User: {selected_user or 'All'}")

    numeric_columns = infer_numeric_columns(user_frame)
    color_candidates = ["clusterDisplay"] + [column for column in ["clusterProbability", "outlierScore", "sequenceLength", "embeddingNorm"] if column in user_frame.columns]
    distribution_candidates = [column for column in ["clusterProbability", "outlierScore", "sequenceLength", "embeddingNorm"] if column in user_frame.columns]

    with st.sidebar:
        st.header("Filters")
        include_noise = st.checkbox("Include noise (-1)", value=True)
        available_clusters = sorted(user_frame["clusterLabel"].unique().tolist())
        default_clusters = [label for label in available_clusters if include_noise or label != NOISE_LABEL]
        selected_clusters = st.multiselect("Visible clusters", available_clusters, default=default_clusters)

        color_column = st.selectbox("Color by", color_candidates, index=0)

        metric_column = None
        if distribution_candidates:
            metric_column = st.selectbox("Distribution metric", distribution_candidates, index=0)

        for column in [name for name in ["sequenceLength", "clusterProbability", "outlierScore", "embeddingNorm"] if name in numeric_columns]:
            col_min = float(user_frame[column].min())
            col_max = float(user_frame[column].max())
            selected_min, selected_max = st.slider(
                f"{column} range",
                min_value=col_min,
                max_value=col_max,
                value=(col_min, col_max),
            )
            user_frame = user_frame[(user_frame[column] >= selected_min) & (user_frame[column] <= selected_max)]

    filtered = filter_frame(user_frame, include_noise=include_noise, selected_clusters=selected_clusters)
    summary = build_cluster_summary(filtered)

    render_metric_cards(filtered)

    main_left, main_right = st.columns([3, 1])
    with main_left:
        render_projection_chart(filtered, color_column=color_column)

    with main_right:
        render_interest_metadata_panel(filtered, movies)
    render_recommendation_panel(filtered, movies)
    render_cluster_size_chart(summary)


def render_replay_summary(summary: dict[str, Any], paths: dict[str, Path]) -> None:
    totals = summary.get("totals", {})
    if not isinstance(totals, dict):
        totals = {}

    input_events = summary.get("inputEvents")
    processed_events = summary.get("processedEvents")
    elapsed_sec = summary.get("elapsedSec")
    throughput = summary.get("throughputEventsPerSec")
    if throughput is None:
        try:
            elapsed = float(elapsed_sec)
            throughput = None if elapsed <= 0 else float(processed_events or 0) / elapsed
        except (TypeError, ValueError):
            throughput = None

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Run status", str(summary.get("status", "-")))
    col2.metric("Processed events", f"{format_count(processed_events)} / {format_count(input_events)}")
    col3.metric("Unique users", format_count(summary.get("uniqueUsers")))
    col4.metric("Elapsed", format_seconds(elapsed_sec))

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Throughput", "-" if throughput is None else f"{float(throughput):,.2f}/s")
    col6.metric("Active embeddings", format_count(totals.get("activeEmbeddingRows")))
    col7.metric("Refit opened", format_count(totals.get("refitRequestsOpened")))
    col8.metric("Refit closed", format_count(totals.get("refitClosed")))

    metadata = {
        "version": summary.get("version"),
        "runId": summary.get("runId"),
        "startedAt": summary.get("startedAt"),
        "endedAt": summary.get("endedAt"),
        "microBatchSize": summary.get("microBatchSize"),
        "refitBackend": summary.get("refitBackend"),
    }
    st.dataframe(pd.DataFrame([metadata]), use_container_width=True, hide_index=True)

    with st.expander("Replay artifact paths", expanded=False):
        st.dataframe(pd.DataFrame(path_status_rows(paths)), use_container_width=True, hide_index=True)


def render_replay_events(frame: pd.DataFrame, max_rows: int) -> None:
    st.subheader("Replay events")
    if frame.empty:
        st.info("No replay event records found.")
        return

    plot_frame = frame.copy()
    plot_frame["row"] = np.arange(1, len(plot_frame) + 1)
    x_column = "recordedAt" if "recordedAt" in plot_frame.columns else "row"
    progress_columns = [
        column for column in ["processedEvents", "activeEmbeddingRows", "refitRequestsOpened", "refitClosed"] if column in plot_frame.columns
    ]
    if progress_columns:
        melted = plot_frame.melt(
            id_vars=[x_column],
            value_vars=progress_columns,
            var_name="metric",
            value_name="value",
        )
        figure = px.line(melted, x=x_column, y="value", color="metric", markers=True, title="Replay progress")
        figure.update_layout(height=380, xaxis_title="", yaxis_title="")
        st.plotly_chart(figure, use_container_width=True)

    left, right = st.columns(2)
    with left:
        if "latencySec" in plot_frame.columns:
            color_column = "stage" if "stage" in plot_frame.columns else None
            figure = px.bar(plot_frame, x=x_column, y="latencySec", color=color_column, title="Batch latency")
            figure.update_layout(height=360, xaxis_title="", yaxis_title="Seconds")
            st.plotly_chart(figure, use_container_width=True)
    with right:
        assignment_columns = [column for column in plot_frame.columns if column.startswith("assignmentStatusCounts.")]
        if assignment_columns:
            melted = plot_frame.melt(
                id_vars=[x_column],
                value_vars=assignment_columns,
                var_name="status",
                value_name="count",
            )
            melted["status"] = melted["status"].str.replace("assignmentStatusCounts.", "", regex=False)
            figure = px.bar(melted, x=x_column, y="count", color="status", title="Assignment status by replay event")
            figure.update_layout(height=360, xaxis_title="", yaxis_title="Records")
            st.plotly_chart(figure, use_container_width=True)

    preferred = [
        "recordedAt",
        "runId",
        "stage",
        "status",
        "batchId",
        "eventStart",
        "eventEnd",
        "processedEvents",
        "uniqueUsers",
        "activeEmbeddingRows",
        "refitRequestsOpened",
        "refitClosed",
        "latencySec",
    ]
    st.dataframe(compact_columns(plot_frame.tail(max_rows), preferred), use_container_width=True, hide_index=True)


def render_assignment_refit_view(assignments: pd.DataFrame, requests: pd.DataFrame, refit_events: pd.DataFrame, max_rows: int) -> None:
    st.subheader("Assignments and refit")

    assignment_records = len(assignments)
    open_requests = 0
    if not requests.empty and "status" in requests.columns:
        open_requests = int((requests["status"].astype(str) == "open").sum())
    closed_refits = 0
    skipped_refits = 0
    if not refit_events.empty and "status" in refit_events.columns:
        closed_refits = int((refit_events["status"].astype(str) == "closed").sum())
        skipped_refits = int((refit_events["status"].astype(str) == "skipped").sum())

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Assignment records", format_count(assignment_records))
    col2.metric("Open requests", format_count(open_requests))
    col3.metric("Closed refits", format_count(closed_refits))
    col4.metric("Skipped refits", format_count(skipped_refits))

    left, right = st.columns(2)
    with left:
        if assignments.empty or "status" not in assignments.columns:
            st.info("No assignment status records found.")
        else:
            counts = status_counts_frame(assignments, "status")
            figure = px.bar(counts, x="status", y="count", title="Assignment statuses")
            figure.update_layout(height=360, xaxis_title="", yaxis_title="Records")
            st.plotly_chart(figure, use_container_width=True)
    with right:
        if refit_events.empty or "status" not in refit_events.columns:
            st.info("No refit event records found.")
        else:
            counts = status_counts_frame(refit_events, "status")
            figure = px.bar(counts, x="status", y="count", title="Refit event statuses")
            figure.update_layout(height=360, xaxis_title="", yaxis_title="Events")
            st.plotly_chart(figure, use_container_width=True)

    assignment_preferred = [
        "recordedAt",
        "runId",
        "userId",
        "rawEventId",
        "eventIdx",
        "movieId",
        "status",
        "reason",
        "assignedInterestId",
        "similarity",
        "historyLen",
    ]
    request_preferred = [
        "recordedAt",
        "runId",
        "userId",
        "status",
        "pendingRawEventCount",
        "assignedSinceLastRefit",
        "outlierSinceLastRefit",
        "reasons",
    ]
    refit_preferred = [
        "recordedAt",
        "runId",
        "userId",
        "status",
        "backendRequested",
        "backendSelected",
        "activeEmbeddingRows",
        "refitElapsedSec",
        "interestCount",
        "clusterSummary.noiseCount",
        "clusterSummary.fallbackUsed",
    ]

    tab1, tab2, tab3 = st.tabs(["Assignments", "Refit requests", "Refit events"])
    with tab1:
        st.dataframe(compact_columns(assignments.tail(max_rows), assignment_preferred), use_container_width=True, hide_index=True)
    with tab2:
        st.dataframe(compact_columns(requests.tail(max_rows), request_preferred), use_container_width=True, hide_index=True)
    with tab3:
        st.dataframe(compact_columns(refit_events.tail(max_rows), refit_preferred), use_container_width=True, hide_index=True)


def interest_state_rows(state_dir: Path) -> list[dict[str, Any]]:
    if not state_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(state_dir.glob("*.json")):
        try:
            state = load_json(path)
        except Exception:
            continue
        user_id = state.get("userId", path.stem)
        rows.append({
            "userId": int(user_id) if str(user_id).isdigit() else str(user_id),
            "path": path,
            "interestCount": len(state.get("interests", [])),
            "pendingCount": len(state.get("pendingRawEventIds", [])),
            "processedCount": len(state.get("processedRawEventIds", [])),
            "updatedAt": state.get("updatedAt"),
        })
    return sorted(rows, key=lambda item: str(item["userId"]))


def render_interest_state_browser(state_dir: Path) -> None:
    st.subheader("Interest state browser")
    rows = interest_state_rows(state_dir)
    if not rows:
        st.info(f"No interest state JSON files found under `{state_dir}`.")
        return

    options = [str(row["userId"]) for row in rows]
    selected_user = st.selectbox("User", options=options)
    selected_row = next(row for row in rows if str(row["userId"]) == selected_user)
    state = load_json(Path(selected_row["path"]))

    interests = state.get("interests", [])
    pending_ids = state.get("pendingRawEventIds", [])
    processed_ids = state.get("processedRawEventIds", [])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Interests", format_count(len(interests)))
    col2.metric("Pending events", format_count(len(pending_ids)))
    col3.metric("Processed events", format_count(len(processed_ids)))
    col4.metric("Embedding dim", format_count(state.get("embeddingDim")))

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Assigned since refit", format_count(state.get("assignedSinceLastRefit")))
    col6.metric("Outliers since refit", format_count(state.get("outlierSinceLastRefit")))
    col7.metric("Refit required", str(state.get("refitRequired", False)))
    col8.metric("Request open", str(state.get("refitRequestOpen", False)))

    st.caption(f"State path: `{selected_row['path']}`")
    refit_reasons = state.get("refitReasons", [])
    if refit_reasons:
        st.write("Refit reasons:", ", ".join(str(value) for value in refit_reasons))

    interest_rows: list[dict[str, Any]] = []
    for interest in interests:
        vector = interest.get("vector", [])
        vector_norm = None
        if isinstance(vector, list) and vector:
            vector_norm = float(np.linalg.norm(np.array(vector, dtype=np.float32)))
        interest_rows.append({
            "interestId": interest.get("interestId"),
            "assignedCount": interest.get("assignedCount"),
            "source": interest.get("source"),
            "vectorNorm": vector_norm,
            "createdAt": interest.get("createdAt"),
            "updatedAt": interest.get("updatedAt"),
        })

    st.dataframe(pd.DataFrame(interest_rows), use_container_width=True, hide_index=True)


def render_replay_dashboard() -> None:
    st.subheader("Replay Monitor")
    st.caption("Read-only monitor for Phase 5 replay artifacts under the streaming replay/dashboard contract.")

    with st.sidebar:
        st.header("Replay Data")
        summary_path_text = st.text_input("Replay summary path", value=str(DEFAULT_REPLAY_SUMMARY_PATH))
        max_rows = st.number_input("Rows per table", min_value=20, max_value=5000, value=200, step=20)

    summary_path = resolve_repo_path(summary_path_text)
    if not summary_path.is_file():
        st.info(f"No replay summary found at `{summary_path}`.")
        st.write("The dashboard reads Phase 5 artifacts only when they exist. Use the cluster explorer for existing dashboard data.")
        with st.expander("Expected default replay paths", expanded=True):
            st.dataframe(pd.DataFrame(path_status_rows(default_replay_paths())), use_container_width=True, hide_index=True)
        return

    try:
        summary = load_json(summary_path)
        paths = replay_paths_from_summary(summary)
        replay_events = records_to_frame(load_jsonl_records(paths["replayEvents"]))
        assignments = records_to_frame(load_jsonl_records(paths["interestAssignments"]))
        refit_requests = records_to_frame(load_jsonl_records(paths["refitRequests"]))
        refit_events = records_to_frame(load_jsonl_records(paths["refitEvents"]))
    except Exception as exc:
        st.error(f"Failed to load replay artifacts: {exc}")
        st.stop()

    render_replay_summary(summary, paths)
    render_replay_events(replay_events, int(max_rows))
    render_assignment_refit_view(assignments, refit_requests, refit_events, int(max_rows))
    render_interest_state_browser(paths["interestStateDir"])


def main() -> None:
    st.set_page_config(page_title="Recommendation Dashboard", layout="wide")
    st.title("Recommendation Dashboard")

    default_view = "Replay monitor" if DEFAULT_REPLAY_SUMMARY_PATH.is_file() else "Cluster explorer"
    with st.sidebar:
        st.header("View")
        view = st.radio(
            "Dashboard view",
            options=["Replay monitor", "Cluster explorer"],
            index=0 if default_view == "Replay monitor" else 1,
        )

    if view == "Replay monitor":
        render_replay_dashboard()
    else:
        render_cluster_dashboard()


if __name__ == "__main__":
    main()
