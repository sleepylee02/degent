# 모델 실험 메타데이터 버저닝

## 목적

모델 학습, 임베딩 추출, 클러스터링 실험의 차이를 무거운 산출물 없이 추적할 수 있게 한다.

## 배경

현재 모델 산출물은 `outputs/sasrec_cl.pt`, `outputs/embeddings.npz`, `outputs/user_interests.npz`처럼 고정 파일명으로 저장되어 새 실행이 이전 결과를 덮어쓴다. 실행 로그는 timestamp로 남지만 config, 입력 fingerprint, git 상태, 지표가 한 run 단위로 구조화되어 있지는 않다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/`
- `schemas/README.md`
- `model/README.md`
- `model/runtime.py`
- `model/train.py`
- `model/extract.py`
- `model/cluster.py`

## 수정 범위

- 수정:
  - `model/runtime.py`
  - `model/train.py`
  - `model/extract.py`
  - `model/cluster.py`
  - `model/README.md`
  - `PROJECT_GUIDE.md`
  - `docs/artifacts.md`
  - `docs/data-flow.md`
  - `README.md`
  - `todo.md`
- 추가:
  - `experiments/model/README.md`
  - `plan/done/model_experiment_versioning.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL
  - 모델 가중치/임베딩/클러스터링 산출물
  - EDA 생성물

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: 모델 스크립트 실행 시 `experiments/model/<run_id>/manifest.json`, `metrics.jsonl`, `notes.md` 메타데이터가 추가된다.
- 호환성 영향: 기존 기본 산출물 경로는 유지한다.

## 실행 계획

1. 공통 run id, manifest, metrics, 파일 fingerprint 유틸을 `model/runtime.py`에 추가한다.
2. `train.py`가 run metadata와 epoch metrics를 기록하게 한다.
3. `extract.py`가 같은 run id를 이어받아 입력/출력 fingerprint를 기록하게 한다.
4. `cluster.py`가 같은 run id를 이어받아 클러스터링 설정과 summary metrics를 기록하게 한다.
5. 모델 실험 메타데이터 정책을 문서에 반영한다.
6. 문법/정적 검증을 실행한다.

## 검증

- [x] `python3 -m py_compile model/runtime.py model/train.py model/extract.py model/cluster.py`
- [x] `.venv/bin/python model/train.py --help`
- [x] `.venv/bin/python model/extract.py --help`
- [x] `.venv/bin/python model/cluster.py --help`
- [x] `git diff --check`
- [x] `git status --short`

## 완료 조건

- `train.py`, `extract.py`, `cluster.py`가 `--run-id`를 받을 수 있다.
- `train.py` 실행 시 새 run id가 생성되고 `outputs/latest_model_run_id.txt`에 기록된다.
- `extract.py`, `cluster.py`는 `--run-id`가 없으면 latest run id를 사용한다.
- 각 단계가 `experiments/model/<run_id>/manifest.json`에 구조화된 메타데이터를 남긴다.
- 학습 지표와 클러스터링 summary가 `metrics.jsonl`에 남는다.
