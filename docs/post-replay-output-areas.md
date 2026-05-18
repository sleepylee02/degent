# POST Replay Output Areas

이 문서는 post-T streaming replay 산출물을 목적에 따라 `outputs/post/<run_id>_production/`과 `outputs/post/<run_id>_history/` 두 output root로 분리하기 위한 준비 문서다. 목표는 처리 로직을 두 벌로 fork하지 않고 같은 replay/streaming stage를 사용하되, clean production benchmark 산출물과 history/dashboard 분석 산출물을 파일/디렉터리 단위로 분리해서 남기는 것이다.

## Output Area Summary

```text
outputs/post/<run_id>_production/
  production/
    production.sqlite
  -> clean production-only replay output
  -> latest cache, runtime metric, recovery/debug 중심
  -> --history-mode off 기본 실행

outputs/post/<run_id>_history/
  production/
    production.sqlite
  history/
    history.sqlite
  dashboard_compact/
    dashboard_compact.sqlite
  -> history/dashboard 분석용 replay output
  -> 같은 history run 안의 production current state + append-only history
  -> compact는 history를 입력으로 만든 dashboard projection
```

Production-only run과 history run은 output root를 분리한다. Production-only run은 clean production benchmark 용도라 `production/production.sqlite`만 남긴다. History run은 dashboard/research 분석 용도라 같은 run 안의 `production/production.sqlite`, `history/history.sqlite`, `dashboard_compact/dashboard_compact.sqlite`를 남긴다.

History run의 `production/production.sqlite`는 그 history run 내부의 current runtime/state output이다. 별도로 돌린 `outputs/post/<run_id>_production/production/production.sqlite`와 파일도, run root도 다르다. Dashboard compact projection은 replay 처리 중 직접 쓰는 값이 아니라, replay 이후 `compact_dashboard.py` 같은 별도 step에서 `history/`를 읽어 만드는 파생물이다.

## Output Layout

권장 산출물 구조:

```text
outputs/post/<run_id>_production/
  replay_summary.json
  production/
    production.sqlite
    ingress_events.jsonl
    replay_events.jsonl
    stream_recommendations.jsonl

outputs/post/<run_id>_history/
  replay_summary.json
  production/
    production.sqlite
    ingress_events.jsonl
    replay_events.jsonl
    stream_recommendations.jsonl
  history/
    history.sqlite
  dashboard_compact/
    dashboard_compact.sqlite
```

역할:

| artifact | role |
|---|---|
| `production/production.sqlite` | production runtime/state store. 최신 state/cache, progress, metric, recovery/debug 중심 |
| `production/*.jsonl` | legacy/debug replay event log와 recommendation log |
| `history/history.sqlite` | append-only full-ish history store. event/state-version 분석과 compact projection 원천 |
| `dashboard_compact/dashboard_compact.sqlite` | dashboard 전용 경량 projection. `history/history.sqlite`에서 후처리로 생성 |

기존 root-level `replay.sqlite`는 legacy/default runtime DB 이름으로 계속 허용한다. 신규 output-root 계약을 쓰는 run에서는 `replay_summary.json`의 `paths`에 산출물 디렉터리와 주요 DB 경로를 명시한다.

## File Dependency

`production/production.sqlite`와 `history/history.sqlite`는 물리적으로 독립적인 SQLite 파일이다. 각 파일은 별도로 열고 읽을 수 있으며, write connection도 분리한다.

Clean production output과 history/dashboard output을 모두 깔끔하게 남기려면 replay를 두 번 실행한다.

```text
production-only run:
replay input events
  -> production processing logic
       -> outputs/post/<run_id>_production/production/production.sqlite

history run:
replay input events
  -> production processing logic
       -> outputs/post/<run_id>_history/production/production.sqlite
       -> outputs/post/<run_id>_history/history/history.sqlite
```

History run에서 `history/history.sqlite`는 `production/production.sqlite`를 나중에 읽어서 만드는 후처리 산출물이 아니다. 같은 replay event 처리 중에 production state update와 나란히 쓰는 append-only side log다.

즉 `--history-mode history`를 켠 실행은 history root 아래에 `production/production.sqlite`와 `history/history.sqlite`를 함께 쓴다. 하지만 이 production DB는 clean production benchmark DB가 아니라 history run에 딸린 current state output이다. Clean production benchmark DB가 필요하면 `--history-mode off`로 별도 production-only root에 한 번 더 실행한다.

`dashboard_compact/dashboard_compact.sqlite`는 replay 이후 생성되는 후처리 산출물이며, 입력은 `history/history.sqlite`다.

```text
outputs/post/<run_id>_history/history/history.sqlite
  -> compact_dashboard.py
  -> outputs/post/<run_id>_history/dashboard_compact/dashboard_compact.sqlite
```

정리:

| file | file-level independence | logical dependency |
|---|---|---|
| `<run_id>_production/production/production.sqlite` | 독립 파일 | production-only replay processing의 primary runtime/state output |
| `<run_id>_history/production/production.sqlite` | 독립 파일 | history run 내부의 primary runtime/state output |
| `<run_id>_history/history/history.sqlite` | 독립 파일 | history run processing을 관찰해 기록하는 optional side log |
| `<run_id>_history/dashboard_compact/dashboard_compact.sqlite` | 독립 파일 | `<run_id>_history/history/history.sqlite`를 입력으로 만든 derived output |

## Timeline Grain

POST replay flow의 시점 단위는 global clock snapshot이 아니라 user별 post-T movie/rating event arrival이다.

기준:

- 한 시점은 "특정 user에게 영화 rating event 하나가 들어온 순간"이다.
- timeline key는 `(user_id, event_id)`다.
- `event_id`는 replay input event id이며 dashboard slider/timeline의 시점 id다.
- `user_id`를 항상 같이 들고 다닌다. Compact dashboard는 전체 유저 global state를 한 row로 만들지 않는다.
- `visualization_states`, `cluster_snapshots`, `recommendations`는 selected user의 selected event 시점 상태를 보여준다.
- 다른 user의 상태 변화는 같은 run 안에 있어도 별도 `(user_id, event_id)` timeline으로 취급한다.
- run-level metric이나 throughput은 production/debug 용도이며 POST Replay Flow의 주 시각화 grain이 아니다.

## Production Store Flow

Production store는 현재 상태와 실행 상태를 빠르게 조회하기 위한 store다. 과거 상태를 완전 복원하는 목적이 아니며, dashboard는 history/compact가 없는 production DB에 대해서 기존 final-state view로 fallback해야 한다.

```text
post-T input event
  -> input_events
  -> event_progress
  -> stage_attempts
  -> runtime_metrics
  -> artifacts
  -> user_states
  -> user_raw_events
  -> user_positive_events
  -> embedding_snapshots
  -> embedding_rows
  -> active_embedding_cache
  -> embedding_cache_changes
  -> interest_states
  -> interest_vectors
  -> assignments
  -> refit_requests
  -> refit_attempts
  -> recommendation_runs
  -> recommendation_rows
```

각 table의 역할:

| table | role |
|---|---|
| `runs` | replay run의 시작/종료/status/root metadata |
| `input_events` | replay 입력 event identity와 원본 payload |
| `event_progress` | event schedule/emit/process timestamp와 lag |
| `stage_attempts` | event별 stage 실행 결과와 latency/error |
| `runtime_metrics` | queue/backlog/throughput snapshot |
| `artifacts` | file-backed artifact metadata |
| `user_states` | user별 최신 raw/positive state payload |
| `user_raw_events` | materialized raw rating event rows |
| `user_positive_events` | 현재 positive projection rows |
| `embedding_snapshots` | file-backed embedding snapshot metadata |
| `embedding_rows` | snapshot row index and event/user mapping |
| `active_embedding_cache` | user/raw event별 최신 active embedding cache |
| `embedding_cache_changes` | event별 active cache insert/update/inactivate signal |
| `interest_states` | user별 최신 interest state payload |
| `interest_vectors` | user별 최신 interest vector cache |
| `assignments` | assignment/pending/outlier/already-processed decision log |
| `refit_requests` | refit lifecycle current/control plane |
| `refit_attempts` | refit attempt result, backend, skip/error metadata |
| `recommendation_runs` | recommendation 실행 metadata |
| `recommendation_rows` | top-K recommendation rows |

Production store에서 즉시 재생 가능한 정보:

- event별 ingress/progress/lag
- stage별 latency와 failure
- assignment/refit/recommendation row의 발생 시점
- touched user의 최신 user/interest state
- 최신 active embedding cache

Production store만으로 복원하기 어려운 정보:

- 특정 replay order 당시의 전체 interest vector set
- refit 전후 membership 변화
- assignment가 반영된 직후의 state version
- recommendation이 어떤 interest state version에서 산출됐는지
- 과거 시점의 active embedding set 전체

## Production SQLite Schema

`production/production.sqlite`는 현재 streaming runtime이 실제로 운영할 current state/control-plane store다. 아래 schema는 현재 `runtime_store.py`의 replay DB 계약을 신규 production-only/history output-root 구조에 맞춰 정리한 필수 계약이다.

```sql
CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    speed REAL,
    output_root TEXT,
    summary_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE input_events (
    run_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    rating REAL NOT NULL,
    rated_at TEXT NOT NULL,
    rated_at_ts REAL NOT NULL,
    source TEXT,
    payload_json TEXT,
    PRIMARY KEY (run_id, event_id)
);

CREATE TABLE event_progress (
    run_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    scheduled_at TEXT,
    emitted_at TEXT,
    processing_started_at TEXT,
    processed_at TEXT,
    injector_lag_sec REAL,
    processing_lag_sec REAL,
    end_to_end_lag_sec REAL,
    behind_schedule INTEGER NOT NULL DEFAULT 0,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, event_id)
);

CREATE TABLE stage_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id INTEGER,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    latency_sec REAL,
    attempt_no INTEGER NOT NULL DEFAULT 1,
    command_json TEXT,
    error_type TEXT,
    error_message TEXT
);

CREATE TABLE runtime_metrics (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    event_id INTEGER,
    queue_depth INTEGER,
    refit_backlog INTEGER,
    open_refit_count INTEGER,
    running_refit_count INTEGER,
    throughput_events_per_sec REAL,
    notes_json TEXT
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    dtype TEXT,
    shape_json TEXT,
    row_count INTEGER,
    size_bytes INTEGER,
    sha256 TEXT,
    created_at TEXT NOT NULL
);
```

```sql
CREATE TABLE user_states (
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    version TEXT,
    status TEXT,
    raw_event_count INTEGER,
    positive_event_count INTEGER,
    active_event_count INTEGER,
    skipped_unknown_items INTEGER,
    last_raw_event_id INTEGER,
    payload_json TEXT NOT NULL,
    payload_encoding TEXT NOT NULL DEFAULT 'json',
    payload_blob BLOB,
    state_path TEXT,
    updated_at TEXT,
    PRIMARY KEY (run_id, user_id)
);

CREATE TABLE user_raw_events (
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    raw_event_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    rating REAL NOT NULL,
    rated_at TEXT NOT NULL,
    rated_at_ts REAL NOT NULL,
    status TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, user_id, raw_event_id)
);

CREATE TABLE user_positive_events (
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    raw_event_id INTEGER NOT NULL,
    event_idx INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    rating REAL NOT NULL,
    rated_at TEXT NOT NULL,
    rated_at_ts REAL NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    z_score REAL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, user_id, raw_event_id)
);
```

```sql
CREATE TABLE active_embedding_cache (
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    raw_event_id INTEGER NOT NULL,
    event_idx INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    rated_at TEXT NOT NULL,
    rated_at_ts REAL NOT NULL,
    history_len INTEGER NOT NULL,
    context_start_idx INTEGER NOT NULL,
    signature_hash TEXT NOT NULL,
    signature_json TEXT NOT NULL,
    dim INTEGER NOT NULL,
    dtype TEXT NOT NULL,
    embedding_blob BLOB NOT NULL,
    status TEXT NOT NULL,
    first_event_id INTEGER,
    last_event_id INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, user_id, raw_event_id)
);

CREATE TABLE embedding_cache_changes (
    run_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    raw_event_id INTEGER NOT NULL,
    change_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, event_id, user_id, raw_event_id)
);

CREATE TABLE embedding_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_attempt_id INTEGER,
    kind TEXT NOT NULL,
    scope TEXT,
    store_mode TEXT NOT NULL,
    artifact_id TEXT,
    path TEXT,
    row_count INTEGER NOT NULL,
    dim INTEGER NOT NULL,
    dtype TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE embedding_rows (
    snapshot_id TEXT NOT NULL,
    row_idx INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    raw_event_id INTEGER,
    event_idx INTEGER,
    movie_id INTEGER,
    status TEXT,
    history_len INTEGER,
    context_start_idx INTEGER,
    already_processed INTEGER NOT NULL DEFAULT 0,
    artifact_id TEXT,
    artifact_row_idx INTEGER,
    PRIMARY KEY (snapshot_id, row_idx)
);
```

`embedding_snapshots.store_mode`는 기존 runtime store의 snapshot storage column 이름이다. 신규 실행 옵션/용어는 `--history-mode off|history`로 통일하며, 이 컬럼을 run-level mode 의미로 사용하지 않는다.

```sql
CREATE TABLE interest_states (
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    version TEXT,
    interest_count INTEGER NOT NULL,
    pending_count INTEGER NOT NULL,
    processed_count INTEGER NOT NULL,
    assigned_since_last_refit INTEGER NOT NULL,
    outlier_since_last_refit INTEGER NOT NULL,
    refit_required INTEGER NOT NULL,
    refit_request_open INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    state_path TEXT,
    updated_at TEXT,
    PRIMARY KEY (run_id, user_id)
);

CREATE TABLE interest_vectors (
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    interest_id INTEGER NOT NULL,
    version TEXT,
    dim INTEGER NOT NULL,
    dtype TEXT NOT NULL,
    vector_blob BLOB NOT NULL,
    assigned_count INTEGER,
    source TEXT,
    top_genres_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (run_id, user_id, interest_id)
);

CREATE TABLE assignments (
    run_id TEXT NOT NULL,
    event_id INTEGER,
    user_id INTEGER NOT NULL,
    raw_event_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    interest_id INTEGER,
    similarity REAL,
    already_processed INTEGER NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, user_id, raw_event_id, status, created_at)
);
```

```sql
CREATE TABLE refit_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    running_at TEXT,
    closed_at TEXT,
    reasons_json TEXT,
    pending_count INTEGER,
    assigned_since_last_refit INTEGER,
    outlier_since_last_refit INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    superseded_by TEXT,
    error_type TEXT,
    error_message TEXT,
    payload_json TEXT
);

CREATE TABLE refit_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    latency_sec REAL,
    active_embedding_rows INTEGER,
    interest_count INTEGER,
    backend TEXT,
    skip_reason TEXT,
    error_type TEXT,
    error_message TEXT,
    payload_json TEXT
);
```

```sql
CREATE TABLE recommendation_runs (
    recommendation_run_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_id INTEGER,
    user_id INTEGER,
    top_k INTEGER NOT NULL,
    normalize INTEGER NOT NULL,
    include_seen INTEGER NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    latency_sec REAL,
    row_count INTEGER NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE recommendation_rows (
    recommendation_run_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    item_idx INTEGER,
    score REAL,
    best_interest_id INTEGER,
    metadata_json TEXT,
    PRIMARY KEY (recommendation_run_id, user_id, rank)
);
```

## History Store Flow

History mode는 replay 처리 결과를 나중에 다시 분석할 수 있게 남기는 append-only full-ish history다. Dashboard만을 위한 compact table이 아니라, compaction의 원천이 되는 기록이다.

다만 "full-ish"는 모든 대형 tensor/blob를 매 event마다 중복 저장한다는 뜻이 아니다. 원본 event, stage decision, state transition, refit 결과, recommendation 결과는 가능한 한 보존하고, 큰 matrix/vector는 의미 있는 변경 시점의 snapshot 또는 artifact reference로 남긴다.

```text
post-T input event
  -> production store flow
  -> history_run_events
  -> history_stage_events
  -> user_state_history
  -> embedding_change_history
  -> assignment_decision_history
  -> user_interest_timeline
  -> refit_lifecycle_history
  -> interest_vector_history
  -> interest_membership_history
  -> recommendation_history
  -> dashboard compact projection input
```

## History Stored Information

`history/history.sqlite`는 `dashboard_compact`의 원천이므로, compact step이 production DB를 다시 읽지 않아도 되도록 event/state-version 재구성에 필요한 정보를 자체적으로 가진다. 단, production DB의 모든 runtime metric을 복제하지는 않는다.

저장 범위:

| category | suggested table | stored information |
|---|---|---|
| run metadata | `history_runs` | `run_id`, same history run의 production DB path, replay root, history mode option, schema version, started/ended/status |
| input event context | `history_run_events` | `event_id`, `replay_order`, `user_id`, `movie_id`, `rating`, `rated_at`, `rated_at_ts`, source, compact input payload |
| stage execution | `history_stage_events` | event별 `extract_online`, `interest_assign`, `cluster_refit`, `recommend_online` status, latency, error summary |
| user state transition | `user_state_history` | raw/positive/active count 변화, last raw event, positive projection summary, state version |
| embedding changes | `embedding_change_history` | active embedding cache insert/update/inactivate/unchanged, signature, history length, active count before/after |
| assignment decisions | `assignment_decision_history` | assigned/pending/outlier/already-processed/skipped decision, interest id, similarity, reason |
| interest state timeline | `user_interest_timeline` | pending/processed/interest count, refit flags, assigned/outlier counters, state version |
| refit lifecycle | `refit_lifecycle_history` | request open/running/closed/skipped/failed, reasons, backend, active rows, elapsed, skip/error summary |
| interest vector snapshot | `interest_vector_history` | refit close/seed 시점의 interest vectors, top genres, assigned count, vector version |
| membership snapshot | `interest_membership_history` | refit close 시점의 raw event -> interest membership, cluster label, optional UMAP coordinate |
| recommendation snapshot | `recommendation_history`, `recommendation_row_history` | trigger event/state version, top-K rows, score, best interest, include-seen/normalize config |

## History SQLite Schema

`history/history.sqlite`는 append-only를 기본으로 한다. 같은 event를 재처리해야 하는 경우에는 기존 row를 overwrite하지 않고 새 `history_id`와 `attempt_no` 또는 더 최신 `recorded_at`을 가진 row를 추가한다.

```sql
CREATE TABLE history_runs (
    run_id TEXT PRIMARY KEY,
    production_db_path TEXT,
    replay_root TEXT,
    history_mode TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE history_run_events (
    run_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    replay_order INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    rating REAL NOT NULL,
    rated_at TEXT NOT NULL,
    rated_at_ts REAL NOT NULL,
    source TEXT,
    payload_json TEXT,
    PRIMARY KEY (run_id, event_id)
);

CREATE TABLE history_stage_events (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    replay_order INTEGER,
    user_id INTEGER,
    source_stage TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 1,
    started_at TEXT,
    ended_at TEXT,
    latency_sec REAL,
    error_type TEXT,
    error_message TEXT,
    payload_json TEXT,
    recorded_at TEXT NOT NULL
);
```

```sql
CREATE TABLE user_state_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    state_version TEXT NOT NULL,
    raw_event_count_before INTEGER,
    raw_event_count_after INTEGER,
    positive_event_count_before INTEGER,
    positive_event_count_after INTEGER,
    active_event_count_before INTEGER,
    active_event_count_after INTEGER,
    last_raw_event_id INTEGER,
    positive_projection_json TEXT,
    changed_raw_event_ids_json TEXT,
    recorded_at TEXT NOT NULL,
    payload_json TEXT
);

CREATE TABLE embedding_change_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    raw_event_id INTEGER NOT NULL,
    event_idx INTEGER,
    movie_id INTEGER,
    change_type TEXT NOT NULL,
    signature_hash TEXT,
    history_len INTEGER,
    context_start_idx INTEGER,
    active_before_count INTEGER,
    active_after_count INTEGER,
    embedding_ref TEXT,
    recorded_at TEXT NOT NULL,
    payload_json TEXT
);
```

```sql
CREATE TABLE assignment_decision_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    raw_event_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    assignment_status TEXT NOT NULL,
    interest_id INTEGER,
    similarity REAL,
    already_processed INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    state_version TEXT,
    recorded_at TEXT NOT NULL,
    payload_json TEXT
);

CREATE TABLE user_interest_timeline (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    state_version TEXT NOT NULL,
    interest_count INTEGER NOT NULL,
    pending_count INTEGER NOT NULL,
    processed_count INTEGER NOT NULL,
    assigned_since_last_refit INTEGER NOT NULL,
    outlier_since_last_refit INTEGER NOT NULL,
    refit_required INTEGER NOT NULL,
    refit_request_open INTEGER NOT NULL,
    refit_request_id TEXT,
    refit_reasons_json TEXT,
    recorded_at TEXT NOT NULL,
    payload_json TEXT
);
```

```sql
CREATE TABLE refit_lifecycle_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id INTEGER,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reasons_json TEXT,
    backend TEXT,
    active_embedding_rows INTEGER,
    interest_count INTEGER,
    noise_count INTEGER,
    latency_sec REAL,
    skip_reason TEXT,
    error_type TEXT,
    error_message TEXT,
    state_version TEXT,
    recorded_at TEXT NOT NULL,
    payload_json TEXT
);

CREATE TABLE interest_vector_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id INTEGER,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    request_id TEXT,
    state_version TEXT NOT NULL,
    interest_id INTEGER NOT NULL,
    dim INTEGER NOT NULL,
    dtype TEXT NOT NULL,
    vector_blob BLOB NOT NULL,
    assigned_count INTEGER,
    source TEXT,
    top_genres_json TEXT,
    recorded_at TEXT NOT NULL,
    payload_json TEXT
);

CREATE TABLE interest_membership_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id INTEGER,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    request_id TEXT,
    state_version TEXT NOT NULL,
    raw_event_id INTEGER NOT NULL,
    event_idx INTEGER,
    movie_id INTEGER,
    interest_id INTEGER,
    cluster_label INTEGER NOT NULL,
    umap_x REAL,
    umap_y REAL,
    membership_source TEXT,
    recorded_at TEXT NOT NULL,
    payload_json TEXT
);
```

```sql
CREATE TABLE recommendation_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_run_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    event_id INTEGER,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    trigger_event_id INTEGER,
    trigger_replay_order INTEGER,
    trigger_state_version TEXT,
    trigger_source_stage TEXT,
    top_k INTEGER NOT NULL,
    normalize INTEGER NOT NULL,
    include_seen INTEGER NOT NULL,
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    latency_sec REAL,
    recorded_at TEXT NOT NULL,
    payload_json TEXT
);

CREATE TABLE recommendation_row_history (
    recommendation_run_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    event_id INTEGER,
    replay_order INTEGER,
    user_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    score REAL,
    src_cluster INTEGER,
    item_idx INTEGER,
    metadata_json TEXT,
    PRIMARY KEY (recommendation_run_id, user_id, rank)
);
```

저장하지 않거나 reference로만 둘 정보:

- 매 event마다 전체 embedding matrix 복사
- 매 event마다 전체 interest vector set 복사
- production-only throughput/backlog metric 전체
- stage command 전문과 긴 traceback 전문
- dashboard 첫 화면에 필요 없는 raw payload 전문

대형 데이터 처리 원칙:

- embedding vector는 active cache나 snapshot artifact를 참조하고, history에는 change signal과 signature를 우선 저장한다.
- interest vector는 refit close/seed import처럼 vector set이 바뀌는 시점에만 snapshot으로 저장한다.
- membership은 refit close 시점 snapshot으로 저장하되, dashboard compact에서는 선택된 user/event 중심으로 줄인다.
- error/debug payload는 summary field와 optional payload reference를 둔다.

History 기록 설계 결정:

- 문제의 원인은 in-process 실행 자체가 아니라 production store가 current-state upsert 중심이라는 점이다. `user_states`, `interest_states`, `interest_vectors`, `active_embedding_cache`는 최신 상태 조회에는 좋지만 과거 event 시점의 상태를 완전히 복원하기 어렵다.
- Production store의 current-state/control-plane 방식은 유지한다. 운영 경로를 history 재생 요구에 맞춰 갈아엎지 않는다.
- History mode에서만 append-only side log를 추가한다. 즉 production state update와 같은 replay 처리 흐름을 관찰해 `history/history.sqlite`에 event/state transition을 남긴다.
- 모든 event마다 full user/interest state 전체를 저장하지 않는다. Event마다 남기는 것은 입력 event, stage 결과, embedding change, assignment decision, user interest timeline counter 중심이다.
- Interest vector와 membership은 매 event마다 저장하지 않고, cluster refit이 닫히는 시점에 snapshot으로 저장한다.
- Recommendation은 recommendation 실행 시점에 run/row snapshot으로 저장한다.
- Compact projection은 event마다 clustering을 새로 수행하지 않는다. Selected `(user_id, event_id)`에 대해 가장 최근 refit close membership snapshot을 carry-forward해 visualization/cluster state를 만든다.

State version rule:

- `state_version`은 user 단위 monotonic integer를 문자열로 저장한다. 예: `"1"`, `"2"`, `"3"`.
- version은 user state 또는 interest state가 dashboard/history 관점에서 의미 있게 바뀔 때 증가한다.
- 같은 replay event 안에서 여러 stage가 상태를 바꾸면 stage 순서대로 증가한다.
- `event_id`, `replay_order`, `source_stage`, `recorded_at`은 version의 원인과 정렬 보조 정보로 따로 저장한다.
- compact 생성 시 selected `event_id`에서 `replay_order <= selected`인 row 중 가장 큰 `state_version`을 최신 상태로 본다.
- 현재 `OnlineUserState.version`과 `InterestState.version`은 schema/version string 성격이므로 history `state_version`으로 그대로 쓰지 않는다. History writer가 user별 monotonic `state_version`을 별도로 만든다.

권장 공통 키:

| column | role |
|---|---|
| `history_id` | append-only row id |
| `run_id` | replay run id |
| `event_id` | trigger input event id |
| `replay_order` | input stream order |
| `user_id` | affected user |
| `raw_event_id` | affected raw event when applicable |
| `state_version` | user/interest state version after this change |
| `source_stage` | `extract_online`, `interest_assign`, `cluster_refit`, `recommend_online` |
| `change_type` | inserted/updated/inactivated/assigned/pending/outlier/refit_closed/etc. |
| `recorded_at` | wall-clock write time |
| `payload_json` | compact dashboard/debug summary |

### `embedding_change_history`

Purpose: event가 active embedding cache를 어떻게 바꿨는지 추적한다.

저장 후보:

- `change_type`: `inserted`, `updated`, `inactivated`, `unchanged`
- `raw_event_id`, `event_idx`, `movie_id`
- `history_len`, `context_start_idx`
- `signature_hash`
- `active_before_count`, `active_after_count`
- full embedding vector는 매 event마다 중복 저장하지 않는다. 필요하면 `active_embedding_cache`, `embedding_snapshots`, 또는 별도 snapshot artifact를 참조한다.

### `user_interest_timeline`

Purpose: event 처리 후 user interest state가 dashboard 관점에서 어떤 상태가 됐는지 기록한다.

저장 후보:

- `assignment_status`: `assigned`, `pending_no_interest`, `pending_outlier`, `already_processed`, `skipped`
- `interest_id`
- `similarity`
- `pending_count`
- `processed_count`
- `interest_count`
- `assigned_since_last_refit`
- `outlier_since_last_refit`
- `refit_required`
- `refit_request_id`
- `refit_reasons_json`

### `interest_vector_history`

Purpose: refit이 닫히는 시점의 interest vector snapshot을 저장한다.

저장 후보:

- `request_id`
- `interest_id`
- `vector_version`
- `dim`, `dtype`, `vector_blob`
- `assigned_count`
- `top_genres_json`
- `source`: `refit`, `mean_fallback`, `seed`

이 table은 매 event마다 쓰지 않고, refit close/seed import처럼 vector set이 실제로 바뀌는 시점에만 쓴다. History mode에서는 이 snapshot을 비교적 풍부하게 보존하고, dashboard compact projection에서 필요한 컬럼만 줄인다.

### `interest_membership_history`

Purpose: refit 후 raw event가 어떤 interest에 속하게 됐는지 dashboard가 재구성할 수 있게 한다.

저장 후보:

- `request_id`
- `raw_event_id`
- `event_idx`
- `movie_id`
- `interest_id`
- `cluster_label`
- `umap_x`, `umap_y`
- `membership_source`: `hdbscan`, `noise`, `mean_fallback`

이 table도 refit close 시점에 snapshot으로 쓴다. 전체 membership은 history store에 남기고, dashboard compact projection은 selected user/event 재생에 필요한 요약 또는 lazy-load 가능한 subset으로 줄인다.

Refit membership snapshot 구현 결정:

- 현재 clustering 함수는 이미 `ClusterOutput.labels`와 `ClusterOutput.z_cluster`를 계산한다. 하지만 현재 `cluster_refit.py`의 `run_refit()` 경로는 이를 interest vector와 `clusterSummary`로 줄인 뒤 per-point detail을 버린다.
- History mode에서는 refit close 시점에 이 per-point detail을 버리지 않는다. `run_refit()` 또는 호출부가 membership writer에 필요한 refit detail을 반환해야 한다.
- 필요한 refit detail은 refit 입력 row 순서를 기준으로 `rawEventId`, `eventIdx`, `movieId`, `cluster_label`, `umap_x`, `umap_y`, `interest_id`를 연결할 수 있어야 한다.
- `cluster_label -> interest_id` mapping은 interest vector 생성 규칙과 동일하게 둔다. 유효 cluster label은 정렬 후 `interest_id=0..K-1`로 매핑한다.
- noise label `-1`은 `interest_id=NULL`, `membership_source='noise'`로 기록한다.
- all-noise mean fallback에서는 fallback interest vector를 `interest_vector_history`에 남기되, membership row는 여전히 `cluster_label=-1`, `interest_id=NULL`, `membership_source='noise'`로 둔다.
- `umap_x`는 `z_cluster[:, 0]`, `umap_y`는 두 번째 축이 있을 때만 `z_cluster[:, 1]`을 쓴다. reduced dimension이 1이면 `umap_y=NULL`이다.
- skipped/failed refit은 `refit_lifecycle_history`만 기록하고 새 membership snapshot을 만들지 않는다.

### `recommendation_history`

Purpose: recommendation run이 어떤 replay event와 state version에서 나왔는지 기록한다.

History store는 production `recommendation_runs`를 수정하지 않고, 별도 `recommendation_history`에 다음 linkage를 남긴다.

- `trigger_event_id`
- `trigger_replay_order`
- `trigger_state_version`
- `trigger_source_stage`

`recommendation_row_history`는 top-K row를 저장한다. Dashboard compact는 특정 event/state version에서 산출된 recommendation run을 찾아 `recommendations` table로 줄인다.

## Dashboard Compact Projection

Dashboard compact projection은 history store를 읽어서 dashboard가 즉시 표시할 수 있는 작은 산출물을 만드는 후처리 단계다. 권장 entrypoint 이름은 다음 중 하나로 둔다.

```text
python3 -m model.stream.compact_dashboard
python3 -m model.stream.compact_replay_dashboard
```

입력:

```text
outputs/post/<run_id>_history/history/history.sqlite
```

출력:

```text
outputs/post/<run_id>_history/dashboard_compact/dashboard_compact.sqlite
```

Compact projection은 `dashboard_compact/dashboard_compact.sqlite` 하나로 고정한다. Dashboard가 여러 table을 한 번에 조회하고, legacy/history fallback과 같은 SQLite read path를 공유할 수 있게 하기 위해 별도 Parquet export는 만들지 않는다.

Compact projection에 넣을 정보는 dashboard가 직접 읽는 최소 schema로 제한한다. Compact schema에서는 `event_idx` 대신 `event_id`로 용어를 통일한다. 기존 구현이나 UI 코드에서 `event_idx`가 필요하면 reader layer에서 alias로 처리한다.

Compact schema 용어 결정:

- `event_id`: replay/post input event의 식별자이며 dashboard slider/timeline 기준이다.
- `event_idx`: user positive sequence 내부 index이며 embedding/history 내부 필드로만 사용한다.
- `dashboard_compact` schema에는 `event_idx`를 노출하지 않는다.
- compact 생성 중 history의 `event_idx`가 필요하면 `event_id`에 매핑된 selected state를 계산하는 내부 필드로만 사용한다.

```sql
CREATE TABLE event_timeline (
    user_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    movie_id INTEGER NOT NULL,
    is_refit_triggered INTEGER NOT NULL DEFAULT 0,
    refit_reason TEXT,
    k_count INTEGER NOT NULL DEFAULT 0,
    noise_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, event_id)
);

CREATE TABLE visualization_states (
    user_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    points_data TEXT NOT NULL,
    PRIMARY KEY (user_id, event_id)
);

CREATE TABLE cluster_snapshots (
    user_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    cluster_id INTEGER NOT NULL,
    size INTEGER NOT NULL,
    top_genres TEXT,
    PRIMARY KEY (user_id, event_id, cluster_id)
);

CREATE TABLE recommendations (
    user_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    score REAL,
    src_cluster INTEGER,
    PRIMARY KEY (user_id, event_id, rank)
);
```

Compact table source mapping:

| compact table | primary history source |
|---|---|
| `event_timeline` | `history_run_events`, `user_interest_timeline`, `refit_lifecycle_history`, `interest_membership_history` |
| `visualization_states` | `interest_membership_history` |
| `cluster_snapshots` | `interest_membership_history`, `interest_vector_history` |
| `recommendations` | `recommendation_history`, `recommendation_row_history` |

`visualization_states.points_data` JSON shape:

```json
[
  {"raw_event_id": 1, "x": 0.5, "y": 0.2, "c": 2},
  {"raw_event_id": 2, "x": 0.1, "y": 0.8, "c": -1}
]
```

`visualization_states.event_id`는 dashboard slider의 selected replay event다. `points_data` 안의 각 point는 user-local `raw_event_id`로 식별한다. `points_data.c`는 cluster label이며 noise는 `-1`로 둔다. `cluster_snapshots.cluster_id`도 noise를 표시해야 하면 `-1` row를 허용하되, `k_count`는 유효 cluster만 세고 noise는 `noise_count`에 반영한다.

Compact 생성 규칙:

- `event_timeline`은 dashboard slider 기준이므로 대상 user의 replay event마다 1 row를 만든다.
- compact 생성은 user별로 수행한다. 전체 run의 모든 user를 하나의 global visualization state로 합치지 않는다.
- `visualization_states`와 `cluster_snapshots`는 selected `event_id` 시점에서 가장 최근의 `state_version` 또는 refit snapshot을 carry-forward해서 만든다.
- 해당 시점 이전에 refit snapshot이 없으면 `points_data`는 빈 배열 `[]`, `k_count=0`, `noise_count=0`으로 기록한다.
- `recommendations`는 같은 `(user_id, event_id)`에 여러 recommendation run이 있으면 dashboard 기본 config의 최신 successful run 하나만 compact에 남긴다. 모든 run 비교가 필요하면 history의 `recommendation_history`를 직접 본다.

Compact projection에서 줄이는 것:

- full payload JSON 중 dashboard에 직접 안 쓰는 필드
- 큰 vector blob
- event slider 첫 로딩에 필요 없는 membership row
- repeated unchanged state
- raw/debug error payload 전문

후속 dashboard 작업에서는 compact projection을 우선 읽고, 없으면 history store를 직접 읽고, history도 없으면 production store final-state view로 fallback하는 reader 정책을 둘 수 있다.

## Runtime Option

권장 CLI/API 옵션:

```text
--history-mode off|history
```

의미:

| mode | behavior |
|---|---|
| `off` | production-only root에 production store만 기록한다. 기본값이다. |
| `history` | history root에 production store와 `history/history.sqlite` append-only history를 함께 기록한다. |

Boolean flag 대신 `--history-mode`로 통일한다. 향후 `debug_full` 같은 모드가 필요하면 같은 옵션의 값을 확장한다.

## Implementation Plan

1. `runtime_store.py`는 production/current-state schema만 소유하고, `history_store.py`에 history schema를 추가한다.
   - 기존 current/cache table은 변경하지 않는다.
   - production schema와 history schema init 경로를 파일 단위로 분리한다.
   - 기존 `replay.sqlite`는 legacy production DB로 계속 읽을 수 있어야 한다.
   - history table이 없어도 기존 production-only 산출물은 계속 유효해야 한다.

2. `history_store.py`에 writer helper를 추가한다.
   - `record_history_run`
   - `record_history_run_event`
   - `record_history_stage_event`
   - `record_user_state_history`
   - `record_embedding_change_history`
   - `record_assignment_decision_history`
   - `record_user_interest_timeline`
   - `record_refit_lifecycle_history`
   - `record_interest_vector_history`
   - `record_interest_membership_history`
   - `record_recommendation_history`
   - `record_recommendation_row_history`

3. replay/stream stage에 `history_mode`를 전달한다.
   - `trace_replay.py` CLI에서 `--history-mode`를 받는다.
   - `--output-root`는 production-only run에서는 `outputs/post/<run_id>_production/`, history run에서는 `outputs/post/<run_id>_history/`를 가리킨다.
   - 필요하면 `--production-db`, `--history-db`, `--dashboard-compact-db` 경로를 명시적으로 받을 수 있게 한다.
   - `inprocess_worker.py`가 stage 호출 context에 history DB path와 replay order를 넘긴다.
   - 개별 stage 단독 실행의 history 옵션은 후속 필요 시 추가한다.

4. stage별 full-ish history 기록 지점을 연결한다.
   - 모든 history writer는 `--history-mode history`일 때만 `history/history.sqlite`에 쓴다.
   - `inprocess_worker.py` extract stage: `embedding_cache_changes` 기록 직후 `embedding_change_history`
   - `inprocess_worker.py` assign stage: assignment/refit request 기록 직후 `assignment_decision_history`, `user_interest_timeline`
   - `inprocess_worker.py` refit stage: refit lifecycle update와 refit close 후 `refit_lifecycle_history`, `interest_vector_history`, `interest_membership_history`
   - `inprocess_worker.py` recommend stage: recommendation run 생성 시 `recommendation_history`, `recommendation_row_history`

5. `compact_dashboard.py` 계열 entrypoint를 추가한다.
   - history store를 읽어 dashboard compact projection을 생성한다.
   - `--history-db`, `--output-root` 또는 `--output-db`를 받는다.
   - projection은 재생 UI가 필요한 최소 컬럼과 요약만 담는다.
   - 대형 detail은 원본 history DB를 lazy-load할 수 있게 reference를 남긴다.

6. 문서와 todo에 신규 history/compact 계약과 검증 결과를 반영한다.
   - dashboard UI/reader 구현은 별도 후속 plan에서 다룬다.

7. 검증한다.
   - schema smoke: 신규 `/tmp` DB에 history table 생성 확인
   - writer smoke: synthetic row append 확인
   - replay smoke: 5-event replay에서 event별 history row 확인
   - compaction smoke: history DB에서 compact projection 생성 확인

## Dashboard Projection Rule

후속 dashboard 작업에서는 compact projection이 있으면 아래 순서로 화면을 만들 수 있다. Compact projection이 없을 때만 `history/history.sqlite`를 직접 읽는다.

```text
event slider
  -> input_events + event_progress
  -> embedding_change_history for selected event/user
  -> user_interest_timeline latest <= selected replay_order
  -> interest_vector_history latest state_version <= selected replay_order
  -> interest_membership_history for selected state_version
  -> recommendation_runs linked to selected event/state_version
```

대형 vector/membership은 화면에서 선택된 user/event 범위로 lazy load한다. 전체 replay의 모든 vector와 membership을 첫 로딩에 올리지 않는다.

## Compatibility

- 기존 `replay.sqlite`는 production store DB로 계속 유효하다.
- 신규 production-only run은 `outputs/post/<run_id>_production/production/production.sqlite`를 생성한다.
- 신규 history run은 `outputs/post/<run_id>_history/production/production.sqlite`, `outputs/post/<run_id>_history/history/history.sqlite`, `outputs/post/<run_id>_history/dashboard_compact/dashboard_compact.sqlite`를 생성한다.
- 기존 DB를 대상으로 할 때 신규 history table은 additive schema로 유지한다.
- 기존 JSONL artifact는 fallback/debug로 유지한다.
- 기본 production 산출물의 성능과 저장량을 history 산출물 때문에 악화시키지 않는다.
- 현재 구현 문서에는 아직 root-level `replay.sqlite` 계약이 남아 있을 수 있다. 이 문서는 신규 계약의 기준 문서이며, 구현 단계에서는 `PROJECT_GUIDE.md`, `docs/artifacts.md`, `docs/data-flow.md`, `docs/streaming-replay-dashboard-contract.md`, `outputs/readme.md`, `model/README.md`를 함께 갱신한다.
