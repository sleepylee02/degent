# Model Implementation Status

검토일: 2026-05-06

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
| 학습 | [~] | `batch/train.py` | CLI hyperparameter, device 선택, train/val loop, Recall@10/NDCG@10, checkpoint와 item2idx 저장, run metadata 기록 코드 | test 평가, scheduler/early stopping, 튜닝 sweep는 없음 |
| 히든스테이트 추출 | [~] | `batch/extract.py` | checkpoint/item2idx 재사용, train split 대상 hidden state 추출, `embeddings.npz` 저장, run metadata 기록 코드 | overlap window 중복 timepoint 처리 방침 결정 필요 |
| canonical event embedding 추출 | [x] | `batch/extract_canonical.py`, `common/canonical.py` | split 없는 전체 positive sequence에서 event 하나당 hidden state 하나를 추출, `event_idx`/`rated_at`/`history_len` metadata와 함께 `canonical_embeddings.npz` 저장, run metadata 기록 | 아직 cluster/replay/dashboard downstream 입력으로 연결되지는 않음 |
| online embedding / user state | [x] | `stream/state.py`, `stream/extract_online.py` | raw rating event를 모두 user state에 저장하고, 현재까지 관측된 user history 기준 positive projection을 재검증한 뒤 active positive canonical embedding을 `outputs/stream/online_embeddings.npz`로 저장 | 실제 checkpoint smoke는 로컬 `outputs/item2idx.json` 존재가 필요함 |
| interest assign / refit trigger | [x] | `stream/interest_assign.py` | active online embedding을 user별 interest state에 cosine nearest-interest로 assign하고, no-interest/pending/outlier/event-count 기준 refit request를 기록 | 실제 refit은 Phase 4-1 이후 범위 |
| 유저별 클러스터링 | [~] | `batch/cluster.py` | 유저별 UMAP + HDBSCAN, interest vector `u_k`, sliding window K(t), NaN 제거, `user_interests.npz` 저장 | 현재 산출물은 특정 유저 테스트 실행 결과로 보이며, 전체 유저 재실행 필요 |
| 클러스터 시각화 | [~] | `batch/visualize_clusters.py` | `user_interests.npz` 로드, 유저별 cluster timeline/K(t)/UMAP plot 저장 | run metadata 기록은 아직 없음 |
| 실험 메타데이터 유틸 | [~] | `common/runtime.py` | 로그, run id, manifest/metrics/notes, git 상태, 입력/출력 metadata, seed/device 유틸 | 기존 산출물에는 run별 manifest가 확인되지 않음 |
| `u_k` 기반 추천 스코어링 | [ ] | 없음 | `user_interests.npz`의 interest vector로 `score(u, i) = max_k(u_k^T v_i)`를 계산하는 모듈 없음 | 추후 보완 후보 |
| downstream 추천 평가 | [ ] | 없음 | `u_k` 기반 retrieval/rerank 평가 파이프라인 없음 | Recall@K/NDCG@K 평가 기준부터 확정 필요 |
| 설정 파일 기반 실행 | [ ] | 없음 | 주요 hyperparameter는 CLI 인자와 코드 기본값에 분산 | run 비교를 위해 config 파일 도입 검토 |

## 현재 산출물 확인

- `outputs/sasrec_cl.pt`: 학습 checkpoint 존재. 로그 기준 2026-04-08 09:34:52부터 20 epoch 학습, epoch 20 validation `Recall@10=0.0406`, `NDCG@10=0.0197`.
- `outputs/item2idx.json`: 학습 vocabulary 존재. 로그 기준 item 수 55,726.
- `outputs/embeddings.npz`: shape `(763772, 128)`, dtype `float32`, unique user 622.
- `outputs/canonical_embeddings.npz`: 기본 출력 경로. Phase 2 smoke test에서는 `outputs/test_canonical_embeddings.npz`로 별도 저장해 검증했다.
- `outputs/stream/user_states/{user_id}.json`: Phase 3 online state 기본 저장 경로. raw event와 positive projection을 함께 저장한다.
- `outputs/stream/online_embeddings.npz`: Phase 3 online embedding 기본 출력 경로. active positive row만 저장한다.
- `outputs/stream/online_embedding_events.jsonl`: Phase 3 online run summary event log.
- `outputs/stream/interest_states/{user_id}.json`: Phase 4 interest assignment/refit trigger state.
- `outputs/stream/interest_assignments.jsonl`: Phase 4 assignment/pending/outlier 결과 log.
- `outputs/stream/refit_requests.jsonl`: Phase 4-1 이후 refit backend가 소비할 request log.
- `outputs/user_interests.npz`: 현재 shape 기준 label row 3,860, user 1명, interest vector `(103, 128)`, `user_ids_list=[10202]`. 최신 로그가 `user_id=10202` 테스트 모드였으므로 전체 유저 클러스터링 산출물로 간주하면 안 된다.
- `outputs/embeddings.npy`: legacy 산출물로 보이며 현재 `np.load` 시 reshape 오류가 발생한다. 현 파이프라인 기준으로는 `outputs/embeddings.npz`를 사용한다.
- `experiments/model/`: 현재 `README.md`만 확인됨. 기존 산출물에 대응되는 `manifest.json`, `metrics.jsonl`, `notes.md` run 디렉토리는 확인되지 않았다.
- `outputs/logs/`: train/extract/cluster 로그가 존재하지만 과거 절대 경로가 서로 달라 historical evidence로만 취급한다.

## Embedding 추출 흐름

현재 `batch/extract.py` 기준 흐름:

1. `outputs/sasrec_cl.pt`와 `outputs/item2idx.json`을 로드한다.
2. `ratings_drop_processed.jsonl`에서 activity span 30일 이상, positive interaction 1000개 이상인 유저만 필터링한다.
3. global temporal split 이후 `train_seq`만 사용한다.
4. `MovieLensDataset(seq_len=100, stride=10)`으로 overlap sliding window를 만든다.
5. 각 window에 대해 `SASRecCL.encode()`를 실행해 `(B, 100, 128)` hidden state를 얻는다.
6. 각 window의 non-padding 영역에서 `interval=10`마다 hidden state를 뽑아 `embeddings`, `user_ids`, `timepoint_idx`를 `outputs/embeddings.npz`에 저장한다.

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
- 이 legacy 산출물은 보존하되, streaming/replay 계약에는 canonical event embedding을 사용한다.

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

계약:

- raw rating event는 모두 보존한다.
- `rawEventId`는 user별 stable id다.
- `eventIdx`는 positive projection 기준 derived id이며 재검증 후 바뀔 수 있다.
- Phase 4는 `status == active`인 online embedding만 소비한다.

## Interest Assign / Refit Trigger 흐름

`stream/interest_assign.py` 기준 흐름:

1. `outputs/stream/online_embeddings.npz`에서 `status == active` row만 로드한다.
2. user별 `outputs/stream/interest_states/{user_id}.json`을 로드하거나 새로 만든다.
3. interest state가 없거나 interest vector가 없으면 assign하지 않고 `pendingRawEventIds`에 쌓는다.
4. pending active event 수가 `refit_min_events` 이상이면 `no_interest_pending_events` refit request를 기록한다.
5. interest vector가 있으면 cosine similarity가 가장 큰 interest에 assign한다.
6. `max_similarity < similarity_threshold`이면 outlier로 기록하고 pending/refit 후보로 누적한다.
7. `assigned_since_last_refit`, `outlier_since_last_refit`, `pendingRawEventIds` 기준으로 refit request를 기록한다.

계약:

- Phase 4는 UMAP/HDBSCAN refit을 실행하지 않는다.
- `interest_assignments.jsonl`은 assignment/pending/outlier 결과 log다.
- `refit_requests.jsonl`은 Phase 4-1 이후 refit backend 입력 후보로 둔다.

## 현재 정리 방향

- 지금 당장 기존 SASRec/cluster 파이프라인의 우선순위 개선 작업을 진행하지 않는다.
- 이 문서는 다른 파이프라인을 붙일 때 참고할 현재 구현 계약과 산출물 상태를 정리하는 용도로 둔다.
- 아래 항목들은 즉시 작업 대상이 아니라, 기존 모델 파이프라인을 다시 강화하거나 비교 실험할 때 고려할 보류 후보이다.
- 새 파이프라인을 붙일 때는 기존 `outputs/` 산출물을 덮어쓰지 않도록 별도 output path 또는 run id 기준 디렉토리를 먼저 정한다.

## 보류 중인 보완 후보

### 나중에 재검토할 핵심 후보

- [ ] 최신 코드로 `--run-id`를 명시해 `python3 -m model.batch.train/extract/cluster`를 다시 실행하고 `experiments/model/<run_id>/manifest.json`, `metrics.jsonl`, `notes.md` 생성 여부를 확인한다.
- [ ] `batch/extract.py`에서 overlap window로 생기는 duplicate `(user_id, timepoint_idx)` 처리 방침을 정한다.
- [ ] `batch/extract.py`에서 발생한 NaN embedding row 원인을 추적하고 제거/방지 로직을 추가한다.
- [ ] `batch/cluster.py`가 테스트 실행 결과로 전체 `outputs/user_interests.npz`를 덮어쓰지 않도록 output path 옵션 또는 테스트 산출물 분리 방식을 추가한다.
- [ ] `outputs/user_interests.npz`를 전체 유저 대상으로 재생성하고 clustered user 수, K 분포, NaN drop 수를 기록한다.
- [ ] `u_k` 기반 추천 스코어링 모듈을 설계/구현한다.
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
- [ ] dashboard 입력 계약과 `user_interests.npz` schema를 문서화한다.

## 열려 있는 결정

- 최종 추천 목표가 sequential next-item prediction인지, `u_k` 기반 multi-interest retrieval/reranking인지 확정해야 한다.
- rating z-score `z > 0`을 positive interaction으로 유지할지, rating threshold 기반으로 바꿀지 결정해야 한다.
- 전체 유저 UMAP/HDBSCAN runtime이 충분한지, top-N 또는 batch processing 전략이 필요한지 확인해야 한다.
