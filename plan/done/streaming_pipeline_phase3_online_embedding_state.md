# Streaming Pipeline Phase 3: Online Embedding / User Interest State

## 목적

새 rating event가 계속 들어오는 상황에서 user별 raw rating history를 보존하고, 현재까지 관측된 history 기준의 positive projection 위에서 canonical event embedding을 생성한다.

Phase 3의 핵심 목표는 interest assignment나 refit이 아니라, batch canonical embedding과 같은 의미의 online embedding을 만들고 Phase 4가 소비할 active positive embedding/state 계약을 확정하는 것이다.

## 배경

Phase 2에서는 batch 경로에서 event 하나당 canonical embedding 하나를 생성하는 계약을 만들었다.

```text
canonical event embedding h_t =
  user의 t번째 positive rating event 직후,
  user의 최근 seq_len개 positive history를 SASRec에 넣고,
  마지막 position의 hidden state를 뽑은 값
```

Streaming 환경에서는 raw rating event를 처음부터 버리면 나중에 positive 여부를 다시 판단할 수 없다. 따라서 Phase 3에서는 raw event log와 model-positive sequence를 분리한다.

```text
raw_events:
  들어온 모든 rating event

positive_events:
  현재까지 관측된 user history 기준으로 positive라고 판단된 event

active embeddings:
  positive_events 중 checkpoint vocabulary에 있는 item으로 생성된 canonical embeddings
```

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/streaming_pipeline.md`
- `plan/done/streaming_pipeline_phase2_canonical_embedding.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `model/common/canonical.py`
- `model/common/sasrec.py`
- `model/stream/extract_online.py`
- `schemas/ml32m/processed/ratings_drop_processed.v1.schema.yaml`

## 수정 범위

- 수정:
  - `model/stream/state.py`
  - `model/stream/extract_online.py`
  - `model/README.md`
  - `model/IMPLEMENTATION_STATUS.md`
  - `docs/data-flow.md`
  - `docs/artifacts.md`
  - `outputs/readme.md`
  - `README.md`
  - `PROJECT_GUIDE.md`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - 기존 `outputs/embeddings.npz`, `outputs/canonical_embeddings.npz`, `outputs/user_interests.npz` 덮어쓰기
  - Phase 4 범위인 interest assignment/refit trigger 구현

## 결정 사항

- raw rating event는 모두 user state에 저장한다.
- 초기 history가 부족한 user는 optimistic policy로 들어온 event를 positive로 간주한다.
- `ratings 수 < min_ratings_for_zscore`이면 모든 raw event를 positive로 둔다.
- `ratings 수 >= min_ratings_for_zscore`이고 rating 표준편차가 0이면 모든 raw event를 positive로 둔다.
- 그 외에는 현재까지 관측된 user ratings 기준 `z > 0`을 positive로 둔다.
- Phase 3 v1은 user별 positive embedding을 전체 재계산한다.
- late event는 허용한다. raw history를 `ratedAt`, `movieId`, `rawEventId` 기준으로 정렬하고 해당 user state/embedding을 전체 재계산한다.
- `item2idx` 밖 movie는 raw state에는 보존하지만 embedding은 생성하지 않고 `skipped_unknown`으로 기록한다.
- Phase 4는 `status == active`인 positive embedding만 소비한다.

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경:
  - `outputs/stream/user_states/{user_id}.json`
  - `outputs/stream/online_embeddings.npz`
  - `outputs/stream/online_embedding_events.jsonl`
- 호환성 영향:
  - 기존 batch entrypoint와 기존 산출물은 변경하지 않는다.
  - stream 산출물은 Phase 4 input 후보이며 기존 dashboard 입력과 직접 호환되지 않는다.

## State 계약

`outputs/stream/user_states/{user_id}.json`은 raw history와 derived positive projection을 함께 저장한다.

```json
{
  "version": "online_user_state.v1",
  "userId": 28,
  "seqLen": 100,
  "nextRawEventId": 120,
  "positivePolicy": {
    "name": "observed_user_zscore_v1",
    "minRatingsForZscore": 3,
    "zThreshold": 0.0,
    "optimisticColdStart": true
  },
  "rawEvents": [],
  "positiveEvents": [],
  "stats": {},
  "updatedAt": "2026-05-05T00:00:00+09:00"
}
```

`rawEventId`는 user별 stable id이고, `eventIdx`는 positive projection 기준 derived index다. 재검증 후 `eventIdx`는 바뀔 수 있으므로 외부 식별자는 `rawEventId`를 우선 사용한다.

## Online Embedding 계약

`outputs/stream/online_embeddings.npz`는 active embedding row만 저장한다.

```text
embeddings          float32 (N, d_model)
user_ids            int64   (N,)
raw_event_ids       int64   (N,)
event_idx           int64   (N,)
movie_ids           int64   (N,)
rated_at_ts         float64 (N,)
rated_at_iso        str     (N,)
history_len         int64   (N,)
context_start_idx   int64   (N,)
status              str     (N,)  # active
```

## 실행 계획

1. [x] `model/stream/state.py`를 추가한다.
   - raw event/state dataclass
   - state JSON 저장/로드
   - positive projection 재검증
   - stable `rawEventId`와 derived `eventIdx` 관리
2. [x] `model/stream/extract_online.py`를 구현한다.
   - checkpoint, `item2idx.json`, movie genre metadata 로드
   - bootstrap user state 생성
   - 새 event 또는 event JSONL ingest
   - positive projection 기반 canonical embedding 전체 재계산
   - state JSON, online embedding NPZ, event log 저장
3. [x] optional batch canonical 비교 기능을 추가한다.
4. [x] 문서를 갱신한다.
5. [x] smoke test를 실행한다.
6. [x] `todo.md` Phase 3 상태를 갱신한다.

## 구현 결과

- 추가 파일:
  - `model/stream/state.py`
- 구현 entrypoint:
  - `python3 -m model.stream.extract_online`
- 기본 출력:
  - `outputs/stream/user_states/{user_id}.json`
  - `outputs/stream/online_embeddings.npz`
  - `outputs/stream/online_embedding_events.jsonl`
- smoke 출력:
  - `outputs/stream/test_user_states/28.json`
  - `outputs/stream/test_online_embeddings.npz`
  - `outputs/stream/test_online_embedding_events.jsonl`
  - `outputs/stream/test_canonical_user28.npz`
- 실험 기록:
  - `experiments/model/phase3_online_smoke/`

구현 중 확인된 사항:

- 로컬에 `outputs/item2idx.json`이 없어 첫 실제 checkpoint smoke가 실패했다.
- 기존 train 기본 정책(`min_interactions=200`, `min_activity_days=30`, user별 `z > 0`)으로 `outputs/item2idx.json`을 재생성했다.
- 재생성된 vocabulary 크기는 checkpoint item embedding 크기와 같은 55,726개다.
- `python3 -m model.batch.extract_canonical --limit-users 1`의 첫 대상 user가 28이므로, Phase 3 online bootstrap user 28과 직접 비교했다.

## 검증

- [x] `python3 -m model.stream.extract_online --help`가 실행된다.
- [x] bootstrap user smoke test가 실행된다.
- [x] online embedding shape와 metadata 길이가 일치한다.
- [x] `history_len <= seq_len`이다.
- [x] `context_start_idx <= event_idx`이다.
- [x] unknown item은 raw state에 남고 embedding 생성에서는 skip된다.
- [x] batch canonical 비교 입력이 있을 때 matching row가 `np.allclose`로 검증된다.

검증 기록:

```bash
.venv/bin/python -m py_compile model/stream/state.py model/stream/extract_online.py

.venv/bin/python -m model.stream.extract_online --help

.venv/bin/python -m model.stream.extract_online \
  --run-id phase3_online_smoke \
  --bootstrap-user-id 28 \
  --output outputs/stream/test_online_embeddings.npz \
  --state-dir outputs/stream/test_user_states \
  --event-log outputs/stream/test_online_embedding_events.jsonl \
  --batch-size 32

.venv/bin/python -m model.batch.extract_canonical \
  --run-id phase3_online_smoke \
  --limit-users 1 \
  --batch-size 32 \
  --num-workers 0 \
  --output outputs/stream/test_canonical_user28.npz

.venv/bin/python -m model.stream.extract_online \
  --run-id phase3_online_smoke \
  --bootstrap-user-id 28 \
  --output outputs/stream/test_online_embeddings.npz \
  --state-dir outputs/stream/test_user_states \
  --event-log outputs/stream/test_online_embedding_events.jsonl \
  --batch-size 32 \
  --compare-canonical outputs/stream/test_canonical_user28.npz

.venv/bin/python -c 'from model.stream.state import PositivePolicy, build_state_from_rating_history; item2idx={1:1}; ratings=[{"movieId":1,"rating":5.0,"ratedAt":"2020-01-01T00:00:00Z"},{"movieId":99,"rating":4.0,"ratedAt":"2020-01-02T00:00:00Z"}]; s=build_state_from_rating_history(user_id=1, ratings=ratings, seq_len=5, item2idx=item2idx, positive_policy=PositivePolicy()); print(s.stats); print([event.status for event in s.positive_events])'
```

Smoke test summary:

```text
userId: 28
rawEventCount: 2835
positiveEventCount: 1579
activeEventCount: 1579
droppedEventCount: 1256
skippedUnknownItems: 0
online embeddings: (1579, 128), float32
nan_embedding_rows: 0
max_history_len: 100
invalid_context_rows: 0
status values: active
canonical matched rows: 1579
canonical allclose rows: 1579
canonical max_abs_diff: 0.0
unknown item unit check: active 1, skipped_unknown 1
```

## 완료 조건

- raw event state와 active positive embedding 계약이 문서화된다.
- `model.stream.extract_online`이 Phase 2 canonical window 의미와 같은 online embedding을 생성한다.
- Phase 4가 active positive embedding만 소비할 수 있는 출력 계약이 준비된다.
