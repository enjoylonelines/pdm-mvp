# AI4I 도메인 팩 — 코어 중립성 검증용 스케치

작성일: 2026-08-04
적용 범위: UCI Machine Learning Repository AI4I 2020 Predictive Maintenance Dataset
관련 문서: `ONTOLOGY_CORE.md` (도메인 중립 코어), `PACK_AZURE_PDM.md` (Azure PdM 팩)

> **목적 및 범위:** 이 문서는 AI4I 데이터셋의 완전한 구현 명세가 아니다. ONTOLOGY_CORE.md에 정의된 도메인 중립 코어가 AI4I처럼 Subsystem과 시계열이 없는 도메인에서도 동일한 구조로 성립하는지를 검증하기 위한 **최소한의 매핑 스케치**다. 실측 수치, 상세 학습 파이프라인, 배포 설계는 이 문서의 범위 밖이다.

---

## 1. AI4I 데이터셋 개요

### 기본 정보

- **출처:** UCI Machine Learning Repository — AI4I 2020 Predictive Maintenance Dataset
- **규모:** 10,000행. 각 행이 하나의 독립 관측
- **시간축:** 없음 (행 간 시간 순서 없음)
- **설비 정체성:** 없음 (같은 설비의 반복 관측인지 알 수 없음)
- **정비 이력:** 없음

### 컬럼 목록

| 컬럼 | 설명 |
|---|---|
| `UID` | 관측 고유 번호 |
| `productID` | 제품 식별자 |
| `Type` | 품질 등급 (L / M / H) |
| `Air temperature [K]` | 공기 온도 (켈빈) |
| `Process temperature [K]` | 공정 온도 (켈빈) |
| `Rotational speed [rpm]` | 회전 속도 |
| `Torque [Nm]` | 토크 |
| `Tool wear [min]` | 공구 마모 시간 (분) |
| `Machine failure` | 고장 발생 여부 (0 / 1) |
| `TWF` | Tool Wear Failure 발생 여부 (0 / 1) |
| `HDF` | Heat Dissipation Failure 발생 여부 (0 / 1) |
| `PWF` | Power Failure 발생 여부 (0 / 1) |
| `OSF` | Overstrain Failure 발생 여부 (0 / 1) |
| `RNF` | Random Failure 발생 여부 (0 / 1) |

### 고장 모드 판정 부등식

| 고장 모드 | 판정 조건 |
|---|---|
| **HDF** | `(Process_temp - Air_temp) < 8.6K` AND `Rotational_speed < 1380 rpm` |
| **PWF** | `Torque × ω < 3500W` OR `Torque × ω > 9000W` (ω = 2π × rpm / 60) |
| **OSF (L등급)** | `Tool_wear × Torque > 11,000 min·Nm` |
| **OSF (M등급)** | `Tool_wear × Torque > 12,000 min·Nm` |
| **OSF (H등급)** | `Tool_wear × Torque > 13,000 min·Nm` |
| **TWF** | Tool_wear가 200~240분 구간에서 무작위 발생 (결정론적 조건 없음) |
| **RNF** | 0.1% 무작위 발생 (결정론적 조건 없음) |

---

## 2. 핵심 매핑 결정

### AI4I 행의 정체 — ObservationWindow (Asset 아님)

AI4I의 각 행은 **독립 관측(ObservationWindow)**으로 취급한다. Asset 인스턴스를 생성하지 않는 이유:

1. **설비 정체성 없음:** 동일 설비의 반복 관측인지 알 수 없으므로 `Asset.asset_id`를 부여할 근거가 없다.
2. **정비 이력 없음:** `Asset.failure_count`, `Asset.preventive_count`, `Asset.reactive_count` 등 Asset의 핵심 속성이 산출 불가능하다.
3. **시간축 없음:** `Asset.time_since_last_failure_days`, `Subsystem.days_since_replacement` 등 시간 기반 속성이 의미를 가지지 못한다.

따라서 AI4I에서 Asset 계층 자체를 사용하지 않고, 각 행을 ObservationWindow의 단위로 취급하는 것이 코어 구조에 가장 자연스럽게 부합한다.

### Type → AssetClass

`Type` 컬럼의 L / M / H 값이 각각 하나의 AssetClass에 매핑된다. 세 등급은 제조 품질이 다르며, OSF 임계값처럼 등급별로 다른 판정 기준을 가진다.

| Type | AssetClass 코드 |
|---|---|
| L | `AC-L` |
| M | `AC-M` |
| H | `AC-H` |

### FailureMode 목록

AI4I의 고장 모드 5종이 각각 FailureMode 인스턴스로 정의된다.

| mode_code | display_name | 비고 |
|---|---|---|
| `TWF` | Tool Wear Failure | 확률적 발생 |
| `HDF` | Heat Dissipation Failure | 결정론적 조건 |
| `PWF` | Power Failure | 결정론적 조건 |
| `OSF` | Overstrain Failure | Type별 임계값 상이 |
| `RNF` | Random Failure | 확률적 발생 (0.1%) |

---

## 3. SamplingPolicy — AI4I 값 (스케치)

AI4I 도메인에서의 SamplingPolicy 파라미터 추정값. 실측값은 데이터 분석 후 확정한다.

| 파라미터 | AI4I 값 (스케치) | 근거 |
|---|---|---|
| `min_peers` | 결정 필요 | AI4I는 Type별 집단으로 정의 가능. L/M/H 각 등급의 행 수를 확인 후 결정 |
| `min_rows` | 1 | 각 행이 이미 단일 집계 관측이므로 1행 = 1관측 |
| `window_hours` | null | 시간축 없음 |
| `age_range` | 해당 없음 | 설비 연식 정보 없음 |
| `class_equivalence_sigma_factor` | 결정 필요 | L/M/H 등급별 센서 분포(온도, 회전수, 토크 등) 비교 후 산출 |
| `miss_target_pct` | 도메인에서 결정 | 고장 모드별 허용 미탐 비율. 운영 환경 기준 설정 필요 |
| `reactive_window_hours` | null | 정비 이력 없음 |

---

## 4. 객체별 매핑 테이블

코어의 모든 객체를 순서대로 AI4I 매핑 여부를 표시한다.

| 코어 객체 | AI4I 매핑 | 분류 |
|---|---|---|
| Asset | 해당 없음 (설비 정체성 없음) | 해당 없음 |
| AssetClass | Type (L / M / H) → 3개 AssetClass | 매핑됨 |
| Subsystem | 해당 없음 | 해당 없음 |
| SubsystemClass | 해당 없음 | 해당 없음 |
| FailureMode | TWF / HDF / PWF / OSF / RNF | 매핑됨 |
| ClassSubsystemProfile | 해당 없음 (Subsystem 없음) | 해당 없음 |
| ClassFailureModeProfile | Type × FailureMode 교차 (3 × 5 = 최대 15개) | 매핑됨 |
| ConsumableDemandProfile | 해당 없음 (Subsystem 없음) | 해당 없음 |
| SensorChannel | Air_temp / Process_temp / Rotational_speed / Torque / Tool_wear | 매핑됨 |
| PeerGroup | Type별 집단 (AC-L / AC-M / AC-H) | 매핑됨 (스케치) |
| PopulationSnapshot | 해당 없음 (시계열 없음) | 해당 없음 |
| MaintenanceCapacity | 해당 없음 (정비 이력 없음) | 해당 없음 |
| ObservationWindow | 각 행 (1행 = 1관측) | 매핑됨 |
| ObservationWindow.window_hours | null (시간축 없음) | 해당 없음 |
| ObservationWindow.window_start / window_end | null | 해당 없음 |
| trend_label / trend_change_pct | 해당 없음 (시간축 없음) | 해당 없음 |
| AlertType | 해당 없음 | 해당 없음 |
| AlertEvent | 해당 없음 | 해당 없음 |
| FailureEvent | Machine failure = 1인 행의 FailureMode | 매핑됨 |
| FailureEvent.subsystem_ref | null (Subsystem 없음) | 해당 없음 |
| FailureEvent.failure_mode_ref | TWF / HDF / PWF / OSF / RNF 중 발생한 것 | 매핑됨 |
| MaintenanceRecord | 해당 없음 (정비 이력 없음) | 해당 없음 |
| RiskEvent | 판정 조건 충족 시 생성 | 매핑됨 |
| Evidence | 판정 근거 (BaselinePolicy + ThresholdPolicy 버전 포함) | 매핑됨 |
| Hypothesis | 채널 임계값 초과 → FailureMode 후보 | 매핑됨 |
| BaselinePolicy | AI4I 전체 또는 Type별 통계 기준 | 매핑됨 |
| ThresholdPolicy | 고장 모드별 판정 부등식 | 매핑됨 |
| SamplingPolicy | 위 스케치값 | 매핑됨 (스케치) |
| WorkOrder | 해당 없음 (정비 이력 없음) | 해당 없음 |
| OperationalDecision | 해당 없음 (운영 행동 이력 없음) | 해당 없음 |

### SensorChannel 세부 매핑

| channel_code | 원본 컬럼 | 단위 | direction |
|---|---|---|---|
| `air_temp` | `Air temperature [K]` | K | both |
| `process_temp` | `Process temperature [K]` | K | both |
| `rotational_speed` | `Rotational speed [rpm]` | rpm | both |
| `torque` | `Torque [Nm]` | Nm | both |
| `tool_wear` | `Tool wear [min]` | min | high |

> `associated_subsystem_class_code`: 모든 채널이 null (Subsystem 없음)

---

## 5. ThresholdPolicy 조건 표현

고장 모드별 판정 부등식을 코어의 Condition 구조로 표현한다.

### HDF — CompoundCondition (AND)

```
ThresholdEntry(target_ref: "HDF")
  condition: CompoundCondition
    logical_operator: AND
    conditions:
      - DerivedCondition
          expression: "process_temp - air_temp"
          channel_refs: ["process_temp", "air_temp"]
          operator: "<"
          threshold: 8.6
      - SingleCondition
          channel_ref: "rotational_speed"
          operator: "<"
          threshold: 1380
          direction: "low"
```

### PWF — CompoundCondition (OR)

```
ThresholdEntry(target_ref: "PWF")
  condition: CompoundCondition
    logical_operator: OR
    conditions:
      - DerivedCondition
          expression: "torque * (2 * pi * rotational_speed / 60)"
          channel_refs: ["torque", "rotational_speed"]
          operator: "<"
          threshold: 3500
      - DerivedCondition
          expression: "torque * (2 * pi * rotational_speed / 60)"
          channel_refs: ["torque", "rotational_speed"]
          operator: ">"
          threshold: 9000
```

> 각속도(ω = 2π × rpm / 60)는 `rotational_speed`에서 파생된 표현식으로 처리한다. `DerivedCondition.expression`에 수식으로 명시한다.

### OSF — Type별 별도 ThresholdPolicy

OSF는 Type별로 임계값이 다르므로 세 개의 독립 ThresholdEntry(또는 독립 ThresholdPolicy)로 표현한다.

```
ThresholdEntry(target_ref: "OSF-L")  // AC-L에 연관
  condition: DerivedCondition
    expression: "tool_wear * torque"
    channel_refs: ["tool_wear", "torque"]
    operator: ">"
    threshold: 11000

ThresholdEntry(target_ref: "OSF-M")  // AC-M에 연관
  condition: DerivedCondition
    expression: "tool_wear * torque"
    channel_refs: ["tool_wear", "torque"]
    operator: ">"
    threshold: 12000

ThresholdEntry(target_ref: "OSF-H")  // AC-H에 연관
  condition: DerivedCondition
    expression: "tool_wear * torque"
    channel_refs: ["tool_wear", "torque"]
    operator: ">"
    threshold: 13000
```

### TWF — 결정론적 조건 없음

TWF는 공구 마모 200~240분 구간에서 무작위로 발생한다. 결정론적 임계값 조건을 정의할 수 없으므로:

- `ThresholdEntry(target_ref: "TWF").condition`: null 또는 별도 확률 모델로 처리
- 이 도메인에서 TWF는 ThresholdPolicy 기반 판정보다 통계적 발생률 기반 판정이 적합함을 팩 구현 단계에서 결정한다

### RNF — 결정론적 조건 없음

RNF는 0.1% 무작위 발생이다. 동일하게 condition이 null이며 ThresholdPolicy 기반 판정이 적용되지 않는다.

---

## 6. ClassFailureModeProfile 검증 — 코어 중립성 확인

OSF의 경우 Type별 임계값이 다르므로 ClassFailureModeProfile이 Type × FailureMode 교차마다 별개의 `associated_threshold`를 가질 수 있는지를 확인한다.

### OSF에 대한 ClassFailureModeProfile 세 인스턴스

```
ClassFailureModeProfile(profile_id: "AC-L-OSF")
  asset_class_code: "AC-L"
  mode_code: "OSF"
  failure_rate: L등급 행 중 OSF=1인 행의 비율  // 실측 필요
  asset_count: L등급 행 수
  associated_threshold: "OSF-L"  // ThresholdPolicy 또는 ThresholdEntry 참조

ClassFailureModeProfile(profile_id: "AC-M-OSF")
  asset_class_code: "AC-M"
  mode_code: "OSF"
  failure_rate: M등급 행 중 OSF=1인 행의 비율  // 실측 필요
  asset_count: M등급 행 수
  associated_threshold: "OSF-M"

ClassFailureModeProfile(profile_id: "AC-H-OSF")
  asset_class_code: "AC-H"
  mode_code: "OSF"
  failure_rate: H등급 행 중 OSF=1인 행의 비율  // 실측 필요
  asset_count: H등급 행 수
  associated_threshold: "OSF-H"
```

### 검증 결론

- 코어의 `ClassFailureModeProfile.associated_threshold`가 nullable string(링크)으로 정의되어 있으므로, Type마다 다른 ThresholdPolicy를 참조하는 것이 코어 구조 변경 없이 표현 가능하다.
- HDF, PWF처럼 Type에 무관하게 동일한 임계값을 가지는 고장 모드는 `associated_threshold`를 단일 값으로 설정하거나 null로 두면 된다.
- **결론:** 코어 구조는 AI4I의 Type별 이종 임계값 패턴을 수용한다. 코어 중립성이 이 교차에서 확인됨.

---

## 7. 6장 검증 사례 AI4I 대응 요약

ONTOLOGY_CORE.md 6장의 5개 검증 사례 각각에 대한 AI4I 대응.

### 6.1 특정 AssetClass × SubsystemClass에 고장이 집중되는 패턴

- **AI4I 대응:** 성립 (변형)
- Subsystem이 없으므로 `ClassSubsystemProfile` 대신 `ClassFailureModeProfile.failure_rate`를 사용한다. `Type × FailureMode` 교차에서 failure_rate가 높은 조합을 내림차순 정렬하면 동일한 패턴이 식별된다.

### 6.2 SubsystemClass별 교체 주기 차이

- **AI4I 대응:** 직접 성립 안 함
- SubsystemClass와 교체 주기 개념이 AI4I에 없다. 대신 `ClassFailureModeProfile.failure_rate`를 FailureMode별로 비교하면 고장 모드별 발생 빈도 차이를 간접적으로 파악할 수 있다. 완전한 대응은 아니나 코어 구조 자체의 결함이 아님을 확인.

### 6.3 PeerGroup.member_count < SamplingPolicy.min_peers 판정

- **AI4I 대응:** 성립
- AI4I의 PeerGroup은 Type별 집단(AC-L / AC-M / AC-H)이다. 각 Type의 행 수가 `SamplingPolicy.min_peers` 이상인지를 동일한 질의로 판정한다. `min_peers` 값만 도메인에 맞게 설정하면 코어 질의가 그대로 적용된다.

### 6.4 AssetClass.same_equipment_class (계산)

- **AI4I 대응:** 성립
- L / M / H 세 AssetClass가 존재하고, 5개 센서 채널의 분포를 클래스별로 비교할 수 있다. `SamplingPolicy.class_equivalence_sigma_factor`를 설정하면 동일한 계산식이 적용된다. 이 결과가 true이면 세 Type을 통합한 전역 기준선이 편향 없이 사용 가능함을 의미한다.

### 6.5 FailureEvent.mtbf_days nullable (고장 0건)

- **AI4I 대응:** 성립 (조건부)
- AI4I에서 Asset 인스턴스를 생성하지 않으므로 `Asset.mtbf_days` 계산은 해당 없다. 그러나 `FailureEvent.mtbf_days`는 `Machine failure = 1`인 행에서 FailureEvent를 생성할 때 동일한 null 제약이 적용된다. 고장 이력이 누적되지 않는 단일 관측 구조에서는 `mtbf_days`가 항상 null이 되며, 이 동작이 코어 제약과 일치함을 확인.

---

## 8. 해당 없음 항목 요약

이 도메인에서 nullable 또는 해당 없음으로 처리되는 코어 요소 전체 목록.

### 객체 — 전체 미사용

| 코어 객체 | 미사용 이유 |
|---|---|
| Asset | 설비 정체성 없음 |
| Subsystem | 계통 구분 없음 |
| SubsystemClass | 계통 구분 없음 |
| ClassSubsystemProfile | Subsystem 없음 |
| ConsumableDemandProfile | Subsystem 없음 |
| PopulationSnapshot | 시계열 없음 |
| MaintenanceCapacity | 정비 이력 없음 |
| AlertType | 경보 이력 없음 |
| AlertEvent | 경보 이력 없음 |
| MaintenanceRecord | 정비 이력 없음 |
| WorkOrder | 정비 이력 없음 |
| OperationalDecision | 운영 행동 이력 없음 |

### 속성 — null 처리

| 코어 속성 | null 이유 |
|---|---|
| ObservationWindow.window_hours | 시간축 없음 |
| ObservationWindow.window_start | 시간축 없음 |
| ObservationWindow.window_end | 시간축 없음 |
| ObservationWindow.trend_label | 시간축 없음 |
| ObservationWindow.trend_change_pct | 시간축 없음 |
| FailureEvent.subsystem_ref | Subsystem 없음 |
| FailureEvent.mtbf_days | 고장 이력 누적 불가 (단일 관측 구조) |
| SamplingPolicy.window_hours | 시간축 없음 |
| SamplingPolicy.reactive_window_hours | 정비 이력 없음 |
| SamplingPolicy.age_range | 연식 정보 없음 |
| ThresholdEntry(TWF).condition | 결정론적 조건 없음 |
| ThresholdEntry(RNF).condition | 결정론적 조건 없음 |
| SensorChannel.associated_subsystem_class_code | Subsystem 없음 |

### 링크 — 해당 없음

Subsystem, Asset, MaintenanceRecord, WorkOrder, AlertEvent, PopulationSnapshot에 연결된 모든 링크 타입이 이 도메인에서 사용되지 않는다. 코어 링크 구조는 변경 없이 유지되며, 미사용 링크는 빈 집합으로 존재한다.
