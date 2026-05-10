# Schema YAML 검증 계획

## 목표
`preprocess/preprocess_movie/preprocess_movies.py`가 생성한 `movies_processed.csv` 구조가 processed schema YAML과 맞는지 검증할 수 있게 만든다.

## 범위
- processed schema YAML 로드
- 전처리 결과 DataFrame과 schema의 컬럼/타입/null 허용 여부 비교
- processed schema의 `constraints`를 validation artifact에 반영
- 기존 전처리 로직과 출력 포맷은 유지
- 검증 결과를 `validation_report.json`, `bad_rows.csv`에서 볼 수 있게 정리

## 구현 단계
1. processed schema YAML 로더 추가
2. 전처리 후 출력 DataFrame을 schema 기준으로 검증하는 함수 추가
3. 검증 항목 구현
4. 검증 결과를 `validation_report.json`, `bad_rows.csv`에 반영
5. 스크립트 실행 또는 정적 검증으로 결과 확인

## 제외 범위
- raw schema 기반 입력 컬럼/타입 자동 연동
- processed schema 기반 출력 컬럼 자동 생성
- schema 값으로 전처리 로직 자체를 바꾸는 기능
- 여러 데이터셋용 공통 프레임워크 추상화

## 완료 기준
- 스크립트가 processed schema YAML을 직접 읽는다.
- 컬럼명/컬럼 순서/타입/null 허용 여부/constraint 위반을 검출할 수 있다.
- schema 위반이 validation artifact에 기록된다.
- schema 위반이 있으면 스크립트가 실패한다.
- 기존 전처리 로직과 출력 생성 흐름은 유지된다.
