# Evidence Package — 설계 및 구현 기록

작성일: 2026-08-01

## 개요

Azure PdM 데이터셋(5개 CSV)을 사용해 이상 이벤트 하나에 대한
근거 패키지를 생성하는 순수 함수. LLM 없음, DB 없음, pandas only.

```python
generate_evidence_package(machine_id, timestamp, tel, errs, fails, maint, mach, baseline=bl)
→ dict
```

---

## 데이터셋 개요

| 파일 | 행 수 | 기간 | 비고 |
|------|-------|------|------|
| PdM_telemetry.csv | 876,100 | 2015-01-01 ~ 2016-01-01 | 100대 × 시간당 1행 |
| PdM_errors.csv | 3,919 | 2015 | errorID: error1~5 |
| PdM_failures.csv | 761 | 2015 | comp1~4 |
| PdM_maint.csv | 3,286 | 2014-06 ~ 2016-01 | 2014년 400건 포함 |
| PdM_machines.csv | 100 | — | model1~4, age |

---

## 함수 구조

```
load_data(data_dir)                     → (tel, errs, fails, maint, mach)
compute_global_baseline(telemetry)      → DataFrame [mean, std] × [volt, rotate, pressure, vibration]
generate_evidence_package(...)          → dict
```

`baseline`은 호출자가 캐시해 반복 사용. 함수 자체는 부수효과 없음.

---

## 출력 스키마

### 최상위

```
machine_id       int
timestamp        str (ISO)
window_start     str (ISO, timestamp - 24h)
sensor_evidence  dict
peer_comparison  dict
error_context    dict
maintenance_context dict
component_hypotheses list[dict]
status_flags     dict
```

### sensor_evidence

24h 창 내 센서 4종의 평균과 전체 기준선 대비 z-score.

```
sensors:
  volt / rotate / pressure / vibration:
    mean_24h       float
    z_score        float | null
    basis:
      global_mean  float   ← 전체 876,100행 기준
      global_std   float
      global_n     int     ← 해당 창의 행 수
window_rows        int
reference_frame    "rolling_mean_std_training_only_lt_2015-10-01"
window:
  start / end      str
```

### peer_comparison

같은 model, 연식 ±3년 장비들의 동일 창 z 분포에서 대상 장비의 백분위.

```
percentile_by_sensor:
  {sensor}:
    percentile       float  (0~100)
    target_z         float
    peers_with_data  int
peer_count           int    ← 조건 충족 장비 수 (데이터 유무 무관)
basis:
  model / age_range / reference_frame
```

### error_context

직전 24h 에러 목록 + errorID별 전체 데이터 기준 24h 고장 전환율.

```
count   int
errors[]:
  errorID                        str
  datetime                       str
  failure_conversion_rate_24h    float (0~1) | null
  basis:
    total_occurrences_all_time   int
    converted_to_failure_24h     int
```

전환율 산출 방식: 전체 데이터에서 해당 errorID 발생 건수 중,
발생 후 24h 내에 같은 장비에서 failure가 기록된 건수의 비율.

### maintenance_context

부품별 마지막 교체 이력.

```
comp1 / comp2 / comp3 / comp4:
  last_replacement   str | null
  days_elapsed       float | null
  type               "preventive" | "reactive" | null
  basis:
    maint_datetime                   str
    failure_within_24h_before_maint  bool
```

- **2014년 400건**: 경과일 계산에 포함, type 판정에도 사용
- **reactive 판정 기준**: 교체 전 24h 내 동일 장비·부품 failure 존재 여부

### component_hypotheses

부품별 임계(thresholds.json)를 초과하는 센서-부품 후보 목록. 단일 확정 금지.

```
[]:
  component          str   (comp1~4)
  associated_sensor  str
  sensor_z_score     float
  z_threshold        float | str   ← 부품별 임계값 (both: float, negative: '<= -N.NN')
  direction          "both" | "negative"
  association        str   ("sensor_anomaly_associated_with_compX_candidate")
```

센서-부품 대응 및 임계:

| 센서 | 부품 | 방향 | 임계 (z_threshold) |
|------|------|------|-------------------|
| volt | comp1 | 양방향 | 3.75 |
| rotate | comp2 | 하락만 (z ≤ -4.00) | '<= -4.0' |
| pressure | comp3 | 양방향 | 5.00 |
| vibration | comp4 | 양방향 | 4.25 |

임계값 출처: `thresholds.json` — 학습 구간(2015-10-01 이전) 기준 이벤트 미탐율 ≤ 5% 조건.

### status_flags

```
no_prior_error       bool  ← 24h 창 에러 0건
multiple_candidates  bool  ← 후보 ≥ 2
insufficient_data    bool  ← 창 내 행 < 12
```

---

## 설계 결정 사항

| 결정 | 이유 |
|------|------|
| 전체 876,100행 기준선 | 특정 기간 편향 방지. reference_frame에 명시 |
| rotate 하락 방향만 | 물리 의미("rotate 하락 → comp2 연관") 반영, 상승은 다른 현상 |
| type 판정에 24h 창 | 같은 날 정비 = 사후 정비, 그 외 = 예방 정비 |
| 2014 maint 포함 (경과일) | 일부 부품은 2014 교체가 마지막 기록. 제외하면 elapsed가 과다 추정됨 |
| peer 백분위 = global z 기준 | 창 내 peer 절대값이 아닌 공통 기준선 z로 비교해야 기간 편향 없음 |
| 인과 표현 금지 | "연관", "후보", "associated" 사용. "원인", "cause" 금지 |
| 물리 단위 미표기 | z-score, 백분위만 반환. volt/rpm/bar 등 단위 없음 |

---

## pytest 결과 (2026-08-04)

**30 passed in 2.01s**

| 클래스 | 케이스 | 테스트 수 |
|--------|--------|----------|
| TestNormalPeriod | machine=1, 2015-06-15 12:00 (정상 구간) | 12 |
| TestPreFailure24h | machine=5, 2015-09-06 06:00 (고장 직전, error5 있음) | 7 |
| TestFailureNoWarning | machine=1, 2015-01-05 06:00 (선행 경고 없는 고장) | 8 |
| TestEdgeCases | 범위 밖 타임스탬프, null 처리, JSON 직렬화 | 3 |

### 주요 검증 항목

- 상태 플래그 3종 일관성 (error_context.count와 no_prior_error 동기화 등)
- 에러 전환율 분자 ≤ 분모 보장
- z 임계 방향성 준수 (rotate 음수만)
- 2014 maint 경과일 반영 확인
- "원인"/"cause" 표현 없음 확인
- JSON 직렬화 가능 (numpy 타입 유출 없음)
- insufficient_data: 창 밖 타임스탬프 → row=0 → True

---

## 샘플 출력 (케이스 2 발췌)

machine=5, 2015-09-06 06:00:00 (comp1 고장 시각, error5 직전 발생)

```json
{
  "sensor_evidence": {
    "sensors": {
      "rotate": { "mean_24h": 355.63, "z_score": -1.73 }
    },
    "reference_frame": "rolling_mean_std_training_only_lt_2015-10-01"
  },
  "error_context": {
    "errors": [{
      "errorID": "error5",
      "failure_conversion_rate_24h": 0.5028,
      "basis": { "total_occurrences_all_time": 356, "converted_to_failure_24h": 179 }
    }]
  },
  "status_flags": {
    "no_prior_error": false,
    "multiple_candidates": false,
    "insufficient_data": false
  }
}
```

---

## 파일 위치

```
pdm-mvp/
├── archive/
│   ├── PdM_telemetry.csv
│   ├── PdM_errors.csv
│   ├── PdM_failures.csv
│   ├── PdM_maint.csv
│   └── PdM_machines.csv
├── evidence_package.py       ← 구현
├── test_evidence_package.py  ← pytest
└── EVIDENCE_PACKAGE.md       ← 이 문서
```
