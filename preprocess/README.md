# preprocess/

전처리와 후처리 스크립트는 원본 데이터를 직접 수정하지 않고, 재생성 가능한 산출물을 만든다. 프로젝트 운영 규칙의 정본은 `PROJECT_GUIDE.md`다.

## 실행 순서

```bash
python3 preprocess/preprocess_movie/preprocess_movies.py
python3 preprocess/drop_movie/drop_movies.py
python3 preprocess/drop_rating/drop_ratings.py
python3 preprocess/process_rating/process_ratings_drop.py
```

보조 장르 산출물이 필요하면 별도로 실행한다. 이 단계는 현재 메인 전처리/drop/user-sequence 파이프라인의 필수 단계가 아니다.

```bash
(cd preprocess/preprocess_genre && python3 preprocess_genre.py)
```

## 단계별 입출력

| step | script | input | output |
|---|---|---|---|
| 1 | `preprocess/preprocess_movie/preprocess_movies.py` | `data/ml-32m/raw/{movies,ratings,tags,links}.csv` | `data/movies_processed.csv` |
| 2 | `preprocess/drop_movie/drop_movies.py` | `data/movies_processed.csv` | `data/movies_processed_drop.csv` |
| 3 | `preprocess/drop_rating/drop_ratings.py` | `data/ml-32m/raw/ratings.csv`, `data/movies_processed_drop.csv` | `data/ratings_drop.csv` |
| 4 | `preprocess/process_rating/process_ratings_drop.py` | `data/ratings_drop.csv` | `data/ratings_drop_processed.jsonl` |
| optional | `preprocess/preprocess_genre/preprocess_genre.py` | `data/ml-32m/raw/movies.csv` | `data/ml-32m/genre.csv` |

## 검증 산출물

각 단계는 해당 전처리 폴더에 검증 산출물을 저장한다.

- `validation_report.json`
- `bad_rows.csv`

## 작업 규칙

- `data/**/raw/`는 수정하지 않는다.
- `data/movies_processed.csv`, `data/movies_processed_drop.csv`, `data/ratings_drop.csv`, `data/ratings_drop_processed.jsonl`은 직접 편집하지 않고 스크립트로 재생성한다.
- 컬럼 추가, 변경, 삭제가 필요하면 코드보다 `schemas/`를 먼저 수정한다.
- 새 전처리 단계나 산출물을 추가하면 `PROJECT_GUIDE.md`, `docs/data-flow.md`, `docs/artifacts.md`도 함께 업데이트한다.
