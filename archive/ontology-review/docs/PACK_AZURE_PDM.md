# Azure PdM 도메인 매핑 팩

작성일: 2026-08-04
목적: `ONTOLOGY_CORE.md`의 모든 객체·속성을 Azure PdM 데이터셋에 매핑한다.
코어 문서: [`ONTOLOGY_CORE.md`](./ONTOLOGY_CORE.md)
관련 문서: [`ONTOLOGY_DESIGN.md`](./ONTOLOGY_DESIGN.md), [`EVIDENCE_PACKAGE.md`](./EVIDENCE_PACKAGE.md)

---

## 매핑 분류 기호

| 기호 | 뜻 |
|---|---|
| **매핑됨** | Azure PdM 원본 필드 또는 계산 코드에 대응 |
| **해당 없음** | 이 도메인에 존재하지 않음. nullable 처리 |

> **의미가 다름** 분류(ClassSubsystemProfile ↔ AI4I 이슈)는 코어 문서에서 이미 수정되었으므로 이 팩 문서에서 별도 언급하지 않는다.

---

## 1. 데이터셋 개요

Azure PdM 온톨로지의 원본은 아래 5개 CSV 파일이다.

| 파일 | 행 수 | 기간 | 비고 |
|---|---|---|---|
| `PdM_telemetry.csv` | 876,100 | 2015-01-01 ~ 2016-01-01 | 100대 × 시간당 1행 |
| `PdM_errors.csv` | 3,919 | 2015 | errorID: error1~error5 |
| `PdM_failures.csv` | 761 | 2015 | comp1~comp4 |
| `PdM_maint.csv` | 3,286 | 2014-06 ~ 2016-01 | 2014년 400건 포함 |
| `PdM_machines.csv` | 100 | — | model1~model4, age |

---

## 2. 어휘 대응

코어 이름과 Azure PdM 원본 이름의 대조표. 팩 문서 전체에서 코어 이름을 우선 사용하며, Azure PdM 원본 식별자는 괄호 안에 명시한다.

| 코어 이름 | Azure PdM 원본 이름 | Azure PdM 식별자/필드 |
|---|---|---|
| Asset | Machine / 설비 | `machineID` (1~100) |
| AssetClass | MachineModel / 설비 모델 | `model1`~`model4` |
| Subsystem | Component / 계통 | `comp1`~`comp4` |
| SubsystemClass | ComponentClass / 계통 타입 | `comp1`~`comp4` |
| ClassSubsystemProfile | ModelComponentProfile / 모델 × 계통 교차 | `{model}-{comp}` |
| ClassFailureModeProfile | — | **해당 없음** |
| ConsumableDemandProfile | PartDemandProfile / 부품 수요 | `comp1`~`comp4` |
| SensorChannel | SensorChannel / 센서 채널 | `volt`, `rotate`, `pressure`, `vibration` |
| PeerGroup | PeerGroup / 동종 집단 | `{model}-{age_min}-{age_max}` |
| PopulationSnapshot | FleetSnapshot / 함대 상태 | `fleet@{timestamp}` |
| ObservationWindow | TelemetryWindow / 관측 창 | `{machineID}@{window_end}` |
| AlertType | ErrorType / 경보 유형 | `error1`~`error5` |
| AlertEvent | ErrorEvent / 경보 발생 | — |
| Evidence | EvidencePackage / 근거 패키지 | `generate_evidence_package()` 출력 |
| Hypothesis | ComponentHypothesis / 계통 가설 | — |
| BaselinePolicy | BaselineProfile / 기준선 | `baseline_constants.json` |
| FailureMode | — | **해당 없음** |

---

## 3. SamplingPolicy — Azure PdM 파라미터 값

`SamplingPolicy` 객체에 기재되는 이 도메인의 실측·설정 값이다. 모든 값은 코드 상수 또는 데이터 분석으로 확인된 값이다.

| 파라미터 | 값 | 출처 | 설명 |
|---|---|---|---|
| `min_peers` | 5 | 신규 | 동종 비교에 필요한 최소 집단 크기. `PeerGroup.sufficient_peers` 판정 기준 |
| `min_rows` | 12 | 계산 | `evidence_package.py` `MIN_ROWS = 12`. `ObservationWindow.sufficient_data` 판정 기준 |
| `window_hours` | 24 | 계산 | `evidence_package.py` `WINDOW_HOURS = 24`. 관측 창 시간 범위 |
| `age_range` | 3 | 계산 | `evidence_package.py` `_build_peer_comparison()` 내 `age ± 3`. PeerGroup 연식 범위 |
| `class_equivalence_sigma_factor` | 0.05 | 신규 | 모델 간 채널 평균 차이가 채널 표준편차의 5% 이하 = `same_equipment_class: true` |
| `miss_target_pct` | 5 | 계산 | `thresholds.json` `miss_target_pct`. ThresholdPolicy 선택 허용 미탐 비율 |
| `reactive_window_hours` | 24 | 계산 | `evidence_package.py` `_build_maintenance_context()` 내 `Timedelta(hours=24)`. 사후 정비 판정 창 |

---

## 4. ThresholdPolicy — Azure PdM 추가 파라미터

코어 `ThresholdPolicy`에 도메인별로 추가되는 파라미터. `thresholds.json` 및 `report_generator.py`에서 확인한 값이다.

| 파라미터 | 값 | 출처 | 설명 |
|---|---|---|---|
| `min_hypothesis_z` | 2.0 | 계산 | `evidence_package.py` `Z_THRESHOLD`. 가설 후보 최소 z-점수 (하위 호환 상수. 실제 부품별 임계는 `thresholds.json`) |
| `grade_alarm_excess_ratio` | 1.0 | 계산 | `report_generator.py` `GRADE_ALARM = 1.0`. `excess_ratio ≥ 1.0` 시 알람 등급 |
| `grade_watch_excess_ratio` | 0.8 | 계산 | `report_generator.py` `GRADE_WATCH = 0.8`. `0.8 ≤ excess_ratio < 1.0` 시 관찰 등급 |
| `trend_change_threshold_pct` | 20 | 계산 | `manager_app.py` `TREND_CHANGE_THRESHOLD_PCT = 20`. 최근 24시간 이상 수준이 직전 24시간 대비 20% 이상 변화 시 추이 라벨 변경 |

> **부품별 z-점수 임계값** (`thresholds.json` 기준, fallback 값):
> comp1: 3.75 / comp2: 4.00 / comp3: 5.00 / comp4: 4.25
> 각 부품의 `excess_ratio = effective_z / comp_threshold`로 계산된다.

---

## 5. 객체별 매핑 테이블

코어의 모든 객체를 순서대로 열거한다.

---

### 5.1 Asset (← Machine)

**Azure PdM 대응:** `PdM_machines.csv`의 각 행. machineID 1~100, 총 100개 인스턴스.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값·비고 |
|---|---|---|---|
| `asset_id` | 원본 | `PdM_machines.machineID` | 정수 1~100. 문자열 변환 시 `"machine-{machineID}"` |
| `asset_class_code` | 원본 | `PdM_machines.model` | `model1`~`model4` |
| `age` | 원본 | `PdM_machines.age` | 연식 범위: 0~20년 |
| `failure_count` | 신규 | `PdM_failures`에서 `machineID` 기준 집계 | 최소 2 / 중앙값 7 / 최대 19. **고장 0건 자산 2대** |
| `preventive_count` | 계산 | `compute_equipment_maint_counts().by_machine[id].preventive` | — |
| `reactive_count` | 계산 | `compute_equipment_maint_counts().by_machine[id].reactive` | — |
| `reactive_ratio` | 신규 | `reactive / (preventive + reactive)` | 정비 품질 지표 |
| `population_rank_reactive` | 계산 | 100대 중 사후 교체 건수 내림차순 순위 | `manager_app.py` 화면에서 계산 |
| `mtbf_days` | 신규 | 고장 간 평균 시간. `failure_count = 0`이면 null | **고장 0건 자산 2대는 null. 고장 2건인 자산은 간격 표본 1개뿐이므로 신뢰도 표기 필요** |
| `time_since_last_failure_days` | 신규 | 마지막 고장 이후 경과일. 고장 이력 없으면 null | — |
| `criticality` | 합성 | 원본에 없음. 별도 생성 필요 | null |
| `downtime_cost_per_hour` | 합성 | 원본에 없음. 별도 생성 필요 | null |
| `location` | 합성 | 원본에 없음. 별도 생성 필요 | null |
| `assigned_engineer` | 합성 | 원본에 없음. 별도 생성 필요 | null |

---

### 5.2 AssetClass (← MachineModel)

**Azure PdM 대응:** `model1`~`model4`, 총 4개 인스턴스.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값 |
|---|---|---|---|
| `class_code` | 원본 | `PdM_machines.model` | `model1`, `model2`, `model3`, `model4` |
| `asset_count` | 신규 | 해당 모델 소속 자산 수 집계 | model1: 16 / model2: 17 / model3: 35 / model4: 32 |
| `failure_rate_per_asset` | 신규 | 해당 모델 자산당 평균 고장 건수 | model1: 11.8 / model2: 9.9 / model3: 6.3 / model4: 5.7 |
| `mean_age` | 신규 | 소속 자산 평균 연식 | 연식 범위 0~20년 |
| `same_equipment_class` | 신규 | 모든 AssetClass 쌍의 채널별 평균 차이가 `class_equivalence_sigma_factor × 채널 표준편차` 이하 여부 | **true** (근거: 9절 참조) |

---

### 5.3 Subsystem (← Component)

**Azure PdM 대응:** `{machineID}-{comp_code}` 조합. 100대 × 4계통 = **400개 인스턴스**. 전 조합에 데이터가 존재함이 검증됨.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값·비고 |
|---|---|---|---|
| `subsystem_id` | 신규 | `{machineID}-{comp_code}`. 예: `1-comp1` | — |
| `subsystem_class_code` | 원본 | `PdM_maint.comp`, `PdM_failures.failure` | `comp1`~`comp4` |
| `display_name` | 계산 | `display_names.COMP_DISPLAY[comp_code]` | 구동 계통(comp1) / 회전 계통(comp2) / 가압 계통(comp3) / 베어링 계통(comp4) |
| `name_is_inferred` | 계산 | `display_names.py` — 원본에 물리적 정체 없음 | **항상 true**. `display_names.py` 헤더에 명시됨 |
| `last_replacement_at` | 계산 | `maintenance_context.{comp}.last_replacement` | — |
| `days_since_replacement` | 계산 | `maintenance_context.{comp}.days_elapsed` | — |
| `last_replacement_type` | 계산 | `maintenance_context.{comp}.type` | `preventive` / `reactive` |
| `failure_within_window_before_maint` | 계산 | `maintenance_context.{comp}.basis.failure_within_24h_before_maint` | reactive 판정 근거 |
| `replacement_count_preventive` | 계산 | `compute_equipment_maint_counts().by_machine_comp[(id, comp)].preventive` | — |
| `replacement_count_reactive` | 계산 | `compute_equipment_maint_counts().by_machine_comp[(id, comp)].reactive` | — |
| `median_interval_days` | 신규 | 해당 자산 × 계통 조합의 교체 간격 중앙값 | `SubsystemClass.median_interval_days` 참조 (comp2만 30일) |
| `life_ratio` | 신규 | `days_since_replacement / median_interval_days` | 전 400개 인스턴스에서 계산 가능 (검증 완료) |
| `failure_count` | 신규 | 해당 계통 고장 건수 집계 | — |
| `mtbf_days` | 신규 | 계통 단위 고장 간 평균 시간 | — |

> `name_is_inferred: true`이면 화면과 보고서에 추정 표기를 한다. `display_names.py`가 이 한계를 소스에 명시하고 있다.

---

### 5.4 SubsystemClass (← ComponentClass)

**Azure PdM 대응:** `comp1`~`comp4`, 총 4개 인스턴스.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값 |
|---|---|---|---|
| `subsystem_class_code` | 원본 | `PdM_maint.comp` / `PdM_failures.failure` | `comp1`, `comp2`, `comp3`, `comp4` |
| `display_name` | 계산 | `display_names.COMP_SHORT[comp_code]` | 구동 계통 / 회전 계통 / 가압 계통 / 베어링 계통 |
| `name_is_inferred` | 계산 | `display_names.py` — 원본에 물리적 정체 없음 | **항상 true** |
| `median_interval_days` | 신규 | 계통 코드별 교체 간격 중앙값 | **comp1: 45일 / comp2: 30일 / comp3: 45일 / comp4: 45일** (각 표본 700건 이상) |
| `total_failures` | 신규 | 계통 단위 누적 고장 건수 | **comp1: 192 / comp2: 259 / comp3: 131 / comp4: 179** |
| `hypothesis_hit_rate` | 신규 | `Hypothesis.confirmed_by_failure` 집계. 사후 대조로 산출 | 현재 미산출 (향후 집계 가능) |

> 회전 계통(comp2)의 교체 간격이 30일로 나머지(45일)의 3분의 2에 불과하다. 전역 중앙값 사용 시 이 차이가 묻히므로 계통별 중앙값이 `life_ratio` 계산에 필수다.

---

### 5.5 FailureMode — 해당 없음

Azure PdM 데이터셋에는 FailureMode가 존재하지 않는다. `PdM_failures.csv`의 `failure` 컬럼은 Subsystem(계통)을 직접 가리키며, 고장 양태(failure mode)는 기록되지 않는다.

- 모든 `FailureMode` 속성: **해당 없음**
- `FailureEvent.failure_mode_ref`: **null** (8절 참조)
- `ClassFailureModeProfile`: **해당 없음** (아래 5.7절 참조)

---

### 5.6 ClassSubsystemProfile (← ModelComponentProfile)

**Azure PdM 대응:** AssetClass(model1~4) × SubsystemClass(comp1~4) 교차. **16개 인스턴스**.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값 |
|---|---|---|---|
| `profile_id` | 신규 | `{model_code}-{comp_code}`. 예: `model1-comp3` | — |
| `failures_per_asset` | 신규 | 해당 모델 자산당 해당 계통 고장 건수 | 아래 실측 교차표 참조 |
| `median_interval_days` | 신규 | 해당 교차의 교체 간격 중앙값 | — |
| `asset_count` | 신규 | 집계에 포함된 자산 수 | — |

**실측 failures_per_asset (대당 고장 건수):**

| | comp1 | comp2 | **comp3** | comp4 |
|---|---|---|---|---|
| model1 | 2.1 | 2.9 | **4.2** | 2.6 |
| model2 | 1.8 | 2.4 | **3.7** | 2.0 |
| model3 | 1.9 | 2.5 | **0.0** | 1.8 |
| model4 | 1.9 | 2.6 | **0.0** | 1.2 |

> 가압 계통(comp3) 고장은 model1·model2에서만 발생한다. model3·model4는 1년간 단 한 건도 없다. 모델별 총 고장률 차이(model1 11.8 vs model4 5.7)의 대부분이 이 교차에서 기인한다.

---

### 5.7 ClassFailureModeProfile — 해당 없음

Azure PdM 데이터셋에는 FailureMode가 없으므로 이 객체는 사용하지 않는다. 모든 속성: **해당 없음**.

---

### 5.8 ConsumableDemandProfile (← PartDemandProfile)

**Azure PdM 대응:** 계통 클래스별 소모품 수요 통계. 4개 인스턴스.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값 |
|---|---|---|---|
| `subsystem_class_code` | 원본 | `PdM_maint.comp` | `comp1`~`comp4` |
| `monthly_mean` | 신규 | 월별 교체 건수 평균 | **comp1: 58.5 / comp2: 63.4 / comp3: 58.8 / comp4: 59.2** |
| `monthly_median` | 신규 | 월별 교체 건수 중앙값 | — |
| `monthly_peak` | 신규 | 월별 교체 건수 최대 | **comp2: 76 / comp3: 75** |
| `preventive_share` | 신규 | 전체 교체 중 예방 교체 비중 | — |

> 월 수요가 계통당 58~63건으로 안정적이며 최대치가 평균의 1.2배 수준이다. 재고 수준 판단에 직접 사용 가능한 실측 분포다.

---

### 5.9 SensorChannel

**Azure PdM 대응:** 4개 채널 타입. 인스턴스가 아닌 타입으로 관리한다.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값·비고 |
|---|---|---|---|
| `channel_code` | 원본 | `PdM_telemetry.csv` 컬럼명 | `volt`, `rotate`, `pressure`, `vibration` |
| `display_name` | 계산 | `report_generator.py` `SENSOR_KO` | 전압 / 회전수 / 압력 / 진동 |
| `unit` | — | **원본에 단위 없음** | **null**. 물리 단위 미표기 원칙 유지 |
| `direction` | 계산 | `evidence_package.py` `SENSOR_COMP_MAP[sensor][1]` | 채널별 이상 방향 (7절 참조) |
| `associated_subsystem_class_code` | 계산 | `SENSOR_COMP_MAP`, `SENSOR_COMP` 매핑 | 7절 매핑표 참조 |

---

### 5.10 PeerGroup

**Azure PdM 대응:** 고유 (모델, 연식) 조합 기준. **최대 56개** 그룹.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값 |
|---|---|---|---|
| `group_id` | 신규 | `{model}-{age_min}-{age_max}`. 예: `model1-5-11` | — |
| `asset_class_code` | 원본 | `PdM_machines.model` | `model1`~`model4` |
| `age_min` / `age_max` | 계산 | `age - 3` / `age + 3`. `SamplingPolicy.age_range = 3` | — |
| `member_count` | 계산 | `peer_comparison.peer_count` | 아래 실측 분포 참조 |
| `sufficient_peers` | 신규 | `member_count >= SamplingPolicy.min_peers (= 5)` | 100대 중 16대가 min_peers 미달 |

**집단 크기 실측 분포:**

| 모델 | peer 최소 | 중앙 | 최대 |
|---|---|---|---|
| model1 | 3 | 9 | 10 |
| **model2** | **2** | **4** | 7 |
| model3 | 4 | 11 | 19 |
| model4 | 5 | 10 | 14 |

> model2는 동종 집단이 최소 2대뿐인 경우가 있다. `sufficient_peers: false`이면 `peer_percentile`은 표시하되 판단 근거로 사용하지 않는다.

---

### 5.11 PopulationSnapshot (← FleetSnapshot)

**Azure PdM 대응:** 신규. 조회 시각당 1개 생성. 현재 코드에서는 즉석 계산 후 버리는 상태이며 영속화가 필요하다.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값·비고 |
|---|---|---|---|
| `snapshot_id` | 신규 | `population@{timestamp}` | — |
| `evaluated_at` | 신규 | 평가 시각 | — |
| `asset_count` | 신규 | 100 (고정) | — |
| `alarm_count` | 계산 | `excess_ratio >= GRADE_ALARM(1.0)` 자산 수 | — |
| `watch_count` | 계산 | `0.8 ≤ excess_ratio < 1.0` 자산 수 | — |
| `insufficient_data_count` | 계산 | `ObservationWindow.sufficient_data = false` 자산 수 | — |
| `top_k_event_refs` | 계산 | `compute_topk_table()` 결과 상위 K건 | — |
| `baseline_version` | 신규 | 평가에 사용된 BaselinePolicy 버전 | lineage 링크 |
| `threshold_policy_version` | 신규 | 평가에 사용된 ThresholdPolicy 버전 | lineage 링크 |

---

### 5.12 MaintenanceCapacity

**Azure PdM 대응:** 합성. 원본 데이터에 인력 정보가 없으므로 별도 생성이 필요하다.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값·비고 |
|---|---|---|---|
| `capacity_id` | 합성 | — | 별도 생성 필요 |
| `period` | 합성 | — | 별도 생성 필요 |
| `available_technician_hours` | 합성 | 원본에 없음 | **null**. 합성 없이는 "누구부터" 판단에 답 불가 |
| `assigned_work_order_count` | 신규 | WorkOrder 집계 | WorkOrder가 영속화된 후 산출 가능 |

---

### 5.13 ObservationWindow (← TelemetryWindow)

**Azure PdM 대응:** `PdM_telemetry.csv` 876,100행에서 파생. 24시간 집계 창 단위로 생성. 조회 시 생성이 기본이며, RiskEvent에 연결된 창만 영속화 권장.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값·비고 |
|---|---|---|---|
| `window_id` | 신규 | `{machineID}@{window_end}` | — |
| `window_start` | 계산 | `timestamp - Timedelta(hours=24)` | — |
| `window_end` | 계산 | `timestamp` | — |
| `window_hours` | 계산 | `WINDOW_HOURS = 24` | 24 |
| `row_count` | 계산 | `sensor_evidence.window_rows` | — |
| `sufficient_data` | 계산 | `row_count >= MIN_ROWS (= 12)` | — |
| `reference_frame` | 계산 | `"global_baseline_all_machines_all_time"` | — |

**ChannelReading 하위 구조:**

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 |
|---|---|---|
| `channel_code` | 원본 | `volt`, `rotate`, `pressure`, `vibration` |
| `mean_value` | 계산 | `sensor_evidence.sensors.{s}.mean_24h` |
| `z_score` | 계산 | `sensor_evidence.sensors.{s}.z_score` |
| `excess_ratio` | 계산 | `\|effective_z\| / comp_threshold`. `report_generator.py` `_all_candidates()` |
| `peer_percentile` | 계산 | `peer_comparison.percentile_by_sensor.{s}.percentile` |

**시간 기반 속성 — 이 팩에서 산출됨:**

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 |
|---|---|---|
| `trend_label` | 계산 | `manager_app.py` `_trend_label()`. `상승 중` / `하락 중` / `보합` / `판단 불가` |
| `trend_change_pct` | 신규 | `(recent_mean - prev_mean) / prev_mean × 100`. 현재 라벨만 반환 중이며 수치 노출 필요 |

---

### 5.14 AlertType (← ErrorType)

**Azure PdM 대응:** `error1`~`error5`, 총 5개 인스턴스.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값 |
|---|---|---|---|
| `alert_code` | 원본 | `PdM_errors.errorID` | `error1`~`error5` |
| `display_name` | 계산 | `display_names.ERROR_DISPLAY[error_code]` | 구동 계통 경고(error1) 등 |
| `name_is_inferred` | 계산 | `display_names.py` — 원본에 경보 정체 없음 | **항상 true** |
| `total_occurrences` | 계산 | 전체 발생 건수 집계 | 예: error5: 356건 (샘플) |
| `converted_to_failure_rate` | 계산 | `_compute_error_conversion_rates()`. 발생 후 24시간 내 동일 자산 FailureEvent 비율 | 예: error5: 0.5028 (50.3%) |

> 경보-서브시스템 연관은 1:1이 아니다. error2와 error3 둘 다 회전 계통(comp2)에 연관된다. 링크 카디널리티는 many-to-many이며 `alert_type_associated_with_subsystem` 링크로 표현한다.

---

### 5.15 AlertEvent (← ErrorEvent)

**Azure PdM 대응:** `PdM_errors.csv`. **3,919건**.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 |
|---|---|---|
| `event_id` | 신규 | 행 기반 생성 식별자 |
| `occurred_at` | 원본 | `PdM_errors.datetime` |
| `alert_code` | 원본 | `PdM_errors.errorID` |

---

### 5.16 FailureEvent

**Azure PdM 대응:** `PdM_failures.csv`. **761건**.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 비고 |
|---|---|---|---|
| `event_id` | 신규 | 행 기반 생성 식별자 | — |
| `occurred_at` | 원본 | `PdM_failures.datetime` | — |
| `subsystem_ref` | 원본 | `PdM_failures.failure` → Subsystem 식별자 변환 | `comp_code → "{machineID}-{comp_code}"` |
| `failure_mode_ref` | — | **해당 없음** | **null**. FailureMode가 없음 |
| `mtbf_days` | 신규 | 해당 자산의 고장 간 평균 시간. 고장 이력 0건이면 null | 고장 0건 자산 2대는 null. 고장 2건 자산은 간격 표본 1개이므로 신뢰도 표기 필요 |

> **제약 충족 확인:** 코어는 `subsystem_ref`와 `failure_mode_ref`가 동시에 null일 수 없다고 정의한다. Azure PdM에서는 `subsystem_ref`가 항상 채워지므로 (`PdM_failures.failure`에 comp1~4 중 하나가 반드시 있음) 제약이 충족된다. 8절 상세 참조.

---

### 5.17 MaintenanceRecord

**Azure PdM 대응:** `PdM_maint.csv`. **3,286건** (2014년 400건 포함).

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 비고 |
|---|---|---|---|
| `record_id` | 신규 | 행 기반 생성 식별자 | — |
| `performed_at` | 원본 | `PdM_maint.datetime` | — |
| `subsystem_class_code` | 원본 | `PdM_maint.comp` | `comp1`~`comp4` |
| `type` | 계산 | `_build_maintenance_context()` — 교체 전 24시간 내 동일 자산·계통 고장 여부 | `preventive` / `reactive` |
| `failure_within_window_before` | 계산 | `basis.failure_within_24h_before_maint` | reactive 판정 근거 |
| `interval_since_previous_days` | 신규 | 이전 교체와의 간격 | 최초 기록이면 null |
| `in_training_scope` | 신규 | 2014년 400건: `false` (type 판정 학습 제외). 경과일 계산에는 포함 | 기존 결정 유지 |

**실측 (결정 004 measured 재현)**

```
preventive 2,543 / reactive 743                    합계 3,286
유형별 다음 교체까지 중앙값   예방 45일 / 사후 30일
maintenance_followed_failure 링크  743건 (reactive 건수와 일치)
```

**경계 조건** — 관측 창은 `(performed_at − 24h, performed_at]`. 끝을 포함한다.

`PdM_*.csv`의 시각은 **시간 단위로 반올림**돼 있어(데이터 한계 4항목) 고장과 그에 따른 교체가 같은 시각으로 기록된다. 끝 경계를 배제하면 `reactive`가 **0건**이 되고 결정 004의 measured 값과 어긋난다.

판정 규칙은 **`maintenance_rules.py`가 단일 출처**로 보유하며, 아래 호출부가 모두 이 모듈을 쓴다.

| 파일 | 호출부 |
|---|---|
| `evidence_package.py` | `_build_maintenance_context()` |
| `manager_app.py` | `compute_maint_interval_stats()` · `compute_equipment_maint_counts()` |
| `build_ontology.py` | `classify_maintenance()` · `maintenance_followed_failure` 링크 생성 |

`test_maintenance_rules.py`가 경계 조건과 결정 004 재현을 잠근다. 옛 경계로 되돌리면 사후 정비가 0건이 되는 것까지 회귀 테스트로 고정돼 있다.

> 사후 정비 743건은 매니저 화면 ④번 질문("미루면 뭐가 나빠지는가")의 근거다. **사후 교체 후 다음 교체까지 30일, 예방 교체 후 45일**이라는 대비가 "지금 갈까 미룰까"에 답한다. `reactive`가 0이면 이 비교가 사라진다.

---

### 5.18 RiskEvent

**Azure PdM 대응:** 신규. 현재 시스템에서는 등급을 즉석 계산하고 버린다. 행동·감사 추적을 위해 영속화가 필요하다.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 실측값 |
|---|---|---|---|
| `event_id` | 신규 | 판정 시 생성 | — |
| `detected_at` | 신규 | 판정 시각 | — |
| `grade` | 계산 | `report_generator.py` `_grade(excess_ratio)` | `alarm` / `watch` / `normal` |
| `grade_display` | 계산 | `manager_app.py` `GRADE_LABEL` | 즉시 교체 필요 / 관찰 필요 / 정상 |
| `max_excess_ratio` | 계산 | `max(excess_ratio)` across 4 channels | 1.0 이상: 알람 / 0.8~1.0: 관찰 |
| `primary_subsystem_ref` | 계산 | `max_excess_ratio` 채널의 연관 Subsystem 식별자 | — |
| `grade_failure_rate_pct` | 계산 | `manager_app.py` `GRADE_FAILURE_RATE` | **알람: 24.0% / 관찰: 15.7% / 정상: 0.01%** |
| `status` | 신규 | `미확인` / `확인됨` / `조치중` / `종결` | — |
| `acknowledged_by` | 신규 | null (영속화 전까지) | — |
| `acknowledged_at` | 신규 | null (영속화 전까지) | — |

---

### 5.19 Evidence (← EvidencePackage)

**Azure PdM 대응:** `evidence_package.py` `generate_evidence_package()` 출력.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 |
|---|---|---|
| `evidence_id` | 신규 | 생성 시각 기반 식별자 |
| `generated_at` | 신규 | 생성 시각 |
| `window_ref` | 계산 | `{machineID}@{timestamp}` |
| `no_prior_alert` | 계산 | `status_flags.no_prior_error` |
| `multiple_candidates` | 계산 | `status_flags.multiple_candidates` |
| `insufficient_data` | 계산 | `status_flags.insufficient_data` |
| `baseline_policy_ref` | 신규 | 사용된 BaselinePolicy 버전 식별자 — lineage 링크 |
| `threshold_policy_ref` | 신규 | 사용된 ThresholdPolicy 버전 식별자 — lineage 링크 |

---

### 5.20 Hypothesis (← ComponentHypothesis)

**Azure PdM 대응:** `evidence_package.py` `_build_hypotheses()` 출력. z-점수 임계(`thresholds.json` 부품별 값) 초과 시 생성.

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 |
|---|---|---|
| `hypothesis_id` | 신규 | 생성 시 부여 |
| `channel_code` | 계산 | `component_hypotheses[].associated_sensor` |
| `z_score` | 계산 | `component_hypotheses[].sensor_z_score` |
| `threshold_applied` | 계산 | `component_hypotheses[].z_threshold`. 부품별 상이 (comp1: 3.75 / comp2: 4.00 / comp3: 5.00 / comp4: 4.25) |
| `direction` | 계산 | `component_hypotheses[].direction` |
| `association` | 계산 | `"sensor_anomaly_associated_with_{comp}_candidate"` |
| `subsystem_ref` | 계산 | `component_hypotheses[].component` → Subsystem 식별자 변환 |
| `failure_mode_ref` | — | **해당 없음**. FailureMode가 없으므로 null |
| `confirmed_by_failure` | 신규 | 이후 FailureEvent로 확인 시 사후 업데이트. 미확인이면 null |

---

### 5.21 BaselinePolicy (← BaselineProfile)

**Azure PdM 대응:** `baseline_constants.json` (`compute_global_baseline()` 출력).

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 | 비고 |
|---|---|---|---|
| `version` | 신규 | `baseline_constants.json` 기반 버전 식별자 | — |
| `computed_on` | 원본 | `baseline_constants.json` 산출일 | — |
| `training_cutoff` | 원본 | `z_baseline.TRAIN_CUTOFF` | 학습 데이터 마감 시각 |
| `window_hours` | 계산 | `WINDOW_HOURS = 24` | — |
| `min_periods` | 원본 | `baseline_constants.json` `min_periods` | — |
| `channel_stats` | 원본 | `compute_global_baseline()` → 채널별 `mean` / `std` / `n_samples` | 876,100행 기준 전 장비·전 기간 집계 |

> 기준선의 `reference_frame`은 `"global_baseline_all_machines_all_time"` (또는 현재 코드 기준 `"rolling_mean_std_training_only_lt_2015-10-01"`). 100대 통합 기준선이 편향 없이 적용 가능한 근거는 9절 참조.

---

### 5.22 ThresholdPolicy

**Azure PdM 대응:** `thresholds.json` (`z_baseline.load_thresholds()` 로드).

| 속성 (코어) | 출처 | Azure PdM 필드 / 계산 방법 |
|---|---|---|
| `version` | 신규 | `thresholds.json` 기반 버전 식별자 |
| `computed_on` | 원본 | `thresholds.json` 산출일 |
| `training_cutoff` | 원본 | `thresholds.json` 학습 데이터 마감 시각 |
| `selection_rule` | 원본 | `thresholds.json` `selection_rule` |
| `miss_target_pct` | 원본 | `thresholds.json` `miss_target_pct = 5` |
| `entries` | 원본 | 계통별 `SingleCondition` (7절 참조) |

**계통별 ThresholdEntry (SingleCondition):**

| 계통 (target_ref) | channel_ref | operator | threshold | direction |
|---|---|---|---|---|
| comp1 | volt | `>=` | 3.75 | `both` |
| comp2 | rotate | `<=` | -4.00 | `low` |
| comp3 | pressure | `>=` | 5.00 | `both` |
| comp4 | vibration | `>=` | 4.25 | `both` |

> comp2(rotate)는 `low` 방향만 유효. 상승 이상은 다른 현상으로 간주. `effective_z = max(-z, 0.0)`.

---

### 5.23 SamplingPolicy

3절에서 별도 상세 기술.

---

### 5.24 WorkOrder

**Azure PdM 대응:** 신규 (합성 필요). 현재 시스템에 없으며 행동 계층 구현 시 생성된다.

| 속성 (코어) | 출처 | 비고 |
|---|---|---|
| `work_order_id` | 합성 | — |
| `status` | 합성 | `요청됨` / `배정됨` / `진행중` / `완료` / `보류` |
| `assignee` | 합성 | — |
| `due_at` | 합성 | — |
| `work_type` | 합성 | `점검` / `교체` / `정밀진단` |
| `created_from_event_id` | 신규 | RiskEvent 식별자 참조 |

---

### 5.25 OperationalDecision

**Azure PdM 대응:** 신규 (합성 필요). 현재 시스템에 없으며 행동 계층 구현 시 생성된다.

| 속성 (코어) | 출처 | 비고 |
|---|---|---|
| `decision_id` | 합성 | — |
| `decision` | 합성 | `계속 운전` / `점검 요청` / `정지 검토` |
| `rationale` | 합성 | 판단 근거 메모 |
| `decided_by` | 합성 | — |
| `decided_at` | 합성 | — |
| `evidence_ref` | 신규 | Evidence 식별자 참조 |

---

## 6. SensorChannel ↔ SubsystemClass 매핑표

Azure PdM 도메인에서의 채널-계통 대응. `evidence_package.py` `SENSOR_COMP_MAP`에 구현되어 있다.

| 채널 코드 (원본) | 채널 코드 (코어) | 연관 서브시스템 클래스 | 이상 방향 | 비고 |
|---|---|---|---|---|
| `volt` | `volt` | `comp1` (구동 계통) | `both` (양방향) | 전압 상승·하락 모두 유효 |
| `rotate` | `rotate` | `comp2` (회전 계통) | `low` (하락만) | 하락(z ≤ -threshold)만 유효. 상승은 다른 현상으로 간주 |
| `pressure` | `pressure` | `comp3` (가압 계통) | `both` (양방향) | — |
| `vibration` | `vibration` | `comp4` (베어링 계통) | `both` (양방향) | — |

> `rotate`의 방향 제약은 물리 의미("회전수 하락 → comp2 연관")를 반영한다. `evidence_package.py` 설계 결정 사항에 명시됨.

---

## 7. FailureEvent 처리

Azure PdM에서 고장은 `PdM_failures.csv`의 `failure` 컬럼에 `comp1`~`comp4` 중 하나로 기록된다. 이 값은 고장 모드(FailureMode)가 아니라 Subsystem(계통)을 직접 가리킨다.

**매핑 방식:**

| 코어 속성 | 처리 방식 | Azure PdM 값 |
|---|---|---|
| `subsystem_ref` | **매핑됨** | `PdM_failures.failure` (comp_code) → `"{machineID}-{comp_code}"` |
| `failure_mode_ref` | **해당 없음** | **항상 null** |

**제약 충족:**

코어는 `subsystem_ref`와 `failure_mode_ref`가 동시에 null일 수 없다고 정의한다.

Azure PdM에서는 `PdM_failures.failure` 컬럼에 반드시 `comp1`~`comp4` 중 하나가 존재하므로 `subsystem_ref`가 항상 채워진다. `failure_mode_ref`가 null이더라도 `subsystem_ref`가 있으므로 제약이 충족된다.

```
subsystem_ref  : 항상 있음 (comp1~comp4 → Subsystem 식별자)
failure_mode_ref: 항상 null (FailureMode 없음)
동시 null 불가 제약: ✓ 충족
```

---

## 8. AssetClass.same_equipment_class 실증

네 모델의 센서 분포 실측값. 모델 간 평균 차이가 채널 표준편차의 5% 이하임을 확인하여 `same_equipment_class: true`의 근거로 삼는다.

**모델별 센서 분포 (평균 ± 표준편차):**

| | volt | rotate | pressure | vibration |
|---|---|---|---|---|
| model1 | 170.64 ± 15.63 | 445.92 ± 52.80 | 101.36 ± 11.45 | 40.44 ± 5.43 |
| model2 | 170.56 ± 15.49 | 446.13 ± 52.60 | 101.27 ± 11.48 | 40.43 ± 5.42 |
| model3 | 170.81 ± 15.50 | 446.74 ± 52.89 | 100.74 ± 10.85 | 40.37 ± 5.36 |
| model4 | 170.76 ± 15.48 | 446.17 ± 52.57 | 100.59 ± 10.90 | 40.35 ± 5.37 |

**검증:**

`SamplingPolicy.class_equivalence_sigma_factor = 0.05` 기준으로, 허용 임계 = `0.05 × 채널 표준편차`.

| 채널 | 채널 표준편차 (전체) | 허용 임계 (5%) | 모델 간 최대 평균 차이 | 기준 충족 |
|---|---|---|---|---|
| volt | ~15.5 | ~0.78 | 0.25 (model1 vs model3) | ✓ |
| rotate | ~52.7 | ~2.64 | 0.82 (model1 vs model3) | ✓ |
| pressure | ~11.2 | ~0.56 | 0.77 (model1 vs model4) | ✓ |
| vibration | ~5.4 | ~0.27 | 0.09 (model1 vs model4) | ✓ |

모든 채널에서 모델 간 평균 차이가 허용 임계 이하이므로 `same_equipment_class: true`.

**도출되는 결론:**

1. **전역 기준선이 타당하다.** `compute_global_baseline()`이 100대를 통합해 기준선을 산출해도 모델에 편향된 z-점수가 나오지 않는다. `BaselinePolicy.reference_frame`이 `global_baseline_all_machines_all_time`인 근거가 여기 있다.

2. **모델 간 고장률 차이의 성격이 분명해진다.** 운전 조건(센서 분포)이 비슷한데 결과(고장 건수)가 갈린다. model1이 model4의 두 배 이상 고장나는 것은 부하 차이가 아니라 설비 특성 차이로 읽을 근거가 된다.

> **주의:** 압력 평균이 model1·2(101.3)이 model3·4(100.6)보다 약간 높으며, 이 방향이 가압 계통(comp3) 고장 분포와 같다. 그러나 차이가 표준편차의 5% 이하이므로 관찰된 연관일 뿐이며 원인으로 기술하지 않는다.

---

## 9. 인스턴스 수 요약

코어 객체별 Azure PdM에서의 실제 인스턴스 수.

| 코어 객체 | Azure PdM 인스턴스 수 | 비고 |
|---|---|---|
| Asset | 100 | machineID 1~100 |
| AssetClass | 4 | model1~model4 |
| Subsystem | 400 | 100대 × 4계통 (전 조합 검증 완료) |
| SubsystemClass | 4 | comp1~comp4 |
| FailureMode | 0 | **해당 없음** |
| ClassSubsystemProfile | 16 | 4모델 × 4계통 |
| ClassFailureModeProfile | 0 | **해당 없음** |
| ConsumableDemandProfile | 4 | comp1~comp4 |
| SensorChannel | 4 | volt / rotate / pressure / vibration (타입 기준) |
| PeerGroup | 최대 56 | 고유 (모델, 연식) 조합 기준 |
| PopulationSnapshot | 조회 시각당 1 | 신규. 현재 미영속화 |
| MaintenanceCapacity | 기간당 1 | 합성. 원본에 인력 데이터 없음 |
| ObservationWindow | 조회 시 생성 | 876,100행 파생. RiskEvent 연결 창만 영속화 권장 |
| AlertType | 5 | error1~error5 |
| AlertEvent | 3,919 | `PdM_errors.csv` |
| FailureEvent | 761 | `PdM_failures.csv` |
| MaintenanceRecord | 3,286 | `PdM_maint.csv` (2014년 400건 포함) |
| RiskEvent | 판정 시 생성 | 신규. 현재 미영속화 |
| Evidence | 조회 시 생성 | `generate_evidence_package()` 출력 |
| Hypothesis | 조회 시 생성 | z 임계 초과 시 생성 |
| BaselinePolicy | 1 (현재) | `baseline_constants.json` |
| ThresholdPolicy | 1 (현재) | `thresholds.json` |
| WorkOrder | 0 | 합성 필요. 행동 계층 미구현 |
| OperationalDecision | 0 | 합성 필요. 행동 계층 미구현 |

> **정적 객체** (Asset, AssetClass, Subsystem, SubsystemClass, ConsumableDemandProfile, SensorChannel, PeerGroup, AlertType, AlertEvent, FailureEvent, MaintenanceRecord): 약 **8,500개**. SQLite에서 무리 없다.

---

## 10. 미결 결정 사항

기존 `ONTOLOGY_DESIGN.md` 8장의 Azure PdM 관련 항목.

1. **SensorChannel을 타입으로 둘지 인스턴스로 둘지.**
   타입(4개)이면 단순하지만 설비별 센서 상태를 표현하지 못한다. 인스턴스(400개)이면 표현력이 늘지만 대부분 빈 객체가 된다.
   → 우선 타입 4개로 시작 권장.

2. **ObservationWindow 영속화 여부.**
   조회 시 생성이 기본이지만, RiskEvent가 발생한 창은 저장해야 나중에 재구성이 가능하다.
   → RiskEvent에 연결된 창만 영속화.

3. **AlertType ↔ SubsystemClass 연관 강도.**
   현재는 표시명으로만 대응시키고 있다. 전환율 데이터(`converted_to_failure_rate`)로 링크 속성 `association_strength`를 계산할 수 있다.
   → 2차 작업.

4. **2014년 MaintenanceRecord 취급.**
   `in_training_scope: false`로 구분하되, 경과일 계산에는 포함하는 기존 결정을 유지한다.

5. **고장 0건 Asset 2대의 `mtbf_days` 신뢰도 표기.**
   `mtbf_days: null`로 처리하며 화면에 "고장 이력 없음" 표기. 고장 2건 자산은 간격 표본이 1개뿐이므로 낮은 신뢰도 표기가 필요하다.
   → 표시 기준 미정.

6. **WorkOrder 및 OperationalDecision 합성 방법.**
   행동 계층 구현 시 최소 `criticality`와 `downtime_cost_per_hour`를 합성해야 기대손실 비교가 성립한다. 인력 시간(`MaintenanceCapacity.available_technician_hours`)도 합성 대상이다.
   → 최소 구현 범위 미정.

7. **Hypothesis.confirmed_by_failure 집계 시점.**
   FailureEvent와 사후 대조하여 가설 적중률을 산출하는 로직이 현재 없다.
   → 구현 일정 미정.
