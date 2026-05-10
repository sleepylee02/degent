# Model Runtime Logging 계획

## 목표
- `model/` 스크립트가 실행 시 사용 가능한 연산 장치를 자동 선택한다.
- 선택된 장치와 주요 실행 상태를 콘솔과 파일 로그에 함께 남긴다.
- 실행 로그는 git 추적 제외된 `outputs/logs/` 아래에 저장한다.

## 대상 파일
- `model/train.py`
- `model/extract.py`
- `model/cluster.py`
- `model/runtime.py` (신규)
- `model/README.md`

## 변경 항목

### 1. 공통 runtime 유틸 추가
- `outputs/logs/` 자동 생성
- 스크립트별 파일 로그 생성
- `cuda` → `mps` → `cpu` 순 장치 자동 선택
- 선택 결과와 PyTorch 런타임 정보 출력

### 2. 학습 스크립트 로깅
- 입력 경로, 데이터셋 크기, item/genre/user 수 로그
- epoch별 loss 및 validation metric 로그
- 모델 저장 위치 로그

### 3. 임베딩 추출 스크립트 로깅
- checkpoint 경로, 선택 device, 데이터셋 크기 로그
- 추출된 embedding block 수와 최종 shape 로그
- 저장 위치 로그

### 4. 클러스터링 스크립트 로깅
- embeddings 로드 shape 로그
- `n_components` 후보별 결과 로그
- 최종 cluster 수와 noise ratio 로그
- 산출물 저장 위치 로그

## 출력 위치
- `outputs/sasrec_cl.pt`
- `outputs/embeddings.npy`
- `outputs/cluster_labels.npy`
- `outputs/clusters_3d.png`
- `outputs/logs/*.log`
