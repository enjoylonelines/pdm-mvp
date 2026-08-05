# PdM MVP — 예지보전 Evidence Package

> **기준 데이터셋**: `canonical-ai4i-physics-v3.1`
> **결정 근거**: [DATASET_DECISION.md](docs/DATASET_DECISION.md) · 결정 로그 020

이상탐지 결과를 역할별 판단 언어로 번역하되, 화면의 모든 문장이 원본 데이터로 되짚어지게 하는 것이 목표다. 예측 모델이 아니라 **의사결정 인터페이스**다(결정 000).

## 데이터셋 경로 설정

절대 경로를 코드에 넣지 않는다. 환경변수 또는 CLI 인자로 받는다.

```bash
export PDM_CANONICAL_PATH=~/Downloads/predictive_maintenance_canonical_v3.1
```

미설정 시 기본값은 `~/Downloads/predictive_maintenance_canonical_v3.1`이다.

> **주의** — 배포 기준본은 `predictive_maintenance_canonical_v3.1/`이다.
> 같은 이름의 `_v3/` 폴더는 과거 작업 경로가 섞여 있어 기준본이 아니다.

## 폴더 구조

| 경로 | 내용 |
|---|---|
| `README.md` | 프로젝트 진입점 |
| `docs/` | W1 공유 문서, 데이터셋 결정, Result Artifact / Evidence Package 계약 |
| `docs/ontology/` | 온톨로지 설계 문서와 데이터셋 팩 |
| `docs/prompts/` | 작업 지시 프롬프트 보관 |
| `scripts/` | canonical 데이터셋 로더와 검증 스크립트 |
| `samples/` | 샘플 JSON |
| `ontology/` | `build_ontology.py` 실행 산출물 |
| `archive/` | **Azure PdM 원본 CSV와 과거 실험 스크립트 (결정 004, superseded)** |
| `cache/` | baseline model 캐시 (git 미포함) |

## 팀원 읽는 순서

| 순서 | 문서 | 내용 |
|---|---|---|
| 1 | **[TEAM_SHARE_W1.md](docs/TEAM_SHARE_W1.md)** | 프로젝트 목표, 현재 상태, 역할 분배, W1 완료 기준 |
| 2 | **[RESULT_ARTIFACT_SCHEMA.md](docs/RESULT_ARTIFACT_SCHEMA.md)** | 산출물 계약 — 대시보드·API·리포트가 공통으로 소비하는 결과 객체 구조 |
| 3 | **[EVIDENCE_PACKAGE_MAPPING.md](docs/EVIDENCE_PACKAGE_MAPPING.md)** | Result Artifact → Evidence Package 필드 매핑 |
| 4 | **[DATASET_APPLICATION.md](docs/DATASET_APPLICATION.md)** | 데이터셋 연결 현황과 미연결 필드 |
| 5 | **[samples/](samples/)** | JSON 예시 |
| 6 | **[EVIDENCE_PACKAGE.md](docs/EVIDENCE_PACKAGE.md)** | Evidence Package 구현 상세 — 함수 구조, 출력 스키마, 설계 결정 |
| 7 | `report_generator.py` · `manager_app.py` | 구현 코드 |

## 온톨로지 문서

- [ONTOLOGY_CORE.md](docs/ontology/ONTOLOGY_CORE.md) — 도메인 중립 코어
- [PACK_AZURE_PDM.md](docs/ontology/PACK_AZURE_PDM.md) — Azure PdM 매핑 (결정 004 기준, superseded)
- [PACK_AI4I.md](docs/ontology/PACK_AI4I.md) — AI4I 매핑 (중립성 검증용)

> 온톨로지는 **미채택 상태**다(결정 006). 설계 문서는 채택 조건 검토 근거로 보존한다.

---

## 빠른 시작

### canonical v3.1 기반 (기준)

```python
from pathlib import Path
import os

from scripts.load_v3_result_artifacts import build_evidence_packages

V3 = Path(os.environ.get(
    "PDM_CANONICAL_PATH",
    Path.home() / "Downloads" / "predictive_maintenance_canonical_v3.1",
))

packages = build_evidence_packages(V3)
critical = build_evidence_packages(V3, status_filter=["critical", "warning"])
one      = build_evidence_packages(V3, asset_id="CMP-S03-L03-01")[0]
```

샘플 출력:

- `samples/evidence_package_v3_critical.json` — critical 케이스 (CMP-S03-L03-01)
- `samples/result_artifact_v3_critical.json` — Result Artifact 스키마 형태
- `samples/result_artifact_v3_normal.json` — normal 케이스 (CMP-S01-L04-01)

### Azure PdM 원본 기반 (구조 참고용, superseded)

결정 004 시기의 자산이다. v3.1 전환(결정 020) 이후 기준이 아니지만, Evidence Package 구조와 판정 규칙의 참조 구현으로 보존한다.

```python
from evidence_package import load_data, compute_global_baseline, generate_evidence_package

tel, errs, fails, maint, mach = load_data('archive/')
baseline = compute_global_baseline(tel)
pkg = generate_evidence_package(
    5, '2015-09-06 06:00:00', tel, errs, fails, maint, mach, baseline=baseline
)
```

---

## 테스트 / 검증

```bash
# 전체
python3 -m pytest -q
# → 47 passed

# 정비 유형 판정 규칙 (결정 019)
python3 -m pytest -q test_maintenance_rules.py
# → 17 passed

# canonical 데이터셋 연결 검증
python3 scripts/validate_v3_dataset.py
```

---

## 원칙

- **인과 표현 금지** — "확정 원인", "root cause" 대신 `후보` · `가설` · `연관`
- **표본 부족 시 판단 보류** — 근거가 부족하면 수치를 제시하지 않고 부족함을 표시
- **모든 수치에 산출 근거** — 어떤 기준, 몇 건 중 몇 건인지 함께 담는다 (결정 002)
- **LLM은 문장 표현에만** — 판단 로직에 넣지 않는다 (결정 000)
- `cache/`는 git 미포함
