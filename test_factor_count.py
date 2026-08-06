"""
기여 변수 개수(top_factors)에 의존하지 않는지 확인한다.

현재 데이터셋(canonical v3.1)은 Top-3 고정이다. 생성 파이프라인과 검증
스크립트 양쪽에 3이 못 박혀 있어 우리 쪽에서 늘릴 수 없다.

그러나 개수는 **데이터셋이 정하는 값**이고 앞으로 바뀔 수 있다. 우리 코드가
3을 전제하고 있으면 그때 조용히 깨진다. 이 테스트는 개수를 1·3·4·6으로
바꿔가며 변환과 리포트 생성이 모두 성립하는지, 그리고 **잘리지 않는지**를
고정한다.

관련: policy.MAX_COMPONENTS_DETAILED (표시 개수 — 데이터 개수와 별개)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "scripts"))

import policy  # noqa: E402
from load_v3_result_artifacts import result_artifact_to_evidence_package  # noqa: E402
from report_generator import generate_role_reports  # noqa: E402


CURRENT_DATASET_FACTOR_COUNT = 3


def _factor(i: int, direction: str = "risk_up") -> dict:
    """i번째 기여 변수. 값은 의미 없고 개수만 본다."""
    return {
        "rank": i,
        "feature": f"sensor_{i}_6h_mean",
        "feature_value": 1.0 + i,
        "signed_contribution": (0.5 - 0.05 * i) * (1 if direction == "risk_up" else -1),
        "direction": direction,
        "explanation_method": "linear_logit_contribution",
    }


def _artifact(n_factors: int, n_risk_up: int | None = None) -> dict:
    """기여 변수 n개짜리 결과 산출물. 앞의 n_risk_up개만 위험 상승 방향."""
    if n_risk_up is None:
        n_risk_up = n_factors
    return {
        "artifact_id": "ART-TEST-001",
        "asset_id": "CNC-S01-L01-01",
        "asset_type": "cnc",
        "observed_at": "2026-08-29T23:00:00+09:00",
        "failure_probability": 0.71,
        "confidence": 0.42,
        "status_grade": "warning",
        "predicted_failure_type": "failure_risk",
        "prediction_horizon_hours": 24,
        "prediction_task": "binary_failure_within_horizon",
        "schema_version": "result-artifact-v1.0",
        "artifact_type": "predictive_maintenance_result",
        "recommended_action": {
            "action": "inspect_within_current_shift",
            "priority": "high",
        },
        "provenance": {
            "dataset_version": "canonical-ai4i-physics-v3.1",
            "model_version": "independent-logreg-v3.1",
            "source_type": "derived_result_artifact",
        },
        "top_factors": [
            _factor(i + 1, "risk_up" if i < n_risk_up else "risk_down")
            for i in range(n_factors)
        ],
    }


# ---------------------------------------------------------------------------
# 변환 — 개수를 그대로 보존해야 한다
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 3, 4, 6])
def test_top_factors_not_truncated(n):
    """어댑터가 기여 변수를 잘라내면 안 된다."""
    pkg = result_artifact_to_evidence_package(_artifact(n))
    assert len(pkg["top_factors"]) == n


@pytest.mark.parametrize("n", [1, 3, 4, 6])
def test_hypotheses_follow_risk_up_count(n):
    """이상 후보는 위험 상승 방향 개수를 그대로 따라간다."""
    pkg = result_artifact_to_evidence_package(_artifact(n))
    assert len(pkg["component_hypotheses"]) == n


def test_risk_down_excluded_regardless_of_count():
    """위험 하락 방향은 개수와 무관하게 후보에서 빠진다."""
    pkg = result_artifact_to_evidence_package(_artifact(6, n_risk_up=2))
    assert len(pkg["top_factors"]) == 6
    assert len(pkg["component_hypotheses"]) == 2


# ---------------------------------------------------------------------------
# 리포트 — 개수가 달라져도 생성되고, 모든 후보가 문장에 나와야 한다
#
# 리포트는 센서 관측이 있어야 본문을 만든다. 관측이 없으면 "데이터 부족"으로
# 끝나 개수를 확인할 수 없다. 그래서 실제 데이터셋 패키지를 바탕으로 기여 변수만
# 갈아 끼운다.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def base_pkg():
    """실제 데이터셋에서 만든 온전한 패키지 1건."""
    import os

    from load_v3_result_artifacts import build_evidence_packages

    v3 = os.environ.get("PDM_CANONICAL_PATH")
    if not v3:
        pytest.skip("PDM_CANONICAL_PATH 미설정")
    pkgs = build_evidence_packages(Path(v3))
    for p in pkgs:
        if not p["status_flags"].get("insufficient_data"):
            return p
    pytest.skip("관측이 충분한 패키지가 없다")


def _with_factors(pkg: dict, n: int) -> dict:
    """패키지의 기여 변수를 n개로 갈아 끼운다. 나머지는 그대로 둔다."""
    out = dict(pkg)
    factors = [_factor(i + 1) for i in range(n)]
    out["top_factors"] = factors
    out["component_hypotheses"] = [
        {
            "feature": f["feature"],
            "feature_value": f["feature_value"],
            "signed_contribution": f["signed_contribution"],
            "direction": f["direction"],
            "explanation_method": f["explanation_method"],
            "association": f"feature_anomaly_risk_up_candidate_rank{f['rank']}",
        }
        for f in factors
    ]
    flags = dict(out["status_flags"])
    flags["multiple_candidates"] = n >= 2
    out["status_flags"] = flags
    return out


@pytest.mark.parametrize("n", [1, 3, 4, 6])
def test_report_generates_for_any_count(base_pkg, n):
    reports = generate_role_reports(_with_factors(base_pkg, n))
    assert reports, f"기여 변수 {n}개에서 리포트가 비었다"


@pytest.mark.parametrize("n", [1, 3, 4, 6])
def test_every_factor_appears_in_report(base_pkg, n):
    """N을 늘렸는데 리포트가 3개까지만 보여주면 늘린 의미가 없다."""
    pkg = _with_factors(base_pkg, n)
    text = str(generate_role_reports(pkg))
    for f in pkg["top_factors"]:
        assert f["feature"] in text, (
            f"기여 변수 {f['feature']} 가 리포트에 없다 (총 {n}개)"
        )


def test_candidate_count_stated_matches_actual(base_pkg):
    """'N건' 문구가 실제 개수와 어긋나면 안 된다. 하드코딩 방지."""
    text = str(generate_role_reports(_with_factors(base_pkg, 5)))
    assert "5건" in text
    assert "3건: " not in text


# ---------------------------------------------------------------------------
# 현재 데이터셋 고정 — 규격이 바뀌면 알아차리도록
# ---------------------------------------------------------------------------

def test_current_dataset_is_top3():
    """canonical v3.1 은 Top-3 이다.

    이 테스트가 깨지면 데이터셋 규격이 바뀐 것이다. 실패 자체가 문제는 아니고,
    바뀐 사실을 알아차리라는 신호다. 위 테스트들이 통과한다면 코드는 그대로
    따라간다.
    """
    import json
    import os

    v3 = os.environ.get("PDM_CANONICAL_PATH")
    if not v3:
        pytest.skip("PDM_CANONICAL_PATH 미설정")
    p = Path(v3) / "canonical" / "model_outputs" / "result_artifact.jsonl"
    if not p.exists():
        pytest.skip(f"{p} 없음")
    counts = {len(json.loads(l)["top_factors"]) for l in p.open()}
    assert counts == {CURRENT_DATASET_FACTOR_COUNT}, (
        f"데이터셋 기여 변수 개수가 {counts} 로 바뀌었다. "
        f"policy.py 의 설명과 이 상수를 갱신할 것."
    )


def test_display_limit_is_ours_not_datasets():
    """표시 개수는 화면 결정이다. 데이터 개수와 분리되어 있어야 한다."""
    assert isinstance(policy.MAX_COMPONENTS_DETAILED, int)
    assert policy.MAX_COMPONENTS_DETAILED >= 1
