# LLM 친화적 프로젝트 문서 구조 정리

## 목적

여러 LLM 도구가 이 저장소에서 같은 순서로 맥락을 파악하고, 원본 데이터와 생성물을 안전하게 다루도록 문서 구조를 정리한다.

## 배경

현재 `PROJECT_GUIDE.md`가 프로젝트 운영 규칙의 정본 역할을 하고 있고, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.windsurfrules`는 모두 이 문서를 읽도록 얇게 연결되어 있다. 여기에 `todo.md`, `docs/`, `plan/active/`, `plan/done/`, `plan/_template.md`, `preprocess/README.md`를 추가해 작업 상태와 산출물 규칙을 더 쉽게 찾게 한다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `README.md`
- 기존 `plan/*.md`
- `schemas/README.md`
- `model/README.md`
- `dashboard/README.md`

## 수정 범위

- 수정:
  - `PROJECT_GUIDE.md`
  - `README.md`
  - `todo.md`
  - `plan/_template.md`
  - `plan/active/llm_project_structure.md`
  - `docs/data-flow.md`
  - `docs/artifacts.md`
  - `docs/decisions/README.md`
  - `preprocess/README.md`
- 수정 금지:
  - `data/**/raw/`
  - 전처리 생성물 CSV/JSONL
  - 기존 계획서 본문

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 변경: 없음
- 호환성 영향: 없음

## 실행 계획

1. 계획/문서 디렉토리 골격을 추가한다.
2. 기존 top-level `plan/*.md`는 이전 문서 구조 기준 계획서로 보고 `plan/expired/`에 보관한다.
3. 작업 상태와 계획 템플릿 문서를 추가한다.
4. 데이터 흐름과 산출물 규칙 문서를 추가한다.
5. 전처리 폴더 README를 추가한다.
6. `PROJECT_GUIDE.md`에 새 구조와 LLM 작업 절차를 반영한다.
7. `README.md`에 정본/빠른 안내 역할 구분을 보강한다.
8. 최종 구조와 diff를 확인한다.

## 검증

- [x] `find`로 새 파일과 디렉토리 구조 확인
- [x] `git diff --stat`로 변경 범위 확인
- [x] `git diff`로 의도하지 않은 변경 확인

## 완료 조건

- `PROJECT_GUIDE.md`가 새 문서 구조를 설명한다.
- `todo.md`와 `plan/active/`에서 진행 중 작업을 확인할 수 있다.
- `docs/artifacts.md`에서 원본/생성물 수정 가능 여부를 확인할 수 있다.
- `preprocess/README.md`에서 전처리 실행 순서와 입출력을 확인할 수 있다.
