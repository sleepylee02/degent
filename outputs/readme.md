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
