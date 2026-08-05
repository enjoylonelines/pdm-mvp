# 작업 지시 — 온톨로지 설계를 코어 / 도메인 팩으로 분리

## 배경

`ONTOLOGY_DESIGN.md`에 Azure PdM 온톨로지 설계가 완료돼 있다. 그런데 이 설계는 **도메인 어휘와 중립 구조가 섞여 있다.** `Machine`, `Component`, `comp_code`, `volt` 같은 이름이 코어에 그대로 들어가 있고, `min_peers=5` 같은 상수가 속성에 박혀 있다.

이 프로젝트의 목표는 **설비 도메인이 바뀌어도 일관된 결과물이 나오는 서비스**다. 지금 상태로 구현하면 도메인이 코어에 굳는다.

이 작업은 코드를 쓰지 않는다. **설계 문서를 재구성하는 작업**이다.

## 산출물

```
ONTOLOGY_CORE.md        도메인 중립 코어 — 어떤 설비 도메인에도 적용
PACK_AZURE_PDM.md       Azure PdM 매핑
PACK_AI4I.md            AI4I 매핑 (스케치 — 중립성 검증용)
```

기존 `ONTOLOGY_DESIGN.md`는 **삭제하지 않는다.** 상단에 "이 문서는 위 셋으로 분리됐다"는 안내만 추가한다.

---

## 1. 반드시 반영할 구조 변경

### 1.1 Subsystem과 FailureMode 분리 — 가장 중요

현 설계는 `Component` 하나로 두 개념을 뭉개고 있다. Azure PdM에서는 `comp1~4`가 우연히 둘 다 역할을 해서 문제가 보이지 않았다.

| | 정비 가능 단위 | 고장 양태 |
|---|---|---|
| Azure PdM | `comp1~4` | 없음 |
| AI4I | 없음 | `TWF` / `HDF` / `PWF` / `OSF` / `RNF` |

ISO 14224로 치면 전자는 **maintainable item**, 후자는 **failure mode**다. 코어에는 둘 다 있어야 한다.

`FailureEvent`는 두 링크를 모두 가지되 각각 nullable로 두고, **최소 하나는 있어야 한다**는 제약을 명시한다.

```
FailureEvent → Subsystem     (nullable)
FailureEvent → FailureMode   (nullable)
제약: 둘 다 null 불가
```

### 1.2 ObservationWindow 일반화

현 `TelemetryWindow`는 24시간 창을 전제한다. AI4I는 시간축이 없어 관측이 1행이다. **"시간 범위"가 아니라 "관측 집합"**으로 정의를 넓힌다.

- `window_hours`는 nullable
- `row_count`와 `sufficient_data`는 유지 (시간 무관하게 의미 있음)
- 시간 기반 속성(`trend_label`, `trend_change_pct`)은 별도 그룹으로 묶고 "시간축이 있는 팩에서만 산출"을 명시

### 1.3 상수를 정책 객체로 분리

아래 값들이 지금 속성이나 코드에 박혀 있다. 전부 정책 객체의 파라미터가 되어야 한다.

| 값 | 현재 위치 | 옮길 곳 |
|---|---|---|
| `min_peers = 5` | `PeerGroup.sufficient_peers` | `SamplingPolicy` |
| `min_rows = 12` | `evidence_package.MIN_ROWS` | `SamplingPolicy` |
| `window_hours = 24` | `evidence_package.WINDOW_HOURS` | `SamplingPolicy` |
| peer 연식 범위 `±3` | `_build_peer_comparison` | `SamplingPolicy` |
| 가설 생성 z 임계 `2.0` | `_build_hypotheses` | `ThresholdPolicy` |
| 등급 경계 `1.0` / `0.8` | `report_generator.GRADE_*` | `ThresholdPolicy` |
| 추세 판정 `20%` | `manager_app.TREND_CHANGE_THRESHOLD_PCT` | `ThresholdPolicy` |
| `same_equipment_class = true` | `MachineModel` 상수 | `AssetClass`의 **계산 속성** |

`SamplingPolicy`는 신규 객체다. 표본 충분성 판단 기준을 한곳에 모은다.

### 1.4 ThresholdPolicy가 두 형태를 받아야 한다

```
Azure PdM   단일 조건    z > 3.75
AI4I        조건 결합    온도차 < 8.6K AND 회전속도 < 1380rpm
```

후자는 **여러 센서의 조건이 AND로 묶인다.** 현 설계는 계통당 센서 하나·임계값 하나를 전제한다. 조건식을 표현할 수 있는 구조로 확장한다. 이게 정책 계층이 튼튼한지 가르는 지점이다.

---

## 2. 어휘 변환

코어에서 도메인 어휘를 제거한다. 아래는 출발점이며, 더 나은 이름이 있으면 제안하고 이유를 적을 것.

| 현재 | 코어 |
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
| ErrorType / ErrorEvent | AlertType / AlertEvent |
| FleetSnapshot | PopulationSnapshot |
| MaintenanceCapacity | MaintenanceCapacity (유지 — 중립) |
| MaintenanceRecord | MaintenanceRecord (유지 — ISO 14224 어휘) |
| RiskEvent, WorkOrder, OperationalDecision, SensorChannel, PeerGroup | 유지 |

**`comp1`, `volt`, `rotate`, `pressure`, `vibration`, `model1`, `error1`, `machineID`는 코어 문서에 한 번도 등장해서는 안 된다.**

---

## 3. 중립성 시험 — 각 코어 요소마다 통과해야 함

코어의 모든 객체와 속성에 대해 아래를 답한다. 답을 `PACK_AZURE_PDM.md`와 `PACK_AI4I.md`의 매핑표에 적는다.

1. **Azure PdM에서 이 값은 무엇인가?**
2. **AI4I에서 이 값은 무엇인가?**

가능한 답은 셋뿐이다.

- **매핑됨** — 해당 도메인의 어떤 필드·계산에 대응
- **해당 없음** — 그 도메인에 존재하지 않음 (nullable로 처리)
- **의미가 다름** — **설계 결함이다.** 코어를 고쳐야 한다

세 번째가 나오면 반드시 보고하고 코어를 수정한다. `Subsystem`/`FailureMode` 분리가 바로 이 경우였다.

### AI4I 매핑 참고

| AI4I | 대응 후보 |
|---|---|
| 10,000행 각 표본 | Asset? Observation? — **판단 필요.** AI4I는 설비 정체성이 없다 |
| `Type` (L/M/H 품질등급) | AssetClass |
| `Air/Process temperature`, `Rotational speed`, `Torque`, `Tool wear` | SensorChannel |
| `TWF/HDF/PWF/OSF/RNF` | FailureMode |
| 고장 판정 부등식 | ThresholdPolicy (조건 결합형) |
| 정비 이력 | **해당 없음** |
| 시간축 | **해당 없음** |

`Type`을 AssetClass로 보면 **등급 × 고장모드 교차**가 성립한다. OSF 임계값이 등급마다 다르므로(L 11,000 / M 12,000 / H 13,000) 의미 있는 교차가 나온다. `ClassSubsystemProfile`이 두 도메인에서 다 작동하는지 확인하는 좋은 사례다.

---

## 4. 코어가 표현할 수 있어야 하는 것 — 검증 사례

아래 발견들이 **코어 구조만으로 표현 가능해야 한다.** 도메인 팩의 값이 바뀌어도 같은 질의가 성립해야 한다.

| 발견 (Azure PdM) | 코어 질의 형태 |
|---|---|
| 가압 계통 고장이 model1·2에만 있고 model3·4는 0건 | `ClassSubsystemProfile.failures_per_asset` 조회 |
| 회전 계통만 교체 주기 30일 (나머지 45일) | `SubsystemClass.median_interval` |
| 동종 5대 미만인 설비 16대 | `PeerGroup.member_count` < `SamplingPolicy.min_peers` |
| 네 모델의 센서 분포가 동일 | `AssetClass.same_equipment_class` (계산) |
| 고장 0건 설비 2대 → MTBF null | nullable 처리 |

AI4I에서도 대응하는 질의가 성립하는지 각 항목마다 적는다. 예: 등급 × 고장모드 교차에서 "L등급은 OSF가 유독 많다" 같은 패턴이 같은 질의로 나오는가.

---

## 5. 유지할 것

기존 설계에서 잘 된 부분은 그대로 가져간다.

- **출처 표기** (`원본` / `계산` / `신규` / `합성`) — 팩 문서에서 유지
- **인과 표현 금지** — `associated`, `candidate`, `연관`, `후보`만 사용
- **추정 플래그** — `name_is_inferred` 같은 표시
- **lineage 링크** — Evidence가 BaselinePolicy·ThresholdPolicy 버전을 참조
- **행동 계층** — WorkOrder, OperationalDecision, 8개 액션, 역할별 권한
- **인터페이스** — Monitorable, Maintainable, EvidenceBearing, Auditable, Versioned, TemporalEvent, FleetAggregate

`FleetAggregate`는 이름이 도메인 어휘이므로 `PopulationAggregate`로 바꾼다.

---

## 6. 하지 말 것

- 코어 문서에 Azure PdM 고유 식별자를 쓰지 말 것 (`comp1`, `volt`, `model1`, `error1`, `machineID`)
- 코어에 숫자 상수를 두지 말 것. 전부 정책 객체 파라미터
- 개수를 고정하지 말 것 (센서 4개, 계통 4개 등)
- 실측값을 코어 문서에 넣지 말 것. 실측은 팩 문서에만
- AI4I 팩을 완전히 설계하지 말 것. **중립성 검증에 필요한 만큼만** — 매핑표와 "해당 없음" 목록이면 충분
- 기존 `ONTOLOGY_DESIGN.md`를 삭제하지 말 것
- 코드를 수정하지 말 것. 이번 작업은 문서만

---

## 7. 완료 기준

1. `ONTOLOGY_CORE.md`에 Azure PdM 고유 식별자가 **0개**
2. 코어의 모든 객체·속성이 두 팩 문서에서 **매핑됨 / 해당 없음** 중 하나로 분류됨
3. **의미가 다름**으로 분류된 항목이 있다면 코어를 수정하고, 수정 내역을 보고
4. `Subsystem`과 `FailureMode`가 분리되고 `FailureEvent`의 nullable 제약이 명시됨
5. 정책 객체로 옮긴 상수 8종이 전부 반영됨
6. `ThresholdPolicy`가 단일 조건과 조건 결합을 모두 표현할 수 있음
7. 4장의 검증 사례 5건이 코어 질의로 표현 가능함이 문서에 적혀 있음

## 8. 참고 파일

```
ONTOLOGY_DESIGN.md        현 설계 — 이것을 분리하는 것이 작업
EVIDENCE_PACKAGE.md       기존 설계 원칙과 문서화 스타일
evidence_package.py       상수 위치 확인용 (WINDOW_HOURS, MIN_ROWS, Z_THRESHOLD)
report_generator.py       상수 위치 확인용 (GRADE_ALARM, GRADE_WATCH)
manager_app.py            상수 위치 확인용 (TREND_CHANGE_THRESHOLD_PCT)
display_names.py          추정 플래그 처리 참고
```

AI4I 사양은 UCI 문서 기준이며, 고장 판정 부등식은 다음과 같다.

```
HDF   온도차 < 8.6K  AND  회전속도 < 1380rpm
PWF   토크 × 각속도 < 3500W  또는  > 9000W
OSF   공구마모 × 토크 > 11,000 / 12,000 / 13,000 minNm (L / M / H)
TWF   공구마모 200~240분 구간에서 무작위
RNF   0.1% 무작위
```
