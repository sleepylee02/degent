# model/

SASRec + Contrastive Loss 기반 적응형 다중 관심사 추천 시스템 구현체

## 전체 흐름

```
python3 -m model.batch.train → 모델 학습 → sasrec_cl.pt + item2idx.json 저장
  ├─ python3 -m model.batch.extract → legacy overlap 히든스테이트 추출 → embeddings.npz 저장
  │    ↓
  │  python3 -m model.batch.cluster → 유저별 UMAP(10D) + HDBSCAN → user_interests.npz 저장
  │    ↓
  │  python3 -m model.batch.recommend → u_k 기반 추천 스코어링 → recommendations.csv/npz 저장
  │    ↓
  │  python3 -m model.batch.visualize_clusters → 유저별 클러스터 변화 시각화 → outputs/viz/ 저장
  └─ python3 -m model.batch.extract_canonical → event당 canonical 히든스테이트 1개 추출 → canonical_embeddings.npz 저장
       ↓
     python3 -m model.stream.extract_online → raw user state 갱신 + active positive online_embeddings.npz 저장
       ↓
     python3 -m model.stream.interest_assign → interest assignment + refit request 기록
```

---

## 파일 구성

| 경로 | 역할 |
|---|---|
| `batch/train.py` | batch 학습 실행 |
| `batch/extract.py` | legacy overlap-window batch 히든스테이트 추출 |
| `batch/extract_canonical.py` | event당 canonical 히든스테이트 1개 추출 |
| `batch/cluster.py` | batch 유저별 UMAP + HDBSCAN 클러스터링 |
| `batch/recommend.py` | user interest vector 기반 item scoring/recommendation 산출 |
| `batch/visualize_clusters.py` | batch 클러스터 변화 시각화 |
| `common/canonical.py` | canonical event window, Dataset, 검증 helper |
| `common/dataset.py` | 데이터 로드, 전처리, Dataset |
| `common/sasrec.py` | SASRecCL 모델, Contrastive Loss |
| `common/runtime.py` | 로그, run metadata, seed/device 유틸 |
| `stream/state.py` | online user raw event state, positive projection, state JSON 저장/로드 |
| `stream/extract_online.py` | online rating ingest, active positive canonical embedding 추출 |
| `stream/interest_assign.py` | online interest assignment, pending buffer, refit request 기록 |
| `stream/drift_detector.py` | Phase 4 refit trigger placeholder |
| `stream/cluster_refit.py` | Phase 4 triggered refit placeholder |
| `IMPLEMENTATION_STATUS.md` | 모델 구현 현황, 산출물 상태, 보류 보완 후보 |

---

## 실행 순서

```bash
# 1. 학습 (GPU 환경)
python3 -m model.batch.train

# 1-1. 특정 실험 ID로 학습
python3 -m model.batch.train --run-id sasrec_cl_cl0_05 --cl-lambda 0.05

# 2. 임베딩 추출
python3 -m model.batch.extract

# 2-1. streaming/replay 계약 검증용 canonical event embedding 추출
python3 -m model.batch.extract_canonical

# 2-2. canonical extract smoke test
python3 -m model.batch.extract_canonical --limit-users 2 --batch-size 32 --num-workers 0 --output outputs/test_canonical_embeddings.npz

# 2-3. online embedding/user state smoke test
python3 -m model.stream.extract_online --bootstrap-user-id 28 --output outputs/stream/test_online_embeddings.npz

# 2-4. online interest assignment/refit trigger smoke test
python3 -m model.stream.interest_assign --embeddings outputs/stream/test_online_embeddings.npz

# 3. 클러스터링 (전체 유저)
python3 -m model.batch.cluster

# 3-1. 클러스터링 (옵션)
python3 -m model.batch.cluster --user-id 28        # 특정 유저만
python3 -m model.batch.cluster --top-n 50          # 시퀀스 긴 상위 50명
python3 -m model.batch.cluster --stride 2          # 매 2번째 시점만 사용 (속도 향상)

# 4. 추천/시각화
python3 -m model.batch.recommend                  # 추천 CSV/NPZ 산출
python3 -m model.batch.recommend --user-id 28 --top-k 20
python3 -m model.batch.visualize_clusters          # 전체 유저
python3 -m model.batch.visualize_clusters --user-id 28  # 특정 유저만
```

---

## 실행 환경과 로그

- `batch/train.py`, `batch/extract.py`, `batch/extract_canonical.py`는 실행 시 `cuda` → `mps` → `cpu` 순으로 자동 선택한다.
- 선택된 device는 콘솔과 실행 로그 파일에 함께 기록된다.
- `batch/cluster.py`는 현재 NumPy/UMAP/HDBSCAN 기반으로 CPU 실행 로그를 남기고, `batch/recommend.py`는 checkpoint item embedding을 CPU로 로드해 추천 로그를 남긴다.
- 실행 로그는 `outputs/logs/<script>_YYYYmmdd_HHMMSS.log`에 저장된다.

---

## 실험 메타데이터

- `python3 -m model.batch.train`는 `--run-id`가 없으면 timestamp 기반 run id를 새로 만들고 `outputs/latest_model_run_id.txt`에 기록한다.
- `python3 -m model.batch.extract`, `python3 -m model.batch.extract_canonical`, `python3 -m model.batch.cluster`, `python3 -m model.batch.recommend`는 `--run-id`가 없으면 `outputs/latest_model_run_id.txt`의 run id를 이어받는다.
- run별 메타데이터는 `experiments/model/<run_id>/` 아래에 저장된다.
- `manifest.json`에는 command, git 상태, 입력 파일 metadata, 스키마 버전, config, 출력 ref를 기록한다.
- `metrics.jsonl`에는 epoch별 학습 지표와 extract/cluster summary를 append한다.
- `notes.md`는 사람이 run 목적, 이전 run 대비 차이, 관찰 내용을 적는 파일이다.
- 큰 입력/산출물은 git에 저장하지 않는다. SHA256은 기본 100MB 이하 파일만 계산하고, 큰 파일은 size/mtime만 남긴다. 필요하면 `--hash-inputs --hash-limit-mb -1`로 강제할 수 있다.

---

## 모델 변경 이력 찾기

이전 모델 코드는 `model/prev/`에 복사해 보관하지 않는다. 과거 정보가 필요하면 아래 순서로 찾는다.

1. 현재 공식 구조와 실행 경로: `PROJECT_GUIDE.md`, `model/README.md`
2. 구조 변경과 모델링 판단 이유: `docs/decisions/`
3. 실험별 config, metric, 산출물 참조, 이전 run 대비 관찰: `experiments/model/<run_id>/`
4. 특정 파일의 과거 코드: git history

```bash
git log -- model/
git show <commit>:model/cluster.py
git diff <old_commit>..<new_commit> -- model/
```

계속 실행할 필요가 있는 비교 구현은 `prev`가 아니라 별도 결정 후 `model/baselines/` 같은 명확한 경로로 둔다.

---

## 주요 하이퍼파라미터

| 파라미터 | 값 | 설명 |
|---|---|---|
| seq_len | 100 | 시퀀스 길이 |
| d_model | 128 | 임베딩 차원 |
| cl_lambda | 0.1 | Contrastive Loss 가중치 (0.05/0.1/0.2 튜닝 필요) |
| train: min_interactions | 200 | 학습 유저 필터링 기준 |
| train: stride | 50 | 학습용 슬라이딩 윈도우 간격 |
| extract: min_interactions | 1000 | 클러스터링 대상 유저 필터링 기준 |
| extract: stride | 10 | 추출용 슬라이딩 윈도우 간격 |
| extract: interval | 10 | 히든스테이트 추출 간격 |
| extract_canonical: min_interactions | 1000 | canonical event embedding 대상 유저 필터링 기준 |
| extract_canonical: output | `outputs/canonical_embeddings.npz` | event당 embedding 1개를 저장하는 기본 출력 경로 |
| stream: min_ratings_for_zscore | 3 | online positive projection에서 z-score를 적용하기 전 optimistic cold-start 기준 |
| stream: output | `outputs/stream/online_embeddings.npz` | active positive online embedding 기본 출력 경로 |
| stream: state_dir | `outputs/stream/user_states/` | user별 raw event state JSON 저장 경로 |
| interest_assign: similarity_threshold | 0.2 | nearest interest cosine similarity가 이 값보다 낮으면 outlier |
| interest_assign: refit_min_events | 20 | no-interest/pending event 기반 refit request 최소 이벤트 수 |
| interest_assign: assign_trigger_count | 50 | refit 이후 assign 누적 수 기반 refit request 기준 |
| interest_assign: outlier_trigger_count | 10 | outlier 누적 수 기반 refit request 기준 |
| cluster: cluster_n_components | 10 | HDBSCAN 입력 UMAP 차원 |
| cluster: viz_n_components | 3 | 시각화용 UMAP 차원 |
| cluster: min_cluster_size | 10 | HDBSCAN 최소 클러스터 크기 |
| recommend: top_k | 20 | 유저별 추천 후보 저장 개수 |
| recommend: output_csv | `outputs/recommendations.csv` | 대시보드/검사용 추천 테이블 기본 출력 |
| recommend: output_npz | `outputs/recommendations.npz` | 추천 결과 배열 기본 출력 |

---

## 산출물

모델 가중치/임베딩/시각화 산출물은 프로젝트 루트의 `outputs/`에 저장된다. `outputs/readme.md`와 `outputs/logs/*.log`는 실행 기록 보존용으로 추적될 수 있고, 가중치/임베딩/플롯은 git 추적에서 제외한다.

| 파일 | 설명 |
|---|---|
| `outputs/sasrec_cl.pt` | 학습된 모델 가중치 |
| `outputs/item2idx.json` | 아이템 ID → 인덱스 매핑 (학습 vocabulary) |
| `outputs/embeddings.npz` | 시점별 히든스테이트 `embeddings(N,128)`, `user_ids(N,)`, `timepoint_idx(N,)` |
| `outputs/canonical_embeddings.npz` | canonical event embedding `embeddings(N,128)`, `user_ids(N,)`, `event_idx(N,)`, `movie_ids(N,)`, `rated_at_ts(N,)`, `rated_at_iso(N,)`, `history_len(N,)`, `context_start_idx(N,)` |
| `outputs/stream/user_states/{user_id}.json` | user별 raw rating event와 현재 positive projection state |
| `outputs/stream/online_embeddings.npz` | active positive online embedding `embeddings(N,128)`, `user_ids(N,)`, `raw_event_ids(N,)`, `event_idx(N,)`, `movie_ids(N,)`, `rated_at_ts(N,)`, `rated_at_iso(N,)`, `history_len(N,)`, `context_start_idx(N,)`, `status(N,)` |
| `outputs/stream/online_embedding_events.jsonl` | online ingest/extract run summary event log |
| `outputs/stream/interest_states/{user_id}.json` | user별 interest vectors, pending raw event ids, assignment/refit trigger state |
| `outputs/stream/interest_assignments.jsonl` | online embedding별 assignment/pending/outlier 결과 log |
| `outputs/stream/refit_requests.jsonl` | Phase 4-1 이후 refit backend가 소비할 open refit request log |
| `outputs/embeddings.npy` | 이전 추출 워크플로우에서 남은 legacy 산출물 |
| `outputs/user_interests.npz` | 유저별 클러스터 레이블, 관심사 벡터 u_k, UMAP 3D 좌표 |
| `outputs/recommendations.csv` | `score(u,i)=max_k(u_k^T v_i)` 기반 유저별 top-k 추천 테이블. 기본적으로 user positive history는 제외 |
| `outputs/recommendations.npz` | 추천 결과 배열 `user_ids`, `ranks`, `movie_ids`, `item_indices`, `scores`, `best_cluster_ids`, `cluster_scores` |
| `outputs/viz/user{id}.png` | 유저별 클러스터 변화 시각화 |
| `outputs/logs/*.log` | 스크립트별 실행 로그 |
| `experiments/model/<run_id>/manifest.json` | run별 config, git 상태, 입력/출력 metadata |
| `experiments/model/<run_id>/metrics.jsonl` | run별 metric 기록 |
| `experiments/model/<run_id>/notes.md` | run별 해석 메모 |

---

## 추후 보완 후보

**cl_lambda 튜닝**
현재 0.1 고정. 0.05 / 0.1 / 0.2 범위에서 실험 필요

**cluster_n_components 동적 조정**
현재 10D 고정. 유저별 시점 수 T에 비례한 동적 설정 고려 가능 (`min(10, max(3, T // 20))`)

**min_cluster_size 튜닝**
현재 10 고정. downstream 추천 성능(Recall@K, NDCG@K)으로 최적값 탐색 필요

**u_k 기반 추천 평가**
`user_interests.npz`의 u_k를 이용한 `score(u, i) = max_k(u_k^T · v_i)` 추천 산출은 `batch/recommend.py`에 구현됨. Recall@K/NDCG@K 평가 파이프라인은 아직 미구현

**canonical embedding downstream 연결**
`outputs/canonical_embeddings.npz`는 streaming/replay 계약 검증용 산출물이다. 아직 `batch/cluster.py`와 dashboard export는 이 산출물을 직접 소비하지 않는다.

**online positive policy 고도화**
Phase 3 stream path는 raw rating을 모두 저장하고, 현재까지 관측된 user history 기준 z-score positive projection을 재검증한다. 실제 서비스 정책에서는 threshold, running statistics, 유보 상태, latency budget을 추가 비교해야 한다.

**데이터 포맷 검증**
`movies_processed_drop.csv`의 genres 컬럼 형태, `ratings_drop_processed.jsonl` 스키마가 실제 파일과 일치하는지 확인 필요
