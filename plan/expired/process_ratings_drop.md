# ratings_drop user-sequence processing 계획

## 목표
`data/ratings_drop.csv`를 사용자 단위의 chronological history로 재구성한
`data/ratings_drop_processed.jsonl`을 생성한다.

이 단계의 목적은 rating event를 row 집합으로 두는 것이 아니라,
각 사용자가 시간 순서대로 어떤 영화를 어떻게 평가했는지 한 번에 볼 수 있는
user-centered sequence 형태로 정리하는 것이다.

## 출력 형태
출력은 CSV가 아니라 JSONL로 저장한다.

- `1 user = 1 line`
- 각 line은 하나의 JSON object
- `ratings`는 `ratedAt` 오름차순으로 정렬된 배열

예상 record shape:

```json
{
  "userId": 123,
  "ratings": [
    {"ratedAt": "2001-01-01T00:00:00Z", "movieId": 10, "rating": 4.0},
    {"ratedAt": "2001-01-03T12:34:56Z", "movieId": 25, "rating": 3.5}
  ],
  "ratingCount": 2,
  "firstRatedAt": "2001-01-01T00:00:00Z",
  "lastRatedAt": "2001-01-03T12:34:56Z"
}
```

## 왜 JSONL인가
- nested sequence 구조를 자연스럽게 표현할 수 있다.
- CSV처럼 JSON 문자열 escaping을 억지로 하지 않아도 된다.
- 대용량 데이터에 대해 line 단위 스트리밍 쓰기가 쉽다.
- downstream에서 user별 history를 바로 읽기 좋다.

## 입력 / 출력

### 입력
- `data/ratings_drop.csv`

### 출력
- `data/ratings_drop_processed.jsonl`

### 구현 위치
- `preprocess/process_rating/`

### 검증 산출물
- `preprocess/process_rating/validation_report.json`
- `preprocess/process_rating/bad_rows.csv`

## 정렬 기준
각 user 내부의 rating event는 아래 순서로 정렬한다.

1. `ratedAt` 오름차순
2. `movieId` 오름차순

핵심 기준은 `ratedAt`이며,
동일 시각이 있을 때 deterministic ordering을 위해
`movieId`를 secondary key로 사용한다.

## 현재 기준 예상 규모
현재 생성된 `data/ratings_drop.csv` 기준 예상 출력 규모는 다음과 같다.

- 입력 rating row 수: `31,916,363`
- 출력 user row 수: `200,948`
- user당 최소 rating 수: `17`
- user당 최대 rating 수: `31,234`
- user당 평균 rating 수: `158.829`

즉 이 단계는 row drop이 아니라,
interaction row를 user sequence record로 재구성하는 단계다.

## 처리 방식
1. `data/ratings_drop.csv` 로드
2. `userId`, `ratedAt`, `movieId` 기준으로 정렬
3. 각 row를 `{ratedAt, movieId, rating}` 구조로 변환
4. `userId` 기준으로 group-by
5. user별 `ratings` 배열 생성
6. `ratingCount`, `firstRatedAt`, `lastRatedAt` 집계
7. 각 user record를 JSON object로 직렬화
8. `data/ratings_drop_processed.jsonl` 저장
9. summary와 이상 케이스를 validation artifact에 저장

## 구현 원칙
- 이 단계에서는 새로운 drop 규칙을 추가하지 않는다.
- 입력 `ratings_drop.csv`의 모든 row는 어떤 user sequence엔가 반드시 포함되어야 한다.
- 동일 user가 같은 movie를 여러 번 평가했더라도 event history로 그대로 유지한다.
- 대용량 데이터이므로 스트리밍 또는 메모리 사용량을 고려한 구현이 필요하다.

## validation 체크 항목
- 입력 row 수 합계와 user별 `ratingCount` 합계가 같은지
- 출력 user record 수가 입력의 unique `userId` 수와 같은지
- 각 user의 `ratings` 배열 길이와 `ratingCount`가 같은지
- 각 user의 `ratings` 배열이 `ratedAt` 오름차순인지
- `firstRatedAt`와 `lastRatedAt`가 배열의 첫/마지막 시각과 일치하는지
- null `userId`, `movieId`, `rating`, `ratedAt`가 있는지

## 예상 디렉토리 구조
```text
preprocess/
└── process_rating/
    ├── process_ratings_drop.py
    ├── validation_report.json
    └── bad_rows.csv
```
