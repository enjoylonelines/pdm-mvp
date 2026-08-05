# 작업 지시 — 행동 계층 구현

## 이 작업의 목적

**기능 추가가 아니라 검증이다.**

결정 006「온톨로지 미채택」은 세 조건이 충족되지 않는다고 판정했다. 그중 하나가 이것이다.

> 권한 제어(ReBAC) 성립 안 함 — 역할 3개가 같은 데이터를 봄. 차이는 접근 권한이 아니라 표현 방식

이 판정은 **조회만 보고 내려진 것**이다. 행동이 들어오면 참인지 거짓인지가 달라진다. 이 작업은 최소 구현으로 그것을 실측한다.

산출물은 화면이 아니라 **9장의 검증 결과 5건**이다. 통과하든 실패하든 결정 006에 `measured` 근거가 붙는다.

따라서 **범위를 늘리지 말 것.** 아래 명시된 것만 만든다.

---

## 선행 조건

`PROMPT_build_ontology.md`에 따른 `build_ontology.py`가 완료되어 정적 객체·링크가 생성돼 있어야 한다. 없으면 그것부터 수행한다.

사양은 `ONTOLOGY_CORE.md`와 `PACK_AZURE_PDM.md`를 따른다. 두 문서와 어긋나면 문서가 우선이며, 문서가 틀렸다고 판단되면 **코드를 고치기 전에 보고**한다.

---

## 만들 것

```
store.py                SQLite 4테이블 · 스키마와 접근 함수        ~120줄
action_registry.py      액션 4종 · 권한 매핑 정의                  ~80줄
action_service.py       실행기 (권한 → 검증 → 멱등 → 감사)        ~200줄
test_action_layer.py    검증 5종 자동 테스트                       ~180줄
manager_app.py          수정 — 역할 전환 · 액션 버튼 · 입력 폼     ~120줄 추가
engineer_app.py         신규 — 배정 작업 목록 · 완료 처리          ~150줄
```

합계 약 850줄.

---

## 1. 액션 4종

`ONTOLOGY_CORE.md` 4장에 8종이 정의돼 있으나, **이번 범위는 4종이다.** 나머지 4종(`assign_work_order`, `record_work_order_note`, `mark_work_order_blocked`, `request_grade_review`)은 정의만 두고 구현하지 않는다.

| 액션 | 대상 | 필수 파라미터 | 선택 파라미터 | 권한 |
|---|---|---|---|---|
| `acknowledge_risk_event` | RiskEvent | — | — | `events.acknowledge` |
| `record_operational_decision` | RiskEvent | `decision`, `rationale` | — | `events.decision` |
| `create_work_order` | RiskEvent | `subsystem_ref`, `work_type` | `due_at` | `workorders.create` |
| `complete_work_order` | WorkOrder | `checklist` | `measurements`, `note` | `workorders.complete` |

이 넷을 고른 이유는 **사슬이 완결되고 권한 대비가 드러나기 때문**이다.

```
확인(공통) → 판단(매니저) → 지시(매니저) → 완료(엔지니어)
```

`decision` 허용값은 `계속 운전` / `점검 요청` / `정지 검토` 셋으로 제한한다.
`work_type` 허용값은 `점검` / `교체` / `정밀진단` 셋으로 제한한다.

## 2. 권한 모델

```python
ROLE_PERMISSIONS = {
    "process_manager":  {"events.acknowledge", "events.decision", "workorders.create"},
    "process_engineer": {"events.acknowledge", "workorders.complete"},
}
```

**매니저는 지시하고 엔지니어는 수행한다.** 두 역할이 겹치는 권한은 `events.acknowledge` 하나뿐이며, 이 비대칭이 검증의 핵심이다.

역할은 둘만 만든다. 킥오프의 세 번째 역할(데이터분석가)은 이번 범위 밖이다.

## 3. 저장소

SQLite 파일 하나. 테이블 넷.

```sql
CREATE TABLE ontology_objects (
    id            TEXT PRIMARY KEY,
    object_type   TEXT NOT NULL,
    properties    TEXT NOT NULL,          -- JSON
    created_at    TEXT NOT NULL
);

CREATE TABLE ontology_links (
    id            TEXT PRIMARY KEY,
    link_type     TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL
);

CREATE TABLE action_invocations (
    id                TEXT PRIMARY KEY,
    idempotency_key   TEXT NOT NULL UNIQUE,
    action_type       TEXT NOT NULL,
    object_id         TEXT NOT NULL,
    actor             TEXT NOT NULL,
    actor_role        TEXT NOT NULL,
    request_hash      TEXT NOT NULL,
    state             TEXT NOT NULL,      -- reserved | succeeded | failed
    result            TEXT,               -- JSON
    audit_id          TEXT,
    created_at        TEXT NOT NULL,
    completed_at      TEXT
);

CREATE TABLE audit_log (
    id            TEXT PRIMARY KEY,
    invocation_id TEXT NOT NULL,
    action_type   TEXT NOT NULL,
    object_id     TEXT NOT NULL,
    actor         TEXT NOT NULL,
    actor_role    TEXT NOT NULL,
    parameters    TEXT NOT NULL,          -- JSON
    result        TEXT,                   -- JSON
    created_at    TEXT NOT NULL
);
```

`RiskEvent`, `WorkOrder`, `OperationalDecision`은 `ontology_objects`에 `object_type`으로 구분해 저장한다. 별도 테이블을 만들지 않는다.

## 4. 실행기 파이프라인

`action_service.invoke(invocation, actor, actor_role)`의 처리 순서다.

```
1  액션 타입이 레지스트리에 등록돼 있는가        → 없으면 UnknownAction
2  권한 검사 (ROLE_PERMISSIONS)                  → 없으면 PermissionDenied
3  대상 객체가 존재하고 object_type이 일치하는가  → 아니면 InvalidTarget
4  파라미터 검증 — 미등록 · 필수 누락 · 타입 · 허용값
5  요청 정규화 → SHA-256 해시
6  멱등 예약 — idempotency_key 로 INSERT 시도
     이미 존재 + 해시 동일  → replay 반환 (재실행 안 함)
     이미 존재 + 해시 상이  → IdempotencyConflict
7  실행 — 객체 생성·상태 변경
8  감사 기록 — audit_log INSERT
9  invocation 완료 처리 (succeeded + audit_id)
   실패 시 failed 로 기록하고 예외 재발생
```

**감사 기록은 실행과 같은 트랜잭션이어야 한다.** 실행됐는데 감사가 없는 상태가 생기면 안 된다.

각 단계의 예외는 구분 가능한 타입으로 던진다. 테스트가 종류를 확인한다.

## 5. UI 요구사항

### 역할 전환

`st.selectbox`로 `매니저(김현우)` / `엔지니어(박지민)` 전환. **진짜 로그인·비밀번호·세션은 만들지 않는다.**

### 중요 — UI 숨김과 서버 거부는 별개다

- **UI**: 권한 없는 액션은 버튼을 표시하지 않는다 (정상 UX)
- **서비스**: 권한 없는 호출이 들어오면 `PermissionDenied`로 거부한다

**둘 다 있어야 한다.** UI가 숨기는 것만으로는 권한 경계가 아니다. 검증 2번은 서비스 계층을 직접 호출해 확인한다.

### 매니저 화면 (`manager_app.py` 수정)

기존 5개 섹션 아래에 행동 영역을 추가한다.

```
[확인함]  버튼
판단 기록  — decision 라디오 3택 + rationale 텍스트
작업 지시  — subsystem 선택 + work_type 3택 + due_at
```

기록 후 같은 화면에 **행동 이력**을 표시한다. 누가 언제 무엇을 했는지, 그리고 그때 참조한 Evidence로 되짚을 수 있어야 한다.

### 엔지니어 화면 (`engineer_app.py` 신규)

```
배정된 작업 지시 목록  — 상태 · 대상 계통 · 기한 · 생성 근거 RiskEvent
완료 처리             — checklist + measurements + note
```

엔지니어 화면에는 **판단 기록·작업 지시 버튼이 나타나지 않아야 한다.**

기존 `report_generator.py`의 `_ENGINEER_PIPELINE` 8블록을 리포트 영역에 그대로 사용한다. 새로 만들지 않는다.

---

## 6. 검증 5종 — 이것이 산출물이다

`test_action_layer.py`에 자동 테스트로 작성한다. 결과를 `ACTION_LAYER_VALIDATION.md`에 기록한다.

| # | 검증 | 통과 조건 |
|---|---|---|
| 1 | **감사가 남는가** | 매니저로 `record_operational_decision` 실행 → `audit_log`에 actor · actor_role · parameters · created_at 이 기록됨 |
| 2 | **권한이 실제 경계인가** | 엔지니어 역할로 같은 액션 호출 → `PermissionDenied`. 반대로 매니저로 `complete_work_order` → `PermissionDenied` |
| 3 | **멱등한가** | 같은 `idempotency_key`로 동일 요청 재호출 → 재실행 없이 replay. 같은 키·다른 파라미터 → `IdempotencyConflict` |
| 4 | **사슬이 이어지는가** | `OperationalDecision` → `evidence_ref` → `Evidence` → `RiskEvent` → `Asset` 을 링크만으로 순회 가능 |
| 5 | **카탈로그 밖을 막는가** | 미등록 `action_type` → `UnknownAction`. 미등록 파라미터 → 거부. 필수 누락 → 거부. `decision` 허용값 밖 → 거부 |

**2번이 이 작업의 핵심이다.** 통과하면 결정 006의 세 번째 조건이 실측으로 반증된다.

`ACTION_LAYER_VALIDATION.md`에는 각 항목의 통과 여부와 함께, **실패한 항목이 있다면 그 원인이 구현 문제인지 설계 문제인지** 구분해 기록한다.

---

## 7. 제약

1. **기존 순수 함수를 수정하지 말 것.** `evidence_package.py`, `report_generator.py`, `z_baseline.py`는 그대로 둔다. 상태는 `store.py`와 `action_service.py`에만 존재한다.
2. **인과 표현 금지.** 기존 원칙 유지. `원인`, `cause`, `때문` 금지. `연관`, `후보`, `associated` 사용.
3. **의존성은 표준 라이브러리 + pandas + streamlit + pytest.** ORM·마이그레이션 도구·인증 라이브러리를 추가하지 않는다.
4. **RiskEvent는 조회 시 생성하되 액션 대상이 되면 영속화한다.** 확인·판단·지시 중 하나라도 발생하면 `ontology_objects`에 저장한다.
5. 모든 시각은 ISO 8601 UTC 문자열로 저장한다.

## 8. 하지 말 것

- 인증·비밀번호·세션·쿠키 구현
- 조직·프로젝트 다중 테넌시 (workspace 하나로 충분)
- 액션 8종 전부 구현 (4종만)
- 세 번째 역할(데이터분석가)
- React·FastAPI 도입 (Streamlit 유지)
- 텔레메트리 876,100행 재처리
- 프로토타입 저장소 코드 복사 — 참고는 하되 구조를 그대로 가져오지 말 것. 그쪽은 4단 스코프 검증까지 있어 이 범위에 과하다

## 9. 완료 기준

1. `pytest test_action_layer.py` 가 5종 전부 실행되고 결과가 명확히 나옴
2. `ACTION_LAYER_VALIDATION.md`에 5종 결과와 판정이 기록됨
3. 매니저 화면에서 확인 → 판단 → 지시 한 사이클이 동작하고 이력이 남음
4. 엔지니어 화면에서 해당 지시가 보이고 완료 처리가 동작함
5. 엔지니어 화면에 판단·지시 버튼이 **나타나지 않음**
6. 두 번 실행해도 같은 결과 (멱등)
7. 기존 `pytest` 30건이 여전히 통과

## 10. 참고 파일

```
ONTOLOGY_CORE.md            객체·액션·권한 사양 — 이것이 기준
PACK_AZURE_PDM.md           도메인 매핑
PROMPT_build_ontology.md    선행 작업 지시
EVIDENCE_PACKAGE.md         설계 원칙과 문서화 스타일
evidence_package.py         순수 함수 스타일 참고 · 수정 금지
report_generator.py         _ENGINEER_PIPELINE 재사용 · 수정 금지
manager_app.py              수정 대상
docs/decisions.md           결정 006 원문 (../docs/)
```

---

## 보고 요청

작업 중 다음이 발견되면 **구현을 계속하지 말고 먼저 보고**한다.

- `ONTOLOGY_CORE.md`의 정의로 4종 액션을 표현할 수 없는 경우
- 검증 5종 중 하나가 **설계 문제로** 통과 불가능한 경우
- 액션 대상을 단일 `object_type`으로 지정할 수 없는 경우 (다형성 필요 신호)

마지막 항목은 특히 중요하다. Azure PdM은 계통(Subsystem) 단위로 정비하고 AI4I는 설비(Asset) 단위로 정비하므로, `complete_work_order`의 대상이 도메인마다 달라질 수 있다. 이 문제가 실제로 발생하면 **결정 006의 첫 번째 조건(다형적 엣지)에 대한 실측 근거**가 되므로 반드시 기록한다.
