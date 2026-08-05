# Azure PdM — 예지보전 Evidence Package

> **기준 데이터셋**: `canonical-ai4i-physics-v3.0`  
> **경로**: `/Users/hb/Downloads/predictive_maintenance_canonical_v3`  
> **데이터셋 결정 근거**: [DATASET_DECISION.md](docs/DATASET_DECISION.md)

## 폴더 구조

| 경로 | 내용 |
|---|---|
| `README.md` | 프로젝트 진입점 |
| `docs/` | W1 공유 문서, 데이터셋 결정, Result Artifact/Evidence Package 계약 |
| `docs/ontology/` | 온톨로지 핵심 문서와 데이터셋 팩 |
| `docs/prompts/` | 이전 작업 지시 프롬프트 보관 |
| `scripts/` | v3 데이터셋 로더와 검증 스크립트 |
| `samples/` | v3 기반 샘플 및 구조 참고용 JSON |
| `archive/` | Azure PdM 원본 CSV와 과거 실험 스크립트 |
| `ontology/` | build_ontology.py 실행 산출물 |
| `cache/` | baseline model 캐시 산출물 |

## 팀원 읽는 순서

| 순서 | 문서 | 내용 |
|------|------|------|
| 1 | **[TEAM_SHARE_W1.md](docs/TEAM_SHARE_W1.md)** | 프로젝트 목표, 현재 상태, 역할 분배, W1 완료 기준 |
| 2 | **[RESULT_ARTIFACT_SCHEMA.md](docs/RESULT_ARTIFACT_SCHEMA.md)** | 산출물 계약 — 대시보드/API/리포트가 공통으로 소비하는 결과 객체 구조 |
| 3 | **[EVIDENCE_PACKAGE_MAPPING.md](docs/EVIDENCE_PACKAGE_MAPPING.md)** | v3 Result Artifact → azure-pdm Evidence Package 필드 매핑표 |
| 4 | **[DATASET_APPLICATION.md](docs/DATASET_APPLICATION.md)** | v3 데이터셋 연결 현황 및 미연결 필드 목록 |
| 5 | **[samples/](samples/)** | v3 기반 JSON 예시 (`*_v3_*.json`) + 구조 참고용 azure-pdm 샘플 |
| 6 | **[EVIDENCE_PACKAGE.md](docs/EVIDENCE_PACKAGE.md)** | Evidence Package 구현 상세 — 함수 구조, 출력 스키마, 설계 결정 |
| 7 | `baseline_model.py`, `report_generator.py` | 구현 코드 |

---

## 온톨로지 문서

- [ONTOLOGY_CORE.md](docs/ontology/ONTOLOGY_CORE.md) — 온톨로지 핵심 개념
- [PACK_AZURE_PDM.md](docs/ontology/PACK_AZURE_PDM.md) — Azure PdM 특화 팩

---

## 빠른 시작

### v3 데이터셋 기반 (권장)

```python
from scripts.load_v3_result_artifacts import build_evidence_packages
from pathlib import Path

V3 = Path("/Users/hb/Downloads/predictive_maintenance_canonical_v3")

# 전체 Evidence Package 로드
packages = build_evidence_packages(V3)

# critical 케이스만
critical = build_evidence_packages(V3, status_filter=["critical", "warning"])

# 특정 장비
ep = build_evidence_packages(V3, asset_id="CMP-S03-L03-01")[0]
```

샘플 출력 (v3 기반):
- `samples/evidence_package_v3_critical.json` — critical 케이스 (CMP-S03-L03-01)
- `samples/result_artifact_v3_critical.json` — Result Artifact 스키마 형태
- `samples/result_artifact_v3_normal.json` — normal 케이스 (CMP-S01-L04-01)

### azure-pdm 원본 데이터 기반 (구조 참고용)

```python
from evidence_package import load_data, compute_global_baseline, generate_evidence_package

tel, errs, fails, maint, mach = load_data('archive/')
baseline = compute_global_baseline(tel)
pkg = generate_evidence_package(5, '2015-09-06 06:00:00', tel, errs, fails, maint, mach, baseline=baseline)
```

---

## 테스트 / 검증

```bash
# azure-pdm 원본 Evidence Package 테스트
python3 -m pytest test_evidence_package.py -q
# → 30 passed

# v3 데이터셋 연결 검증
python3 scripts/validate_v3_dataset.py
# → 전체 PASS (14개 항목)
```

---

## 주의사항

- `cache/` 디렉토리 (~93MB pkl 파일) — git 미포함
- "확정 원인", "root cause" 표현 금지 — "후보", "가설", "연관"으로 표현
- LLM은 Evidence Package를 문장으로 변환하는 보조 계층 (판단 주체 아님)
