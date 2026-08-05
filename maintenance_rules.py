"""
정비 유형 판정 규칙 — 단일 출처.

`evidence_package.py`, `manager_app.py`가 이 모듈을 호출한다.
같은 규칙을 각자 구현하면 경계 조건이 갈리고, 실제로 갈렸던 적이 있다.

결정 002(필드 단위 authoritative source 지정) 원칙을 판정 규칙에도 적용한 것.

---

## 관측 창

    (performed_at - REACTIVE_WINDOW_HOURS, performed_at]

**시작은 배제하고 끝은 포함한다.**

`PdM_*.csv`의 시각은 시간 단위로 반올림돼 있어(데이터 한계 4항목) 고장과 그에
따른 교체가 같은 시각으로 기록된다. 끝 경계를 배제하면 사후 정비를 전부 놓치고
모든 기록이 preventive 로 판정된다.

## 기대값 (결정 004 measured)

    preventive 2,543 / reactive 743                합계 3,286
    유형별 다음 교체까지 중앙값  예방 45일 / 사후 30일

의존성은 pandas 뿐이다. 부수효과 없음.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

# 사후 정비 판정 관측 창 (시간).
# 코어 설계의 SamplingPolicy.reactive_window_hours 에 대응한다.
REACTIVE_WINDOW_HOURS = 24

PREVENTIVE = "preventive"
REACTIVE = "reactive"


def reactive_window(
    performed_at: pd.Timestamp,
    window_hours: int = REACTIVE_WINDOW_HOURS,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """관측 창의 (시작, 끝)을 반환. 시작 배제 · 끝 포함."""
    return performed_at - pd.Timedelta(hours=window_hours), performed_at


def is_reactive(
    performed_at: pd.Timestamp,
    failure_times: Iterable[pd.Timestamp],
    window_hours: int = REACTIVE_WINDOW_HOURS,
) -> bool:
    """
    교체 시각과 같은 자산·계통의 고장 시각들을 받아 사후 정비 여부를 판정한다.

    Args:
        performed_at:  교체 수행 시각
        failure_times: 같은 자산·계통의 고장 시각. 전체를 넘겨도 되고 걸러 넘겨도 된다
        window_hours:  관측 창 길이

    Returns:
        관측 창 (performed_at - window_hours, performed_at] 안에 고장이 있으면 True
    """
    start, end = reactive_window(performed_at, window_hours)
    return any(start < ft <= end for ft in failure_times)


def is_reactive_series(
    performed_at: pd.Timestamp,
    failure_times: pd.Series,
    window_hours: int = REACTIVE_WINDOW_HOURS,
) -> bool:
    """`is_reactive`의 pandas Series 판. 빈 Series면 False."""
    if failure_times is None or len(failure_times) == 0:
        return False
    start, end = reactive_window(performed_at, window_hours)
    return bool(((failure_times > start) & (failure_times <= end)).any())


def classify(
    performed_at: pd.Timestamp,
    failure_times: Iterable[pd.Timestamp],
    window_hours: int = REACTIVE_WINDOW_HOURS,
) -> str:
    """`REACTIVE` 또는 `PREVENTIVE` 문자열을 반환."""
    return REACTIVE if is_reactive(performed_at, failure_times, window_hours) else PREVENTIVE
