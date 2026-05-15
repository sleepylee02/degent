# degent 프로젝트 가이드

영화 추천 시스템 연구 프로젝트. 여러 명이 각자 LLM을 사용하며 협업한다.

## 디렉토리 구조

```
degent/
├── data/                    # 데이터 저장소
│   ├── docs/                #   연구 제안서 등 참고 문서
│   ├── ml-32m/              #   MovieLens 32M 데이터셋
│   │   ├── raw/             #     원본 CSV (movies, ratings, tags, links)
│   │   ├── genre.csv        #     장르 multi-hot 보조 산출물
│   │   └── readme.md        #     데이터셋 설명
│   ├── ml-32m-extension-main/  # ML-32M 확장 데이터셋
│   │   ├── raw/             #     원본 파일
│   │   └── readme.md        #     데이터셋 설명
│   ├── genome_2021/         #   Tag Genome 2021 데이터셋
│   │   ├── raw/             #     원본 파일
│   │   └── readme.md        #     데이터셋 설명
│   ├── movies_processed.csv #   전처리 완료된 통합 영화 데이터 (생성물)
│   ├── movies_processed_drop.csv # drop 규칙 적용 후 학습용 영화 데이터 (생성물)
│   ├── ratings_drop.csv    # drop 규칙을 반영한 학습용 rating 데이터 (생성물)
│   └── ratings_drop_processed.jsonl # user별 rating history JSONL (생성물)
├── schemas/                 # 데이터 스키마 정의 (정본)
│   ├── ml32m/raw/           #   원본 데이터 스키마
│   ├── ml32m/processed/     #   전처리 데이터 스키마
│   └── README.md            #   스키마 컨벤션 규칙
├── preprocess/              # 전처리 스크립트
│   ├── preprocess_movie/    #   영화 데이터 전처리
│   ├── preprocess_genre/    #   장르 multi-hot 보조 산출물 생성
│   ├── drop_movie/          #   전처리 결과에 drop 규칙을 적용하는 후처리
│   ├── drop_rating/         #   movie drop 결과를 rating에 전파하는 후처리
│   ├── process_rating/      #   rating row를 user sequence JSONL로 재구성
│   └── README.md            #   전처리 실행 순서와 입출력 요약
├── replay/                  # C++ rating replay event generator
│   ├── cpp/                 #   replay source
│   ├── Makefile             #   replay binary build
│   └── README.md            #   replay generator/orchestrator usage
├── dashboard/               # 클러스터링 결과 시각화 대시보드
├── model/                   # SASRec + Contrastive Loss 추천 모델
│   ├── batch/               #   batch 모델 파이프라인 실행 entrypoint
│   │   ├── train.py         #     학습 실행 → sasrec_cl.pt + sasrec_cl_best.pt + item2idx.json
│   │   ├── extract_canonical.py #  event당 canonical 히든스테이트 추출 → canonical_embeddings.npz
│   │   ├── cluster.py       #     유저별 UMAP + HDBSCAN → user_interests.npz + batch/interest_states/
│   │   ├── export_clusters.py #   user_interests.npz → dashboard table export
│   │   ├── recommend.py     #     batch interest vector 기반 top-K 추천 산출
│   │   └── visualize_clusters.py # 클러스터 변화 시각화 → outputs/viz/
│   ├── common/              #   batch/stream 공통 모델 유틸
│   │   ├── canonical.py     #     canonical event window/Dataset/검증 helper
│   │   ├── cluster.py       #     UMAP+HDBSCAN, GPU/CPU backend, top_genres_for_cluster
│   │   ├── dataset.py       #     데이터 로드/전처리/Dataset
│   │   ├── sasrec.py        #     SASRecCL 모델, Contrastive Loss
│   │   └── runtime.py       #     로그, run metadata, device/seed 유틸
│   ├── stream/              #   streaming/replay pipeline
│   │   ├── seed_pre_t_state.py # cutoff 이전 user state seed 생성
│   │   ├── extract_online.py #    online rating ingest → user state + canonical embedding
│   │   ├── interest_assign.py #   online interest assignment + refit request 기록
│   │   ├── cluster_refit.py #     triggered cluster refit backend (genre labeling 포함)
│   │   ├── recommend_online.py #  streaming interest state 기반 top-K 추천 산출
│   │   ├── runtime_store.py #    SQLite runtime/state store schema/API
│   │   ├── runtime_report.py #   replay.sqlite 병목/상태 요약 report CLI
│   │   ├── trace_replay.py #     N배속 trace-clock replay runner
│   │   └── replay_pipeline.py #   trace replay 공식 entrypoint 호환 래퍼
│   ├── IMPLEMENTATION_STATUS.md # 구현 현황, 산출물 상태, 보류 보완 후보
│   └── README.md            #   모델 파이프라인 설명
├── eda/                     # 탐색적 데이터 분석 (EDA)
│   ├── eda_outputs/         #   raw EDA 현재 출력 위치
│   ├── processed/           #   processed 데이터 EDA
│   │   ├── eda_processed.py #     정제 후 데이터 EDA 스크립트
│   │   └── outputs/         #     분석 결과물 (리포트, 차트)
│   └── raw/                 #   raw 데이터 EDA (보존용)
├── outputs/                 # 모델/stream 산출물 (대형 산출물과 실행 로그는 git 추적 제외)
│   ├── pre/                 #   temporal cutoff 이전 batch/model/state 산출물 (e.g. temporal_2022/)
│   ├── post/                #   temporal cutoff 이후 replay/runtime 산출물 (e.g. temporal_2022/)
│   └── stream/              #   default/standalone streaming/replay 산출물
├── experiments/             # 실험 메타데이터 (가벼운 manifest/metrics/notes)
│   └── model/               #   모델 run별 추적 기록
├── docs/                    # LLM/사람이 함께 보는 보조 문서
│   ├── current-pipeline-snapshot.md # 현재 batch/streaming 구현과 문제 포인트
│   ├── data-flow.md         #   raw -> processed -> model -> dashboard 흐름
│   ├── artifacts.md         #   원본/생성물 목록과 수정 가능 여부
│   ├── part-contracts.md    #   협업용 파트별 담당 파일/input/output/endpoint 계약
│   ├── streaming-e2e-pipeline.md # streaming replay e2e 실행/인계 문서
│   ├── streaming-replay-dashboard-contract.md # Phase 5/6 replay artifact 계약
│   └── decisions/           #   중요한 설계 결정 기록
├── plan/                    # 작업 계획서
│   ├── _template.md         #   새 계획서 템플릿
│   ├── active/              #   진행 중 계획
│   ├── done/                #   완료된 계획
│   └── expired/             #   현재 구조 이전의 만료된 계획
├── AGENTS.md                # Codex 등 LLM 작업 진입점
├── CLAUDE.md                # Claude 작업 진입점
├── .cursorrules             # Cursor 작업 진입점
├── .windsurfrules           # Windsurf 작업 진입점
├── todo.md                  # 할 일 목록
└── requirements.txt         # Python 의존성
```

## 핵심 규칙

### 데이터
- `data/**/raw/`는 원본 데이터. **절대 수정하지 않는다.**
- `raw/` 내부 파일은 `.gitignore`로 추적 제외됨. 각자 로컬에 직접 배치해야 한다.
- 전처리 결과물(`movies_processed.csv` 등)도 git 추적하지 않는다. 스크립트로 재생성한다.
- `data/ml-32m/genre.csv`는 장르 multi-hot 보조 산출물이며 현재 저장소에 포함된 예외 파일이다.

### 스키마
- 컬럼 추가/변경/삭제는 **`schemas/`에서 먼저 정의**한 뒤 코드를 수정한다.
- 스키마 컨벤션은 `schemas/README.md` 참고.
- 호환되지 않는 변경은 버전을 올린다 (e.g. `v2.schema.yaml`).
- `raw` 단계의 시간 컬럼은 원본을 보존한다. `processed` 단계에서는 의미 기반 이름을 사용한다.
  예: `releaseYear`, `ratedAt`, `taggedAt`

### 전처리
- 전처리 스크립트는 `preprocess/` 아래에 둔다.
- 입력: `data/**/raw/`, 출력: `data/` 루트 또는 해당 데이터셋 폴더.
- 후처리(drop) 스크립트는 기존 전처리 산출물(`data/movies_processed.csv`)을 입력으로 사용할 수 있다.
- `processed` 단계의 이벤트 시각은 가능한 한 UTC ISO 8601 문자열로 저장한다.
- 검증 리포트(`validation_report.json`, `bad_rows.csv`)는 해당 전처리 폴더에 저장한다.
- `preprocess/preprocess_genre/`는 `data/ml-32m/genre.csv`를 만드는 보조 단계이며, 현재 메인 전처리/drop/user-sequence 파이프라인의 필수 단계는 아니다.

### 계획
- 새로운 작업을 시작하기 전에 `todo.md`와 `plan/active/`를 확인한다.
- 새 계획서는 `plan/_template.md`를 기준으로 작성한다.
- 진행 중 계획은 `plan/active/`, 완료된 계획은 `plan/done/`에 둔다.
- 이전 문서 구조나 더 이상 유효하지 않은 계획은 `plan/expired/`에 보관한다.
- 기존 계획이 있으면 그것을 따르고, 변경이 필요하면 계획서를 먼저 수정한다.
- 현황 점검, 문서 인벤토리, 짧은 정리처럼 별도 실행 계획보다 추적 문서가 적합한 작업은 active plan을 생략할 수 있다. 이 경우 `todo.md`에 기준 문서와 생략 사유를 명시한다.

### 대시보드
- 대시보드 코드는 `dashboard/` 아래에 둔다.
- 시각화용 입력 산출물은 스크립트로 재생성 가능해야 하며, 원본 데이터처럼 수동 편집하지 않는다.
- batch cluster 결과를 dashboard에 연결할 때는 `python3 -m model.batch.export_clusters`로 `outputs/user_interests.npz`를 `data/clustering/user_clusters.parquet` 등 테이블 포맷으로 변환한다.
- Replay monitor는 replay output root 아래 artifact를 읽기만 하며 replay/stream state를 생성하거나 수정하지 않는다. 기본 demo root는 `outputs/stream/replay_demo/`이고, temporal post-T run은 `outputs/post/<run_label>/`를 사용한다.
- 인터랙티브 시각화를 위한 새 패키지를 추가하면 반드시 `requirements.txt`에 반영한다.

### 모델 실험
- 모델 가중치, 임베딩, 클러스터링 결과 같은 대형 산출물은 `outputs/`에 두고 git으로 추적하지 않는다.
- 새 temporal run의 pre-T checkpoint, item2idx, canonical embedding, batch interest state, user state는 `outputs/pre/<run_label>/` 아래에 모은다. post-T replay/runtime 산출물은 `outputs/post/<run_label>/` 아래에 둔다. 기존 `outputs/sasrec_cl.pt` 같은 루트 경로는 default/legacy 호환 경로로 유지한다.
- `python3 -m model.batch.train`, `python3 -m model.batch.extract_canonical`, `python3 -m model.batch.cluster`, `python3 -m model.batch.recommend`는 run별 메타데이터를 `experiments/model/<run_id>/`에 기록한다.
- `python3 -m model.stream.seed_pre_t_state`는 temporal cutoff 이전 rating history로 replay 시작용 user state를 만들고, 기존 interest state가 있으면 pre-cutoff active raw event를 processed로 표시한다.
- `python3 -m model.stream.extract_online`은 raw rating event를 user state에 저장하고 active positive embedding을 `outputs/stream/` 아래에 기록한다.
- `python3 -m model.stream.interest_assign`은 active positive embedding을 interest state에 assign하고 refit request를 `outputs/stream/` 아래에 기록한다.
- `python3 -m model.stream.cluster_refit`은 refit request를 소비해 user별 interest state를 갱신한다.
- `python3 -m model.stream.recommend_online`은 streaming interest state와 item embedding으로 top-K 추천을 만들고 `outputs/stream/stream_recommendations.jsonl`에 기록한다.
- `python3 -m model.stream.replay_pipeline`은 `replay/bin/rating_replay` 출력 또는 기존 replay JSONL을 timestamp trace로 읽고, `--speed N` 기준 virtual clock에 맞춰 event를 주입한다. replay 산출물은 `--output-root` 아래에 격리하며, 기본값은 `outputs/stream/replay_demo/`다. Temporal post-T run은 `outputs/post/<run_label>/`를 output root로 사용한다. `replay.sqlite`, `ingress_events.jsonl`, event-level `replay_events.jsonl`, `replay_summary.json`에 runtime state, schedule/lag/throughput metric을 남긴다. `--recommend`를 주면 event 처리 후 `recommend_online`도 호출해 output root의 `stream_recommendations.jsonl`을 남긴다.
- `python3 -m model.stream.runtime_report`는 `replay.sqlite`를 읽어 stage latency, event lag, refit lifecycle, assignment/repeated-processing, user state progress를 요약한다.
- `experiments/model/<run_id>/manifest.json`과 `metrics.jsonl`은 실험 비교용 기록이다.
- `experiments/model/<run_id>/notes.md`는 사람이 run 목적, 이전 run 대비 차이, 관찰 내용을 적는 메모다.
- 대형 파일의 재현 근거는 파일 경로, size/mtime, 가능한 경우 SHA256, git 상태, config, metric으로 남긴다.

### 모델 변경 이력 추적
- 이전 모델 코드는 `model/prev/` 같은 스냅샷 디렉터리에 복사하지 않는다.
- 코드가 어떤 식으로 바뀌었는지는 git commit, `git log`, `git show`, `git diff`로 추적한다.
- 왜 바꿨는지는 `docs/decisions/`의 ADR에 기록한다.
- 실험별 config, metric, 산출물 참조, 이전 run 대비 관찰은 `experiments/model/<run_id>/manifest.json`, `metrics.jsonl`, `notes.md`에 기록한다.
- 현재 구현 상태와 보류 결정은 `model/IMPLEMENTATION_STATUS.md`에서 먼저 확인한다.
- 비교 가능한 오래된 구현을 계속 실행해야 하는 경우에만 `model/baselines/`처럼 목적이 명확한 디렉터리를 별도 결정 후 추가한다.

## 환경 설정

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Python 패키지 설치/제거/업데이트는 반드시 프로젝트 루트의 repo-local `.venv`에서만 수행한다.
- 시스템 Python, `sudo pip`, OS package manager, 전역 CUDA/toolkit 설치 같은 system-level 환경 변경은 이 프로젝트 작업 범위에서 금지한다.
- GPU 의존성은 현재 `torch==2.5.1+cu121`과 RAPIDS/cuML `25.10.0` 계열을 기준으로 고정한다. 관련 결정과 재검토 조건은 `docs/decisions/0003-pin-rapids-cuml-gpu-dependencies.md`에 기록한다.

### 패키지 추가
- 새 패키지를 설치하면 반드시 `requirements.txt`에 반영한다.
  ```bash
  .venv/bin/pip install <패키지> && .venv/bin/pip freeze > requirements.txt
  ```
- 불필요한 패키지는 설치하지 않는다. 기존 의존성으로 해결 가능한지 먼저 확인한다.

## 협업 시 주의사항

- 코드를 작성하기 전에 `todo.md`, `plan/active/`, `schemas/`, 이 문서를 먼저 읽어서 현재 맥락을 파악한다.
- 다른 사람이 작업 중인 파일을 동시에 수정하지 않도록 `todo.md`와 `plan/`을 확인한다.
- 새 데이터셋을 추가하면 반드시 `data/<dataset>/readme.md`를 함께 작성한다.
- 프로젝트용 AI 진입점 파일(`AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.windsurfrules`)은 협업 맥락을 공유하기 위해 저장소에 포함한다.
- `.codex`, `.codex/`, `.claude/`, `.cursor/`, `.cursorignore`, `.aider*`, `.roo/` 같은 도구별 로컬 설정/상태 파일은 저장소에 포함하지 않는다. 새 도구 진입점을 추가할 때는 실제 도구가 읽는 파일인지 먼저 확인한다.

## LLM 작업 절차

AI 도구(Claude Code, Cursor, Codex 등)는 작업 전에 아래 순서를 따른다.

1. `PROJECT_GUIDE.md`를 먼저 읽는다.
2. `todo.md`, `plan/active/`, 관련 `schemas/README.md`, 해당 모듈 README를 확인한다.
   - 모델 과거 정보가 필요하면 `model/README.md`의 변경 이력 안내를 따른다. 우선순위는 `docs/decisions/` → `experiments/model/` → git history다.
3. 단순 질의, 한 파일 안의 경미한 수정, 현황 점검/문서 인벤토리 작업이 아니라면, 작업 전에 `plan/_template.md`를 기준으로 `plan/active/`에 계획서를 작성하고 `todo.md`의 Active에 등록한다.
4. 이미 관련 active plan이 있으면 새 계획서를 만들지 않고 기존 계획서를 따른다. 범위, 산출물, 검증 방법이 바뀌면 코드보다 계획서와 `todo.md`를 먼저 갱신한다.
5. 스키마 변경이 있으면 코드보다 `schemas/`를 먼저 수정한다.
6. `data/**/raw/`와 생성물 CSV/JSONL은 직접 수정하지 않는다.
7. 코드나 파이프라인 변경 후 가능한 검증 명령을 실행하고, 계획서가 있는 작업은 검증 결과를 계획서에 반영한다.
8. 계획된 작업을 완료하면 관련 계획서를 `plan/active/`에서 `plan/done/`으로 옮기고 `todo.md`의 Active 항목을 Done으로 정리한다. 막힌 작업은 Blocked로 옮기고 이유를 적는다.
9. 디렉토리 구조, 규칙, 파이프라인, 산출물 계약이 바뀌면 `PROJECT_GUIDE.md`도 함께 수정한다.

## 이 문서의 유지보수

이 문서(`PROJECT_GUIDE.md`)는 프로젝트의 단일 진실 공급원(Single Source of Truth)이다.
아래 변경이 발생하면 반드시 이 문서도 함께 업데이트한다:

- 디렉토리/폴더 구조가 변경되었을 때 (추가, 이름 변경, 삭제)
- 핵심 규칙이나 컨벤션이 새로 정해지거나 바뀌었을 때
- 새로운 데이터셋, 전처리 파이프라인, 도구가 추가되었을 때
- 환경 설정 방법이 달라졌을 때

AI 도구(Claude Code, Cursor, Codex 등)를 사용할 때도 위 변경을 수행했다면 이 문서를 업데이트할 것을 요청하거나 직접 수정한다.
