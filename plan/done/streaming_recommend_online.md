# Streaming Online Recommend

## 목적

`batch/recommend.py`의 `u_k^T v_i` 스코어링+추천 로직을 streaming 파이프라인에 적용한다.
`model/stream/recommend_online.py`를 신규 작성하고, `replay_pipeline.py` micro-batch loop에 추천 단계를 추가한다.

## 배경

`IMPLEMENTATION_STATUS.md`에 `u_k 기반 추천 스코어링 모듈: [ ] 없음`으로 명시된 보류 후보.
- `batch/recommend.py`는 `user_interests.npz`(NPZ)에서 interest vector를 읽는다.
- streaming 파이프라인은 `interest_states/{user_id}.json`(JSON)에 같은 역할의 벡터를 저장한다.
- 스코어링 공식(`max_k(u_k^T v_i)`)과 item embedding 로드는 동일하게 재사용 가능하다.

## 읽어야 할 파일

- `PROJECT_GUIDE.md`
- `model/IMPLEMENTATION_STATUS.md`
- `model/batch/recommend.py`
- `model/stream/interest_assign.py` (InterestState, Interest 정의)
- `model/stream/state.py` (OnlineUserState, positive_events)
- `model/stream/replay_pipeline.py` (통합 지점)

## 수정 범위

- 신규: `model/stream/recommend_online.py`
- 수정: `model/stream/replay_pipeline.py` (micro-batch loop에 recommend 단계 추가)
- 수정: `model/IMPLEMENTATION_STATUS.md` (상태 업데이트)
- 수정: `todo.md` (Active 등록 → Done 이동)
- 수정 금지: `model/batch/recommend.py`, `model/stream/state.py`, `model/stream/interest_assign.py`

## 데이터/스키마 영향

- 컬럼 변경: 없음
- 생성물 추가:
  - `outputs/stream/stream_recommendations.jsonl` — 유저별 top-K 추천 결과 JSONL (append)
  - `outputs/stream/replay_demo/stream_recommendations.jsonl` — replay demo 격리 경로
- 호환성 영향: 없음 (기존 artifact 덮어쓰지 않음)

## 실행 계획

1. `model/stream/recommend_online.py` 작성
   - `batch/recommend.py`에서 `load_item2idx`, `load_item_embeddings`, `build_candidate_index`, `top_recommendations_for_user`, `load_movie_metadata`, `l2_normalize` import
   - streaming 전용 함수 구현
     - `load_interest_vectors_from_stream()`: `interest_states/{user_id}.json` → `dict[int, list[tuple[int, np.ndarray]]]`
     - `load_seen_from_user_states()`: `user_states/{user_id}.json`의 `positiveEvents` → `dict[int, set[int]]`
   - `main()` 구현: CLI entrypoint (`--interest-state-dir`, `--user-state-dir`, `--checkpoint`, `--item2idx`, `--movies`, `--output-jsonl`, `--top-k`, `--normalize`, `--include-seen`, `--user-id`)
   - interest가 없는 유저(refit 전)는 skip 처리

2. `replay_pipeline.py` 수정
   - `--recommend` flag 추가 (기본 off — 기존 smoke 호환 유지)
   - `--recommend-top-k` 추가 (기본 20)
   - micro-batch loop에서 `cluster_refit` 이후 recommend 단계 호출
   - recommend 산출물도 replay demo 격리 경로(`output_root`)에 저장

3. `model/IMPLEMENTATION_STATUS.md` 업데이트
   - `u_k 기반 추천 스코어링` 항목을 `[x]` 로 변경
   - 산출물 목록에 `stream_recommendations.jsonl` 추가

4. `todo.md` 업데이트

## 검증

- [ ] `python3 -m model.stream.recommend_online --user-id <id>` 단독 실행 → `stream_recommendations.jsonl` 생성 확인
- [ ] interest 없는 유저는 skip되고 로그에 기록됨을 확인
- [ ] `replay_pipeline.py --recommend` 포함 재실행 → recommend 산출물이 `replay_demo/` 아래에 생성됨을 확인
- [ ] top-K row 수, score 범위(finite), bestClusterId 유효성 확인

## 완료 조건

- `model/stream/recommend_online.py`가 단독으로 실행 가능하다
- `replay_pipeline.py --recommend` 옵션이 정상 동작한다
- `IMPLEMENTATION_STATUS.md`의 해당 항목이 `[x]`로 갱신된다
