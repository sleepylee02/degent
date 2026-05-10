# Streaming Pipeline Phase 4: Interest Assign / Refit Trigger

## 목적

Phase 3가 생성한 active positive online embedding을 user별 interest state에 연결한다.

Phase 4의 목표는 refit을 직접 실행하는 것이 아니라, interest state가 있는 user는 가장 가까운 interest에 assign하고, interest state가 없거나 outlier/event count가 누적된 user는 refit request를 기록하는 것이다.

## 배경

Phase 3 output은 raw rating 전체가 아니라 현재까지 관측된 positive projection 위에서 생성한 active canonical embeddings다.

```text
outputs/stream/online_embeddings.npz
  -> active positive event embeddings
```

Phase 4는 이 embedding을 downstream interest layer에 연결한다. 다만 현재 `outputs/user_interests.npz`는 legacy/test 성격이 강하므로, 이를 무조건 신뢰해 seed로 사용하지 않는다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/streaming_pipeline.md`
- `plan/done/streaming_pipeline_phase3_online_embedding_state.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `model/stream/extract_online.py`
- `model/stream/interest_assign.py`

## 수정 범위

- 수정:
  - `model/stream/interest_assign.py`
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
  - UMAP/HDBSCAN refit 구현
  - 추천 scoring/evaluation 구현

## 결정 사항

- Phase 4는 `status == active`인 online embedding만 소비한다.
- interest state가 없는 user는 assign하지 않고 pending buffer에 쌓는다.
- pending active event 수가 `refit_min_events` 이상이면 refit request를 기록한다.
- interest state가 있는 user는 cosine similarity로 가장 가까운 interest에 assign한다.
- `max_similarity < similarity_threshold`이면 outlier로 기록하고 refit 후보로 누적한다.
- 1차 refit trigger는 event count 기반으로 둔다.
- Phase 4는 refit request만 남기고 실제 refit은 Phase 4-1 이후로 넘긴다.

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경:
  - `outputs/stream/interest_states/{user_id}.json`
  - `outputs/stream/interest_assignments.jsonl`
  - `outputs/stream/refit_requests.jsonl`
- 호환성 영향:
  - Phase 3 output을 입력으로 사용한다.
  - 기존 legacy `outputs/user_interests.npz`는 자동 seed로 사용하지 않는다.

## Interest State 계약

`outputs/stream/interest_states/{user_id}.json`은 user별 interest assignment/refit trigger state를 저장한다.

```json
{
  "version": "online_interest_state.v1",
  "userId": 28,
  "embeddingDim": 128,
  "interests": [
    {
      "interestId": 0,
      "vector": [0.0],
      "assignedCount": 0,
      "createdAt": "2026-05-06T00:00:00+09:00",
      "updatedAt": "2026-05-06T00:00:00+09:00"
    }
  ],
  "pendingRawEventIds": [],
  "processedRawEventIds": [],
  "assignedSinceLastRefit": 0,
  "outlierSinceLastRefit": 0,
  "refitRequired": false,
  "refitReasons": [],
  "updatedAt": "2026-05-06T00:00:00+09:00"
}
```

## 실행 계획

1. [x] `model/stream/interest_assign.py`를 구현한다.
   - interest state JSON 저장/로드
   - online embedding NPZ 로드
   - `status == active` row 필터링
   - no-interest pending 처리
   - cosine nearest-interest assignment
   - outlier 처리
   - event-count 기반 refit trigger
2. [x] assignment/refit request JSONL을 저장한다.
3. [x] run metadata와 metric을 기록한다.
4. [x] 문서를 갱신한다.
5. [x] smoke test를 실행한다.
6. [x] `todo.md` Phase 4 상태를 갱신한다.

## 구현 결과

- 구현 entrypoint:
  - `python3 -m model.stream.interest_assign`
- 기본 출력:
  - `outputs/stream/interest_states/{user_id}.json`
  - `outputs/stream/interest_assignments.jsonl`
  - `outputs/stream/refit_requests.jsonl`
- smoke 출력:
  - `outputs/stream/test_interest_states_no_interest/28.json`
  - `outputs/stream/test_interest_assignments_no_interest.jsonl`
  - `outputs/stream/test_refit_requests_no_interest.jsonl`
  - `outputs/stream/test_interest_states_seeded/28.json`
  - `outputs/stream/test_interest_assignments_seeded.jsonl`
  - `outputs/stream/test_online_embedding_outlier.npz`
  - `outputs/stream/test_interest_states_outlier/1.json`
  - `outputs/stream/test_interest_assignments_outlier.jsonl`
  - `outputs/stream/test_refit_requests_outlier.jsonl`
- 실험 기록:
  - `experiments/model/phase4_interest_assign_smoke/`

구현 중 확인된 사항:

- interest state가 없으면 assignment를 하지 않고 pending으로만 기록한다.
- pending count가 `refit_min_events`를 넘으면 `no_interest_pending_events` reason으로 open refit request를 기록한다.
- interest state가 있으면 cosine similarity로 nearest interest를 고른다.
- `similarity_threshold`를 넘지 못하면 outlier로 기록하고 pending/refit 후보로 누적한다.
- 이미 assigned 처리된 `rawEventId`는 `processedRawEventIds`로 보존해 같은 state에서 중복 assign하지 않는다.

## 검증

- [x] `python3 -m model.stream.interest_assign --help`가 실행된다.
- [x] interest state가 없는 user는 pending으로 기록된다.
- [x] pending count가 threshold 이상이면 refit request가 기록된다.
- [x] seed interest state가 있으면 cosine assignment가 수행된다.
- [x] low-similarity row는 outlier로 기록된다.
- [x] `interest_assignments.jsonl`, `refit_requests.jsonl`, `interest_states/{user_id}.json`이 생성된다.

검증 기록:

```bash
.venv/bin/python -m py_compile model/stream/interest_assign.py

.venv/bin/python -m model.stream.interest_assign --help

.venv/bin/python -m model.stream.interest_assign \
  --run-id phase4_interest_assign_smoke \
  --embeddings outputs/stream/test_online_embeddings.npz \
  --interest-state-dir outputs/stream/test_interest_states_no_interest \
  --assignments outputs/stream/test_interest_assignments_no_interest.jsonl \
  --refit-requests outputs/stream/test_refit_requests_no_interest.jsonl \
  --refit-min-events 20

.venv/bin/python -m model.stream.interest_assign \
  --run-id phase4_interest_assign_smoke \
  --embeddings outputs/stream/test_online_embeddings.npz \
  --interest-state-dir outputs/stream/test_interest_states_seeded \
  --assignments outputs/stream/test_interest_assignments_seeded.jsonl \
  --refit-requests outputs/stream/test_refit_requests_seeded.jsonl \
  --similarity-threshold -1.0 \
  --assign-trigger-count 2000 \
  --outlier-trigger-count 2000 \
  --refit-min-events 2000

.venv/bin/python -m model.stream.interest_assign \
  --run-id phase4_interest_assign_smoke \
  --embeddings outputs/stream/test_online_embedding_outlier.npz \
  --interest-state-dir outputs/stream/test_interest_states_outlier \
  --assignments outputs/stream/test_interest_assignments_outlier.jsonl \
  --refit-requests outputs/stream/test_refit_requests_outlier.jsonl \
  --similarity-threshold 0.2 \
  --outlier-trigger-count 1
```

Smoke test summary:

```text
no-interest user 28:
  inputRows: 1579
  pendingAfter: 1579
  refitRequired: true
  refitReasons: no_interest_pending_events
  status counts: pending_no_interest 19, pending_refit_required 1560
  refit requests: 1

seeded-interest user 28:
  inputRows: 1579
  interestCount: 1
  processedAfter: 1579
  status counts: assigned 1579
  refitRequired: false

outlier fixture user 1:
  inputRows: 1
  status counts: outlier 1
  refitRequired: true
  refitReasons: outlier_since_last_refit
```

## 완료 조건

- Phase 3 active online embedding을 Phase 4 assignment/refit trigger 입력으로 소비할 수 있다.
- interest state가 없는 user를 안전하게 pending/refit request로 처리한다.
- 실제 clustering/refit 구현 없이 Phase 4-1이 소비할 refit request 계약이 준비된다.
