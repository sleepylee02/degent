# Part Contracts

이 문서는 여러 명이 작업을 나눌 때 각 파트가 어떤 파일을 맡고, 무엇을 입력으로 받아서, 무엇을 출력해야 하는지 정리한 협업용 계약표다.

프로젝트 전체 규칙은 `PROJECT_GUIDE.md`를 따른다. 컬럼 단위 스키마 변경은 `schemas/`를 먼저 수정한다. Streaming replay e2e 실행과 단계별 데이터 전달 설명은 `docs/streaming-e2e-pipeline.md`를 참고한다.

## 읽는 법

- `담당 파일`: 해당 파트 작업자가 주로 수정하는 파일
- `Input`: 이 파트가 읽는 파일이나 산출물
- `Output`: 이 파트가 만들어 다음 파트에 넘기는 파일이나 산출물
- `Endpoint`: 실행 진입점. 현재는 CLI와 파일 산출물 기준
- `넘기는 기준`: 다음 파트가 받아도 되는 최소 조건

원본 데이터(`data/**/raw/`)와 생성물(`data/*.csv`, `data/*.jsonl`, `outputs/**`)은 직접 편집하지 않는다. 반드시 담당 endpoint로 재생성한다.

## 전체 파트 흐름

```text
A. Schema/Data Contract
  -> B. Preprocessing
  -> C. Batch Model
  -> D. Streaming State/Interest
  -> E. Replay Pipeline
  -> F. Dashboard

G. Experiment/Docs Tracking은 모든 파트의 실행 기록과 문서 갱신을 담당한다.
```

## A. Schema/Data Contract

데이터 컬럼, 타입, raw 파일 계약을 먼저 고정하는 파트다.

| 항목 | 내용 |
|---|---|
| 담당 파일 | `schemas/README.md`, `schemas/ml32m/raw/*.schema.yaml`, `schemas/ml32m/processed/*.schema.yaml`, `data/*/readme.md` |
| Input | 원본 데이터 문서, raw CSV header, downstream에서 필요한 컬럼 요구사항 |
| Output | 버전이 명시된 schema YAML, 데이터셋별 readme |
| Endpoint | 파일 계약 문서. 별도 실행 명령 없음 |
| 넘기는 기준 | downstream 코드가 참조할 컬럼명, 타입, nullable, primary key, constraints가 명시되어 있어야 함 |

### A 파트가 건드리지 않는 것

- `data/**/raw/` 원본 파일
- 전처리 생성물 CSV/JSONL
- 모델 산출물

### 다음 파트로 넘기는 것

- `schemas/ml32m/raw/*.schema.yaml`
- `schemas/ml32m/processed/*.schema.yaml`
- 변경이 있으면 `PROJECT_GUIDE.md`, `docs/data-flow.md`, `docs/artifacts.md` 갱신 필요 여부

## B. Preprocessing

raw 데이터를 모델 학습 가능한 CSV/JSONL로 바꾸는 파트다.

| 항목 | 내용 |
|---|---|
| 담당 파일 | `preprocess/preprocess_movie/`, `preprocess/drop_movie/`, `preprocess/drop_rating/`, `preprocess/process_rating/`, `preprocess/README.md` |
| Input | `data/ml-32m/raw/*.csv`, A 파트의 `schemas/` 계약 |
| Output | `data/movies_processed.csv`, `data/movies_processed_drop.csv`, `data/ratings_drop.csv`, `data/ratings_drop_processed.jsonl`, 각 단계의 validation report |
| Endpoint | 아래 단계별 CLI |
| 넘기는 기준 | 생성물이 스키마와 맞고, 각 단계의 `validation_report.json`이 생성되어야 함 |

### B 파트 endpoint

| step | 파일 | Input | Output |
|---|---|---|---|
| movie preprocess | `preprocess/preprocess_movie/preprocess_movies.py` | `data/ml-32m/raw/{movies,ratings,tags,links}.csv` | `data/movies_processed.csv` |
| movie drop | `preprocess/drop_movie/drop_movies.py` | `data/movies_processed.csv` | `data/movies_processed_drop.csv` |
| rating drop | `preprocess/drop_rating/drop_ratings.py` | `data/ml-32m/raw/ratings.csv`, `data/movies_processed_drop.csv` | `data/ratings_drop.csv` |
| rating sequence | `preprocess/process_rating/process_ratings_drop.py` | `data/ratings_drop.csv` | `data/ratings_drop_processed.jsonl` |
| genre auxiliary | `preprocess/preprocess_genre/preprocess_genre.py` | `data/ml-32m/raw/movies.csv` | `data/ml-32m/genre.csv` |

### B 파트가 C 파트에 넘기는 것

- 필수: `data/ratings_drop_processed.jsonl`
- 보조: `data/movies_processed_drop.csv`, `data/ratings_drop.csv`
- 검증: `preprocess/*/validation_report.json`, 필요 시 `preprocess/*/bad_rows.csv`

## C. Batch Model

전처리된 user sequence를 받아 모델을 학습하고 batch embedding/cluster 산출물을 만드는 파트다.

| 항목 | 내용 |
|---|---|
| 담당 파일 | `model/batch/`, `model/common/`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md` |
| Input | `data/ratings_drop_processed.jsonl`, B 파트 validation 결과 |
| Output | `outputs/sasrec_cl.pt`, `outputs/item2idx.json`, `outputs/canonical_embeddings.npz`, `outputs/user_interests.npz`, `outputs/batch/interest_states/`, `data/clustering/user_clusters.parquet`, `outputs/viz/`, run metadata |
| Endpoint | `python3 -m model.batch.*` |
| 넘기는 기준 | 모델 artifact와 `experiments/model/<run_id>/manifest.json`, `metrics.jsonl`이 같은 run 기준으로 남아야 함 |

### C 파트 endpoint

| step | 파일 | Input | Output |
|---|---|---|---|
| train | `model/batch/train.py` | `data/ratings_drop_processed.jsonl` | `outputs/sasrec_cl.pt`, `outputs/item2idx.json` |
| canonical extract | `model/batch/extract_canonical.py` | model artifact, `data/ratings_drop_processed.jsonl` | `outputs/canonical_embeddings.npz` |
| cluster | `model/batch/cluster.py` | `outputs/canonical_embeddings.npz` | `outputs/user_interests.npz`, `outputs/batch/interest_states/{user_id}.json` |
| cluster export | `model/batch/export_clusters.py` | `outputs/user_interests.npz` | `data/clustering/user_clusters.parquet` |
| batch recommend | `model/batch/recommend.py` | interest vector NPZ, model artifact | `outputs/recommendations.csv`, `outputs/recommendations.npz` |
| visualize | `model/batch/visualize_clusters.py` | `outputs/user_interests.npz` | `outputs/viz/` |

### C 파트가 D/E/F 파트에 넘기는 것

- D 파트: `outputs/sasrec_cl.pt`, `outputs/item2idx.json`, 필요 시 `outputs/canonical_embeddings.npz`
- E 파트: replay orchestration에 필요한 model artifact
- F 파트: `data/clustering/user_clusters.parquet`를 Cluster explorer가 읽음. 이 파일은 `model/batch/export_clusters.py`로 재생성

## D. Streaming State/Interest

rating event를 online user state로 반영하고, active positive embedding을 interest state에 연결하는 파트다.

| 항목 | 내용 |
|---|---|
| 담당 파일 | `model/stream/state.py`, `model/stream/extract_online.py`, `model/stream/interest_assign.py`, `model/stream/cluster_refit.py`, `model/stream/recommend_online.py` |
| Input | C 파트 model artifact, `data/ratings_drop_processed.jsonl` 또는 E 파트 trace replay event |
| Output | `outputs/stream/user_states/`, `outputs/stream/online_embeddings.npz`, `outputs/stream/interest_states/`, `outputs/stream/interest_assignments.jsonl`, `outputs/stream/refit_requests.jsonl`, `outputs/stream/refit_events.jsonl`, `outputs/stream/stream_recommendations.jsonl` |
| Endpoint | `python3 -m model.stream.*` |
| 넘기는 기준 | user state, online embedding, assignment/refit log가 같은 output scope 안에서 만들어져야 함 |

### D 파트 endpoint

| step | 파일 | Input | Output |
|---|---|---|---|
| online extract | `model/stream/extract_online.py` | rating events, `outputs/sasrec_cl.pt`, `outputs/item2idx.json` | `outputs/stream/user_states/{user_id}.json`, `outputs/stream/online_embeddings.npz`, `outputs/stream/online_embedding_events.jsonl` |
| interest assign | `model/stream/interest_assign.py` | `outputs/stream/online_embeddings.npz`, existing `outputs/stream/interest_states/` | `outputs/stream/interest_states/{user_id}.json`, `outputs/stream/interest_assignments.jsonl`, `outputs/stream/refit_requests.jsonl` |
| cluster refit | `model/stream/cluster_refit.py` | `outputs/stream/refit_requests.jsonl`, `outputs/stream/online_embeddings.npz` | updated `outputs/stream/interest_states/{user_id}.json`, `outputs/stream/refit_events.jsonl` |
| online recommend | `model/stream/recommend_online.py` | `outputs/stream/interest_states/`, `outputs/stream/user_states/`, model artifact | `outputs/stream/stream_recommendations.jsonl` |

### D 파트가 E/F 파트에 넘기는 것

- E 파트가 replay scope에서 같은 endpoint를 재사용할 수 있어야 함
- F 파트는 replay demo의 stream artifact를 read-only로 읽음

## E. Trace Replay

과거 rating sequence를 timestamp trace로 재생하고, `--speed N` virtual clock 기준으로 event를 주입한 뒤 D 파트 endpoint를 event 단위로 호출하는 파트다.

| 항목 | 내용 |
|---|---|
| 담당 파일 | `replay/`, `model/stream/trace_replay.py`, `model/stream/replay_pipeline.py`, `docs/streaming-replay-dashboard-contract.md`, `replay/README.md` |
| Input | `data/ratings_drop_processed.jsonl`, C 파트 model artifact |
| Output | `outputs/stream/replay_demo/replay_input_events.jsonl`, `ingress_events.jsonl`, `replay_summary.json`, `replay_events.jsonl`, replay-scoped stream artifacts, optional `stream_recommendations.jsonl` |
| Endpoint | `make -C replay`, `replay/bin/rating_replay`, `python3 -m model.stream.replay_pipeline --speed N` |
| 넘기는 기준 | 모든 replay demo artifact는 `outputs/stream/replay_demo/` 아래에 격리되어야 함 |

### E 파트 endpoint

| step | 파일 | Input | Output |
|---|---|---|---|
| build replay binary | `replay/Makefile`, `replay/cpp/` | C++ source | `replay/bin/rating_replay` |
| generate replay input | `replay/bin/rating_replay` | `data/ratings_drop_processed.jsonl` | `outputs/stream/replay_demo/replay_input_events.jsonl` |
| trace replay runner | `model/stream/replay_pipeline.py` | replay input events, model artifact, `--speed N` | `outputs/stream/replay_demo/replay_summary.json`, `ingress_events.jsonl`, `replay_events.jsonl`, replay-scoped states/logs, optional `stream_recommendations.jsonl` |

### E 파트가 F 파트에 넘기는 것

- 필수 entrypoint: `outputs/stream/replay_demo/replay_summary.json`
- 추가 read files: `ingress_events.jsonl`, `replay_events.jsonl`, `interest_assignments.jsonl`, `refit_requests.jsonl`, `refit_events.jsonl`, `interest_states/{user_id}.json`, `stream_recommendations.jsonl`
- 세부 파일 계약: `docs/streaming-replay-dashboard-contract.md`

## F. Dashboard

batch cluster 결과나 replay 진행 상황을 사람이 탐색하는 read-only UI 파트다.

| 항목 | 내용 |
|---|---|
| 담당 파일 | `dashboard/`, `dashboard/README.md` |
| Input | batch cluster table 또는 E 파트 replay artifact |
| Output | Streamlit UI. 데이터 artifact를 생성하거나 수정하지 않음 |
| Endpoint | `streamlit run dashboard/cluster_dashboard.py` |
| 넘기는 기준 | dashboard는 입력 artifact를 read-only로 읽고, 없는 경우 demo data로 UI만 확인 가능해야 함 |

### F 파트 input

| view | Input | Contract |
|---|---|---|
| Cluster explorer | `data/clustering/user_clusters.parquet` 또는 `.csv/.jsonl/.ndjson` | `dashboard/README.md`의 필수 컬럼: `userId`, `clusterLabel`, `x`, `y` |
| Replay monitor | `outputs/stream/replay_demo/replay_summary.json` | `docs/streaming-replay-dashboard-contract.md` |

### F 파트 연결 기준

- Cluster explorer 입력은 `model/batch/export_clusters.py`로 생성한다.
- Replay monitor는 summary `paths`가 있으면 이를 우선 사용하며, `stream_recommendations.jsonl`이 있으면 recommendation view도 표시한다.

## G. Experiment/Docs Tracking

실험 비교, 실행 기록, 문서 동기화를 담당하는 보조 파트다.

| 항목 | 내용 |
|---|---|
| 담당 파일 | `experiments/model/`, `outputs/readme.md`, `docs/`, `todo.md`, `plan/`, `PROJECT_GUIDE.md` |
| Input | 각 파트의 실행 명령, artifact 경로, metric, git 상태, 변경된 계약 |
| Output | `experiments/model/<run_id>/manifest.json`, `metrics.jsonl`, `notes.md`, 갱신된 문서 |
| Endpoint | 모델 스크립트의 metadata writer, 사람이 작성하는 notes/docs |
| 넘기는 기준 | 같은 run의 입력, 출력, config, metric을 나중에 재현 가능하게 남겨야 함 |

## Handoff Note Template

파트 작업을 넘길 때 아래 형식으로 남긴다.

```text
Part:
Owner:
Changed files:
Input used:
Command:
Output produced:
Validation result:
Next consumer:
Known issue:
```

## 경계 변경 규칙

- 다른 파트의 input 파일 경로나 컬럼을 바꾸면 먼저 이 문서와 `schemas/`를 수정한다.
- output을 새로 만들면 누가 소비하는지 명시한다.
- 기존 output을 대체하면 이전 consumer가 깨지지 않게 migration 기준을 남긴다.
- dashboard는 replay/stream 내부 함수에 의존하지 않고 artifact만 읽는다.
- replay demo output은 `outputs/stream/replay_demo/` 아래에만 쓴다.
