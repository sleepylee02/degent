# Temporal 2022 Streaming E2E

## 목적

`T = 2022-01-01T00:00:00Z`를 기준으로 T 이전 데이터로 offline 준비를 끝내고, T 이후 rating event를 streaming replay로 흘려 end-to-end 부하와 artifact 계약을 검증한다.

목표 흐름:

```text
pre-T:
  train model
  build item2idx
  extract canonical embeddings
  build user state
  build interest state

post-T:
  generate replay events
  replay/stream events
  update user state
  assign/refit interest state
  measure replay load
```

## 배경

현재 replay generator와 `model.stream.replay_pipeline`은 `--start-rated-at` / `--end-rated-at` 기반 post-T event 생성을 지원한다. 그러나 batch train과 canonical extract는 아직 cutoff를 받지 않아 전체 데이터를 학습/추출에 사용한다.

또한 streaming replay는 output root 내부 state를 기준으로 동작하므로, T 시점에 이미 존재해야 하는 `user_states/`와 `interest_states/`를 seed하지 않으면 2022년 이후 기존 user도 cold-start처럼 처리된다. 이번 작업은 모델뿐 아니라 pre-T user/interest state까지 포함한 temporal streaming E2E 계약을 만든다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `schemas/README.md`
- `preprocess/README.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `docs/current-pipeline-snapshot.md`
- `docs/streaming-e2e-pipeline.md`
- `docs/streaming-replay-dashboard-contract.md`
- `docs/artifacts.md`
- `model/common/dataset.py`
- `model/batch/train.py`
- `model/common/canonical.py`
- `model/batch/extract_canonical.py`
- `model/batch/cluster.py`
- `model/stream/state.py`
- `model/stream/extract_online.py`
- `model/stream/trace_replay.py`
- `replay/cpp/rating_replay.cpp`

## 수정 범위

- 수정:
  - 모델 산출물 경로 계약
    - 신규 temporal run의 모델 관련 산출물은 `outputs/pre/temporal_2022/` 아래에 둔다.
    - 기존 루트 산출물(`outputs/sasrec_cl.pt`, `outputs/item2idx.json`, `outputs/canonical_embeddings.npz` 등)은 legacy/default 호환 경로로 유지하고, 이번 작업에서 일괄 이동하지 않는다.
    - replay runtime 산출물은 실행 성격상 `outputs/post/temporal_2022/` 아래에 격리하되, 입력 checkpoint/item2idx/seed state는 `outputs/pre/temporal_2022/`를 참조한다.
  - `model/common/dataset.py`
    - `build_user_sequences`에 `max_rated_at_exclusive` cutoff를 추가한다.
    - cutoff 적용 후 activity span, z-score positive projection, min interactions를 계산한다.
  - `model/batch/train.py`
    - `--max-rated-at-exclusive` 옵션을 추가한다.
    - run-scoped checkpoint/item2idx output 옵션을 추가해 기존 `outputs/sasrec_cl.pt`, `outputs/item2idx.json` 덮어쓰기를 피할 수 있게 한다.
    - manifest `training_config`와 `data_summary`에 cutoff와 pre-T train 범위를 기록한다.
  - `model/common/canonical.py`, `model/batch/extract_canonical.py`
    - canonical extraction에 같은 `--max-rated-at-exclusive` cutoff를 추가한다.
    - checkpoint/item2idx 입력 path 옵션을 명시적으로 받을 수 있게 한다.
    - manifest `extraction_config`에 cutoff를 기록한다.
  - `model/batch/cluster.py`
    - `outputs/user_interests.npz` 고정 저장 대신 `--output` 옵션을 추가한다.
    - pre-T interest state dir와 viz NPZ를 run-scoped 경로에 저장할 수 있게 한다.
  - `model/stream/seed_pre_t_state.py` 신규 추가
    - `ratings_drop_processed.jsonl`에서 `ratedAt < T` rating만 읽어 `user_states/{user_id}.json`을 생성한다.
    - `item2idx` 기준 known/unknown item 상태와 positive projection을 기존 `model.stream.state` 정책으로 계산한다.
    - seed summary를 JSON/metrics로 기록한다.
  - `model/stream/trace_replay.py`
    - `--seed-user-state-dir`, `--seed-interest-state-dir` 옵션을 추가한다.
    - replay 시작 시 seed state를 replay output root로 복사해 replay artifact를 격리한다.
    - summary/manifest에 seed path와 seed count를 기록한다.
  - 문서
    - `docs/streaming-e2e-pipeline.md`: temporal 2022 runbook 추가
    - `docs/current-pipeline-snapshot.md`: cutoff/state seed 계약 반영
    - `docs/artifacts.md`: 신규 seed artifact와 run-scoped artifact 경로 반영
    - `model/README.md`: 신규 CLI 옵션과 실행 순서 반영
    - `PROJECT_GUIDE.md`: 신규 entrypoint/산출물 구조가 생기면 반영
    - `todo.md`: 진행/완료 상태 갱신
- 수정 금지:
  - `data/**/raw/`
  - `data/ratings_drop.csv`
  - `data/ratings_drop_processed.jsonl`
  - 기존 대형 `outputs/` 산출물의 수동 편집

## 데이터/스키마 영향

- 컬럼 변경: 없음.
- 원본/전처리 CSV, JSONL 변경: 없음.
- 신규 생성물:
  - `outputs/pre/temporal_2022/sasrec_cl.pt`
  - `outputs/pre/temporal_2022/sasrec_cl_best.pt`
  - `outputs/pre/temporal_2022/item2idx.json`
  - `outputs/pre/temporal_2022/canonical_embeddings.npz`
  - `outputs/pre/temporal_2022/user_interests.npz`
  - `outputs/pre/temporal_2022/user_states/{user_id}.json`
  - `outputs/pre/temporal_2022/interest_states/{user_id}.json`
  - `outputs/pre/temporal_2022/pre_summary.json`
  - `outputs/post/temporal_2022/`
- 호환성 영향:
  - 기존 CLI 기본값은 유지한다.
  - cutoff/output path 옵션을 명시한 temporal run만 신규 경로를 사용한다.
  - schemas 변경은 없다. 신규 JSON artifact 계약은 `docs/artifacts.md`와 streaming E2E 문서에 우선 명시한다.

## 실행 계획

1. Cutoff helper와 train cutoff를 구현한다.
   - `max_rated_at_exclusive=2022-01-01T00:00:00Z`일 때 train loader가 pre-T rating만 쓰는지 검증한다.
   - train output path를 `outputs/pre/temporal_2022/` 같은 run-scoped model artifact root로 받을 수 있게 한다.
2. Canonical extract와 batch cluster를 run-scoped로 연결한다.
   - pre-T checkpoint/item2idx로 pre-T canonical embeddings를 추출한다.
   - pre-T canonical embeddings로 interest state를 만든다.
3. Pre-T user state seeding entrypoint를 추가한다.
   - T 이전 raw rating history를 replay 시작 상태로 저장한다.
   - state seed summary에 users, raw events, positive events, unknown item events를 기록한다.
4. Replay pipeline에 seed state 복사를 붙인다.
   - replay output root를 reset해도 seed state가 복사된 뒤 post-T event가 처리되게 한다.
   - replay input은 `--start-rated-at 2022-01-01T00:00:00Z`를 사용한다.
5. Temporal 2022 smoke를 실행한다.
   - 작은 limit으로 train/extract/cluster/seed/replay가 한 run id와 artifact root에서 이어지는지 확인한다.
   - `runtime_report`로 replay 부하 summary를 확인한다.
6. 문서와 todo를 업데이트한다.
   - 실행 명령, artifact map, cutoff 계약, 검증 결과를 문서화한다.

## 검증

- [x] `python3 -m py_compile model/common/dataset.py model/common/canonical.py model/batch/train.py model/batch/extract_canonical.py model/batch/cluster.py model/stream/seed_pre_t_state.py model/stream/trace_replay.py`
- [x] CLI help: `model.batch.train`, `model.batch.extract_canonical`, `model.stream.seed_pre_t_state`, `model.stream.replay_pipeline`
- [x] cutoff helper smoke: `2022-01-01T00:00:00Z` → `1640995200.0`
- [x] state seed logic smoke: 실제 `ratings_drop_processed.jsonl` 첫 user로 pre-T raw state 생성 로직 확인
- [x] replay seed copy helper smoke: 임시 JSON seed dir 복사 확인
- [x] interest processed marker smoke: 기존 interest state의 `processedRawEventIds` merge와 pending/refit clear 확인
- [x] `git diff --check`
- [ ] train smoke: `model.batch.train --max-rated-at-exclusive 2022-01-01T00:00:00Z`가 run-scoped checkpoint/item2idx를 생성
- [ ] canonical smoke: pre-T checkpoint/item2idx로 `canonical_embeddings.npz` 생성
- [ ] cluster smoke: pre-T `interest_states/`와 `user_interests.npz` 생성
- [ ] state seed full/smoke CLI: pre-T `user_states/` 생성 및 summary 기록
- [ ] replay smoke: `--start-rated-at 2022-01-01T00:00:00Z`와 seed dirs를 사용해 post-T event 처리
- [ ] `model.stream.runtime_report --db outputs/post/temporal_2022/replay.sqlite`
- [x] 문서의 runbook 명령이 실제 CLI와 일치하도록 업데이트

## 완료 조건

- `T = 2022-01-01T00:00:00Z` 계약이 train, canonical, state seed, replay manifest에 모두 기록된다.
- pre-T checkpoint/item2idx/canonical/user state/interest state가 `outputs/pre/temporal_2022/` 아래에 생성된다.
- post-T replay가 seed state를 기반으로 실행되고 `replay_summary.json`과 `replay.sqlite`에 부하 지표가 기록된다.
- 문서에서 파일 간 계약과 실행 순서를 재현 가능하게 설명한다.
- 검증 결과를 이 계획서와 `todo.md`에 반영한 뒤 완료 시 `plan/done/`으로 이동한다.
