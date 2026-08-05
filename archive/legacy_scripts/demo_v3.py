"""
v3 재검증 — 정보 제시 방식 + 임계 1.75 적용.

고정 케이스 (demo_fp_v2.py 에서 선정):
  TP: 장비 11  @  2015-10-01 17:00
  FP: 장비 4   @  2015-10-13 06:00
  FN: 장비 23  @  2015-10-01 06:00
  TN: 장비 1   @  2015-10-01 00:00

모델/피처는 캐시(cache/) 에서 로드. 없으면 학습 후 저장.
"""

from __future__ import annotations
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from evidence_package  import load_data, compute_global_baseline, generate_evidence_package
from baseline_model    import (
    build_features, build_labels, train_baseline,
    add_model_prediction, TRAIN_CUTOFF, FEATURE_COLS,
)
from report_generator  import generate_role_reports

DATA_DIR  = Path(__file__).parent / 'archive'
CACHE_DIR = Path(__file__).parent / 'cache'

# 직전 demo_fp_v2.py 선정 케이스
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


# ── 캐시 저장/로드 ────────────────────────────────────────────────────────────

def _save_cache(model, threshold: float, feat_df: pd.DataFrame,
                val_df: pd.DataFrame, feat_imp: dict, metrics: dict):
    CACHE_DIR.mkdir(exist_ok=True)
    with open(CACHE_DIR / 'model.pkl',    'wb') as f: pickle.dump(model, f)
    with open(CACHE_DIR / 'threshold.pkl', 'wb') as f: pickle.dump(threshold, f)
    with open(CACHE_DIR / 'feat_imp.pkl', 'wb') as f: pickle.dump(feat_imp, f)
    with open(CACHE_DIR / 'metrics.json', 'w')  as f: json.dump(metrics, f, indent=2)
    feat_df.to_pickle(CACHE_DIR / 'feat_df.pkl')
    val_df.to_pickle( CACHE_DIR / 'val_df.pkl')
    print("  캐시 저장 완료 →", CACHE_DIR)


def _load_cache():
    try:
        with open(CACHE_DIR / 'model.pkl',     'rb') as f: model     = pickle.load(f)
        with open(CACHE_DIR / 'threshold.pkl', 'rb') as f: threshold = pickle.load(f)
        with open(CACHE_DIR / 'feat_imp.pkl',  'rb') as f: feat_imp  = pickle.load(f)
        with open(CACHE_DIR / 'metrics.json',  'r')  as f: metrics   = json.load(f)
        feat_df = pd.read_pickle(CACHE_DIR / 'feat_df.pkl')
        val_df  = pd.read_pickle(CACHE_DIR / 'val_df.pkl')
        print("  캐시 로드 완료 ←", CACHE_DIR)
        return model, threshold, feat_df, val_df, feat_imp, metrics
    except Exception as e:
        print(f"  캐시 없음 또는 오류 ({e}). 새로 학습합니다.")
        return None


def load_or_train(tel: pd.DataFrame, fails: pd.DataFrame):
    cached = _load_cache()
    if cached:
        return cached

    print("  피처/라벨 구축 중...")
    feat_df  = build_features(tel)
    feat_lab = build_labels(feat_df, fails)

    print("  모델 학습 중...")
    model, threshold, val_df, metrics, feat_imp = train_baseline(feat_lab)
    _save_cache(model, threshold, feat_df, val_df, feat_imp, metrics)
    return model, threshold, feat_df, val_df, feat_imp, metrics


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

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
    prob  = mp.get('probability', '?')
    thr   = mp.get('threshold', '?')
    alarm = mp.get('alarm_triggered', '?')
    flags = pkg['status_flags']
    hyps  = pkg['component_hypotheses']

    print(f"\n{_hr()}")
    print(f"  [{lbl}]  {outcome}")
    print(f"  장비 {report['machine_id']}  ·  {report['timestamp'][:16]}")
    print(
        f"  모델: 확률={prob if prob=='?' else f'{prob:.4f}'}  "
        f"임계={thr if thr=='?' else f'{thr:.4f}'}  알람={alarm}"
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
    """all_cases: [(label, report, pkg), ...]"""
    print(f"\n{_hr('═')}")
    print("  분석 보고")
    print(_hr('═'))

    by = {lbl: (rpt, pkg) for lbl, rpt, pkg in all_cases}

    # 1. 매니저/엔지니어 출력 차이
    print("\n[1] 억제 로직 제거 후 매니저/엔지니어 출력이 여전히 다른가")
    for lbl, (rpt, _) in by.items():
        mgr_t = [b['type'] for b in rpt['manager']]
        eng_t = [b['type'] for b in rpt['engineer']]
        mgr_only = sorted(set(mgr_t) - set(eng_t))
        eng_only = sorted(set(eng_t) - set(mgr_t))
        shared   = sorted(set(mgr_t) & set(eng_t))
        print(f"\n  {lbl}: 매니저={len(mgr_t)}블록  엔지니어={len(eng_t)}블록")
        if shared:   print(f"    공통: {shared}")
        if mgr_only: print(f"    매니저 전용: {mgr_only}")
        if eng_only: print(f"    엔지니어 전용: {eng_only}")

    # 2. FP 케이스 — "이건 아닐 수도 있다" 판단 재료
    print("\n[2] FP 케이스 — 사람이 판단할 재료가 제시되는가")
    fp_rpt, fp_pkg = by.get('FP', (None, None))
    if fp_pkg:
        mp = fp_pkg.get('model_prediction', {})
        ec = fp_pkg['error_context']
        maint = fp_pkg['maintenance_context']
        hyps  = fp_pkg['component_hypotheses']

        print(f"  장비 4  모델 확률: {mp.get('probability','?'):.4f}  알람: {mp.get('alarm_triggered')}")
        print(f"  선행 에러: {ec['count']}건  z임계초과: {len(hyps)}건")

        # 교체 이력 — 사람이 '최근 교체 후 과도 반응' 가능성을 판단할 재료
        for comp, info in maint.items():
            if info.get('days_elapsed') is not None:
                print(f"  {comp}: 교체 {info['days_elapsed']:.0f}일 경과 / {info['type']}")

        print("  → 에러 선행·단일 센서 이상·최근 교체 이력이 모두 리포트에 제시됨.")
        print("    '최근 교체된 comp3가 압력 이상' 패턴을 사람이 읽어야 한다.")
        print("    리포트는 이 패턴을 '억제 근거'로 처리하지 않고 사실로만 나열.")

    # 3. 임계 1.75 적용 후 부품 후보 변화 (특히 FN)
    print("\n[3] 임계 1.75 적용 후 FN 케이스 변화")
    fn_rpt, fn_pkg = by.get('FN', (None, None))
    if fn_pkg:
        from report_generator import _all_candidates, NEW_Z_THRESHOLD
        cands = _all_candidates(fn_pkg)
        print(f"  장비 23  임계: {NEW_Z_THRESHOLD}")
        for c in cands:
            z    = c['z']
            mark = '[임계 초과]' if c['above'] else '[임계 미달]'
            z_s  = f"z={z:+.3f}" if z is not None else 'z=N/A'
            print(f"    {c['comp']} / {c['sensor']}: {z_s}  {mark}")
        print()
        above = [c for c in cands if c['above']]
        if above:
            print(f"  → 임계 초과 {len(above)}건: "
                  + ', '.join(f"{c['sensor']}(z={c['z']:+.2f})" for c in above))
            print("    이전 임계 2.0에서는 임계 미달이었던 항목이 이제 '임계 초과'로 표기됨.")
        else:
            print("  → 임계 1.75에서도 초과 항목 없음.")

        print(f"\n  FN 모델 확률: {fn_pkg.get('model_prediction', {}).get('probability','?'):.4f}  (알람 없음)")
        print("  → 모델이 알람 미발생. 리포트 자체에는 경보 신호 없음.")
        print("     에러 전환율 블록에 에러 이력이 표시되어 있음 — 사람이 참조해야 함.")

    # 4. TP vs FP — 리포트 차이 요약
    print("\n[4] TP vs FP 리포트 핵심 차이 (억제 없이)")
    tp_rpt, tp_pkg = by.get('TP', (None, None))
    fp_rpt, fp_pkg = by.get('FP', (None, None))
    if tp_pkg and fp_pkg:
        def _block_texts(rpt, type_):
            return next((b['text'] for b in rpt['manager'] if b['type'] == type_), '')

        tp_ec = tp_pkg['error_context']['count']
        fp_ec = fp_pkg['error_context']['count']
        tp_hyp = len(tp_pkg['component_hypotheses'])
        fp_hyp = len(fp_pkg['component_hypotheses'])
        tp_npe = tp_pkg['status_flags']['no_prior_error']
        fp_npe = fp_pkg['status_flags']['no_prior_error']

        print(f"  TP: 선행에러={tp_ec}건  z임계초과={tp_hyp}건  no_prior_error={tp_npe}")
        print(f"  FP: 선행에러={fp_ec}건  z임계초과={fp_hyp}건  no_prior_error={fp_npe}")
        print()
        print("  구분 재료 (리포트가 제시하는 사실):")
        if tp_ec > 0 and fp_ec > 0:
            print("    - 에러 선행: 둘 다 있음. 전환율 수치를 사람이 비교해야 함.")
        if tp_hyp != fp_hyp:
            print(f"    - z임계초과: TP={tp_hyp}건  FP={fp_hyp}건")
        print("    - 억제 판단 없음 → 사람이 에러 전환율·교체 이력·z 값을 보고 결정.")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("데이터 로딩...")
    tel, errs, fails, maint, mach = load_data(DATA_DIR)
    baseline = compute_global_baseline(tel)
    print("완료.")

    print("\n모델/피처 준비 (캐시 확인)...")
    model, threshold, feat_df, val_df, feat_imp, metrics = load_or_train(tel, fails)
    print(f"  threshold={metrics['threshold']}  PR-AUC={metrics['pr_auc']}\n")

    print("리포트 생성 (임계 1.75 적용)...")
    all_cases: list[tuple[str, dict, dict]] = []

    for lbl, (mid, ts) in CASES.items():
        print(f"  [{lbl}] 장비 {mid} @ {str(ts)[:16]}")
        pkg    = generate_evidence_package(
            mid, ts, tel, errs, fails, maint, mach, baseline=baseline
        )
        add_model_prediction(pkg, model, feat_df, threshold, feat_imp)
        report = generate_role_reports(pkg)
        all_cases.append((lbl, report, pkg))
        print_case(lbl, report, pkg, OUTCOMES[lbl])

    print_analysis(all_cases)


if __name__ == '__main__':
    main()
