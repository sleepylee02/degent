# Temporal Cutoff 2020 Pipeline Deep Dive

## 목적

2020년 전후 cutoff 후보를 현재 모델/streaming 파이프라인 기준으로 깊게 분석한다.

단순 event volume이 아니라, 실제 파이프라인에서 필요한 학습 가능 user 수, positive sequence 길이, item vocabulary coverage, post-`T` online 처리 부하, refit 압력을 확인한다.

## 배경

현재 파이프라인의 주요 부하 기준은 아래와 같다.

- `model.batch.train`
  - `ratings_drop_processed.jsonl`에서 user sequence를 읽는다.
  - user별 전체 rating에서 z-score `> 0`인 positive만 사용한다.
  - 기본 필터는 `min_activity_days=30`, `min_interactions=200`.
  - `seq_len=100`, `stride=50`으로 training window를 만든다.
  - `item2idx`는 필터를 통과한 positive sequence의 movie만 포함한다.
- `model.batch.extract_canonical`
  - 기본 필터는 `min_activity_days=30`, `min_interactions=1000`.
  - event 하나당 canonical embedding 하나를 만든다.
- `model.stream.extract_online`
  - raw event가 들어올 때마다 해당 user의 raw history 전체로 positive projection을 다시 계산한다.
  - active positive event 전체에 대해 embedding NPZ를 다시 만든다.
- `model.stream.interest_assign`
  - online embedding NPZ의 active row를 읽고, 이미 처리된 row도 `already_processed`로 확인한다.
  - 기본 trigger는 `refit_min_events=20`, `assign_trigger_count=50`, `outlier_trigger_count=10`.
- `model.stream.cluster_refit`
  - refit 요청 user의 active online embedding 전체를 UMAP/HDBSCAN 대상으로 사용한다.

따라서 2020 cutoff는 아래 질문에 답해야 한다.

- `T` 이전만 봤을 때 train eligible user가 얼마나 남는가?
- `T` 이전 positive sequence 길이 분포가 `seq_len=100` 학습에 충분한가?
- `T` 이전 item2idx가 `T` 이후 stream movie를 얼마나 cover하는가?
- `T` 이후 event가 seed된 user / 기존이지만 학습 제외 user / 신규 user 중 어디로 들어오는가?
- 현재 event-per-stage 구조에서 online embedding recompute row가 얼마나 증폭되는가?
- default refit trigger 기준으로 refit 후보 user가 얼마나 생기는가?

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `model/common/dataset.py`
- `model/common/canonical.py`
- `model/batch/train.py`
- `model/batch/extract_canonical.py`
- `model/stream/state.py`
- `model/stream/extract_online.py`
- `model/stream/interest_assign.py`
- `model/stream/cluster_refit.py`
- `model/stream/trace_replay.py`
- `plan/done/temporal_cutoff_t_candidate_eda.md`

## 수정 범위

- 수정:
  - `eda/processed/temporal_cutoff_2020_deep_dive.py`
  - `eda/processed/outputs/temporal_cutoff_2020_deep_dive_report.md`
  - `eda/processed/outputs/temporal_cutoff_2020_deep_dive.csv`
  - `eda/processed/outputs/temporal_cutoff_2020_*.png`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - `data/ratings_drop.csv`
  - `data/ratings_drop_processed.jsonl`
  - 모델 checkpoint 또는 replay artifact 덮어쓰기

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: 2020 cutoff deep dive EDA 산출물 추가
- 호환성 영향: 없음

## 실행 계획

1. 현재 파이프라인의 train/canonical/stream/refit 기준을 코드에서 확인한다.
2. 2020 전후 cutoff 후보를 정한다.
   - `2019-12-31T23:59:59Z`
   - `2020-06-30T23:59:59Z`
   - `2020-10-29T23:59:59Z`
   - `2020-12-31T23:59:59Z`
3. `ratings_drop_processed.jsonl`을 기준으로 user sequence deep dive를 계산한다.
4. 후보별 train eligibility, sequence length, item vocabulary, stream category, known item pressure, online load proxy, refit pressure를 report로 정리한다.
5. 다음 구현에서 필요한 CLI/API 변경점을 구체화한다.

## 진행 결과

산출물:

- Script: `eda/processed/temporal_cutoff_2020_deep_dive.py`
- Report: `eda/processed/outputs/temporal_cutoff_2020_deep_dive_report.md`
- CSV: `eda/processed/outputs/temporal_cutoff_2020_deep_dive.csv`
- Plots:
  - `eda/processed/outputs/temporal_cutoff_2020_post_event_categories.png`
  - `eda/processed/outputs/temporal_cutoff_2020_sequence_lengths.png`
  - `eda/processed/outputs/temporal_cutoff_2020_online_load_proxy.png`

핵심 결과:

- `pre_2020` (`2019-12-31T23:59:59Z`): post 4,589,198 events, train users 12,650, canonical users 508, item vocab 40,973, online rows/event proxy 238.
- `mid_2020` (`2020-06-30T23:59:59Z`): post 3,718,515 events, train users 13,021, canonical users 531, item vocab 43,151, online rows/event proxy 245.
- `q90_2020` (`2020-10-29T23:59:59Z`): post 3,190,916 events, train users 13,273, canonical users 545, item vocab 44,342, online rows/event proxy 254.
- `end_2020` (`2020-12-31T23:59:59Z`): post 2,916,119 events, train users 13,389, canonical users 555, item vocab 45,123, online rows/event proxy 260.

해석:

- 2020 전후 cutoff는 train user/sequence 품질 차이가 크지 않다. train positive sequence p50은 모두 326~327이고 p90은 717~724 수준이다.
- `T`가 늦어질수록 post event는 줄지만, 남는 stream user는 더 긴 active sequence를 갖는 경향이 있어 event당 online recompute row proxy는 증가한다.
- canonical 기본 threshold 1000을 그대로 두면 초기 interest state를 seed할 수 있는 user가 508~555명뿐이다.
- post-`T` event의 75~81%는 신규 user로 들어오므로, canonical seed만으로는 대부분의 streaming 부하가 no-interest/refit 경로로 간다.
- post-`T` item coverage는 92% 이상으로 충분하지만, 7~8% unknown item event는 별도 cold item 처리 정책이 필요하다.

## 검증

- [x] `.venv/bin/python -m py_compile eda/processed/temporal_cutoff_2020_deep_dive.py`
- [x] `.venv/bin/python eda/processed/temporal_cutoff_2020_deep_dive.py --help`
- [x] full deep dive 실행 완료
- [x] report/CSV/plot 산출물 생성
- [x] `git diff --check`

## 완료 조건

- 2020 전후 cutoff별로 현재 파이프라인에 실제 필요한 지표가 정리된다.
- 어떤 `T`를 기본 후보로 둘지 더 구체적으로 판단할 수 있다.
- 다음 구현에서 어떤 옵션과 artifact 계약이 필요한지 명확해진다.
