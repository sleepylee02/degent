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
  -> python3 -m model.batch.train
  -> outputs/sasrec_cl.pt + outputs/item2idx.json
  -> experiments/model/<run_id>/manifest.json + metrics.jsonl
     -> legacy branch:
        -> python3 -m model.batch.extract
        -> outputs/embeddings.npz
        -> experiments/model/<run_id>/manifest.json + metrics.jsonl
        -> python3 -m model.batch.cluster
        -> outputs/user_interests.npz
        -> experiments/model/<run_id>/manifest.json + metrics.jsonl
        -> python3 -m model.batch.visualize_clusters
        -> outputs/viz/
        -> dashboard input export (not implemented yet)
        -> data/clustering/user_clusters.parquet
        -> dashboard/cluster_dashboard.py
     -> streaming/replay contract branch:
        -> python3 -m model.batch.extract_canonical
        -> outputs/canonical_embeddings.npz
        -> experiments/model/<run_id>/manifest.json + metrics.jsonl
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
python3 -m model.batch.extract
python3 -m model.batch.extract_canonical
python3 -m model.batch.cluster
python3 -m model.batch.visualize_clusters
```

주요 출력:

- `outputs/sasrec_cl.pt`
- `outputs/item2idx.json`
- `outputs/embeddings.npz`
- `outputs/canonical_embeddings.npz`
- `outputs/user_interests.npz`
- `outputs/viz/`
- `outputs/logs/`
- `experiments/model/<run_id>/manifest.json`
- `experiments/model/<run_id>/metrics.jsonl`
- `experiments/model/<run_id>/notes.md`

세부 실행 옵션은 `model/README.md`를 따른다.

모델 대형 산출물은 `outputs/`에 두고 git으로 추적하지 않는다. run별 비교에 필요한 command, git 상태, 입력/출력 metadata, config, metric은 `experiments/model/<run_id>/`에 남긴다.

`outputs/embeddings.npz`는 기존 overlap-window 추출 산출물이고, `outputs/canonical_embeddings.npz`는 streaming/replay 전환을 위해 event 하나당 embedding 하나를 보장하는 산출물이다. 현재 `batch/cluster.py`는 아직 legacy `embeddings.npz`를 입력으로 사용한다.

## 7. Dashboard input

현재 모델 클러스터링 산출물은 `outputs/user_interests.npz`이고, 대시보드 기본 입력은 테이블 파일이다.

기본 입력 경로:

- `data/clustering/user_clusters.parquet`

현재 저장소에는 `outputs/user_interests.npz`를 위 테이블 포맷으로 변환하는 export 스크립트가 없다. 새 파이프라인에서 실제 모델 결과를 대시보드에 연결하려면 이 단계를 명시적으로 추가한다.

대시보드 입력 파일은 `dashboard/README.md`의 입력 스키마를 따른다. 실제 결과 파일이 없으면 대시보드에서 demo 데이터를 사용해 UI를 먼저 확인할 수 있다.

## 8. Dashboard

```bash
streamlit run dashboard/cluster_dashboard.py
```

세부 입력 계약은 `dashboard/README.md`를 따른다.
