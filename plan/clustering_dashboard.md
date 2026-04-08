# 클러스터링 대시보드 계획

## 목표

사용자 상태 임베딩을 차원 축소한 뒤 밀도 기반 클러스터링한 결과를 시각적으로 탐색할 수 있는 대시보드를 추가한다. 아직 실제 산출물이 없으므로, 결과 파일이 있다고 가정한 입력 계약과 렌더링 코드를 먼저 준비한다.

## 범위

- `dashboard/cluster_dashboard.py` 추가
- 대시보드 입력 포맷 문서화
- 실행 의존성(`requirements.txt`) 반영
- 프로젝트 문서(`README.md`, `PROJECT_GUIDE.md`) 갱신

## 입력 가정

최소 컬럼:

- `userId`
- `clusterLabel`
- `x`
- `y`

선택 컬럼:

- `z`
- `clusterProbability`
- `outlierScore`
- `sequenceLength`
- `embeddingNorm`
- 기타 사용자 메타데이터 컬럼

## 대시보드 구성

- 전체 사용자 수, 클러스터 수, 노이즈 비율 등 핵심 지표 카드
- 2D / 3D 임베딩 산점도
- 클러스터별 사용자 수 막대 차트
- 선택 가능한 수치형 메타데이터 분포 차트
- 클러스터 요약 테이블
- 실제 산출물이 없을 때를 위한 synthetic demo 데이터 모드

## 검증

- `python3 -m py_compile dashboard/cluster_dashboard.py`
- 문서와 실행 경로가 일치하는지 확인
