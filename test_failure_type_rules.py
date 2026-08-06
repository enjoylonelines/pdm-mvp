"""
고장 유형 규칙 검증.

두 가지를 고정한다.

1. **규칙 자체가 맞는가** — 실제 고장 56건의 유형과 대조한다.
   측정 기준: 실제 유형이 후보에 포함 52/56.

2. **우리 사본이 데이터셋 원본과 같은가** — 상수를 복사해 뒀으므로
   원본이 바뀌면 조용히 어긋난다. 결정 022와 같은 유형의 사고를 막는다.
"""

from __future__ import annotations

import csv
import math
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import failure_type_rules as ftr  # noqa: E402


# 실측 기준선. 규칙을 바꿔 이 값이 떨어지면 시험이 알린다.
EXPECTED_INCLUSION = 52      # 실제 유형이 후보에 포함
EXPECTED_TOTAL = 56          # CNC 고장 건수
EXPECTED_UNMATCHED_MODE = "random_failure"   # 규칙 0개 발동은 전부 이 유형


def _v3() -> Path:
    p = os.environ.get("PDM_CANONICAL_PATH")
    if not p:
        pytest.skip("PDM_CANONICAL_PATH 미설정")
    return Path(p)


# ---------------------------------------------------------------------------
# 단위 — 조건식
# ---------------------------------------------------------------------------

def _row(**kw):
    base = {"torque_nm": 40.0, "rotational_speed_rpm": 1500.0, "tool_wear_min": 100.0,
            "air_temperature_k": 298.0, "process_temperature_k": 308.0}
    base.update(kw)
    return base


def _matched_codes(row, **kw):
    return {m["code"] for m in ftr.evaluate(row, **kw)["matched"]}


def test_twf_boundary_inclusive():
    """공구 마모 200·240분은 구간에 포함된다. 경계 조건."""
    assert "TWF" in _matched_codes(_row(tool_wear_min=200.0))
    assert "TWF" in _matched_codes(_row(tool_wear_min=240.0))
    assert "TWF" not in _matched_codes(_row(tool_wear_min=199.9))
    assert "TWF" not in _matched_codes(_row(tool_wear_min=240.1))


def test_pwf_both_sides():
    """전력은 너무 낮아도 너무 높아도 고장 조건이다."""
    assert "PWF" in _matched_codes(_row(torque_nm=5.0, rotational_speed_rpm=1000.0))
    assert "PWF" in _matched_codes(_row(torque_nm=80.0, rotational_speed_rpm=2000.0))
    assert "PWF" not in _matched_codes(_row(torque_nm=40.0, rotational_speed_rpm=1500.0))


def test_hdf_needs_both_conditions():
    """온도차와 회전수 두 조건을 동시에 만족해야 한다."""
    assert "HDF" in _matched_codes(
        _row(air_temperature_k=300.0, process_temperature_k=308.0, rotational_speed_rpm=1300.0))
    # 온도차만 만족
    assert "HDF" not in _matched_codes(
        _row(air_temperature_k=300.0, process_temperature_k=308.0, rotational_speed_rpm=1400.0))
    # 회전수만 만족
    assert "HDF" not in _matched_codes(
        _row(air_temperature_k=290.0, process_temperature_k=310.0, rotational_speed_rpm=1300.0))


def test_osf_threshold_varies_by_product():
    """과부하 임계는 제품군마다 다르다. 같은 값이 L에서는 걸리고 H에서는 안 걸린다."""
    r = _row(tool_wear_min=250.0, torque_nm=48.0)   # 곱 12,000
    assert "OSF" in _matched_codes(r, product_type="L")       # 11,000
    assert "OSF" not in _matched_codes(r, product_type="H")   # 13,000


def test_unknown_product_type_assumed_and_flagged():
    """제품군을 모르면 중간 등급으로 가정하고, 가정했다는 사실을 표시한다."""
    res = ftr.evaluate(_row(tool_wear_min=250.0, torque_nm=48.0))
    osf = next(c for c in res["candidates"] if c["code"] == "OSF")
    assert osf["threshold"]["product_type_assumed"] is True
    assert osf["threshold"]["limit"] == ftr.OVERSTRAIN_THRESHOLDS["M"]


# ---------------------------------------------------------------------------
# 가용성 — 판정할 수 없을 때 조용히 틀리지 않는다
# ---------------------------------------------------------------------------

def test_missing_measurement_is_not_a_verdict():
    """측정 항목이 없으면 '해당 없음'이 아니라 '판정 불가'다."""
    res = ftr.evaluate({"torque_nm": 40.0})
    assert res["available"] is False
    assert res["conclusion"] is None
    assert res["matched"] == []
    assert "부족" in res["reason"]


def test_compressor_not_supported():
    """압축기는 물리 계약이 다르다. 규칙을 적용하면 안 된다."""
    res = ftr.evaluate(_row(), asset_type="compressor")
    assert res["available"] is False
    assert res["matched"] == []


def test_no_match_is_information_not_failure():
    """조건 미해당은 정상 결과다. 결론이 'none' 으로 명시돼야 한다."""
    res = ftr.evaluate(_row())
    assert res["available"] is True
    assert res["conclusion"] == "none"
    assert "해당하는 항목이 없습니다" in ftr.summary_line(res)


def test_every_candidate_carries_its_evidence():
    """근거 없이 판정만 내놓으면 리포트를 쓸 수 없다."""
    for c in ftr.evaluate(_row(tool_wear_min=210.0))["candidates"]:
        assert c["reason"]
        assert c["measured"]
        assert c["threshold"]


# ---------------------------------------------------------------------------
# 실측 대조 — 실제 고장 56건
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def truth_eval():
    """실제 고장 시점의 측정값으로 규칙을 평가한 결과."""
    v3 = _v3()
    ds, et = v3 / "canonical" / "dataset", v3 / "canonical" / "evaluation_truth"
    truth_csv = et / "cnc_failure_truth.csv"
    if not truth_csv.exists():
        pytest.skip("cnc_failure_truth.csv 없음")

    sensors: dict[str, list[dict]] = {}
    with open(ds / "cnc_sensor_observation.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            sensors.setdefault(r["asset_id"], []).append(r)
    for v in sensors.values():
        v.sort(key=lambda r: r["observed_at"])

    cycles: dict[str, list[tuple[str, str]]] = {}
    with open(ds / "cnc_production_cycle.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            cycles.setdefault(r["cnc_asset_id"], []).append(
                (r["cycle_started_at"], r["product_type"]))
    for v in cycles.values():
        v.sort(key=lambda t: t[0])

    out = []
    with open(truth_csv, newline="", encoding="utf-8") as fh:
        for e in csv.DictReader(fh):
            aid, ft = e["asset_id"], e["failure_occurred_at"]
            rows = [r for r in sensors.get(aid, []) if r["observed_at"] <= ft]
            if not rows:
                continue
            pt = None
            for started, ptype in cycles.get(aid, []):
                if started <= ft:
                    pt = ptype
                else:
                    break
            res = ftr.evaluate(rows[-1], product_type=pt)
            out.append((e["failure_mode"],
                        [m["failure_mode"] for m in res["matched"]]))
    return out


def test_truth_coverage(truth_eval):
    """실제 유형이 후보에 포함되는 비율을 고정한다."""
    assert len(truth_eval) == EXPECTED_TOTAL
    inc = sum(actual in matched for actual, matched in truth_eval)
    assert inc >= EXPECTED_INCLUSION, (
        f"포함 {inc}/{len(truth_eval)} — 기준 {EXPECTED_INCLUSION} 미달"
    )


def test_unmatched_are_only_random_failures(truth_eval):
    """규칙이 못 잡는 것은 물리 조건이 없는 유형뿐이어야 한다.

    다른 유형이 여기 섞이면 규칙 구현이 잘못된 것이다.
    """
    unmatched = {actual for actual, matched in truth_eval if actual not in matched}
    assert unmatched <= {EXPECTED_UNMATCHED_MODE}, (
        f"물리 조건이 있는 유형을 놓쳤습니다: {unmatched - {EXPECTED_UNMATCHED_MODE}}"
    )


def test_conditioned_modes_fully_covered(truth_eval):
    """물리 조건이 정의된 4개 유형은 전건 포착되어야 한다."""
    for actual, matched in truth_eval:
        if actual == EXPECTED_UNMATCHED_MODE:
            continue
        assert actual in matched, f"{actual} 미포착"


# ---------------------------------------------------------------------------
# 드리프트 — 우리 사본이 데이터셋 원본과 같은가
# ---------------------------------------------------------------------------

def test_matches_dataset_contract():
    """상수와 조건식이 데이터셋 `scripts/ai4i_contract.py` 와 일치해야 한다.

    사본을 두는 이유는 데이터셋 경로 의존을 피하기 위해서다. 대신 원본이
    바뀌면 여기서 잡는다. 조용히 어긋나는 것이 가장 위험하다.
    """
    v3 = _v3()
    contract = v3 / "scripts" / "ai4i_contract.py"
    if not contract.exists():
        pytest.skip("ai4i_contract.py 없음")

    sys.path.insert(0, str(v3 / "scripts"))
    try:
        import ai4i_contract as src
    finally:
        sys.path.pop(0)

    for name in ("POWER_LOW_W", "POWER_HIGH_W", "HDF_TEMPERATURE_GAP_MAX_K",
                 "HDF_RPM_MAX", "TWF_WEAR_MIN", "TWF_WEAR_MAX",
                 "OVERSTRAIN_THRESHOLDS"):
        assert getattr(ftr, name) == getattr(src, name), f"{name} 불일치"

    # 조건식도 대조한다. 상수만 같고 식이 달라질 수 있다.
    import random
    rng = random.Random(42)
    for _ in range(400):
        row = {
            "torque_nm": rng.uniform(3, 80),
            "rotational_speed_rpm": rng.uniform(1100, 2900),
            "tool_wear_min": rng.uniform(0, 260),
            "air_temperature_k": rng.uniform(295, 305),
            "process_temperature_k": rng.uniform(305, 315),
        }
        for pt in ("L", "M", "H"):
            row["product_type"] = pt
            ours = {m["code"] for m in ftr.evaluate(row, product_type=pt)["matched"]}
            theirs = {c for c in ("PWF", "HDF", "OSF", "TWF")
                      if src.failure_condition(c, row)}
            assert ours == theirs, f"조건식 불일치 {row} → 우리 {ours} / 원본 {theirs}"


def test_power_formula():
    """동력 = 토크 × 각속도. 단위 환산이 틀리면 전체가 어긋난다."""
    assert ftr.power_w(40.0, 1500.0) == pytest.approx(40.0 * 1500.0 * 2 * math.pi / 60.0)
