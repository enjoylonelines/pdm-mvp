# Dataset Application — v3 연결 현황

작성일: 2026-08-04

---

## 연결된 데이터셋

| 항목 | 값 |
|------|---|
| 데이터셋 이름 | `canonical-ai4i-physics-v3.0` |
| 경로 | `/Users/hb/Downloads/predictive_maintenance_canonical_v3` |
| 핵심 입력 파일 | `canonical/model_outputs/result_artifact.jsonl` |
| 레코드 수 | 100건 (compressor 20 / cnc 80) |
| 모델 버전 | `independent-logreg-v3.0` |
| 스키마 버전 | `result-artifact-v1.0` |
| 검증 상태 | 전체 PASS (`scripts/validate_v3_dataset.py`) |

---

## v3 데이터 연결 현황

### 연결된 파분 (구현 완료)

| 구성 요소 | 연결 방법 | 파일 |
|----------|----------|------|
| **Result Artifact 로드** | `result_artifact.jsonl` 전체 파싱 | `scripts/load_v3_result_artifacts.py` |
| **Evidence Package 변환** | `result_artifact_to_evidence_package()` | `scripts/load_v3_result_artifacts.py` |
| **센서 창 조인** | `compressor_sensor_observation.csv` 24h 필터 | `scripts/load_v3_result_artifacts.py` |
| **정비 이력 조인** | `maintenance_event.csv` 최근 이벤트 조회 | `scripts/load_v3_result_artifacts.py` |
| **status_grade 매핑** | critical/warning→alarm, attention→watch, normal→normal | `scripts/load_v3_result_artifacts.py` |
| **샘플 JSON (critical)** | CMP-S03-L03-01, probability=0.825, 센서 144행 | `samples/evidence_package_v3_critical.json` |
| **샘플 JSON (Result Artifact critical)** | 동일 케이스, Result Artifact 스키마 형태 | `samples/result_artifact_v3_critical.json` |
| **샘플 JSON (normal)** | CMP-S01-L04-01, probability=0.101 | `samples/result_artifact_v3_normal.json` |
| **데이터셋 검증** | 경로/버전/파일/파싱/미사용 원칙 확인 | `scripts/validate_v3_dataset.py` |

### 연결되지 않은 부분

| 구성 요소 | 현황 | 다음 작업 |
|----------|------|----------|
| **CNC 센서 조인** | cnc_sensor_observation.csv 미연결 (어댑터가 compressor만 처리) | CNC 장비 대상 sensor_evidence 확장 |
| **prediction_snapshot.jsonl** | 로드 미연결 | 장비별 스냅샷 타임라인 시각화 연결 |
| **prediction_factor.jsonl** | 로드 미연결 | top_factors와 feature-level 기여도 통합 |
| **asset_relation.csv** | 장비 간 관계 미연결 | peer_comparison 구조 확장 시 필요 |
| **대시보드 (manager_app.py)** | 아직 v3 데이터 미연결 | Result Artifact → Streamlit 화면 연결 |
| **report_generator.py** | azure-pdm 구조 기반, v3 필드 미지원 | v3 Evidence Package 구조 기반으로 리포트 블록 수정 |
| **LLM 리포트 레이어** | 미구현 | Evidence Package → 역할별 문장 변환 프롬프트 설계 |
| **API 엔드포인트** | 미구현 | Result Artifact를 JSON으로 제공하는 API 초안 |
| **peer_comparison** | v3에는 peer 비교 구조 없음 | asset_master.csv 기반 동급 장비 비교 설계 필요 |
| **error_context** | azure-pdm 전용 구조 (errorID 기반), v3에 없음 | v3 이벤트 로그 연결 방안 검토 |
| `recommended_decision` | 아직 미정 | 팀 합의 필요 |
| `lineage.evidence_id` 고유성 | artifact_id 재사용 중 | uuid4 또는 hash 생성 정책 결정 |

---

## v3 데이터 구조 요약

### result_artifact.jsonl (100건) 필드

```
artifact_id              str   "RESULT#CMP-S03-L03-01#2026-08-29T23:00:00+09:00"
artifact_type            str   "predictive_maintenance_result"
schema_version           str   "result-artifact-v1.0"
asset_id                 str   "CMP-S03-L03-01"
asset_type               str   "compressor" | "cnc"
observed_at              str   ISO datetime
prediction_horizon_hours int   24
prediction_task          str   "binary_failure_within_horizon"
failure_probability      float 0.0~1.0
predicted_failure_type   str   "failure_risk" | "no_significant_risk"
status_grade             str   "critical" | "warning" | "attention" | "normal"
confidence               float 0.0~1.0
top_factors[]
  rank                   int
  feature                str   "rotation_raw_6h_mean" 등
  feature_value          float
  signed_contribution    float
  direction              str   "risk_up" | "risk_down"
  explanation_method     str   "linear_logit_contribution"
recommended_action
  action                 str   "immediate_inspection_and_stop_review" 등
  priority               str   "urgent" | "medium" | "routine"
provenance
  dataset_version        str   "canonical-ai4i-physics-v3.0"
  model_version          str   "independent-logreg-v3.0"
  prediction_id          str
  source_type            str
  canonical_source_mutated bool
```

### status_grade 분포

| status_grade | 건수 | 매핑 (azure-pdm prediction.status) |
|-------------|------|--------------------------------------|
| critical | 3 | alarm |
| warning | 12 | alarm |
| attention | 53 | watch |
| normal | 32 | normal |

---

## evaluation_truth 미사용 원칙

`canonical/evaluation_truth/` 디렉토리는 **평가 전용**이다.

- 대시보드 입력으로 사용하지 않는다
- LLM 프롬프트에 전달하지 않는다
- Evidence Package 생성 입력으로 사용하지 않는다
- `scripts/validate_v3_dataset.py`가 프로젝트 소스의 `evaluation_truth` 참조 여부를 검사한다

---

## 검증 실행 결과 (2026-08-04)

```
=== v3 데이터셋 검증 ===
경로: /Users/hb/Downloads/predictive_maintenance_canonical_v3
기준 버전: canonical-ai4i-physics-v3.0

[PASS] v3 경로 존재
[PASS] dataset_version == canonical-ai4i-physics-v3.0
[PASS] 파일 존재: canonical/dataset/asset_master.csv
[PASS] 파일 존재: canonical/dataset/compressor_sensor_observation.csv
[PASS] 파일 존재: canonical/dataset/cnc_sensor_observation.csv
[PASS] 파일 존재: canonical/dataset/maintenance_event.csv
[PASS] 파일 존재: canonical/model_outputs/model_contract.json
[PASS] 파일 존재: canonical/model_outputs/result_artifact.jsonl
[PASS] 파일 존재: canonical/model_outputs/prediction_snapshot.jsonl
[PASS] 파일 존재: canonical/model_outputs/prediction_factor.jsonl
[PASS] result_artifact.jsonl 파싱 가능 (100건)
[PASS] result_artifact 필수 필드 존재
[PASS] evaluation_truth 미사용 (azure-pdm 소스 참조 없음)
[PASS] 어댑터 변환 (critical 케이스 3건)

=== 전체 PASS ===
```

---

## 다음 팀원이 이어서 해야 할 작업

### 팀원1 (데이터/모델)
- CNC 장비(`asset_type=cnc`) 대상 sensor_evidence 조인 구현
- `prediction_snapshot.jsonl` + `prediction_factor.jsonl` 어댑터 확장
- `asset_relation.csv` 기반 peer_comparison 구조 설계

### 팀원2 (대시보드/API)
- `build_evidence_packages(V3)` 결과를 `manager_app.py` Streamlit 화면에 연결
- Result Artifact list/detail API 엔드포인트 초안 작성
- `status_grade` 기준 필터/정렬 UI 구현

### 팀원3 (LLM 리포트/Evidence Package)
- `report_generator.py`를 v3 Evidence Package 필드 기준으로 수정
- LLM 프롬프트 설계 (Evidence Package → 역할별 문장)
- `recommended_decision` 필드 생성 규칙 정의

### 팀장
- `recommended_decision`, `confidence`, `evidence_id` 고유성 정책 팀 합의
- v3 Result Artifact → 역할별 표시 계약 확정
- evaluation_truth 미사용 원칙을 팀원에게 명시적으로 공유
