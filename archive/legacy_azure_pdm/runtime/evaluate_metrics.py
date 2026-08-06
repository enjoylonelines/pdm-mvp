"""
지표 단위 통일 + Precision@K + 등급 체계 검증.
규칙·임계 변경 없음. 평가와 보고만.

단위 원칙:
  행 단위(row-level): TP/FP/FN/TN 모두 시간-행 기준
  이벤트 단위(event-level): TP(이벤트)/FN(이벤트)만 정의.
                            FP/TN은 이벤트 단위 정의 없음 → 별도 지표
  두 단위를 같은 표에서 비교하지 않는다.
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pandas as pd

from evidence_package import load_data, SENSOR_COMP_MAP
from baseline_model   import build_features, FEATURE_COLS
from z_baseline import (
    load_baseline, load_thresholds,
    DEFAULT_BASELINE_PATH, DEFAULT_THRESHOLD_PATH, TRAIN_CUTOFF,
)

DATA_DIR  = Path(__file__).parent / 'archive'
CACHE_DIR = Path(__file__).parent / 'cache'

K_VALUES       = [5, 10, 20, 50]
GRADE_ALARM    = 1.0   # excess_ratio ≥ 1.0
GRADE_WATCH    = 0.8   # 0.8 ≤ excess_ratio < 1.0
FAIL_HOURS     = 24
EXCL_PRE_DAYS  = 7
EXCL_POST_DAYS = 7


# ── 레이블링 ─────────────────────────────────────────────────────────────────

def label_windows(feat_df: pd.DataFrame,
                  failures: pd.DataFrame, comp: str) -> np.ndarray:
    """
    failure  : (ft-24h, ft]
    pre_excl : (ft-7d, ft-24h]
    post_excl: (ft, ft+7d]   ← 현재 정상에 포함되는 구간, 명시적 분리
    normal   : 나머지
    """
    comp_fails = failures[failures['failure'] == comp]
    labels  = np.full(len(feat_df), 'normal', dtype=object)
    mid_arr = feat_df['machineID'].values
    dt_arr  = feat_df['datetime'].values

    for _, fr in comp_fails.iterrows():
        mid = fr['machineID']
        ft  = fr['datetime']
        ws  = ft - pd.Timedelta(hours=FAIL_HOURS)
        xs  = ft - pd.Timedelta(days=EXCL_PRE_DAYS)
        pe  = ft + pd.Timedelta(days=EXCL_POST_DAYS)
        m   = mid_arr == mid

        labels[m & (dt_arr > ws.to_datetime64()) & (dt_arr <= ft.to_datetime64())] = 'failure'
        labels[m & (dt_arr > xs.to_datetime64()) & (dt_arr <= ws.to_datetime64())] = 'pre_excl'
        labels[m & (dt_arr > ft.to_datetime64()) & (dt_arr <= pe.to_datetime64())] = 'post_excl'

    return labels


# ── long-format DataFrame 구축 ───────────────────────────────────────────────

def build_long_df(feat_df: pd.DataFrame,
                  failures: pd.DataFrame,
                  baseline: dict, thresholds: dict) -> pd.DataFrame:
    """
    (machineID, datetime, comp, sensor, excess_ratio, label) — 4×N 행
    excess_ratio = effective_z / comp_threshold
    """
    valid   = feat_df.dropna(subset=FEATURE_COLS)
    records = []

    for sensor, (comp, direction) in SENSOR_COMP_MAP.items():
        gm  = baseline[sensor]['mean']
        gs  = baseline[sensor]['std']
        thr = thresholds[comp]
        col = f'{sensor}_mean24h'

        sub = valid[['machineID', 'datetime', col]].copy()
        z   = (sub[col].values - gm) / gs
        ez  = np.abs(z) if direction != 'negative' else np.maximum(-z, 0.0)
        sub['excess_ratio'] = ez / thr
        sub['comp']   = comp
        sub['sensor'] = sensor

        labels     = label_windows(valid, failures, comp)
        sub['label'] = labels

        records.append(
            sub[['machineID', 'datetime', 'comp', 'sensor', 'excess_ratio', 'label']]
        )

    long_df = pd.concat(records, ignore_index=True)
    return long_df


def split_period(long_df: pd.DataFrame, failures: pd.DataFrame):
    train_df  = long_df[long_df['datetime'] < TRAIN_CUTOFF]
    val_df    = long_df[long_df['datetime'] >= TRAIN_CUTOFF]
    train_f   = failures[failures['datetime'] < TRAIN_CUTOFF]
    val_f     = failures[failures['datetime'] >= TRAIN_CUTOFF]
    return train_df, val_df, train_f, val_f


# ══════════════════════════════════════════════════════════════════════════════
# 1. 혼동행렬 (행 단위 + 이벤트 단위 분리)
# ══════════════════════════════════════════════════════════════════════════════

def compute_row_metrics(df: pd.DataFrame, alarm_thr: float = GRADE_ALARM):
    """
    행 단위 TP/FP/FN/TN.
    정상(normal)만 FP/TN 분모에 포함. pre_excl/post_excl 제외.
    """
    fail_m  = df['label'] == 'failure'
    norm_m  = df['label'] == 'normal'
    alarm_m = df['excess_ratio'] >= alarm_thr

    tp = int((alarm_m & fail_m).sum())
    fp = int((alarm_m & norm_m).sum())
    fn = int((~alarm_m & fail_m).sum())
    tn = int((~alarm_m & norm_m).sum())

    prec   = tp / (tp + fp) * 100 if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) * 100 if (tp + fn) > 0 else 0.0
    f1     = 2*prec*recall / (prec + recall) if (prec + recall) > 0 else 0.0
    return tp, fp, fn, tn, prec, recall, f1


def compute_event_metrics(failures_period: pd.DataFrame,
                          long_period: pd.DataFrame,
                          alarm_thr: float = GRADE_ALARM):
    """
    이벤트 단위 TP/FN.
    FP/TN은 이벤트 단위로 정의 불가 → 별도 반환 없음.
    고장 1건당 평균 FP 행 수 = FP_rows / TP_events.
    """
    # (machineID, comp) → datetime 인덱스 서브프레임 캐시
    mid_comp_cache: dict[tuple, pd.DataFrame] = {}
    for (mid, comp), grp in long_period.groupby(['machineID', 'comp']):
        mid_comp_cache[(int(mid), comp)] = (
            grp.set_index('datetime').sort_index()
        )

    tp_events = 0
    fn_events = 0

    for _, fr in failures_period.iterrows():
        mid  = int(fr['machineID'])
        comp = fr['failure']
        ft   = fr['datetime']
        ws   = ft - pd.Timedelta(hours=FAIL_HOURS)

        grp = mid_comp_cache.get((mid, comp), pd.DataFrame())
        if grp.empty:
            fn_events += 1
            continue
        win = grp[ws:ft]['excess_ratio']
        if len(win) > 0 and win.max() >= alarm_thr:
            tp_events += 1
        else:
            fn_events += 1

    n_events = len(failures_period)
    event_recall = tp_events / n_events * 100 if n_events > 0 else 0.0
    return tp_events, fn_events, n_events, event_recall


def section1_confusion_matrix(long_df: pd.DataFrame, failures: pd.DataFrame):
    print(f"\n{'═'*72}")
    print("  [1] 혼동행렬 (부품별 × 구간별)")
    print(f"{'═'*72}")

    train_df, val_df, train_f, val_f = split_period(long_df, failures)

    print("\n  ── (A) 행 단위 — TP/FP/FN/TN = 시간-행 (고장창/정상 구간) ──\n")
    print(f"  {'구간':4s}  {'부품':6s}  {'TP행':>8s}  {'FP행':>10s}  "
          f"{'FN행':>8s}  {'TN행':>10s}  {'행정밀도':>8s}  {'행재현율':>8s}  F1")
    print(f"  {'─'*4}  {'─'*6}  " + "  ".join(["─"*8, "─"*10, "─"*8, "─"*10, "─"*8, "─"*8, "─"*6]))

    for period_name, df_p in [('학습', train_df), ('검증', val_df)]:
        for comp in ['comp1', 'comp2', 'comp3', 'comp4']:
            sub = df_p[df_p['comp'] == comp]
            tp, fp, fn, tn, prec, recall, f1 = compute_row_metrics(sub)
            print(f"  {period_name:4s}  {comp:6s}  {tp:>8,}  {fp:>10,}  "
                  f"{fn:>8,}  {tn:>10,}  {prec:>7.1f}%  {recall:>7.1f}%  {f1:.1f}%")

    print("\n  ── (B) 이벤트 단위 — TP/FN = 고장 건수 (FP/TN 이벤트 미정의) ──\n")
    print(f"  {'구간':4s}  {'부품':6s}  {'TP건':>6s}  {'FN건':>6s}  "
          f"{'전체':>6s}  {'재현율':>8s}  고장1건당FP행")
    print(f"  {'─'*4}  {'─'*6}  " + "  ".join(["─"*6]*4 + ["─"*8, "─"*12]))

    for period_name, df_p, fails_p in [('학습', train_df, train_f), ('검증', val_df, val_f)]:
        # 행 단위 FP (분모 = TP_events)
        row_fp = {c: compute_row_metrics(df_p[df_p['comp'] == c])[1] for c in ['comp1','comp2','comp3','comp4']}
        for comp in ['comp1', 'comp2', 'comp3', 'comp4']:
            tp_e, fn_e, n_e, recall_e = compute_event_metrics(fails_p, df_p)
            # filter to this comp only
            comp_fails_p = fails_p[fails_p['failure'] == comp]
            tp_c, fn_c, n_c, recall_c = compute_event_metrics(comp_fails_p, df_p[df_p['comp'] == comp])
            fp_per_event = row_fp[comp] / tp_c if tp_c > 0 else float('inf')
            print(f"  {period_name:4s}  {comp:6s}  {tp_c:>6d}  {fn_c:>6d}  "
                  f"{n_c:>6d}  {recall_c:>7.1f}%  {fp_per_event:>8.1f}행/건")

    print("""
  ※ FP 이벤트 미정의: 정상 구간에는 '알람이 터지면 안 되는 사건'이 없으므로
    이벤트 FP를 정의할 수 없다. 대신 "고장 1건당 평균 FP 행 수"를 사용.
  ※ 행 단위 F1 이 낮은 이유: 정상 구간 FP 행 수가 매우 많기 때문.
    이것이 임계 이진 판정의 한계이며, 순위(P@K) 방식 전환의 근거.
""")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Precision@K
# ══════════════════════════════════════════════════════════════════════════════

def compute_pak_rak(df: pd.DataFrame, failures_period: pd.DataFrame, k_values: list[int]):
    """
    P@K: 전체 타임스탬프에서 상위 K 중 failure 행 비율 (행 단위).
    R@K: 전체 failure 이벤트 중 24h 창 안에서 상위 K에 한 번이라도 포함된 비율 (이벤트 단위).
    """
    df = df.copy()
    # 타임스탬프 내 순위 (excess_ratio 내림차순)
    df['rank'] = (
        df.groupby('datetime')['excess_ratio']
          .rank(ascending=False, method='first')
    )

    # P@K: mean P(failure | rank ≤ K)
    # failure/normal 만 집계 (exclude 제외)
    fn_mask = df['label'].isin(['failure', 'normal'])

    pak: dict[int, float] = {}
    for K in k_values:
        top_k = df[fn_mask & (df['rank'] <= K)]
        pak[K] = (top_k['label'] == 'failure').mean() * 100

    # R@K: per-failure-event recall
    mid_comp_cache: dict[tuple, pd.DataFrame] = {}
    for (mid, comp), grp in df.groupby(['machineID', 'comp']):
        mid_comp_cache[(int(mid), comp)] = grp.set_index('datetime').sort_index()

    rak: dict[int, int] = {K: 0 for K in k_values}
    n_events = len(failures_period)

    for _, fr in failures_period.iterrows():
        mid  = int(fr['machineID'])
        comp = fr['failure']
        ft   = fr['datetime']
        ws   = ft - pd.Timedelta(hours=FAIL_HOURS)

        grp = mid_comp_cache.get((mid, comp), pd.DataFrame())
        if grp.empty:
            continue
        win = grp[ws:ft]
        if win.empty:
            continue

        min_rank = win['rank'].min()
        for K in k_values:
            if min_rank <= K:
                rak[K] += 1

    rak_pct = {K: v / n_events * 100 if n_events > 0 else 0.0 for K, v in rak.items()}
    return pak, rak_pct, n_events


def section2_precision_at_k(long_df: pd.DataFrame, failures: pd.DataFrame):
    print(f"\n{'═'*72}")
    print("  [2] Precision@K / Recall@K")
    print(f"{'═'*72}")
    print("""
  정렬 기준: excess_ratio (부품별 임계 대비 초과 배수) — 전 100대 × 4부품 통합
  P@K = 상위 K 행 중 실제 고장 24h 창 비율 (행 단위)
  R@K = 전체 고장 이벤트 중 24h 창 안에서 상위 K에 진입한 이벤트 비율 (이벤트 단위)
  타임스탬프 1개 = 1시간, 후보 수 ≈ 400 (100대 × 4부품)
""")
    train_df, val_df, train_f, val_f = split_period(long_df, failures)

    print(f"  {'구간':6s}  {'지표':>6s}  " + "  ".join(f"K={K:>3d}" for K in K_VALUES))
    print(f"  {'─'*6}  {'─'*6}  " + "  ".join(["─"*6] * len(K_VALUES)))

    for period_name, df_p, fails_p in [
        ('학습', train_df, train_f), ('검증', val_df, val_f)
    ]:
        pak, rak, n_e = compute_pak_rak(df_p, fails_p, K_VALUES)
        print(f"  {period_name:6s}  {'P@K':>6s}  " +
              "  ".join(f"{pak[K]:>4.1f}%" for K in K_VALUES))
        print(f"  {'':6s}  {'R@K':>6s}  " +
              "  ".join(f"{rak[K]:>4.1f}%" for K in K_VALUES))
        print(f"  {'':6s}  {'이벤트':>6s}  {n_e:>4d}건")

    print("""
  ── K 값 권고 ──
  가정: 운영자 1인당 하루 처리 가능 알람 수 = 50건 (교대 2회, 교대당 25건)
        전체 운영팀 5인 → 하루 250건 처리 가능
        타임스탬프 = 1시간 → 하루 24 시간

  K=5: 하루 5×24=120건 → 팀 전체로 처리 가능
  K=10: 하루 240건 → 한계 수준
  K=20: 하루 480건 → 과부하
  K=50: 하루 1,200건 → 운용 불가

  권고: K=5 (P@K 수치 확인 후 최종 결정)
  P@K 수치 해석: 현행 모델 정밀도(21%) 대비 높으면 순위 방식이 유효.
""")


# ══════════════════════════════════════════════════════════════════════════════
# 3. 등급 체계 유효성 (0.8 경계값 검증)
# ══════════════════════════════════════════════════════════════════════════════

def section3_grade_validation(long_df: pd.DataFrame, failures: pd.DataFrame):
    print(f"\n{'═'*72}")
    print("  [3] 등급 체계 유효성 — 0.8 경계값 검증")
    print(f"{'═'*72}")

    # 전 기간 합산으로 분석
    df = long_df[long_df['label'].isin(['failure', 'normal'])].copy()

    # excess_ratio 구간별 P(failure|tier)
    buckets = [
        (0.0, 0.2, '정상 영역'),
        (0.2, 0.4, '정상 영역'),
        (0.4, 0.6, '정상 영역'),
        (0.6, 0.8, '정상 영역'),
        (0.8, 1.0, '관찰 영역'),
        (1.0, 1.5, '알람 영역'),
        (1.5, 3.0, '알람 영역'),
        (3.0, np.inf, '알람 영역'),
    ]

    print(f"\n  excess_ratio 구간별 행 분포 (전 기간, failure/normal 만):")
    print(f"  {'구간':18s}  {'등급':6s}  {'fail행':>8s}  {'norm행':>10s}  {'P(fail)':>9s}")
    print(f"  {'─'*18}  {'─'*6}  " + "  ".join(["─"*8, "─"*10, "─"*9]))

    prev_pfail = 0.0
    monotone = True
    for lo, hi, tier in buckets:
        sub  = df[(df['excess_ratio'] >= lo) & (df['excess_ratio'] < hi)]
        nf   = (sub['label'] == 'failure').sum()
        nn   = (sub['label'] == 'normal').sum()
        tot  = nf + nn
        pf   = nf / tot * 100 if tot > 0 else 0.0
        if pf < prev_pfail - 1:  # slight tolerance
            monotone = False
        prev_pfail = pf
        hi_s = f"{hi:.1f}" if hi < np.inf else "∞"
        print(f"  [{lo:.1f}, {hi_s}){'':<6}  {tier:6s}  {nf:>8,}  {nn:>10,}  {pf:>8.2f}%")

    print(f"\n  P(failure|tier) 단조 증가: {'✓' if monotone else '✗ (비단조 구간 있음)'}")

    # 등급별 집계
    print(f"\n  등급별 요약:")
    print(f"  {'등급':6s}  {'범위':18s}  {'fail행':>8s}  {'norm행':>10s}  "
          f"{'P(fail)':>9s}  {'fail전체비중':>12s}")

    total_fail = (df['label'] == 'failure').sum()
    grades = [('정상', 0.0, 0.8), ('관찰', 0.8, 1.0), ('알람', 1.0, np.inf)]
    for name, lo, hi in grades:
        sub = df[(df['excess_ratio'] >= lo) & (df['excess_ratio'] < hi)]
        nf  = int((sub['label'] == 'failure').sum())
        nn  = int((sub['label'] == 'normal').sum())
        pf  = nf / (nf + nn) * 100 if (nf + nn) > 0 else 0.0
        fail_share = nf / total_fail * 100 if total_fail > 0 else 0.0
        hi_s = '∞' if hi == np.inf else f'{hi:.1f}'
        print(f"  {name:6s}  [{lo:.1f}, {hi_s}){'':<5}  "
              f"{nf:>8,}  {nn:>10,}  {pf:>8.2f}%  {fail_share:>11.1f}%")

    print("""
  ── 0.8 경계값 타당성 판단 기준 ──
  1. P(failure) 가 0.6→0.8→1.0 구간에서 단조 증가하면 0.8이 자연스러운 경계
  2. '관찰' 구간에 전체 fail행의 X% 가 포함되는지 확인 (관찰의 실용성)
  3. FN 케이스(장비23, excess_ratio=0.91)가 관찰 등급에 포함되는지 확인
""")

    # FN 케이스 확인
    fn_er = 4.552 / 5.00  # pressure excess_ratio for machine 23
    fn_grade = '알람' if fn_er >= 1.0 else ('관찰' if fn_er >= 0.8 else '정상')
    print(f"  FN 케이스(장비23, comp3/pressure) excess_ratio={fn_er:.3f}: "
          f"등급={fn_grade} ({'관찰에 포함' if fn_grade == '관찰' else '관찰에 미포함'})")


# ══════════════════════════════════════════════════════════════════════════════
# 4. FN 케이스 상위 K 포함 여부
# ══════════════════════════════════════════════════════════════════════════════

def section4_fn_case_rank(long_df: pd.DataFrame, failures: pd.DataFrame):
    print(f"\n{'═'*72}")
    print("  [4] FN 케이스(장비23) 상위 K 포함 여부")
    print(f"{'═'*72}")

    # 실제 고장 이벤트 탐색 (machine 23 근처)
    m23_fails = failures[failures['machineID'] == 23].sort_values('datetime')
    print(f"\n  장비 23 실제 고장 이벤트:")
    if m23_fails.empty:
        print("    없음")
    else:
        for _, fr in m23_fails.iterrows():
            print(f"    {fr['failure']} @ {str(fr['datetime'])[:16]}")

    # FN timestamp 2015-10-01 06:00 에서 excess_ratio 및 순위
    fn_ts   = pd.Timestamp('2015-10-01 06:00:00')
    at_ts   = long_df[long_df['datetime'] == fn_ts].copy()

    if at_ts.empty:
        print(f"\n  {fn_ts}: 데이터 없음")
        return

    at_ts['rank'] = at_ts['excess_ratio'].rank(ascending=False, method='first')
    at_ts_sorted  = at_ts.sort_values('rank')

    # machine 23 rows
    m23 = at_ts[at_ts['machineID'] == 23].sort_values('rank')
    print(f"\n  장비 23 @ {str(fn_ts)[:16]}  — excess_ratio 및 순위 (400개 중):")
    print(f"  {'comp':6s}  {'sensor':10s}  {'excess_ratio':>12s}  {'rank':>6s}  "
          f"{'label':10s}  {'등급':6s}")
    for _, row in m23.iterrows():
        er = row['excess_ratio']
        gr = '알람' if er >= 1.0 else ('관찰' if er >= 0.8 else '정상')
        print(f"  {row['comp']:6s}  {row['sensor']:10s}  {er:>12.4f}  "
              f"{int(row['rank']):>6d}  {row['label']:10s}  {gr:6s}")

    # 상위 K에 포함 여부
    print(f"\n  K 별 포함 여부 (장비23/comp3 가장 높은 순위 기준):")
    m23_comp3 = m23[m23['comp'] == 'comp3']
    if m23_comp3.empty:
        print("    comp3 데이터 없음")
    else:
        rank_m23c3 = int(m23_comp3.iloc[0]['rank'])
        for K in K_VALUES:
            incl = rank_m23c3 <= K
            print(f"    K={K:>2d}: {'포함 ✓' if incl else '미포함 ✗'}  (순위 {rank_m23c3})")

    # 상위 10개 candidates 미리보기
    print(f"\n  타임스탬프 전체 상위 10개 후보:")
    print(f"  {'rank':>5s}  {'machineID':>10s}  {'comp':6s}  "
          f"{'excess_ratio':>12s}  {'label':10s}")
    for _, row in at_ts_sorted.head(10).iterrows():
        print(f"  {int(row['rank']):>5d}  {int(row['machineID']):>10d}  "
              f"{row['comp']:6s}  {row['excess_ratio']:>12.4f}  {row['label']:10s}")


# ══════════════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("데이터 로딩...")
    tel, _, fails, _, _ = load_data(DATA_DIR)
    baseline   = load_baseline(DEFAULT_BASELINE_PATH)
    thresholds = load_thresholds(DEFAULT_THRESHOLD_PATH)
    print("완료.")

    print("피처 로드/구축...")
    try:
        feat_df = pd.read_pickle(CACHE_DIR / 'feat_df.pkl')
        print("  캐시 로드 완료.")
    except Exception:
        from baseline_model import build_features
        feat_df = build_features(tel)
    print("완료.")

    print("Long-format DataFrame 구축 중 (4 × ~875k rows)...")
    long_df = build_long_df(feat_df, fails, baseline, thresholds)
    print(f"  완료: {len(long_df):,}행\n")

    section1_confusion_matrix(long_df, fails)
    section2_precision_at_k(long_df, fails)
    section3_grade_validation(long_df, fails)
    section4_fn_case_rank(long_df, fails)


if __name__ == '__main__':
    main()
