# degent

영화 추천 시스템 연구 프로젝트다. MovieLens 32M을 주 상호작용 로그로 사용하고, Genome 2021과 ML-32M 확장 데이터를 보조 신호로 활용해 전처리, 품질 필터링, user-sequence 생성, EDA를 수행한다.

이 프로젝트의 운영 규칙과 디렉터리 정책은 `PROJECT_GUIDE.md`가 정본이다. 작업을 시작하기 전에 먼저 읽는 것을 전제로 한다.

## 프로젝트 범위

- 목적: 학습 가능한 영화 추천용 데이터셋을 일관된 스키마와 파이프라인으로 정리한다.
- 주 데이터: `data/ml-32m/raw/`
- 보조 데이터: `data/genome_2021/`, `data/ml-32m-extension-main/`
- 주요 산출물:
  - `data/movies_processed.csv`
  - `data/movies_processed_drop.csv`
  - `data/ratings_drop.csv`
  - `data/ratings_drop_processed.jsonl`

## 저장소 구조

```text
degent/
├── data/
│   ├── docs/                         # 연구 제안서 등 참고 문서
│   ├── ml-32m/                       # MovieLens 32M
│   ├── ml-32m-extension-main/        # ML-32M extension
│   ├── genome_2021/                  # Genome 2021
│   ├── movies_processed.csv          # 영화 통합 전처리 결과
│   ├── movies_processed_drop.csv     # drop 규칙 반영 영화 데이터
│   ├── ratings_drop.csv              # drop 결과 반영 rating 데이터
│   └── ratings_drop_processed.jsonl  # user sequence JSONL
├── schemas/                          # 데이터 계약 정본
├── preprocess/                       # 전처리 및 후처리 스크립트
├── dashboard/                        # 클러스터링 결과 시각화 대시보드
├── eda/                              # raw / processed EDA
├── plan/                             # 작업 계획서
├── requirements.txt
├── PROJECT_GUIDE.md
└── README.md
```

## 데이터 준비

원본 데이터는 git으로 추적하지 않는다. `data/**/raw/` 내부 파일은 각자 로컬에 직접 배치해야 한다.

### MovieLens 32M

아래 파일이 `data/ml-32m/raw/`에 있어야 한다.

- `movies.csv`
- `ratings.csv`
- `tags.csv`
- `links.csv`

### Genome 2021

raw EDA를 실행하려면 Genome 2021 데이터가 `data/genome_2021/` 아래에 배치되어 있어야 한다. 현재 코드 기준으로 다음 경로 중 하나를 인식한다.

- `data/genome_2021/raw/`
- `data/genome_2021/movie_dataset_public_final/`

### ML-32M Extension

보조 데이터 설명은 `data/ml-32m-extension-main/readme.md`를 참고한다. 현재 루트 파이프라인의 필수 입력은 아니지만 연구 참고용으로 보관한다.

## 환경 설정

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

주요 의존성은 `polars`, `matplotlib`, `numpy`, `PyYAML`, `streamlit`, `plotly`, `pandas`다.

## 전처리 파이프라인

기본 파이프라인은 아래 순서로 진행한다.

### 1. 영화 메타데이터 통합 전처리

MovieLens 원본 CSV를 조인해 `data/movies_processed.csv`를 만든다.

```bash
python3 preprocess/preprocess_movie/preprocess_movies.py
```

주요 처리:

- `title`에서 연도를 분리해 `title`, `releaseYear` 생성
- `genres`를 JSON 배열 문자열로 변환
- `tags.csv`를 집계해 `tag`, `tagCount` 생성
- `ratings.csv`를 집계해 `ratingAvg`, `ratingCount` 생성
- `links.csv`를 조인해 `imdbId`, `tmdbId` 추가

함께 생성되는 검증 산출물:

- `preprocess/preprocess_movie/validation_report.json`
- `preprocess/preprocess_movie/bad_rows.csv`

### 2. 영화 drop 후처리

학습용 영화 테이블을 만들기 위해 품질 규칙으로 row를 제거한다.

```bash
python3 preprocess/drop_movie/drop_movies.py
```

현재 drop 기준:

- `ratingCount == 0`
- `genres == []`
- `releaseYear IS NULL`

출력:

- `data/movies_processed_drop.csv`
- `preprocess/drop_movie/validation_report.json`
- `preprocess/drop_movie/bad_rows.csv`

### 3. rating drop 전파

유지된 영화 집합만 남기도록 raw rating을 필터링한다.

```bash
python3 preprocess/drop_rating/drop_ratings.py
```

출력:

- `data/ratings_drop.csv`
- `preprocess/drop_rating/validation_report.json`
- `preprocess/drop_rating/bad_rows.csv`

`ratings_drop.csv`는 `userId`, `ratedAt`, `movieId` 기준으로 정렬되어 저장된다.

### 4. user sequence JSONL 생성

rating row를 user별 chronological history로 재구성한다.

```bash
python3 preprocess/process_rating/process_ratings_drop.py
```

출력:

- `data/ratings_drop_processed.jsonl`
- `preprocess/process_rating/validation_report.json`
- `preprocess/process_rating/bad_rows.csv`

JSONL 레코드 예시:

```json
{
  "userId": 123,
  "ratings": [
    {
      "ratedAt": "2001-01-01T00:00:00Z",
      "movieId": 10,
      "rating": 4.0
    }
  ],
  "ratingCount": 1,
  "firstRatedAt": "2001-01-01T00:00:00Z",
  "lastRatedAt": "2001-01-01T00:00:00Z"
}
```

## EDA

### Raw 데이터 EDA

MovieLens와 Genome 2021을 분리 분석한 뒤 통합 요약까지 생성한다.

```bash
python3 -m eda.raw.eda_overview --source all
```

선택 가능한 옵션:

- `--source ml32m`
- `--source genome2021`
- `--source combined`
- `--source all`

출력 위치:

- `eda/raw/outputs/ml_32m/`
- `eda/raw/outputs/genome_2021/`
- `eda/raw/outputs/`

### Processed 데이터 EDA

전처리 완료 후 산출물을 기준으로 drop 전후 비교와 최종 rating 분포를 분석한다.

```bash
python3 eda/processed/eda_processed.py
```

출력 위치:

- `eda/processed/outputs/eda_report.md`
- `eda/processed/outputs/*.png`

## 클러스터링 대시보드

사용자 상태 임베딩을 차원 축소하고 밀도 기반 클러스터링한 결과를 인터랙티브하게 탐색할 수 있다.

```bash
streamlit run dashboard/cluster_dashboard.py
```

기본적으로 아래 결과 파일을 기대한다.

- `data/clustering/user_clusters.parquet`

필수 컬럼:

- `userId`
- `clusterLabel`
- `x`
- `y`

선택 컬럼:

- `z`
- `clusterProbability`
- `outlierScore`
- `sequenceLength`
- `embeddingNorm`

실제 결과 파일이 아직 없으면 앱에서 demo 데이터를 사용해 UI를 먼저 점검할 수 있다. 세부 입력 계약은 `dashboard/README.md`를 따른다.

## 스키마 정책

스키마는 `schemas/`가 정본이다.

- 컬럼 추가, 변경, 삭제는 코드보다 먼저 `schemas/`에서 정의한다.
- 호환되지 않는 변경은 새 버전 파일로 올린다.
- CSV에 배열을 저장할 때는 `logical_type`과 `physical_type`을 분리한다.
- 현재 주요 processed 스키마:
  - `schemas/ml32m/processed/movies_processed.v2.schema.yaml`
  - `schemas/ml32m/processed/ratings_drop.v1.schema.yaml`
  - `schemas/ml32m/processed/ratings_drop_processed.v1.schema.yaml`

세부 규칙은 `schemas/README.md`를 참고한다.

## 협업 규칙

- `PROJECT_GUIDE.md`를 단일 진실 공급원으로 사용한다.
- 새 작업 전 `plan/`의 기존 계획을 확인한다.
- `data/**/raw/`는 절대 수정하지 않는다.
- 생성물은 스크립트로 재생성하는 것을 원칙으로 한다.
- 데이터셋이나 파이프라인이 바뀌면 `PROJECT_GUIDE.md`도 함께 갱신한다.

## 현재 산출물 기준 참고 수치

현재 저장소에 포함된 validation artifact 기준 요약은 다음과 같다.

- `movies_processed.csv`: 87,584 rows
- `movies_processed_drop.csv`: 77,647 rows
- `ratings_drop.csv`: 31,916,363 rows
- `ratings_drop_processed.jsonl`: 200,948 users

정확한 세부 수치는 각 `validation_report.json`을 확인하면 된다.

## 관련 문서

- `PROJECT_GUIDE.md`: 프로젝트 운영 규칙과 구조
- `schemas/README.md`: 스키마 컨벤션
- `plan/`: 단계별 구현 계획
- `eda/processed/outputs/eda_report.md`: processed 데이터 분석 결과
- `eda/raw/outputs/eda_report.md`: raw 데이터 통합 분석 결과
