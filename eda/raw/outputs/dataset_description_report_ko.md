# 두 영화 데이터 소스 통합 설명

작성 시각: 2026-03-27 11:58:42

## 1. 왜 둘로 나눠 봐야 하는가

지금 작업 공간에는 성격이 다른 두 데이터 소스가 있다. 하나는 사용자 상호작용 로그 중심의 MovieLens ml-32m이고, 다른 하나는 영화 semantic 정보와 tag relevance 학습을 위해 정리된 Genome 2021 소스다. 둘을 한 덩어리로 읽으면 목적이 흐려진다.

## 2. 역할 분리

| 소스 | 핵심 단위 | 강점 | 약점 | 권장 역할 |
| --- | --- | --- | --- | --- |
| MovieLens ml-32m | user-movie rating event | timestamp, 대규모 평점, 자유 태그 | 리뷰 없음, 설명 신호 약함 | 추천 모델의 주 상호작용 로그 |
| Genome 2021 | movie metadata + reviews + tag supervision | 리뷰, tag count, survey, relevance score | timestamp 없음 | 아이템 semantic feature와 설명 신호 |

## 3. 연결 방식

| 항목 | 설명 |
| --- | --- |
| 연결 키 | `movieId`와 `item_id`를 동일 영화 키로 사용 |
| 겹치는 영화 수 | 73,476 |
| MovieLens 대비 Genome 커버리지 | 83.89% |
| Genome 대비 MovieLens 커버리지 | 86.79% |

사용자 id는 안전한 조인 키가 아니다. 두 소스를 합칠 때는 영화 중심으로 붙이고, 사용자 축은 분리된 것으로 보는 편이 맞다.

## 4. 작업 디렉터리 구조

- MovieLens 상세 결과: `eda_outputs/ml_32m/`
- Genome 2021 상세 결과: `eda_outputs/genome_2021/`
- 현재 문서 3개: 두 소스를 다시 모아 비교한 루트 요약

## 5. 실무적으로 해석하면

- 시퀀스 추천이나 adaptive multi-interest의 backbone은 MovieLens다. timestamp가 있고 평점 수가 32,000,204건이기 때문이다.
- 영화 의미 표현과 설명 가능성은 Genome 2021이 보강한다. 리뷰 2,624,608건, 설문 응답 58,903건, relevance score 파일이 있기 때문이다.
- 따라서 전처리와 분석은 source별로 유지하고, 모델링 직전 혹은 feature store 단계에서 item 기준으로 결합하는 구조가 가장 안정적이다.

## 6. 상세 문서 링크

- MovieLens 설명: [ml_32m/dataset_description_report_ko.md](ml_32m/dataset_description_report_ko.md)
- Genome 2021 설명: [genome_2021/dataset_description_report_ko.md](genome_2021/dataset_description_report_ko.md)
- 전체 비교 EDA: [eda_report.md](eda_report.md)
