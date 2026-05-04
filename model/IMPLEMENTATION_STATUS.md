# Model Implementation Status

검토일: 2026-04-14

이 문서는 별도 파이프라인을 붙이기 전에 현재 model 파트의 구현 범위, 산출물 상태, 추후 보완 후보를 한곳에서 확인하기 위한 체크 파일이다.

## 상태 기준

- `[x]` 구현됨
- `[~]` 부분 구현됨 또는 추가 확인 필요
- `[ ]` 미구현

## 파이프라인 현황

| 단계 | 상태 | 구현 파일 | 현재 범위 | 체크할 점 |
|---|---:|---|---|---|
| 입력 데이터 로드/필터링 | [~] | `dataset.py` | `movies_processed_drop.csv`, `ratings_drop_processed.jsonl` 로드, rating z-score 기반 positive 필터링, 활동 기간/상호작용 수 필터링 | 실제 파일과 스키마 일치 검증 로직은 별도 없음 |
| 시계열 split/Dataset | [~] | `dataset.py` | global timestamp split, sliding window 샘플 생성, padding, 장르 multi-hot tensor 생성 | 평가 프로토콜이 next-item last label 중심이라 negative sampling/후보군 정의 확인 필요 |
| SASRec + Contrastive Loss | [x] | `model.py` | item/genre/position embedding, causal Transformer encoder, weight tying score, item masking augmentation, InfoNCE loss | 모델 구조 자체는 구현됨 |
| 학습 | [~] | `train.py` | CLI hyperparameter, device 선택, train/val loop, Recall@10/NDCG@10, checkpoint와 item2idx 저장, run metadata 기록 코드 | test 평가, scheduler/early stopping, 튜닝 sweep는 없음 |
| 히든스테이트 추출 | [~] | `extract.py` | checkpoint/item2idx 재사용, train split 대상 hidden state 추출, `embeddings.npz` 저장, run metadata 기록 코드 | overlap window 중복 timepoint 처리 방침 결정 필요 |
| 유저별 클러스터링 | [~] | `cluster.py` | 유저별 UMAP + HDBSCAN, interest vector `u_k`, sliding window K(t), NaN 제거, `user_interests.npz` 저장 | 현재 산출물은 특정 유저 테스트 실행 결과로 보이며, 전체 유저 재실행 필요 |
| 클러스터 시각화 | [~] | `visualize_clusters.py` | `user_interests.npz` 로드, 유저별 cluster timeline/K(t)/UMAP plot 저장 | run metadata 기록은 아직 없음 |
| 실험 메타데이터 유틸 | [~] | `runtime.py` | 로그, run id, manifest/metrics/notes, git 상태, 입력/출력 metadata, seed/device 유틸 | 기존 산출물에는 run별 manifest가 확인되지 않음 |
| `u_k` 기반 추천 스코어링 | [ ] | 없음 | `user_interests.npz`의 interest vector로 `score(u, i) = max_k(u_k^T v_i)`를 계산하는 모듈 없음 | 추후 보완 후보 |
| downstream 추천 평가 | [ ] | 없음 | `u_k` 기반 retrieval/rerank 평가 파이프라인 없음 | Recall@K/NDCG@K 평가 기준부터 확정 필요 |
| 설정 파일 기반 실행 | [ ] | 없음 | 주요 hyperparameter는 CLI 인자와 코드 기본값에 분산 | run 비교를 위해 config 파일 도입 검토 |

## 현재 산출물 확인

- `outputs/sasrec_cl.pt`: 학습 checkpoint 존재. 로그 기준 2026-04-08 09:34:52부터 20 epoch 학습, epoch 20 validation `Recall@10=0.0406`, `NDCG@10=0.0197`.
- `outputs/item2idx.json`: 학습 vocabulary 존재. 로그 기준 item 수 55,726.
- `outputs/embeddings.npz`: shape `(763772, 128)`, dtype `float32`, unique user 622.
- `outputs/user_interests.npz`: 현재 shape 기준 label row 3,860, user 1명, interest vector `(103, 128)`, `user_ids_list=[10202]`. 최신 로그가 `user_id=10202` 테스트 모드였으므로 전체 유저 클러스터링 산출물로 간주하면 안 된다.
- `outputs/embeddings.npy`: legacy 산출물로 보이며 현재 `np.load` 시 reshape 오류가 발생한다. 현 파이프라인 기준으로는 `outputs/embeddings.npz`를 사용한다.
- `experiments/model/`: 현재 `README.md`만 확인됨. 기존 산출물에 대응되는 `manifest.json`, `metrics.jsonl`, `notes.md` run 디렉토리는 확인되지 않았다.
- `outputs/logs/`: train/extract/cluster 로그가 존재하지만 과거 절대 경로가 서로 달라 historical evidence로만 취급한다.

## Embedding 추출 흐름

현재 `extract.py` 기준 흐름:

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
- 이 방식을 유지할지, 각 timepoint별 canonical embedding만 남길지 먼저 결정해야 한다.

## 현재 정리 방향

- 지금 당장 기존 SASRec/cluster 파이프라인의 우선순위 개선 작업을 진행하지 않는다.
- 이 문서는 다른 파이프라인을 붙일 때 참고할 현재 구현 계약과 산출물 상태를 정리하는 용도로 둔다.
- 아래 항목들은 즉시 작업 대상이 아니라, 기존 모델 파이프라인을 다시 강화하거나 비교 실험할 때 고려할 보류 후보이다.
- 새 파이프라인을 붙일 때는 기존 `outputs/` 산출물을 덮어쓰지 않도록 별도 output path 또는 run id 기준 디렉토리를 먼저 정한다.

## 보류 중인 보완 후보

### 나중에 재검토할 핵심 후보

- [ ] 최신 코드로 `--run-id`를 명시해 train/extract/cluster를 다시 실행하고 `experiments/model/<run_id>/manifest.json`, `metrics.jsonl`, `notes.md` 생성 여부를 확인한다.
- [ ] `extract.py`에서 overlap window로 생기는 duplicate `(user_id, timepoint_idx)` 처리 방침을 정한다.
- [ ] `extract.py`에서 발생한 NaN embedding row 원인을 추적하고 제거/방지 로직을 추가한다.
- [ ] `cluster.py`가 테스트 실행 결과로 전체 `outputs/user_interests.npz`를 덮어쓰지 않도록 output path 옵션 또는 테스트 산출물 분리 방식을 추가한다.
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

- [ ] `visualize_clusters.py` 실행도 run metadata에 기록한다.
- [ ] 전체 유저 클러스터 결과를 요약하는 report 파일을 추가한다.
- [ ] dashboard 입력 계약과 `user_interests.npz` schema를 문서화한다.

## 열려 있는 결정

- 최종 추천 목표가 sequential next-item prediction인지, `u_k` 기반 multi-interest retrieval/reranking인지 확정해야 한다.
- rating z-score `z > 0`을 positive interaction으로 유지할지, rating threshold 기반으로 바꿀지 결정해야 한다.
- 전체 유저 UMAP/HDBSCAN runtime이 충분한지, top-N 또는 batch processing 전략이 필요한지 확인해야 한다.
