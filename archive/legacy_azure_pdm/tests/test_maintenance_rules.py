"""
정비 유형 판정 규칙 테스트.

경계 조건이 실제로 갈렸던 적이 있다. 세 파일이 각자 구현하면서
끝 경계를 배제한 구현이 섞여 reactive 가 0건으로 나왔다.
이 테스트는 규칙 자체와, 세 호출부가 같은 결과를 내는지를 잠근다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import maintenance_rules as mr
from evidence_package import load_data

ROOT = Path(__file__).parent


class TestBoundary:
    """관측 창 (performed_at - 24h, performed_at] — 시작 배제 · 끝 포함."""

    @pytest.fixture(scope="class")
    def mdt(self):
        return pd.Timestamp("2015-06-15 12:00:00")

    def test_같은_시각_고장은_reactive(self, mdt):
        """원본 시각이 시간 단위로 반올림돼 고장과 교체가 같은 시각에 기록된다.
        이 케이스를 놓치면 사후 정비가 전부 사라진다."""
        assert mr.is_reactive(mdt, [mdt]) is True

    def test_창_시작_경계는_배제(self, mdt):
        start = mdt - pd.Timedelta(hours=24)
        assert mr.is_reactive(mdt, [start]) is False

    def test_창_안쪽은_reactive(self, mdt):
        assert mr.is_reactive(mdt, [mdt - pd.Timedelta(hours=1)]) is True
        assert mr.is_reactive(mdt, [mdt - pd.Timedelta(hours=23)]) is True

    def test_창_밖은_preventive(self, mdt):
        assert mr.is_reactive(mdt, [mdt - pd.Timedelta(hours=25)]) is False
        assert mr.is_reactive(mdt, [mdt + pd.Timedelta(hours=1)]) is False

    def test_고장_없으면_preventive(self, mdt):
        assert mr.is_reactive(mdt, []) is False

    def test_classify_문자열(self, mdt):
        assert mr.classify(mdt, [mdt]) == mr.REACTIVE
        assert mr.classify(mdt, []) == mr.PREVENTIVE


class TestSeriesEquivalence:
    """스칼라 판과 pandas Series 판이 같은 답을 내야 한다."""

    @pytest.mark.parametrize(
        "offsets_h",
        [[], [0], [-1], [-24], [-25], [1], [-24, -1], [-30, 0]],
    )
    def test_두_구현이_일치(self, offsets_h):
        mdt = pd.Timestamp("2015-06-15 12:00:00")
        times = [mdt + pd.Timedelta(hours=h) for h in offsets_h]
        assert mr.is_reactive(mdt, times) == mr.is_reactive_series(mdt, pd.Series(times))

    def test_빈_시리즈(self):
        mdt = pd.Timestamp("2015-06-15 12:00:00")
        assert mr.is_reactive_series(mdt, pd.Series([], dtype="datetime64[ns]")) is False
        assert mr.is_reactive_series(mdt, None) is False


class TestAgainstDataset:
    """결정 004 measured 값이 재현되어야 한다."""

    @pytest.fixture(scope="class")
    def data(self):
        _, _, fails, maint, _ = load_data(ROOT / "archive")
        return maint, fails

    def test_유형_분해가_결정004와_일치(self, data):
        maint, fails = data
        lookup: dict[tuple, list] = {}
        for _, r in fails.iterrows():
            lookup.setdefault((int(r["machineID"]), str(r["failure"])), []).append(r["datetime"])

        counts = {mr.PREVENTIVE: 0, mr.REACTIVE: 0}
        for _, r in maint.iterrows():
            key = (int(r["machineID"]), str(r["comp"]))
            counts[mr.classify(r["datetime"], lookup.get(key, []))] += 1

        assert counts[mr.PREVENTIVE] == 2543
        assert counts[mr.REACTIVE] == 743
        assert sum(counts.values()) == 3286

    def test_끝_경계를_배제하면_reactive가_0이_된다(self, data):
        """회귀 방지 — 옛 구현을 재현해 실패 양상을 고정한다."""
        maint, fails = data
        lookup: dict[tuple, list] = {}
        for _, r in fails.iterrows():
            lookup.setdefault((int(r["machineID"]), str(r["failure"])), []).append(r["datetime"])

        reactive = 0
        for _, r in maint.iterrows():
            mdt = r["datetime"]
            start = mdt - pd.Timedelta(hours=mr.REACTIVE_WINDOW_HOURS)
            times = lookup.get((int(r["machineID"]), str(r["comp"])), [])
            if any(start <= ft < mdt for ft in times):  # 옛 경계
                reactive += 1

        assert reactive == 0, (
            "끝 경계를 배제하면 사후 정비가 0건이 된다. "
            "이 데이터셋에서 고장과 교체는 같은 시각에 기록되기 때문이다."
        )
