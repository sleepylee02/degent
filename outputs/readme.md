# outputs/

모델 가중치, 임베딩, 클러스터링 결과, 시각화 결과 같은 대형 산출물을 두는 로컬 출력 디렉토리다.

Git 추적 정책:

- 추적 가능: `outputs/readme.md`, `.gitkeep` placeholder
- 추적 제외: checkpoint, embedding, clustering result, plot, runtime log 같은 재생성 가능한 산출물

주요 산출물 계약은 `model/README.md`와 `docs/artifacts.md`를 따른다.

신규 temporal/model run의 checkpoint, item2idx, canonical embedding, batch interest state, pre-T user state는 `outputs/pre/<run_label>/` 아래에 모으는 것을 권장한다. pre-T user/interest state의 정본은 per-user JSON directory가 아니라 SQLite seed store다. 예: `outputs/pre/temporal_2022/`.

- `outputs/pre/temporal_2022/sasrec_cl.pt`: pre-T 학습 checkpoint
- `outputs/pre/temporal_2022/item2idx.json`: pre-T item vocabulary
- `outputs/pre/temporal_2022/canonical_embeddings.npz`: pre-T canonical embeddings
- `outputs/pre/temporal_2022/user_interests.npz`: pre-T batch cluster visualization/export source
- `outputs/pre/temporal_2022/state.sqlite`: replay 시작용 pre-T user/interest seed state store
- `outputs/pre/temporal_2022/pre_summary.json`: pre-T state seed summary

Standalone streaming 산출물은 `outputs/stream/` 아래에 둔다. `--runtime-db`나 `--state-db`를 주면 user/interest state는 SQLite에 기록하고, per-user JSON directory는 legacy/debug 옵션이다.

- `outputs/stream/state.sqlite` 또는 지정한 runtime DB: raw/positive user state와 interest state
- `outputs/stream/online_embeddings.npz`: Phase 4가 소비할 active positive online embeddings
- `outputs/stream/online_embedding_events.jsonl`: online ingest/extract 실행 요약 로그
- `outputs/stream/interest_assignments.jsonl`: assignment/pending/outlier 결과 로그
- `outputs/stream/refit_requests.jsonl`: triggered refit backend가 소비할 refit 요청 로그
- `outputs/stream/refit_events.jsonl`: triggered refit backend의 close/skip 결과 로그
- `outputs/stream/stream_recommendations.jsonl`: `model.stream.recommend_online`이 append하는 top-K 추천 결과 로그

Post-T trace replay 신규 산출물은 mode별 root를 분리한다.

- `outputs/post/<run_id>_production/replay_summary.json`: production-only run summary
- `outputs/post/<run_id>_production/production/production.sqlite`: clean production runtime/current-state store
- `outputs/post/<run_id>_production/production/*.jsonl`: replay input, ingress/progress, assignment/refit/recommendation debug logs
- `outputs/post/<run_id>_history/replay_summary.json`: history run summary
- `outputs/post/<run_id>_history/production/production.sqlite`: history run 내부의 production current-state store
- `outputs/post/<run_id>_history/history/history.sqlite`: append-only event/state-version history store
- `outputs/post/<run_id>_history/dashboard_compact/dashboard_compact.sqlite`: history DB에서 만든 dashboard용 compact projection

기존 `outputs/post/<run_label>_events_<N>/`, `outputs/post/<run_label>_full/`, root-level `replay.sqlite` 계약은 legacy/default 호환 경로로 유지한다. Replay dashboard는 `replay_summary.json`의 `paths.replayDb`/`paths.productionDb`가 있으면 SQLite를 우선 읽고, 기존 JSONL/JSON 파일은 fallback/debug 경로로 사용한다. 위 파일을 읽기만 하며 생성/수정/삭제하지 않는다. 세부 계약은 `docs/streaming-replay-dashboard-contract.md`를 따른다.
