# outputs/

모델 가중치, 임베딩, 클러스터링 결과, 시각화 결과 같은 대형 산출물을 두는 로컬 출력 디렉토리다.

Git 추적 정책:

- 추적 가능: `outputs/readme.md`, `.gitkeep` placeholder
- 추적 제외: checkpoint, embedding, clustering result, plot, runtime log 같은 재생성 가능한 산출물

주요 산출물 계약은 `model/README.md`와 `docs/artifacts.md`를 따른다.

신규 temporal/model run의 checkpoint, item2idx, canonical embedding, batch interest state, pre-T user state는 `outputs/pre/<run_label>/` 아래에 모으는 것을 권장한다. 예: `outputs/pre/temporal_2022/`.

- `outputs/pre/temporal_2022/sasrec_cl.pt`: pre-T 학습 checkpoint
- `outputs/pre/temporal_2022/item2idx.json`: pre-T item vocabulary
- `outputs/pre/temporal_2022/canonical_embeddings.npz`: pre-T canonical embeddings
- `outputs/pre/temporal_2022/user_interests.npz`: pre-T batch cluster visualization/export source
- `outputs/pre/temporal_2022/user_states/{user_id}.json`: replay 시작용 pre-T user state
- `outputs/pre/temporal_2022/interest_states/{user_id}.json`: replay 시작용 pre-T interest state
- `outputs/pre/temporal_2022/pre_summary.json`: pre-T state seed summary

Streaming Phase 3 산출물은 `outputs/stream/` 아래에 둔다.

- `outputs/stream/user_states/{user_id}.json`: raw rating event와 현재 positive projection state
- `outputs/stream/online_embeddings.npz`: Phase 4가 소비할 active positive online embeddings
- `outputs/stream/online_embedding_events.jsonl`: online ingest/extract 실행 요약 로그
- `outputs/stream/interest_states/{user_id}.json`: user별 interest vectors, pending ids, assignment/refit trigger state
- `outputs/stream/interest_assignments.jsonl`: assignment/pending/outlier 결과 로그
- `outputs/stream/refit_requests.jsonl`: triggered refit backend가 소비할 refit 요청 로그
- `outputs/stream/refit_events.jsonl`: triggered refit backend의 close/skip 결과 로그
- `outputs/stream/stream_recommendations.jsonl`: `model.stream.recommend_online`이 append하는 top-K 추천 결과 로그

Streaming trace replay demo 산출물은 기본적으로 `outputs/stream/replay_demo/` 아래에 격리한다. Temporal post-T replay는 `outputs/post/<run_label>/` 같은 별도 output root를 사용한다. 두 경로 모두 같은 파일 계약을 따른다.

- `<replay_output_root>/replay_input_events.jsonl`: timestamp-sorted replay input event stream
- `<replay_output_root>/replay.sqlite`: replay-scoped runtime/state/control-plane store. event progress, stage attempts, user/interest payload, assignment, refit lifecycle, recommendation metadata, embedding snapshot index를 기록
- `<replay_output_root>/ingress_events.jsonl`: trace-clock event emit schedule/lag 로그
- `<replay_output_root>/replay_events.jsonl`: event-level replay progress, processing/end-to-end lag, assignment/refit count 로그
- `<replay_output_root>/replay_summary.json`: dashboard stable entrypoint. speed, trace span, scheduled span, target/actual throughput을 포함
- `<replay_output_root>/user_states/{user_id}.json`: replay-scoped raw/positive user state
- `<replay_output_root>/interest_states/{user_id}.json`: replay-scoped interest state
- `<replay_output_root>/online_embeddings.npz`: replay-scoped active online embeddings
- `<replay_output_root>/online_embedding_events.jsonl`: replay-scoped online ingest/extract 실행 요약 로그
- `<replay_output_root>/interest_assignments.jsonl`: replay-scoped assignment/pending/outlier 결과 로그
- `<replay_output_root>/refit_requests.jsonl`: replay-scoped refit 요청 로그
- `<replay_output_root>/refit_events.jsonl`: replay-scoped refit close/skip 결과 로그
- `<replay_output_root>/stream_recommendations.jsonl`: `replay_pipeline --recommend` 사용 시 replay-scoped top-K 추천 결과 로그

Replay dashboard는 `replay_summary.json`의 `paths.replayDb`가 있으면 SQLite를 우선 읽고, 기존 JSONL/JSON 파일은 fallback/debug 경로로 사용한다. 위 파일을 읽기만 하며 생성/수정/삭제하지 않는다. 세부 계약은 `docs/streaming-replay-dashboard-contract.md`를 따른다.
