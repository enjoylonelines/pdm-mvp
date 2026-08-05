"""
오탐(FP) 케이스 리포트 검증 데모.

순서:
  1. 데이터 로드 + 피처/라벨 구축
  2. 베이스라인 모델 학습 (HistGBC, 튜닝 없음)
  3. 검증 구간에서 TP/FP/FN/TN 케이스 선택
  4. 4개 케이스 각각에 Evidence Package + 모델 예측 추가
  5. 역할별 리포트 생성 및 전문 출력
  6. 분석 보고 (TP vs FP 구분 가능성, FN 누락, 억제 한계)
"""

from __future__ import annotations
from pathlib import Path

import pandas as pd

from evidence_package  import load_data, compute_global_baseline, generate_evidence_package
from baseline_model    import (
    build_features, build_labels, train_baseline,
    add_model_prediction, find_four_cases,
)
from report_generator  import generate_role_reports

DATA_DIR = Path(__file__).parent / 'archive'


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

def _hr(ch: str = '=', n: int = 74) -> str:
    return ch * n


def _print_block(block: dict, show_source: bool = True):
    print(f"\n  ┌─ {block['title']}")
    for line in block['text'].splitlines():
        print(f"  │  {line}")
    if show_source and block['source_fields']:
        shown  = block['source_fields'][:4]
        extra  = len(block['source_fields']) - 4
        suffix = f" +{extra}개" if extra > 0 else ""
        print(f"  │  [근거: {', '.join(shown)}{suffix}]")
    print(f"  └{'─'*52}")


def print_case_report(report: dict, label: str, pkg: dict, outcome: str):
    mp    = pkg.get('model_prediction', {})
    prob  = mp.get('probability', '?')
    thr   = mp.get('threshold',   '?')
    alarm = mp.get('alarm_triggered', '?')

    print(f"\n{_hr()}")
    print(f"  [{label}]  {outcome}")
    print(f"  장비 {report['machine_id']}  ·  {report['timestamp'][:16]}")
    flags = pkg['status_flags']
    print(
        f"  모델: 확률={prob}  임계={thr}  알람={alarm}"
        f"  |  z이상후보={len(pkg['component_hypotheses'])}건"
        f"  |  no_prior_error={flags['no_prior_error']}"
    )
    print(_hr())

    for role_label, role_key in [('MANAGER', 'manager'), ('ENGINEER', 'engineer')]:
        print(f"\n  {'─'*28} [{role_label}] {'─'*28}")
        for block in report[role_key]:
            _print_block(block)


# ── 분석 보고 ─────────────────────────────────────────────────────────────────

def print_analysis(cases: list[tuple[str, str, dict, dict]]):
    """cases: [(label, outcome, report, pkg), ...]"""
    print(f"\n{_hr('═')}")
    print("  분석 보고")
    print(_hr('═'))

    tp_data = next((r for l, o, r, _ in cases if l == 'TP'), None)
    fp_data = next((r for l, o, r, _ in cases if l == 'FP'), None)

    # 1. TP vs FP 블록 비교
    print("\n[1] TP vs FP 리포트 차이 — 사람이 구분할 수 있는가")
    if tp_data and fp_data:
        for role in ('manager', 'engineer'):
            tp_blocks = {b['type']: b['text'] for b in tp_data[role]}
            fp_blocks = {b['type']: b['text'] for b in fp_data[role]}
            print(f"\n  [{role.upper()}]")
            # alarm_context 비교
            tp_ac = tp_blocks.get('alarm_context', '')
            fp_ac = fp_blocks.get('alarm_context', '')
            if tp_ac or fp_ac:
                print("  alarm_context 차이:")
                print(f"    TP: {tp_ac[:200].replace(chr(10), ' / ')}")
                print(f"    FP: {fp_ac[:200].replace(chr(10), ' / ')}")
            # 억제 근거 존재 여부 — "없음" 포함 여부로 판별
            # (매니저: "알람 긴급도를 낮춰볼 근거:", 엔지니어: "억제 신호:")
            tp_no_supp = '억제 근거 없음' in tp_ac or '억제 신호 없음' in tp_ac
            fp_no_supp = '억제 근거 없음' in fp_ac or '억제 신호 없음' in fp_ac
            print(f"    TP 억제 근거 있음: {not tp_no_supp}")
            print(f"    FP 억제 근거 있음: {not fp_no_supp}")

    # 2. FN 케이스 분석
    print("\n[2] FN 케이스 — 리포트가 무엇을 놓쳤는가")
    fn_data = next((r for l, o, r, _ in cases if l == 'FN'), None)
    fn_pkg  = next((p for l, o, _, p in cases if l == 'FN'), None)
    if fn_data and fn_pkg:
        mp   = fn_pkg.get('model_prediction', {})
        hyps = fn_pkg['component_hypotheses']
        err  = fn_pkg['error_context']['count']
        print(f"  모델 확률: {mp.get('probability', '?')}  (알람 없음)")
        print(f"  z이상 후보: {len(hyps)}건  |  직전 에러: {err}건")
        if not hyps:
            print("  → z-score 이상 없음: 규칙 기반 리포트도 이상 없음으로 생성됨.")
            print("  → 모델도 알람 미발생. 리포트는 실제 고장 발생 사실을 알 방법 없음.")
        else:
            print("  → z이상은 있으나 모델이 알람 미발생. 역치 미달로 판단.")
    else:
        print("  FN 케이스 없음 (검증 구간에서 FN이 없거나 탐색 실패)")

    # 3. 억제 근거 중 규칙 불가 항목
    print("\n[3] 억제 근거 중 규칙으로 도출 불가능한 항목")
    unruleable = [
        "점검 직후 센서 진정 여부: 교체 30일 이내를 S2로 표시하지만,"
        " 실제로 센서가 안정됐는지는 이후 추이 데이터 없이 판단 불가.",
        "동급 장비가 동시에 이상이면 S4 무력화: 동급 전체가 이상이면"
        " 백분위 50%여도 위험할 수 있음 — 규칙 범위 밖.",
        "복수 알람의 패턴 해석: 같은 장비가 반복 알람일 때 '누적 위험 증가'인지"
        " '과적합 오탐'인지 규칙으로 구분 불가.",
        "운영 맥락(공휴일·계획 정지·부하 감소): 센서 편차가 운영 조건 변화로"
        " 설명될 수 있으나 텔레메트리 외 데이터 없이 판단 불가.",
        "FN의 '왜 못 잡았는가': 피처에 없는 패턴(예: 단시간 급변, 진동 주파수)"
        " 이면 규칙/모델 둘 다 탐지 불가 — 원인 서술 자체가 불가능.",
    ]
    for i, item in enumerate(unruleable, 1):
        # 줄 너비 맞춰 출력
        words  = item.split()
        line   = f"  {i}. "
        indent = " " * 5
        for w in words:
            if len(line) + len(w) + 1 > 80:
                print(line)
                line = indent + w + " "
            else:
                line += w + " "
        print(line.rstrip())


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    # 1. 데이터 로드
    print("데이터 로딩 중...")
    tel, errs, fails, maint, mach = load_data(DATA_DIR)
    baseline = compute_global_baseline(tel)
    print("완료.\n")

    # 2. 피처 + 라벨
    print("피처 구축 중 (100대 × 24h rolling)...")
    feat_df  = build_features(tel)
    print("라벨 구축 중 (761건 고장 기준)...")
    feat_lab = build_labels(feat_df, fails)
    print("완료.\n")

    # 3. 모델 학습
    print("베이스라인 모델 학습 중 (2015-10-01 이전)...")
    model, threshold, val_df, metrics, feat_imp = train_baseline(feat_lab)

    print("\n── 모델 성능 (검증 구간) ──")
    print(f"  PR-AUC:    {metrics['pr_auc']}")
    print(f"  임계값:    {metrics['threshold']}")
    print(f"  정밀도:    {metrics['precision']}   "
          f"({metrics['tp']} TP / {metrics['tp'] + metrics['fp']} 알람)")
    print(f"  재현율:    {metrics['recall']}   "
          f"({metrics['tp']} TP / {metrics['val_positives']} 실제 양성)")
    print(f"  F1:        {metrics['f1']}")
    print(f"  혼동행렬:  TP={metrics['tp']}  FP={metrics['fp']}"
          f"  FN={metrics['fn']}  TN={metrics['tn']}")
    print()

    # 4. 4개 케이스 탐색
    cases_key = find_four_cases(val_df)
    print("선택된 케이스:")
    for label, (mid, ts) in cases_key.items():
        if mid is None:
            print(f"  {label}: 탐색 실패")
        else:
            row = val_df[(val_df['machineID'] == mid) & (val_df['datetime'] == ts)].iloc[0]
            print(f"  {label}: 장비 {mid}  @  {str(ts)[:16]}"
                  f"  prob={row['prob']:.4f}  label={int(row['label'])}")
    print()

    # 5. Evidence Package + 모델 예측 + 리포트 생성
    outcomes = {
        'TP': "알람 발생 · 실제 24h 내 고장 (True Positive)",
        'FP': "알람 발생 · 실제 고장 없음 (False Positive) ← 핵심",
        'FN': "알람 없음 · 실제 고장 발생 (False Negative)",
        'TN': "알람 없음 · 고장 없음 (True Negative)",
    }

    all_cases: list[tuple[str, str, dict, dict]] = []

    for label, (mid, ts) in cases_key.items():
        if mid is None:
            print(f"[{label}] 케이스 없음 — 스킵")
            continue

        outcome = outcomes[label]
        print(f"리포트 생성: [{label}] 장비 {mid} @ {str(ts)[:16]}")

        pkg = generate_evidence_package(
            mid, ts, tel, errs, fails, maint, mach, baseline=baseline
        )
        add_model_prediction(pkg, model, feat_df, threshold, feat_imp)
        report = generate_role_reports(pkg)

        all_cases.append((label, outcome, report, pkg))
        print_case_report(report, label, pkg, outcome)

    # 6. 분석 보고
    print_analysis(all_cases)


if __name__ == '__main__':
    main()
