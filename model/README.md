# model/

SASRec + Contrastive Loss 기반 적응형 다중 관심사 추천 시스템 구현체

## 전체 흐름

```
train.py 실행 → 모델 학습 → sasrec_cl.pt 저장
     ↓
extract.py 실행 → 50개 간격 히든스테이트 추출 → embeddings.npy 저장
     ↓
cluster.py 실행 → UMAP 차원 축소 → HDBSCAN 클러스터링 → cluster_labels.npy 저장
```

---

## 파일 구성

| 파일 | 역할 |
|---|---|
| `dataset.py` | 데이터 로드, 전처리, Dataset |
| `model.py` | SASRecCL 모델, Contrastive Loss |
| `train.py` | 학습 실행 |
| `extract.py` | 히든스테이트 추출 |
| `cluster.py` | UMAP + HDBSCAN 클러스터링 |

---

## 실행 순서

```bash
# 1. 학습
python model/train.py

# 2. 임베딩 추출
python model/extract.py

# 3. 클러스터링
python model/cluster.py
```

---

## 실행 환경과 로그

- `train.py`, `extract.py`는 실행 시 `cuda` → `mps` → `cpu` 순으로 자동 선택한다.
- 선택된 device는 콘솔과 실행 로그 파일에 함께 기록된다.
- `cluster.py`는 현재 NumPy/UMAP/HDBSCAN 기반으로 CPU 실행 로그를 남긴다.
- 실행 로그는 `outputs/logs/<script>_YYYYmmdd_HHMMSS.log`에 저장된다.

---

## 주요 하이퍼파라미터

| 파라미터 | 값 | 설명 |
|---|---|---|
| seq_len | 100 | 시퀀스 길이 |
| stride | 50 | 슬라이딩 윈도우 간격 |
| d_model | 128 | 임베딩 차원 |
| cl_lambda | 0.1 | Contrastive Loss 가중치 (0.05/0.1/0.2 튜닝 필요) |
| min_interactions | 200 | 유저 필터링 기준 (긍정 상호작용 수) |
| min_activity_days | 30 | 유저 필터링 기준 (활동 기간) |
| interval | 50 | 히든스테이트 추출 간격 |
| n_components | 실험으로 결정 | UMAP 축소 차원 (10~20 범위) |

---

## 산출물

모든 산출물은 프로젝트 루트의 `outputs/`에 저장된다 (git 추적 제외).

| 파일 | 설명 |
|---|---|
| `outputs/sasrec_cl.pt` | 학습된 모델 가중치 |
| `outputs/embeddings.npy` | 시점별 히든스테이트 `(전체 시점 수, 128)` |
| `outputs/cluster_labels.npy` | HDBSCAN 클러스터 레이블 `(전체 시점 수,)` |
| `outputs/clusters_3d.png` | 3D 시각화 (데모용) |
| `outputs/logs/*.log` | 스크립트별 실행 로그 |

---

## 보완할 점

**cl_lambda 튜닝**
현재 0.1 고정. 0.05 / 0.1 / 0.2 범위에서 실험 필요

**UMAP n_components 결정**
`cluster.py` 실행 시 자동으로 5/10/15/20 후보 실험 후 노이즈 비율 기준으로 선택. 실험 결과 보고 수동으로 조정 가능

**히든스테이트 추출 방식**
현재 50개 간격 고정. 비율 기반(25%/50%/75%/100%) 방식과 비교 실험 고려 가능

**데이터 포맷 검증**
`movies_processed_drop.csv`의 genres 컬럼 형태, `ratings_drop_processed.jsonl` 스키마가 실제 파일과 일치하는지 확인 필요
