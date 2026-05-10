# Streaming Pipeline Phase 1: 모델 구조 재정리

## 목적

`model/` 아래 기존 batch 파이프라인과 앞으로 추가할 streaming 파이프라인의 경계를 분리한다.

Phase 1은 behavior-preserving structure refactor다. 알고리즘, embedding 계약, clustering 방식, 산출물 포맷은 바꾸지 않는다. Canonical event embedding 전환은 Phase 2에서 별도 계획 후 진행한다.

## 배경

현재 모델 코드는 `model/` 루트에 batch 실행 파일과 공통 모듈이 섞여 있다.

- `train.py`, `extract.py`, `cluster.py`, `visualize_clusters.py`: batch 실행 entrypoint
- `dataset.py`, `model.py`, `runtime.py`: batch와 stream 양쪽에서 재사용될 공통 로직

Streaming hybrid pipeline을 붙이려면 batch 실행, stream 실행, 공통 로직을 명확히 나눠야 한다. 기존 top-level wrapper를 남기면 legacy artifact가 계속 쌓이므로, Phase 1에서는 clean break를 선택한다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `todo.md`
- `plan/active/streaming_pipeline.md`
- `model/README.md`
- `model/IMPLEMENTATION_STATUS.md`
- `docs/data-flow.md`
- `model/train.py`
- `model/extract.py`
- `model/cluster.py`
- `model/visualize_clusters.py`
- `model/dataset.py`
- `model/model.py`
- `model/runtime.py`

## 수정 범위

- 수정:
  - `model/` 구조
  - `model/README.md`
  - `PROJECT_GUIDE.md`
  - `README.md`
  - `docs/data-flow.md`
  - `docs/decisions/`
  - `experiments/model/README.md`
  - `todo.md`
  - 필요 시 `model/IMPLEMENTATION_STATUS.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL
  - `outputs/` 대형 산출물
  - Phase 2 이후에 바꿀 embedding/clustering 알고리즘

## 결정 사항

- 기존 `python model/train.py` 형식의 top-level 실행 파일은 유지하지 않는다.
- 새 공식 실행 방식은 `python3 -m model.batch.<entrypoint>`다.
- 기존 top-level wrapper는 만들지 않는다.
- `model/model.py`는 `model/common/sasrec.py`로 이동해 이름 충돌을 줄인다.
- import는 `model.common.*` absolute import로 통일한다.
- `stream/`에는 skeleton 파일만 만들고 실제 streaming 로직은 구현하지 않는다.
- 이전 모델 코드는 `model/prev/`에 복사 보관하지 않는다.
- 모델 변경 이력은 git history, 설계 결정은 `docs/decisions/`, 실험별 비교는 `experiments/model/<run_id>/`로 추적한다.
- 계속 실행해야 하는 비교 구현이 생기면 `prev`가 아니라 별도 결정 후 `model/baselines/` 같은 명확한 경로를 사용한다.

## 목표 구조

```text
model/
├── __init__.py
├── batch/
│   ├── __init__.py
│   ├── train.py
│   ├── extract.py
│   ├── cluster.py
│   └── visualize_clusters.py
├── common/
│   ├── __init__.py
│   ├── dataset.py
│   ├── sasrec.py
│   └── runtime.py
├── stream/
│   ├── __init__.py
│   ├── extract_online.py
│   ├── interest_assign.py
│   ├── drift_detector.py
│   └── cluster_refit.py
└── README.md
```

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: 없음
- 호환성 영향:
  - 기존 `python model/train.py`, `python model/extract.py`, `python model/cluster.py`, `python model/visualize_clusters.py` 명령은 더 이상 공식 지원하지 않는다.
  - 새 공식 명령은 아래와 같다.

```bash
python3 -m model.batch.train
python3 -m model.batch.extract
python3 -m model.batch.cluster
python3 -m model.batch.visualize_clusters
```

## 실행 계획

1. `model/batch`, `model/common`, `model/stream` 디렉터리와 `__init__.py`를 만든다.
2. batch entrypoint를 `model/batch/`로 이동한다.
   - `model/train.py` -> `model/batch/train.py`
   - `model/extract.py` -> `model/batch/extract.py`
   - `model/cluster.py` -> `model/batch/cluster.py`
   - `model/visualize_clusters.py` -> `model/batch/visualize_clusters.py`
3. 공통 모듈을 `model/common/`으로 이동한다.
   - `model/dataset.py` -> `model/common/dataset.py`
   - `model/model.py` -> `model/common/sasrec.py`
   - `model/runtime.py` -> `model/common/runtime.py`
4. import를 `model.common.*` 기준으로 수정한다.
5. `model/stream/` skeleton 파일을 추가한다.
   - 각 파일에는 Phase 3/4에서 구현 예정이라는 docstring과 minimal CLI placeholder만 둔다.
   - 실제 online embedding, assignment, drift/refit 로직은 구현하지 않는다.
6. 문서를 갱신한다.
   - `PROJECT_GUIDE.md`: 모델 디렉터리 구조와 실행 명령 반영
   - `model/README.md`: 새 구조, 새 실행 명령, stream skeleton 역할 반영
   - `docs/data-flow.md`: model 실행 명령 반영
   - `README.md`: model 실행 명령 반영
   - `model/IMPLEMENTATION_STATUS.md`: 기존 산출물은 old top-level 실행 로그 기반이라는 점이 필요하면 보강
7. `todo.md`의 Phase 1 상태를 갱신한다.
8. 후속 정리로 `model/prev/`를 제거하고, 과거 모델 정보 탐색 경로를 `PROJECT_GUIDE.md`, `model/README.md`, `docs/decisions/`, `experiments/model/README.md`에 문서화한다.

## 검증

- [x] 새 batch entrypoint help가 실행된다.

```bash
python3 -m model.batch.train --help
python3 -m model.batch.extract --help
python3 -m model.batch.cluster --help
python3 -m model.batch.visualize_clusters --help
```

- [x] stream skeleton help 또는 placeholder 실행이 가능하다.

```bash
python3 -m model.stream.extract_online --help
python3 -m model.stream.interest_assign --help
python3 -m model.stream.drift_detector --help
python3 -m model.stream.cluster_refit --help
```

- [x] 기존 top-level 명령이 문서에서 공식 실행 경로로 남아 있지 않다.
- [x] `rg "from dataset|from model import|from runtime"` 결과에 오래된 import가 남아 있지 않다.
- [x] `PROJECT_GUIDE.md`, `model/README.md`, `docs/data-flow.md`, `README.md`의 모델 구조/명령이 새 구조와 일치한다.
- [x] `model/prev/`를 제거하고, 과거 모델 정보는 git history, ADR, experiment metadata로 찾도록 문서화했다.
- [x] `find model -maxdepth 2 -type d` 결과에 `model/prev`가 남아 있지 않다.
- [x] 오래된 `prev` README 문구와 legacy 파일명 참조가 남아 있지 않다. 정책 설명의 `model/prev/` 언급은 제거 이유를 설명하기 위한 의도된 참조다.

검증 메모:

- 로컬 shell에는 `python` 명령이 없고 시스템 `python3`에는 프로젝트 의존성이 설치되어 있지 않다.
- 실제 smoke test는 프로젝트 `.venv`의 `.venv/bin/python -m ... --help`로 통과했다.
- 가상환경을 활성화한 뒤에는 문서의 `python3 -m ...` 명령을 사용한다.

## 완료 조건

- `model/`이 `batch/`, `common/`, `stream/` 구조로 분리된다.
- 새 공식 실행 명령은 `python3 -m model.batch.*`로 문서화된다.
- top-level wrapper 없이 clean separation이 유지된다.
- batch 알고리즘과 산출물 계약은 Phase 1에서 바뀌지 않는다.
- Phase 2가 canonical event embedding 전환 계획을 작성할 수 있는 구조가 준비된다.
- LLM과 사람이 과거 모델 정보를 찾을 때 `prev`가 아니라 `docs/decisions/`, `experiments/model/`, git history를 사용하도록 안내된다.
