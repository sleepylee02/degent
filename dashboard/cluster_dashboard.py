#!/usr/bin/env python3
"""Streamlit dashboard for reduced user embeddings and density-based clustering results."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import polars as pl
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLUSTER_PATH = REPO_ROOT / "data" / "clustering" / "user_clusters.parquet"
REQUIRED_COLUMNS = {"userId", "clusterLabel", "x", "y"}
NOISE_LABEL = -1


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
            rows.append({
                "userId": user_id,
                "clusterLabel": cluster_label,
                "x": float(point[0]),
                "y": float(point[1]),
                "z": float(point[2]),
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
        rows.append({
            "userId": user_id,
            "clusterLabel": NOISE_LABEL,
            "x": float(point[0]),
            "y": float(point[1]),
            "z": float(point[2]),
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


def main() -> None:
    st.set_page_config(page_title="User Cluster Dashboard", layout="wide")
    st.title("User Cluster Dashboard")
    st.caption("User-state embeddings reduced for density-based clustering exploration.")

    with st.sidebar:
        st.header("Data")
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

    numeric_columns = infer_numeric_columns(frame)
    color_candidates = ["clusterDisplay"] + [column for column in ["clusterProbability", "outlierScore", "sequenceLength", "embeddingNorm"] if column in frame.columns]
    distribution_candidates = [column for column in ["clusterProbability", "outlierScore", "sequenceLength", "embeddingNorm"] if column in frame.columns]

    with st.sidebar:
        st.header("Filters")
        include_noise = st.checkbox("Include noise (-1)", value=True)
        available_clusters = sorted(frame["clusterLabel"].unique().tolist())
        default_clusters = [label for label in available_clusters if include_noise or label != NOISE_LABEL]
        selected_clusters = st.multiselect("Visible clusters", available_clusters, default=default_clusters)

        color_column = st.selectbox("Color by", color_candidates, index=0)

        metric_column = None
        if distribution_candidates:
            metric_column = st.selectbox("Distribution metric", distribution_candidates, index=0)

        for column in [name for name in ["sequenceLength", "clusterProbability", "outlierScore", "embeddingNorm"] if name in numeric_columns]:
            col_min = float(frame[column].min())
            col_max = float(frame[column].max())
            selected_min, selected_max = st.slider(
                f"{column} range",
                min_value=col_min,
                max_value=col_max,
                value=(col_min, col_max),
            )
            frame = frame[(frame[column] >= selected_min) & (frame[column] <= selected_max)]

    filtered = filter_frame(frame, include_noise=include_noise, selected_clusters=selected_clusters)
    summary = build_cluster_summary(filtered)

    st.caption(f"Data source: `{data_source}`")
    render_metric_cards(filtered)

    left, right = st.columns([2, 1])
    with left:
        render_projection_chart(filtered, color_column=color_column)
    with right:
        render_cluster_size_chart(summary)

    if metric_column is not None and not filtered.empty:
        render_distribution_chart(filtered, metric_column)

    st.subheader("Cluster summary")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.subheader("Filtered records")
    visible_columns = ["userId", "clusterLabel", "clusterDisplay", "x", "y"]
    if "z" in filtered.columns:
        visible_columns.append("z")
    visible_columns.extend([column for column in ["clusterProbability", "outlierScore", "sequenceLength", "embeddingNorm"] if column in filtered.columns])
    st.dataframe(filtered[visible_columns], use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
