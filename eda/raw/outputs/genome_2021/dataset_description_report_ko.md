# Genome 2021 영화 데이터셋 설명 보고서

작성 시각: 2026-03-27 12:44:40

## 1. 이 데이터셋은 무엇인가

이 소스는 일반적인 추천 로그만 담은 데이터가 아니라, Tag Genome 점수를 만들기 위해 모은 복합 영화 데이터셋이다. MovieLens 평점, MovieLens 태그 집계, IMDb 리뷰, 사용자 설문 응답, 그리고 태그-영화 relevance score까지 한 번에 포함한다.

## 2. 핵심 구성

MovieLens처럼 이 소스도 파일별 역할을 나눠서 보면 이해가 쉽다. 다만 차이는 분명하다. MovieLens가 `ratings/tags/movies/links` 중심의 추천 로그 구조라면, Genome 2021은 평점 로그 외에도 리뷰, 설문, relevance score를 함께 담은 복합 구조다.

- `raw/metadata.json`

  => 영화 제목, 감독, 배우, 평균 평점, IMDb id 같은 메타데이터를 담는다. MovieLens의 `movies.csv`와 `links.csv`를 합친 느낌에 가깝다.

- `raw/ratings.json`

  => 한 사용자가 한 영화에 남긴 평점 이벤트를 나타낸다. 관측 단위는 `user_id-item_id` 수준의 평점 1건이다. 다만 `timestamp`는 없다.

- `raw/reviews.json`

  => IMDb에서 수집한 영화 리뷰 텍스트다. 한 행이 한 개의 리뷰이며, 영화 semantic 정보를 풍부하게 만드는 핵심 소스다.

- `raw/tags.json`

  => tag vocabulary 사전이다. `tag_id`와 실제 태그 문자열을 연결한다.

- `raw/tag_count.json`

  => MovieLens 사용자들이 특정 태그를 특정 영화에 몇 번 붙였는지를 집계한 데이터다. 자유 텍스트 원문 로그가 아니라 `영화-태그 빈도` 테이블이다.

- `raw/survey_answers.json`

  => 사용자가 `이 태그가 이 영화에 얼마나 잘 맞는가`를 1~5점으로 평가한 설문 응답이다. `-1`은 확신 없음이다.

- `scores/glmer.csv`

  => 회귀 기반 알고리즘이 예측한 `영화-태그 relevance score`다. 점수는 대체로 0~1 범위다.

- `scores/tagdl.csv`

  => TagDL 신경망 모델이 예측한 `영화-태그 relevance score`다. 설계상 일부 값은 0 미만 또는 1 초과일 수 있다.

| 파일 | 행 수 | 주요 컬럼 | 설명 |
| --- | --- | --- | --- |
| `raw/metadata.json` | 84,661 | `item_id`, `title`, `directedBy`, `starring`, `avgRating`, `imdbId` | 영화 메타데이터 |
| `raw/ratings.json` | 28,490,116 | `user_id`, `item_id`, `rating` | 평점 이벤트 로그 |
| `raw/reviews.json` | 2,624,608 | `item_id`, `txt` | IMDb 리뷰 텍스트 |
| `raw/tags.json` | 1,094 | `id`, `tag` | 태그 사전 |
| `raw/tag_count.json` | 212,704 | `item_id`, `tag_id`, `num` | 영화-태그 집계 빈도 |
| `raw/survey_answers.json` | 58,903 | `user_id`, `item_id`, `tag_id`, `score` | 태그 적합도 설문 응답 |
| `scores/glmer.csv` | 10,551,655 | `tag`, `item_id`, `score` | 회귀 기반 relevance score |
| `scores/tagdl.csv` | 10,551,655 | `tag`, `item_id`, `score` | 신경망 기반 relevance score |

| 구성요소 | 규모 | 역할 |
| --- | --- | --- |
| 메타데이터 영화 | 84,661 | 제목, 감독, 배우, 평균 평점, IMDb id |
| 평점 | 28,490,116 | MovieLens 기반 사용자-영화 평점 |
| 리뷰 | 2,624,608 | IMDb 텍스트 리뷰 |
| 태그 어휘 | 1,094 | 고정 tag vocabulary |
| 영화-태그 집계쌍 | 212,704 | MovieLens에서 태그가 몇 번 붙었는지 |
| 설문 응답 | 58,903 | 영화-태그 pair에 대한 사용자 판단 |
| GLMER 점수 | 10,551,655 | 회귀 기반 tag relevance |
| TagDL 점수 | 10,551,655 | 신경망 기반 tag relevance |

## 3. 평점 행렬 관점

| 지표 | 값 |
| --- | --- |
| 평점 사용자 수 | 247,383 |
| 평점 영화 수 | 67,873 |
| 사용자당 평점 중앙값 | 38 |
| 영화당 평점 중앙값 | 5 |
| 영화당 평점 99퍼센타일 | 10,262 |
| 최다 평점 영화 | 98,967 |

| 인기 집중 구간 | 커버리지 | 전체 평점 점유율 |
| --- | --- | --- |
| 상위 1% 영화 | 679 / 67,873 | 50.92% |
| 상위 5% 영화 | 3,394 / 67,873 | 86.64% |
| 상위 10% 영화 | 6,788 / 67,873 | 95.00% |

평점 행렬 자체도 분명히 롱테일이다. 다만 이 데이터에는 `timestamp`가 없기 때문에 시퀀스 추천용 원천 로그로 보기에는 한계가 있다. 여기서의 평점은 순서 정보보다 협업 신호와 영화별 평균 성향을 제공하는 역할에 가깝다.

## 4. 이 데이터가 MovieLens와 다른 점

- 리뷰 텍스트가 있다. 영화 의미를 설명하거나 텍스트 임베딩을 만들기에 좋다.
- 자유 텍스트 태그 원문이 아니라 고정 tag vocabulary와 영화-태그 집계가 있다.
- 설문 응답과 relevance score 파일이 있어 tag relevance prediction이나 설명 가능성 연구에 적합하다.
- 평점은 많지만 시간 정보가 없어 adaptive sequence 실험의 주력 소스로 쓰기는 어렵다.

## 5. 해석 시 주의점

- 메타데이터 전체 영화 수와 평점/리뷰/태그 집계 커버리지는 다르다. 즉 메타데이터에 있다고 해서 모든 부가 정보가 다 있는 것은 아니다.
- `survey_answers.json`의 `-1`은 확신 없음이다. 일반 점수와 섞어서 평균내면 안 된다.
- `TagDL` 점수는 설계상 0 미만 또는 1 초과가 나올 수 있다.
- 사용자 id는 이 데이터 내부에서는 일관되지만, 다른 MovieLens 버전과 동일 사용자라고 가정하는 것은 안전하지 않다.

## 6. 언제 쓰면 좋은가

| 목적 | 적합도 | 이유 |
| --- | --- | --- |
| 시퀀스 추천의 주력 로그 | 낮음 | timestamp가 없다 |
| 영화 semantic side feature | 매우 높음 | 리뷰, tag count, relevance score가 풍부하다 |
| 설명 가능한 추천 | 높음 | 영화-태그 연결 근거가 많다 |
| tag relevance prediction | 매우 높음 | 설문 정답과 점수 파일이 있다 |
| 롱테일 영화 의미 보강 | 높음 | 리뷰와 tag score를 함께 사용할 수 있다 |

이 소스는 추천 모델의 주 상호작용 로그라기보다, 영화 표현을 풍부하게 만드는 보조 지식 소스로 보는 편이 맞다.

## 7. 참고 수치

| 지표 | 값 |
| --- | --- |
| 태그 적용 수 중앙값 | 3 |
| 태그 적용 수 평균 | 21 |
| 설문 응답 중 -1 비중 | 13.14% |
| GLMER score 범위 | 0.0000 ~ 1.0000 |
| TagDL score 범위 | -0.0784 ~ 1.1396 |
