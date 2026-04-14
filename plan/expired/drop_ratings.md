# rating ground truth drop 파이프라인 계획

## 목표
`movie drop` 결과와 정합한 rating ground truth를 생성한다.

기존 `data/movies_processed_drop.csv`에서 제거된 영화에 대응하는 rating event를
`data/ml-32m/raw/ratings.csv`에서도 함께 제거하여,
최종적으로 학습에 사용할 `data/ratings_drop.csv`를 만든다.

이번 단계의 목적은 영화 테이블과 rating 테이블의 기준을 일치시키는 것이다.
즉 rating 데이터는 독립 기준으로 drop하지 않고,
movie drop 결과를 그대로 전파하는 방식으로 정리한다.

## 입력 / 출력

### 입력
- `data/ml-32m/raw/ratings.csv`
- `data/movies_processed_drop.csv`
- `preprocess/drop_movie/bad_rows.csv`

### 출력
- `data/ratings_drop.csv`

### 구현 위치
- `preprocess/drop_rating/`

### 검증 산출물
- `preprocess/drop_rating/validation_report.json`
- `preprocess/drop_rating/bad_rows.csv`

`bad_rows.csv`에는 drop된 rating row와 해당 rating이 제거된 movie drop 사유를 기록한다.

## 기준 정의
rating row의 drop 여부는 rating 자체 값이 아니라
해당 `movieId`가 `movies_processed_drop.csv`에 남아 있는지로 판정한다.

- keep 조건:
  `ratings.csv.movieId`가 `movies_processed_drop.csv.movieId`에 존재
- drop 조건:
  `ratings.csv.movieId`가 `movies_processed_drop.csv.movieId`에 존재하지 않음

즉 `movies_processed_drop.csv`를 정답 keep set으로 사용한다.

movie drop 사유는 아래 3가지에서 전파된다.

1. `missing_ratings`
2. `missing_genres`
3. `title_without_year`

단, `missing_ratings`로 drop된 영화는 원래 rating event가 없으므로
실제 `ratings.csv` row 제거에는 직접 영향이 없다.

## 현재 기준 예상 제거 규모
현재 생성된 `movies_processed_drop.csv`와 `drop_movie/bad_rows.csv`를 기준으로 계산했을 때,
ratings raw에서 제거될 rating event는 총 `83,806`개다.

- 전체 rating row 수: `32,000,204`
- 제거 rating row 수: `83,806`
- 유지 rating row 수: `31,916,398`
- 제거 비율: `0.2619%`

추가 참고 수치는 다음과 같다.

- ratings에 등장하는 전체 movie 수: `84,432`
- drop 대상 movie 중 ratings에 실제 등장하는 movie 수: `6,784`
- keep 이후 ratings에 남는 movie 수: `77,648`
- 영향 받는 user 수: `21,698`

movie drop으로 제거된 영화는 총 `9,937`개지만,
그중 `3,153`개는 `missing_ratings` 영화라서 애초에 ratings.csv에 row가 없다.
따라서 실제 ratings drop에는 `6,784`개 movie만 관여한다.

## drop 사유별 rating 영향
현재 기준으로 ratings row 제거는 아래 조합에서만 발생한다.

- `missing_genres`만 해당하는 영화의 rating: `48,643` row, `6,206` movie
- `title_without_year`만 해당하는 영화의 rating: `28,308` row, `229` movie
- `missing_genres + title_without_year` 영화의 rating: `6,855` row, `349` movie

`missing_ratings`가 포함된 영화는 rating event가 없으므로
ratings row 제거 통계에는 나타나지 않는다.

## 처리 방식
1. `data/movies_processed_drop.csv`에서 keep 대상 `movieId` 로드
2. `preprocess/drop_movie/bad_rows.csv`에서 dropped `movieId`별 사유 집계
3. `data/ml-32m/raw/ratings.csv` 로드
4. keep 대상 `movieId`와 `semi join`하여 `data/ratings_drop.csv` 생성
5. drop 대상 rating row는 `anti join`으로 분리
6. drop 대상 rating row에 movie drop 사유를 join하여 `preprocess/drop_rating/bad_rows.csv` 저장
7. keep/drop 수치, 영향 movie 수, 영향 user 수, 사유별 count를 `validation_report.json`에 저장

## 구현 원칙
- movie drop 결과를 기준으로 rating을 필터링한다.
- rating 전용 새로운 품질 규칙은 이번 단계에서 추가하지 않는다.
- `movies_processed_drop.csv`를 기준 keep set으로 사용해 movie/rating 정합성을 보장한다.
- `drop_movie/bad_rows.csv`는 audit 용도이며, 실제 필터 판정의 기준은 keep set이다.
- 산출물 파일명과 리포트 형식은 기존 `drop_movie`와 최대한 유사하게 맞춘다.

## 스키마 고려사항
구현 전에 새 processed 출력 스키마를 정의하는 것이 맞다.

예상 스키마 위치:
- `schemas/ml32m/processed/ratings_drop.v1.schema.yaml`

예상 컬럼은 raw ratings와 거의 동일하지만,
processed 단계부터는 이벤트 시각을 UTC ISO 8601 문자열로 저장한다.

```text
userId, movieId, rating, ratedAt
```

차이는 컬럼 구조뿐 아니라
`movie drop keep set` 기준으로 필터된 processed 산출물이며,
이벤트 시각 필드가 `timestamp` int가 아니라 `ratedAt` 문자열이라는 점이다.

## 예상 디렉토리 구조
```text
preprocess/
└── drop_rating/
    ├── drop_ratings.py
    ├── validation_report.json
    └── bad_rows.csv
```

## 구현 시 확인할 사항
- `ratings.csv`는 row 수가 매우 크므로 lazy scan 기반으로 처리하는 것이 안전하다.
- drop 사유는 movie 단위로 발생하므로, rating bad rows에는 동일 사유가 여러 row에 반복될 수 있다.
- `bad_rows.csv`의 행 수는 제거된 rating row 수와 같아질 가능성이 크다.
  다만 movie에 여러 drop 사유가 있으면 사유 기록 방식에 따라 더 커질 수 있으므로,
  한 row당 사유 배열로 저장할지, 사유별 row로 펼칠지 구현 전에 결정해야 한다.
- 추천 학습용 ground truth에서는 movie 테이블과 ratings 테이블의 movie universe가 정확히 같아야 한다.
