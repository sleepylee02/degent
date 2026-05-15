# Model Implementation Status

검토일: 2026-05-15

이 문서는 별도 파이프라인을 붙이기 전에 현재 model 파트의 구현 범위, 산출물 상태, 추후 보완 후보를 한곳에서 확인하기 위한 체크 파일이다.

## 상태 기준

- `[x]` 구현됨
- `[~]` 부분 구현됨 또는 추가 확인 필요
- `[ ]` 미구현

## 과거 정보 탐색

이전 모델 코드는 `model/prev/`에 복사 보관하지 않는다. 현재 구현 상태는 이 문서에서 확인하고, 변경 이유는 `docs/decisions/`, 실험별 비교는 `experiments/model/<run_id>/`, 특정 파일의 과거 코드는 git history에서 확인한다.

## 파이프라인 현황

| 단계 | 상태 | 구현 파일 | 현재 범위 | 체크할 점 |
|---|---:|---|---|---|
| 입력 데이터 로드/필터링 | [~] | `common/dataset.py` | `movies_processed_drop.csv`, `ratings_drop_processed.jsonl` 로드, rating z-score 기반 positive 필터링, 활동 기간/상호작용 수 필터링 | 실제 파일과 스키마 일치 검증 로직은 별도 없음 |
| 시계열 split/Dataset | [~] | `common/dataset.py` | global timestamp split, sliding window 샘플 생성, padding, 장르 multi-hot tensor 생성 | 평가 프로토콜이 next-item last label 중심이라 negative sampling/후보군 정의 확인 필요 |
| SASRec + Contrastive Loss | [x] | `common/sasrec.py` | item/genre/position embedding, causal Transformer encoder, weight tying score, item masking augmentation, InfoNCE loss | 모델 구조 자체는 구현됨 |
| 학습 | [~] | `batch/train.py` | CLI hyperparameter, device 선택, train/val loop, loss/CE/CL 분리 기록, Recall@10 기준 early stopping, 마지막/best checkpoint와 item2idx 저장, run metadata 기록 코드 | test 평가, scheduler, 튜닝 sweep는 없음 |
| legacy overlap 히든스테이트 추출 | [~] | 제거됨 | 과거 overlap-window extract가 `outputs/embeddings.npz`를 만들었으나 현재 공식 entrypoint는 아님 | 기존 artifact는 historical evidence로만 보존 |
| canonical event embedding 추출 | [x] | `batch/extract_canonical.py`, `common/canonical.py` | split 없는 전체 positive sequence에서 event 하나당 hidden state 하나를 추출, `event_idx`/`rated_at`/`history_len` metadata와 함께 `canonical_embeddings.npz` 저장, run metadata 기록 | 현재 batch cluster 기본 입력 |
| online embedding / user state | [x] | `stream/state.py`, `stream/extract_online.py` | raw rating event를 모두 user state에 저장하고, 현재까지 관측된 user history 기준 positive projection을 재검증한 뒤 active positive canonical embedding을 `outputs/stream/online_embeddings.npz`로 저장 | 실제 checkpoint smoke는 로컬 `outputs/item2idx.json` 존재가 필요함 |
| interest assign / refit trigger | [x] | `stream/interest_assign.py` | active online embedding을 user별 interest state에 cosine nearest-interest로 assign하고, no-interest/pending/outlier/event-count 기준 refit request를 기록 | 실제 refit은 Phase 4-1 이후 범위 |
| triggered cluster refit | [x] | `stream/cluster_refit.py` | Phase 4 refit request를 소비해 user별 active online embeddings 전체를 UMAP+HDBSCAN으로 refit하고 interest state를 replace | `auto`는 cuML/CUDA runtime 가능 시 GPU, 불가하거나 auto GPU refit 실패 시 CPU fallback |
| trace replay engine / closed-loop demo | [x] | `replay/cpp/rating_replay.cpp`, `stream/trace_replay.py`, `stream/replay_pipeline.py`, `stream/runtime_store.py` | ML-32M user history를 timestamp-sorted replay input으로 만들고, `--speed N` trace clock에 맞춰 Phase 3~4-1 CLI와 선택적 recommend 단계를 event 단위로 호출한다. Runtime state/payload/metadata/lifecycle/metric은 `replay.sqlite`에 기록하고 JSONL/JSON/NPZ는 fallback/debug와 대형 vector artifact 정본으로 유지한다 | Dashboard는 `paths.replayDb`가 있으면 SQLite를 우선 읽고 없으면 기존 JSONL artifact로 fallback한다 |
| 유저별 클러스터링 | [~] | `batch/cluster.py`, `common/cluster.py` | 유저별 UMAP + HDBSCAN, interest vector `u_k`, sliding window K(t), NaN 제거, `user_interests.npz`와 `outputs/batch/interest_states/{user_id}.json` 저장, genre labeling 포함 | 현재 산출물은 특정 유저 테스트 실행 결과로 보이며, 전체 유저 재실행 필요 |
| 클러스터 시각화 | [~] | `batch/visualize_clusters.py` | `user_interests.npz` 로드, 유저별 cluster timeline/K(t)/UMAP plot 저장 | run metadata 기록은 아직 없음 |
| dashboard cluster export | [x] | `batch/export_clusters.py` | `outputs/user_interests.npz`의 labels/UMAP 배열을 `data/clustering/user_clusters.parquet` 등 dashboard table로 변환 | batch interest state JSON 자체는 export하지 않음 |
| 실험 메타데이터 유틸 | [~] | `common/runtime.py` | 로그, run id, manifest/metrics/notes, git 상태, 입력/출력 metadata, seed/device 유틸 | 기존 산출물에는 run별 manifest가 확인되지 않음 |
| `u_k` 기반 추천 스코어링 | [x] | `batch/recommend.py`, `stream/recommend_online.py` | `score(u, i) = max_k(u_k^T v_i)`를 계산하고 top-K 추천을 CSV/NPZ 또는 JSONL로 기록. `replay_pipeline.py --recommend` 옵션으로 trace replay event loop에 통합 | stream 경로는 current interest state JSON을 읽음. batch 경로는 interest vector NPZ 입력 계약 정리 필요 |
| downstream 추천 평가 | [ ] | 없음 | `u_k` 기반 retrieval/rerank 평가 파이프라인 없음 | Recall@K/NDCG@K 평가 기준부터 확정 필요 |
| 설정 파일 기반 실행 | [ ] | 없음 | 주요 hyperparameter는 CLI 인자와 코드 기본값에 분산 | run 비교를 위해 config 파일 도입 검토 |

## 현재 산출물 확인

- `outputs/sasrec_cl.pt`: 학습 checkpoint 존재. 로그 기준 2026-04-08 09:34:52부터 20 epoch 학습, epoch 20 validation `Recall@10=0.0406`, `NDCG@10=0.0197`.
- `outputs/sasrec_cl_best.pt`: 최신 `batch/train.py`는 validation `Recall@10`이 개선될 때 best checkpoint를 저장한다. 기존 산출물 존재 여부는 최신 코드로 재학습 후 확인한다.
- `outputs/item2idx.json`: 학습 vocabulary 존재. 로그 기준 item 수 55,726.
- `outputs/embeddings.npz`: legacy overlap-window extract artifact. shape `(763772, 128)`, dtype `float32`, unique user 622로 확인됐으나 현재 재생성 entrypoint는 제거됐다.
- `outputs/canonical_embeddings.npz`: canonical extract 기본 출력 경로이며 현재 `batch/cluster.py` 기본 입력이다. Phase 2 smoke test에서는 `outputs/test_canonical_embeddings.npz`로 별도 저장해 검증했다.
- `outputs/batch/interest_states/{user_id}.json`: batch cluster가 저장하는 user별 interest state. interest vector와 genre label을 포함한다.
- `outputs/stream/user_states/{user_id}.json`: Phase 3 online state 기본 저장 경로. raw event와 positive projection을 함께 저장한다.
- `outputs/stream/online_embeddings.npz`: Phase 3 online embedding 기본 출력 경로. active positive row만 저장한다.
- `outputs/stream/online_embedding_events.jsonl`: Phase 3 online run summary event log.
- `outputs/stream/interest_states/{user_id}.json`: Phase 4 interest assignment/refit trigger state.
- `outputs/stream/interest_assignments.jsonl`: Phase 4 assignment/pending/outlier 결과 log.
- `outputs/stream/refit_requests.jsonl`: Phase 4-1 이후 refit backend가 소비할 request log.
- `outputs/stream/refit_events.jsonl`: Phase 4-1 refit request close/skip event log.
- `outputs/stream/stream_recommendations.jsonl`: `recommend_online` 단독 실행 기본 출력 경로. 유저별 top-K 추천 결과 JSONL (append).
- `outputs/stream/replay_demo/replay.sqlite`: trace replay runtime/state/control-plane store. run/event/stage/user/interest/refit/recommendation/embedding index를 기록한다.
- `outputs/stream/replay_demo/ingress_events.jsonl`: trace replay event emit log. event별 scheduled/emitted time, injector lag, behind-schedule flag를 기록한다.
- `outputs/stream/replay_demo/replay_events.jsonl`: trace replay progress log. event별 처리 결과, processing/end-to-end lag, assignment/refit/recommendation count를 기록한다.
- `outputs/stream/replay_demo/replay_summary.json`: dashboard stable entrypoint. speed, trace span, scheduled span, target/actual throughput, lag totals, `paths.replayDb`를 기록한다.
- `outputs/stream/replay_demo/stream_recommendations.jsonl`: replay demo 격리 경로. `--recommend` 옵션 사용 시 생성.
- `outputs/stream/replay_demo/`: trace replay demo root. 나머지 stream state/log/embedding은 replay run 안에 격리된다.
- `outputs/user_interests.npz`: 현재 shape 기준 label row 3,860, user 1명, `user_ids_list=[10202]` 테스트 모드 산출물로 보인다. 현재 포맷은 dashboard/export용 labels/UMAP/sliding-window 배열 중심이며, interest vector는 `outputs/batch/interest_states/{user_id}.json`에 저장된다. 전체 유저 클러스터링 산출물로 간주하면 안 된다.
- `outputs/embeddings.npy`: legacy 산출물로 보이며 현재 `np.load` 시 reshape 오류가 발생한다. 현 파이프라인 기준으로는 `outputs/canonical_embeddings.npz`를 사용한다.
- `experiments/model/`: 현재 `README.md`만 확인됨. 기존 산출물에 대응되는 `manifest.json`, `metrics.jsonl`, `notes.md` run 디렉토리는 확인되지 않았다.
- `outputs/logs/`: train/extract/cluster 로그가 존재하지만 과거 절대 경로가 서로 달라 historical evidence로만 취급한다.

## Legacy Overlap Embedding Artifact

과거 `model/batch/extract.py`는 overlap sliding window hidden state를 `outputs/embeddings.npz`로 저장했다. 현재 git 추적 대상에서는 이 entrypoint가 제거됐고, 공식 batch/stream/replay 흐름은 `batch/extract_canonical.py`의 `outputs/canonical_embeddings.npz`를 기준으로 한다.

현재 산출물 통계:

- 총 row: 763,772
- finite row: 763,760
- NaN row: 12 (`user_id=20921`의 timepoint 9~69, `user_id=87324`의 timepoint 9~49)
- unique `(user_id, timepoint_idx)`: 81,968
- duplicate row: 681,804
- 유저별 row 수: min 5, median 1,090, mean 1,227.93, max 11,930
- 유저별 unique timepoint 수: min 5, median 118, mean 131.78, max 1,202
- 동일 유저/동일 timepoint의 최대 중복 수: 10
- 동일 `(user_id, timepoint_idx)`라도 window context와 local positional embedding이 달라 벡터가 완전히 같지 않다. 예: `(user_id=28, timepoint_idx=19)`의 두 벡터 L2 distance는 약 0.378.

해석:

- 현재 embedding은 고유 시점별 representation이 아니라 overlap window context별 representation에 가깝다.
- downstream cluster는 중복 timepoint를 제거하지 않고 받기 때문에, 특정 시점이 최대 10번까지 가중되는 효과가 생긴다.
- 이 legacy 산출물은 보존하되, 새 실행 경로와 streaming/replay 계약에는 canonical event embedding을 사용한다.

## Canonical Event Embedding 흐름

`batch/extract_canonical.py` 기준 흐름:

1. `outputs/sasrec_cl.pt`와 `outputs/item2idx.json`을 로드한다.
2. `ratings_drop_processed.jsonl`에서 activity span 30일 이상, positive interaction 1000개 이상인 유저를 필터링한다.
3. split 없이 user별 positive sequence 전체를 timestamp 순서로 사용한다.
4. event를 먼저 고르고, 해당 event를 마지막 non-padding 위치로 하는 최근 `seq_len`개 window를 만든다.
5. `SASRecCL.get_last_hidden()`으로 마지막 non-padding hidden state를 뽑는다.
6. `embeddings`, `user_ids`, `event_idx`, `movie_ids`, `rated_at_ts`, `rated_at_iso`, `history_len`, `context_start_idx`를 `outputs/canonical_embeddings.npz`에 저장한다.

계약:

- row 하나는 user의 positive rating event 하나를 뜻한다.
- `(user_id, event_idx)`는 고유해야 한다.
- `event_idx`는 user별 positive sequence 기준 0-based index다.
- `history_len`은 해당 embedding이 사용한 context 길이이며, 첫 event는 1이고 최대 `seq_len`이다.
- `item2idx`에 없는 event는 현재 checkpoint가 모르는 item이므로 1차 정책에서 skip하고 metric으로 기록한다.

Phase 2 smoke test:

- command: `.venv/bin/python -m model.batch.extract_canonical --run-id phase2_canonical_smoke --limit-users 2 --batch-size 32 --num-workers 0 --output outputs/test_canonical_embeddings.npz`
- output: `outputs/test_canonical_embeddings.npz`
- shape: `(2869, 128)`
- unique `(user_id, event_idx)`: 2869 / 2869
- NaN row: 0
- max `history_len`: 100
- invalid `context_start_idx`: 0
- skipped unknown item: 0

## Batch Cluster / Export 흐름

`batch/cluster.py` 기준 흐름:

1. `outputs/canonical_embeddings.npz`를 기본 입력으로 읽는다. `--embeddings`로 다른 NPZ를 지정할 수 있으나 현재 공식 경로는 canonical embedding이다.
2. `event_idx`가 있으면 timepoint로 사용하고, legacy NPZ의 `timepoint_idx`도 fallback으로 허용한다.
3. `--cluster-backend auto|gpu|cpu`로 `model.common.cluster` backend를 선택한다.
4. 유저별 embedding을 UMAP + HDBSCAN으로 clustering한다.
5. `movie_ids`와 `data/movies_processed_drop.csv`가 있으면 cluster별 `topGenres`를 계산한다.
6. `outputs/user_interests.npz`에는 dashboard/export용 labels, UMAP 좌표, sliding-window K(t) 배열을 저장한다.
7. `outputs/batch/interest_states/{user_id}.json`에는 recommendation/refit과 공유 가능한 interest vector state를 저장한다.

`batch/export_clusters.py`는 `outputs/user_interests.npz`를 `data/clustering/user_clusters.parquet` 같은 table 포맷으로 바꿔 Cluster explorer가 읽을 수 있게 한다.

주의:

- 현재 `batch/recommend.py`는 interest vector arrays가 들어 있는 NPZ 입력을 기대한다.
- 현재 `batch/cluster.py`는 interest vector를 JSON interest state로 저장하므로 batch recommend와 새 cluster output을 바로 연결하려면 JSON loader 또는 별도 NPZ export가 필요하다.
- Streaming/replay 추천은 아래 `stream/recommend_online.py` 경로가 현재 주 경로다.

## Online Embedding / User State 흐름

`stream/extract_online.py` 기준 흐름:

1. `outputs/sasrec_cl.pt`와 `outputs/item2idx.json`을 로드한다.
2. 필요하면 `ratings_drop_processed.jsonl`에서 특정 user를 bootstrap한다.
3. 새 rating event는 평점과 무관하게 raw state에 저장한다.
4. user의 현재 raw history 전체를 `ratedAt`, `movieId`, `rawEventId` 순서로 정렬한다.
5. `ratings 수 < 3` 또는 rating 표준편차가 0이면 optimistic하게 positive로 둔다.
6. 그 외에는 현재까지 관측된 user ratings 기준 `z > 0`을 positive로 둔다.
7. positive event 중 `item2idx`에 있는 event만 active embedding 대상으로 삼는다.
8. active positive sequence 전체를 Phase 2 canonical window와 같은 right-padding + `SASRecCL.get_last_hidden()` 방식으로 재계산한다.
9. state JSON, online embedding NPZ, event log JSONL, run metadata를 저장한다.
10. `--runtime-db`가 있으면 `user_states`, `user_raw_events`, `user_positive_events`, `embedding_snapshots`, `embedding_rows`를 SQLite에 기록한다.

계약:

- raw rating event는 모두 보존한다.
- `rawEventId`는 user별 stable id다.
- `eventIdx`는 positive projection 기준 derived id이며 재검증 후 바뀔 수 있다.
- Phase 4는 `status == active`인 online embedding만 소비한다.
- Trace replay에서는 payload/state 요약은 SQLite에도 저장되고, 대형 embedding matrix 자체는 NPZ 파일 정본으로 유지한다.

## Interest Assign / Refit Trigger 흐름

`stream/interest_assign.py` 기준 흐름:

1. `outputs/stream/online_embeddings.npz`에서 `status == active` row만 로드한다.
2. user별 `outputs/stream/interest_states/{user_id}.json`을 로드하거나 새로 만든다.
3. interest state가 없거나 interest vector가 없으면 assign하지 않고 `pendingRawEventIds`에 쌓는다.
4. pending active event 수가 `refit_min_events` 이상이면 `no_interest_pending_events` refit request를 기록한다.
5. interest vector가 있으면 cosine similarity가 가장 큰 interest에 assign한다.
6. `max_similarity < similarity_threshold`이면 outlier로 기록하고 pending/refit 후보로 누적한다.
7. `assigned_since_last_refit`, `outlier_since_last_refit`, `pendingRawEventIds` 기준으로 refit request를 기록한다.
8. `--runtime-db`가 있으면 `interest_states`, `interest_vectors`, `assignments`, `refit_requests`를 SQLite에 기록한다.

계약:

- Phase 4는 UMAP/HDBSCAN refit을 실행하지 않는다.
- `interest_assignments.jsonl`은 assignment/pending/outlier 결과 fallback/debug log다.
- `refit_requests.jsonl`은 Phase 4-1 이후 refit backend 입력 후보와 fallback/debug log로 둔다. Trace replay에서 refit lifecycle 정본은 SQLite `refit_requests` table이다.

## Triggered Cluster Refit 흐름

`stream/cluster_refit.py` 기준 흐름:

1. `outputs/stream/refit_requests.jsonl`에서 open request를 읽는다.
2. `outputs/stream/online_embeddings.npz`에서 request user의 active embedding 전체를 모은다.
3. `--cluster-backend auto|gpu|cpu`로 backend를 선택한다.
4. `auto`는 cuML import와 CUDA runtime probe가 통과하면 GPU를 사용하고, GPU가 불가하거나 auto GPU refit 실행이 실패하면 CPU fallback을 사용한다.
5. CPU fallback은 `umap-learn + hdbscan`이다.
6. user별 active embedding 전체를 UMAP + HDBSCAN으로 clustering한다.
7. noise label `-1`은 interest vector에서 제외한다.
8. 전부 noise거나 샘플이 부족하면 전체 embedding mean fallback interest 1개를 만든다.
9. 기존 interest vectors를 replace하고 pending/refit flags를 clear한다.
10. SQLite `refit_requests` lifecycle을 `running -> closed/skipped/failed`로 갱신하고 `refit_attempts`를 기록한다.
11. `outputs/stream/refit_events.jsonl`에 request close/skip event fallback/debug log를 기록한다.

Phase 4-1 CPU fallback smoke:

- 초기 CPU fallback smoke에서는 `cuml` 미설치 환경에서 `--cluster-backend auto`가 CPU fallback을 선택했다.
- user 28 active embedding 1,579개를 refit해 interest 21개를 생성했다.
- noise row 43개는 interest vector에서 제외했다.
- refit 후 pending 0, processed 1,579, `refitRequired=false`, `refitRequestOpen=false`를 확인했다.
- 작은 fixture에서는 insufficient/all-noise case가 mean fallback interest 1개로 처리됐다.

Phase 4-1 GPU dependency/smoke:

- `cuml-cu12==26.4.0`은 CUDA 12.9 runtime wheel을 설치해 기존 `torch==2.5.1+cu121` 의존성과 충돌했고, 로컬 driver `535.288.01` / CUDA `12.2` 환경에서 CuPy kernel과 cuML UMAP이 `CUDA_ERROR_INVALID_IMAGE`로 실패했다.
- 최종 `.venv`는 `torch==2.5.1+cu121`, RAPIDS/cuML `25.10.0`, `cuda-toolkit==12.1.1`, `cupy-cuda12x==13.6.0`, `scikit-learn==1.7.2` 조합으로 고정했다.
- `pip check`, PyTorch CUDA import, CuPy kernel, cuML/cudf import를 확인했다.
- `phase4_1_cluster_refit_auto_gpu_smoke_final`에서 `--cluster-backend auto`가 GPU를 선택했고, user 28 active embedding 1,579개를 refit해 interest 33개를 생성했다. elapsed는 약 0.26초, noise row는 124개였다.
- GPU dependency 버저닝 결정은 `docs/decisions/0003-pin-rapids-cuml-gpu-dependencies.md`에 기록했다.

## Recommendation 흐름

`stream/recommend_online.py` 기준 흐름:

1. `outputs/stream/interest_states/{user_id}.json` 또는 지정한 `--interest-state-dir`에서 interest vector를 읽는다.
2. `outputs/sasrec_cl.pt`의 `item_emb.weight`와 `outputs/item2idx.json`을 로드한다.
3. `outputs/stream/user_states/{user_id}.json`에서 seen positive movie를 읽어 기본적으로 추천 후보에서 제외한다. `--include-seen`을 주면 제외하지 않는다.
4. `score(u, i) = max_k(u_k^T v_i)`로 candidate item을 scoring한다.
5. top-K 결과를 `outputs/stream/stream_recommendations.jsonl`에 append하고 run metadata/metric을 기록한다.
6. `--runtime-db`가 있으면 `recommendation_runs`, `recommendation_rows`를 SQLite에 기록한다.

`replay_pipeline.py --recommend`를 사용하면 각 event 처리 이후 replay scope의 `interest_states/`와 `user_states/`를 대상으로 같은 추천 단계를 호출한다. 결과는 SQLite와 `outputs/stream/replay_demo/stream_recommendations.jsonl`에 기록된다.

아직 없는 것:

- 추천 결과에 대한 Recall@K/NDCG@K 평가 파이프라인
- `batch/cluster.py`의 JSON interest state를 `batch/recommend.py`가 직접 읽는 연결

## Trace Replay Engine 흐름

Trace replay는 새 모델링을 추가하지 않고 Phase 3~4-1 CLI를 replay run 단위로 묶는다.

1. `make -C replay`로 `replay/bin/rating_replay`를 빌드한다.
2. `rating_replay`가 `data/ratings_drop_processed.jsonl`을 읽고 `ratedAtTs`, `userId`, `movieId`, `eventId` 순서의 `replay_input_events.jsonl`을 만든다.
3. `python3 -m model.stream.replay_pipeline --speed N`이 `scheduledAt = wallStart + (ratedAtTs - firstRatedAtTs) / N` 기준으로 event를 주입한다.
4. `outputs/stream/replay_demo/replay.sqlite`를 초기화하고 run/input/event progress를 기록한다.
5. event마다 `extract_online -> interest_assign -> cluster_refit`을 호출하며 각 stage에 `--runtime-db`, `--event-id`를 전달한다.
6. `--recommend` 옵션이 있으면 각 event 처리 뒤에 `recommend_online`을 호출한다.
7. 모든 산출물은 `outputs/stream/replay_demo/` 아래에 저장해 기본 `outputs/stream/*` 산출물을 덮어쓰지 않는다.
8. `ingress_events.jsonl`에는 event 주입 schedule/lag를 append한다.
9. `replay_events.jsonl`에는 progress, replay clock, processing/end-to-end latency, assignment/refit/recommendation count를 append하고, `replay_summary.json`에는 dashboard가 읽을 stable entrypoint와 `paths.replayDb`를 기록한다.

Trace replay smoke:

- command: `.venv/bin/python -m model.stream.replay_pipeline --reset-output --generate-events --replay-user-id 28 --limit-events 5 --speed 100 --refit-min-events 3 --assign-trigger-count 3 --outlier-trigger-count 3 --min-cluster-size 2 --cluster-dim 3 --cluster-backend cpu --skip-refit --run-id trace_replay_smoke`
- output root: `outputs/stream/replay_demo/`
- status: completed
- input/processed events: 5 / 5
- unique users: 1
- speed: 100
- trace/scheduled span: 32 sec / 0.32 sec
- target/actual throughput: 15.625 EPS / 약 0.158 EPS
- assignment records: 10
- refit requests opened/closed/skipped: 1 / 0 / 0 (`--skip-refit`)
- behind schedule events: 4
- max injector/processing/end-to-end lag: 약 25.26 / 6.94 / 31.42초
- elapsed: 약 31.74초

SQLite runtime store smoke:

- command: `.venv/bin/python -m model.stream.replay_pipeline --reset-output --generate-events --replay-user-id 28 --limit-events 3 --speed 100 --refit-min-events 3 --assign-trigger-count 3 --outlier-trigger-count 3 --min-cluster-size 2 --cluster-dim 3 --cluster-backend cpu --run-id sqlite_runtime_refit_smoke`
- output root: `outputs/stream/replay_demo/`
- SQLite table count: `runs=1`, `input_events=3`, `event_progress=3`, `stage_attempts=7`, `user_states=1`, `user_raw_events=3`, `user_positive_events=3`, `interest_states=1`, `assignments=5`, `refit_requests=1`, `refit_attempts=1`, `embedding_snapshots=3`, `embedding_rows=5`
- refit lifecycle: `skipped=1`
- stage attempts: `extract_online=3`, `interest_assign=3`, `cluster_refit=1`, all `completed`
- dashboard SQLite reader frame count: `replay_events=3`, `stage_attempts=7`, `assignments=5`, `refit_requests=1`, `refit_events=1`, `recommendations=0`

## 현재 정리 방향

- 지금 당장 기존 SASRec/cluster 파이프라인의 우선순위 개선 작업을 진행하지 않는다.
- 이 문서는 다른 파이프라인을 붙일 때 참고할 현재 구현 계약과 산출물 상태를 정리하는 용도로 둔다.
- 아래 항목들은 즉시 작업 대상이 아니라, 기존 모델 파이프라인을 다시 강화하거나 비교 실험할 때 고려할 보류 후보이다.
- 새 파이프라인을 붙일 때는 기존 `outputs/` 산출물을 덮어쓰지 않도록 별도 output path 또는 run id 기준 디렉토리를 먼저 정한다.

## 보류 중인 보완 후보

### 나중에 재검토할 핵심 후보

- [ ] 최신 코드로 `--run-id`를 명시해 `python3 -m model.batch.train/extract_canonical/cluster`를 다시 실행하고 `experiments/model/<run_id>/manifest.json`, `metrics.jsonl`, `notes.md` 생성 여부를 확인한다.
- [ ] legacy `outputs/embeddings.npz`를 계속 보존만 할지, 별도 baseline/compat entrypoint로 되살릴지 결정한다.
- [ ] `batch/recommend.py`가 현재 `outputs/batch/interest_states/{user_id}.json`을 직접 읽도록 바꾸거나, JSON interest state를 recommendation용 NPZ로 export하는 경로를 추가한다.
- [ ] `batch/cluster.py`가 테스트 실행 결과로 전체 `outputs/user_interests.npz`를 덮어쓰지 않도록 output path 옵션 또는 테스트 산출물 분리 방식을 추가한다.
- [ ] `outputs/user_interests.npz`를 전체 유저 대상으로 재생성하고 clustered user 수, K 분포, NaN drop 수를 기록한다.
- [x] `u_k` 기반 추천 스코어링 모듈을 설계/구현한다. → `stream/recommend_online.py`
- [ ] `u_k` 기반 scoring을 Recall@K/NDCG@K로 평가하는 파이프라인을 추가한다.
- [ ] `movies_processed_drop.csv`의 `genres`와 `ratings_drop_processed.jsonl` 입력 포맷을 스키마 기준으로 검증하는 체크를 추가한다.

### 후순위 후보

- [ ] `cl_lambda` 후보 `0.05`, `0.1`, `0.2` 실험을 run별로 기록한다.
- [ ] `min_cluster_size`, `cluster_n_components`, `stride`, `window_size` 튜닝 실험을 run별로 기록한다.
- [ ] 학습 후 test split 평가를 추가하고 validation/test metric을 분리 기록한다.
- [ ] 주요 hyperparameter를 config 파일로 묶어 재현성을 높인다.

### 문서화 후보

- [ ] `batch/visualize_clusters.py` 실행도 run metadata에 기록한다.
- [ ] 전체 유저 클러스터 결과를 요약하는 report 파일을 추가한다.
- [x] dashboard 입력 계약과 `user_interests.npz` export 경로를 문서화한다. → `dashboard/README.md`, `docs/artifacts.md`

## 열려 있는 결정

- 최종 추천 목표가 sequential next-item prediction인지, `u_k` 기반 multi-interest retrieval/reranking인지 확정해야 한다.
- rating z-score `z > 0`을 positive interaction으로 유지할지, rating threshold 기반으로 바꿀지 결정해야 한다.
- 전체 유저 UMAP/HDBSCAN runtime이 충분한지, top-N 또는 batch processing 전략이 필요한지 확인해야 한다.
