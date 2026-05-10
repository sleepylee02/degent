# 최근 저장소 업데이트 문서 반영

## 목적

최근 저장소 구조와 코드 변경이 주요 문서에 덜 반영된 부분을 찾아 정리한다.

## 배경

이 프로젝트는 LLM 협업 규칙을 저장소에 함께 싣는 방향이므로 `.gitignore`의 AI 도구 설정 ignore 규칙도 점검한다. Codex는 `AGENTS.md`를 진입점으로 사용하므로 별도 `.codex` 포인터 파일은 만들지 않는다. 실제 파일 구조와 코드 기준으로 `PROJECT_GUIDE.md`, `README.md`, 보조 문서의 불일치를 점검한다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `README.md`
- `todo.md`
- `docs/artifacts.md`
- `docs/data-flow.md`
- `preprocess/README.md`
- `model/README.md`
- `eda/raw/shared.py`
- `preprocess/preprocess_genre/preprocess_genre.py`
- `.gitignore`

## 수정 범위

- 수정:
  - `PROJECT_GUIDE.md`
  - `README.md`
  - `docs/artifacts.md`
  - `docs/data-flow.md`
  - `preprocess/README.md`
  - `model/README.md`
  - `.gitignore`
  - `todo.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL
  - 모델 생성물
  - EDA 생성물

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: 없음
- 호환성 영향: 없음

## 실행 계획

1. 프로젝트 가이드, 실제 파일 구조, AI 도구 진입점 파일을 확인한다.
2. 최근 구조가 문서에 덜 반영된 항목을 찾는다.
3. `.gitignore`가 프로젝트용 AI 설정 파일 추적을 막고 있으면 정리한다.
4. 누락된 보조 전처리, EDA 출력 경로, 모델/로그 산출물 설명을 문서에 반영한다.
5. stale ignore 규칙이 있으면 정리한다.
6. 최종 diff와 공백 오류를 확인한다.

## 검증

- [x] `git diff --check`
- [x] `git status --short`
- [x] 주요 문서에서 stale 경로 검색

## 완료 조건

- `preprocess_genre`와 `data/ml-32m/genre.csv`가 문서에 반영된다.
- raw EDA 현재 출력 경로 `eda/eda_outputs/`가 README에 반영된다.
- 모델 출력과 로그 추적 정책 설명이 `.gitignore`와 모순되지 않는다.
- 실제 도구가 읽는 AI 진입점만 문서에 남고, 불필요한 `.codex` 포인터 파일은 만들지 않는다.

## 반영 내용

- `.gitignore`에서 로컬 AI 도구 설정/상태 파일 ignore 규칙을 유지했다.
- `.gitignore`에서 삭제된 `build_item2idx.py` 관련 잔여 ignore 규칙을 제거했다.
- `docs/decisions/0001-track-project-ai-assistant-configs.md`에 AI 진입점 파일 추적 결정을 기록했다.
- `preprocess/preprocess_genre/`와 `data/ml-32m/genre.csv`를 주요 문서에 반영했다.
- raw EDA 현재 출력 위치를 `eda/eda_outputs/`로 반영하고, `eda/raw/outputs/`는 legacy 출력으로 문서화했다.
- `outputs/embeddings.npy`는 legacy 모델 산출물로 문서화했다.
