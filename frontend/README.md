# React Replay Dashboard

React + Vite 기반 POST replay dashboard다. FastAPI backend(`api/`)를 통해 compact dashboard DB를 읽고, user별 event timeline, K 변화, cluster scatter, recommendation panel을 표시한다.

## 실행

API를 먼저 띄운다.

```bash
.venv/bin/pip install -r api/requirements.txt
DASHBOARD_DB_PATH=outputs/post/<run_id>_history/dashboard_compact/dashboard_compact.sqlite \
MOVIES_DB_PATH=data/movies.db \
POSTER_DIR=data/MLP-20M \
.venv/bin/uvicorn api.main:app --reload
```

frontend를 실행한다.

```bash
cd frontend
npm install
VITE_API_BASE=http://localhost:8000 npm run dev
```

## 구조

- `src/App.jsx`: user/timeline selection, playback, frame prefetch/cache
- `src/api.js`: FastAPI client. 기본 base는 `http://localhost:8000`, `VITE_API_BASE`로 변경 가능
- `src/components/KTimeline.jsx`: event-local index 기준 K/noise timeline
- `src/components/ClusterView.jsx`: selected event의 compact cluster scatter
- `src/components/Recommendations.jsx`: selected event recommendation list
- `src/components/PerfOverlay.jsx`, `src/hooks/usePerf.js`: fetch/render timing overlay

## 입력 계약

Frontend는 API만 호출한다. Replay/runtime/history DB나 JSONL artifact를 직접 읽지 않는다. API와 dashboard 입력 계약은 `api/README.md`, `docs/streaming-replay-dashboard-contract.md`, `docs/post-replay-output-areas.md`를 따른다.
