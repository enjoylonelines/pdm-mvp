"""
FP 검증 v2 — 케이스 재선정 + 억제 강도 체계 적용.

변경 사항:
  TP  : 고장 12~24h 전 · z이상 ≥1
  FP  : 억제 근거 0개 (또는 최소 개수) — 확률 최고값 기준 아님
  FN  : TP와 다른 장비·다른 고장 이벤트 · 확률이 임계보다 확실히 낮음
  억제: S2는 이상 센서 연관 부품 일치 시에만 · S4는 |z|<3.0 조건 추가

규칙 밖 항목 재분류 결과는 실행 말미에 출력됨.
"""

from __future__ import annotations
from pathlib import Path

import pandas as pd

from evidence_package import load_data, compute_global_baseline, generate_evidence_package
from baseline_model   import (
    build_features, build_labels, train_baseline,
    add_model_prediction, find_cases_v2, TRAIN_CUTOFF,
)
from report_generator import (
    generate_role_reports,
    _get_suppression_evidence,
    _supp_total_strength,
)

DATA_DIR = Path(__file__).parent / 'archive'


# ── 콜백 팩토리 (find_cases_v2에 넘길 클로저) ───────────────────────────────

def make_callbacks(tel, errs, fails, maint, mach, baseline, model, feat_df, threshold, feat_imp):

    def generate_pkg(mid: int, ts: pd.Timestamp) -> dict:
        pkg = generate_evidence_package(mid, ts, tel, errs, fails, maint, mach, baseline=baseline)
        add_model_prediction(pkg, model, feat_df, threshold, feat_imp)
        return pkg

    def count_supp(pkg: dict) -> tuple[int, int]:
        ev = _get_suppression_evidence(pkg)
        return len(ev), _supp_total_strength(ev)

    return generate_pkg, count_supp


# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────────

def _hr(ch: str = '=', n: int = 74) -> str:
    return ch * n


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


def print_case(label: str, meta: dict, report: dict, pkg: dict, outcome: str):
    mp      = pkg.get('model_prediction', {})
    prob    = mp.get('probability', '?')
    thr     = mp.get('threshold',   '?')
    alarm   = mp.get('alarm_triggered', '?')
    flags   = pkg['status_flags']
    supp    = _get_suppression_evidence(pkg)
    n_supp  = len(supp)
    st_supp = _supp_total_strength(supp)

    print(f"\n{_hr()}")
    print(f"  [{label}]  {outcome}")
    print(f"  장비 {report['machine_id']}  ·  {report['timestamp'][:16]}")
    print(
        f"  모델: 확률={prob}  임계={thr}  알람={alarm}"
        f"  z이상={len(pkg['component_hypotheses'])}건"
        f"  no_prior_error={flags['no_prior_error']}"
    )
    if label in ('TP', 'FN') and meta:
        htf = meta.get('hours_to_failure')
        if htf is not None:
            print(f"  ← 실제 고장까지 {htf:.1f}시간 남은 시점")
    if label == 'FP' and meta:
        print(
            f"  억제 근거: {n_supp}개  강도 합계: {st_supp}"
            f"  (스캔 {meta.get('scan_size')}건 중 0개짜리 {meta.get('zero_supp_count')}건)"
        )
    print(_hr())

    for role_label, role_key in [('MANAGER', 'manager'), ('ENGINEER', 'engineer')]:
        print(f"\n  {'─'*28} [{role_label}] {'─'*28}")
        for block in report[role_key]:
            _print_block(block)


# ── 분석 보고 ─────────────────────────────────────────────────────────────────

def print_analysis(cases: list[tuple[str, dict, dict, dict]]):
    """cases: [(label, meta, report, pkg), ...]"""
    print(f"\n{_hr('═')}")
    print("  분석 보고")
    print(_hr('═'))

    by_label = {label: (meta, report, pkg) for label, meta, report, pkg in cases}

    # 1. 재선정 근거
    print("\n[1] 재선정된 케이스 선정 근거")
    for label, meta, report, pkg in cases:
        mid = pkg['machine_id']
        ts  = pkg['timestamp'][:16]
        mp  = pkg.get('model_prediction', {})
        supp = _get_suppression_evidence(pkg)
        n_s  = len(supp)
        st_s = _supp_total_strength(supp)

        line = f"  {label}: 장비 {mid} @ {ts}  prob={mp.get('probability', '?'):.4f}"
        if label in ('TP', 'FN') and meta:
            htf = meta.get('hours_to_failure')
            if htf:
                line += f"  고장까지 {htf:.1f}h"
        if label in ('TP', 'FP', 'FN'):
            line += f"  억제근거={n_s}개(강도{st_s})"
        print(line)

    # 2. FP vs TP 억제 강도 비교
    print("\n[2] TP vs FP 억제 강도 비교")
    tp_meta, tp_report, tp_pkg = by_label.get('TP', (None, None, None))
    fp_meta, fp_report, fp_pkg = by_label.get('FP', (None, None, None))

    for lbl, pkg in [('TP', tp_pkg), ('FP', fp_pkg)]:
        if pkg is None:
            continue
        supp = _get_suppression_evidence(pkg)
        total = _supp_total_strength(supp)
        print(f"  {lbl}: 억제 근거 {len(supp)}개  강도 합계 {total}")
        for e in supp:
            print(f"      [{e['rule']} 강도{e['strength']}] {e['text']}")
        if not supp:
            print("      (억제 근거 없음)")

    # 3. FP 0-억제 리포트 — 무엇을 말하는가
    print("\n[3] FP 0-억제 리포트 — 근거 없이 '위험' 만 말하는가, 불확실성을 표현하는가")
    if fp_pkg:
        supp = _get_suppression_evidence(fp_pkg)
        if not supp:
            print("  ← FP 케이스의 억제 근거 0개. alarm_context 블록 원문:")
            for role in ('manager', 'engineer'):
                for block in fp_report[role]:
                    if block['type'] == 'alarm_context':
                        print(f"\n  [{role.upper()}] {block['title']}")
                        for line in block['text'].splitlines():
                            print(f"    {line}")
        else:
            print(f"  FP 케이스가 억제 근거 {len(supp)}개 보유 (0개짜리 FP 없음)")
            print("  → 아래 전체 보고 [FP 억제 근거 최소] 케이스의 alarm_context 참조")

    # 4. FN 케이스 — 리포트가 놓친 것
    print("\n[4] FN 케이스 — 리포트가 놓친 것")
    fn_meta, fn_report, fn_pkg = by_label.get('FN', (None, None, None))
    if fn_pkg:
        mp   = fn_pkg.get('model_prediction', {})
        hyps = fn_pkg['component_hypotheses']
        errs = fn_pkg['error_context']['count']
        htf  = fn_meta.get('hours_to_failure') if fn_meta else '?'
        print(f"  고장 {htf:.1f}h 전  prob={mp.get('probability','?'):.4f}  z이상={len(hyps)}건  에러={errs}건")
        if hyps:
            print("  → z이상 있음 — 규칙 기반 리포트에는 부품 후보 노출됨")
            print("  → 그러나 모델 알람 없음 → alarm_context 블록 생성 안 됨")
            print("  → '이상 후보는 있지만 모델은 침묵' 이 리포트에서 드러나는 갭")
        else:
            print("  → z이상 없음 + 모델 침묵 = 리포트에 경보 신호 없음")
            if errs > 0:
                print(f"  → 에러 {errs}건이 선행했음에도 탐지 실패: 에러 전환율을 참고해야 함")
    else:
        print("  FN 케이스 없음")

    # 5. 규칙 밖 항목 재분류
    print("\n[5] 규칙 밖 항목 재분류")
    print("""
  ┌─ 데이터로 계산 가능 → 구현됨
  │  S4 개선: |z| ≥ 3.0이면 동급 백분위 비교로 억제 불가 (절대 편차 우선)
  │  S2 개선: 비연관 부품 교체는 억제 근거에서 제외 (센서-부품 매칭)
  │
  └─ 데이터에 답이 없음 → 설명하지 않음 (LLM에 넘기지 않음)
     × 교체 후 안정화 완료 여부: 현재 창 이후 추이 데이터 없음
     × 동급 장비 전체가 이상인지: pkg에 피어 절대 z-score 없음
     × 반복 알람 패턴 해석: pkg에 과거 알람 이력 없음
     × 운영 맥락(공휴일·계획 정지·부하 감소): 텔레메트리 외 데이터 없음
     × FN이 왜 탐지 안 됐는가: 피처에 없는 패턴은 추론 불가
       → 위 항목들은 리포트에서 생략하거나 '근거 없음'으로만 표기
""")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("데이터 로딩...")
    tel, errs, fails, maint, mach = load_data(DATA_DIR)
    baseline = compute_global_baseline(tel)
    print("완료.\n")

    print("피처/라벨 구축...")
    feat_df  = build_features(tel)
    feat_lab = build_labels(feat_df, fails)
    print("완료.\n")

    print("모델 학습 (직전 모델과 동일, random_state=42)...")
    model, threshold, val_df, metrics, feat_imp = train_baseline(feat_lab)
    print(f"  PR-AUC={metrics['pr_auc']}  정밀도={metrics['precision']}"
          f"  재현율={metrics['recall']}  임계={metrics['threshold']}")
    print(f"  TP={metrics['tp']}  FP={metrics['fp']}"
          f"  FN={metrics['fn']}  TN={metrics['tn']}\n")

    # 콜백 생성
    gen_pkg, count_supp = make_callbacks(
        tel, errs, fails, maint, mach, baseline,
        model, feat_df, threshold, feat_imp,
    )

    print(f"케이스 선정 중 (FP 풀 최대 150건 스캔)...")
    cases_meta = find_cases_v2(
        val_df, fails, gen_pkg, count_supp, threshold,
        fp_scan_size=150,
    )

    print("\n선정 결과:")
    for lbl, meta in cases_meta.items():
        if meta:
            mid = meta['mid']
            ts  = meta['ts']
            extra = ""
            if 'hours_to_failure' in meta:
                extra = f"  고장까지 {meta['hours_to_failure']:.1f}h"
            if 'n_rules' in meta:
                extra += (f"  억제근거={meta['n_rules']}개"
                          f"  강도={meta['total_strength']}"
                          f"  (0개짜리={meta['zero_supp_count']}건)")
            print(f"  {lbl}: 장비 {mid} @ {str(ts)[:16]}{extra}")
        else:
            print(f"  {lbl}: 선정 실패")
    print()

    # 리포트 생성 + 출력
    outcomes = {
        'TP': "알람 발생 · 실제 24h 내 고장 (True Positive)",
        'FP': "알람 발생 · 실제 고장 없음 (False Positive) ← 핵심",
        'FN': "알람 없음 · 실제 고장 발생 (False Negative)",
        'TN': "알람 없음 · 고장 없음 (True Negative)",
    }

    all_cases: list[tuple[str, dict, dict, dict]] = []
    for lbl in ['TP', 'FP', 'FN', 'TN']:
        meta = cases_meta.get(lbl)
        if not meta:
            print(f"[{lbl}] 없음 — 스킵")
            continue
        mid = meta['mid']
        ts  = meta['ts']
        print(f"리포트 생성: [{lbl}] 장비 {mid} @ {str(ts)[:16]}")
        pkg    = gen_pkg(mid, ts)
        report = generate_role_reports(pkg)
        all_cases.append((lbl, meta, report, pkg))
        print_case(lbl, meta, report, pkg, outcomes[lbl])

    print_analysis(all_cases)


if __name__ == '__main__':
    main()
