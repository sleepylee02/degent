# 영화 데이터 전처리 계획

## 목표
`data/ml-32m/raw/` 내 CSV 파일들을 조인/변환하여 `data/movies_processed.csv` 생성

## 최종 스키마
```
movieId, title, releaseYear, genres, tag, tagCount, ratingAvg, ratingCount, imdbId, tmdbId
```

| 필드 | 타입 | 설명 |
|---|---|---|
| movieId | int | 영화 고유 ID |
| title | string | 영화 제목 (연도 제거) |
| releaseYear | int | title에서 추출한 개봉 연도 (e.g. 1995) |
| genres | arr[string] | 장르 배열 (e.g. `["Adventure","Animation"]`) |
| tag | arr[string] | 사용자 태그 배열, 없으면 `[]` |
| tagCount | int | 영화별 고유 태그 개수 |
| ratingAvg | float | 영화별 평균 평점 |
| ratingCount | int | 영화별 평점 개수 |
| imdbId | int | IMDb ID |
| tmdbId | int | TMDb ID |

## 소스 파일 → 필드 매핑

| 소스 파일 | 사용 필드 | 변환 |
|---|---|---|
| `movies.csv` (movieId, title, genres) | movieId, title, releaseYear, genres | title → 제목/연도 분리, genres `\|` 구분 → 배열 |
| `tags.csv` (userId, movieId, tag, timestamp) | tag, tagCount | movieId 기준 그룹핑, 중복 제거 → 배열 및 개수 집계 |
| `ratings.csv` (userId, movieId, rating, timestamp) | ratingAvg, ratingCount | movieId 기준 평균 평점 및 개수 집계 |
| `links.csv` (movieId, imdbId, tmdbId) | imdbId, tmdbId | movieId 기준 join |

## 작업 순서

1. **movies.csv 로드** — 기준 테이블
2. **title 파싱** — 문자열 끝의 마지막 `(YYYY)`를 우선 인식하고, 필요 시 `(YYYY))` 같은 깨진 suffix도 relaxed fallback으로 복구
3. **빈 제목 검출** — 연도 제거 후 제목이 빈 문자열이면 `bad_rows.csv`에 기록하고 최종 output에서 제외
4. **genres 변환** — `"Adventure|Animation|Children"` → `["Adventure","Animation","Children"]`
5. **tags.csv 집계** — movieId 기준 tag 중복 제거 후 배열과 개수 집계
6. **ratings.csv 집계** — movieId 기준 평균 평점과 개수 집계
7. **links.csv join** — movieId 기준 left join (imdbId, tmdbId)
8. **컬럼 정렬** — movieId, title, releaseYear, genres, tag, tagCount, ratingAvg, ratingCount, imdbId, tmdbId
9. **저장** — `data/movies_processed.csv`
10. **검증 산출물 저장** — `preprocess/preprocess_movie/validation_report.json`, `preprocess/preprocess_movie/bad_rows.csv`

## 변환 예시
```
before:
  movies.csv  → movieId=1, title="Toy Story (1995)", genres="Adventure|Animation|Children|Comedy|Fantasy"
  tags.csv    → movieId=1, tag="pixar" / tag="fun"
  ratings.csv → movieId=1, rating=4.0 / 5.0 / 3.5
  links.csv   → movieId=1, imdbId=0114709, tmdbId=862

after:
  movieId=1, title="Toy Story", releaseYear=1995, genres=["Adventure","Animation","Children","Comedy","Fantasy"], tag=["pixar","fun"], tagCount=2, ratingAvg=4.17, ratingCount=3, imdbId=114709, tmdbId=862
```

## 엣지 케이스
- tag 없는 영화 → `[]`
- rating 없는 영화 → `ratingAvg=null`, `ratingCount=0`
- title에 연도 없는 경우 → releaseYear `null`
- 연도 제거 후 제목이 빈 문자열인 경우 → `bad_rows.csv`에 기록하고 output에서 제외
- title suffix가 `(YYYY))` 같이 깨진 경우 → relaxed fallback으로 연도 복구하고 검증 리포트에 기록
- imdbId/tmdbId 누락 → null 허용 여부 검토 필요
