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


# ── 기여 변수 개수 ─────────────────────────────────────────────────────────
#
# 기여 변수(top_factors)를 몇 개 낼지는 **데이터셋이 정한다**. 우리가 정하지 않는다.
#
# 현재 canonical v3.1 은 Top-3 이며, 생성 파이프라인과 검증 스크립트 양쪽에
# 3이 못 박혀 있다.
#   model/prediction_pipeline.py:281,320   np.argsort(...)[:3]
#   scripts/validate_package.py:811,850    개수가 3이 아니면 AssertionError
#   RESULT_ARTIFACT_SCHEMA.md:30           "Top-3 feature contribution"
#
# 따라서 우리 쪽에서 4개로 늘릴 수 없다. 4번째 기여도 값 자체가 패키지에 없다.
# 대신 **개수를 가정하지 않는다.** 데이터셋이 N을 늘리면 코드 수정 없이 따라간다.
#
# 지켜야 할 것:
#   - top_factors / component_hypotheses 를 슬라이스하지 않는다
#   - len(...) == 3 을 전제한 분기를 두지 않는다
#   - 개수를 문장에 쓸 때는 len() 으로 세어 쓴다 ("3건" 하드코딩 금지)
#
# 화면 공간 때문에 잘라야 하면 아래 상수를 쓴다. 데이터 개수와 표시 개수는
# 별개다. 이 값은 화면 결정이지 데이터셋 결정이 아니다.
MAX_COMPONENTS_DETAILED = 2   # 정비 이력을 상세 서술할 계통 최대 개수


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
#   등급    모델 4단계 → 화면 3단계 (load_v3_result_artifacts.STATUS_MAP 참조)
#   evaluation_truth 는 사용하지 않는다.
#
# 매핑 경계는 실측으로 정했다. critical 과 warning 을 함께 알람으로 묶던
# 이전 매핑에서는 관찰(1.05%)과 정상(1.00%)의 변별력이 없었다.
#
MEASURED_ON = "canonical-ai4i-physics-v3.1"
CURRENT_DATASET = "canonical-ai4i-physics-v3.1"

GRADE_FAILURE_RATE = {
    ALARM: 38.38,
    WATCH: 2.95,
    NORMAL: 1.03,
}

# 표본 수. 비율만 제시하면 근거가 반쪽이다.
GRADE_FAILURE_SAMPLE = {
    ALARM: 2_298,
    WATCH: 12_041,
    NORMAL: 53_869,
}

# 전체 고장 중 이 등급이 포착한 비율.
# 알람만 확인하면 절반을 놓친다. 관찰까지 보면 69%.
GRADE_CAPTURE_PCT = {
    ALARM: 49.2,
    WATCH: 19.8,
    NORMAL: 31.0,
}

# 전체 기저율. 등급 비율을 이것과 대비해야 의미가 읽힌다.
BASE_FAILURE_RATE_PCT = 2.63

# 자산 유형별 편차가 크다. 하나의 숫자로 제시하면 오해를 부른다.
#   CNC 알람 51.34% (기저 21.3배) · 압축기 알람 17.57% (기저 5.0배)
GRADE_FAILURE_RATE_BY_ASSET_TYPE = {
    "cnc":        {ALARM: 51.34, WATCH: 2.26, NORMAL: 0.87, "base": 2.41},
    "compressor": {ALARM: 17.57, WATCH: 4.87, NORMAL: 1.78, "base": 3.52},
}

# 변별력이 확인되지 않은 등급. 현재 매핑에서는 없다.
# 인접 등급 간 격차 13.0배 / 2.9배.
GRADES_WITHOUT_DISCRIMINATION: set[str] = set()


def grade_failure_rate(grade: str, asset_type: str | None = None) -> float | None:
    """
    등급별 실제 고장률을 반환한다.

    `asset_type`을 주면 해당 유형의 측정값을 쓴다. 유형별 편차가 크므로
    (CNC 알람 51.34% 대 압축기 17.57%) 가능하면 유형을 지정하는 편이 정확하다.

    다음 두 경우 `None`을 반환하며, 화면은 수치를 제시하지 않아야 한다.

    1. 현재 데이터셋에서 측정되지 않은 값
    2. 변별력이 확인되지 않은 등급

    측정되지 않았거나 변별력 없는 값을 위험 근거로 제시하지 않는다.
    """
    if MEASURED_ON != CURRENT_DATASET:
        return None
    if grade in GRADES_WITHOUT_DISCRIMINATION:
        return None
    if asset_type and asset_type in GRADE_FAILURE_RATE_BY_ASSET_TYPE:
        return GRADE_FAILURE_RATE_BY_ASSET_TYPE[asset_type].get(grade)
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


def grade_failure_rate_detail(grade: str, asset_type: str | None = None) -> str:
    """수치와 함께 표시할 산출 근거. 비율만으로는 근거가 반쪽이다."""
    rate = grade_failure_rate(grade, asset_type)
    if rate is None:
        return ""
    if asset_type and asset_type in GRADE_FAILURE_RATE_BY_ASSET_TYPE:
        base = GRADE_FAILURE_RATE_BY_ASSET_TYPE[asset_type]["base"]
        return f"{asset_type} 기준 · 해당 유형 기저율 {base:.2f}%"
    n = GRADE_FAILURE_SAMPLE.get(grade)
    n_txt = f"{n:,}건 기준" if n else "표본 수 미상"
    return f"{n_txt} · 전체 기저율 {BASE_FAILURE_RATE_PCT:.2f}%"


def grade_rate_summary(sep: str = " / ") -> str:
    """세 등급의 24시간 내 고장률 한 줄 요약. 화면 안내 문구용.

    측정 데이터셋과 현재 데이터셋이 다르면 빈 문자열. 화면은 이 경우
    수치 없이 등급 의미만 설명해야 한다.
    """
    if MEASURED_ON != CURRENT_DATASET:
        return ""
    return sep.join(
        f"{g} 약 {GRADE_FAILURE_RATE[g]:.1f}%"
        for g in (ALARM, WATCH, NORMAL)
        if g in GRADE_FAILURE_RATE
    )


def grade_threshold_caption(grade: str) -> str:
    """차트 기준선 라벨. 수치를 못 쓰면 등급명만."""
    rate = grade_failure_rate(grade)
    if rate is None:
        return f"{grade} 기준"
    return f"{grade} 기준 (24시간 내 고장 약 {rate:.1f}%)"


# ── 선행 경고 없는 고장 ────────────────────────────────────────────────────
#
# "경고 이벤트 없이 이상 신호만 있는 상태"가 얼마나 드문지 제시하는 근거.
#
# 이 값은 Azure PdM(결정 004, superseded)에서 잰 것이다.
# canonical v3.1은 **에러 로그 자체가 없어** 측정 자체가 불가능하다
# (`error_context.available = False`, 100대 전건).
#
# 따라서 현재 데이터셋에서는 이 문구를 화면에 내보내지 않는다.
# 상수를 지우지 않고 남기는 이유는, 에러 로그가 있는 데이터셋으로 다시
# 바뀌었을 때 재측정 대상임을 드러내기 위해서다.
PRIOR_ERROR_ABSENT = {
    "measured_on": "azure-pdm",
    "failures_total": 761,
    "without_prior_error": 15,
    "pct": 2.0,
}


def prior_error_absent_note() -> str:
    """선행 경고 없는 고장이 얼마나 드문지. 측정 불가면 빈 문자열."""
    if PRIOR_ERROR_ABSENT["measured_on"] != CURRENT_DATASET:
        return ""
    d = PRIOR_ERROR_ABSENT
    return (
        f"전체 고장 {d['failures_total']:,}건 중 {d['without_prior_error']:,}건"
        f"({d['pct']:.1f}%)이 선행 경고 없이 발생한 사례입니다."
    )


def grade_capture_note(grade: str) -> str:
    """이 등급만 확인했을 때 놓치는 비율. 알람만 보면 절반을 놓친다."""
    pct = GRADE_CAPTURE_PCT.get(grade)
    if pct is None:
        return ""
    if grade == ALARM:
        both = GRADE_CAPTURE_PCT.get(ALARM, 0) + GRADE_CAPTURE_PCT.get(WATCH, 0)
        return (
            f"전체 고장의 {pct:.0f}%가 이 등급에서 발생했습니다. "
            f"관찰 등급까지 확인하면 {both:.0f}%입니다."
        )
    return f"전체 고장의 {pct:.0f}%가 이 등급에서 발생했습니다."
