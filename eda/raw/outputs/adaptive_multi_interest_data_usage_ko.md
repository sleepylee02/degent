# 두 소스를 함께 쓰는 추천 실험 가이드

작성 시각: 2026-03-27 11:58:42

## 1. 결론

Adaptive multi-interest 추천을 하려면 두 소스를 같은 역할로 쓰면 안 된다.

- MovieLens ml-32m: 사용자 행동 시퀀스의 본체
- Genome 2021: 영화 semantic feature와 설명 신호 보강

## 2. 권장 파이프라인

1. MovieLens `ratings.csv`를 사용자별 시간순 시퀀스로 재구성한다.
2. Genome 2021의 `metadata`, `reviews`, `tag_count`, `scores`를 영화 side feature로 정리한다.
3. 두 소스는 겹치는 영화 73,476편에 한해 `movieId = item_id`로 결합한다.
4. 먼저 MovieLens만으로 baseline을 만든다.
5. 그 다음 Genome feature를 추가해서 tail item 설명력과 성능 개선을 본다.

## 3. 왜 이렇게 나눠야 하는가

- MovieLens는 timestamp가 있어서 관심사 전환과 최근성 정보를 볼 수 있다. 이 소스의 평점은 32,000,204건이다.
- Genome 2021은 timestamp는 없지만 리뷰 2,624,608건과 tag relevance score 파일이 있어서 영화 의미를 더 잘 설명한다.
- 즉 하나는 행동 로그, 다른 하나는 아이템 표현이다.

## 4. 추천 실험 순서

1. Baseline: MovieLens 시퀀스만 사용
2. Content Augmentation: Genome 메타데이터, 리뷰 임베딩, tag relevance 추가
3. Explainability: 추천된 아이템에 대해 상위 tag score와 리뷰 키워드로 설명 생성
4. Evaluation: 전체 NDCG/HitRate뿐 아니라 head/tail movie bucket 성능을 분리해서 측정

## 5. 주의사항

- Genome 2021의 평점을 시퀀스 데이터처럼 쓰면 안 된다. timestamp가 없다.
- 사용자 id를 두 소스 사이에서 동일 인물로 가정하면 안 된다.
- 조인 전에 영화 id overlap을 기준으로 item universe를 명시적으로 제한해야 한다.
- long-tail이 강하므로 인기작 위주 성능만 보고 결론 내리면 안 된다.

## 6. 상세 분석 위치

- MovieLens EDA: [ml_32m/eda_report.md](ml_32m/eda_report.md)
- Genome 2021 EDA: [genome_2021/eda_report.md](genome_2021/eda_report.md)
- 통합 비교: [dataset_description_report_ko.md](dataset_description_report_ko.md)
