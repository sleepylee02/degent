# model/

SASRec + Contrastive Loss 기반 적응형 다중 관심사 추천 시스템 구현체

## 전체 흐름

현재 batch / streaming 구현과 문제 포인트를 구체적으로 확인할 때는 `docs/current-pipeline-snapshot.md`를 먼저 본다. Streaming replay e2e를 실행하거나 다른 파트에 넘길 artifact를 확인할 때는 `docs/streaming-e2e-pipeline.md`를 함께 본다.

```
python3 -m model.batch.train → 모델 학습 → sasrec_cl.pt + sasrec_cl_best.pt + item2idx.json 저장
  └─ python3 -m model.batch.extract_canonical → event당 canonical 히든스테이트 1개 추출 → canonical_embeddings.npz 저장
       ↓
     python3 -m model.batch.cluster → 유저별 UMAP + HDBSCAN → user_interests.npz + outputs/batch/interest_states/{id}.json 저장
       ↓
     python3 -m model.batch.export_clusters → dashboard table 저장
       ↓
     python3 -m model.batch.visualize_clusters → 유저별 클러스터 변화 시각화 → outputs/viz/ 저장

streaming/replay path:
python3 -m model.stream.extract_online → raw user state 갱신 + active positive online_embeddings.npz 저장
       ↓
     python3 -m model.stream.interest_assign → interest assignment + refit request 기록
       ↓
     python3 -m model.stream.cluster_refit → refit request 소비 + interest state replace
       ↓
     python3 -m model.stream.recommend_online → interest state 기반 top-K 추천 기록
       ↓
     python3 -m model.stream.replay_pipeline --speed N --recommend → N배속 trace-clock replay + 추천 demo
```

---

## 파일 구성

| 경로 | 역할 |
|---|---|
| `batch/train.py` | batch 학습 실행 |
| `batch/extract_canonical.py` | event당 canonical 히든스테이트 1개 추출 |
| `batch/cluster.py` | batch 유저별 UMAP + HDBSCAN 클러스터링 (기본 입력: `canonical_embeddings.npz`, genre labeling 포함) |
| `batch/export_clusters.py` | `user_interests.npz`를 dashboard table(`parquet/csv/jsonl/ndjson`)로 변환 |
| `batch/recommend.py` | interest vector NPZ 기반 batch top-K 추천 export. 현재 stream/replay 경로는 `stream/recommend_online.py`가 주 경로 |
| `batch/visualize_clusters.py` | batch 클러스터 변화 시각화 |
| `common/canonical.py` | canonical event window, Dataset, 검증 helper |
| `common/cluster.py` | UMAP+HDBSCAN, GPU/CPU backend 선택, top_genres_for_cluster |
| `common/dataset.py` | 데이터 로드, 전처리, Dataset |
| `common/sasrec.py` | SASRecCL 모델, Contrastive Loss |
| `common/runtime.py` | 로그, run metadata, seed/device 유틸 |
| `stream/state.py` | online user raw event state, positive projection, state JSON 저장/로드 |
| `stream/extract_online.py` | online rating ingest, active positive canonical embedding 추출 |
| `stream/interest_assign.py` | online interest assignment, pending buffer, refit request 기록 |
| `stream/cluster_refit.py` | triggered cluster refit backend, GPU-first/CPU fallback, genre labeling 포함 |
| `stream/recommend_online.py` | streaming interest state를 읽어 top-K 추천 JSONL append |
| `stream/trace_replay.py` | replay input event를 `--speed N` trace clock으로 주입하고 event-level lag/throughput metric 기록 |
| `stream/replay_pipeline.py` | `trace_replay.py`를 실행하는 공식 replay entrypoint 호환 래퍼 |
| `IMPLEMENTATION_STATUS.md` | 모델 구현 현황, 산출물 상태, 보류 보완 후보 |

---

## 실행 순서

```bash
# 1. 학습 (GPU 환경)
python3 -m model.batch.train

# 1-1. 특정 실험 ID로 학습
python3 -m model.batch.train --run-id sasrec_cl_cl0_05 --cl-lambda 0.05

# 2. canonical event embedding 추출 (streaming/replay 파이프라인 기본 입력)
python3 -m model.batch.extract_canonical

# 2-1. canonical extract smoke test
python3 -m model.batch.extract_canonical --limit-users 2 --batch-size 32 --num-workers 0 --output outputs/test_canonical_embeddings.npz

# 2-2. 단일 유저 추출 (CPU 테스트용, min-interactions 필터 우회)
python3 -m model.batch.extract_canonical --user-id 28

# 2-3. 배치 클러스터링 (canonical_embeddings.npz 기본 입력, genre labeling 자동)
python3 -m model.batch.cluster

# 2-4. 배치 클러스터링 단일 유저 테스트
python3 -m model.batch.cluster --user-id 28

# 2-5. online embedding/user state smoke test
python3 -m model.stream.extract_online --bootstrap-user-id 28 --output outputs/stream/test_online_embeddings.npz

# 2-6. online interest assignment/refit trigger smoke test
python3 -m model.stream.interest_assign --embeddings outputs/stream/test_online_embeddings.npz

# 2-7. triggered cluster refit smoke test
python3 -m model.stream.cluster_refit --embeddings outputs/stream/test_online_embeddings.npz

# 2-8. triggered cluster refit GPU/auto smoke test
python3 -m model.stream.cluster_refit --embeddings outputs/stream/test_online_embeddings.npz --cluster-backend auto

# 2-9. replay event generator build
make -C replay

# 2-10. trace-clock replay smoke test
python3 -m model.stream.replay_pipeline --reset-output --generate-events --replay-user-id 28 --limit-events 5 --speed 100 --refit-min-events 3 --assign-trigger-count 3 --outlier-trigger-count 3 --min-cluster-size 2 --cluster-dim 3 --cluster-backend cpu --skip-refit --run-id trace_replay_smoke

# 2-11. trace-clock replay + online recommendation smoke test
python3 -m model.stream.replay_pipeline --reset-output --generate-events --replay-user-id 28 --limit-events 5 --speed 100 --refit-min-events 3 --assign-trigger-count 3 --outlier-trigger-count 3 --min-cluster-size 2 --cluster-dim 3 --cluster-backend cpu --skip-refit --recommend --recommend-top-k 20 --run-id trace_replay_with_recommend

# 3. 클러스터링 (배치, 전체 유저)
python3 -m model.batch.cluster

# 3-1. 클러스터링 (옵션)
python3 -m model.batch.cluster --user-id 28        # 특정 유저만
python3 -m model.batch.cluster --top-n 50          # 시퀀스 긴 상위 50명
python3 -m model.batch.cluster --stride 2          # 매 2번째 시점만 사용 (속도 향상)
python3 -m model.batch.cluster --embeddings outputs/canonical_embeddings.npz

# 3-2. dashboard table export
python3 -m model.batch.export_clusters --input outputs/user_interests.npz --output data/clustering/user_clusters.parquet

# 3-3. streaming interest state 기반 online recommendation
python3 -m model.stream.recommend_online --user-id 28

# 4. 시각화
python3 -m model.batch.visualize_clusters          # 전체 유저
python3 -m model.batch.visualize_clusters --user-id 28  # 특정 유저만
```

---

## 실행 환경과 로그

- `batch/train.py`, `batch/extract_canonical.py`는 실행 시 `cuda` → `mps` → `cpu` 순으로 자동 선택한다.
- 선택된 device는 콘솔과 실행 로그 파일에 함께 기록된다.
- `batch/cluster.py`와 `stream/cluster_refit.py`는 `model.common.cluster`의 backend 선택 로직을 공유한다. `auto`는 cuML/CUDA runtime probe가 통과하면 GPU를 사용하고, GPU가 불가하거나 실행 중 실패하면 CPU `umap-learn + hdbscan`으로 fallback한다.
- `stream/replay_pipeline.py`는 `trace_replay.py` entrypoint로 동작한다. `--speed N`은 `scheduledAt = wallStart + (ratedAtTs - firstRatedAtTs) / N` 기준으로 event를 주입한다.
- `stream/replay_pipeline.py --recommend`는 각 event 처리 이후 `stream/recommend_online.py`를 호출하고 replay scope 안의 `stream_recommendations.jsonl`에 append한다.
- 현재 GPU 검증된 `.venv` 조합은 `torch==2.5.1+cu121`, RAPIDS/cuML `25.10.0`, `cuda-toolkit==12.1.1`, `cupy-cuda12x==13.6.0`, `scikit-learn==1.7.2`다. 버저닝 결정은 `docs/decisions/0003-pin-rapids-cuml-gpu-dependencies.md`를 따른다.
- 실행 로그는 `outputs/logs/<script>_YYYYmmdd_HHMMSS.log`에 저장된다.

---

## 실험 메타데이터

- `python3 -m model.batch.train`는 `--run-id`가 없으면 timestamp 기반 run id를 새로 만들고 `outputs/latest_model_run_id.txt`에 기록한다.
- `python3 -m model.batch.extract_canonical`, `python3 -m model.batch.cluster`, `python3 -m model.batch.recommend`, `python3 -m model.stream.recommend_online`은 `--run-id`가 없으면 `outputs/latest_model_run_id.txt`의 run id를 이어받는다.
- `python3 -m model.stream.replay_pipeline`은 `ingress_events.jsonl`, event-level `replay_events.jsonl`, `replay_summary.json`, 선택적 `stream_recommendations.jsonl` metadata를 run별 manifest/metrics에 기록한다.
- run별 메타데이터는 `experiments/model/<run_id>/` 아래에 저장된다.
- `manifest.json`에는 command, git 상태, 입력 파일 metadata, 스키마 버전, config, 출력 ref를 기록한다.
- `metrics.jsonl`에는 epoch별 학습 지표와 extract/cluster/refit/recommend/replay summary를 append한다.
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
| train: epochs | 100 | 최대 학습 epoch 수 |
| train: eval_every | 5 | validation 평가 주기 |
| train: patience | 10 | Recall@10 기준 early stopping patience (평가 횟수 기준) |
| train: min_interactions | 200 | 학습 유저 필터링 기준 |
| train: stride | 50 | 학습용 슬라이딩 윈도우 간격 |
| extract_canonical: min_interactions | 1000 | canonical event embedding 대상 유저 필터링 기준 |
| extract_canonical: output | `outputs/canonical_embeddings.npz` | event당 embedding 1개를 저장하는 기본 출력 경로 |
| stream: min_ratings_for_zscore | 3 | online positive projection에서 z-score를 적용하기 전 optimistic cold-start 기준 |
| stream: output | `outputs/stream/online_embeddings.npz` | active positive online embedding 기본 출력 경로 |
| stream: state_dir | `outputs/stream/user_states/` | user별 raw event state JSON 저장 경로 |
| interest_assign: similarity_threshold | 0.2 | nearest interest cosine similarity가 이 값보다 낮으면 outlier |
| interest_assign: refit_min_events | 20 | no-interest/pending event 기반 refit request 최소 이벤트 수 |
| interest_assign: assign_trigger_count | 50 | refit 이후 assign 누적 수 기반 refit request 기준 |
| interest_assign: outlier_trigger_count | 10 | outlier 누적 수 기반 refit request 기준 |
| cluster_refit: cluster_backend | `auto` | cuML/CUDA runtime 사용 가능 시 GPU, 아니면 CPU fallback |
| cluster_refit: refit_min_events | 20 | refit 실행 최소 active embedding 수 |
| cluster_refit: min_cluster_size | 10 | HDBSCAN 최소 클러스터 크기 |
| replay: output_root | `outputs/stream/replay_demo` | trace replay 산출물 격리 경로 |
| replay: speed | 1.0 | trace timestamp를 wall-clock으로 압축하는 배속. `100`이면 trace 100초가 실제 1초 |
| recommend: top_k | 20 | batch/stream recommendation 기본 후보 수 |
| recommend: normalize | false | 기본 raw dot product 사용. true면 cosine-normalized dot product 사용 |
| cluster: cluster_n_components | 10 | HDBSCAN 입력 UMAP 차원 |
| cluster: viz_n_components | 3 | 시각화용 UMAP 차원 |
| cluster: min_cluster_size | 10 | HDBSCAN 최소 클러스터 크기 |

---

## 산출물

모델 가중치/임베딩/시각화 산출물은 프로젝트 루트의 `outputs/`에 저장된다. `outputs/readme.md`와 `outputs/logs/*.log`는 실행 기록 보존용으로 추적될 수 있고, 가중치/임베딩/플롯은 git 추적에서 제외한다.

| 파일 | 설명 |
|---|---|
| `outputs/sasrec_cl.pt` | 마지막 epoch 모델 가중치 |
| `outputs/sasrec_cl_best.pt` | validation Recall@10 기준 best 모델 가중치 |
| `outputs/item2idx.json` | 아이템 ID → 인덱스 매핑 (학습 vocabulary) |
| `outputs/canonical_embeddings.npz` | canonical event embedding `embeddings(N,128)`, `user_ids(N,)`, `event_idx(N,)`, `movie_ids(N,)`, `rated_at_ts(N,)`, `rated_at_iso(N,)`, `history_len(N,)`, `context_start_idx(N,)` |
| `outputs/user_interests.npz` | batch cluster visualization/export source. `labels_user_ids`, `labels_timepoints`, `labels`, `umap_z`, `win_*` 배열을 저장 |
| `outputs/batch/interest_states/{user_id}.json` | 배치 클러스터링 결과 interest state. interest vector와 `interests[k].topGenres`에 클러스터별 상위 장르 포함 |
| `outputs/recommendations.csv` | batch recommendation table. `batch/recommend.py` 실행 시 생성 |
| `outputs/recommendations.npz` | batch recommendation arrays. `batch/recommend.py` 실행 시 생성 |
| `outputs/stream/user_states/{user_id}.json` | user별 raw rating event와 현재 positive projection state |
| `outputs/stream/online_embeddings.npz` | active positive online embedding `embeddings(N,128)`, `user_ids(N,)`, `raw_event_ids(N,)`, `event_idx(N,)`, `movie_ids(N,)`, `rated_at_ts(N,)`, `rated_at_iso(N,)`, `history_len(N,)`, `context_start_idx(N,)`, `status(N,)` |
| `outputs/stream/online_embedding_events.jsonl` | online ingest/extract run summary event log |
| `outputs/stream/interest_states/{user_id}.json` | user별 interest vectors, pending raw event ids, assignment/refit trigger state |
| `outputs/stream/interest_assignments.jsonl` | online embedding별 assignment/pending/outlier 결과 log |
| `outputs/stream/refit_requests.jsonl` | Phase 4-1 이후 refit backend가 소비할 open refit request log |
| `outputs/stream/refit_events.jsonl` | refit request 소비/skip/close 결과 log |
| `outputs/stream/stream_recommendations.jsonl` | `stream/recommend_online.py` 단독 실행 추천 결과 |
| `outputs/stream/replay_demo/replay_input_events.jsonl` | C++ replay generator가 만든 timestamp-sorted rating event stream |
| `outputs/stream/replay_demo/ingress_events.jsonl` | trace scheduler가 event를 emit한 시각과 `scheduledAt`/`injectorLagSec` log |
| `outputs/stream/replay_demo/replay_events.jsonl` | event-level replay progress, processing latency, lag, assignment/refit count log |
| `outputs/stream/replay_demo/replay_summary.json` | dashboard가 읽는 trace replay run summary entrypoint |
| `outputs/stream/replay_demo/user_states/{user_id}.json` | replay run에 격리된 user raw/positive state |
| `outputs/stream/replay_demo/interest_states/{user_id}.json` | replay run에 격리된 user interest state |
| `outputs/stream/replay_demo/online_embeddings.npz` | replay run의 active positive online embedding |
| `outputs/stream/replay_demo/interest_assignments.jsonl` | replay run의 assignment/pending/outlier 결과 log |
| `outputs/stream/replay_demo/refit_requests.jsonl` | replay run의 refit request log |
| `outputs/stream/replay_demo/refit_events.jsonl` | replay run의 refit close/skip 결과 log |
| `outputs/stream/replay_demo/stream_recommendations.jsonl` | `replay_pipeline --recommend` 실행 시 replay scope에 append되는 추천 결과 |
| `outputs/embeddings.npz` | 삭제된 overlap-window extract entrypoint가 만들던 legacy 산출물. 현재 공식 경로는 `canonical_embeddings.npz` |
| `outputs/embeddings.npy` | 이전 추출 워크플로우에서 남은 legacy 산출물 |
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

**추천 평가 파이프라인**
`batch/recommend.py`와 `stream/recommend_online.py`는 top-K 후보를 생성하지만 Recall@K/NDCG@K 평가 파이프라인은 아직 없다.

**batch recommendation 입력 계약 정리**
현재 `batch/cluster.py`는 interest vector를 `outputs/batch/interest_states/{user_id}.json`에 저장하고, `outputs/user_interests.npz`는 dashboard/export용 label/UMAP 배열 중심이다. `batch/recommend.py`는 interest vector 배열이 들어 있는 NPZ 포맷을 읽도록 구현되어 있어, 현재 batch cluster 산출물과 바로 연결하려면 JSON interest state loader 또는 별도 export가 필요하다. Streaming/replay 추천은 `stream/recommend_online.py`가 현재 주 경로다.

**online positive policy 고도화**
Phase 3 stream path는 raw rating을 모두 저장하고, 현재까지 관측된 user history 기준 z-score positive projection을 재검증한다. 실제 서비스 정책에서는 threshold, running statistics, 유보 상태, latency budget을 추가 비교해야 한다.

**데이터 포맷 검증**
`movies_processed_drop.csv`의 genres 컬럼 형태, `ratings_drop_processed.jsonl` 스키마가 실제 파일과 일치하는지 확인 필요
