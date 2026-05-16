# degent

영화 추천 시스템 연구 프로젝트다. MovieLens 32M을 주 상호작용 로그로 사용하고, Genome 2021과 ML-32M 확장 데이터를 보조 신호로 활용해 전처리, 품질 필터링, user-sequence 생성, EDA를 수행한다.

이 프로젝트의 운영 규칙, 디렉터리 정책, LLM 작업 절차는 `PROJECT_GUIDE.md`가 정본이다. 이 README는 빠른 실행과 사용 안내를 위한 문서다.

## 프로젝트 범위

- 목적: 학습 가능한 영화 추천용 데이터셋을 일관된 스키마와 파이프라인으로 정리한다.
- 주 데이터: `data/ml-32m/raw/`
- 보조 데이터: `data/genome_2021/`, `data/ml-32m-extension-main/`
- 주요 산출물:
  - `data/movies_processed.csv`
  - `data/movies_processed_drop.csv`
  - `data/ratings_drop.csv`
  - `data/ratings_drop_processed.jsonl`
- 보조 산출물:
  - `data/ml-32m/genre.csv`

## 저장소 구조

```text
degent/
├── data/
│   ├── docs/                         # 연구 제안서 등 참고 문서
│   ├── ml-32m/                       # MovieLens 32M
│   │   ├── raw/                      # 원본 CSV
│   │   ├── genre.csv                 # 장르 multi-hot 보조 산출물
│   │   └── readme.md                 # 데이터셋 설명
│   ├── ml-32m-extension-main/        # ML-32M extension
│   ├── genome_2021/                  # Genome 2021
│   ├── movies_processed.csv          # 영화 통합 전처리 결과
│   ├── movies_processed_drop.csv     # drop 규칙 반영 영화 데이터
│   ├── ratings_drop.csv              # drop 결과 반영 rating 데이터
│   └── ratings_drop_processed.jsonl  # user sequence JSONL
├── schemas/                          # 데이터 계약 정본
├── preprocess/                       # 전처리 및 후처리 스크립트
├── replay/                           # C++ rating replay event generator
├── dashboard/                        # 클러스터링 결과 시각화 대시보드
├── model/                            # SASRec + Contrastive Loss 모델 파이프라인
├── outputs/                          # 모델 산출물과 실행 로그
├── experiments/                      # 로컬 실험 메타데이터 (git에는 구조 파일만 유지)
├── eda/                              # raw / processed EDA
├── docs/                             # 데이터 흐름, 산출물, 설계 결정 보조 문서
├── plan/                             # 로컬 작업 계획서와 상태별 보관 (git에는 구조 파일과 템플릿만 유지)
├── todo.md                           # 현재 작업 상태와 협업 메모
├── AGENTS.md                         # Codex 등 LLM 작업 진입점
├── CLAUDE.md                         # Claude 작업 진입점
├── .cursorrules                      # Cursor 작업 진입점
├── .windsurfrules                    # Windsurf 작업 진입점
├── requirements.txt
├── PROJECT_GUIDE.md
└── README.md
```

## 데이터 준비

원본 데이터는 git으로 추적하지 않는다. `data/**/raw/` 내부 파일은 각자 로컬에 직접 배치해야 한다.

### MovieLens 32M

아래 파일이 `data/ml-32m/raw/`에 있어야 한다.

- `movies.csv`
- `ratings.csv`
- `tags.csv`
- `links.csv`

### Genome 2021

raw EDA를 실행하려면 Genome 2021 데이터가 `data/genome_2021/` 아래에 배치되어 있어야 한다. 현재 코드 기준으로 다음 경로 중 하나를 인식한다.

- `data/genome_2021/raw/`
- `data/genome_2021/movie_dataset_public_final/`

### ML-32M Extension

보조 데이터 설명은 `data/ml-32m-extension-main/readme.md`를 참고한다. 현재 루트 파이프라인의 필수 입력은 아니지만 연구 참고용으로 보관한다.

## 환경 설정

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

패키지 설치와 제거는 프로젝트 루트의 repo-local `.venv`에서만 수행한다. 시스템 Python, `sudo pip`, OS package manager, 전역 CUDA/toolkit 설치는 프로젝트 작업 범위에서 사용하지 않는다.

주요 의존성은 `torch==2.5.1+cu121`, RAPIDS/cuML `25.10.0`, `polars`, `matplotlib`, `numpy`, `PyYAML`, `pandas`, `umap-learn`, `hdbscan`, `streamlit`, `plotly`다. GPU dependency 버저닝 결정은 `docs/decisions/0003-pin-rapids-cuml-gpu-dependencies.md`를 따른다.

## 전처리 파이프라인

기본 파이프라인은 아래 순서로 진행한다.

### 1. 영화 메타데이터 통합 전처리

MovieLens 원본 CSV를 조인해 `data/movies_processed.csv`를 만든다.

```bash
python3 preprocess/preprocess_movie/preprocess_movies.py
```

주요 처리:

- `title`에서 연도를 분리해 `title`, `releaseYear` 생성
- `genres`를 JSON 배열 문자열로 변환
- `tags.csv`를 집계해 `tag`, `tagCount` 생성
- `ratings.csv`를 집계해 `ratingAvg`, `ratingCount` 생성
- `links.csv`를 조인해 `imdbId`, `tmdbId` 추가

함께 생성되는 검증 산출물:

- `preprocess/preprocess_movie/validation_report.json`
- `preprocess/preprocess_movie/bad_rows.csv`

### 2. 영화 drop 후처리

학습용 영화 테이블을 만들기 위해 품질 규칙으로 row를 제거한다.

```bash
python3 preprocess/drop_movie/drop_movies.py
```

현재 drop 기준:

- `ratingCount == 0`
- `genres == []`
- `releaseYear IS NULL`

출력:

- `data/movies_processed_drop.csv`
- `preprocess/drop_movie/validation_report.json`
- `preprocess/drop_movie/bad_rows.csv`

### 3. rating drop 전파

유지된 영화 집합만 남기도록 raw rating을 필터링한다.

```bash
python3 preprocess/drop_rating/drop_ratings.py
```

출력:

- `data/ratings_drop.csv`
- `preprocess/drop_rating/validation_report.json`
- `preprocess/drop_rating/bad_rows.csv`

`ratings_drop.csv`는 `userId`, `ratedAt`, `movieId` 기준으로 정렬되어 저장된다.

### 4. user sequence JSONL 생성

rating row를 user별 chronological history로 재구성한다.

```bash
python3 preprocess/process_rating/process_ratings_drop.py
```

출력:

- `data/ratings_drop_processed.jsonl`
- `preprocess/process_rating/validation_report.json`
- `preprocess/process_rating/bad_rows.csv`

JSONL 레코드 예시:

```json
{
  "userId": 123,
  "ratings": [
    {
      "ratedAt": "2001-01-01T00:00:00Z",
      "movieId": 10,
      "rating": 4.0
    }
  ],
  "ratingCount": 1,
  "firstRatedAt": "2001-01-01T00:00:00Z",
  "lastRatedAt": "2001-01-01T00:00:00Z"
}
```

### 보조. 장르 multi-hot CSV 생성

MovieLens `movies.csv`의 pipe-delimited genre를 multi-hot 벡터로 변환해 `data/ml-32m/genre.csv`를 만든다. 현재 메인 전처리/drop/user-sequence 파이프라인의 필수 단계는 아니다.

```bash
(cd preprocess/preprocess_genre && python3 preprocess_genre.py)
```

출력:

- `data/ml-32m/genre.csv`

## EDA

### Raw 데이터 EDA

MovieLens와 Genome 2021을 분리 분석한 뒤 통합 요약까지 생성한다.

```bash
python3 -m eda.raw.eda_overview --source all
```

선택 가능한 옵션:

- `--source ml32m`
- `--source genome2021`
- `--source combined`
- `--source all`

출력 위치:

- `eda/eda_outputs/ml_32m/`
- `eda/eda_outputs/genome_2021/`
- `eda/eda_outputs/`

이전 실행 결과가 `eda/raw/outputs/` 아래에 남아 있을 수 있지만, 현재 raw EDA 코드는 `eda/eda_outputs/`를 쓴다.

### Processed 데이터 EDA

전처리 완료 후 산출물을 기준으로 drop 전후 비교와 최종 rating 분포를 분석한다.

```bash
python3 eda/processed/eda_processed.py
```

출력 위치:

- `eda/processed/outputs/eda_report.md`
- `eda/processed/outputs/*.png`

## 추천 대시보드

사용자 상태 임베딩 cluster 결과, pre-T seed state, post-T replay 진행 상황, replay 이후 interest state를 인터랙티브하게 탐색할 수 있다.

```bash
streamlit run dashboard/cluster_dashboard.py
```

사이드바의 `Dashboard view`에서 `PRE Cluster`, `PRE Seed State`, `POST Replay`, `POST Interest State`를 전환한다. 기본 post replay artifact가 있으면 `POST Replay`가 먼저 열리고, 없으면 `PRE Cluster`가 먼저 열린다.

PRE Cluster는 기본적으로 아래 결과 파일을 기대한다.

- `data/clustering/user_clusters.parquet`

`outputs/user_interests.npz`를 `data/clustering/user_clusters.parquet`로 변환하는 export 스크립트는 `model/batch/export_clusters.py`다.

사용법:

```bash
python3 -m model.batch.export_clusters \
  --input outputs/user_interests.npz \
  --output data/clustering/user_clusters.parquet
```

필수 컬럼:

- `userId`
- `clusterLabel`
- `x`
- `y`

선택 컬럼:

- `z`
- `clusterProbability`
- `outlierScore`
- `sequenceLength`
- `embeddingNorm`

Dashboard는 네 view로 나뉜다. `PRE Cluster`는 `outputs/pre/**/user_interests.npz`를 자동 탐색해 pre-T batch cluster를 선택할 수 있게 하고, `PRE Seed State`는 `outputs/pre/**/pre_summary.json`과 `state.sqlite`를 읽어 replay 시작 상태를 요약한다. 실제 결과 파일이 아직 없으면 앱에서 demo 데이터를 사용해 cluster UI를 먼저 점검할 수 있다. 세부 입력 계약은 `dashboard/README.md`를 따른다.

`POST Replay`는 `outputs/post/**/replay_summary.json`을 자동 탐색해 post-T replay artifact를 읽는 read-only view다. 선택한 summary의 `paths` 값이 있으면 그 경로를 우선 사용한다. `paths.replayDb`가 있으면 해당 `replay.sqlite`를 우선 읽고, 없으면 같은 post run root의 JSONL artifact를 fallback으로 읽는다. `POST Interest State`는 같은 `replay.sqlite`에서 replay 이후 final interest state와 interest vector projection을 표시한다. 세부 파일 계약은 `docs/streaming-replay-dashboard-contract.md`를 따른다.

## 모델 실험 기록

`python3 -m model.batch.train`는 기본적으로 새 run id를 만들고, `python3 -m model.batch.extract_canonical`, `python3 -m model.batch.cluster`, `python3 -m model.batch.recommend`, `python3 -m model.stream.recommend_online`은 최신 run id를 이어받는다.

`python3 -m model.batch.extract_canonical`은 event 하나당 embedding 하나를 보장하는 `outputs/canonical_embeddings.npz`를 저장한다. 현재 `python3 -m model.batch.cluster`의 기본 입력도 이 canonical embedding이다. 과거 overlap-window 추출 산출물인 `outputs/embeddings.npz`는 legacy artifact로만 취급한다.

`python3 -m model.batch.cluster`는 유저별 UMAP+HDBSCAN을 실행하고 `outputs/user_interests.npz`와 `outputs/batch/interest_states/{user_id}.json`을 만든다. `model.common.cluster`의 공통 backend를 사용하며 `--cluster-backend auto`는 가능한 경우 GPU, 불가능하면 CPU fallback을 사용한다. 장르 라벨링은 `data/movies_processed_drop.csv`가 있으면 자동으로 붙는다.

`python3 -m model.batch.export_clusters`는 `outputs/user_interests.npz`를 dashboard용 `data/clustering/user_clusters.parquet` 등 테이블 포맷으로 변환한다.

`python3 -m model.stream.extract_online`은 raw rating event를 user state에 모두 저장하고, 현재까지 관측된 history 기준 positive projection에서 active online embedding을 만든다. 기본 출력은 `outputs/stream/user_states/{user_id}.json`, `outputs/stream/online_embeddings.npz`, `outputs/stream/online_embedding_events.jsonl`이다.

`python3 -m model.stream.interest_assign`은 active online embedding을 user별 interest state에 연결한다. interest vector가 없으면 pending buffer와 refit request를 남기고, interest vector가 있으면 cosine similarity로 assign한다. 기본 출력은 `outputs/stream/interest_states/{user_id}.json`, `outputs/stream/interest_assignments.jsonl`, `outputs/stream/refit_requests.jsonl`이다.

`python3 -m model.stream.cluster_refit`은 open refit request를 소비해 user별 active embedding 전체를 다시 clustering하고 interest state를 replace한다. 기본 backend는 `auto`이며 cuML import와 CUDA runtime probe가 통과하면 GPU를 사용한다. GPU가 불가하거나 `auto` GPU refit 실행이 실패하면 CPU `umap-learn + hdbscan`으로 fallback한다. refit 결과는 `outputs/stream/refit_events.jsonl`에 기록된다.

`python3 -m model.stream.recommend_online`은 `outputs/stream/interest_states/{user_id}.json`의 interest vector와 SASRec item embedding으로 `score(u, i) = max_k(u_k^T v_i)`를 계산해 `outputs/stream/stream_recommendations.jsonl`에 top-K 추천을 append한다. seen positive item은 기본적으로 제외한다.

`make -C replay`는 `replay/bin/rating_replay`를 빌드한다. `python3 -m model.stream.replay_pipeline`은 replay input event를 timestamp trace로 소비해 `--speed N` 기준 schedule에 맞춰 event를 주입하고, 각 event 처리 후 `extract_online -> interest_assign -> cluster_refit`을 호출한다. 기본 replay root는 `outputs/post/replay_demo/`이고, temporal run은 `--output-root outputs/post/<run_label>_events_<N>[_recommend]`처럼 명시해 실행 범위를 드러낸다. 각 root 아래에는 `replay.sqlite`, `ingress_events.jsonl`, event-level `replay_events.jsonl`, `replay_summary.json`, replay-scoped state/log/embedding을 기록한다. SQLite는 payload/state/metadata/lifecycle/runtime metric을 기록하고, 대형 vector artifact는 기존 NPZ/checkpoint 파일로 유지한다. `--recommend`를 추가하면 event 처리 후 `recommend_online`을 실행해 output root의 `stream_recommendations.jsonl`도 남긴다.

로컬 실험 기록:

- 로컬 `experiments/model/<run_id>/manifest.json`: command, git 상태, 입력/출력 metadata, config
- 로컬 `experiments/model/<run_id>/metrics.jsonl`: 학습 지표와 extract/cluster summary
- 로컬 `experiments/model/<run_id>/notes.md`: 사람이 적는 실험 해석

모델 산출물과 run별 실험 기록은 git으로 추적하지 않는다. 세부 옵션은 `model/README.md`를 따른다.

## 모델 변경 이력 찾기

이전 모델 코드는 `model/prev/` 같은 스냅샷 디렉터리에 복사하지 않는다.

- 현재 공식 구조와 실행 경로: `PROJECT_GUIDE.md`, `model/README.md`
- 구조 변경과 모델링 판단 이유: `docs/decisions/`
- 실험별 config, metric, 산출물 참조, 이전 run 대비 관찰: 로컬 `experiments/model/<run_id>/`
- 특정 파일의 과거 코드: git history

비교 대상으로 계속 실행해야 하는 구현은 별도 결정 후 `model/baselines/`처럼 목적이 명확한 경로로 둔다.

## 스키마 정책

스키마는 `schemas/`가 정본이다.

- 컬럼 추가, 변경, 삭제는 코드보다 먼저 `schemas/`에서 정의한다.
- 호환되지 않는 변경은 새 버전 파일로 올린다.
- CSV에 배열을 저장할 때는 `logical_type`과 `physical_type`을 분리한다.
- 현재 주요 processed 스키마:
  - `schemas/ml32m/processed/movies_processed.v2.schema.yaml`
  - `schemas/ml32m/processed/ratings_drop.v1.schema.yaml`
  - `schemas/ml32m/processed/ratings_drop_processed.v1.schema.yaml`

세부 규칙은 `schemas/README.md`를 참고한다.

## 협업 규칙

- `PROJECT_GUIDE.md`를 단일 진실 공급원으로 사용한다.
- 새 작업 전 `todo.md`와 `plan/active/`의 기존 계획을 확인한다.
- `data/**/raw/`는 절대 수정하지 않는다.
- 생성물은 스크립트로 재생성하는 것을 원칙으로 한다. 단, `data/ml-32m/genre.csv`처럼 명시적으로 포함된 보조 산출물은 예외로 둔다.
- 데이터셋이나 파이프라인이 바뀌면 `PROJECT_GUIDE.md`도 함께 갱신한다.

## 현재 산출물 기준 참고 수치

현재 저장소에 포함된 validation artifact 기준 요약은 다음과 같다.

- `movies_processed.csv`: 87,584 rows
- `movies_processed_drop.csv`: 77,647 rows
- `ratings_drop.csv`: 31,916,363 rows
- `ratings_drop_processed.jsonl`: 200,948 users

정확한 세부 수치는 각 `validation_report.json`을 확인하면 된다.

## 관련 문서

- `PROJECT_GUIDE.md`: 프로젝트 운영 규칙과 구조
- `docs/README.md`: GitHub에 남는 docs 읽는 순서와 local-only 기록 정책
- `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.windsurfrules`: LLM 도구별 진입점
- `todo.md`: 현재 작업 상태와 협업 메모
- `docs/current-pipeline-snapshot.md`: 현재 batch / streaming 구현, streaming data flow, 문제 포인트
- `docs/data-flow.md`: raw -> processed -> model -> dashboard 흐름
- `docs/artifacts.md`: 원본 데이터와 생성물의 수정 가능 여부
- `docs/decisions/`: 중요한 설계 결정 기록
- `experiments/model/README.md`: 로컬 모델 실험 메타데이터 기록 규칙
- `schemas/README.md`: 스키마 컨벤션
- `preprocess/README.md`: 전처리 실행 순서와 입출력
- `plan/_template.md`: 새 로컬 계획서 템플릿
- `plan/active/`, `plan/done/`, `plan/expired/`: 로컬 작업 계획서 보관 구조
- `experiments/model/`: 로컬 모델 실험 메타데이터. git에는 README와 `.gitkeep` 구조만 유지
- `eda/processed/outputs/eda_report.md`: processed 데이터 분석 결과
- `eda/eda_outputs/eda_report.md`: raw 데이터 통합 분석 결과
