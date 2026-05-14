# Recommendation Dashboard

사용자 상태 임베딩 클러스터링 결과, streaming replay 진행 상황, replay-scoped 추천 결과를 탐색하기 위한 Streamlit 대시보드.

## 실행

```bash
streamlit run dashboard/cluster_dashboard.py
```

기본 replay artifact가 있으면 `Replay monitor`가 먼저 열리고, 없으면 기존 `Cluster explorer`가 먼저 열린다. 사이드바의 `Dashboard view`에서 두 view를 전환할 수 있다.

## Cluster explorer

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

앱 기본값은 `data/clustering/user_clusters.parquet`를 먼저 찾는다. 파일이 없으면 demo 데이터를 사용할 수 있다.

Cluster explorer는 선택적으로 replay recommendation JSONL도 읽을 수 있다.

- 기본 경로: `outputs/stream/replay_demo/stream_recommendations.jsonl`
- 파일이 있으면 sidebar의 `Load replay recommendations`가 기본으로 켜진다.
- 추천 레코드에 `movieId` 또는 `recommendedMovieId`가 있으면 영화 메타데이터와 함께 recommendation panel에 표시한다.

## Replay monitor

Phase 6 replay monitor는 Phase 5가 생성한 replay artifact를 읽기만 하는 reader다. Replay pipeline을 실행하거나 `outputs/stream/replay_demo/` 아래 파일을 생성/수정/삭제하지 않는다.

Stable entrypoint:

- `outputs/stream/replay_demo/replay_summary.json`

`replay_summary.json`에 `paths`가 있으면 해당 경로를 우선 사용하고, 없으면 아래 기본 경로를 fallback으로 사용한다.

- `outputs/stream/replay_demo/replay_events.jsonl`
- `outputs/stream/replay_demo/interest_assignments.jsonl`
- `outputs/stream/replay_demo/refit_requests.jsonl`
- `outputs/stream/replay_demo/refit_events.jsonl`
- `outputs/stream/replay_demo/interest_states/{user_id}.json`
- `outputs/stream/replay_demo/stream_recommendations.jsonl`

Replay monitor에서 표시하는 내용:

- run status, processed/input events, unique users, elapsed, throughput
- replay event timeline, latency, active embedding rows, assignment status counts
- assignment status counts, open refit requests, closed/skipped refit events
- user별 interest count, pending/processed event count, refit trigger state
- replay recommendation row 수, unique user/movie 수, top score 추천 테이블

파일 계약의 정본은 `docs/streaming-replay-dashboard-contract.md`다.
