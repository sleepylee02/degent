# Keep Plan And Experiment Records Local

## Status

Accepted

## Context

`plan/`과 `experiments/`는 작업 중에는 유용하지만, 짧은 계획서, run manifest, metric log, notes가 빠르게 쌓인다. 이 raw 기록을 모두 git에 남기면 GitHub에서 durable project knowledge와 local execution history가 섞여 문서 탐색 비용이 커진다.

그래도 두 디렉터리는 로컬에서는 계속 필요하다. `plan/`은 사람/LLM 협업 계획과 진행 상태를 잡는 작업장이고, `experiments/model/<run_id>/`는 같은 머신에서 run을 재현하거나 비교하는 데 필요한 기록이다.

## Decision

`plan/`, `experiments/`, `outputs/logs/`를 local-first 기록으로 취급한다. 디렉터리 구조와 사용법을 설명하는 파일은 추적하고, 개별 작업/run 기록은 로컬에 둔다.

Git은 아래 구조 파일을 추적한다.

- `plan/.gitkeep`
- `plan/_template.md`
- `plan/active/.gitkeep`
- `plan/active/README.md`
- `plan/done/.gitkeep`
- `plan/done/README.md`
- `plan/expired/.gitkeep`
- `plan/expired/README.md`
- `experiments/.gitkeep`
- `experiments/model/.gitkeep`
- `experiments/model/README.md`
- `outputs/logs/.gitkeep`

장기 보존해야 하는 지식은 `docs/`에 둔다. 로컬 plan이나 experiment에 미래 협업자가 알아야 하는 결론이 있으면 raw 기록을 추적하지 말고 관련 docs 또는 ADR에 요약한다.

## Consequences

기존에 git이 추적하던 개별 `plan/**` 작업 기록과 `experiments/model/<run_id>/**` run 기록은 index에서 제거한다. 구조 파일은 추적 유지한다. 각 개발자의 로컬 workspace에는 기존 작업/run 기록이 계속 남아 있을 수 있다.

새 clone은 디렉터리 구조, plan template, 상태별 README, experiment README를 받는다. GitHub-visible 프로젝트 맥락은 `docs/README.md`, `PROJECT_GUIDE.md`, `README.md`, 관련 ADR에서 확인한다. 새 작업의 로컬 계획과 run metadata는 각자 `plan/`과 `experiments/` 아래에 다시 쌓는다.
