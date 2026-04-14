# Track Project AI Assistant Entry Points

## Status

Accepted

## Context

이 프로젝트는 여러 LLM 도구를 사용해 협업한다. `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.windsurfrules` 같은 파일이 각 도구의 진입점 역할을 한다. 이 파일들을 로컬 전용 파일로 ignore하면 프로젝트 규칙과 작업 절차가 사람마다 달라질 수 있다.

Codex는 이 저장소에서 `AGENTS.md`를 진입점으로 사용한다. 별도 `.codex` 파일을 만들면 `AGENTS.md`와 역할이 겹쳐 혼란을 만든다.

## Decision

프로젝트용 AI assistant 진입점 파일은 저장소에 포함한다. 상세 운영 규칙은 `PROJECT_GUIDE.md`를 정본으로 두고, 각 도구별 파일은 정본 문서를 읽으라는 얇은 포인터로 유지한다.

`.codex`와 도구별 로컬 상태/설정 디렉토리는 프로젝트 진입점으로 만들지 않는다.

## Consequences

`.gitignore`는 `.codex`, `.codex/`, `.claude/`, `.cursor/`, `.cursorignore`, `.aider*`, `.roo/` 같은 로컬 도구 설정/상태 파일을 계속 제외한다. 새 AI 도구 파일을 저장소에 추가할 때는 실제 도구가 읽는 프로젝트 진입점인지 확인한 뒤 추가한다.
