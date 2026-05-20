# Replay Dashboard API

React replay dashboard가 사용하는 read-only FastAPI backend다. 입력은 POST history run에서 생성한 compact dashboard DB와 선택적인 movie metadata DB다.

## 입력

- `DASHBOARD_DB_PATH`: `outputs/post/<run_id>_history/dashboard_compact/dashboard_compact.sqlite`
- `MOVIES_DB_PATH`: `data/movies.db`
- `POSTER_DIR`: `data/MLP-20M/`

`DASHBOARD_DB_PATH`는 compact DB의 `event_timeline`, `visualization_states`, `cluster_snapshots`, `recommendations` table을 읽는다. `MOVIES_DB_PATH`는 `movies(movie_id, title, poster_url, genres, release_year)` table을 기대하며, recommendation 응답에 제목/포스터/장르를 붙이는 보조 입력이다. `POSTER_DIR`가 있으면 `/posters` static route로 mount한다.

API는 `production/production.sqlite`, `history/history.sqlite`, legacy `replay.sqlite`, JSONL debug artifact를 display fallback으로 읽지 않는다.

## 실행

```bash
.venv/bin/pip install -r api/requirements.txt
DASHBOARD_DB_PATH=outputs/post/<run_id>_history/dashboard_compact/dashboard_compact.sqlite \
MOVIES_DB_PATH=data/movies.db \
POSTER_DIR=data/MLP-20M \
.venv/bin/uvicorn api.main:app --reload
```

## Endpoints

- `GET /api/users`
- `GET /api/users/{user_id}/timeline`
- `GET /api/users/{user_id}/events/{event_id}`
- `GET /api/users/{user_id}/events/{event_id}/visualization`
- `GET /api/users/{user_id}/events/{event_id}/clusters`
- `GET /api/users/{user_id}/events/{event_id}/recommendations`

계약의 정본은 `docs/streaming-replay-dashboard-contract.md`와 `docs/post-replay-output-areas.md`다.
