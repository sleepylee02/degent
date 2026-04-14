# Data Flow

이 문서는 원본 데이터에서 대시보드까지 이어지는 흐름을 빠르게 파악하기 위한 보조 문서다. 프로젝트 운영 규칙의 정본은 `PROJECT_GUIDE.md`다.

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
  -> model/train.py
  -> outputs/sasrec_cl.pt + outputs/item2idx.json
  -> experiments/model/<run_id>/manifest.json + metrics.jsonl
  -> model/extract.py
  -> outputs/embeddings.npz
  -> experiments/model/<run_id>/manifest.json + metrics.jsonl
  -> model/cluster.py
  -> outputs/user_interests.npz
  -> experiments/model/<run_id>/manifest.json + metrics.jsonl
  -> model/visualize_clusters.py
  -> outputs/viz/
  -> dashboard input export
  -> data/clustering/user_clusters.parquet
  -> dashboard/cluster_dashboard.py
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
python model/train.py
python model/extract.py
python model/cluster.py
python model/visualize_clusters.py
```

주요 출력:

- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- `outputs/embeddings.npz`
- `outputs/user_interests.npz`
- `outputs/viz/`
- `outputs/logs/`
- `experiments/model/<run_id>/manifest.json`
- `experiments/model/<run_id>/metrics.jsonl`
- `experiments/model/<run_id>/notes.md`

세부 실행 옵션은 `model/README.md`를 따른다.

모델 대형 산출물은 `outputs/`에 두고 git으로 추적하지 않는다. run별 비교에 필요한 command, git 상태, 입력/출력 metadata, config, metric은 `experiments/model/<run_id>/`에 남긴다.

## 7. Dashboard input

현재 모델 클러스터링 산출물은 `outputs/user_interests.npz`이고, 대시보드 기본 입력은 테이블 파일이다.

기본 입력 경로:

- `data/clustering/user_clusters.parquet`

대시보드 입력 파일은 `dashboard/README.md`의 입력 스키마를 따른다. 실제 결과 파일이 없으면 대시보드에서 demo 데이터를 사용해 UI를 먼저 확인할 수 있다.

## 8. Dashboard

```bash
streamlit run dashboard/cluster_dashboard.py
```

세부 입력 계약은 `dashboard/README.md`를 따른다.
