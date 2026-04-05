# Schema Conventions

`schemas/`는 데이터 계약의 정본이다. 전처리 코드 연결은 나중에 하더라도, 컬럼 추가/이름 변경/null 허용 여부 변경은 먼저 여기서 정의한다.

## Directory Rules

- `dataset/stage/file.schema.yaml` 구조를 사용한다.
- `stage`는 `raw`, `intermediate`, `processed` 중 하나를 쓴다.
- 호환되지 않는 출력 변경은 새 버전 파일로 올린다.
  - 예: `movies_processed.v2.schema.yaml`

## Required Top-Level Fields

- `name`: 데이터셋 이름
- `version`: 계약 버전
- `stage`: 데이터 단계
- `primary_key`: 주 키 컬럼 목록
- `columns`: 컬럼 정의 목록
- `constraints`: 검증 규칙 목록

## Column Fields

- `name`: 컬럼명
- `logical_type`: 의미 기준 타입
- `physical_type`: 실제 저장 타입
- `nullable`: null 허용 여부
- `description`: 컬럼 설명
- `source_columns`: 파생 출처 컬럼 목록
- `ingest`: raw 입력을 읽을 때 필요한 컬럼인지 여부
- `default`: 기본값이 있으면 명시

## Type Rules

- 배열이나 구조화 필드가 CSV에 저장되면 `logical_type`과 `physical_type`을 분리한다.
- 예:
  - `logical_type: list[string]`
  - `physical_type: json_string`

## Current Scope

- `ml32m/raw`: MovieLens 32M 원본 CSV 계약
- `ml32m/processed`: 전처리 산출물 계약
