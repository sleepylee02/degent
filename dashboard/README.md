# Clustering Dashboard

사용자 상태 임베딩을 차원 축소하고 밀도 기반 클러스터링한 결과를 탐색하기 위한 Streamlit 대시보드.

## 실행

```bash
streamlit run dashboard/cluster_dashboard.py
```

실제 결과 파일이 아직 없으면 앱에서 `Use demo data`를 켜서 synthetic 예시 데이터로 UI를 먼저 확인할 수 있다.

## 지원 포맷

- `.csv`
- `.parquet`
- `.jsonl`
- `.ndjson`

## 기대 입력 스키마

필수 컬럼:

- `userId`: 사용자 식별자
- `clusterLabel`: 클러스터 ID. `-1`은 noise로 간주
- `x`: 차원 축소 결과 1축
- `y`: 차원 축소 결과 2축

선택 컬럼:

- `z`: 3차원 시각화용 3축
- `clusterProbability`: HDBSCAN soft membership 등 신뢰도
- `outlierScore`: 이상치 점수
- `sequenceLength`: 사용자 시퀀스 길이
- `embeddingNorm`: 임베딩 norm
- 그 외 추가 메타데이터 컬럼

예시 CSV:

```csv
userId,clusterLabel,x,y,z,clusterProbability,outlierScore,sequenceLength,embeddingNorm
10,3,-4.12,2.07,0.53,0.94,0.03,87,11.8
11,-1,8.51,-6.24,-1.22,0.18,0.91,6,9.4
12,1,-1.92,4.65,1.03,0.88,0.10,42,10.7
```

## 기본 경로

앱 기본값은 `data/clustering/user_clusters.parquet`를 먼저 찾는다. 파일이 없으면 demo 데이터를 사용할 수 있다.
