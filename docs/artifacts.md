# Artifacts

이 문서는 주요 원본 데이터와 생성물의 소유권을 빠르게 확인하기 위한 보조 문서다. 프로젝트 운영 규칙의 정본은 `PROJECT_GUIDE.md`다.

## 규칙

- `data/**/raw/` 아래 파일은 원본 데이터이므로 직접 수정하지 않는다.
- 전처리 생성물은 직접 편집하지 않고 해당 스크립트로 재생성한다.
- 스키마가 바뀌는 작업은 `schemas/`를 먼저 수정한다.
- 산출물 경로나 생성 규칙이 바뀌면 `PROJECT_GUIDE.md`도 함께 업데이트한다.

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
| `outputs/sasrec_cl.pt` | model artifact | `python model/train.py` | regenerate |
| `outputs/item2idx.json` | model artifact | `python model/train.py` | regenerate |
| `outputs/embeddings.npz` | model artifact | `python model/extract.py` | regenerate |
| `outputs/embeddings.npy` | legacy model artifact | previous extract workflow | no new writes |
| `outputs/user_interests.npz` | model artifact | `python model/cluster.py` | regenerate |
| `outputs/viz/` | visualization artifact | `python model/visualize_clusters.py` | regenerate |
| `outputs/logs/` | tracked runtime logs | model scripts | append/regenerate |
| `outputs/latest_model_run_id.txt` | local run pointer | model scripts | regenerate |
| `experiments/model/<run_id>/manifest.json` | experiment metadata | model scripts | append/update |
| `experiments/model/<run_id>/metrics.jsonl` | experiment metrics | model scripts | append |
| `experiments/model/<run_id>/notes.md` | experiment notes | model scripts / manual note | edit |
| `data/clustering/user_clusters.parquet` | dashboard input | export step from clustering/model result, not implemented yet | regenerate |
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
python model/train.py
python model/extract.py
python model/cluster.py
python model/visualize_clusters.py
# add/run an explicit export step for data/clustering/user_clusters.parquet when using the dashboard with real data
streamlit run dashboard/cluster_dashboard.py
```
