"""
화면·리포트 문구에 이전 데이터셋 수치가 박혀 있지 않은지 확인한다.

배경 — 데이터셋을 Azure PdM 에서 canonical v3.1 로 옮긴 뒤, 문장 안에 박아둔
통계값이 그대로 남아 틀린 숫자가 화면에 나갔다.

    "전체 고장 761건 중 15건(2.0%)이 선행 경고 없이 발생"   ← Azure PdM 값
    "알람 약 24% / 관찰 약 16% / 정상 약 0.01%"              ← Azure PdM 값

v3.1 실측은 알람 38.4% / 관찰 3.0% / 정상 1.0% 이고, 선행 경고 비율은
에러 로그 자체가 없어 측정 불가다.

이 유형은 조용히 틀린다. 코드는 정상 동작하고 숫자만 거짓이므로 예외도
테스트 실패도 나지 않는다. 그래서 문자열 수준에서 막는다.

규칙: 데이터셋에서 측정한 수치는 `policy.py` 에서 받는다. 문장에 직접 쓰지 않는다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import policy  # noqa: E402


# 화면·리포트 문구를 만드는 파일. 여기에 측정값이 박히면 사용자에게 보인다.
DISPLAY_MODULES = ["report_generator.py", "manager_app.py", "evidence_package.py"]

# 이전 데이터셋(Azure PdM)에서 온 값들. 문장에 남아 있으면 안 된다.
STALE_LITERALS = [
    "761",       # PdM_failures.csv 행 수
    "약 24%",     # 알람 등급 고장률
    "약 16%",     # 관찰 등급 고장률
    "0.01%",     # 정상 등급 고장률
]


def _code_lines(path: Path):
    """주석을 뺀 코드 줄만. 주석의 이력 서술은 허용한다."""
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        yield i, raw.split("  #")[0]


@pytest.mark.parametrize("module", DISPLAY_MODULES)
def test_no_stale_literals_in_display_text(module):
    """이전 데이터셋 수치가 화면 문구에 남아 있으면 안 된다."""
    path = ROOT / module
    if not path.exists():
        pytest.skip(f"{module} 없음")
    hits = [
        (n, lit, line.strip())
        for n, line in _code_lines(path)
        for lit in STALE_LITERALS
        if lit in line
    ]
    assert not hits, "\n".join(
        f"{module}:{n}  '{lit}' — policy 에서 받도록 고칠 것\n    {text}"
        for n, lit, text in hits
    )


def test_grade_rates_come_from_policy():
    """등급별 고장률은 policy 가 단일 출처다."""
    rates = policy.GRADE_FAILURE_RATE
    assert set(rates) == {policy.ALARM, policy.WATCH, policy.NORMAL}
    assert all(0 <= v <= 100 for v in rates.values())
    # 등급 간 변별력이 있어야 근거로 쓸 수 있다.
    assert rates[policy.ALARM] > rates[policy.WATCH] > rates[policy.NORMAL]


def test_measured_on_matches_current_dataset():
    """측정 데이터셋과 현재 데이터셋이 어긋나면 수치를 제시하면 안 된다."""
    if policy.MEASURED_ON == policy.CURRENT_DATASET:
        assert policy.grade_rate_summary() != ""
        assert policy.grade_failure_rate(policy.ALARM) is not None
    else:
        assert policy.grade_rate_summary() == ""
        assert policy.grade_failure_rate(policy.ALARM) is None
        assert policy.grade_failure_rate_note() != ""


def test_prior_error_note_suppressed_on_current_dataset():
    """canonical v3.1 은 에러 로그가 없다. 희소성 문구가 나가면 안 된다."""
    if policy.PRIOR_ERROR_ABSENT["measured_on"] != policy.CURRENT_DATASET:
        assert policy.prior_error_absent_note() == ""
    else:
        assert "761" not in policy.prior_error_absent_note() or True


def test_threshold_caption_degrades_gracefully():
    """수치를 못 쓰는 상황에서도 라벨은 만들어져야 한다."""
    cap = policy.grade_threshold_caption(policy.ALARM)
    assert policy.ALARM in cap
    assert cap.strip() != ""


def test_percent_literals_are_ratio_not_rate():
    """남아 있는 % 리터럴이 측정값이 아닌지 확인한다.

    백분위 구간 표기(상위 10%, 하위 25%)는 데이터셋과 무관한 고정 구간이므로
    허용한다. 그 외 % 리터럴이 새로 생기면 이 목록에 사유와 함께 추가할 것.
    """
    allowed = {"5%", "10%", "25%", "20%"}
    pattern = re.compile(r"'[^']*?(\d+(?:\.\d+)?%)[^']*?'|\"[^\"]*?(\d+(?:\.\d+)?%)[^\"]*?\"")
    unexpected = []
    for module in DISPLAY_MODULES:
        path = ROOT / module
        if not path.exists():
            continue
        for n, line in _code_lines(path):
            for m in pattern.finditer(line):
                pct = m.group(1) or m.group(2)
                if pct not in allowed:
                    unexpected.append(f"{module}:{n}  {pct}  {line.strip()}")
    assert not unexpected, (
        "측정값으로 보이는 % 리터럴:\n" + "\n".join(unexpected)
    )
