# 온톨로지 코어 설계

작성일: 2026-08-04
적용 범위: 도메인 중립 코어 — Azure PdM, AI4I, 기타 설비 도메인에 공통 적용
관련 문서: `ONTOLOGY_DESIGN.md` (Azure PdM 도메인 팩), `PROMPT_split_core_pack.md`

---

## 설계 원칙

1. **모든 속성은 출처가 있다.** 원본 데이터, 계산 코드, 또는 신규 정의 중 하나로 표기한다. 출처 표기는 팩 문서에서 수행하며, 코어 문서의 속성 테이블에는 출처 열을 두지 않는다.
2. **인과 표현 금지.** 연관·후보·가설만 쓰고 원인·확정은 쓰지 않는다. `associated`, `candidate`, `연관`, `후보`만 허용한다.
3. **판정 근거는 객체다.** 기준선과 임계값을 1급 객체로 두어 어떤 버전으로 판정했는지 되짚을 수 있게 한다.
4. **행동은 기록된다.** 시스템은 읽기 전용이 원칙이다. 액션 계층을 추가해 사람의 판단이 남게 한다.
5. **도메인 중립성.** 특정 도메인의 고유 식별자, 채널 수, 계통 수, 수치 상수를 코어에 고정하지 않는다. 개수와 임계값은 정책 객체 파라미터로 표현한다.

---

## 출처 표기 규칙

> 아래 표기는 **팩 문서에서 사용**한다. 코어 문서의 속성 정의에는 출처를 기술하지 않는다.

| 표기 | 뜻 |
|---|---|
| `원본` | 도메인 데이터셋에 직접 존재 |
| `계산` | 기존 코드에 이미 구현됨 |
| `신규` | 본 설계에서 추가 제안. 기존 데이터로 산출 가능 |
| `합성` | 데이터에 없음. 별도 생성 필요 |

---

## 설계 결함 보고 및 수정

### ClassSubsystemProfile 한계

**결함:** `ClassSubsystemProfile`은 AssetClass × SubsystemClass 교차 프로파일이다. Subsystem이 존재하지 않는 도메인(예: AI4I처럼 고장 모드만 기록되고 계통이 구분되지 않는 데이터셋)에는 이 객체가 의미를 가지지 못한다. 기존 `ModelComponentProfile` 설계가 이 한계를 내포하고 있었다.

**수정:** `ClassFailureModeProfile`을 코어에 추가한다. AssetClass × FailureMode 교차를 표현하며, Subsystem이 없는 도메인에서도 성립한다. 두 프로파일은 상호 배타적이지 않으며 같은 도메인이 둘 다 가질 수 있다.

---

# 1. 객체 타입

## 1.1 자산 계층

### Asset — 자산

인터페이스: `Monitorable`

물리적 정비 단위. 특정 AssetClass에 속하며, 하나 이상의 Subsystem을 포함할 수 있다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `asset_id` | string | 자산 고유 식별자 |
| `asset_class_code` | string | 소속 AssetClass 코드 |
| `age` | integer | 연식 (년) |
| `failure_count` | integer | 누적 고장 건수 |
| `preventive_count` | integer | 예방 정비 건수 |
| `reactive_count` | integer | 사후 정비 건수 |
| `reactive_ratio` | number | `reactive / (preventive + reactive)`. 정비 품질 지표 |
| `population_rank_reactive` | integer | 모집단 내 사후 교체 건수 순위 |
| `mtbf_days` | number \| null | 고장 간 평균 시간 (ISO 14224). 고장 0건이면 null |
| `time_since_last_failure_days` | number \| null | 마지막 고장 이후 경과일. 고장 이력 없으면 null |
| `criticality` | string \| null | 우선순위 등급. 원본에 없을 수 있음 |
| `downtime_cost_per_hour` | number \| null | 단위 시간당 비가동 비용. 기대손실 계산에 사용 |
| `location` | string \| null | 설치 위치 |
| `assigned_engineer` | string \| null | 담당 엔지니어 |

> `mtbf_days`는 고장 건수가 0이면 반드시 null이다. 고장 건수가 소수인 경우 신뢰도 표기를 팩 문서에 명시한다.

---

### AssetClass — 자산 클래스

자산의 종류. 동일 AssetClass에 속한 자산은 동일 기준선을 공유할 수 있는지 검토 대상이 된다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `class_code` | string | 클래스 고유 코드 |
| `asset_count` | integer | 소속 자산 수 |
| `failure_rate_per_asset` | number | 자산당 평균 고장 건수 |
| `mean_age` | number | 소속 자산 평균 연식 |
| `same_equipment_class` | boolean (계산 속성) | 아래 정의 참조 |

> **`same_equipment_class` — 계산 속성 정의**
>
> 이 값은 정적 boolean이 아니다. 모집단 내 복수의 AssetClass 간 각 채널의 평균 차이를 해당 채널 표준편차의 일정 배수(`SamplingPolicy.class_equivalence_sigma_factor`)와 비교하여 산출한다.
>
> 정의: 모든 AssetClass 쌍에 대해 각 채널 평균 차이가 `class_equivalence_sigma_factor × 채널 표준편차` 이하일 때 `true`.
>
> `true`이면 여러 AssetClass가 동일 기준선을 공유해도 편향이 없음을 의미한다. 팩 문서에서 실측 근거를 제시한다.

---

### Subsystem — 서브시스템

인터페이스: `Monitorable`, `Maintainable`

정비 가능 단위 (ISO 14224 maintainable item). Asset 내 논리적·물리적 구성 요소. 정비와 고장이 이 단위로 기록된다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `subsystem_id` | string | 서브시스템 고유 식별자. 예: `{asset_id}-{subsystem_class_code}` |
| `subsystem_class_code` | string | 소속 SubsystemClass 코드 |
| `display_name` | string \| null | 표시 이름 |
| `name_is_inferred` | boolean | 이름이 추정인 경우 true. 원본에 물리적 정체가 없는 경우 |
| `last_replacement_at` | datetime \| null | 마지막 교체 시각 |
| `days_since_replacement` | number \| null | 마지막 교체 이후 경과일 |
| `last_replacement_type` | string \| null | `preventive` / `reactive` |
| `failure_within_window_before_maint` | boolean \| null | reactive 판정 근거. 정비 직전 관측 창 내 고장 여부 |
| `replacement_count_preventive` | integer | 예방 교체 누계 |
| `replacement_count_reactive` | integer | 사후 교체 누계 |
| `median_interval_days` | number \| null | 교체 간격 중앙값 |
| `life_ratio` | number \| null | `days_since_replacement / median_interval_days`. 수명 소모 비율. null이면 중앙값 미산출 |
| `failure_count` | integer | 이 서브시스템 누적 고장 건수 |
| `mtbf_days` | number \| null | 서브시스템 단위 고장 간 평균 시간 |

> `life_ratio`는 `median_interval_days`가 산출된 서브시스템에서만 계산된다. 팩 문서에서 어느 서브시스템에 적용 가능한지 명시한다.
>
> `name_is_inferred: true`이면 화면과 보고서에 추정 표기를 한다.

---

### SubsystemClass — 서브시스템 클래스

Subsystem 인스턴스의 공통 속성을 집약한다. 계통 단위 통계가 여기 집중된다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `subsystem_class_code` | string | 클래스 고유 코드 |
| `display_name` | string \| null | 표시 이름 |
| `name_is_inferred` | boolean | 이름이 추정인 경우 true |
| `median_interval_days` | number \| null | 클래스 단위 교체 간격 중앙값 |
| `total_failures` | integer | 클래스 단위 누적 고장 건수 |
| `hypothesis_hit_rate` | number \| null | 이 클래스를 가리킨 가설의 적중률. `confirmed_by_failure` 집계값 |

---

### FailureMode — 고장 모드

고장 양태 (ISO 14224 failure mode). Subsystem과 독립적으로 정의된다. AI4I처럼 계통이 구분되지 않는 도메인에서는 FailureMode만 사용한다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `mode_code` | string | 고장 모드 고유 코드 |
| `display_name` | string | 표시 이름 |
| `description` | string \| null | 상세 설명 |

---

### ClassSubsystemProfile — 클래스 × 서브시스템 프로파일

AssetClass와 SubsystemClass의 교차 통계. **Subsystem이 존재하는 도메인에서만 사용한다.**

| 속성 | 타입 | 설명 |
|---|---|---|
| `profile_id` | string | `{asset_class_code}-{subsystem_class_code}` |
| `failures_per_asset` | number | 해당 클래스 자산당 해당 서브시스템 고장 건수 |
| `median_interval_days` | number \| null | 해당 교차의 교체 간격 중앙값 |
| `asset_count` | integer | 집계에 포함된 자산 수 |

> 이 객체는 Subsystem이 없는 도메인에서는 의미를 가지지 않는다. 해당 도메인은 `ClassFailureModeProfile`을 대신 사용한다.

---

### ClassFailureModeProfile — 클래스 × 고장 모드 프로파일

AssetClass와 FailureMode의 교차 통계. Subsystem 구분 없이 고장 모드만 기록되는 도메인(예: AI4I)에서 `ClassSubsystemProfile` 대신 사용한다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `profile_id` | string | `{asset_class_code}-{mode_code}` |
| `failure_rate` | number | 해당 클래스에서 해당 고장 모드 발생 비율 |
| `asset_count` | integer | 집계에 포함된 자산 수 |
| `associated_threshold` | string \| null | 연관 ThresholdPolicy 식별자. 클래스별로 다른 임계값을 가질 때 사용. nullable |

> `associated_threshold`는 클래스별로 임계값이 동일하면 null로 둘 수 있다. 클래스별 임계값이 다르면 해당 ThresholdPolicy를 링크로 참조한다.

---

### ConsumableDemandProfile — 소모품 수요 프로파일

서브시스템 클래스별 소모품 수요 통계. 재고 및 조달 판단에 사용한다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `subsystem_class_code` | string | 대상 서브시스템 클래스 |
| `monthly_mean` | number | 월 평균 교체 건수 |
| `monthly_median` | number | 월 중앙 교체 건수 |
| `monthly_peak` | integer | 월 최대 교체 건수 |
| `preventive_share` | number | 전체 교체 중 예방 교체 비중 |

---

### SensorChannel — 센서 채널

인터페이스: `Monitorable`

자산 또는 서브시스템에 부착된 측정 채널.

| 속성 | 타입 | 설명 |
|---|---|---|
| `channel_code` | string | 채널 고유 코드 |
| `display_name` | string \| null | 표시 이름 |
| `unit` | string \| null | 물리 단위. 원본에 단위가 없으면 null |
| `direction` | string | 이상 방향. `high` / `low` / `both` |
| `associated_subsystem_class_code` | string \| null | 연관된 서브시스템 클래스 코드. 매핑이 없으면 null |

> 센서-서브시스템 대응을 링크로 관리하면 매핑 변경이 코드 수정이 아니라 데이터 변경이 된다. 팩 문서에서 도메인별 매핑을 정의한다.

---

### PeerGroup — 동종 집단

동종 비교 기준이 되는 자산 집합.

| 속성 | 타입 | 설명 |
|---|---|---|
| `group_id` | string | 집단 고유 식별자. 예: `{class_code}-{age_min}-{age_max}` |
| `asset_class_code` | string | 기준 AssetClass |
| `age_min` / `age_max` | integer | 연식 범위 |
| `member_count` | integer | 집단 내 자산 수 |
| `sufficient_peers` | boolean | `member_count >= SamplingPolicy.min_peers`. 미달 시 동종 비교 판단 보류 |

> `sufficient_peers`가 `false`이면 백분위를 표시하되 판단 근거로 사용하지 않는다. 팩 문서에서 집단 크기 분포를 실측으로 명시한다.

---

## 1.2 모집단 계층

### PopulationSnapshot — 모집단 스냅샷

특정 시각의 전체 자산 모집단 상태.

| 속성 | 타입 | 설명 |
|---|---|---|
| `snapshot_id` | string | `population@{timestamp}` |
| `evaluated_at` | datetime | 평가 시각 |
| `asset_count` | integer | 평가 대상 자산 수 |
| `alarm_count` | integer | 알람 등급 건수 |
| `watch_count` | integer | 관찰 등급 건수 |
| `insufficient_data_count` | integer | 데이터 부족 자산 수 |
| `top_k_event_refs` | list[string] | 우선 확인 대상 RiskEvent 식별자 목록 |
| `baseline_version` | string | 평가에 사용된 BaselinePolicy 버전 |
| `threshold_policy_version` | string | 평가에 사용된 ThresholdPolicy 버전 |

> 인터페이스: `PopulationAggregate`
>
> `baseline_version`과 `threshold_policy_version`을 두면 정책 변경 전후의 PopulationSnapshot을 비교해 알람 건수 변화를 추적할 수 있다.

---

### MaintenanceCapacity — 정비 여력

기간별 정비 가용 자원.

| 속성 | 타입 | 설명 |
|---|---|---|
| `capacity_id` | string | 여력 기록 고유 식별자 |
| `period` | string | 대상 기간 |
| `available_technician_hours` | number | 가용 기술인 시간 |
| `assigned_work_order_count` | integer | 배정된 작업 지시 건수. WorkOrder에서 집계 |

---

## 1.3 관측 계층

### ObservationWindow — 관측 창

자산의 일정 기간 센서 집계. 추론의 기본 단위.

| 속성 | 타입 | 설명 |
|---|---|---|
| `window_id` | string | `{asset_id}@{window_end}` |
| `window_start` | datetime \| null | 관측 시작 시각. 시간축이 없는 도메인은 null |
| `window_end` | datetime \| null | 관측 종료 시각. 시간축이 없는 도메인은 null |
| `window_hours` | number \| null | 관측 창 시간 범위. 시간축이 없는 도메인은 null |
| `row_count` | integer | 창 내 데이터 행 수 |
| `sufficient_data` | boolean | `row_count >= SamplingPolicy.min_rows` |
| `reference_frame` | string | 기준선 참조 프레임 식별자 |

> `window_hours`가 null인 도메인(예: AI4I처럼 시계열이 아닌 단일 관측 레코드)에서는 `window_start`와 `window_end`도 null로 둔다. 이 경우 아래 시간 기반 속성은 산출하지 않는다.

**채널별 측정값 — ChannelReading (하위 구조)**

ObservationWindow에 포함되는 채널별 집계값. 별도 객체이거나 내장 구조체로 표현한다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `channel_code` | string | 측정 채널 코드 |
| `mean_value` | number | 창 내 평균값 |
| `z_score` | number \| null | 기준선 대비 z-점수. 기준선 미적용 시 null |
| `excess_ratio` | number \| null | `|z_score| / channel_threshold`. 초과 비율 |
| `peer_percentile` | number \| null | 동종 집단 내 백분위. `sufficient_peers`가 false이면 신뢰 보류 |

**시간 기반 속성 — 시간축이 있는 팩에서만 산출**

> 아래 속성은 `window_hours`가 null이 아닌 도메인에서만 계산한다. 팩 문서에서 산출 여부를 명시한다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `trend_label` | string \| null | `상승 중` / `하락 중` / `보합` / `판단 불가` |
| `trend_change_pct` | number \| null | 창 내 변화율 (%) |

---

## 1.4 이력 계층

### AlertType — 경보 유형

도메인에서 정의된 경보의 종류.

| 속성 | 타입 | 설명 |
|---|---|---|
| `alert_code` | string | 경보 유형 코드 |
| `display_name` | string \| null | 표시 이름 |
| `name_is_inferred` | boolean | 이름이 추정인 경우 true |
| `total_occurrences` | integer | 전체 발생 건수 |
| `converted_to_failure_rate` | number \| null | 이후 FailureEvent로 전환된 비율. 관측 창 기준 |

> 경보-서브시스템 연관은 1:1이 아닐 수 있다. 링크 카디널리티를 many-to-many로 두고 연관 강도를 링크 속성으로 표현한다.

---

### AlertEvent — 경보 발생

인터페이스: `TemporalEvent`

| 속성 | 타입 | 설명 |
|---|---|---|
| `event_id` | string | 이벤트 고유 식별자 |
| `occurred_at` | datetime | 발생 시각 |
| `alert_code` | string | AlertType 코드 |

---

### FailureEvent — 고장 발생

인터페이스: `TemporalEvent`

| 속성 | 타입 | 설명 |
|---|---|---|
| `event_id` | string | 이벤트 고유 식별자 |
| `occurred_at` | datetime | 발생 시각 |
| `subsystem_ref` | string \| null | 연관 Subsystem 식별자. nullable |
| `failure_mode_ref` | string \| null | 연관 FailureMode 코드. nullable |
| `mtbf_days` | number \| null | 이 자산의 고장 간 평균 시간. 고장 이력 0건이면 null |

> **제약:** `subsystem_ref`와 `failure_mode_ref`가 동시에 null일 수 없다. 둘 중 하나 이상 반드시 존재해야 한다.
>
> Subsystem이 있는 도메인은 `subsystem_ref`를 채운다. FailureMode만 기록하는 도메인은 `failure_mode_ref`를 채운다. 두 정보를 모두 가진 도메인은 둘 다 채울 수 있다.
>
> `mtbf_days`는 FailureEvent 생성 시점 기준으로 해당 자산의 고장 이력이 0건이면 null이다. 고장 건수가 소수인 경우 신뢰도 표기를 팩 문서에 명시한다.

---

### MaintenanceRecord — 정비 기록

인터페이스: `TemporalEvent`, `Auditable`

| 속성 | 타입 | 설명 |
|---|---|---|
| `record_id` | string | 기록 고유 식별자 |
| `performed_at` | datetime | 수행 시각 |
| `subsystem_class_code` | string \| null | 정비 대상 서브시스템 클래스 |
| `type` | string | `preventive` / `reactive` |
| `failure_within_window_before` | boolean | type 판정 근거. 정비 직전 관측 창 내 고장 여부 |
| `interval_since_previous_days` | number \| null | 이전 정비와의 간격. 최초 기록이면 null |
| `in_training_scope` | boolean | 학습 범위 포함 여부. 초기 기록 등 제외 대상은 false |

---

## 1.5 판정 계층

### RiskEvent — 위험 사건

인터페이스: `EvidenceBearing`, `TemporalEvent`

특정 자산에 대해 판정 시스템이 생성한 위험 사건. 등급과 근거를 포함하며 영속화된다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `event_id` | string | 이벤트 고유 식별자 |
| `detected_at` | datetime | 판정 시각 |
| `grade` | string | 등급 코드. 예: `alarm` / `watch` / `normal` |
| `grade_display` | string | 등급 표시 이름 |
| `max_excess_ratio` | number | 등급 판정의 근거값. 임계값 대비 최대 초과 비율 |
| `primary_subsystem_ref` | string \| null | 최대 초과 서브시스템 식별자. null이면 서브시스템 구분 없음 |
| `grade_failure_rate_pct` | number \| null | 해당 등급의 과거 FailureEvent 전환율. 팩 문서에서 실측 |
| `status` | string | `미확인` / `확인됨` / `조치중` / `종결` |
| `acknowledged_by` | string \| null | 확인자 |
| `acknowledged_at` | datetime \| null | 확인 시각 |

---

### Evidence — 근거

인터페이스: `EvidenceBearing`, `Versioned`

판정의 기반이 된 데이터와 정책 버전을 함께 보관한다. lineage 추적의 핵심 객체.

| 속성 | 타입 | 설명 |
|---|---|---|
| `evidence_id` | string | 근거 고유 식별자 |
| `generated_at` | datetime | 생성 시각 |
| `window_ref` | string | 대상 ObservationWindow 식별자 |
| `no_prior_alert` | boolean | 직전 관측 창에 AlertEvent 없음 |
| `multiple_candidates` | boolean | 복수의 Hypothesis가 제기된 경우 |
| `insufficient_data` | boolean | `ObservationWindow.sufficient_data`가 false인 경우 |
| `baseline_policy_ref` | string | 사용된 BaselinePolicy 버전 — lineage 링크 |
| `threshold_policy_ref` | string | 사용된 ThresholdPolicy 버전 — lineage 링크 |

> `baseline_policy_ref`와 `threshold_policy_ref`는 문자열이 아니라 객체 참조다. 어떤 정책 버전으로 근거가 만들어졌는지 되짚을 수 있다.

---

### Hypothesis — 가설

인터페이스: 없음 (Evidence에 귀속)

특정 채널 이상이 특정 서브시스템 또는 고장 모드와 연관될 수 있음을 나타내는 추론 단위.

| 속성 | 타입 | 설명 |
|---|---|---|
| `hypothesis_id` | string | 가설 고유 식별자 |
| `channel_code` | string | 이상이 감지된 채널 |
| `z_score` | number | 해당 채널의 z-점수 |
| `threshold_applied` | number | 적용된 임계값 |
| `direction` | string | 이상 방향. `high` / `low` / `both` |
| `association` | string | `channel_anomaly_associated_with_{target}_candidate` 형식 |
| `subsystem_ref` | string \| null | 연관 Subsystem 식별자. nullable |
| `failure_mode_ref` | string \| null | 연관 FailureMode 코드. nullable |
| `confirmed_by_failure` | boolean \| null | 이후 실제 FailureEvent로 확인되었는지. 사후 대조. 미확인이면 null |

> `confirmed_by_failure`를 집계하면 가설 적중률을 산출할 수 있다. 이 값이 null이 아닌 Hypothesis의 비율이 시스템 자체 평가 지표가 된다.

---

## 1.6 정책 계층

### BaselinePolicy — 기준선 정책

인터페이스: `Versioned`

z-점수 계산의 기준이 되는 채널별 통계. 버전이 관리되며 Evidence가 참조한다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `version` | string | 정책 버전 식별자 |
| `computed_on` | date | 산출일 |
| `training_cutoff` | datetime | 학습 데이터 마감 시각 |
| `window_hours` | number \| null | 집계 창 시간 범위. 시간축 없는 도메인은 null |
| `min_periods` | integer | 유효 집계 최소 기간 수 |
| `channel_stats` | map[channel_code → ChannelStat] | 채널별 통계 |

**ChannelStat (내장 구조)**

| 속성 | 타입 | 설명 |
|---|---|---|
| `mean` | number | 채널 기준 평균 |
| `std` | number | 채널 기준 표준편차 |
| `n_samples` | integer | 집계에 사용된 표본 수 |

---

### ThresholdPolicy — 임계값 정책

인터페이스: `Versioned`

이상 탐지 임계값. 버전이 관리되며 Evidence가 참조한다. 단일 조건과 복합 조건을 모두 지원한다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `version` | string | 정책 버전 식별자 |
| `computed_on` | date | 산출일 |
| `training_cutoff` | datetime | 학습 데이터 마감 시각 |
| `selection_rule` | string | 임계값 선택 규칙 설명 |
| `miss_target_pct` | number | 허용 미탐 비율 파라미터 (SamplingPolicy에서 설정) |
| `entries` | map[target_ref → ThresholdEntry] | 서브시스템 또는 고장 모드별 임계값 항목 |

**ThresholdEntry (내장 구조)**

각 서브시스템 또는 고장 모드가 하나의 Condition을 가진다.

| 속성 | 타입 | 설명 |
|---|---|---|
| `target_ref` | string | 대상 서브시스템 또는 고장 모드 코드 |
| `condition` | SingleCondition \| DerivedCondition \| CompoundCondition | 판정 조건 |
| `train_metrics` | map[string → number] \| null | 학습 데이터 성능 지표 |
| `val_metrics` | map[string → number] \| null | 검증 데이터 성능 지표 |

**SingleCondition**

단일 채널 기반 조건.

| 속성 | 타입 | 설명 |
|---|---|---|
| `channel_ref` | string | 대상 채널 코드 |
| `operator` | `"<"` \| `">"` \| `"<="` \| `">="` | 비교 연산자 |
| `threshold` | number | 임계값 |
| `direction` | `"low"` \| `"high"` \| `"both"` | 이상 방향 |

**DerivedCondition**

복수 채널의 파생 표현식 기반 조건.

| 속성 | 타입 | 설명 |
|---|---|---|
| `expression` | string | 채널 간 표현식. 예: `"channel_a - channel_b"` |
| `channel_refs` | list[string] | 참조 채널 코드 목록 |
| `operator` | `"<"` \| `">"` \| `"<="` \| `">="` | 비교 연산자 |
| `threshold` | number | 임계값 |

**CompoundCondition**

복수 조건의 논리 결합.

| 속성 | 타입 | 설명 |
|---|---|---|
| `logical_operator` | `"AND"` \| `"OR"` | 논리 연산자 |
| `conditions` | list[SingleCondition \| DerivedCondition \| CompoundCondition] | 하위 조건 목록 (재귀) |

> **설계 의도:** `SingleCondition`은 채널 z-점수 비교처럼 단순한 경우에 사용한다. `DerivedCondition`은 두 채널 차이나 비율을 임계값과 비교하는 경우에 사용한다. `CompoundCondition`은 여러 채널 조건이 AND/OR로 묶이는 경우(예: AI4I의 HDF 판정)에 사용한다. 세 형태는 중첩될 수 있다.

---

### SamplingPolicy — 샘플링 정책

모집단 비교와 충분성 판정에 사용되는 파라미터 집합. 코어의 수치 상수를 모두 이 객체로 이전한다.

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `min_peers` | integer | 동종 비교에 필요한 최소 집단 크기. `PeerGroup.sufficient_peers` 판정 기준 |
| `min_rows` | integer | 충분한 데이터 판정 최소 행 수. `ObservationWindow.sufficient_data` 판정 기준 |
| `window_hours` | number \| null | 관측 창 시간 범위. 시간축 없는 도메인은 null. `BaselinePolicy`와 일치해야 함 |
| `age_range` | integer | 동종 집단 연식 범위 (±N년). `PeerGroup.age_min` / `age_max` 산출 기준 |
| `class_equivalence_sigma_factor` | number | `AssetClass.same_equipment_class` 계산에 사용. 채널별 평균 차이 허용 배수 |
| `miss_target_pct` | number | ThresholdPolicy 선택 시 허용 미탐 비율 |
| `reactive_window_hours` | number \| null | 사후 정비 판정 관측 창 시간. 시간축 없는 도메인은 null |

---

## 1.7 행동 계층

현재 시스템에 완전히 빠져 있는 부분이다. 전부 신규 정의.

### WorkOrder — 작업 지시

인터페이스: `Auditable`

| 속성 | 타입 | 설명 |
|---|---|---|
| `work_order_id` | string | 작업 지시 고유 식별자 |
| `status` | string | `요청됨` / `배정됨` / `진행중` / `완료` / `보류` |
| `assignee` | string \| null | 담당자 |
| `due_at` | datetime \| null | 완료 기한 |
| `work_type` | string | `점검` / `교체` / `정밀진단` |
| `created_from_event_id` | string | 원인 RiskEvent 식별자 |

---

### OperationalDecision — 운영 판단

인터페이스: `Auditable`

| 속성 | 타입 | 설명 |
|---|---|---|
| `decision_id` | string | 판단 고유 식별자 |
| `decision` | string | `계속 운전` / `점검 요청` / `정지 검토` |
| `rationale` | string | 판단 근거 메모 |
| `decided_by` | string | 판단자 |
| `decided_at` | datetime | 판단 시각 |
| `evidence_ref` | string | 근거로 참조한 Evidence 식별자 |

---

# 2. 인터페이스

다형성 조회를 위한 분류. 어떤 도메인에도 동일하게 적용된다.

| 인터페이스 | 구현 객체 | 가능해지는 질의 |
|---|---|---|
| `Monitorable` | Asset, Subsystem, SensorChannel | 감시 대상 전체의 최근 이상 등급 |
| `Maintainable` | Subsystem | 정비 대상 중 수명 비율 상위 N |
| `EvidenceBearing` | RiskEvent, Evidence | 근거를 가진 모든 판정의 추적 |
| `Auditable` | WorkOrder, OperationalDecision, MaintenanceRecord | 사람의 행동 전체 이력 |
| `Versioned` | BaselinePolicy, ThresholdPolicy | 정책 변경 이력과 영향 범위 |
| `TemporalEvent` | AlertEvent, FailureEvent, MaintenanceRecord, RiskEvent | 한 자산의 시간순 전체 사건 |
| `PopulationAggregate` | AssetClass, SubsystemClass, ClassSubsystemProfile, ClassFailureModeProfile, ConsumableDemandProfile, PopulationSnapshot | 모집단 단위 통계 전체 — 개별 자산을 거치지 않는 조회 |

> `TemporalEvent`로 묶으면 경보·고장·정비를 각각 다른 테이블에서 조회하지 않고, "이 자산에 지난 N일간 일어난 모든 일"이 한 번의 순회로 나온다.
>
> `PopulationAggregate`는 `FleetAggregate`를 대체하는 이름이다. 기존 설계에서의 `FleetAggregate` 인터페이스가 이 이름으로 변경되었다.

---

# 3. 링크 타입

어휘 변환(Machine → Asset, Component → Subsystem 등)이 반영된다.

| 링크 | 출발 | 도착 | 카디널리티 |
|---|---|---|---|
| `asset_has_class` | Asset | AssetClass | many-to-one |
| `asset_has_subsystem` | Asset | Subsystem | one-to-many |
| `subsystem_has_class` | Subsystem | SubsystemClass | many-to-one |
| `class_subsystem_profile_of_class` | ClassSubsystemProfile | AssetClass | many-to-one |
| `class_subsystem_profile_of_subclass` | ClassSubsystemProfile | SubsystemClass | many-to-one |
| `class_failure_mode_profile_of_class` | ClassFailureModeProfile | AssetClass | many-to-one |
| `class_failure_mode_profile_of_mode` | ClassFailureModeProfile | FailureMode | many-to-one |
| `subclass_has_demand_profile` | SubsystemClass | ConsumableDemandProfile | one-to-one |
| `subsystem_monitored_by` | Subsystem | SensorChannel | one-to-one |
| `asset_belongs_to_peer_group` | Asset | PeerGroup | many-to-many |
| `asset_has_observation_window` | Asset | ObservationWindow | one-to-many |
| `asset_had_alert` | Asset | AlertEvent | one-to-many |
| `alert_event_has_type` | AlertEvent | AlertType | many-to-one |
| `alert_type_associated_with_subsystem` | AlertType | SubsystemClass | many-to-many |
| `subsystem_had_failure` | Subsystem | FailureEvent | one-to-many |
| `failure_event_has_mode` | FailureEvent | FailureMode | many-to-one |
| `subsystem_had_maintenance` | Subsystem | MaintenanceRecord | one-to-many |
| `maintenance_followed_failure` | MaintenanceRecord | FailureEvent | many-to-one |
| `asset_has_risk_event` | Asset | RiskEvent | one-to-many |
| `risk_event_has_evidence` | RiskEvent | Evidence | one-to-one |
| `evidence_covers_window` | Evidence | ObservationWindow | one-to-one |
| `evidence_used_baseline` | Evidence | BaselinePolicy | many-to-one |
| `evidence_used_threshold_policy` | Evidence | ThresholdPolicy | many-to-one |
| `evidence_proposes_hypothesis` | Evidence | Hypothesis | one-to-many |
| `hypothesis_names_subsystem` | Hypothesis | Subsystem | many-to-one |
| `hypothesis_names_failure_mode` | Hypothesis | FailureMode | many-to-one |
| `hypothesis_cites_channel` | Hypothesis | SensorChannel | many-to-one |
| `population_snapshot_includes_event` | PopulationSnapshot | RiskEvent | one-to-many |
| `population_snapshot_used_threshold_policy` | PopulationSnapshot | ThresholdPolicy | many-to-one |
| `risk_event_triggered_decision` | RiskEvent | OperationalDecision | one-to-many |
| `decision_created_work_order` | OperationalDecision | WorkOrder | one-to-many |
| `work_order_targets_subsystem` | WorkOrder | Subsystem | many-to-one |
| `work_order_produced_maintenance` | WorkOrder | MaintenanceRecord | one-to-many |

> `evidence_used_baseline`과 `evidence_used_threshold_policy`가 lineage 핵심 링크다. 근거가 어떤 정책 버전으로 만들어졌는지가 객체 참조로 남는다.
>
> `work_order_produced_maintenance`가 연결되면 작업 지시가 실제 정비로 이어졌는지가 추적된다.

---

# 4. 액션 타입

전부 신규 정의. 현재 시스템은 읽기 전용이며, 이 계층이 행동을 기록한다.

| 액션 | 대상 | 파라미터 | 필요 권한 | 승인 필요 |
|---|---|---|---|---|
| `acknowledge_risk_event` | RiskEvent | — | `events.acknowledge` | 아니오 |
| `record_operational_decision` | RiskEvent | decision, rationale | `events.decision` | 예 |
| `create_work_order` | RiskEvent | subsystem_ref, work_type, due_at | `workorders.create` | 예 |
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

> 운영 매니저는 지시하고 도메인 엔지니어는 수행한다. 이 비대칭이 화면 분리를 권한으로 뒷받침한다.

---

# 5. 정책 객체로 이전한 상수

기존 설계에서 속성에 직접 박혀 있던 수치 상수를 `SamplingPolicy`로 이전한다. 팩 문서에서 도메인별 실측값을 채운다.

| 이전 전 (속성 직접 기재) | 이전 후 (SamplingPolicy 파라미터) | 사용처 |
|---|---|---|
| `member_count >= 5` 판정 기준 | `min_peers` | `PeerGroup.sufficient_peers` |
| `row_count >= 12` 판정 기준 | `min_rows` | `ObservationWindow.sufficient_data` |
| 24시간 관측 창 | `window_hours` | `BaselinePolicy`, `ObservationWindow` |
| 연식 범위 ±3년 | `age_range` | `PeerGroup` 산출 |
| `class_equivalence` 표준편차 배수 | `class_equivalence_sigma_factor` | `AssetClass.same_equipment_class` |
| 미탐 허용 비율 | `miss_target_pct` | `ThresholdPolicy` 선택 |
| 사후 정비 판정 시간 창 | `reactive_window_hours` | `MaintenanceRecord.type` 판정 |
| 교체 간격 중앙값 (서브시스템별 상이) | 없음 (SubsystemClass 단위 실측) | `Subsystem.life_ratio` |

> 마지막 항목(교체 간격 중앙값)은 서브시스템 클래스별로 다르므로 단일 정책 파라미터가 아니다. `SubsystemClass.median_interval_days`에 팩 문서가 실측값을 채운다.

---

# 6. 검증 사례

코어 질의만으로 아래 5개 패턴이 표현 가능함을 보여준다. 각 패턴에 대해 AI4I 도메인에서 대응 질의가 성립하는지도 명시한다.

---

## 6.1 특정 AssetClass × SubsystemClass에 고장이 집중되는 패턴

**코어 질의:**

```
ClassSubsystemProfile
  where failures_per_asset > population_mean
  order by failures_per_asset desc
```

`ClassSubsystemProfile.failures_per_asset`을 AssetClass × SubsystemClass로 집계한 후 모집단 평균을 초과하는 교차를 내림차순 정렬한다. 특정 AssetClass와 특정 SubsystemClass의 조합에 고장이 집중되는 패턴이 단일 조회로 식별된다.

**AI4I 대응:** AI4I는 계통(Subsystem)이 구분되지 않으므로 `ClassSubsystemProfile` 대신 `ClassFailureModeProfile`을 사용한다. 동일 질의 구조로 AssetClass × FailureMode 교차의 `failure_rate`를 비교하면 같은 패턴이 성립한다.

---

## 6.2 SubsystemClass별 교체 주기 차이

**코어 질의:**

```
SubsystemClass
  select subsystem_class_code, display_name, median_interval_days
  order by median_interval_days asc
```

`SubsystemClass.median_interval_days`를 비교하면 어느 서브시스템 클래스가 더 빠르게 교체되는지가 나온다. 교체 주기가 짧은 클래스는 `ConsumableDemandProfile.monthly_mean`이 높을 가능성이 있으며 재고 우선순위에 반영된다.

**AI4I 대응:** AI4I는 SubsystemClass가 없으므로 이 질의는 직접 성립하지 않는다. 대신 `ClassFailureModeProfile.failure_rate`를 FailureMode별로 비교하면 고장 모드별 발생 주기 차이를 간접적으로 파악할 수 있다.

---

## 6.3 PeerGroup.member_count < SamplingPolicy.min_peers 판정

**코어 질의:**

```
PeerGroup
  where member_count < SamplingPolicy.min_peers
  select group_id, asset_class_code, age_min, age_max, member_count
```

`sufficient_peers: false`인 집단 목록이 나온다. 이 집단에 속한 자산의 `ObservationWindow.peer_percentile`은 표시하되 판단 근거로 사용하지 않아야 함을 경고한다. `SamplingPolicy.min_peers`가 파라미터이므로 기준을 바꾸면 재판정이 자동으로 반영된다.

**AI4I 대응:** AI4I도 동일한 `PeerGroup` 구조를 사용한다. `SamplingPolicy.min_peers` 값만 도메인에 맞게 설정하면 같은 질의가 그대로 성립한다.

---

## 6.4 AssetClass.same_equipment_class (계산)

**코어 질의:**

```
// 모든 AssetClass 쌍에 대해
for each (classA, classB) in AssetClass × AssetClass:
  for each channel in SensorChannel:
    diff = |mean(classA, channel) - mean(classB, channel)|
    threshold = SamplingPolicy.class_equivalence_sigma_factor × std(population, channel)
    if diff > threshold: return false
return true
```

모든 AssetClass 쌍과 모든 채널에 걸쳐 평균 차이가 허용 배수 이하이면 `same_equipment_class: true`이다. 이 결과가 `true`이면 여러 AssetClass를 통합한 전역 기준선이 편향 없이 적용됨을 의미한다.

**AI4I 대응:** AI4I가 복수의 AssetClass를 가지고 각 채널(온도, 회전수, 토크 등)의 분포를 클래스별로 비교할 수 있다면 동일한 계산이 성립한다. 단일 AssetClass만 있는 경우 이 속성은 항상 true이며 계산을 생략할 수 있다.

---

## 6.5 FailureEvent.mtbf_days nullable (고장 0건)

**코어 질의:**

```
Asset
  where failure_count = 0
  select asset_id, mtbf_days
// → mtbf_days is null for all results

Asset
  where failure_count > 0
  select asset_id, failure_count, mtbf_days
  where mtbf_days is null
// → 위 결과는 빈 집합이어야 함 (제약 검증)
```

고장 0건 자산의 `mtbf_days`가 null임을 확인하고, 고장 1건 이상인 자산에서 null이 없음을 검증한다. `FailureEvent.mtbf_days`도 동일하게 검증할 수 있다. 고장 건수가 소수인 자산은 팩 문서에서 신뢰도 표기를 별도로 다룬다.

**AI4I 대응:** AI4I에서도 고장 발생 여부가 레코드별로 기록되므로 고장 0건 자산이 존재할 수 있다. 동일한 null 제약이 성립하며, 집합적으로 고장 발생 비율이 낮은 경우 신뢰도 표기 기준을 팩 문서에 명시한다.

---

## 부록 — 어휘 변환 대조표

코어 문서와 기존 Azure PdM 팩 문서 간 용어 대응.

| 기존 (Azure PdM 팩) | 코어 |
|---|---|
| Machine | Asset |
| MachineModel | AssetClass |
| Component | Subsystem |
| ComponentClass | SubsystemClass |
| ModelComponentProfile | ClassSubsystemProfile |
| ComponentHypothesis | Hypothesis |
| EvidencePackage | Evidence |
| TelemetryWindow | ObservationWindow |
| BaselineProfile | BaselinePolicy |
| PartDemandProfile | ConsumableDemandProfile |
| ErrorType | AlertType |
| ErrorEvent | AlertEvent |
| FleetSnapshot | PopulationSnapshot |
| FleetAggregate | PopulationAggregate |
| (신규) | ClassFailureModeProfile |
| (신규) | SamplingPolicy |
| (신규) | FailureMode |
