"""
v4 재생성 — 분모 정본 적용 + 부품별 임계 + excess_ratio 정렬.

고정 케이스:
  TP: 장비 11  @  2015-10-01 17:00
  FP: 장비 4   @  2015-10-13 06:00
  FN: 장비 23  @  2015-10-01 06:00
  TN: 장비 1   @  2015-10-01 00:00
"""
from __future__ import annotations
import json
from pathlib import Path

import pandas as pd

from evidence_package  import load_data, compute_global_baseline, generate_evidence_package
from baseline_model    import build_features, build_labels, train_baseline, add_model_prediction, TRAIN_CUTOFF
from report_generator  import generate_role_reports, _all_candidates, _comp_threshold
from z_baseline        import load_thresholds_full, DEFAULT_THRESHOLD_PATH

DATA_DIR  = Path(__file__).parent / 'archive'
CACHE_DIR = Path(__file__).parent / 'cache'

CASES = {
    'TP': (11, pd.Timestamp('2015-10-01 17:00:00')),
    'FP': (4,  pd.Timestamp('2015-10-13 06:00:00')),
    'FN': (23, pd.Timestamp('2015-10-01 06:00:00')),
    'TN': (1,  pd.Timestamp('2015-10-01 00:00:00')),
}
OUTCOMES = {
    'TP': "알람 발생 · 실제 24h 내 고장",
    'FP': "알람 발생 · 실제 고장 없음 ← 핵심",
    'FN': "알람 없음 · 실제 고장 발생",
    'TN': "알람 없음 · 고장 없음",
}


# ── 캐시 로드 (모델) ──────────────────────────────────────────────────────────

def load_model():
    import pickle
    with open(CACHE_DIR / 'model.pkl',    'rb') as f: model     = pickle.load(f)
    with open(CACHE_DIR / 'threshold.pkl','rb') as f: threshold = pickle.load(f)
    with open(CACHE_DIR / 'feat_imp.pkl', 'rb') as f: feat_imp  = pickle.load(f)
    feat_df = pd.read_pickle(CACHE_DIR / 'feat_df.pkl')
    return model, threshold, feat_df, feat_imp


# ── 출력 ─────────────────────────────────────────────────────────────────────

def _hr(ch='=', n=74): return ch * n


def _print_block(block: dict):
    print(f"\n  ┌─ {block['title']}")
    for line in block['text'].splitlines():
        print(f"  │  {line}")
    if block['source_fields']:
        shown  = block['source_fields'][:4]
        extra  = len(block['source_fields']) - 4
        suffix = f" +{extra}개" if extra > 0 else ""
        print(f"  │  [근거: {', '.join(shown)}{suffix}]")
    print(f"  └{'─'*52}")


def print_case(lbl: str, report: dict, pkg: dict, outcome: str):
    mp    = pkg.get('model_prediction', {})
    flags = pkg['status_flags']
    hyps  = pkg['component_hypotheses']

    print(f"\n{_hr()}")
    print(f"  [{lbl}]  {outcome}")
    print(f"  장비 {report['machine_id']}  ·  {report['timestamp'][:16]}")
    print(
        f"  모델: 확률={mp.get('probability','?'):.4f}  "
        f"임계={mp.get('threshold','?'):.4f}  알람={mp.get('alarm_triggered','?')}"
    )
    print(
        f"  z임계초과={len(hyps)}건  "
        f"no_prior_error={flags['no_prior_error']}  "
        f"multiple_candidates={flags['multiple_candidates']}"
    )
    print(_hr())

    for role_label, role_key in [('MANAGER', 'manager'), ('ENGINEER', 'engineer')]:
        print(f"\n  {'─'*28} [{role_label}] {'─'*28}")
        for block in report[role_key]:
            _print_block(block)


# ── 분석 보고 ─────────────────────────────────────────────────────────────────

def print_analysis(all_cases: list[tuple[str, dict, dict]]):
    print(f"\n{_hr('═')}")
    print("  분석 보고")
    print(_hr('═'))

    by = {lbl: (rpt, pkg) for lbl, rpt, pkg in all_cases}
    thresholds_full = load_thresholds_full(DEFAULT_THRESHOLD_PATH)

    # 1. 확정 임계 + 과적합 여부
    print("\n[1] 부품별 확정 임계 및 학습/검증 성능")
    print(f"  {'부품':6s}  {'임계':>6s}  {'학습미탐':>8s}  {'학습정밀':>8s}  {'검증미탐':>8s}  {'검증정밀':>8s}  과적합  비고")
    print(f"  {'─'*6}  {'─'*6}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*6}  {'─'*30}")
    for comp in ['comp1', 'comp2', 'comp3', 'comp4']:
        d = thresholds_full[comp]
        tr_miss = d.get('train_miss_pct', 0)
        vl_miss = d.get('val_miss_pct', 0)
        tr_prec = d.get('train_prec_pct', 0)
        vl_prec = d.get('val_prec_pct', 0)
        overfit_miss = abs(vl_miss - tr_miss) > 5
        overfit_prec = abs(vl_prec - tr_prec) > 5
        overfit = '⚠' if (overfit_miss or overfit_prec) else 'OK'
        note_short = d.get('note', '')[:30]
        print(f"  {comp:6s}  {d['threshold']:>6.2f}  {tr_miss:>7.1f}%  {tr_prec:>7.1f}%  "
              f"{vl_miss:>7.1f}%  {vl_prec:>7.1f}%  {overfit:^6s}  {note_short}")

    # 2. 매니저/엔지니어 차이
    print("\n[2] 매니저 vs 엔지니어 블록 수")
    for lbl, (rpt, _) in by.items():
        print(f"  {lbl}: 매니저={len(rpt['manager'])}  엔지니어={len(rpt['engineer'])}")

    # 3. FN 케이스 압력 상황
    print("\n[3] FN 케이스 (장비 23) — 새 정의에서 부품 후보")
    fn_rpt, fn_pkg = by.get('FN', (None, None))
    if fn_pkg:
        cands = _all_candidates(fn_pkg)
        for c in cands:
            thr = c['threshold']
            er  = c['excess_ratio']
            z   = c['z']
            mark = '임계 초과' if c['above'] else '임계 미달'
            print(f"  {c['comp']}/{c['sensor']}: z={z:+.3f}  배수={er:.2f}×  임계={thr:.2f}  [{mark}]")
        print()
        mp = fn_pkg.get('model_prediction', {})
        print(f"  모델 확률 {mp.get('probability','?'):.4f} (알람 없음)")
        print("  → 모델 알람 없음 = 규칙 기반 리포트의 고장 경보도 없음")
        print("  → 압력의 excess_ratio 로 '임계에 얼마나 근접했는가' 표시 가능")

    # 4. comp1/comp2 조기 탐지 한계
    print("\n[4] comp1/comp2 조기 탐지 한계")
    for comp in ['comp1', 'comp2']:
        d = thresholds_full[comp]
        print(f"  {comp}: 임계 {d['threshold']:.2f}")
        print(f"    학습 미탐 {d.get('train_miss_pct',0):.1f}%  검증 미탐 {d.get('val_miss_pct',0):.1f}%")
        print(f"    {d.get('note','')}")

    # 5. 혼동행렬 (이벤트 단위, 학습/검증)
    print("\n[5] 부품별 임계 도입 후 혼동행렬 — 이벤트 단위")
    print("  (z 규칙 기반 판정. 모델 알람이 아닌 z임계 초과 기준)")
    print(f"\n  {'구간':6s}  {'부품':6s}  {'TP':>6s}  {'FP':>10s}  {'FN':>6s}  "
          f"{'Prec':>8s}  {'Recall':>8s}  {'F1':>8s}")
    print(f"  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*10}  {'─'*6}  "
          f"{'─'*8}  {'─'*8}  {'─'*8}")
    for comp in ['comp1', 'comp2', 'comp3', 'comp4']:
        d = thresholds_full[comp]
        for period, pfx in [('학습', 'train'), ('검증', 'val')]:
            tp  = d.get(f'{pfx}_n_events', 0) - d.get(f'{pfx}_miss_n', 0)
            fn_ = d.get(f'{pfx}_miss_n', 0)
            n_e = d.get(f'{pfx}_n_events', 0)
            fp  = d.get(f'{pfx}_norm_rows', 0)  # 행 단위 (이벤트 단위 FP 없음)
            if n_e == 0:
                continue
            prec   = tp / (tp + fp) * 100 if (tp + fp) > 0 else 0
            recall = tp / n_e * 100 if n_e > 0 else 0
            f1     = 2*prec*recall/(prec+recall) if (prec+recall) > 0 else 0
            # TP/FN은 이벤트 단위, FP는 행 단위 — 혼동 방지를 위해 명시
            print(f"  {period:6s}  {comp:6s}  {tp:>6d}  {fp:>10,}행  {fn_:>6d}  "
                  f"{prec:>7.1f}%  {recall:>7.1f}%  {f1:>7.1f}%")
    print("  ※ FP는 행 단위 (정상 구간에서 임계 초과한 행 수). TP/FN은 이벤트 단위.")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("데이터 로딩...")
    tel, errs, fails, maint, mach = load_data(DATA_DIR)
    baseline = compute_global_baseline()   # JSON 로드
    print("완료.\n")

    print("모델 로드 (캐시)...")
    model, threshold, feat_df, feat_imp = load_model()
    print(f"  threshold={threshold:.4f}\n")

    print("리포트 생성 (부품별 임계 적용)...")
    all_cases: list[tuple[str, dict, dict]] = []

    for lbl, (mid, ts) in CASES.items():
        print(f"  [{lbl}] 장비 {mid} @ {str(ts)[:16]}")
        pkg    = generate_evidence_package(mid, ts, tel, errs, fails, maint, mach, baseline=baseline)
        add_model_prediction(pkg, model, feat_df, threshold, feat_imp)
        report = generate_role_reports(pkg)
        all_cases.append((lbl, report, pkg))
        print_case(lbl, report, pkg, OUTCOMES[lbl])

    print_analysis(all_cases)


if __name__ == '__main__':
    main()
