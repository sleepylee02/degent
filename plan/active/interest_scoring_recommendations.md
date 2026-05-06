# Interest Scoring Recommendations

## 목적

`outputs/user_interests.npz`의 user interest vector를 받아 아이템별 점수를 계산하고 추천 리스트를 산출하는 batch 로직을 구현한다.


## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/streaming_pipeline.md`
- `schemas/README.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `model/batch/cluster.py`
- `model/common/sasrec.py`

## 수정 범위

- 수정: `model/batch/recommend.py`, `model/README.md`, `model/IMPLEMENTATION_STATUS.md`, `docs/artifacts.md`, `docs/data-flow.md`, `outputs/readme.md`, `todo.md`
- 수정 금지: `data/**/raw/`, 전처리 생성물 CSV/JSONL, 모델 가중치/대형 산출물

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: `outputs/recommendations.csv`, `outputs/recommendations.npz` 추가
- 호환성 영향: 기존 산출물은 읽기만 하며 덮어쓰지 않는다.

## 실행 계획

1. cluster/model/movie metadata 입력 계약을 확인한다.
2. `user_interests.npz` + `sasrec_cl.pt` + `item2idx.json` 기반 scoring/recommend 모듈을 추가한다.
3. 이미 본 영화 제외, cluster별 score breakdown, top-k 저장을 구현한다.
4. smoke 검증과 문서 업데이트를 수행한다.

## 검증

- [x] `python3 -m compileall model`
- [ ] `python3 -m model.batch.recommend --user-id 10202 --top-k 5 --output-csv outputs/test_recommendations.csv --output-npz outputs/test_recommendations.npz`

## 완료 조건

- 추천 산출 batch 명령이 동작한다.
- 추천 CSV/NPZ 계약이 문서화된다.
