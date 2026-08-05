# Evidence Package 필드 매핑

작성일: 2026-08-04 (v3 반영: 2026-08-04)

> **기준**: `canonical-ai4i-physics-v3.1` result_artifact.jsonl → Evidence Package  
> 어댑터 구현: `scripts/load_v3_result_artifacts.py`

---

## v3 Result Artifact → Evidence Package 필드 매핑 (구현 완료)

| v3 result_artifact 필드 | Evidence Package 필드 | 변환 |
|------------------------|--------------------------------|------|
| `artifact_id` | `lineage.evidence_id` | 직접 복사 |
| `asset_id` | `asset_id` | 직접 복사 |
| `asset_type` | `asset_type` | 직접 복사 |
| `observed_at` | `observed_at` | 직접 복사 |
| `prediction_horizon_hours` | `prediction_horizon_hours` | 직접 복사 |
| `failure_probability` | `model_prediction.probability` | 직접 복사 |
| `confidence` | `model_prediction.confidence` | 직접 복사 |
| `status_grade` | `model_prediction.status_grade` | 직접 복사 |
| `status_grade` | `model_prediction.status` | critical/warning→alarm, attention→watch, normal→normal |
| `predicted_failure_type` | `model_prediction.predicted_failure_type` | 직접 복사 |
| `prediction_task` | `model_prediction.prediction_task` | 직접 복사 |
| `top_factors[]` | `top_factors[]` | 직접 복사 (전체) |
| `top_factors[direction=risk_up]` | `component_hypotheses[]` | risk_up 방향만 필터 |
| `recommended_action` | `recommended_actions[]` | 배열로 감쌈 |
| `provenance.dataset_version` | `lineage.dataset_version` | 직접 복사 |
| `provenance.model_version` | `lineage.model_version` | 직접 복사 |
| `provenance.prediction_id` | `lineage.prediction_id` | 직접 복사 |
| — | `sensor_evidence` | compressor_sensor_observation.csv 조인 (24h 창) |
| — | `maintenance_context` | maintenance_event.csv 조인 |
| — | `status_flags.insufficient_data` | sensor_evidence.window_rows == 0 |
| — | `lineage.generated_at` | datetime.now(utc) |

---

## Evidence Package 출력 구조

`generate_evidence_package()` + `add_model_prediction()` 합산 출력:

```
machine_id                    int
timestamp                     str (ISO)
window_start                  str (ISO)
sensor_evidence
  sensors
    volt / rotate / pressure / vibration
      mean_24h                float
      z_score                 float | null
      basis
        global_mean           float
        global_std            float
        global_n              int
  window_rows                 int
  reference_frame             str
  window
    start / end               str
peer_comparison
  percentile_by_sensor
    {sensor}
      percentile              float (0~100)
      target_z                float
      peers_with_data         int
  peer_count                  int
  basis
    model / age_range / reference_frame
error_context
  count                       int
  errors[]
    errorID                   str
    datetime                  str
    failure_conversion_rate_24h  float (0~1) | null
    basis
      total_occurrences_all_time  int
      converted_to_failure_24h    int
maintenance_context
  comp1 / comp2 / comp3 / comp4
    last_replacement          str | null
    days_elapsed              float | null
    type                      "preventive" | "reactive" | null
    basis
      maint_datetime          str
      failure_within_24h_before_maint  bool
component_hypotheses[]
  component                   str
  associated_sensor           str
  sensor_z_score              float
  z_threshold                 float | str
  direction                   "both" | "negative"
  association                 str
status_flags
  no_prior_error              bool
  multiple_candidates         bool
  insufficient_data           bool
model_prediction              (add_model_prediction 호출 시 추가)
  probability                 float
  threshold                   float
  alarm_triggered             bool
  model_name                  str
```

---

## 프로토타입 evidence-package.schema.json 구조

agentic-ontology-dashboard prototype 기준:

```
evidence_id                   str (uuid)
event_id                      str
scenario_id                   str
equipment
  equipment_id                str | int
  model_code                  str
  age_years                   int
observation
  timestamp                   str (ISO)
  window_hours                int
  sensors[]
    sensor_id                 str
    mean_value                float
    z_score                   float
    threshold                 float
    status                    "alarm" | "watch" | "normal"
top_factors[]
  component                   str
  sensor_id                   str
  z_score                     float
  threshold                   float
  direction                   str
  confidence                  float
history[]
  event_type                  "error" | "failure" | "maintenance"
  datetime                    str
  detail                      str
maintenance_context
  comp1~4
    last_replaced             str | null
    days_since_replaced       float | null
    type                      str | null
predicted_failure_type        str (가설 후보, 미확정)
failure_probability           float
threshold                     float
status                        "alarm" | "watch" | "normal"
recommended_decision          str
confidence                    float
data_quality_warnings[]       str
lineage
  generated_at                str
  model_version               str
  data_sources[]              str
```

---

## 필드 매핑표

### 공통 식별자

| Evidence Package 필드 | 프로토타입 필드 | 비고 |
|----------------|----------------|------|
| `machine_id` | `equipment.equipment_id` | 타입 int → str 변환 필요 |
| `timestamp` | `observation.timestamp` | |
| `window_start` | 계산값 | `timestamp - window_hours` |
| — | `evidence_id` | 생성 필요 (uuid4 등) |
| — | `event_id` | 생성 필요 |
| — | `scenario_id` | 생성 필요 |

### 장비 정보

| Evidence Package 필드 | 프로토타입 필드 | 비고 |
|----------------|----------------|------|
| machines.csv lookup | `equipment.model_code` | Evidence Package에 미포함 → 별도 lookup |
| machines.csv lookup | `equipment.age_years` | 동일 |

### 센서 관측

| Evidence Package 필드 | 프로토타입 필드 | 비고 |
|----------------|----------------|------|
| `sensor_evidence.sensors.{s}.mean_24h` | `observation.sensors[].mean_value` | |
| `sensor_evidence.sensors.{s}.z_score` | `observation.sensors[].z_score` | |
| 부품별 임계 (thresholds.json) | `observation.sensors[].threshold` | |
| 임계 초과 여부 | `observation.sensors[].status` | alarm/watch/normal 변환 필요 |
| `sensor_evidence.window_rows` | `observation.window_hours` (간접) | |
| `sensor_evidence.reference_frame` | `lineage` 내 기재 | |

### 이상 후보 (component_hypotheses → top_factors)

| Evidence Package 필드 | 프로토타입 필드 | 비고 |
|----------------|----------------|------|
| `component_hypotheses[].component` | `top_factors[].component` | |
| `component_hypotheses[].associated_sensor` | `top_factors[].sensor_id` | |
| `component_hypotheses[].sensor_z_score` | `top_factors[].z_score` | |
| `component_hypotheses[].z_threshold` | `top_factors[].threshold` | float 변환 필요 |
| `component_hypotheses[].direction` | `top_factors[].direction` | |
| — | `top_factors[].confidence` | 생성 필요 |

### 에러/이력

| Evidence Package 필드 | 프로토타입 필드 | 비고 |
|----------------|----------------|------|
| `error_context.errors[].errorID` | `history[].detail` | type="error" |
| `error_context.errors[].datetime` | `history[].datetime` | |
| `error_context.errors[].failure_conversion_rate_24h` | `history[].detail`에 포함 | |

### 정비 이력

| Evidence Package 필드 | 프로토타입 필드 | 비고 |
|----------------|----------------|------|
| `maintenance_context.compX.last_replacement` | `maintenance_context.compX.last_replaced` | 필드명 차이 |
| `maintenance_context.compX.days_elapsed` | `maintenance_context.compX.days_since_replaced` | 필드명 차이 |
| `maintenance_context.compX.type` | `maintenance_context.compX.type` | 동일 |

### 모델 판정

| Evidence Package 필드 | 프로토타입 필드 | 비고 |
|----------------|----------------|------|
| `model_prediction.probability` | `failure_probability` | |
| `model_prediction.threshold` | `threshold` | |
| `model_prediction.alarm_triggered` | `status` | bool → "alarm"/"normal" 변환 필요 |
| — | `predicted_failure_type` | top_factors[0].component (가설, 미확정) |
| — | `recommended_decision` | 생성 필요 |
| — | `confidence` | 생성 필요 |

### 데이터 품질

| Evidence Package 필드 | 프로토타입 필드 | 비고 |
|----------------|----------------|------|
| `status_flags.insufficient_data` | `data_quality_warnings[]` | bool → 경고 문자열 변환 |

---

## 아직 없는 필드 및 생성 방법

| 필드 | 생성 방법 | 우선순위 |
|------|----------|---------|
| `evidence_id` | `uuid.uuid4()` 또는 `sha256(machine_id + timestamp)[:16]` | W1 |
| `event_id` | Evidence Package 생성 이벤트마다 순번 또는 uuid | W1 |
| `scenario_id` | 케이스 분류(TP/FP/FN/TN) 기반 또는 uuid | W2 |
| `equipment.model_code` | `machines_df[machines_df.machineID==id].model` | W1 |
| `equipment.age_years` | `machines_df[machines_df.machineID==id].age` | W1 |
| `recommended_decision` | maintenance_context + hypotheses 기반 규칙 문자열 | W2 |
| `confidence` | model_prediction.probability 재활용 또는 별도 산출 | W2 |
| `lineage.generated_at` | `datetime.utcnow().isoformat()` | W1 |
| `lineage.model_version` | baseline_model.py 버전 상수 | W1 |
| `lineage.data_sources` | `["PdM_telemetry.csv", "PdM_errors.csv", ...]` | W1 |

---

## 필드명 불일치 요약

| Evidence Package | 프로토타입 | 처리 방법 |
|-----------|-----------|----------|
| `last_replacement` | `last_replaced` | 변환 레이어 (adapter) |
| `days_elapsed` | `days_since_replaced` | 변환 레이어 |
| `alarm_triggered` (bool) | `status` (str) | `"alarm" if True else "normal"` |
| `sensor_z_score` | `z_score` | 동일 의미, 필드명만 상이 |
| `z_threshold` (float \| str) | `threshold` (float) | str 형태 `'<= -4.0'`는 float 추출 필요 |
