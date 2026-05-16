# Artifacts

이 문서는 주요 원본 데이터와 생성물의 소유권을 빠르게 확인하기 위한 보조 문서다. 프로젝트 운영 규칙의 정본은 `PROJECT_GUIDE.md`다.

## 규칙

- `data/**/raw/` 아래 파일은 원본 데이터이므로 직접 수정하지 않는다.
- 전처리 생성물은 직접 편집하지 않고 해당 스크립트로 재생성한다.
- 스키마가 바뀌는 작업은 `schemas/`를 먼저 수정한다.
- 산출물 경로나 생성 규칙이 바뀌면 `PROJECT_GUIDE.md`도 함께 업데이트한다.
- Python dependency 변경은 repo-local `.venv`에서만 수행하고 `requirements.txt`에 반영한다. GPU dependency 버저닝 결정은 `docs/decisions/0003-pin-rapids-cuml-gpu-dependencies.md`를 따른다.

## 주요 파일

| path | type | produced by | editable |
|---|---|---|---|
| `data/ml-32m/raw/movies.csv` | raw input | manual local placement | no |
| `data/ml-32m/raw/ratings.csv` | raw input | manual local placement | no |
| `data/ml-32m/raw/tags.csv` | raw input | manual local placement | no |
| `data/ml-32m/raw/links.csv` | raw input | manual local placement | no |
| `data/ml-32m/genre.csv` | auxiliary generated data | `(cd preprocess/preprocess_genre && python3 preprocess_genre.py)` | regenerate |
| `data/genome_2021/raw/` | raw input | manual local placement | no |
| `data/ml-32m-extension-main/raw/` | raw input | manual local placement | no |
| `data/movies_processed.csv` | generated data | `python3 preprocess/preprocess_movie/preprocess_movies.py` | no |
| `data/movies_processed_drop.csv` | generated data | `python3 preprocess/drop_movie/drop_movies.py` | no |
| `data/ratings_drop.csv` | generated data | `python3 preprocess/drop_rating/drop_ratings.py` | no |
| `data/ratings_drop_processed.jsonl` | generated data | `python3 preprocess/process_rating/process_ratings_drop.py` | no |
| `preprocess/preprocess_movie/validation_report.json` | validation artifact | `python3 preprocess/preprocess_movie/preprocess_movies.py` | regenerate |
| `preprocess/preprocess_movie/bad_rows.csv` | validation artifact | `python3 preprocess/preprocess_movie/preprocess_movies.py` | regenerate |
| `preprocess/drop_movie/validation_report.json` | validation artifact | `python3 preprocess/drop_movie/drop_movies.py` | regenerate |
| `preprocess/drop_movie/bad_rows.csv` | validation artifact | `python3 preprocess/drop_movie/drop_movies.py` | regenerate |
| `preprocess/drop_rating/validation_report.json` | validation artifact | `python3 preprocess/drop_rating/drop_ratings.py` | regenerate |
| `preprocess/drop_rating/bad_rows.csv` | validation artifact | `python3 preprocess/drop_rating/drop_ratings.py` | regenerate |
| `preprocess/process_rating/validation_report.json` | validation artifact | `python3 preprocess/process_rating/process_ratings_drop.py` | regenerate |
| `preprocess/process_rating/bad_rows.csv` | validation artifact | `python3 preprocess/process_rating/process_ratings_drop.py` | regenerate |
| `outputs/sasrec_cl.pt` | model artifact | `python3 -m model.batch.train` | regenerate |
| `outputs/sasrec_cl_best.pt` | model artifact | `python3 -m model.batch.train` | regenerate |
| `outputs/item2idx.json` | model artifact | `python3 -m model.batch.train` | regenerate |
| `outputs/pre/temporal_2022/sasrec_cl.pt` | temporal model artifact | `python3 -m model.batch.train --max-rated-at-exclusive 2022-01-01T00:00:00Z --output-dir outputs/pre/temporal_2022` | regenerate |
| `outputs/pre/temporal_2022/sasrec_cl_best.pt` | temporal model artifact | `python3 -m model.batch.train --max-rated-at-exclusive 2022-01-01T00:00:00Z --output-dir outputs/pre/temporal_2022` | regenerate |
| `outputs/pre/temporal_2022/item2idx.json` | temporal model artifact | `python3 -m model.batch.train --max-rated-at-exclusive 2022-01-01T00:00:00Z --output-dir outputs/pre/temporal_2022` | regenerate |
| `outputs/embeddings.npz` | legacy model artifact | removed overlap-window extract workflow | no new writes |
| `outputs/canonical_embeddings.npz` | model artifact | `python3 -m model.batch.extract_canonical` | regenerate |
| `outputs/pre/temporal_2022/canonical_embeddings.npz` | temporal model artifact | `python3 -m model.batch.extract_canonical --max-rated-at-exclusive 2022-01-01T00:00:00Z --checkpoint outputs/pre/temporal_2022/sasrec_cl.pt --item2idx outputs/pre/temporal_2022/item2idx.json` | regenerate |
| `outputs/user_interests.npz` | batch cluster export source | `python3 -m model.batch.cluster` | regenerate |
| `outputs/batch/state.sqlite` | batch interest state store | `python3 -m model.batch.cluster` | regenerate |
| `outputs/pre/temporal_2022/user_interests.npz` | temporal batch cluster export source | `python3 -m model.batch.cluster --embeddings outputs/pre/temporal_2022/canonical_embeddings.npz --output outputs/pre/temporal_2022/user_interests.npz --state-db outputs/pre/temporal_2022/state.sqlite` | regenerate |
| `outputs/pre/temporal_2022/state.sqlite` | temporal pre-T user/interest seed state store with compressed user payload and no pre event row materialization | `python3 -m model.stream.seed_pre_t_state --state-db outputs/pre/temporal_2022/state.sqlite --max-rated-at-exclusive 2022-01-01T00:00:00Z` | regenerate |
| `outputs/pre/temporal_2022/pre_summary.json` | temporal pre-T state seed summary | `python3 -m model.stream.seed_pre_t_state --state-db outputs/pre/temporal_2022/state.sqlite --summary outputs/pre/temporal_2022/pre_summary.json` | regenerate |
| `outputs/recommendations.csv` | batch recommendation table | `python3 -m model.batch.recommend` | regenerate |
| `outputs/recommendations.npz` | batch recommendation arrays | `python3 -m model.batch.recommend` | regenerate |
| `outputs/stream/state.sqlite` | standalone streaming state store when explicitly used | `python3 -m model.stream.extract_online --state-db outputs/stream/state.sqlite` | regenerate |
| `outputs/stream/online_embeddings.npz` | streaming model artifact | `python3 -m model.stream.extract_online` | regenerate |
| `outputs/stream/online_embedding_events.jsonl` | streaming run log | `python3 -m model.stream.extract_online` | append/regenerate |
| `outputs/stream/interest_assignments.jsonl` | streaming assignment log | `python3 -m model.stream.interest_assign` | append/regenerate |
| `outputs/stream/refit_requests.jsonl` | streaming refit request log | `python3 -m model.stream.interest_assign` | append/regenerate |
| `outputs/stream/refit_events.jsonl` | streaming refit event log | `python3 -m model.stream.cluster_refit` | append/regenerate |
| `outputs/stream/stream_recommendations.jsonl` | streaming recommendation log | `python3 -m model.stream.recommend_online` | append/regenerate |
| `outputs/stream/replay_demo/replay_input_events.jsonl` | replay input event stream | `replay/bin/rating_replay` 또는 `python3 -m model.stream.replay_pipeline --generate-events` | regenerate |
| `outputs/stream/replay_demo/ingress_events.jsonl` | trace replay event emit log with schedule/lag | `python3 -m model.stream.replay_pipeline --speed N` | append/regenerate |
| `outputs/stream/replay_demo/replay.sqlite` | SQLite runtime/state store for trace replay run/event/stage/state/refit/embedding index | `python3 -m model.stream.replay_pipeline --speed N` | regenerate |
| `outputs/stream/replay_demo/replay_summary.json` | trace replay dashboard entrypoint | `python3 -m model.stream.replay_pipeline --speed N` | regenerate |
| `outputs/stream/replay_demo/replay_events.jsonl` | event-level trace replay progress and lag log | `python3 -m model.stream.replay_pipeline --speed N` | append/regenerate |
| `outputs/stream/replay_demo/online_embeddings.npz` | replay-scoped online embeddings | `python3 -m model.stream.replay_pipeline` | regenerate |
| `outputs/stream/replay_demo/interest_assignments.jsonl` | replay-scoped assignment log | `python3 -m model.stream.replay_pipeline` | append/regenerate |
| `outputs/stream/replay_demo/refit_requests.jsonl` | replay-scoped refit request log | `python3 -m model.stream.replay_pipeline` | append/regenerate |
| `outputs/stream/replay_demo/refit_events.jsonl` | replay-scoped refit event log | `python3 -m model.stream.replay_pipeline` | append/regenerate |
| `outputs/stream/replay_demo/stream_recommendations.jsonl` | replay-scoped recommendation log | `python3 -m model.stream.replay_pipeline --recommend` | append/regenerate |
| `outputs/post/temporal_2022_events_<N>/` | temporal post-T replay artifact root | `python3 -m model.stream.replay_pipeline --output-root outputs/post/temporal_2022_events_<N> --seed-state-db outputs/pre/temporal_2022/state.sqlite --seed-run-id temporal_2022 --start-rated-at 2022-01-01T00:00:00Z` | regenerate |
| `outputs/post/temporal_2022_events_<N>/replay.sqlite` | temporal post-T SQLite runtime/state store with touched state | `python3 -m model.stream.replay_pipeline --output-root outputs/post/temporal_2022_events_<N>` | regenerate |
| `outputs/post/temporal_2022_events_<N>/replay_summary.json` | temporal post-T replay summary entrypoint | `python3 -m model.stream.replay_pipeline --output-root outputs/post/temporal_2022_events_<N>` | regenerate |
| `outputs/post/temporal_2022_events_<N>/online_embeddings.npz` | temporal post-T replay-scoped online embeddings | `python3 -m model.stream.replay_pipeline --output-root outputs/post/temporal_2022_events_<N>` | regenerate |
| `outputs/embeddings.npy` | legacy model artifact | previous extract workflow | no new writes |
| `outputs/viz/` | visualization artifact | `python3 -m model.batch.visualize_clusters` | regenerate |
| `outputs/logs/` | local runtime logs, git ignored except `.gitkeep` | model scripts | append/regenerate |
| `outputs/latest_model_run_id.txt` | local run pointer | model scripts | regenerate |
| `experiments/model/<run_id>/manifest.json` | local experiment metadata, git ignored | model scripts | append/update |
| `experiments/model/<run_id>/metrics.jsonl` | local experiment metrics, git ignored | model scripts | append |
| `experiments/model/<run_id>/notes.md` | local experiment notes, git ignored | model scripts / manual note | edit |
| `requirements.txt` | Python dependency lock | repo-local `.venv` / `.venv/bin/pip freeze` | edit/regenerate |
| `data/clustering/user_clusters.parquet` | dashboard input | `python3 -m model.batch.export_clusters` | regenerate |
| `eda/eda_outputs/` | raw EDA artifacts | `python3 -m eda.raw.eda_overview --source all` | regenerate |
| `eda/raw/outputs/` | legacy raw EDA artifacts | previous raw EDA workflow | no new writes |
| `eda/processed/outputs/` | processed EDA artifacts | `python3 eda/processed/eda_processed.py` | regenerate |

## 생성 순서

```bash
python3 preprocess/preprocess_movie/preprocess_movies.py
python3 preprocess/drop_movie/drop_movies.py
python3 preprocess/drop_rating/drop_ratings.py
python3 preprocess/process_rating/process_ratings_drop.py
(cd preprocess/preprocess_genre && python3 preprocess_genre.py)
python3 -m model.batch.train
python3 -m model.batch.extract_canonical
python3 -m model.batch.cluster
python3 -m model.stream.seed_pre_t_state --max-rated-at-exclusive 2022-01-01T00:00:00Z
python3 -m model.batch.export_clusters
python3 -m model.batch.visualize_clusters
python3 -m model.stream.extract_online --bootstrap-user-id <userId>
python3 -m model.stream.interest_assign
python3 -m model.stream.cluster_refit
python3 -m model.stream.recommend_online
make -C replay
python3 -m model.stream.replay_pipeline --generate-events --speed 100 --recommend
streamlit run dashboard/cluster_dashboard.py
```
