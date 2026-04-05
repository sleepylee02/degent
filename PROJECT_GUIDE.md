# degent 프로젝트 가이드

영화 추천 시스템 연구 프로젝트. 여러 명이 각자 LLM을 사용하며 협업한다.

## 디렉토리 구조

```
degent/
├── data/                    # 데이터 저장소
│   ├── docs/                #   연구 제안서 등 참고 문서
│   ├── ml-32m/              #   MovieLens 32M 데이터셋
│   │   ├── raw/             #     원본 CSV (movies, ratings, tags, links)
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
│   ├── drop_movie/          #   전처리 결과에 drop 규칙을 적용하는 후처리
│   ├── drop_rating/         #   movie drop 결과를 rating에 전파하는 후처리
│   └── process_rating/      #   rating row를 user sequence JSONL로 재구성
├── eda/                     # 탐색적 데이터 분석 (EDA)
│   ├── processed/           #   processed 데이터 EDA
│   │   ├── eda_processed.py #     정제 후 데이터 EDA 스크립트
│   │   └── outputs/         #     분석 결과물 (리포트, 차트)
│   └── raw/                 #   raw 데이터 EDA (보존용)
├── plan/                    # 작업 계획서
├── todo.md                  # 할 일 목록
└── requirements.txt         # Python 의존성
```

## 핵심 규칙

### 데이터
- `data/**/raw/`는 원본 데이터. **절대 수정하지 않는다.**
- `raw/` 내부 파일은 `.gitignore`로 추적 제외됨. 각자 로컬에 직접 배치해야 한다.
- 전처리 결과물(`movies_processed.csv` 등)도 git 추적하지 않는다. 스크립트로 재생성한다.

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

### 계획
- 새로운 작업을 시작하기 전에 `plan/` 폴더에 계획서를 작성하거나 확인한다.
- 기존 계획이 있으면 그것을 따르고, 변경이 필요하면 계획서를 먼저 수정한다.

## 환경 설정

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 패키지 추가
- 새 패키지를 설치하면 반드시 `requirements.txt`에 반영한다.
  ```bash
  pip install <패키지> && pip freeze > requirements.txt
  ```
- 불필요한 패키지는 설치하지 않는다. 기존 의존성으로 해결 가능한지 먼저 확인한다.

## 협업 시 주의사항

- 코드를 작성하기 전에 `plan/`, `schemas/`, 이 문서를 먼저 읽어서 현재 맥락을 파악한다.
- 다른 사람이 작업 중인 파일을 동시에 수정하지 않도록 `todo.md`와 `plan/`을 확인한다.
- 새 데이터셋을 추가하면 반드시 `data/<dataset>/readme.md`를 함께 작성한다.

## 이 문서의 유지보수

이 문서(`PROJECT_GUIDE.md`)는 프로젝트의 단일 진실 공급원(Single Source of Truth)이다.
아래 변경이 발생하면 반드시 이 문서도 함께 업데이트한다:

- 디렉토리/폴더 구조가 변경되었을 때 (추가, 이름 변경, 삭제)
- 핵심 규칙이나 컨벤션이 새로 정해지거나 바뀌었을 때
- 새로운 데이터셋, 전처리 파이프라인, 도구가 추가되었을 때
- 환경 설정 방법이 달라졌을 때

AI 도구(Claude Code, Cursor, Codex 등)를 사용할 때도 위 변경을 수행했다면 이 문서를 업데이트할 것을 요청하거나 직접 수정한다.
