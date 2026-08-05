# W1 팀 공유 — Azure PdM 예지보전 프로젝트

작성일: 2026-08-04 (v3 데이터셋 반영: 2026-08-04)

> **기준 데이터셋**: `canonical-ai4i-physics-v3.0`  
> **경로**: `/Users/hb/Downloads/predictive_maintenance_canonical_v3`  
> **핵심 입력**: `canonical/model_outputs/result_artifact.jsonl` (100건)

---

## 프로젝트 목표

모델 성능 수치 개선이 아닌, **예지보전 결과를 역할별로 표현하는 서비스 기반 구축**이 목표다.

"이 장비는 X% 확률로 고장난다"는 숫자 한 줄이 아니라,
매니저와 엔지니어가 각자의 언어로 이해할 수 있는 **근거 패키지 + 역할별 리포트**를 만든다.

인과를 확정적으로 표현하지 않는다. 모든 판단은 "연관", "후보", "가설", "근거" 중심으로 표현한다.
LLM은 판단 주체가 아니라, Evidence Package를 역할별 문장으로 변환하는 보조 계층이다.

---

## 현재 구현 상태

### 기준 데이터셋 — v3 (`canonical-ai4i-physics-v3.0`)

> W1 공식 데이터셋. Result Artifact 100건이 확정 출력.

| 파일 | 행 수 | 비고 |
|------|-------|------|
| `canonical/model_outputs/result_artifact.jsonl` | 100건 | **W1 핵심 입력** — normal 32 / attention 53 / warning 12 / critical 3 |
| `canonical/model_outputs/prediction_snapshot.jsonl` | — | 장비별 예측 스냅샷 |
| `canonical/model_outputs/prediction_factor.jsonl` | — | 피처별 기여도 |
| `canonical/dataset/compressor_sensor_observation.csv` | 86,400행 | 20대 압축기 센서 |
| `canonical/dataset/cnc_sensor_observation.csv` | 345,600행 | 80대 CNC 센서 |
| `canonical/dataset/maintenance_event.csv` | 790건 | 정비 이벤트 |
| `canonical/dataset/asset_master.csv` | 100건 | 장비 마스터 |

**사용 금지**: `canonical/evaluation_truth/*` — 평가 전용, 대시보드/API/LLM/Evidence Package 입력 불가

### 기존 azure-pdm 데이터 (구조 참고용)

| 파일 | 행 수 | 기간 | 비고 |
|------|-------|------|------|
| PdM_telemetry.csv | 876,100 | 2015-01 ~ 2016-01 | 100대 × 시간당 1행 |
| PdM_errors.csv | 3,919 | 2015 | errorID: error1~5 |
| PdM_failures.csv | 761 | 2015 | comp1~4 |
| PdM_maint.csv | 3,286 | 2014-06 ~ 2016-01 | 2014년 400건 포함 |
| PdM_machines.csv | 100 | — | model1~4, age |

학습/검증 분할: `2015-10-01` 기준 (학습 이전 / 검증 이후)

### v3 어댑터 (`scripts/load_v3_result_artifacts.py`)

`result_artifact.jsonl` → azure-pdm Evidence Package 형태 변환 스크립트.
센서 창(compressor_sensor_observation.csv)과 정비 이력(maintenance_event.csv)을 선택적으로 조인한다.

```bash
# critical 케이스 변환 → samples/ 저장
python3 scripts/load_v3_result_artifacts.py \
  --v3-path /Users/hb/Downloads/predictive_maintenance_canonical_v3 \
  --status critical warning --out samples/

# 데이터셋 연결 검증
python3 scripts/validate_v3_dataset.py
```

### Evidence Package (`evidence_package.py`)

입력: `machine_id`, `timestamp`  
출력: 근거 항목을 담은 `dict` — LLM 없음, DB 없음, pandas only

| 영역 | 내용 |
|------|------|
| sensor_evidence | 센서 4종의 24h 평균 + 기준선 대비 z-score |
| peer_comparison | 동급 장비(같은 model, 연식 ±3년) 백분위 비교 |
| error_context | 직전 24h 에러 목록 + errorID별 24h 고장 전환율 |
| maintenance_context | 부품별 마지막 교체 이력 + 경과일 + 유형(예방/사후) |
| component_hypotheses | 임계 초과 센서-부품 후보 목록 (단일 확정 금지) |
| status_flags | no_prior_error / multiple_candidates / insufficient_data |

pytest: **30/30 통과** (2026-08-04)

### Baseline Model (`baseline_model.py`)

| 항목 | 내용 |
|------|------|
| 알고리즘 | HistGradientBoostingClassifier (default 파라미터, 튜닝 없음) |
| 피처 | 센서 4종의 24h 롤링 mean/std (8개) |
| 라벨 | 이후 24h 내 고장 발생 여부 |
| 출력 | Evidence Package에 `model_prediction` 필드 추가 |

`add_model_prediction(pkg, model, threshold)` — 기존 Evidence Package에 모델 판정을 덧붙인다.

### Manager/Engineer Report (`report_generator.py`)

| 역할 | 리포트 블록 |
|------|------------|
| 매니저 | 상황 요약, 에러 전환 이력, 부품 이상 후보, 교체 현황, 데이터 품질 경고 |
| 엔지니어 | 모델 판정 상세, 센서 편차, 동급 비교, 부품 후보, 에러 상세, 교체 이력, 불충분 경고 |

### Ontology (`ontology/`)

- `link_types.json` — 15개 링크 타입 정의
- `instances/` — machine, component 등 인스턴스
- `links/` — 16개 jsonl 관계 파일

### Streamlit Manager App (`manager_app.py`)

장비별 Evidence Package와 리포트를 웹 UI로 확인하는 프로토타입.

---

## W1 핵심 산출물

### 1. Result Artifact

모델·규칙·근거를 합쳐 대시보드, API, 리포트가 **공통으로 소비하는 결과 객체**.
구조 정의: `RESULT_ARTIFACT_SCHEMA.md`

### 2. Evidence Package

`generate_evidence_package()` + `add_model_prediction()` 출력.
필드 매핑: `EVIDENCE_PACKAGE_MAPPING.md`

### 3. Sample Fixture

`samples/` 디렉토리의 JSON 파일 — **v3 기반 파일을 우선 참조**.

| 파일 | 데이터 출처 | 상태 |
|------|------------|------|
| `evidence_package_v3_critical.json` | v3 실제 데이터 | critical (CMP-S03-L03-01, 센서 144행) |
| `result_artifact_v3_critical.json` | v3 실제 데이터 | critical (Result Artifact 스키마) |
| `result_artifact_v3_normal.json` | v3 실제 데이터 | normal (CMP-S01-L04-01) |
| `evidence_package_warning.json` | azure-pdm 수동 근사값 | [구조 참고용] |
| `result_artifact_warning.json` | azure-pdm 수동 근사값 | [구조 참고용] |
| `result_artifact_normal.json` | azure-pdm 수동 근사값 | [구조 참고용] |

---

## 역할 분배 (팀장 포함 4명)

| 역할 | 담당 | W1 주요 작업 |
|------|------|-------------|
| **팀장** | 산출물 계약 / 통합 / 일정 | Result Artifact 스키마 확정, 역할 간 인터페이스 정의, 일정 조율 |
| **팀원1** | 데이터 / 모델 파이프라인 | evidence_package.py 유지보수, baseline_model.py 출력 안정화, 데이터 검증 |
| **팀원2** | 대시보드 / API | Streamlit manager_app, Result Artifact 소비 API 초안 |
| **팀원3** | LLM 리포트 / Evidence Package | report_generator.py, LLM 프롬프트 설계, Evidence Package → 역할별 문장 변환 |

---

## 이번 주 완료 기준 (W1 Definition of Done)

- [x] `pytest test_evidence_package.py` 30/30 통과
- [x] `RESULT_ARTIFACT_SCHEMA.md` 필드 정의 완료
- [x] `EVIDENCE_PACKAGE_MAPPING.md` 필드 매핑표 작성
- [x] `samples/` v3 기반 JSON 3개 생성 (파싱 가능)
- [x] `scripts/validate_v3_dataset.py` 전체 PASS (14항목)
- [x] v3 어댑터 `scripts/load_v3_result_artifacts.py` 작동 확인
- [ ] 팀원 4명이 같은 Result Artifact를 기준으로 독립 개발 가능한 상태 (팀 공유 후 확인)

---

## 팀원 읽는 순서

1. **이 문서** (TEAM_SHARE_W1.md) — 전체 맥락
2. `RESULT_ARTIFACT_SCHEMA.md` — 산출물 계약
3. `EVIDENCE_PACKAGE_MAPPING.md` — 필드 매핑
4. `samples/` — 실제 JSON 예시
5. `EVIDENCE_PACKAGE.md` — Evidence Package 구현 상세
6. `baseline_model.py`, `report_generator.py` — 구현 코드

---

## 주의사항

- "확정 원인", "root cause 확정" 표현 금지. 항상 "후보", "가설", "연관"으로 표현.
- LLM은 Evidence Package를 문장으로 변환하는 보조 계층. 판단 주체가 아님.
- 모델 성능 개선(정밀도/재현율 튜닝)은 W1 범위 밖.
- `cache/` 디렉토리의 pkl 파일(~93MB)은 git에 포함하지 않는다.
