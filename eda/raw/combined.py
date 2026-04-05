from __future__ import annotations

import os
from datetime import datetime

from .shared import EDA_OUTPUT_DIR, format_float, format_int, markdown_table, percent, write_markdown


def write_combined_eda_report(ml_summary: dict, genome_summary: dict, overlap_movies: int) -> str:
    report_path = os.path.join(EDA_OUTPUT_DIR, "eda_report.md")

    lines = [
        "# Multi-Source Movie EDA Summary",
        "",
        f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Output Structure",
        "",
        "- MovieLens detailed EDA: [ml_32m/eda_report.md](ml_32m/eda_report.md)",
        "- MovieLens dataset note (KO): [ml_32m/dataset_description_report_ko.md](ml_32m/dataset_description_report_ko.md)",
        "- Genome 2021 detailed EDA: [genome_2021/eda_report.md](genome_2021/eda_report.md)",
        "- Genome 2021 dataset note (KO): [genome_2021/dataset_description_report_ko.md](genome_2021/dataset_description_report_ko.md)",
        "",
        "## Side-By-Side Snapshot",
        "",
        markdown_table(
            ["Source", "Catalog movies", "Ratings", "Users", "Reviews", "Tag signal", "Timestamps", "Best role"],
            [
                [
                    ml_summary["source_name"],
                    format_int(ml_summary["movie_count"]),
                    format_int(ml_summary["ratings_total"]),
                    format_int(ml_summary["user_count"]),
                    "-",
                    f"{format_int(ml_summary['tag_total'])} free-text tags",
                    "Yes",
                    "Primary interaction log",
                ],
                [
                    genome_summary["source_name"],
                    format_int(genome_summary["movie_count"]),
                    format_int(genome_summary["ratings_total"]),
                    format_int(genome_summary["user_count"]),
                    format_int(genome_summary["review_total"]),
                    f"{format_int(genome_summary['tag_total'])} tag applications + survey/scores",
                    "No",
                    "Semantic enrichment",
                ],
            ],
        ),
        "",
        "## Joinability",
        "",
        markdown_table(
            ["Metric", "Value"],
            [
                ["Safe join key", "`ml-32m.movieId = genome_2021.item_id`"],
                ["Overlapping movie ids", format_int(overlap_movies)],
                ["Genome catalog covered by MovieLens ids", percent(overlap_movies, genome_summary["movie_count"])],
                ["MovieLens catalog covered by Genome ids", percent(overlap_movies, ml_summary["movie_count"])],
            ],
        ),
        "",
        "User ids should not be assumed to align across the two sources. Use movie ids for integration and keep user spaces separate unless provenance has been verified independently.",
        "",
        "## Main Takeaways",
        "",
        f"- MovieLens is the better source for sequential recommendation because it has timestamps and {format_int(ml_summary['ratings_total'])} rating events.",
        f"- Genome 2021 is the better source for item semantics because it adds {format_int(genome_summary['review_total'])} IMDb reviews, survey labels, and precomputed tag-relevance scores.",
        f"- Both sources are long-tail. In MovieLens, the top 1% of movies account for about {format_float(ml_summary['head_movie_share_top1'], 2)}% of ratings. In Genome 2021, the top 1% of movies account for about {format_float(genome_summary['head_movie_share_top1'], 2)}% of ratings.",
        "",
    ]

    return write_markdown(report_path, lines)


def write_combined_dataset_report_ko(ml_summary: dict, genome_summary: dict, overlap_movies: int) -> str:
    report_path = os.path.join(EDA_OUTPUT_DIR, "dataset_description_report_ko.md")

    lines = [
        "# 두 영화 데이터 소스 통합 설명",
        "",
        f"작성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 왜 둘로 나눠 봐야 하는가",
        "",
        "지금 작업 공간에는 성격이 다른 두 데이터 소스가 있다. 하나는 사용자 상호작용 로그 중심의 MovieLens ml-32m이고, 다른 하나는 영화 semantic 정보와 tag relevance 학습을 위해 정리된 Genome 2021 소스다. 둘을 한 덩어리로 읽으면 목적이 흐려진다.",
        "",
        "## 2. 역할 분리",
        "",
        markdown_table(
            ["소스", "핵심 단위", "강점", "약점", "권장 역할"],
            [
                ["MovieLens ml-32m", "user-movie rating event", "timestamp, 대규모 평점, 자유 태그", "리뷰 없음, 설명 신호 약함", "추천 모델의 주 상호작용 로그"],
                ["Genome 2021", "movie metadata + reviews + tag supervision", "리뷰, tag count, survey, relevance score", "timestamp 없음", "아이템 semantic feature와 설명 신호"],
            ],
        ),
        "",
        "## 3. 연결 방식",
        "",
        markdown_table(
            ["항목", "설명"],
            [
                ["연결 키", "`movieId`와 `item_id`를 동일 영화 키로 사용"],
                ["겹치는 영화 수", format_int(overlap_movies)],
                ["MovieLens 대비 Genome 커버리지", percent(overlap_movies, ml_summary["movie_count"])],
                ["Genome 대비 MovieLens 커버리지", percent(overlap_movies, genome_summary["movie_count"])],
            ],
        ),
        "",
        "사용자 id는 안전한 조인 키가 아니다. 두 소스를 합칠 때는 영화 중심으로 붙이고, 사용자 축은 분리된 것으로 보는 편이 맞다.",
        "",
        "## 4. 작업 디렉터리 구조",
        "",
        "- MovieLens 상세 결과: `eda_outputs/ml_32m/`",
        "- Genome 2021 상세 결과: `eda_outputs/genome_2021/`",
        "- 현재 문서 3개: 두 소스를 다시 모아 비교한 루트 요약",
        "",
        "## 5. 실무적으로 해석하면",
        "",
        f"- 시퀀스 추천이나 adaptive multi-interest의 backbone은 MovieLens다. timestamp가 있고 평점 수가 {format_int(ml_summary['ratings_total'])}건이기 때문이다.",
        f"- 영화 의미 표현과 설명 가능성은 Genome 2021이 보강한다. 리뷰 {format_int(genome_summary['review_total'])}건, 설문 응답 {format_int(genome_summary['survey_answer_total'])}건, relevance score 파일이 있기 때문이다.",
        "- 따라서 전처리와 분석은 source별로 유지하고, 모델링 직전 혹은 feature store 단계에서 item 기준으로 결합하는 구조가 가장 안정적이다.",
        "",
        "## 6. 상세 문서 링크",
        "",
        "- MovieLens 설명: [ml_32m/dataset_description_report_ko.md](ml_32m/dataset_description_report_ko.md)",
        "- Genome 2021 설명: [genome_2021/dataset_description_report_ko.md](genome_2021/dataset_description_report_ko.md)",
        "- 전체 비교 EDA: [eda_report.md](eda_report.md)",
        "",
    ]

    return write_markdown(report_path, lines)


def write_combined_usage_report_ko(ml_summary: dict, genome_summary: dict, overlap_movies: int) -> str:
    report_path = os.path.join(EDA_OUTPUT_DIR, "adaptive_multi_interest_data_usage_ko.md")

    lines = [
        "# 두 소스를 함께 쓰는 추천 실험 가이드",
        "",
        f"작성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 결론",
        "",
        "Adaptive multi-interest 추천을 하려면 두 소스를 같은 역할로 쓰면 안 된다.",
        "",
        "- MovieLens ml-32m: 사용자 행동 시퀀스의 본체",
        "- Genome 2021: 영화 semantic feature와 설명 신호 보강",
        "",
        "## 2. 권장 파이프라인",
        "",
        "1. MovieLens `ratings.csv`를 사용자별 시간순 시퀀스로 재구성한다.",
        "2. Genome 2021의 `metadata`, `reviews`, `tag_count`, `scores`를 영화 side feature로 정리한다.",
        f"3. 두 소스는 겹치는 영화 {format_int(overlap_movies)}편에 한해 `movieId = item_id`로 결합한다.",
        "4. 먼저 MovieLens만으로 baseline을 만든다.",
        "5. 그 다음 Genome feature를 추가해서 tail item 설명력과 성능 개선을 본다.",
        "",
        "## 3. 왜 이렇게 나눠야 하는가",
        "",
        f"- MovieLens는 timestamp가 있어서 관심사 전환과 최근성 정보를 볼 수 있다. 이 소스의 평점은 {format_int(ml_summary['ratings_total'])}건이다.",
        f"- Genome 2021은 timestamp는 없지만 리뷰 {format_int(genome_summary['review_total'])}건과 tag relevance score 파일이 있어서 영화 의미를 더 잘 설명한다.",
        "- 즉 하나는 행동 로그, 다른 하나는 아이템 표현이다.",
        "",
        "## 4. 추천 실험 순서",
        "",
        "1. Baseline: MovieLens 시퀀스만 사용",
        "2. Content Augmentation: Genome 메타데이터, 리뷰 임베딩, tag relevance 추가",
        "3. Explainability: 추천된 아이템에 대해 상위 tag score와 리뷰 키워드로 설명 생성",
        "4. Evaluation: 전체 NDCG/HitRate뿐 아니라 head/tail movie bucket 성능을 분리해서 측정",
        "",
        "## 5. 주의사항",
        "",
        "- Genome 2021의 평점을 시퀀스 데이터처럼 쓰면 안 된다. timestamp가 없다.",
        "- 사용자 id를 두 소스 사이에서 동일 인물로 가정하면 안 된다.",
        "- 조인 전에 영화 id overlap을 기준으로 item universe를 명시적으로 제한해야 한다.",
        "- long-tail이 강하므로 인기작 위주 성능만 보고 결론 내리면 안 된다.",
        "",
        "## 6. 상세 분석 위치",
        "",
        "- MovieLens EDA: [ml_32m/eda_report.md](ml_32m/eda_report.md)",
        "- Genome 2021 EDA: [genome_2021/eda_report.md](genome_2021/eda_report.md)",
        "- 통합 비교: [dataset_description_report_ko.md](dataset_description_report_ko.md)",
        "",
    ]

    return write_markdown(report_path, lines)


def run(ml_summary: dict, genome_summary: dict) -> dict:
    overlap_movies = len(ml_summary["movie_ids"] & genome_summary["movie_ids"])

    eda_report_path = write_combined_eda_report(ml_summary, genome_summary, overlap_movies)
    dataset_report_path = write_combined_dataset_report_ko(ml_summary, genome_summary, overlap_movies)
    usage_report_path = write_combined_usage_report_ko(ml_summary, genome_summary, overlap_movies)

    return {
        "eda_report_path": eda_report_path,
        "dataset_report_path": dataset_report_path,
        "usage_report_path": usage_report_path,
        "overlap_movies": overlap_movies,
    }
