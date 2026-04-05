# EDA 계획

## 목표
전처리 산출물을 영화 추천 시스템 연구 관점에서 탐색·분석한다.

## 입력
- `data/movies_processed.csv`
- `data/movies_processed_drop.csv`
- `data/ratings_drop.csv`
- `data/ratings_drop_processed.jsonl`
- `preprocess/drop_movie/validation_report.json`
- `preprocess/process_rating/validation_report.json`

## 출력 위치
- `eda/processed/outputs/eda_report.md` + PNG 차트

## 분석 항목

### 1. Movie Drop 전후 비교
- 전체 row 수 변화, drop ratio
- drop 사유별 count + issue combination (겹침 분석)
- 전후 주요 지표 비교 (mean ratingAvg, median ratingCount, median tagCount)
- 장르 분포 비교 (before vs after)

### 2. Dropped Movie 심층 분석
- releaseYear 분포: dropped vs kept 비교
- ratingCount 분포: dropped movie의 interaction 수준
- ratingAvg 분포: dropped vs kept 비교
- tagCount 분포: dropped movie의 tag 보유율
- 장르별 drop rate: 특정 장르에 편향되었는지
- issue 단일/복합 사유 비율

### 3. Rating Drop 분석 → 스킵
- movie drop에 따른 rating 전파는 단순 파생이므로 별도 분석 불필요

### 4. 최종 Rating 데이터 종합 분석
- **기본 프로파일**: row 수, unique user/movie, 시간 범위, rating 값 분포
- **Sparsity**: user-item matrix density
- **연도별 트렌드**: 연도별 rating 수 + cumulative %
- **Interaction Density**: user당/movie당 rating 수 분포
- **Long-tail & Popularity Bias**: threshold coverage curve, concentration (top 1/5/10%), Gini coefficient
- **Cold-start**: 저interaction movie/user 비율
- **User Sequence**: sequence 길이 분포, activity span 분포, span bucket, notable users

## 구현 방식
- 단일 스크립트: `eda/processed/eda_processed.py`
- `python -m eda.processed.eda_processed` 으로 실행
- 기존 raw EDA(`eda/raw/`)는 보존

## 기존 EDA 처리
- `eda/raw/`는 raw 데이터 EDA 보존용으로 유지
