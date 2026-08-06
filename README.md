# PdM MVP — 핵심 결과물

> 기준 데이터셋: `canonical-ai4i-physics-v3.1`  
> 핵심 입력: `canonical/model_outputs/result_artifact.jsonl`

이 저장소는 예측 모델을 새로 튜닝하는 프로젝트가 아니다. 모델이 산출한
예지보전 결과를 역할별 판단 언어로 바꾸고, 모든 문장이 데이터 근거로
되짚히도록 만드는 **의사결정 인터페이스 계약**이다.

## 핵심 흐름

```text
canonical v3.1 result_artifact.jsonl
  -> scripts/load_v3_result_artifacts.py
  -> Evidence Package 형태
  -> report_generator.py
  -> 매니저 / 엔지니어 리포트 블록
```

화면, 권한, 감사, 출력 레이아웃은 별도 프로토타입이 담당한다. 이 저장소는
그 화면이 소비할 데이터 계약, 근거 패키지, 리포트 블록만 보유한다.

## 남긴 핵심 파일

| 경로 | 역할 |
|---|---|
| `docs/DATASET_DECISION.md` | canonical v3.1 채택 근거와 사용 정책 |
| `docs/DATASET_APPLICATION.md` | v3 연결 현황과 남은 통합 작업 |
| `docs/RESULT_ARTIFACT_SCHEMA.md` | 대시보드/API/리포트 공통 결과 객체 |
| `docs/EVIDENCE_PACKAGE_MAPPING.md` | Result Artifact -> Evidence Package 매핑 |
| `docs/TEAM_SHARE_W1.md` | 팀 공유용 W1 요약 |
| `scripts/load_v3_result_artifacts.py` | v3 Result Artifact 로더/어댑터 |
| `scripts/validate_v3_dataset.py` | 데이터셋 경로, 버전, 필수 파일 검증 |
| `report_generator.py` | 역할별 리포트 블록 생성 |
| `policy.py` | 등급, 표본 기준, 데이터셋 종속 측정값 |
| `failure_type_rules.py` | 센서값 기반 고장 유형 후보 규칙 |
| `samples/` | v3 기반 샘플 JSON |
| `test_*.py` | 핵심 계약 회귀 테스트 |

과거 Azure PdM 원본 CSV, baseline 모델, 캐시, 참조 Streamlit 화면, 온톨로지
검토 산출물은 `archive/` 아래에 보존했다. 공유 시 핵심만 보낼 경우
`archive/`는 제외해도 된다.

## 데이터셋 경로

절대 경로를 코드에 넣지 않는다. 환경변수 또는 CLI 인자로 받는다.

```bash
export PDM_CANONICAL_PATH=~/Downloads/predictive_maintenance_canonical_v3.1
```

미설정 시 기본값은 `~/Downloads/predictive_maintenance_canonical_v3.1`이다.

## 빠른 시작

```python
from pathlib import Path
import os

from scripts.load_v3_result_artifacts import build_evidence_packages

v3_path = Path(os.environ.get(
    "PDM_CANONICAL_PATH",
    Path.home() / "Downloads" / "predictive_maintenance_canonical_v3.1",
))

packages = build_evidence_packages(v3_path)
critical = build_evidence_packages(v3_path, status_filter=["critical"])
one = build_evidence_packages(v3_path, asset_id="CMP-S03-L03-01")[0]
```

샘플:

- `samples/evidence_package_v3_critical.json`
- `samples/result_artifact_v3_critical.json`
- `samples/result_artifact_v3_normal.json`

## 검증

```bash
python3 -m pytest -q
python3 scripts/validate_v3_dataset.py
```

## 원칙

- `evaluation_truth`는 평가 전용이다. 대시보드, API, LLM, Evidence Package
  입력으로 사용하지 않는다.
- LLM은 판단 로직이 아니라 문장 표현에만 사용한다.
- "확정 원인", "root cause" 대신 `후보`, `가설`, `연관`, `근거`를 쓴다.
- 비용, 권한, 알림, 온톨로지 전체 구조는 핵심 범위가 아니라 별도
  프로토타입/확장 범위로 둔다.
