# Result Artifact Schema

작성일: 2026-08-04 (v3 반영: 2026-08-04)

> **기준 데이터셋**: `canonical-ai4i-physics-v3.1`  
> **핵심 입력**: `canonical/model_outputs/result_artifact.jsonl`  
> v3 스키마 버전: `result-artifact-v1.0`

---

## 정의

Result Artifact는 **모델 판정 + 규칙 기반 근거 + 문맥 정보를 합쳐**
대시보드, API, LLM 리포트가 공통으로 소비하는 결과 객체다.

```
Evidence Package (generate_evidence_package)
  + Model Prediction (add_model_prediction)
  ─────────────────────────────────────────
  → Result Artifact
```

프로토타입 스키마 참고: `schemas/prediction-result.schema.json`
(agentic-ontology-dashboard prototype)

---

## 필수 영역

```
subject              장비 식별 정보
prediction           모델 판정 결과
evidence             근거 항목 목록
top_factors          상위 이상 후보 목록
recommended_actions  권고 행동 목록
model                모델 메타데이터
lineage              데이터 계보
data_quality         데이터 품질 경고
```

---

## 필드 상세

### subject

| 필드 | 타입 | 설명 |
|------|------|------|
| equipment_id | int | 장비 ID (Azure PdM: machineID) |
| model_code | str | 장비 모델 코드 (model1~4) |
| age_years | int | 장비 연식 |
| observation_timestamp | str (ISO) | 판단 기준 시각 |
| window_start | str (ISO) | 24h 관측 창 시작 시각 |

### prediction

| 필드 | 타입 | 설명 |
|------|------|------|
| status | "alarm" \| "watch" \| "normal" | 판정 결과 |
| score | float (0~1) | 모델 예측 확률 |
| threshold | float | 알람 기준 임계값 |
| basis | str | 판정 근거 요약 ("model_prediction", "rule_only" 등) |

`alarm`: v3 `status_grade` = critical 또는 warning  
`watch`: v3 `status_grade` = attention  
`normal`: v3 `status_grade` = normal

v3 `status_grade` 분포 (result_artifact.jsonl 100건):
- `critical`: 3건
- `warning`: 12건  
- `attention`: 53건
- `normal`: 32건

### evidence

근거 항목 배열. 각 항목:

| 필드 | 타입 | 설명 |
|------|------|------|
| type | str | "sensor_anomaly" \| "error_event" \| "maintenance_overdue" \| "peer_outlier" |
| sensor \| errorID \| component | str | 근거 대상 |
| value | float \| str | 측정값 또는 관측값 |
| reference | str | 비교 기준 설명 |
| z_score | float \| null | 기준선 대비 편차 (센서 근거인 경우) |
| note | str | 해석 보조 텍스트 ("후보", "가설", "연관" 표현 사용) |

### top_factors

이상 가설 중 상위 N개 요약 (LLM 리포트 입력 최적화):

| 필드 | 타입 | 설명 |
|------|------|------|
| rank | int | 중요도 순위 |
| component | str | 부품 후보 (comp1~4) |
| associated_sensor | str | 연관 센서 |
| sensor_z_score | float | z-score |
| z_threshold | float \| str | 해당 부품 임계 |
| direction | "both" \| "negative" | 이탈 방향 |
| association | str | "sensor_anomaly_associated_with_compX_candidate" |

### recommended_actions

| 필드 | 타입 | 설명 |
|------|------|------|
| priority | "urgent" \| "scheduled" \| "monitor" | 우선순위 |
| action | str | 권고 행동 텍스트 |
| target_component | str \| null | 대상 부품 |
| basis | str | 근거 출처 |

### model

| 필드 | 타입 | 설명 |
|------|------|------|
| name | str | 모델 이름 |
| version | str | 버전 |
| algorithm | str | 알고리즘 이름 |
| train_cutoff | str (ISO) | 학습 데이터 컷오프 |
| features | list[str] | 사용 피처 목록 |

### lineage

| 필드 | 타입 | 설명 |
|------|------|------|
| evidence_id | str | Evidence Package 고유 ID (uuid 등) |
| generated_at | str (ISO) | 생성 시각 |
| data_sources | list[str] | 원천 데이터 파일 목록 |

### data_quality

| 필드 | 타입 | 설명 |
|------|------|------|
| insufficient_data | bool | 창 내 행 < 12 |
| window_rows | int | 실제 관측 행 수 |
| warnings | list[str] | 경고 메시지 목록 |

---

## azure-pdm 출력 → Result Artifact 필드 매핑

| azure-pdm 필드 | Result Artifact 필드 |
|----------------|----------------------|
| `machine_id` | `subject.equipment_id` |
| `timestamp` | `subject.observation_timestamp` |
| `window_start` | `subject.window_start` |
| `model_prediction.probability` | `prediction.score` |
| `model_prediction.threshold` | `prediction.threshold` |
| `model_prediction.alarm_triggered` | `prediction.status` (alarm/watch/normal) |
| `component_hypotheses` | `top_factors` + `evidence` (type=sensor_anomaly) |
| `sensor_evidence` | `evidence` (type=sensor_anomaly) |
| `error_context.errors` | `evidence` (type=error_event) |
| `maintenance_context` | `recommended_actions` + `evidence` (type=maintenance_overdue) |
| `status_flags.insufficient_data` | `data_quality.insufficient_data` |
| `sensor_evidence.window_rows` | `data_quality.window_rows` |

---

## 예시 (축약)

```json
{
  "subject": {
    "equipment_id": 5,
    "model_code": "model3",
    "age_years": 7,
    "observation_timestamp": "2015-09-06T06:00:00",
    "window_start": "2015-09-05T06:00:00"
  },
  "prediction": {
    "status": "alarm",
    "score": 0.72,
    "threshold": 0.35,
    "basis": "model_prediction"
  },
  "top_factors": [
    {
      "rank": 1,
      "component": "comp1",
      "associated_sensor": "volt",
      "sensor_z_score": 4.09,
      "z_threshold": 3.75,
      "direction": "both",
      "association": "sensor_anomaly_associated_with_comp1_candidate"
    }
  ],
  "evidence": [
    {
      "type": "error_event",
      "errorID": "error5",
      "value": "2015-09-05T18:30:00",
      "reference": "24h window",
      "z_score": null,
      "note": "error5 발생 — 과거 50.3% 빈도로 24h 내 고장 전환 (후보)"
    }
  ],
  "data_quality": {
    "insufficient_data": false,
    "window_rows": 24,
    "warnings": []
  }
}
```

---

## 프로토타입 호환성

프로토타입 `prediction-result.schema.json` 주요 필드와의 대응:

| 프로토타입 | 이 스키마 |
|-----------|----------|
| `equipment.equipment_id` | `subject.equipment_id` |
| `failure_probability` | `prediction.score` |
| `threshold` | `prediction.threshold` |
| `status` | `prediction.status` |
| `predicted_failure_type` | `top_factors[0].component` (가설, 미확정) |
| `top_factors` | `top_factors` |
| `evidence` | `evidence` |
| `maintenance_context` | `recommended_actions` + evidence |
| `data_quality_warnings` | `data_quality.warnings` |

---

## 미정 필드 (팀 합의 필요)

| 필드 | 위치 | 생성 방법 |
|------|------|----------|
| `lineage.evidence_id` | `lineage` | uuid4() 또는 hash(machine_id + timestamp) |
| `lineage.generated_at` | `lineage` | 생성 시각 |
| `prediction.basis` | `prediction` | model_prediction 유무로 판단 |
| `recommended_actions` | 최상위 | maintenance_context + hypotheses 기반 규칙 생성 |
| `subject.model_code` | `subject` | machines.csv에서 lookup |
| `subject.age_years` | `subject` | machines.csv에서 lookup |
