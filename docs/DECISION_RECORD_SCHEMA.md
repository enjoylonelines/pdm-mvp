# 판단 기록 계약

- 작성일: 2026-08-05
- 상태: **제안** — 구현 전, 팀 확인 필요
- 범위: `pdm-mvp`가 정의하고 프로토타입이 화면으로 구현한다

## 왜 필요한가

킥오프(결정 000)는 이 시스템을 **의사결정 인터페이스**로 정의했다. 그런데 현재 두 저장소 모두 사람의 판단을 담는 구조가 없다.

데이터가 답하는 것과 못 답하는 것이 갈린다.

```
canonical v3.1 이 답하는 것    무엇이 일어났고 무엇을 교체했는가
                              source_event_id 로 정비의 원인 사건까지 (790건 중 76건)

답하지 못하는 것              누가 무엇을 보고 그렇게 결정했는가
```

`maintenance_event.csv`는 **설비가 남긴 기록**이다. 사람이 화면을 보고 내린 판단은 시스템 밖에서 사라진다.

## 설계 원칙

**1. 판단은 근거를 참조한다.** 판단만 남기면 "그때 무엇을 보고 그렇게 결정했나"에 답할 수 없다. Evidence Package 식별자를 필수로 갖는다.

**2. 판단은 사실을 바꾸지 않는다.** 결정 008(억제 규칙 폐기)의 연장이다. 판단 기록이 등급·확률·후보를 덮어쓰지 않는다. 별도 계층에 쌓인다.

**3. 인과 표현 금지.** 판단 사유에 `원인`·`root cause`를 쓰지 않는다. `연관`·`후보`·`가설`.

**4. 취소하지 않고 덧쓴다.** 잘못된 판단도 이력이다. 삭제 대신 후속 판단으로 대체한다.

---

## DecisionRecord

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `decision_id` | string | ○ | 판단 고유 식별자 |
| `asset_id` | string | ○ | 대상 자산 |
| `observed_at` | datetime | ○ | 판단 대상 사건의 관측 시각 |
| `decided_at` | datetime | ○ | 판단 시각 |
| `decided_by` | string | ○ | 판단자 |
| `role` | string | ○ | 판단 시점의 역할 |
| `decision` | enum | ○ | 아래 참조 |
| `rationale` | string | ○ | 판단 근거 메모. 빈 문자열 불가 |
| `evidence_ref` | object | ○ | 아래 참조 |
| `supersedes` | string \| null | — | 이 판단이 대체하는 이전 `decision_id` |
| `schema_version` | string | ○ | `decision-record-v1` |

### `decision` 허용값

```
continue_operation   계속 운전
request_inspection   점검 요청
review_shutdown      정지 검토
```

세 값으로 제한한다. 자유 문자열을 허용하면 집계가 불가능해지고, 나중에 "판단이 결과로 이어졌나"를 되짚을 수 없다.

### `evidence_ref` — 판단이 무엇을 보고 내려졌는가

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `evidence_id` | string | ○ | Evidence Package 식별자 (`lineage.evidence_id`) |
| `dataset_version` | string | ○ | `lineage.dataset_version` |
| `model_version` | string | ○ | `lineage.model_version` |
| `status_grade` | string | ○ | 판단 시점의 등급 |
| `failure_probability` | number \| null | — | 판단 시점의 확률 |
| `blocks_shown` | array | ○ | 판단자에게 실제로 표시된 리포트 블록 `type` 목록 |

**`blocks_shown`이 핵심이다.** 같은 Evidence라도 화면에 무엇이 표시됐는지에 따라 판단 근거가 다르다. 데이터 부족으로 블록이 생략됐다면 판단자는 그 근거를 보지 못한 것이고, 사후 검토에서 그 사실이 드러나야 한다.

---

## 예시

```json
{
  "schema_version": "decision-record-v1",
  "decision_id": "DEC-20260830-0001",
  "asset_id": "CNC-S01-L01-04",
  "observed_at": "2026-08-29T23:00:00+09:00",
  "decided_at": "2026-08-30T09:14:22+09:00",
  "decided_by": "김현우",
  "role": "process_manager",
  "decision": "request_inspection",
  "rationale": "회전속도 기여도가 2건 연속 상위. 교체 후 2일이라 마모는 후보에서 제외.",
  "evidence_ref": {
    "evidence_id": "RESULT#CNC-S01-L01-04#2026-08-29T23:00:00+09:00",
    "dataset_version": "canonical-ai4i-physics-v3.1",
    "model_version": "independent-logreg-v3.1",
    "status_grade": "critical",
    "failure_probability": 0.610,
    "blocks_shown": ["executive_summary", "component_candidates", "maintenance_status"]
  },
  "supersedes": null
}
```

---

## 저장

`pdm-mvp`는 스키마와 검증만 제공한다. 저장은 프로토타입의 감사 계층이 담당한다.

```
pdm-mvp        DecisionRecord 스키마 · 검증 함수 · 집계 함수
프로토타입      판단 입력 화면 · 권한 검사 · 영속화 · 감사 기록
```

프로토타입에는 이미 `audit_log`와 4단 스코프 검증, 요청 해시 기반 멱등성이 구현돼 있다. 그 위에 얹는다.

## 판단이 무엇을 가능하게 하는가

기록이 쌓이면 세 질문에 답할 수 있다.

| 질문 | 산출 |
|---|---|
| 알람 중 몇 %가 조치로 이어졌나 | `decision` 분포 by `status_grade` |
| 점검 요청한 건이 실제 고장으로 갔나 | `decision` × 후속 `failure_recovery` 정비 |
| 어떤 블록을 본 판단이 더 정확했나 | `blocks_shown` × 결과 |

**세 번째가 이 프로젝트의 평가 기준(근거 추적성)에 직결된다.** 어떤 근거가 실제로 판단 품질을 높였는지 측정할 수 있다.

## 하지 않는 것

- **판단을 자동 생성하지 않는다.** 시스템은 재료를 제시하고 사람이 고른다 (결정 008)
- **판단으로 등급을 덮어쓰지 않는다.** 별도 계층에 쌓는다
- **판단 삭제 없음.** `supersedes`로 대체한다

## 미결

| | 항목 |
|---|---|
| 1 | 킥오프의 "보는 대시보드" 대 "의사결정 인터페이스" — 판단 기록이 이번 범위인지 멘토 확인 |
| 2 | `blocks_shown`을 프로토타입이 실제로 수집할 수 있는지 |
| 3 | 역할별 권한 — 매니저만 판단하는지, 엔지니어도 하는지 |
