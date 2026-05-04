# Streaming Pipeline Phase 2: Canonical Event Embedding 전환

## 목적

기존 overlap-window 기반 embedding 추출과 별도로, rating event 하나가 embedding 하나만 갖는 canonical event embedding 추출 경로를 추가한다.

Phase 2의 핵심 목표는 split/evaluation 정책을 확정하는 것이 아니라, batch replay와 online inference가 공유할 수 있는 embedding 계약을 먼저 세우는 것이다.

## 배경

현재 `model/batch/extract.py`는 sliding window를 먼저 만들고, 각 window 안에서 `interval`마다 hidden state를 추출한다. 이 방식에서는 같은 user event가 여러 window에 포함될 수 있다.

예:

```text
기존:
  window를 먼저 만들고 window 안에서 여러 시점을 뽑음
  -> 같은 event가 서로 다른 context/position으로 여러 embedding을 가질 수 있음

수정:
  event를 먼저 고르고 event를 마지막 position으로 하는 window 하나를 만듦
  -> 같은 event는 embedding 하나만 가짐
```

Streaming pipeline에서는 새 rating event가 들어올 때 hidden state 하나를 만들어 append해야 한다. 따라서 batch extract와 online extract가 같은 의미의 embedding을 생성해야 한다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/streaming_pipeline.md`
- `plan/done/streaming_pipeline_phase1_structure.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `model/batch/extract.py`
- `model/common/dataset.py`
- `model/common/sasrec.py`
- `schemas/ml32m/processed/ratings_drop_processed.v1.schema.yaml`

## 수정 범위

- 수정:
  - `model/common/canonical.py`
  - `model/batch/extract_canonical.py`
  - `model/README.md`
  - `model/IMPLEMENTATION_STATUS.md`
  - `docs/data-flow.md`
  - `README.md`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL 직접 편집
  - 기존 `outputs/embeddings.npz`, `outputs/user_interests.npz` 덮어쓰기
  - 기존 legacy `model/batch/extract.py` 동작 변경

## Canonical Embedding 계약

```text
canonical event embedding h_t =
  user의 t번째 positive rating event 직후,
  user의 최근 seq_len개 positive history를 SASRec에 넣고,
  마지막 position의 hidden state를 뽑은 값
```

Phase 2에서는 split을 적용하지 않는다. user별 positive sequence 전체를 대상으로 canonical embedding을 만든다. Chronological replay split은 Phase 5 C++ replay engine에서 timestamp 기준으로 적용한다.

### Early History와 Downstream 사용 조건

Canonical embedding 생성 자체는 일정량 이상의 history가 쌓여야만 가능한 작업이 아니다. user의 첫 positive event도 해당 event 하나를 context로 삼아 hidden state를 만들 수 있다.

```text
event_idx=0  -> history_len=1
event_idx=1  -> history_len=2
...
event_idx=99 -> history_len=100
event_idx>=100 -> history_len=seq_len, 최근 seq_len개 event만 사용
```

다만 embedding을 만들 수 있다는 것과, 그 embedding을 clustering/refit에 의미 있게 사용할 수 있다는 것은 다르다.

- canonical embedding: event 1개부터 생성 가능
- interest assignment: 기존 `u_k`가 있으면 새 event embedding을 assign 가능
- cluster/refit: user별 event embedding이 충분히 쌓인 뒤 수행해야 안정적

따라서 Phase 2 산출물은 early event embedding도 저장하되, `history_len`을 함께 저장한다. 이후 Phase 4의 interest assign/refit에서는 `history_len`, user별 event count, refit trigger 기준을 사용해 early event를 제외하거나 낮은 신뢰도로 다룰 수 있게 한다.

초기 해석 기준:

```text
history_len < refit_min_events:
  embedding은 저장하되 cluster/refit 대상에서는 제외 가능

history_len >= refit_min_events:
  assignment/refit 후보

history_len == seq_len:
  full-context canonical embedding
```

## 결정 사항

- `model/batch/extract.py`는 legacy overlap extract로 유지한다.
- 새 entrypoint `model/batch/extract_canonical.py`를 추가한다.
- 공통 canonical window 로직은 `model/common/canonical.py`에 둔다.
- canonical 대상은 split 없는 전체 positive sequence다.
- `event_idx`는 user별 positive sequence 기준 0-based index다.
- `rated_at_ts`와 `rated_at_iso`를 둘 다 저장한다.
- 출력 기본 경로는 `outputs/canonical_embeddings.npz`다.
- smoke test를 위해 `--limit-users` 옵션을 추가한다.

## Item Vocabulary 판단 기록

`item2idx`에 없는 event는 1차 정책으로 skip한다.

이때 "없는 movieId"는 원본 데이터에 없는 영화라는 뜻이 아니다. 현재 checkpoint의 item embedding table이 모르는 movieId라는 뜻이다.

현재 `item2idx.json`은 학습 시점의 필터링된 positive sequence에서 만들어진 vocabulary다. Phase 2 canonical extract는 전체 positive sequence를 대상으로 하므로, 어떤 movieId는 원본 데이터와 영화 metadata에는 존재하지만 현재 checkpoint의 embedding table에는 없을 수 있다.

1차 skip 정책을 택하는 이유:

- 기존 checkpoint를 그대로 사용한다.
- unknown item index를 새로 추가하면 checkpoint embedding table과 shape가 맞지 않는다.
- item2idx를 다시 만들면 모델 재학습이 필요하다.
- Phase 2의 목적은 vocabulary 재설계가 아니라 canonical embedding 계약 검증이다.

대안:

- unknown item index를 도입한다.
- item2idx를 전체 catalog 기준으로 다시 만들고 모델을 재학습한다.
- canonical extract 대상 범위를 train-time vocabulary에 맞춘다.

검증 metric으로 아래 값을 남긴다.

```text
total_positive_events
kept_events
skipped_unknown_items
skipped_unknown_item_users
```

이 skip 비율이 크면 Phase 3 이후에 vocabulary 정책을 다시 검토한다.

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경:
  - 새 산출물 `outputs/canonical_embeddings.npz` 추가
  - 기존 `outputs/embeddings.npz`는 legacy overlap-window 산출물로 유지
- 호환성 영향:
  - 기존 `python3 -m model.batch.extract` 동작은 바꾸지 않는다.
  - 새 canonical extract는 `python3 -m model.batch.extract_canonical`로 실행한다.

예상 저장 키:

```text
embeddings          float32 (N, d_model)
user_ids            int64   (N,)
event_idx           int64   (N,)
movie_ids           int64   (N,)
rated_at_ts         float64 (N,)
rated_at_iso        str     (N,)
history_len         int64   (N,)
context_start_idx   int64   (N,)
```

## 실행 계획

1. [x] `model/common/canonical.py`를 추가한다.
   - canonical window 생성 함수
   - canonical event dataset 또는 sample builder
   - uniqueness/stat 검증 helper
2. [x] `model/batch/extract_canonical.py`를 추가한다.
   - checkpoint, `item2idx.json`, movie genre metadata 로드
   - `ratings_drop_processed.jsonl`에서 positive sequence 생성
   - user별 event를 canonical window로 변환
   - 마지막 hidden state를 embedding으로 저장
   - `--limit-users`, `--output`, `--batch-size`, `--num-workers` 옵션 제공
3. [x] `item2idx` 밖 event skip count를 summary metric에 기록한다.
4. [x] `outputs/canonical_embeddings.npz` 저장 후 uniqueness 검증을 수행한다.
5. [x] run metadata에 canonical extract stage를 기록한다.
6. [x] 문서를 갱신한다.
   - `model/README.md`
   - `model/IMPLEMENTATION_STATUS.md`
   - `docs/data-flow.md`
   - `README.md`
   - `PROJECT_GUIDE.md`
7. [x] `todo.md` Phase 2 상태를 갱신한다.

## 구현 결과

- 추가 파일:
  - `model/common/canonical.py`
  - `model/batch/extract_canonical.py`
- 추가 entrypoint:
  - `python3 -m model.batch.extract_canonical`
- 기본 출력:
  - `outputs/canonical_embeddings.npz`
- smoke 출력:
  - `outputs/test_canonical_embeddings.npz`
- 실험 기록:
  - `experiments/model/phase2_canonical_smoke/`

구현 중 확인된 사항:

- 초기에는 left-padding window에서 early history row 일부가 NaN이 되는 문제가 있었다.
- canonical dataset은 right-padding으로 구성하고, extractor는 `SASRecCL.get_last_hidden()`을 사용해 마지막 non-padding hidden state를 선택하도록 수정했다.
- 이로써 첫 event처럼 `history_len < seq_len`인 early embedding도 생성 가능하다.
- 다만 clustering/refit은 여전히 downstream에서 `history_len`, user별 event count, refit threshold를 보고 사용할지 결정해야 한다.

## 검증

- [x] help가 실행된다.

```bash
python3 -m model.batch.extract_canonical --help
```

- [x] 작은 smoke test가 실행된다.

```bash
python3 -m model.batch.extract_canonical \
  --limit-users 2 \
  --batch-size 32 \
  --num-workers 0 \
  --output outputs/test_canonical_embeddings.npz
```

- [x] 산출물 shape가 일관된다.

```text
embeddings.shape[0] == user_ids.shape[0]
embeddings.shape[0] == event_idx.shape[0]
```

- [x] uniqueness 검증을 통과한다.

```text
row_count == unique(user_id, event_idx)
```

- [x] no NaN embedding 검증을 통과한다.
- [x] `history_len <= seq_len`이다.
- [x] `context_start_idx <= event_idx`이다.
- [x] `skipped_unknown_items`가 metric으로 기록된다.

검증 기록:

```bash
.venv/bin/python -m model.batch.extract_canonical --help

.venv/bin/python -m model.batch.extract_canonical \
  --run-id phase2_canonical_smoke \
  --limit-users 2 \
  --batch-size 32 \
  --num-workers 0 \
  --output outputs/test_canonical_embeddings.npz
```

Smoke test summary:

```text
output: outputs/test_canonical_embeddings.npz
embeddings: (2869, 128), float32
unique (user_id, event_idx): 2869 / 2869
nan_rows: 0
max_history_len: 100
invalid_context_rows: 0
skipped_unknown_items: 0
skipped_unknown_item_users: 0
```

## 완료 조건

- 기존 legacy `model/batch/extract.py`를 보존한 채 canonical extract entrypoint가 추가된다.
- canonical embedding 산출물은 event 하나당 row 하나를 보장한다.
- `item2idx` 밖 event skip 정책과 대안이 문서화되어 있다.
- Phase 3 online embedding이 같은 canonical window 로직을 재사용할 수 있다.
