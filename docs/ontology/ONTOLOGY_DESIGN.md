> **주의:** 이 문서는 아래 세 파일로 분리되었습니다. 새 작업은 분리된 문서를 참조하십시오.
>
> - `ONTOLOGY_CORE.md` — 도메인 중립 코어 (어떤 설비 도메인에도 적용)
> - `PACK_AZURE_PDM.md` — Azure PdM 도메인 매핑
> - `PACK_AI4I.md` — AI4I 도메인 매핑 (중립성 검증용 스케치)
>
> 이 문서는 분리 이전의 원본으로 보존됩니다.

---

# Azure PdM 온톨로지 설계

작성일: 2026-08-03
대상: `azure-pdm` 저장소 (Azure Predictive Maintenance 5개 CSV + `evidence_package.py` + `manager_app.py`)

## 설계 원칙

1. **모든 속성은 출처가 있다.** 원본 CSV, 계산 코드, 또는 신규 정의 중 하나로 표기한다.
2. **인과 표현 금지.** `evidence_package.py`의 원칙을 온톨로지에도 적용한다. 연관·후보·가설만 쓰고 원인·확정은 쓰지 않는다.
3. **판정 근거는 객체다.** 기준선과 임계값을 1급 객체로 두어 어떤 버전으로 판정했는지 되짚을 수 있게 한다.
4. **행동은 기록된다.** 현재 시스템은 읽기 전용이다. 액션 계층을 추가해 사람의 판단이 남게 한다.

## 출처 표기 규칙

| 표기 | 뜻 |
|---|---|
| `원본` | 5개 CSV에 직접 존재 |
| `계산` | `evidence_package.py` 또는 `manager_app.py`에 이미 구현됨 |
| `신규` | 본 설계에서 추가 제안. 기존 데이터로 산출 가능 |
| `합성` | 데이터에 없음. 별도 생성 필요 |

---

# 1. 객체 타입

## 1.1 자산 계층

### Machine — 설비

인터페이스: `Monitorable`

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `machine_id` | integer | 원본 | `PdM_machines.machineID` (1~100) |
| `model_code` | string | 원본 | `model1`~`model4` |
| `age` | integer | 원본 | 연식(년) |
| `failure_count` | integer | 신규 | `PdM_failures`에서 집계. 실측 분포: 최소 2 / 중앙값 7 / 최대 19, **고장 0건 설비 2대** |
| `preventive_count` | integer | 계산 | `compute_equipment_maint_counts().by_machine` |
| `reactive_count` | integer | 계산 | 같음 |
| `reactive_ratio` | number | 신규 | `reactive / (preventive + reactive)`. 정비 품질 지표 |
| `fleet_rank_reactive` | integer | 계산 | 100대 중 사후 교체 건수 순위 |
| `mtbf_days` | number \| null | 신규 | 고장 간 평균 시간. ISO 14224 표준 지표. **고장 0건 설비 2대는 null, 2건인 설비는 간격 표본이 1개뿐이므로 신뢰도 표기 필요** |
| `time_since_last_failure_days` | number | 신규 | |
| `criticality` | string | **합성** | 우선순위 판단에 필요. 원본에 없음 |
| `downtime_cost_per_hour` | number | **합성** | 기대손실 계산에 필요 |
| `location` | string | **합성** | |
| `assigned_engineer` | string | **합성** | |

### MachineModel — 설비 모델

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `model_code` | string | 원본 | `model1`~`model4` |
| `machine_count` | integer | 신규 | 해당 모델 설비 수 |
| `failure_rate_per_machine` | number | 신규 | 대당 평균 고장 건수. **실측: model1 11.8 / model2 9.9 / model3 6.3 / model4 5.7** |
| `machine_count` 실측 | — | — | model3 35 / model4 32 / model2 17 / model1 16 |
| `mean_age` | number | 신규 | 연식 범위 0~20년 |
| `same_equipment_class` | boolean | 신규 | 항상 `true`. 아래 근거 참조 |

> 모델별 고장률 차이가 실측으로 확인된다. **model1이 model4의 두 배 이상 고장난다.** 조달·교체 우선순위 판단의 근거가 되므로 객체로 승격한다.
>
> **네 모델은 같은 종류의 설비다.** 센서 분포를 대조한 결과 모델 간 평균 차이가 전부 표준편차의 5% 미만이다.
>
> | | volt | rotate | pressure | vibration |
> |---|---|---|---|---|
> | model1 | 170.64 ± 15.63 | 445.92 ± 52.80 | 101.36 ± 11.45 | 40.44 ± 5.43 |
> | model2 | 170.56 ± 15.49 | 446.13 ± 52.60 | 101.27 ± 11.48 | 40.43 ± 5.42 |
> | model3 | 170.81 ± 15.50 | 446.74 ± 52.89 | 100.74 ± 10.85 | 40.37 ± 5.36 |
> | model4 | 170.76 ± 15.48 | 446.17 ± 52.57 | 100.59 ± 10.90 | 40.35 ± 5.37 |
>
> 이 확인이 두 가지를 뒷받침한다.
>
> **첫째, 전역 기준선이 타당하다.** `compute_global_baseline()`은 100대를 통합해 평균·표준편차를 낸다. 모델별로 물리량 범위가 달랐다면 z-score가 모델에 편향됐을 것이다. `BaselineProfile.reference_frame`이 `global_baseline_all_machines_all_time`인 근거가 여기 있다.
>
> **둘째, 모델 간 고장률 차이의 성격이 분명해진다.** 운전 조건이 비슷한데 결과가 갈린다. 부하 차이가 아니라 설비 특성 차이로 읽을 근거가 된다.
>
> 다만 압력 평균이 model1·2(101.3)가 model3·4(100.6)보다 약간 높고, 이는 가압 계통 고장 분포와 방향이 같다. **차이가 표준편차의 6% 수준이므로 관찰된 상관일 뿐이며 원인으로 기술하지 않는다.**

### Component — 계통

인터페이스: `Monitorable`, `Maintainable`

설비 100대 × 4계통 = **400개 인스턴스**. 정비와 고장이 계통 단위로 기록되므로 반드시 객체여야 한다.

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `component_id` | string | 신규 | `{machine_id}-{comp_code}` |
| `comp_code` | string | 원본 | `comp1`~`comp4` |
| `display_name` | string | 계산 | `display_names.COMP_DISPLAY` (구동/회전/가압/베어링 계통) |
| `last_replacement_at` | datetime | 계산 | `maintenance_context.{comp}.last_replacement` |
| `days_since_replacement` | number | 계산 | `maintenance_context.{comp}.days_elapsed` |
| `last_replacement_type` | string | 계산 | `preventive` / `reactive` |
| `failure_within_24h_before_maint` | boolean | 계산 | reactive 판정 근거 |
| `replacement_count_preventive` | integer | 계산 | `by_machine_comp` |
| `replacement_count_reactive` | integer | 계산 | 같음 |
| `median_interval_days` | number | 신규 | **계통 코드별** 교체 간격 중앙값. 실측: comp1 45일 / comp2 **30일** / comp3 45일 / comp4 45일 (각 표본 700건 이상) |
| **`life_ratio`** | number | **신규** | `days_since_replacement / median_interval_days`. **수명의 몇 %인지** — 매니저 화면에 직접 쓸 수 있는 단일 지표 |
| `failure_count` | integer | 신규 | 해당 계통 고장 건수 |
| `mtbf_days` | number | 신규 | 계통 단위 고장 간 평균 시간 |

> **검증 완료.** 400개 (설비, 계통) 조합이 모두 실제 데이터에 등장하며, 교체 간격을 산출할 수 없는 조합은 **0개**다. `life_ratio`는 전 인스턴스에서 계산 가능하다.
>
> `compute_maint_interval_stats()`는 지금 **유형별(preventive/reactive) 전역 중앙값**만 낸다. `life_ratio`에는 계통별 중앙값이 더 적절하므로 신규 산출이 필요하다. 회전 계통(comp2)만 30일로 나머지의 3분의 2인데, 이 차이가 전역 중앙값에 묻히기 때문이다.

> `display_name`은 원본에 물리적 정체가 정의돼 있지 않아 센서 특성으로 추정한 것이다. `display_names.py`가 이미 이 한계를 명시하고 있으며, 온톨로지에도 `name_is_inferred: true` 플래그로 남긴다.

### SensorChannel — 센서 채널

인터페이스: `Monitorable`

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `sensor_code` | string | 원본 | `volt` / `rotate` / `pressure` / `vibration` |
| `direction` | string | 계산 | `both` / `negative`. comp2는 하락 방향만 유효 |
| `unit` | string | — | **원본에 단위 없음.** `null`로 두고 물리 단위 미표기 원칙 유지 |
| `associated_component_code` | string | 계산 | `SENSOR_COMP` 매핑 |

> 센서-계통 대응이 지금은 파이썬 dict에 하드코딩돼 있다. 링크로 승격하면 매핑 변경이 코드 수정이 아니라 데이터 변경이 된다.

### PeerGroup — 동종 집단

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `group_id` | string | 신규 | `{model}-{age_min}-{age_max}` |
| `model_code` | string | 계산 | |
| `age_min` / `age_max` | integer | 계산 | 대상 설비 연식 ±3년 |
| `member_count` | integer | 계산 | `peer_comparison.peer_count` |

| **`sufficient_peers`** | boolean | **신규** | `member_count >= 5`. 미달 시 백분위 판단 보류 |

> 고유 `(모델, 연식)` 조합이 **56개**이므로 그룹도 최대 56개다.
>
> **집단 크기가 심각하게 작다.** 실측 결과:
>
> | 모델 | peer 최소 | 중앙 | 최대 |
> |---|---|---|---|
> | model1 | 3 | 9 | 10 |
> | **model2** | **2** | **4** | 7 |
> | model3 | 4 | 11 | 19 |
> | model4 | 5 | 10 | 14 |
>
> **동종 설비가 2대뿐인 경우가 있고, 5대 미만인 설비가 100대 중 16대다.** 동종 2대에서 뽑은 백분위는 "세 대 중 한 대"라는 뜻이라 판단 근거가 되지 못한다.
>
> 기존 코드는 정비 이력 5건 미만일 때 "이력이 적어 비교가 어려움"을 표시한다. **동종 비교에도 같은 처리가 필요하다.** `sufficient_peers`가 `false`면 백분위를 표시하되 판단 근거로 쓰지 않는다.

### ComponentClass — 계통 타입

`MachineModel`을 만들면서 이쪽을 빠뜨렸다. 계통 **인스턴스**(400개)만 있고 `comp1~4` 자체를 담을 객체가 없으면, 계통 단위 통계가 400곳에 중복 저장된다.

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `comp_code` | string | 원본 | `comp1`~`comp4` |
| `display_name` | string | 계산 | 구동 / 회전 / 가압 / 베어링 계통 |
| `name_is_inferred` | boolean | — | 항상 `true`. 원본에 물리적 정체 없음 |
| `median_interval_days` | number | 신규 | **실측: comp1 45 / comp2 30 / comp3 45 / comp4 45** |
| `total_failures` | integer | 신규 | **실측: comp1 192 / comp2 259 / comp3 131 / comp4 179** |
| `annual_replacements` | integer | 신규 | **실측(2015): comp1 702 / comp2 761 / comp3 706 / comp4 710** |
| `hypothesis_hit_rate` | number | 신규 | 이 계통 가설의 적중률 |

### ModelComponentProfile — 모델 × 계통 교차

**이 설계에서 가장 값어치 있는 신규 객체다.** 모델과 계통을 각각 따로 두면 교차 패턴이 보이지 않는다.

| 속성 | 타입 | 출처 |
|---|---|---|
| `profile_id` | string | 신규 — `{model_code}-{comp_code}` |
| `failures_per_machine` | number | 신규 |
| `median_interval_days` | number | 신규 |
| `machine_count` | integer | 신규 |

실측 결과 (대당 고장 건수):

| | comp1 | comp2 | **comp3** | comp4 |
|---|---|---|---|---|
| model1 | 2.1 | 2.9 | **4.2** | 2.6 |
| model2 | 1.8 | 2.4 | **3.7** | 2.0 |
| model3 | 1.9 | 2.5 | **0.0** | 1.8 |
| model4 | 1.9 | 2.6 | **0.0** | 1.2 |

> **가압 계통 고장은 model1·model2에서만 발생한다.** model3·model4는 1년간 단 한 건도 없다. 다른 계통은 모델 간 차이가 미미한데 comp3만 극단적으로 갈린다.
>
> 앞서 확인한 모델별 총 고장률 차이(model1 11.8 vs model4 5.7)의 **대부분이 이 한 칸에서 나온다.** 조달 판단("model1을 더 사지 마라")과 정비 계획("model1·2의 가압 계통을 집중 관리")으로 바로 이어진다.
>
> 그리고 이건 **설비 한 대를 조회해서는 절대 나오지 않는다.** 함대 축이 필요한 이유의 실증이다.

### PartDemandProfile — 부품 수요

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `comp_code` | string | 신규 | |
| `monthly_mean` | number | 신규 | **실측: comp1 58.5 / comp2 63.4 / comp3 58.8 / comp4 59.2** |
| `monthly_median` | number | 신규 | |
| `monthly_peak` | integer | 신규 | **실측 최대: comp2 76, comp3 75** |
| `preventive_share` | number | 신규 | 예방 교체 비중 |

> 월 수요가 계통당 58~63건으로 안정적이고 최대치가 평균의 1.2배 수준이다. **재고 수준 판단에 바로 쓸 수 있는 실측 분포**이며, 합성이 아니다.

---

## 1.2 함대 계층

### FleetSnapshot — 함대 상태

특정 시각의 100대 전체 상태. `manager_app.py`의 Top-K 목록이 지금은 매번 재계산되고 버려지는데, 이를 객체로 두면 시간에 따른 비교가 가능해진다.

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `snapshot_id` | string | 신규 | `fleet@{timestamp}` |
| `evaluated_at` | datetime | 신규 | |
| `machine_count` | integer | 신규 | 평가 대상 설비 수 |
| `alarm_count` | integer | 계산 | 알람 등급 건수 |
| `watch_count` | integer | 계산 | 관찰 등급 건수 |
| `insufficient_data_count` | integer | 계산 | 데이터 부족 설비 수 |
| `top_k_event_refs` | list | 계산 | 우선 확인 대상 |
| `baseline_version` / `threshold_policy_version` | string | 신규 | 어떤 정책으로 평가했는지 |

> **정책 효과 측정이 여기서 가능해진다.** 임계값 정책 버전을 바꾼 전후의 `FleetSnapshot`을 비교하면 알람 건수가 어떻게 변했는지가 나온다. 지금은 이걸 볼 방법이 없다.

### MaintenanceCapacity — 정비 여력

| 속성 | 타입 | 출처 |
|---|---|---|
| `period` | string | **합성** |
| `available_technician_hours` | number | **합성** |
| `assigned_work_order_count` | integer | 계산 (WorkOrder에서) |

> 우선순위 배분의 다른 축이다. 위험한 설비가 동시에 5대여도 인력이 2명이면 3대는 대기다. 원본에 인력 데이터가 없어 합성이 필요하지만, **합성 없이는 "누구부터"라는 매니저의 1번 질문에 답이 반쪽**이다.

---

## 1.3 관측 계층

### TelemetryWindow — 24시간 관측 창

876,100행을 개별 객체로 만들지 않는다. 시스템이 실제로 추론하는 단위는 24시간 집계 창이다.

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `window_id` | string | 신규 | `{machine_id}@{window_end}` |
| `window_start` / `window_end` | datetime | 계산 | |
| `row_count` | integer | 계산 | `sensor_evidence.window_rows` |
| `sufficient_data` | boolean | 계산 | `row_count >= 12` |
| `reference_frame` | string | 계산 | `global_baseline_all_machines_all_time` |

**센서별 측정값** (`SensorReading` 하위 구조 또는 별도 객체):

| 속성 | 타입 | 출처 |
|---|---|---|
| `mean_24h` | number | 계산 |
| `z_score` | number \| null | 계산 |
| `excess_ratio` | number | 계산 — `|z| / component_threshold` |
| `peer_percentile` | number | 계산 — 동종 집단 내 백분위 |
| `trend_label` | string | 계산 — `상승 중` / `하락 중` / `보합` / `판단 불가` |
| `trend_change_pct` | number | 신규 — 지금은 라벨만 반환. 변화율 자체를 노출 |

---

## 1.4 이력 계층

### ErrorType — 경고 유형

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `error_code` | string | 원본 | `error1`~`error5` |
| `display_name` | string | 계산 | `ERROR_DISPLAY` |
| `total_occurrences` | integer | 계산 | 전체 발생 건수 |
| `converted_to_failure_24h` | integer | 계산 | 24h 내 고장 전환 건수 |
| `failure_conversion_rate_24h` | number | 계산 | 전환율 |

> **경고-계통 연관은 1:1이 아니다.** error2와 error3이 모두 회전 계통에 연관된다. 링크 카디널리티를 many-to-many로 두고, 연관 강도를 링크 속성으로 갖는다.

### ErrorEvent — 경고 발생

인터페이스: `TemporalEvent`

| 속성 | 타입 | 출처 |
|---|---|---|
| `event_id` | string | 신규 |
| `occurred_at` | datetime | 원본 |
| `error_code` | string | 원본 |

### FailureEvent — 고장 발생

인터페이스: `TemporalEvent`

| 속성 | 타입 | 출처 |
|---|---|---|
| `event_id` | string | 신규 |
| `occurred_at` | datetime | 원본 |
| `comp_code` | string | 원본 — `PdM_failures.failure` |

### MaintenanceRecord — 정비 기록

인터페이스: `TemporalEvent`, `Auditable`

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `record_id` | string | 신규 | |
| `performed_at` | datetime | 원본 | |
| `comp_code` | string | 원본 | |
| `type` | string | 계산 | `preventive` / `reactive` |
| `failure_within_24h_before` | boolean | 계산 | type 판정 근거 |
| `interval_since_previous_days` | number | 신규 | 이전 교체와의 간격 |

> 2014년 400건은 경과일 계산에 포함하되 `type` 학습에서는 제외한다는 기존 결정을 `in_training_scope: boolean` 속성으로 명시한다.

---

## 1.5 판정 계층

### RiskEvent — 위험 사건

인터페이스: `EvidenceBearing`, `TemporalEvent`

**현재 시스템에 없는 객체다.** 지금은 등급을 화면에서 즉석 계산하고 버린다. 행동과 감사를 붙이려면 사건이 영속화되어야 한다.

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `event_id` | string | 신규 | |
| `detected_at` | datetime | 계산 | |
| `grade` | string | 계산 | `알람` / `관찰` / `정상` |
| `grade_display` | string | 계산 | `즉시 교체 필요` / `관찰 필요` / `정상` |
| `max_excess_ratio` | number | 계산 | 등급 판정의 근거값 |
| `primary_component_code` | string | 계산 | 최대 초과 계통 |
| `grade_failure_rate_pct` | number | 계산 | 등급별 과거 24h 고장률 (알람 24.0 / 관찰 15.7 / 정상 0.01) |
| **`status`** | string | **신규** | `미확인` / `확인됨` / `조치중` / `종결` |
| **`acknowledged_by`** | string | **신규** | |
| **`acknowledged_at`** | datetime | **신규** | |

### EvidencePackage — 근거 패키지

인터페이스: `EvidenceBearing`, `Versioned`

`generate_evidence_package()`의 출력이 그대로 대응된다.

| 속성 | 타입 | 출처 |
|---|---|---|
| `evidence_id` | string | 신규 |
| `generated_at` | datetime | 신규 |
| `window_ref` | string | 계산 |
| `no_prior_error` | boolean | 계산 |
| `multiple_candidates` | boolean | 계산 |
| `insufficient_data` | boolean | 계산 |
| `baseline_version` | string | **신규 — 링크로** |
| `threshold_policy_version` | string | **신규 — 링크로** |

> 기존 `ontology-dashboard`에서 지적된 **lineage 단방향 문제**가 여기서 해결된다. 근거가 어떤 기준선·임계값 버전으로 만들어졌는지가 문자열이 아니라 객체 참조가 된다.

### ComponentHypothesis — 계통 가설

| 속성 | 타입 | 출처 | 설명 |
|---|---|---|---|
| `hypothesis_id` | string | 신규 | |
| `comp_code` | string | 계산 | |
| `sensor_code` | string | 계산 | |
| `sensor_z_score` | number | 계산 | |
| `z_threshold` | number | 계산 | |
| `direction` | string | 계산 | |
| `association` | string | 계산 | `sensor_anomaly_associated_with_compX_candidate` |
| **`confirmed_by_failure`** | boolean | **신규** | 이후 실제 고장으로 확인됐는지 |

> `confirmed_by_failure`를 두면 **가설 적중률**을 집계할 수 있다. "가압 계통 가설이 187번 제기됐고 그중 41번 실제 고장" — 시스템이 자기 성능을 스스로 보고하는 지표가 된다.

---

## 1.6 정책 계층

### BaselineProfile — 기준선

인터페이스: `Versioned`

| 속성 | 타입 | 출처 |
|---|---|---|
| `version` | string | 신규 |
| `computed_on` | date | 원본 (`baseline_constants.json`) |
| `training_cutoff` | datetime | 원본 |
| `window_hours` | integer | 원본 |
| `min_periods` | integer | 원본 |
| 센서별 `mean` / `std` / `n_samples` | number | 원본 |

### ThresholdPolicy — 임계값 정책

인터페이스: `Versioned`

| 속성 | 타입 | 출처 |
|---|---|---|
| `version` | string | 신규 |
| `computed_on` | date | 원본 (`thresholds.json`) |
| `training_cutoff` | datetime | 원본 |
| `selection_rule` | string | 원본 |
| `miss_target_pct` | number | 원본 |
| 계통별 `sensor` / `direction` / `threshold` | — | 원본 |
| 계통별 `train_*` / `val_*` 지표 | — | 원본 |

> 임계값 정책을 객체로 두면 매니저 화면에서 **"이 기준은 왜 이 값인가"**에 답할 수 있다. 미탐 5% 제약과 train/val 검증 결과가 그대로 근거가 된다.

---

## 1.7 행동 계층 — 전부 신규

현재 시스템에 완전히 빠져 있는 부분이다.

### WorkOrder — 작업 지시

인터페이스: `Auditable`

| 속성 | 타입 | 설명 |
|---|---|---|
| `work_order_id` | string | |
| `status` | string | `요청됨` / `배정됨` / `진행중` / `완료` / `보류` |
| `assignee` | string | |
| `due_at` | datetime | |
| `work_type` | string | `점검` / `교체` / `정밀진단` |
| `created_from_event_id` | string | 어느 위험 사건에서 나왔는지 |

### OperationalDecision — 운영 판단

인터페이스: `Auditable`

| 속성 | 타입 | 설명 |
|---|---|---|
| `decision_id` | string | |
| `decision` | string | `계속 운전` / `점검 요청` / `정지 검토` |
| `rationale` | string | 판단 근거 메모 |
| `decided_by` | string | |
| `decided_at` | datetime | |
| `evidence_ref` | string | 어떤 근거를 보고 판단했는지 |

---

# 2. 인터페이스

다형성 조회를 위한 분류. 기존 `ontology-dashboard`에서 선언만 되고 쓰이지 않던 기능을 실제로 사용한다.

| 인터페이스 | 구현 객체 | 가능해지는 질의 |
|---|---|---|
| `Monitorable` | Machine, Component, SensorChannel | 감시 대상 전체의 최근 이상 등급 |
| `Maintainable` | Component | 정비 대상 중 수명 비율 상위 N |
| `EvidenceBearing` | RiskEvent, EvidencePackage | 근거를 가진 모든 판정의 추적 |
| `Auditable` | WorkOrder, OperationalDecision, MaintenanceRecord | 사람의 행동 전체 이력 |
| `Versioned` | BaselineProfile, ThresholdPolicy | 정책 변경 이력과 영향 범위 |
| `TemporalEvent` | ErrorEvent, FailureEvent, MaintenanceRecord, RiskEvent | 한 설비의 시간순 전체 사건 |
| `FleetAggregate` | MachineModel, ComponentClass, ModelComponentProfile, PartDemandProfile, FleetSnapshot | **모집단 단위 통계 전체** — 개별 설비를 거치지 않는 조회 |

`TemporalEvent`가 특히 유용하다. 지금은 경고·고장·정비를 각각 다른 DataFrame에서 조회하지만, 인터페이스로 묶으면 **"이 설비에 지난 90일간 일어난 모든 일"**이 한 번의 순회로 나온다.

---

# 3. 링크 타입

| 링크 | 출발 | 도착 | 카디널리티 | 출처 |
|---|---|---|---|---|
| `machine_has_model` | Machine | MachineModel | many-to-one | 원본 |
| `machine_has_component` | Machine | Component | one-to-many | 신규 (400 인스턴스 생성) |
| `component_has_class` | Component | ComponentClass | many-to-one | 신규 |
| `model_component_profile_of_model` | ModelComponentProfile | MachineModel | many-to-one | 신규 |
| `model_component_profile_of_class` | ModelComponentProfile | ComponentClass | many-to-one | 신규 |
| `component_class_has_demand_profile` | ComponentClass | PartDemandProfile | one-to-one | 신규 |
| `fleet_snapshot_includes_event` | FleetSnapshot | RiskEvent | one-to-many | 신규 |
| `fleet_snapshot_used_threshold_policy` | FleetSnapshot | ThresholdPolicy | many-to-one | 신규 |
| `component_monitored_by` | Component | SensorChannel | one-to-one | 계산 (`SENSOR_COMP`) |
| `machine_belongs_to_peer_group` | Machine | PeerGroup | many-to-many | 계산 |
| `machine_has_telemetry_window` | Machine | TelemetryWindow | one-to-many | 계산 |
| `machine_had_error` | Machine | ErrorEvent | one-to-many | 원본 |
| `error_event_has_type` | ErrorEvent | ErrorType | many-to-one | 원본 |
| `error_type_associated_with_component` | ErrorType | Component | **many-to-many** | 계산 |
| `component_had_failure` | Component | FailureEvent | one-to-many | 원본 |
| `component_had_maintenance` | Component | MaintenanceRecord | one-to-many | 원본 |
| `maintenance_followed_failure` | MaintenanceRecord | FailureEvent | many-to-one | 계산 (reactive 판정) |
| `machine_has_risk_event` | Machine | RiskEvent | one-to-many | 신규 |
| `risk_event_has_evidence` | RiskEvent | EvidencePackage | one-to-one | 신규 |
| `evidence_covers_window` | EvidencePackage | TelemetryWindow | one-to-one | 계산 |
| `evidence_used_baseline` | EvidencePackage | BaselineProfile | many-to-one | **신규 — lineage** |
| `evidence_used_threshold_policy` | EvidencePackage | ThresholdPolicy | many-to-one | **신규 — lineage** |
| `evidence_proposes_hypothesis` | EvidencePackage | ComponentHypothesis | one-to-many | 계산 |
| `hypothesis_names_component` | ComponentHypothesis | Component | many-to-one | 계산 |
| `hypothesis_cites_sensor` | ComponentHypothesis | SensorChannel | many-to-one | 계산 |
| `risk_event_triggered_decision` | RiskEvent | OperationalDecision | one-to-many | 신규 |
| `decision_created_work_order` | OperationalDecision | WorkOrder | one-to-many | 신규 |
| `work_order_targets_component` | WorkOrder | Component | many-to-one | 신규 |
| `work_order_produced_maintenance` | WorkOrder | MaintenanceRecord | one-to-many | 신규 |

**핵심은 마지막 링크다.** `WorkOrder → MaintenanceRecord`가 연결되면 **작업 지시가 실제 정비로 이어졌는지**가 추적된다. 지금 `maint` 테이블은 결과만 있고 왜 했는지가 없다.

---

# 4. 액션 타입

전부 신규. 현재 시스템은 읽기 전용이다.

| 액션 | 대상 | 파라미터 | 필요 권한 | 승인 필요 |
|---|---|---|---|---|
| `acknowledge_risk_event` | RiskEvent | — | `events.acknowledge` | 아니오 |
| `record_operational_decision` | RiskEvent | decision, rationale | `events.decision` | 예 |
| `create_work_order` | RiskEvent | component, work_type, due_at | `workorders.create` | 예 |
| `assign_work_order` | WorkOrder | assignee | `workorders.assign` | 예 |
| `record_work_order_note` | WorkOrder | body | `workorders.note` | 아니오 |
| `complete_work_order` | WorkOrder | checklist, measurements, note | `workorders.complete` | 예 |
| `mark_work_order_blocked` | WorkOrder | reason, safety_risk | `workorders.complete` | 예 |
| `request_grade_review` | RiskEvent | rationale | `events.review` | 예 |

## 역할별 권한

| 권한 | 운영 매니저 | 도메인 엔지니어 |
|---|---|---|
| `events.acknowledge` | ○ | ○ |
| `events.decision` | ○ | — |
| `events.review` | — | ○ |
| `workorders.create` | ○ | — |
| `workorders.assign` | ○ | — |
| `workorders.note` | ○ | ○ |
| `workorders.complete` | — | ○ |

**매니저는 지시하고 엔지니어는 수행한다.** 이 비대칭이 화면 차이를 권한으로 뒷받침한다. 지금은 화면이 파일로만 분리돼 있고 경계가 없다.

---

# 5. 신규 파라미터 요약

기존 데이터로 산출 가능하며 화면 가치가 높은 것들.

| 파라미터 | 대상 | 산출 | 쓰임 |
|---|---|---|---|
| **`life_ratio`** | Component | `days_since_replacement / median_interval_days` | 매니저 화면 단일 지표. "수명의 140% 초과" |
| `reactive_ratio` | Machine | `reactive / total` | 정비 품질. 높으면 예방정비 실패 |
| `mtbf_days` | Machine, Component | 고장 간 평균 시간 | ISO 14224 표준 지표 |
| `time_since_last_failure_days` | Machine | | 안정 기간 |
| `failure_rate_per_machine` | MachineModel | | 조달 판단 |
| `trend_change_pct` | TelemetryWindow | 이미 계산 중, 노출만 | 라벨 대신 수치 |
| **`confirmed_by_failure`** | ComponentHypothesis | 사후 대조 | **가설 적중률 = 시스템 자체 평가** |
| `interval_since_previous_days` | MaintenanceRecord | | 교체 주기 분포 |
| **`failures_per_machine`** | ModelComponentProfile | 모델 × 계통 교차 집계 | **조달·집중관리 판단. comp3 고장이 model1·2에만 발생** |
| `monthly_mean` / `monthly_peak` | PartDemandProfile | 월별 교체 집계 | 재고 수준 |
| `alarm_count` / `watch_count` | FleetSnapshot | 등급 집계 | 정책 효과 측정 |

## 합성이 필요한 것

원본에 없으며 운영 판단에 필요하다. **합성임을 화면과 문서에 명시해야 한다.**

| 항목 | 대상 | 필요 이유 |
|---|---|---|
| `criticality` | Machine | 우선순위 |
| `downtime_cost_per_hour` | Machine | 기대손실 계산 |
| `location` | Machine | 작업 배정 |
| `assigned_engineer` | Machine | 담당자 |
| 부품 재고·리드타임 | Component | 교체 가능 여부 |
| 생산 오더·납기 | (신규 객체) | 정지 영향 |
| `available_technician_hours` | MaintenanceCapacity | **"누구부터" 판단의 나머지 절반** |

> 최소 구현에서는 `criticality`와 `downtime_cost_per_hour` 둘만 합성해도 기대손실 비교가 성립한다. 나머지는 뒤로 미룰 수 있다.
>
> 다만 함대 운영의 핵심 질문("오늘 누구부터")에는 정비 여력이 필요하다. 실측 부품 수요(`PartDemandProfile`, 월 58~63건)가 있으므로, **인력 시간만 합성하면 수요와 여력을 맞대볼 수 있다.**

---

# 6. 이 설계로 답할 수 있게 되는 질문

현재는 함수를 새로 짜야 답할 수 있는 것들이다.

## 단순 순회 (2~3홉)

- 이 설비의 4개 계통 중 수명 비율이 가장 높은 것은?
- 이 근거는 어떤 기준선·임계값 버전으로 만들어졌나?
- 이 정비 기록은 어떤 작업 지시에서 나왔나?

## 함대 질의 (3~4홉)

- 가압 계통 사후 교체가 잦은 설비 상위 10대
- 동종 집단 내에서 이 설비의 진동 백분위 추이
- **가압 계통 고장이 어느 모델에 몰려 있나** → `ModelComponentProfile` 한 번 조회로 model1·2에만 발생함이 나온다
- **다음 달 계통별 부품 소요량은** → `PartDemandProfile.monthly_mean` + `monthly_peak`
- **지금 알람이 어제보다 늘었나** → `FleetSnapshot` 두 개 비교
- **정비 여력 대비 알람 건수가 초과했나** → `FleetSnapshot.alarm_count` vs `MaintenanceCapacity`

## 자기 평가 (4~5홉)

- **가압 계통 가설의 적중률은?** (`ComponentHypothesis → confirmed_by_failure` 집계)
- **알람 등급 중 실제 조치로 이어진 비율은?** (`RiskEvent → Decision → WorkOrder → MaintenanceRecord`)
- **임계값 정책 버전을 바꾼 뒤 미탐이 줄었나?** (`ThresholdPolicy → FleetSnapshot → RiskEvent`)
- **model3·model4에서 가압 계통 가설이 제기된 적 있나?** — 실제 고장이 0건인데 가설이 나왔다면 **오탐 패턴**이다. (`ModelComponentProfile` × `ComponentHypothesis`)

마지막 네 개가 이 설계의 값어치다. **시스템이 자기 판단의 결과를 되짚는 질의**이고, 온톨로지 없이는 매번 새 스크립트를 써야 한다.

특히 마지막 질의는 실측 데이터가 **답이 있을 것이라고 시사한다.** model3·4는 가압 계통이 1년간 한 번도 고장나지 않았는데, 압력 센서 z-score는 다른 모델과 같은 임계값(5.0)으로 판정된다. 이 조합에서 알람이 발생했다면 전부 오탐이다. 온톨로지 순회 한 번으로 확인된다.

---

# 7. 규모

| 객체 타입 | 인스턴스 수 |
|---|---|
| Machine | 100 |
| MachineModel | 4 |
| Component | **400 (검증 완료 — 전 조합 데이터 존재)** |
| ComponentClass | 4 |
| **ModelComponentProfile** | **16** (모델 4 × 계통 4) |
| PartDemandProfile | 4 |
| SensorChannel | 4 (타입) 또는 400 (인스턴스) |
| PeerGroup | 최대 56 |
| FleetSnapshot | 조회 시각당 1 |
| MaintenanceCapacity | 기간당 1 (합성) |
| ErrorType | 5 |
| ErrorEvent | 3,919 |
| FailureEvent | 761 |
| MaintenanceRecord | 3,286 |
| TelemetryWindow | 조회 시 생성 (876,100행에서 파생) |
| EvidencePackage | 조회 시 생성 |
| RiskEvent | 판정 시 생성 |

정적 객체는 약 **8,500개**로 SQLite에서 무리 없다. `TelemetryWindow`만 지연 생성한다.

---

# 8. 남은 결정 사항

1. **SensorChannel을 타입으로 둘지 인스턴스로 둘지.** 타입(4개)이면 단순하지만 설비별 센서 상태를 표현 못 한다. 인스턴스(400개)면 표현력이 늘지만 대부분 빈 객체가 된다. → 우선 타입 4개로 시작 권장.

2. **TelemetryWindow 영속화 여부.** 조회 시 생성이 기본이지만, 위험 사건이 발생한 창은 저장해야 나중에 재구성이 가능하다. → `RiskEvent`에 연결된 창만 영속화.

3. **경고-계통 연관 강도.** 지금은 표시명으로만 대응시키고 있다. 실제 전환율 데이터로 링크 속성 `association_strength`를 계산할 수 있다. → 2차 작업.

4. **2014년 정비 기록 취급.** `in_training_scope` 플래그로 구분하되, 경과일 계산에는 포함하는 기존 결정을 유지한다.
