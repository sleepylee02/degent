# outputs/

모델 가중치, 임베딩, 클러스터링 결과, 시각화 결과 같은 대형 산출물을 두는 로컬 출력 디렉토리다.

Git 추적 정책:

- 추적 가능: `outputs/readme.md`, `outputs/logs/*.log`
- 추적 제외: checkpoint, embedding, clustering result, plot 같은 재생성 가능한 대형 산출물

주요 산출물 계약은 `model/README.md`와 `docs/artifacts.md`를 따른다.

Streaming Phase 3 산출물은 `outputs/stream/` 아래에 둔다.

- `outputs/stream/user_states/{user_id}.json`: raw rating event와 현재 positive projection state
- `outputs/stream/online_embeddings.npz`: Phase 4가 소비할 active positive online embeddings
- `outputs/stream/online_embedding_events.jsonl`: online ingest/extract 실행 요약 로그
- `outputs/stream/interest_states/{user_id}.json`: user별 interest vectors, pending ids, assignment/refit trigger state
- `outputs/stream/interest_assignments.jsonl`: assignment/pending/outlier 결과 로그
- `outputs/stream/refit_requests.jsonl`: triggered refit backend가 소비할 refit 요청 로그
- `outputs/stream/refit_events.jsonl`: triggered refit backend의 close/skip 결과 로그
- `outputs/stream/stream_recommendations.jsonl`: `model.stream.recommend_online`이 append하는 top-K 추천 결과 로그

Streaming trace replay demo 산출물은 `outputs/stream/replay_demo/` 아래에 격리한다. 이 경로는 기본 Phase 3~4-1 산출물을 덮어쓰지 않고, replay dashboard가 읽는 파일 계약이다.

- `outputs/stream/replay_demo/replay_input_events.jsonl`: timestamp-sorted replay input event stream
- `outputs/stream/replay_demo/replay.sqlite`: replay-scoped runtime/state/control-plane store. event progress, stage attempts, user/interest payload, assignment, refit lifecycle, recommendation metadata, embedding snapshot index를 기록
- `outputs/stream/replay_demo/ingress_events.jsonl`: trace-clock event emit schedule/lag 로그
- `outputs/stream/replay_demo/replay_events.jsonl`: event-level replay progress, processing/end-to-end lag, assignment/refit count 로그
- `outputs/stream/replay_demo/replay_summary.json`: dashboard stable entrypoint. speed, trace span, scheduled span, target/actual throughput을 포함
- `outputs/stream/replay_demo/user_states/{user_id}.json`: replay-scoped raw/positive user state
- `outputs/stream/replay_demo/interest_states/{user_id}.json`: replay-scoped interest state
- `outputs/stream/replay_demo/online_embeddings.npz`: replay-scoped active online embeddings
- `outputs/stream/replay_demo/online_embedding_events.jsonl`: replay-scoped online ingest/extract 실행 요약 로그
- `outputs/stream/replay_demo/interest_assignments.jsonl`: replay-scoped assignment/pending/outlier 결과 로그
- `outputs/stream/replay_demo/refit_requests.jsonl`: replay-scoped refit 요청 로그
- `outputs/stream/replay_demo/refit_events.jsonl`: replay-scoped refit close/skip 결과 로그
- `outputs/stream/replay_demo/stream_recommendations.jsonl`: `replay_pipeline --recommend` 사용 시 replay-scoped top-K 추천 결과 로그

Replay dashboard는 `replay_summary.json`의 `paths.replayDb`가 있으면 SQLite를 우선 읽고, 기존 JSONL/JSON 파일은 fallback/debug 경로로 사용한다. 위 파일을 읽기만 하며 생성/수정/삭제하지 않는다. 세부 계약은 `docs/streaming-replay-dashboard-contract.md`를 따른다.
