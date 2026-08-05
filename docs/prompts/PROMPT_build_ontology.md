# 작업 지시 — `build_ontology.py` 작성

## 배경

이 저장소(`azure-pdm`)는 Azure Predictive Maintenance 데이터셋으로 설비 이상 신호를 판정하고 역할별 리포트를 생성한다. 데이터 정제·모델 학습·임계값 설정·근거 생성·매니저 화면이 이미 구현돼 있다.

다음 단계는 이 데이터를 **온톨로지 객체와 링크로 물질화**하는 것이다. 설계는 `ONTOLOGY_DESIGN.md`에 완료돼 있다. 이 작업은 그 설계가 실제 데이터와 맞는지 검증하는 동시에, 이후 온톨로지 기반 조회의 기반을 만든다.

## 작업

`build_ontology.py` 하나를 작성한다. 5개 CSV와 2개 JSON에서 **정적 객체와 링크를 생성**해 파일로 출력한다.

### 입력

```
archive/PdM_telemetry.csv      876,100행
archive/PdM_errors.csv           3,919행
archive/PdM_failures.csv           761행
archive/PdM_maint.csv            3,286행
archive/PdM_machines.csv           100행
baseline_constants.json
thresholds.json
```

`PdM_telemetry.csv`는 이번 범위에서 **읽지 않는다.** 관측 창은 조회 시 생성하는 동적 객체다.

### 출력

```
ontology/
  object_types.json              객체 타입 정의 (설계 문서 그대로)
  link_types.json                링크 타입 정의
  instances/
    machine.jsonl                100
    machine_model.jsonl            4
    component.jsonl              400
    component_class.jsonl          4
    model_component_profile.jsonl 16
    part_demand_profile.jsonl      4
    sensor_channel.jsonl           4
    peer_group.jsonl             ≤56
    error_type.jsonl               5
    error_event.jsonl          3,919
    failure_event.jsonl          761
    maintenance_record.jsonl   3,286
    baseline_profile.jsonl         1
    threshold_policy.jsonl         1
  links/
    {link_type}.jsonl            링크별 파일
  build_report.json              생성 통계 + 검증 결과
```

JSONL을 쓰는 이유는 diff와 육안 검사가 쉽기 때문이다. SQLite 적재는 이후 단계에서 한다.

## 범위 밖

다음은 동적 객체이거나 합성이 필요하므로 **이번에 만들지 않는다.**

`TelemetryWindow` · `EvidencePackage` · `ComponentHypothesis` · `RiskEvent` · `FleetSnapshot` · `WorkOrder` · `OperationalDecision` · `MaintenanceCapacity`

`Machine`의 `criticality`, `downtime_cost_per_hour`, `location`, `assigned_engineer`도 합성 항목이므로 필드만 `null`로 두고 값을 만들지 않는다.

## 제약

1. **순수 함수로 작성한다.** 기존 `evidence_package.py`와 같은 스타일. 전역 상태 없음, 부수효과는 파일 쓰기 한 곳으로 모은다.
2. **의존성은 pandas와 표준 라이브러리만.** LLM 없음, DB 없음, 외부 API 없음.
3. **인과 표현 금지.** 기존 원칙을 유지한다. 필드명과 값에 `cause`, `원인`, `때문` 금지. `associated`, `candidate`, `연관`, `후보`만 쓴다.
4. **모든 속성에 출처를 남긴다.** 각 객체에 `_source` 필드를 두고 `원본` / `계산` / `신규` 중 하나를 값별로 기록하거나, 최소한 객체 단위로 기록한다.
5. **추정값에 플래그를 단다.** `ComponentClass.display_name`은 원본에 물리적 정체가 없어 센서 특성으로 추정한 것이다. `name_is_inferred: true`를 반드시 포함한다. 표시명은 `display_names.py`를 재사용한다.
6. **null을 숨기지 않는다.** 고장이 0건인 설비 2대의 `mtbf_days`는 `null`이다. 0으로 채우지 않는다.
7. **기존 코드를 재사용한다.** `display_names.py`, `z_baseline.py`의 로더를 쓴다. 계산 로직을 복제하지 않는다.

## 파생값 산출 규칙

| 값 | 규칙 |
|---|---|
| `Component.last_replacement_type` | 교체 24시간 이내에 같은 설비·계통 고장이 있으면 `reactive`, 아니면 `preventive` |
| `Component.median_interval_days` | **계통 코드별** 교체 간격 중앙값. 설비별이 아니다 |
| `Component.life_ratio` | `days_since_replacement / median_interval_days`. 기준 시각은 인자로 받는다 |
| `Machine.reactive_ratio` | `reactive_count / (preventive_count + reactive_count)` |
| `Machine.mtbf_days` | 고장 간격의 평균. 고장 1건 이하면 `null` |
| `MaintenanceRecord.in_training_scope` | `performed_at >= 2015-01-01`. 2014년 기록은 경과일 계산에는 쓰되 학습 범위에서 제외 |
| `PeerGroup` | 각 설비의 `(model, age±3)`으로 정의. 고유 조합만 생성 |
| `PeerGroup.sufficient_peers` | `member_count >= 5`. 미달 시 백분위를 판단 근거로 쓰지 않는다는 표시 |
| `PartDemandProfile` | 2015년 기록만 사용. 2014년 제외 |

기준 시각(`as_of`)은 CLI 인자로 받고 기본값은 `2016-01-01 00:00:00`(데이터 종료 시점)으로 한다.

## 검증 — 이 수치가 나와야 한다

`build_report.json`에 아래 항목을 산출하고, `--verify` 플래그를 주면 기대값과 대조해 불일치를 표준 오류로 출력하고 종료 코드 1을 반환한다.

### 인스턴스 수

| 객체 | 기대값 |
|---|---|
| Machine | 100 |
| Component | 400 |
| ErrorEvent | 3,919 |
| FailureEvent | 761 |
| MaintenanceRecord | 3,286 |
| PeerGroup | 56 |

### 모델 분포

```
model3 35 · model4 32 · model2 17 · model1 16
연식 범위 0 ~ 20
```

### 계통별 교체 간격 중앙값 (일)

```
comp1 45 · comp2 30 · comp3 45 · comp4 45
간격 산출 불가 조합: 0개
```

### 계통별 총 고장

```
comp1 192 · comp2 259 · comp3 131 · comp4 179
```

### 계통별 2015년 교체 건수 / 월평균

```
comp1 702 / 58.5    comp2 761 / 63.4
comp3 706 / 58.8    comp4 710 / 59.2
월 최대: comp1 67 · comp2 76 · comp3 75 · comp4 71
```

### 모델별 대당 고장

```
model1 11.8 · model2 9.9 · model3 6.3 · model4 5.7
```

### 모델 × 계통 대당 고장 — 가장 중요한 검증

```
          comp1  comp2  comp3  comp4
model1      2.1    2.9    4.2    2.6
model2      1.8    2.4    3.7    2.0
model3      1.9    2.5    0.0    1.8
model4      1.9    2.6    0.0    1.2
```

**model3과 model4의 comp3 고장이 정확히 0이어야 한다.** 이 칸이 0이 아니면 집계 로직에 오류가 있다.

### 설비별 고장 분포

```
최소 2 · 중앙값 7 · 최대 19
고장 0건 설비: 2대 → mtbf_days 는 null
```

### 모델 간 설비 동질성 — 참고

네 모델은 같은 종류의 설비다. 센서 분포 평균 차이가 전부 표준편차의 5% 미만으로 확인됐다(`ONTOLOGY_DESIGN.md` 참조). 따라서 `MachineModel.same_equipment_class`는 항상 `true`이고, 전역 기준선 사용이 타당하다.

이 스크립트는 텔레메트리를 읽지 않으므로 이 값을 재계산하지 않는다. 상수로 둔다.

### 동종 집단 크기 — 표본 부족 검출

```
model1  최소 3 / 중앙  9 / 최대 10
model2  최소 2 / 중앙  4 / 최대  7
model3  최소 4 / 중앙 11 / 최대 19
model4  최소 5 / 중앙 10 / 최대 14

전체 최소 2 · 중앙 10 · 최대 19
sufficient_peers = false 인 설비: 16대
```

**동종 5대 미만이 16대**로 나와야 한다. 이 값이 0이면 `age±3` 범위 계산이 틀린 것이다.

## 무결성 검사

`build_report.json`에 아래를 포함한다.

- 모든 링크의 출발·도착 객체 ID가 실제 인스턴스에 존재하는가 (dangling reference 0건)
- 모든 객체 ID가 고유한가
- 카디널리티 위반이 없는가 (예: `component_monitored_by`가 one-to-one인데 2개 이상 연결)
- JSON 직렬화 시 numpy 타입이 새어 나가지 않는가

## 완료 기준

1. `python build_ontology.py --verify` 가 종료 코드 0으로 끝난다
2. `ontology/` 아래 14개 인스턴스 파일과 링크 파일이 생성된다
3. `build_report.json`의 모든 검증 항목이 통과로 기록된다
4. dangling reference 0건, 중복 ID 0건
5. 스크립트를 두 번 실행해도 같은 결과가 나온다 (멱등)

## 하지 말 것

- 텔레메트리 876,100행을 읽지 말 것. 이번 범위 아님
- 없는 값을 그럴듯하게 채우지 말 것. `criticality` 등 합성 항목은 `null`
- 설계 문서에 없는 객체 타입을 추가하지 말 것. 필요하다고 판단되면 먼저 보고할 것
- 검증 기대값을 코드에 맞추지 말 것. 불일치가 나오면 **집계 로직을 의심할 것**
- 기존 `evidence_package.py`, `report_generator.py`, `manager_app.py`를 수정하지 말 것

## 참고 파일

```
ONTOLOGY_DESIGN.md          객체·링크·속성 정의 — 이것이 사양이다
EVIDENCE_PACKAGE.md         기존 설계 원칙과 문서화 스타일
display_names.py            표시명 매핑
z_baseline.py               baseline / thresholds 로더
evidence_package.py         순수 함수 스타일 참고
```
