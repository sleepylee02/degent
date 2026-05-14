# Data Flow

이 문서는 원본 데이터에서 대시보드까지 이어지는 흐름을 빠르게 파악하기 위한 보조 문서다. 프로젝트 운영 규칙의 정본은 `PROJECT_GUIDE.md`다. 현재 batch / streaming 구현 상태와 문제 포인트를 구체적으로 보려면 `docs/current-pipeline-snapshot.md`를 먼저 본다. 파트별 담당 파일, input, output, endpoint는 `docs/part-contracts.md`를 따른다. Streaming replay e2e를 직접 실행하고 단계별 artifact를 확인하려면 `docs/streaming-e2e-pipeline.md`를 본다.

## 전체 흐름

```text
data/**/raw/
  -> preprocess/preprocess_movie/preprocess_movies.py
  -> data/movies_processed.csv
  -> preprocess/drop_movie/drop_movies.py
  -> data/movies_processed_drop.csv
  -> preprocess/drop_rating/drop_ratings.py
  -> data/ratings_drop.csv
  -> preprocess/process_rating/process_ratings_drop.py
  -> data/ratings_drop_processed.jsonl
  -> python3 -m model.batch.train
  -> outputs/sasrec_cl.pt + outputs/item2idx.json
  -> experiments/model/<run_id>/manifest.json + metrics.jsonl
     -> batch cluster/dashboard branch:
        -> python3 -m model.batch.extract_canonical
        -> outputs/canonical_embeddings.npz
        -> experiments/model/<run_id>/manifest.json + metrics.jsonl
        -> python3 -m model.batch.cluster
        -> outputs/user_interests.npz
        -> outputs/batch/interest_states/{user_id}.json
        -> experiments/model/<run_id>/manifest.json + metrics.jsonl
        -> python3 -m model.batch.export_clusters
        -> data/clustering/user_clusters.parquet
        -> python3 -m model.batch.visualize_clusters
        -> outputs/viz/
        -> dashboard/cluster_dashboard.py Cluster explorer
     -> streaming/replay/recommend branch:
        -> python3 -m model.stream.extract_online
        -> outputs/stream/user_states/{user_id}.json
        -> outputs/stream/online_embeddings.npz
        -> outputs/stream/online_embedding_events.jsonl
        -> experiments/model/<run_id>/manifest.json + metrics.jsonl
        -> python3 -m model.stream.interest_assign
        -> outputs/stream/interest_states/{user_id}.json
        -> outputs/stream/interest_assignments.jsonl
        -> outputs/stream/refit_requests.jsonl
        -> experiments/model/<run_id>/manifest.json + metrics.jsonl
        -> python3 -m model.stream.cluster_refit
        -> outputs/stream/refit_events.jsonl
        -> outputs/stream/interest_states/{user_id}.json
        -> experiments/model/<run_id>/manifest.json + metrics.jsonl
        -> python3 -m model.stream.recommend_online
        -> outputs/stream/stream_recommendations.jsonl
        -> trace replay artifacts
        -> make -C replay
        -> replay/bin/rating_replay
        -> outputs/stream/replay_demo/replay_input_events.jsonl
        -> python3 -m model.stream.replay_pipeline --speed N
        -> outputs/stream/replay_demo/ingress_events.jsonl
        -> outputs/stream/replay_demo/replay_summary.json
        -> outputs/stream/replay_demo/replay_events.jsonl
        -> outputs/stream/replay_demo/user_states/{user_id}.json
        -> outputs/stream/replay_demo/online_embeddings.npz
        -> outputs/stream/replay_demo/interest_assignments.jsonl
        -> outputs/stream/replay_demo/refit_requests.jsonl
        -> outputs/stream/replay_demo/refit_events.jsonl
        -> outputs/stream/replay_demo/interest_states/{user_id}.json
        -> outputs/stream/replay_demo/stream_recommendations.jsonl  (when --recommend)
        -> dashboard/cluster_dashboard.py Replay monitor
```

보조 장르 산출물 흐름:

```text
data/ml-32m/raw/movies.csv
  -> preprocess/preprocess_genre/preprocess_genre.py
  -> data/ml-32m/genre.csv
```

## 1. Raw inputs

MovieLens 32M 원본 CSV는 `data/ml-32m/raw/` 아래에 둔다.

- `movies.csv`
- `ratings.csv`
- `tags.csv`
- `links.csv`

Genome 2021과 ML-32M extension은 보조 데이터로 `data/genome_2021/`, `data/ml-32m-extension-main/` 아래에 둔다. 원본 파일은 직접 수정하지 않는다.

## 2. Movie preprocessing

```bash
python3 preprocess/preprocess_movie/preprocess_movies.py
```

입력:

- `data/ml-32m/raw/movies.csv`
- `data/ml-32m/raw/ratings.csv`
- `data/ml-32m/raw/tags.csv`
- `data/ml-32m/raw/links.csv`

출력:

- `data/movies_processed.csv`
- `preprocess/preprocess_movie/validation_report.json`
- `preprocess/preprocess_movie/bad_rows.csv`

## 3. Movie drop postprocess

```bash
python3 preprocess/drop_movie/drop_movies.py
```

입력:

- `data/movies_processed.csv`

출력:

- `data/movies_processed_drop.csv`
- `preprocess/drop_movie/validation_report.json`
- `preprocess/drop_movie/bad_rows.csv`

## 4. Rating drop propagation

```bash
python3 preprocess/drop_rating/drop_ratings.py
```

입력:

- `data/ml-32m/raw/ratings.csv`
- `data/movies_processed_drop.csv`

출력:

- `data/ratings_drop.csv`
- `preprocess/drop_rating/validation_report.json`
- `preprocess/drop_rating/bad_rows.csv`

## 5. Rating sequence processing

```bash
python3 preprocess/process_rating/process_ratings_drop.py
```

입력:

- `data/ratings_drop.csv`

출력:

- `data/ratings_drop_processed.jsonl`
- `preprocess/process_rating/validation_report.json`
- `preprocess/process_rating/bad_rows.csv`

## 6. Model pipeline

```bash
python3 -m model.batch.train
python3 -m model.batch.extract_canonical
python3 -m model.batch.cluster
python3 -m model.batch.export_clusters
python3 -m model.batch.visualize_clusters
python3 -m model.stream.extract_online
python3 -m model.stream.interest_assign
python3 -m model.stream.cluster_refit
python3 -m model.stream.recommend_online
make -C replay
python3 -m model.stream.replay_pipeline --generate-events --speed 100 --recommend
```

주요 출력:

- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- `outputs/canonical_embeddings.npz`
- `outputs/user_interests.npz`
- `outputs/batch/interest_states/{user_id}.json`
- `data/clustering/user_clusters.parquet`
- `outputs/stream/user_states/{user_id}.json`
- `outputs/stream/online_embeddings.npz`
- `outputs/stream/online_embedding_events.jsonl`
- `outputs/stream/interest_states/{user_id}.json`
- `outputs/stream/interest_assignments.jsonl`
- `outputs/stream/refit_requests.jsonl`
- `outputs/stream/refit_events.jsonl`
- `outputs/stream/stream_recommendations.jsonl`
- `outputs/stream/replay_demo/replay_input_events.jsonl`
- `outputs/stream/replay_demo/ingress_events.jsonl`
- `outputs/stream/replay_demo/replay_summary.json`
- `outputs/stream/replay_demo/replay_events.jsonl`
- `outputs/stream/replay_demo/user_states/{user_id}.json`
- `outputs/stream/replay_demo/online_embeddings.npz`
- `outputs/stream/replay_demo/interest_assignments.jsonl`
- `outputs/stream/replay_demo/refit_requests.jsonl`
- `outputs/stream/replay_demo/refit_events.jsonl`
- `outputs/stream/replay_demo/interest_states/{user_id}.json`
- `outputs/stream/replay_demo/stream_recommendations.jsonl`
- `outputs/viz/`
- `outputs/logs/`
- `experiments/model/<run_id>/manifest.json`
- `experiments/model/<run_id>/metrics.jsonl`
- `experiments/model/<run_id>/notes.md`

세부 실행 옵션은 `model/README.md`를 따른다.

모델 대형 산출물은 `outputs/`에 두고 git으로 추적하지 않는다. run별 비교에 필요한 command, git 상태, 입력/출력 metadata, config, metric은 `experiments/model/<run_id>/`에 남긴다.

`outputs/canonical_embeddings.npz`는 event 하나당 embedding 하나를 보장하는 batch 산출물이고 현재 `batch/cluster.py`의 기본 입력이다. 과거 overlap-window 추출 산출물인 `outputs/embeddings.npz`는 legacy artifact로만 취급한다. `outputs/stream/online_embeddings.npz`는 raw rating을 모두 user state에 저장한 뒤 현재까지 관측된 positive projection에서 생성한 active online embedding이다.

`outputs/stream/interest_assignments.jsonl`과 `outputs/stream/refit_requests.jsonl`은 active online embedding을 interest state에 연결하기 위한 stream 산출물이다. Phase 4는 refit request만 기록하고 실제 UMAP/HDBSCAN refit은 실행하지 않는다.

`outputs/stream/refit_events.jsonl`은 Phase 4-1 triggered refit backend의 close/skip 로그다. refit backend는 request user의 active embedding 전체를 다시 clustering하고 `interest_states/{user_id}.json`의 interest vectors를 replace한다. `--cluster-backend auto`는 cuML import와 CUDA runtime probe가 통과하면 GPU를 사용한다. GPU가 불가하거나 `auto` GPU refit 실행이 실패하면 CPU `umap-learn + hdbscan`으로 fallback한다.

`outputs/stream/stream_recommendations.jsonl`은 current streaming interest state에서 생성한 top-K 추천 결과다. Trace replay에서 `--recommend`를 사용하면 같은 추천 결과가 `outputs/stream/replay_demo/stream_recommendations.jsonl`에 격리된다.

Trace replay artifact는 `outputs/stream/replay_demo/` 아래에 저장된다. `replay/bin/rating_replay`은 `ratings_drop_processed.jsonl`을 timestamp-sorted event stream으로 변환하고, `python3 -m model.stream.replay_pipeline --speed N`은 이 입력을 `scheduledAt = wallStart + (ratedAtTs - firstRatedAtTs) / N` 기준으로 event 단위 주입한다. 각 event 처리 후 `extract_online -> interest_assign -> cluster_refit`을 호출하고, `--recommend` 사용 시 `recommend_online`도 호출한다. Dashboard는 `replay_summary.json`을 stable entrypoint로 읽고, summary의 `paths`가 있으면 해당 경로를 우선 사용한다. Replay dashboard는 reader이며 replay artifact를 생성하거나 수정하지 않는다. 세부 계약은 `docs/streaming-replay-dashboard-contract.md`를 따른다.

## 7. Dashboard input

현재 모델 클러스터링 산출물은 `outputs/user_interests.npz`이고, cluster explorer의 기본 입력은 테이블 파일이다.

기본 입력 경로:

- `data/clustering/user_clusters.parquet`

현재 저장소에는 `outputs/user_interests.npz`를 위 테이블 포맷으로 변환하는 `model/batch/export_clusters.py`가 있다.

```bash
python3 -m model.batch.export_clusters \
  --input outputs/user_interests.npz \
  --output data/clustering/user_clusters.parquet
```

Cluster explorer 입력 파일은 `dashboard/README.md`의 입력 스키마를 따른다. 실제 결과 파일이 없으면 대시보드에서 demo 데이터를 사용해 UI를 먼저 확인할 수 있다.

Replay monitor 입력은 trace replay artifact다.

- `outputs/stream/replay_demo/replay_summary.json`
- `outputs/stream/replay_demo/ingress_events.jsonl`
- `outputs/stream/replay_demo/replay_events.jsonl`
- `outputs/stream/replay_demo/interest_assignments.jsonl`
- `outputs/stream/replay_demo/refit_requests.jsonl`
- `outputs/stream/replay_demo/refit_events.jsonl`
- `outputs/stream/replay_demo/interest_states/{user_id}.json`
- `outputs/stream/replay_demo/stream_recommendations.jsonl`

## 8. Dashboard

```bash
streamlit run dashboard/cluster_dashboard.py
```

세부 입력 계약은 `dashboard/README.md`와 `docs/streaming-replay-dashboard-contract.md`를 따른다.
