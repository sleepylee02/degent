# data/ml-32m/

MovieLens 32M 원본 데이터와 이 프로젝트에서 생성한 보조 산출물을 둔다.

## Raw Files

아래 파일은 `data/ml-32m/raw/`에 로컬로 배치한다. 원본 파일은 git으로 추적하지 않고 직접 수정하지 않는다.

- `movies.csv`
- `ratings.csv`
- `tags.csv`
- `links.csv`

## Tracked Auxiliary Output

- `genre.csv`: `preprocess/preprocess_genre/preprocess_genre.py`가 생성하는 장르 multi-hot 보조 산출물이다. 현재 메인 전처리/drop/user-sequence 파이프라인의 필수 단계는 아니다.
