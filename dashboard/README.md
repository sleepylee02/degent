# POST Replay Compact Dashboard

POST history run에서 생성한 compact SQLite만 읽는 Streamlit dashboard.

## 실행

```bash
streamlit run dashboard/cluster_dashboard.py
```

입력은 하나만 사용한다.

```text
outputs/post/<run_id>_history/dashboard_compact/dashboard_compact.sqlite
```

앱은 `outputs/post/**/dashboard_compact/dashboard_compact.sqlite`를 자동 탐색하고, 필요하면 sidebar에서 직접 경로를 입력할 수 있다.

## 표시 기능

- user 선택: `event_timeline` 기준 user별 event 수와 기간 확인
- 시점별 클러스터 시각화: `visualization_states.points_data`를 UMAP scatter로 표시
- 전체 기간 K 변화: `event_timeline.k_count`와 `noise_count`를 event timeline으로 표시
- 시점별 추천 결과: `recommendations`를 rank, movie_id, score, source cluster로 표시
- cluster detail: `cluster_snapshots`의 size와 top genres 표시

## Reader 계약

Dashboard는 아래 compact tables만 읽는다.

- `event_timeline`
- `visualization_states`
- `cluster_snapshots`
- `recommendations`

Dashboard는 `production/production.sqlite` 또는 `history/history.sqlite`를 직접 읽지 않는다. Replay/runtime/history store를 생성하거나 수정하지도 않는다.

파일 계약의 정본은 `docs/post-replay-output-areas.md`와 `docs/streaming-replay-dashboard-contract.md`다.
