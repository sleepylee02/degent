# Temporal Cutoff T Candidate EDA

## 목적

임의의 시간 cutoff `T`를 정하기 위해 MovieLens processed rating 데이터의 시간 분포와 `T` 이후 streaming 부하 후보를 분석한다.

이 계획의 목표는 아직 모델을 새로 학습시키는 것이 아니다. 먼저 `T`를 어디로 잡아야 `T` 이전 학습 데이터가 충분하고, `T` 이후 streaming replay 부하도 관찰 가능할 만큼 남는지 판단할 근거를 만든다.

## 배경

다음 실험 파이프라인은 특정 시점 `T`까지의 데이터로 모델과 user/interest state를 최신화한 뒤, `T` 이후 이벤트를 streaming replay로 흘려보내는 구조다.

이를 구현하기 전에 아래 질문에 답해야 한다.

- 전체 rating event는 시간에 따라 어떻게 분포하는가?
- `T` 후보별로 train/pre-`T` 데이터와 stream/post-`T` 데이터가 얼마나 남는가?
- `T` 이후 event/sec, event/day, burst 구간은 어느 정도인가?
- `T` 이후 새 user와 기존 user 비율은 어느 정도인가?
- `T` 이후 movie가 pre-`T` item vocabulary에 없는 cold item 압력은 어느 정도인가?
- 현재 streaming positive projection 기준으로 active embedding 후보가 얼마나 발생할 것으로 보이는가?
- refit trigger threshold 기준으로 user별 post-`T` 이벤트가 얼마나 몰리는가?

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `schemas/README.md`
- `schemas/ml32m/processed/ratings_drop.v1.schema.yaml`
- `schemas/ml32m/processed/ratings_drop_processed.v1.schema.yaml`
- `preprocess/README.md`
- `eda/processed/eda_processed.py`
- `eda/processed/outputs/eda_report.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `plan/active/sqlite_runtime_state_store.md`

## 수정 범위

- 수정:
  - `eda/processed/temporal_cutoff_eda.py`
  - `eda/processed/outputs/temporal_cutoff_eda_report.md`
  - `eda/processed/outputs/temporal_cutoff_candidates.csv`
  - `eda/processed/outputs/temporal_monthly_event_counts.csv`
  - `eda/processed/outputs/temporal_daily_event_counts.csv`
  - `eda/processed/outputs/temporal_hourly_event_counts.csv`
  - `eda/processed/outputs/temporal_cutoff_*.png`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - `data/ratings_drop.csv`
  - `data/ratings_drop_processed.jsonl`
  - `data/movies_processed_drop.csv`
  - 모델 checkpoint 또는 기존 replay artifact 덮어쓰기

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경:
  - temporal cutoff EDA script와 report/CSV/plot 산출물 추가
- 호환성 영향:
  - 없음. 분석 산출물만 추가하며 기존 preprocessing/model/replay 실행 경로는 바꾸지 않는다.

## 실행 계획

### Phase 0. 기준 확인

1. 기존 processed EDA와 rating schema를 확인한다.
2. `ratings_drop.csv`와 `ratings_drop_processed.jsonl`의 역할을 분리한다.
   - flat event 시간 분포와 cutoff count는 `ratings_drop.csv`를 사용한다.
   - user sequence 기반 positive projection 근사나 후속 state seed 설계는 `ratings_drop_processed.jsonl` 계약을 참고한다.

### Phase 1. 전체 시간 분포 EDA

1. 전체 event 수, unique user, unique movie, 최초/최종 `ratedAt`을 산출한다.
2. 연도/월/일 단위 event count를 산출한다.
3. 누적 event 비율을 계산해 event quantile 기반 `T` 후보를 만든다.
4. 월별/연도별 event trend plot을 저장한다.

### Phase 2. `T` 후보 생성

1. event 누적 비율 기반 후보를 만든다.
   - 70%, 80%, 85%, 90%, 95%
2. 자연스러운 calendar cutoff 후보를 만든다.
   - 연말 cutoff
   - 최근 1년/2년/3년 stream window 후보
3. 후보가 너무 이르거나 늦으면 제외 기준을 문서화한다.
   - pre-`T` train event가 너무 적은 경우
   - post-`T` stream event가 너무 적은 경우
   - post-`T` 기간이 너무 짧아 부하 패턴을 보기 어려운 경우

### Phase 3. 후보별 부하 지표 산출

각 `T` 후보별로 아래 지표를 계산한다.

1. Train/pre-`T`
   - event 수와 비율
   - unique user/movie 수
   - user당 train history 분포
2. Stream/post-`T`
   - event 수와 비율
   - stream 기간 일수
   - 평균 event/day, event/hour
   - day/hour burst p50/p90/p95/p99/max
   - unique stream user/movie 수
3. Cold pressure
   - post-`T` 신규 user 수와 비율
   - post-`T` movie 중 pre-`T`에 없던 movie 수
   - post-`T` event 중 unknown item event 수와 비율
4. Embedding/refit pressure proxy
   - post-`T` user별 event count 분포
   - post-`T` user별 rating>=4 또는 streaming positive policy에 가까운 active-positive proxy count
   - `refit_min_events`, `assign_trigger_count`, `outlier_trigger_count` 후보 기준으로 refit trigger 가능 user 수

### Phase 4. 추천 `T` 후보 정리

1. 후보별 장단점을 report에 적는다.
2. 1차 추천 후보를 2~3개로 좁힌다.
3. 후속 구현에서 필요한 옵션을 정리한다.
   - train cutoff option
   - replay input cutoff option
   - pre-`T` user/interest state seed option
   - SQLite load profile query/report option

## 진행 결과

- Script: `eda/processed/temporal_cutoff_eda.py`
- Report: `eda/processed/outputs/temporal_cutoff_eda_report.md`
- Candidate table: `eda/processed/outputs/temporal_cutoff_candidates.csv`
- Time count exports:
  - `eda/processed/outputs/temporal_monthly_event_counts.csv`
  - `eda/processed/outputs/temporal_daily_event_counts.csv`
  - `eda/processed/outputs/temporal_hourly_event_counts.csv`
- Plots:
  - `eda/processed/outputs/temporal_cutoff_events_by_year.png`
  - `eda/processed/outputs/temporal_cutoff_events_by_month.png`
  - `eda/processed/outputs/temporal_cutoff_candidate_volume.png`
  - `eda/processed/outputs/temporal_cutoff_unknown_item_pressure.png`
  - `eda/processed/outputs/temporal_cutoff_hourly_burst_p99.png`

1차 추천 후보:

- `recent_5y`: `2018-10-14T23:59:59Z`, stress 후보. pre 80.29%, post 6,291,468 events.
- `q85`: `2019-10-29T23:59:59Z`, balanced 후보. pre 85.00%, post 4,786,679 events.
- `q90`: `2020-10-29T23:59:59Z`, conservative 후보. pre 90.00%, post 3,190,916 events.

## 검증

- [x] `.venv/bin/python -m py_compile eda/processed/temporal_cutoff_eda.py`
- [x] `.venv/bin/python eda/processed/temporal_cutoff_eda.py --help`
- [x] full EDA 실행이 완료된다.
- [x] `eda/processed/outputs/temporal_cutoff_eda_report.md`가 생성된다.
- [x] `eda/processed/outputs/temporal_cutoff_candidates.csv`가 생성된다.
- [x] report에 추천 `T` 후보와 제외 후보 사유가 기록된다.
- [x] 원본 데이터와 기존 모델/replay artifact를 수정하지 않는다.
- [x] `git diff --check`

## 완료 조건

- `T` 후보별 train/stream event volume, user/movie coverage, cold item pressure, burst load, active-positive/refit pressure proxy를 한 report에서 비교할 수 있다.
- 다음 구현 계획에서 사용할 1차 `T` 후보 2~3개가 명시된다.
- `T` 이후 streaming 부하를 측정하기 위해 model/replay 쪽에 어떤 CLI option과 artifact 계약이 필요한지 정리된다.
