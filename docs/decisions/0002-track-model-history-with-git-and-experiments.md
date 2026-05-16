# Track Model History With Git And Experiments

## Status

Accepted

## Context

`model/prev/`에 이전 모델 코드를 복사해두면 특정 시점의 스냅샷은 남길 수 있다. 하지만 시간이 지나면 그 코드가 언제 기준인지, 왜 바뀌었는지, 현재 코드와 어떤 차이가 중요한지, 계속 실행 가능한 구현인지 알기 어렵다.

이 프로젝트에는 이미 git history, ADR, run별 experiment metadata가 있다. 모델 구조가 `batch/`, `common/`, `stream/`으로 분리된 뒤에는 `prev/`가 clean separation을 흐리고 LLM이 현재 공식 경로와 오래된 실험 코드를 혼동할 수 있다.

## Decision

이전 모델 코드는 `model/prev/` 같은 스냅샷 디렉터리에 보관하지 않는다.

코드 변경 내용은 git commit, `git log`, `git show`, `git diff`로 추적한다. 설계 변경 이유와 장기적으로 참고해야 할 판단은 `docs/decisions/`에 ADR로 기록한다. 실험별 command, git 상태, config, metric, 산출물 참조, 이전 run 대비 관찰은 로컬 `experiments/model/<run_id>/manifest.json`, `metrics.jsonl`, `notes.md`에 기록한다.

`0004-keep-plan-and-experiment-records-local.md` 결정 이후 raw run 기록은 git으로 추적하지 않는다. 장기 보존할 결론은 `docs/`에 요약한다.

과거 구현이 단순 참고가 아니라 계속 실행해야 하는 비교 대상이면 `prev`가 아니라 `model/baselines/`처럼 목적이 명확한 디렉터리를 별도 ADR 또는 계획서로 정의한 뒤 추가한다.

## Consequences

`model/prev/`는 제거한다. 삭제된 파일은 git history에서 복구하거나 확인할 수 있다.

LLM이나 사람이 모델 과거 정보를 찾을 때는 다음 순서를 따른다.

1. 현재 구조와 공식 실행 경로는 `PROJECT_GUIDE.md`와 `model/README.md`에서 확인한다.
2. 왜 그런 구조나 정책을 택했는지는 `docs/decisions/`에서 확인한다.
3. 실험 결과와 이전 run 대비 관찰은 로컬 `experiments/model/<run_id>/`에서 확인한다.
4. 특정 파일의 과거 코드는 git history에서 확인한다.

예시:

```bash
git log -- model/batch/cluster.py model/common/sasrec.py
git show <commit>:model/cluster.py
git diff <old_commit>..<new_commit> -- model/
```
