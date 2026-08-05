"""
역할별 리포트 데모 — 3가지 케이스:
  Case 1: 정상 구간 (이상 없음)
  Case 2: 선행 경고가 있는 고장
  Case 3: 선행 경고가 없는 고장 (직전 24h 에러 없음)
"""

from __future__ import annotations
from pathlib import Path
import pandas as pd

from evidence_package import load_data, compute_global_baseline, generate_evidence_package
from report_generator import generate_role_reports

DATA_DIR = Path(__file__).parent / 'archive'


# ── 케이스 탐색 ───────────────────────────────────────────────────────────────

def find_cases(
    errors: pd.DataFrame,
    failures: pd.DataFrame,
    telemetry: pd.DataFrame,
) -> tuple:
    """
    Case 1: 이상 후보 없는 정상 구간 (센서 z < 2, 에러 없음)
    Case 2: 직전 24h 에러 있는 고장
    Case 3: 직전 24h 에러 없는 고장
    """
    # Case 2 & 3: 고장 레코드 순회
    case2 = case3 = None
    for _, row in failures.sort_values('datetime').iterrows():
        mid = int(row['machineID'])
        ts  = row['datetime']
        win = ts - pd.Timedelta(hours=24)
        has_prior = not errors[
            (errors['machineID'] == mid) &
            (errors['datetime'] > win) &
            (errors['datetime'] <= ts)
        ].empty

        if case2 is None and has_prior:
            case2 = (mid, ts)
        if case3 is None and not has_prior:
            case3 = (mid, ts)
        if case2 and case3:
            break

    # Case 1: 고장 목록에 없는 날짜·장비 (machine 1, 2015-03-15)
    #   실제로 이 날짜에 machine 1 고장 없음을 failures에서 확인.
    candidate_ts  = pd.Timestamp('2015-03-15 06:00:00')
    candidate_mid = 1
    conflict = failures[
        (failures['machineID'] == candidate_mid) &
        (failures['datetime'].dt.date == candidate_ts.date())
    ]
    if not conflict.empty:
        # 고장 있으면 다른 날 선택
        candidate_ts = pd.Timestamp('2015-05-01 06:00:00')

    case1 = (candidate_mid, candidate_ts)
    return case1, case2, case3


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

def _sep(ch: str = '=', n: int = 72) -> str:
    return ch * n


def _print_block(block: dict, show_source: bool = True):
    print(f"\n  ┌─ {block['title']}")
    for line in block['text'].splitlines():
        print(f"  │  {line}")
    if show_source and block['source_fields']:
        shown = block['source_fields'][:4]
        extra = len(block['source_fields']) - 4
        suffix = f" +{extra}개" if extra > 0 else ""
        print(f"  │  [근거: {', '.join(shown)}{suffix}]")
    print(f"  └{'─'*50}")


def print_report(report: dict, case_label: str, pkg: dict):
    print(f"\n{_sep()}")
    print(f"  {case_label}")
    print(f"  장비 {report['machine_id']}  ·  {report['timestamp'][:16]}")
    flags = pkg['status_flags']
    print(f"  플래그: no_prior_error={flags['no_prior_error']} | "
          f"multiple_candidates={flags['multiple_candidates']} | "
          f"insufficient_data={flags['insufficient_data']}")
    print(_sep())

    for role_label, role_key in [('MANAGER', 'manager'), ('ENGINEER', 'engineer')]:
        print(f"\n  {'─'*28} [{role_label}] {'─'*28}")
        for block in report[role_key]:
            _print_block(block)


# ── 분석 보고 ─────────────────────────────────────────────────────────────────

def print_analysis(all_cases: list[tuple[dict, str, dict]]):
    print(f"\n{_sep('═')}")
    print("  분석 보고")
    print(_sep('═'))

    # 1. 매니저/엔지니어 차이
    print("\n[1] 매니저 vs 엔지니어 출력 차이")
    for report, label, _ in all_cases:
        mgr_types = [b['type'] for b in report['manager']]
        eng_types = [b['type'] for b in report['engineer']]
        mgr_only  = sorted(set(mgr_types) - set(eng_types))
        eng_only  = sorted(set(eng_types) - set(mgr_types))
        shared    = sorted(set(mgr_types) & set(eng_types))

        print(f"\n  {label}:")
        print(f"    매니저 블록 수: {len(mgr_types)}  →  {mgr_types}")
        print(f"    엔지니어 블록 수: {len(eng_types)}  →  {eng_types}")
        if shared:
            print(f"    공통 블록: {shared}")
        if mgr_only:
            print(f"    매니저 전용: {mgr_only}")
        if eng_only:
            print(f"    엔지니어 전용: {eng_only}")

    # 2. 근거 부족으로 생략된 항목
    print(f"\n[2] 근거 부족으로 생략되거나 [근거 부족] 표기된 항목")
    any_missing = False
    for report, label, _ in all_cases:
        missing = []
        for role in ('manager', 'engineer'):
            for b in report[role]:
                if b['text'].startswith('[근거 부족'):
                    missing.append(f"{role}/{b['type']}")
        if missing:
            print(f"  {label}: {', '.join(missing)}")
            any_missing = True
        else:
            print(f"  {label}: 없음 (모든 블록 근거 충족)")
    if not any_missing:
        print("  → 3개 케이스 모두 근거 부족 블록 없음")

    # 3. 규칙만으로 어색한 지점
    print(f"\n[3] 규칙만으로 어색한 지점 (LLM이 필요하다고 판단되는 곳)")
    issues = [
        "severity 레이블이 기계적: z≥3→'현저한', z≥2→'유의한'으로만 분류."
        " 점검 직후·정기 교체 직전 맥락을 반영하지 못함.",
        "복수 후보의 우선순위 없음: 규칙으로는 어느 부품을 먼저 점검할지"
        " 순위를 매길 수 없음 (부품 간 연령·교체 이력 조합 필요).",
        "교체 이력 없음 의미 불명: 신품인지 기록 누락인지 구분 불가.",
        "선행 경고 없는 케이스의 '왜' 서술 불가: 상태 나열은 가능하나"
        " 경고 없이 발생한 이유 추론은 규칙 범위 밖.",
        "peer_comparison 백분위가 '높다/낮다' 이상의 의미 해석 불가:"
        " 동급 장비가 모두 이상 상태면 백분위 50%여도 위험함.",
    ]
    for i, issue in enumerate(issues, 1):
        print(f"  {i}. {issue}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("데이터 로딩 중 (최초 1회, 약 10~20초)...")
    tel, errs, fails, maint, mach = load_data(DATA_DIR)
    baseline = compute_global_baseline(tel)
    print("로딩 완료.\n")

    case1_key, case2_key, case3_key = find_cases(errs, fails, tel)

    cases_meta = [
        (case1_key, "CASE 1: 정상 구간"),
        (case2_key, "CASE 2: 선행 경고 있는 고장"),
        (case3_key, "CASE 3: 선행 경고 없는 고장"),
    ]

    all_cases: list[tuple[dict, str, dict]] = []

    for (mid, ts), label in cases_meta:
        if mid is None or ts is None:
            print(f"\n{label}: 케이스를 찾지 못했습니다.")
            continue

        print(f"Evidence Package 생성: 장비 {mid} @ {str(ts)[:16]}  ({label})")
        pkg    = generate_evidence_package(
            mid, ts, tel, errs, fails, maint, mach, baseline=baseline
        )
        report = generate_role_reports(pkg)
        all_cases.append((report, label, pkg))
        print_report(report, label, pkg)

    if all_cases:
        print_analysis(all_cases)


if __name__ == '__main__':
    main()
