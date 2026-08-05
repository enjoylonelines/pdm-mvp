"""
분포 측정 스크립트 — 규칙 변경 없음. 측정과 보고만.

1. z 대역별 분포 (고장창 vs 정상)
2. 임계 후보별 정밀도/미탐 trade-off
3. 동급 비교 vs 글로벌 z 실익
4. TP/FP 억제 강도 분포 비교
"""

from __future__ import annotations
from pathlib import Path

import numpy as np
import pandas as pd

from evidence_package import (
    load_data, compute_global_baseline, generate_evidence_package,
)
from baseline_model import (
    build_features, build_labels, train_baseline,
    add_model_prediction, TRAIN_CUTOFF, FEATURE_COLS,
)
from report_generator import _get_suppression_evidence, _supp_total_strength

DATA_DIR = Path(__file__).parent / 'archive'

SENSORS = ['volt', 'rotate', 'pressure', 'vibration']

# 센서 → (부품, 방향)
SENSOR_COMP_DIR = {
    'volt':      ('comp1', 'both'),
    'rotate':    ('comp2', 'negative'),
    'pressure':  ('comp3', 'both'),
    'vibration': ('comp4', 'both'),
}

BANDS      = [(1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, np.inf)]
THRESHOLDS = [1.5, 1.75, 2.0, 2.25, 2.5]


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────────

def effective_z(feat_df: pd.DataFrame, sensor: str, direction: str,
                baseline: pd.DataFrame) -> np.ndarray:
    """
    부품 이상 감지 방향에 맞춘 유효 z값.
    both     → |z|
    negative → max(-z, 0)   (음의 이탈만 카운트)
    """
    gm    = float(baseline.loc['mean', sensor])
    gs    = float(baseline.loc['std',  sensor])
    raw_z = ((feat_df[f'{sensor}_mean24h'] - gm) / gs).values
    if direction == 'negative':
        return np.maximum(-raw_z, 0.0)
    return np.abs(raw_z)


def label_component_windows(
    feat_df: pd.DataFrame,
    failures: pd.DataFrame,
    comp: str,
    fail_h: int = 24,
    excl_d: int = 7,
) -> np.ndarray:
    """
    각 행을 'failure' / 'exclude' / 'normal' 로 레이블링.
    failure : 해당 부품 고장 24h 전 창
    exclude : 고장 7일 전~24h 전 (집계 제외)
    normal  : 고장과 7일 이상 떨어진 구간

    pandas boolean mask 방식을 사용하여 인덱스 불일치 문제를 회피.
    반환: len(feat_df) 크기 numpy 문자열 배열 (행 순서 대응).
    """
    comp_fails = failures[failures['failure'] == comp]
    # numpy 배열: feat_df 행 순서와 동일, bool 마스크로 직접 인덱싱
    labels = np.full(len(feat_df), 'normal', dtype=object)

    mid_arr = feat_df['machineID'].values
    dt_arr  = feat_df['datetime'].values  # datetime64[ns]

    for _, fr in comp_fails.iterrows():
        mid = fr['machineID']
        ft  = fr['datetime']
        ws  = ft - pd.Timedelta(hours=fail_h)
        xs  = ft - pd.Timedelta(days=excl_d)

        mid_mask  = mid_arr == mid
        fail_mask = mid_mask & (dt_arr > ws.to_datetime64()) & (dt_arr <= ft.to_datetime64())
        excl_mask = mid_mask & (dt_arr > xs.to_datetime64()) & (dt_arr <= ws.to_datetime64())

        labels[fail_mask] = 'failure'
        labels[excl_mask] = 'exclude'

    return labels


# ── Section 1: z 대역별 분포 ─────────────────────────────────────────────────

def section1_z_band_distribution(feat_df: pd.DataFrame,
                                  failures: pd.DataFrame,
                                  baseline: pd.DataFrame) -> list[dict]:
    print("\n" + "═" * 72)
    print("  [1] z 대역별 분포  (고장창 = 고장 24h 전 / 정상 = 고장 7일 밖)")
    print("═" * 72)

    valid    = feat_df.dropna(subset=FEATURE_COLS)
    agg_list = []   # 합산용

    for sensor, (comp, direction) in SENSOR_COMP_DIR.items():
        dir_label = '양방향' if direction == 'both' else '음(-)방향만'
        print(f"\n  {sensor}/{comp}  [{dir_label}]")
        print(f"  {'대역':12s}  {'고장창':>10s}  {'정상':>10s}  {'고장창 정밀도':>12s}")
        print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*12}")

        labels = label_component_windows(valid, failures, comp)
        ez     = effective_z(valid, sensor, direction, baseline)

        fail_m   = labels == 'failure'
        normal_m = labels == 'normal'

        for lo, hi in BANDS:
            band_m  = (ez >= lo) & (ez < hi)
            n_fail  = int((band_m & fail_m).sum())
            n_norm  = int((band_m & normal_m).sum())
            total   = n_fail + n_norm
            prec    = n_fail / total * 100 if total else 0.0
            hi_s    = f"{hi:.1f}" if hi < np.inf else "∞"
            print(f"  [{lo:.1f},{hi_s}){'':<3}  {n_fail:>10,}  {n_norm:>10,}  {prec:>11.1f}%")
            agg_list.append({'lo': lo, 'hi': hi, 'n_fail': n_fail, 'n_norm': n_norm})

    # 합산
    print(f"\n  합산 (4종 센서 전체)")
    print(f"  {'대역':12s}  {'고장창':>10s}  {'정상':>10s}  {'고장창 정밀도':>12s}")
    print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*12}")
    agg: dict[tuple, dict] = {}
    for r in agg_list:
        k = (r['lo'], r['hi'])
        if k not in agg:
            agg[k] = {'n_fail': 0, 'n_norm': 0}
        agg[k]['n_fail'] += r['n_fail']
        agg[k]['n_norm'] += r['n_norm']

    for (lo, hi), v in sorted(agg.items()):
        nf, nn = v['n_fail'], v['n_norm']
        tot    = nf + nn
        prec   = nf / tot * 100 if tot else 0.0
        hi_s   = f"{hi:.1f}" if hi < np.inf else "∞"
        print(f"  [{lo:.1f},{hi_s}){'':<3}  {nf:>10,}  {nn:>10,}  {prec:>11.1f}%")

    return agg_list


# ── Section 2: 임계 후보별 trade-off ─────────────────────────────────────────

def section2_threshold_tradeoff(feat_df: pd.DataFrame,
                                 failures: pd.DataFrame,
                                 baseline: pd.DataFrame):
    print("\n" + "═" * 72)
    print("  [2] 임계 후보별 trade-off")
    print("═" * 72)
    print("  행 수준 정밀도 = 고장창 행 / (고장창 + 정상 행 중 임계 초과)")
    print("  이벤트 미탐   = 고장 이벤트 중 24h 창 전체에서 임계를 못 넘은 건수")
    print()

    valid   = feat_df.dropna(subset=FEATURE_COLS)

    # 센서별 레이블 + 유효z를 미리 계산
    pre: dict[str, dict] = {}
    for sensor, (comp, direction) in SENSOR_COMP_DIR.items():
        labels = label_component_windows(valid, failures, comp)
        ez     = effective_z(valid, sensor, direction, baseline)
        pre[sensor] = {
            'labels': labels, 'ez': ez,
            'comp': comp, 'direction': direction,
        }

    # 장비별 데이터 캐시 (이벤트 미탐 계산용)
    mid_cache: dict[int, pd.DataFrame] = {
        mid: grp for mid, grp in valid.groupby('machineID')
    }

    print(f"  {'임계':>6s}  {'행 후보':>10s}  {'고장창':>8s}  {'행정밀도':>8s}  {'이벤트미탐':>10s}  {'전체이벤트':>10s}")
    print(f"  {'-'*6}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*10}")

    for T in THRESHOLDS:
        tot_cand   = 0
        tot_fail   = 0
        tot_missed = 0  # 이벤트 수준 미탐
        tot_events = 0

        for sensor, pc in pre.items():
            labels = pc['labels']
            ez     = pc['ez']
            comp   = pc['comp']

            above   = ez >= T
            fail_m  = labels == 'failure'
            norm_m  = labels == 'normal'

            tot_cand += int((above & (fail_m | norm_m)).sum())
            tot_fail += int((above & fail_m).sum())

            # 이벤트 수준 미탐
            comp_fails = failures[failures['failure'] == comp]
            for _, fr in comp_fails.iterrows():
                mid = int(fr['machineID'])
                ft  = fr['datetime']
                ws  = ft - pd.Timedelta(hours=24)
                mid_df = mid_cache.get(mid, pd.DataFrame())
                if mid_df.empty:
                    tot_missed += 1
                    tot_events += 1
                    continue
                win = mid_df[(mid_df['datetime'] > ws) & (mid_df['datetime'] <= ft)]
                # ez for this window: re-derive
                win_ez = effective_z(win, sensor, pc['direction'], baseline)
                if len(win_ez) == 0 or win_ez.max() < T:
                    tot_missed += 1
                tot_events += 1

        prec = tot_fail / tot_cand * 100 if tot_cand else 0.0
        print(f"  {T:>6.2f}  {tot_cand:>10,}  {tot_fail:>8,}  {prec:>7.1f}%  {tot_missed:>10,}  {tot_events:>10,}")

    print()
    print("  ※ 임계 낮출수록: 후보 증가, 정밀도 하락, 미탐 감소")
    print("  ※ 미탐 최소화를 우선하면 임계 1.5~1.75 구간이 타당")
    print("  ※ 임계를 낮춰도 정밀도가 크게 떨어지지 않으면 낮추는 편이 유리")


# ── Section 3: 동급 비교 vs 글로벌 z ─────────────────────────────────────────

def section3_peer_vs_global(tel: pd.DataFrame,
                             feat_df: pd.DataFrame,
                             machines: pd.DataFrame,
                             baseline: pd.DataFrame):
    print("\n" + "═" * 72)
    print("  [3] 동급 비교 vs 글로벌 z 실익 확인")
    print("═" * 72)

    # 장비별 센서 전체 평균 (between-machine variation)
    machine_means = tel.groupby('machineID')[SENSORS].mean()

    print("\n  장비별 평균의 산포 vs 글로벌 표준편차:")
    print(f"  {'센서':10s}  {'글로벌μ':>10s}  {'글로벌σ':>10s}  {'장비간σ':>10s}  {'비율(%)':>8s}")
    print(f"  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*8}")
    between_ratios: dict[str, float] = {}
    for s in SENSORS:
        gm  = float(baseline.loc['mean', s])
        gs  = float(baseline.loc['std',  s])
        bm  = float(machine_means[s].std())   # between-machine std
        rat = bm / gs * 100
        between_ratios[s] = rat
        print(f"  {s:10s}  {gm:>10.4f}  {gs:>10.4f}  {bm:>10.4f}  {rat:>7.1f}%")

    # 글로벌 z vs 피어 z 상관계수 (기계별 전체 평균 기반)
    print("\n  글로벌 z vs 동급 z 상관계수 (장비 수준 전체 평균 사용):")
    print(f"  {'센서':10s}  {'N쌍':>5s}  {'상관계수':>10s}  {'평균절대차':>12s}  {'중앙절대차':>12s}")
    print(f"  {'-'*10}  {'-'*5}  {'-'*10}  {'-'*12}  {'-'*12}")

    for s in SENSORS:
        gm = float(baseline.loc['mean', s])
        gs = float(baseline.loc['std',  s])
        gz_list: list[float] = []
        pz_list: list[float] = []

        for mid in machine_means.index:
            val = float(machine_means.loc[mid, s])
            gz  = (val - gm) / gs

            minfo    = machines[machines['machineID'] == mid]
            if minfo.empty:
                continue
            model    = minfo.iloc[0]['model']
            age      = int(minfo.iloc[0]['age'])
            peer_ids = machines[
                (machines['model'] == model) &
                (machines['age'] >= age - 3) &
                (machines['age'] <= age + 3) &
                (machines['machineID'] != mid)
            ]['machineID'].values

            peer_vals = machine_means.loc[
                machine_means.index.isin(peer_ids), s
            ].dropna().values
            if len(peer_vals) < 2:
                continue
            pm  = peer_vals.mean()
            pst = peer_vals.std()
            if pst < 1e-9:
                continue
            pz = (val - pm) / pst

            gz_list.append(gz)
            pz_list.append(pz)

        if len(gz_list) < 5:
            print(f"  {s:10s}  데이터 부족")
            continue

        gz_arr = np.array(gz_list)
        pz_arr = np.array(pz_list)
        corr   = float(np.corrcoef(gz_arr, pz_arr)[0, 1])
        mae    = float(np.mean(np.abs(gz_arr - pz_arr)))
        medad  = float(np.median(np.abs(gz_arr - pz_arr)))
        print(f"  {s:10s}  {len(gz_arr):>5d}  {corr:>10.4f}  {mae:>12.4f}  {medad:>12.4f}")

    print()
    print("  판단 기준: 장비간σ/글로벌σ < 5% 이고 상관계수 > 0.95이면")
    print("  동급 비교로 얻는 z 차이가 미미 → 동급 비교 규칙 추가의 실익 없음.")
    print()
    print("  ※ 동급 비교의 실익이 있는 유일한 경우: 특정 model 군이 구조적으로")
    print("     센서값이 다를 때. 비율이 5% 미만이면 이 구조 차이가 없다는 뜻.")


# ── Section 4: TP/FP 억제 강도 분포 ─────────────────────────────────────────

def section4_suppression_distribution(
    val_df: pd.DataFrame,
    tel: pd.DataFrame, errs: pd.DataFrame,
    fails: pd.DataFrame, maint: pd.DataFrame, mach: pd.DataFrame,
    baseline: pd.DataFrame,
    model, feat_df: pd.DataFrame,
    threshold: float, feat_imp: dict,
    n_sample: int = 50,
):
    print("\n" + "═" * 72)
    print(f"  [4] 억제 강도 분포 — TP {n_sample}건 vs FP {n_sample}건")
    print("═" * 72)

    tp_pool = val_df[(val_df['alarm']) & (val_df['label'] == 1)]
    fp_pool = val_df[(val_df['alarm']) & (val_df['label'] == 0)]
    tp_samp = tp_pool.sample(min(n_sample, len(tp_pool)), random_state=42)
    fp_samp = fp_pool.sample(min(n_sample, len(fp_pool)), random_state=42)

    def compute_batch(sample_df: pd.DataFrame, tag: str) -> list[dict]:
        rows = []
        for i, (_, r) in enumerate(sample_df.iterrows(), 1):
            if i % 10 == 0:
                print(f"    {tag} {i}/{len(sample_df)} 처리 중...")
            mid = int(r['machineID'])
            ts  = r['datetime']
            pkg = generate_evidence_package(
                mid, ts, tel, errs, fails, maint, mach, baseline=baseline
            )
            add_model_prediction(pkg, model, feat_df, threshold, feat_imp)
            ev   = _get_suppression_evidence(pkg)
            st   = _supp_total_strength(ev)
            rule_set = {e['rule'] for e in ev}
            rows.append({
                'tag': tag, 'n_rules': len(ev), 'total_strength': st,
                'S1': 'S1' in rule_set, 'S2': 'S2' in rule_set,
                'S3': 'S3' in rule_set, 'S4': 'S4' in rule_set,
                'S5': 'S5' in rule_set,
                'prob': float(r['prob']),
            })
        return rows

    print(f"\n  TP {len(tp_samp)}건 처리 중...")
    tp_rows = compute_batch(tp_samp, 'TP')
    print(f"  FP {len(fp_samp)}건 처리 중...")
    fp_rows = compute_batch(fp_samp, 'FP')

    df = pd.DataFrame(tp_rows + fp_rows)
    df_tp = df[df['tag'] == 'TP']
    df_fp = df[df['tag'] == 'FP']

    # 억제 강도 분포
    print("\n  억제 강도별 건수 분포:")
    max_st = int(df['total_strength'].max())
    print(f"  {'강도':>5s}  {'TP 건':>8s}  {'TP%':>7s}  {'FP 건':>8s}  {'FP%':>7s}  {'차이':>7s}")
    print(f"  {'-'*5}  {'-'*8}  {'-'*7}  {'-'*8}  {'-'*7}  {'-'*7}")
    for st in range(0, max_st + 1):
        nt = int((df_tp['total_strength'] == st).sum())
        nf = int((df_fp['total_strength'] == st).sum())
        pt = nt / len(df_tp) * 100
        pf = nf / len(df_fp) * 100
        print(f"  {st:>5d}  {nt:>8d}  {pt:>6.1f}%  {nf:>8d}  {pf:>6.1f}%  {pt-pf:>+6.1f}%p")

    tp_mean = df_tp['total_strength'].mean()
    fp_mean = df_fp['total_strength'].mean()
    tp_med  = df_tp['total_strength'].median()
    fp_med  = df_fp['total_strength'].median()
    print(f"\n  평균 강도:   TP={tp_mean:.2f}  FP={fp_mean:.2f}  차이={tp_mean-fp_mean:+.2f}")
    print(f"  중앙값 강도: TP={tp_med:.1f}   FP={fp_med:.1f}")

    # 규칙별 발화 빈도
    print("\n  규칙별 발화 빈도:")
    print(f"  {'규칙':>5s}  {'TP발화':>8s}  {'TP%':>7s}  {'FP발화':>8s}  {'FP%':>7s}  {'차이':>7s}")
    print(f"  {'-'*5}  {'-'*8}  {'-'*7}  {'-'*8}  {'-'*7}  {'-'*7}")
    for rule in ['S1', 'S2', 'S3', 'S4', 'S5']:
        nt = int(df_tp[rule].sum())
        nf = int(df_fp[rule].sum())
        pt = nt / len(df_tp) * 100
        pf = nf / len(df_fp) * 100
        note = ''
        if rule == 'S2':
            note = '  ← 이상센서-부품 매칭'
        print(f"  {rule:>5s}  {nt:>8d}  {pt:>6.1f}%  {nf:>8d}  {pf:>6.1f}%  {pt-pf:>+6.1f}%p{note}")

    # 컷오프별 분리도
    print("\n  임의 강도 컷오프에서 TP/FP 분리도:")
    print(f"  {'컷오프':>6s}  {'TP≥컷':>8s}  {'FP≥컷':>8s}  {'차이':>7s}  해석")
    print(f"  {'-'*6}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*20}")
    for cutoff in [1, 2, 3]:
        ta = df_tp['total_strength'] >= cutoff
        fa = df_fp['total_strength'] >= cutoff
        pt = ta.mean() * 100
        pf = fa.mean() * 100
        diff = pt - pf
        note = ('TP억제↑(역효과)' if diff > 5
                else 'FP억제↑(기대방향)' if diff < -5
                else '차이미미')
        print(f"  {cutoff:>6d}  {pt:>7.1f}%  {pf:>7.1f}%  {diff:>+6.1f}%p  {note}")

    print()
    print("  ※ 억제 강도 분포에서 FP가 TP보다 높으면: 규칙이 FP를 더 억제 →기대 방향")
    print("  ※ TP가 FP보다 높으면: 고장 예정 케이스에 억제 신호가 붙음 → 역효과")


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

    print("모델 학습 (동일 파라미터)...")
    model, threshold, val_df, metrics, feat_imp = train_baseline(feat_lab)
    print(f"  threshold={metrics['threshold']}  PR-AUC={metrics['pr_auc']}\n")

    section1_z_band_distribution(feat_df, fails, baseline)
    section2_threshold_tradeoff(feat_df, fails, baseline)
    section3_peer_vs_global(tel, feat_df, mach, baseline)
    section4_suppression_distribution(
        val_df, tel, errs, fails, maint, mach, baseline,
        model, feat_df, threshold, feat_imp,
        n_sample=50,
    )


if __name__ == '__main__':
    main()
