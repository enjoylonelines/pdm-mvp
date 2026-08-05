"""
판정·표시 정책 상수 — 단일 출처.

같은 값을 여러 파일이 각자 갖고 있으면 한쪽만 바뀌었을 때 조용히 어긋난다.
정비 유형 판정에서 실제로 그런 일이 있었다(결정 019).

`maintenance_rules.py`는 정비 유형 판정 규칙을 보유하고,
이 모듈은 그 외 관측 창·등급 경계·표시 기준을 보유한다.

---

## 데이터셋 종속 값 주의

아래 `GRADE_FAILURE_RATE`는 **측정값**이다. 데이터셋이 바뀌면 다시 재야 한다.
현재 값은 Azure PdM(결정 004, superseded) 기준이며 canonical v3.1에서는
재측정되지 않았다. `MEASURED_ON` 으로 어느 데이터셋에서 잰 값인지 표시한다.

의존성은 없다. 순수 상수 모듈.
"""

from __future__ import annotations

# ── 관측 창 ────────────────────────────────────────────────────────────────
# 센서 관측 집계 창. evidence_package·manager_app 이 공유한다.
OBSERVATION_WINDOW_HOURS = 24

# 추이 비교용 직전 창. 최근 24h 대 직전 24h.
TREND_COMPARE_WINDOW_HOURS = 48

# 창 내 최소 유효 행 수. 미달이면 z·백분위 신뢰도 낮음으로 표시한다.
MIN_WINDOW_ROWS = 12

# 추이 판정 기준. 최근 창 평균이 직전 창 대비 이 % 이상 변하면 상승·하락.
TREND_CHANGE_THRESHOLD_PCT = 20


# ── 등급 경계 ──────────────────────────────────────────────────────────────
# excess_ratio = |z| / 계통별 임계값
GRADE_ALARM_THRESHOLD = 1.0   # er >= 1.0  → 알람
GRADE_WATCH_THRESHOLD = 0.8   # 0.8 <= er < 1.0 → 관찰

ALARM = "알람"
WATCH = "관찰"
NORMAL = "정상"


# ── 표본 충분성 ────────────────────────────────────────────────────────────
# 미달이면 비율·백분위를 판단 근거로 제시하지 않는다.
MIN_MAINTENANCE_HISTORY = 5   # 교체 이력 건수
MIN_PEER_GROUP_SIZE = 5       # 동종 집단 설비 수
MIN_RATIO_SAMPLE = 30         # 비율 제시 최소 표본 (결정 018)


# ── 데이터셋 종속 측정값 ───────────────────────────────────────────────────
#
# 등급별 "판정 시점 이후 24시간 내 실제 고장 발생 비율".
#
# 산출 방법 (canonical v3.1):
#   모집단  prediction_timeline.jsonl 68,208행
#   고장    maintenance_event.csv 의 maintenance_type=failure_recovery 76건의 started_at
#   창      (observed_at, observed_at + 24h]
#   등급    critical·warning → 알람 / attention → 관찰 / normal → 정상
#   evaluation_truth 는 사용하지 않는다 (미사용 원칙).
#
# ⚠ 관찰 등급의 변별력이 없다.
#   관찰 1.05% 대 정상 1.00% — 차이가 0.05%p 로 사실상 같다.
#   전체 기저율 2.63% 보다도 낮다. 결정 012(3단계 등급) 재검토 근거다.
#   화면에서 관찰 등급의 고장률을 "정상보다 위험함"의 근거로 쓰면 안 된다.
#
MEASURED_ON = "canonical-ai4i-physics-v3.1"
CURRENT_DATASET = "canonical-ai4i-physics-v3.1"

GRADE_FAILURE_RATE = {
    ALARM: 8.63,
    WATCH: 1.05,
    NORMAL: 1.00,
}

# 표본 수. 비율만 제시하면 근거가 반쪽이다 (결정 002).
GRADE_FAILURE_SAMPLE = {
    ALARM: 14_339,
    WATCH: 32_382,
    NORMAL: 21_487,
}

# 전체 기저율. 등급 비율을 이것과 대비해야 의미가 읽힌다.
BASE_FAILURE_RATE_PCT = 2.63

# 변별력이 확인되지 않은 등급. 화면은 이 등급의 고장률을 위험 근거로 쓰지 않는다.
GRADES_WITHOUT_DISCRIMINATION = {WATCH}


def grade_failure_rate(grade: str) -> float | None:
    """
    등급별 실제 고장률을 반환한다.

    다음 두 경우 `None`을 반환하며, 화면은 수치를 제시하지 않아야 한다.

    1. 현재 데이터셋에서 측정되지 않은 값
    2. 변별력이 확인되지 않은 등급

    측정되지 않았거나 변별력 없는 값을 위험 근거로 제시하는 것은
    결정 002(모든 수치에 산출 근거)와 결정 008(억제 없이 재료 나열)에 어긋난다.
    """
    if MEASURED_ON != CURRENT_DATASET:
        return None
    if grade in GRADES_WITHOUT_DISCRIMINATION:
        return None
    return GRADE_FAILURE_RATE.get(grade)


def grade_failure_rate_note(grade: str | None = None) -> str:
    """수치를 제시할 수 없을 때 화면에 표시할 사유."""
    if MEASURED_ON != CURRENT_DATASET:
        return (
            f"등급별 실제 고장률은 `{MEASURED_ON}` 기준 측정값이며 "
            f"현재 데이터셋(`{CURRENT_DATASET}`)에서 재측정되지 않았습니다."
        )
    if grade in GRADES_WITHOUT_DISCRIMINATION:
        w = GRADE_FAILURE_RATE.get(WATCH)
        n = GRADE_FAILURE_RATE.get(NORMAL)
        return (
            f"이 등급의 24시간 내 고장률({w:.2f}%)은 정상 등급({n:.2f}%)과 "
            f"차이가 없어 위험 근거로 제시하지 않습니다."
        )
    return ""


def grade_failure_rate_detail(grade: str) -> str:
    """수치와 함께 표시할 산출 근거. 비율만으로는 근거가 반쪽이다."""
    rate = grade_failure_rate(grade)
    if rate is None:
        return ""
    n = GRADE_FAILURE_SAMPLE.get(grade)
    n_txt = f"{n:,}건 기준" if n else "표본 수 미상"
    return f"{n_txt} · 전체 기저율 {BASE_FAILURE_RATE_PCT:.2f}%"
