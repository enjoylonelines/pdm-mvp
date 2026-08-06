"""
고장 유형 판정 규칙 — 학습 없이 측정값에서 유도한다.

## 왜 규칙인가

제공된 예측 모델은 "24시간 내 고장 여부"만 내놓는 이진 분류기다. 유형은
`failure_risk` / `no_significant_risk` 2값이라 어느 유형인지 알 수 없다.

그런데 AI4I 계열 고장 유형은 **현재 측정값에 대한 결정론적 조건**이다.
학습이 필요 없다. 조건을 그대로 평가하면 된다.

고장 56건 대조 결과 (canonical v3.1, measured):

    실제 유형이 후보에 포함    52/56  (93%)
    단독 정확 판정             43/56  (77%)
    규칙 0개 발동               4/56  ← 전부 random_failure

무작위 고장은 물리 조건이 없으므로 어떤 규칙도 발동하지 않는 것이 정상이다.
0개 발동은 실패가 아니라 "알려진 조건에 해당하지 않음"이라는 정보다.

## 모델 출력이 아니다

데이터셋 규격은 *이진 모델의* `predicted_failure_type`을 multiclass 예측으로
표현하는 것을 금지한다(ARCHITECTURE_DECISION.md 금지 사항). 이 모듈이 만드는
값은 모델 출력이 아니라 canonical 센서 값에서 규칙으로 유도한 별도 필드이며,
`component_hypotheses` 와 같은 계열이다. 리포트에서도 모델 예측과 구분해 쓴다.

또한 생성기 내부 `failure_schedule`(어느 설비가 언제 고장날지)은 쓰지 않는다.
쓰는 것은 공개된 물리 조건뿐이다.

## 출처

상수와 조건식은 데이터셋 패키지 `scripts/ai4i_contract.py` 와 같아야 한다.
우리 저장소가 사본을 보유하는 이유는 데이터셋 경로에 의존하지 않기 위해서다.
사본이 어긋나면 `test_failure_type_rules.py::test_matches_dataset_contract` 가
실패한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional


# ── AI4I 물리 상수 (출처: 데이터셋 scripts/ai4i_contract.py) ────────────────
POWER_LOW_W = 3500.0
POWER_HIGH_W = 9000.0
HDF_TEMPERATURE_GAP_MAX_K = 8.6
HDF_RPM_MAX = 1380.0
TWF_WEAR_MIN = 200.0
TWF_WEAR_MAX = 240.0
OVERSTRAIN_THRESHOLDS = {"L": 11000.0, "M": 12000.0, "H": 13000.0}
DEFAULT_PRODUCT_TYPE = "M"

# 규칙이 필요로 하는 측정 항목. 하나라도 없으면 판정하지 않는다.
REQUIRED_FIELDS = (
    "torque_nm",
    "rotational_speed_rpm",
    "tool_wear_min",
    "air_temperature_k",
    "process_temperature_k",
)

# 규칙으로 판정할 수 없는 유형. 물리 조건이 없다.
UNCONDITIONED = ("RNF",)

TYPE_META = {
    "PWF": ("power_failure",            "전력 이상"),
    "HDF": ("heat_dissipation_failure", "방열 불량"),
    "OSF": ("overstrain_failure",       "과부하"),
    "TWF": ("tool_wear_failure",        "공구 마모"),
}

# 이 규칙은 절삭기(AI4I 계열)에만 적용된다. 압축기는 물리 계약이 다르다.
SUPPORTED_ASSET_TYPES = ("cnc",)


def power_w(torque_nm: float, rotational_speed_rpm: float) -> float:
    """회전 동력 [W]. 토크[N·m] × 각속도[rad/s]."""
    return torque_nm * rotational_speed_rpm * 2.0 * math.pi / 60.0


def overstrain_threshold(product_type: Optional[str]) -> float:
    """제품군별 과부하 임계. 제품군을 모르면 중간 등급으로 둔다."""
    return OVERSTRAIN_THRESHOLDS.get(
        str(product_type or DEFAULT_PRODUCT_TYPE), OVERSTRAIN_THRESHOLDS[DEFAULT_PRODUCT_TYPE]
    )


@dataclass
class TypeCandidate:
    """유형 하나에 대한 판정 결과.

    `matched` 만으로는 리포트를 쓸 수 없다. 어떤 값이 어느 조건에 걸렸는지
    함께 담아야 "공구 마모 213분(조건 200~240분)" 같은 문장이 나온다.
    """

    code: str                 # TWF
    failure_mode: str         # tool_wear_failure
    display_name: str         # 공구 마모
    matched: bool
    reason: str               # 사람이 읽는 근거 한 줄
    measured: dict = field(default_factory=dict)
    threshold: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "failure_mode": self.failure_mode,
            "display_name": self.display_name,
            "matched": self.matched,
            "reason": self.reason,
            "measured": self.measured,
            "threshold": self.threshold,
        }


def _num(row: Mapping, key: str) -> Optional[float]:
    v = row.get(key)
    if v in (None, "", "nan"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _pwf(row) -> TypeCandidate:
    torque, rpm = _num(row, "torque_nm"), _num(row, "rotational_speed_rpm")
    p = power_w(torque, rpm)
    hit = p < POWER_LOW_W or p > POWER_HIGH_W
    side = "미만" if p < POWER_LOW_W else "초과"
    reason = (
        f"전력 {p:,.0f}W — 정상 범위 {POWER_LOW_W:,.0f}~{POWER_HIGH_W:,.0f}W {side}"
        if hit else
        f"전력 {p:,.0f}W — 정상 범위 내"
    )
    return TypeCandidate("PWF", *TYPE_META["PWF"], matched=hit, reason=reason,
                         measured={"power_w": round(p, 1), "torque_nm": torque,
                                   "rotational_speed_rpm": rpm},
                         threshold={"low_w": POWER_LOW_W, "high_w": POWER_HIGH_W})


def _hdf(row) -> TypeCandidate:
    air, proc = _num(row, "air_temperature_k"), _num(row, "process_temperature_k")
    rpm = _num(row, "rotational_speed_rpm")
    gap = proc - air
    hit = gap < HDF_TEMPERATURE_GAP_MAX_K and rpm < HDF_RPM_MAX
    reason = (
        f"온도차 {gap:.2f}K (조건 {HDF_TEMPERATURE_GAP_MAX_K}K 미만) · "
        f"회전수 {rpm:,.0f} (조건 {HDF_RPM_MAX:,.0f} 미만)"
        if hit else
        f"온도차 {gap:.2f}K · 회전수 {rpm:,.0f} — 두 조건 동시 충족 아님"
    )
    return TypeCandidate("HDF", *TYPE_META["HDF"], matched=hit, reason=reason,
                         measured={"temperature_gap_k": round(gap, 3),
                                   "rotational_speed_rpm": rpm},
                         threshold={"gap_max_k": HDF_TEMPERATURE_GAP_MAX_K,
                                    "rpm_max": HDF_RPM_MAX})


def _osf(row, product_type=None) -> TypeCandidate:
    wear, torque = _num(row, "tool_wear_min"), _num(row, "torque_nm")
    thr = overstrain_threshold(product_type)
    load = wear * torque
    hit = load > thr
    pt = str(product_type or DEFAULT_PRODUCT_TYPE)
    reason = (
        f"마모×토크 {load:,.0f} — 제품군 {pt} 임계 {thr:,.0f} 초과"
        if hit else
        f"마모×토크 {load:,.0f} — 제품군 {pt} 임계 {thr:,.0f} 이하"
    )
    return TypeCandidate("OSF", *TYPE_META["OSF"], matched=hit, reason=reason,
                         measured={"wear_torque": round(load, 1),
                                   "tool_wear_min": wear, "torque_nm": torque,
                                   "product_type": pt},
                         threshold={"limit": thr, "product_type_assumed": product_type is None})


def _twf(row) -> TypeCandidate:
    wear = _num(row, "tool_wear_min")
    hit = TWF_WEAR_MIN <= wear <= TWF_WEAR_MAX
    reason = (
        f"공구 마모 {wear:.1f}분 — 고장 구간 {TWF_WEAR_MIN:.0f}~{TWF_WEAR_MAX:.0f}분 내"
        if hit else
        f"공구 마모 {wear:.1f}분 — 고장 구간 {TWF_WEAR_MIN:.0f}~{TWF_WEAR_MAX:.0f}분 밖"
    )
    return TypeCandidate("TWF", *TYPE_META["TWF"], matched=hit, reason=reason,
                         measured={"tool_wear_min": wear},
                         threshold={"min": TWF_WEAR_MIN, "max": TWF_WEAR_MAX})


def evaluate(
    row: Mapping,
    *,
    asset_type: str = "cnc",
    product_type: Optional[str] = None,
) -> dict:
    """측정값 한 행 → 유형 판정 결과.

    반환 구조

        available   판정 가능 여부. 필요한 측정 항목이 없으면 False
        reason      판정 불가 사유 (available=False 일 때)
        candidates  4개 유형 전체의 판정 결과 (발동 여부 포함)
        matched     발동한 유형만
        conclusion  'single' | 'multiple' | 'none'

    `conclusion='none'` 은 실패가 아니다. 알려진 조건에 해당하지 않는다는
    정보이며, 무작위 고장이 여기 해당한다.
    """
    if asset_type not in SUPPORTED_ASSET_TYPES:
        return {"available": False,
                "reason": f"유형 판정 규칙은 {'/'.join(SUPPORTED_ASSET_TYPES)} 에만 적용됩니다",
                "candidates": [], "matched": [], "conclusion": None}

    missing = [f for f in REQUIRED_FIELDS if _num(row, f) is None]
    if missing:
        return {"available": False,
                "reason": f"측정 항목 부족: {', '.join(missing)}",
                "candidates": [], "matched": [], "conclusion": None}

    cands = [_pwf(row), _hdf(row), _osf(row, product_type), _twf(row)]
    matched = [c for c in cands if c.matched]
    return {
        "available": True,
        "reason": "",
        "candidates": [c.to_dict() for c in cands],
        "matched": [c.to_dict() for c in matched],
        "conclusion": "single" if len(matched) == 1 else ("multiple" if matched else "none"),
        "unconditioned_types": list(UNCONDITIONED),
    }


def summary_line(result: dict) -> str:
    """리포트 한 줄 요약. 근거를 함께 담는다."""
    if not result.get("available"):
        return f"고장 유형 판정 불가 — {result.get('reason', '사유 미상')}"

    matched = result.get("matched") or []
    if not matched:
        return ("알려진 고장 조건에 해당하는 항목이 없습니다. "
                "측정값 이상이 있어도 유형은 특정되지 않습니다.")
    if len(matched) == 1:
        m = matched[0]
        return f"{m['display_name']} 조건 충족 — {m['reason']}"
    names = ' · '.join(m["display_name"] for m in matched)
    return f"복수 조건 충족 ({len(matched)}건): {names}"
