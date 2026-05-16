# Recommendation Dashboard

사용자 상태 임베딩 클러스터링 결과, pre-T seed state, post-T replay 진행 상황, replay 이후 interest state를 탐색하기 위한 Streamlit 대시보드.

## 실행

```bash
streamlit run dashboard/cluster_dashboard.py
```

기본 post replay artifact가 있으면 `POST Replay`가 먼저 열리고, 없으면 `PRE Cluster`가 먼저 열린다. 사이드바의 `Dashboard view`에서 네 view를 전환할 수 있다.

## Dashboard views

- `PRE Cluster`: `outputs/pre/**/user_interests.npz`를 읽어 cutoff 이전 batch cluster snapshot을 시각화한다.
- `PRE Seed State`: `outputs/pre/**/pre_summary.json`과 `state.sqlite`를 읽어 replay 시작 전 user/interest seed 상태를 요약한다.
- `POST Replay`: `outputs/post/**/replay_summary.json`과 `replay.sqlite`를 읽어 post-T event 처리 timeline, stage latency, assignment/refit/recommendation을 표시한다.
- `POST Interest State`: post replay의 `replay.sqlite`에서 최종 touched-user interest state와 interest vectors를 읽고, vector를 dashboard 안에서 SVD projection으로 시각화한다.

## PRE Cluster

실제 cluster 결과 파일이 아직 없으면 앱에서 `Use demo data`를 켜서 synthetic 예시 데이터로 UI를 먼저 확인할 수 있다.

대시보드 입력 파일 생성 예시:

```bash
python3 -m model.batch.export_clusters \
  --input outputs/user_interests.npz \
  --output data/clustering/user_clusters.parquet
```

### 지원 포맷

- `.csv`
- `.parquet`
- `.jsonl`
- `.ndjson`

### 기대 입력 스키마

필수 컬럼:

- `userId`: 사용자 식별자
- `clusterLabel`: 클러스터 ID. `-1`은 noise로 간주
- `x`: 차원 축소 결과 1축
- `y`: 차원 축소 결과 2축

선택 컬럼:

- `z`: 3차원 시각화용 3축
- `timepoint`: 유저 시청 이력 내 글로벌 시점 인덱스
- `clusterProbability`: HDBSCAN soft membership 등 신뢰도
- `outlierScore`: 이상치 점수
- `sequenceLength`: 사용자 시퀀스 길이
- `embeddingNorm`: 임베딩 norm
- 그 외 추가 메타데이터 컬럼

예시 CSV:

```csv
userId,clusterLabel,x,y,z,timepoint,clusterProbability,outlierScore,sequenceLength,embeddingNorm
10,3,-4.12,2.07,0.53,120,0.94,0.03,87,11.8
11,-1,8.51,-6.24,-1.22,340,0.18,0.91,6,9.4
12,1,-1.92,4.65,1.03,80,0.88,0.10,42,10.7
```

## 기본 경로

앱은 `outputs/pre/**/user_interests.npz`를 자동 탐색해 sidebar selectbox에 최신순으로 표시한다. Pre cluster 산출물이 없으면 `data/clustering/user_clusters.parquet` 직접 입력과 demo 데이터를 사용할 수 있다.

PRE Cluster는 선택적으로 replay recommendation JSONL도 읽을 수 있다.

- 기본 경로: 최신 `outputs/post/**/stream_recommendations.jsonl`
- 파일이 있으면 sidebar의 `Load replay recommendations`가 기본으로 켜진다.
- 추천 레코드에 `movieId` 또는 `recommendedMovieId`가 있으면 영화 메타데이터와 함께 recommendation panel에 표시한다.

## PRE Seed State

Stable entrypoint:

- `outputs/pre/**/pre_summary.json`
- `outputs/pre/**/state.sqlite`

앱은 `pre_summary.json`을 자동 탐색해 sidebar selectbox에 최신순으로 표시한다. Seed state view는 summary metric과 SQLite aggregate를 우선 읽고, 특정 user ID를 입력했을 때만 compressed user payload를 decode해 event sample을 보여준다.

## POST Replay

POST Replay는 post-T trace replay가 생성한 artifact를 읽기만 하는 reader다. Replay pipeline을 실행하거나 `outputs/post/` 아래 파일을 생성/수정/삭제하지 않는다.

Stable entrypoint:

- `outputs/post/**/replay_summary.json`
- 진행 중 run은 아직 summary가 없을 수 있으므로 sibling `outputs/post/**/replay.sqlite`도 자동 탐색한다.

앱은 `outputs/post/**/replay_summary.json`과 summary가 아직 없는 `outputs/post/**/replay.sqlite`를 자동 탐색해 sidebar selectbox에 최신순으로 표시한다. `replay_summary.json`에 `paths`가 있으면 해당 경로를 우선 사용한다. summary 없이 SQLite runtime store만 있으면 `runs`와 runtime table count에서 표시용 summary를 만든다. SQLite runtime store가 있으면 그 DB를 우선 읽고, 없으면 선택한 post run root의 아래 JSONL 파일들을 fallback으로 사용한다.

- `replay.sqlite`
- `replay_events.jsonl`
- `ingress_events.jsonl`
- `interest_assignments.jsonl`
- `refit_requests.jsonl`
- `refit_events.jsonl`
- `interest_states/{user_id}.json`
- `stream_recommendations.jsonl`

POST Replay에서 표시하는 내용:

- run status, processed/input events, unique users, elapsed, target/actual throughput, speed
- trace replay event timeline, scheduled/emitted/processed time, injector/processing/end-to-end latency, active embedding rows, assignment status counts
- assignment status counts, open refit requests, closed/skipped refit events
- SQLite runtime store가 있으면 stage attempts, refit lifecycle, assignment/recommendation metadata를 DB에서 우선 표시
- user별 interest count, pending/processed event count, refit trigger state
- replay recommendation row 수, unique user/movie 수, top score 추천 테이블

## POST Interest State

Stable input:

- `outputs/post/**/replay_summary.json`
- 선택된 replay artifact의 `replay.sqlite`; 완료/실패 run은 보통 summary의 `paths.replayDb`, 진행 중 run은 sibling `outputs/post/<run>/replay.sqlite`

POST Interest State는 replay 이후 touched user의 final `interest_states`, `interest_vectors`, `refit_attempts`, assignment status count, recommendation rows를 SQLite에서 직접 읽는다. 같은 summary에 pre seed DB metadata가 있으면 pre interest count와 post interest count를 같은 table에서 비교한다.

파일 계약의 정본은 `docs/streaming-replay-dashboard-contract.md`다.
