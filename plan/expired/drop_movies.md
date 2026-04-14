# 영화 row drop 파이프라인 계획

## 목표
기존 전처리 결과물인 `data/movies_processed.csv`를 입력으로 받아,
품질 기준을 만족하지 못하는 영화 row를 제거한 `data/movies_processed_drop.csv`를 생성한다.

이번 단계의 목적은 원본 전처리 파이프라인을 변경하지 않고,
별도의 drop 전용 후처리 단계를 추가해 학습용 영화 테이블을 만드는 것이다.

## 입력 / 출력

### 입력
- `data/movies_processed.csv`

### 출력
- `data/movies_processed_drop.csv`

### 구현 위치
- `preprocess/drop_movie/`

### 검증 산출물
- `preprocess/drop_movie/validation_report.json`
- `preprocess/drop_movie/bad_rows.csv`

`bad_rows.csv`에는 drop된 영화 row와 drop 사유를 기록한다.

## drop 기준
아래 조건 중 하나라도 만족하면 해당 row를 제거한다.

1. `missing_ratings`
   `ratingCount == 0`
2. `missing_genres`
   `genres == []`
3. `title_without_year`
   `releaseYear IS NULL`

기준은 `movies_processed.csv`의 processed 컬럼을 기준으로 판정한다.
즉 raw 파일을 다시 참조하지 않고, 현재 전처리 결과의 컬럼 상태만으로 drop 여부를 결정한다.

## 현재 기준 예상 제거 규모
`preprocess/preprocess_movie/validation_report.json`과
`preprocess/preprocess_movie/bad_rows.csv`를 기준으로 계산했을 때,
세 조건의 합집합 drop 수는 총 `9,937` row이다.

- 전체 row 수: `87,584`
- 제거 row 수: `9,937`
- 유지 row 수: `77,647`
- 제거 비율: `11.35%`

조건별 단순 합은 `10,849`이지만,
동일 영화가 여러 조건에 동시에 걸리는 경우가 있어 실제 제거 수는 `9,937`이다.

겹침 포함 분포는 다음과 같다.

- `missing_genres`만: `6,206`
- `missing_ratings`만: `2,614`
- `title_without_year`만: `229`
- `missing_genres + missing_ratings`: `501`
- `missing_genres + title_without_year`: `349`
- `missing_ratings + title_without_year`: `14`
- 세 조건 모두: `24`

## 처리 방식
1. `data/movies_processed.csv` 로드
2. `genres`, `ratingCount`, `releaseYear` 컬럼을 기준으로 drop 조건 계산
3. drop 대상 row를 별도 프레임으로 보관
4. keep 대상 row만 남겨 `data/movies_processed_drop.csv` 저장
5. drop 사유별 count와 sample `movieId`를 `validation_report.json`에 저장
6. drop된 개별 row와 사유를 `bad_rows.csv`에 저장

## 구현 원칙
- 기존 `preprocess/preprocess_movie/` 코드는 수정하지 않는다.
- drop 단계는 독립된 후처리 파이프라인으로 구현한다.
- 입력 파일이 이미 processed 상태이므로, 스키마 검증보다 filter 정책과 audit 기록에 집중한다.
- drop 기준은 명시적 boolean 조건으로 코드에 고정하고, 추후 필요하면 CLI 옵션으로 확장한다.

## 예상 디렉토리 구조
```
preprocess/
└── drop_movie/
    ├── drop_movies.py
    ├── validation_report.json
    └── bad_rows.csv
```

## 구현 시 확인할 사항
- `genres` 컬럼이 JSON 문자열이므로 파싱 후 빈 배열 여부를 판정해야 한다.
- `releaseYear`는 null 허용 컬럼이므로, drop 단계에서만 stricter rule을 적용한다.
- `ratingAvg`는 `ratingCount == 0`와 함께 null일 수 있으므로, 직접 기준은 `ratingCount == 0`로 통일한다.
- 산출물 파일명과 리포트 형식은 기존 `preprocess_movie`와 최대한 유사하게 맞춘다.
