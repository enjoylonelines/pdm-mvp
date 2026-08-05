"""
z-score 계산 정의 불일치 조사.
조사·보고만. 규칙·임계·모델 변경 없음.
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pandas as pd

from evidence_package import (
    load_data, compute_global_baseline, generate_evidence_package,
    SENSORS, WINDOW_HOURS,
)
from baseline_model import build_features, TRAIN_CUTOFF, FEATURE_COLS

DATA_DIR = Path(__file__).parent / 'archive'

SENSOR_DIRECTION = {
    'volt': 'both', 'rotate': 'negative',
    'pressure': 'both', 'vibration': 'both',
}
SENSOR_COMP = {
    'volt': 'comp1', 'rotate': 'comp2',
    'pressure': 'comp3', 'vibration': 'comp4',
}

def _eff_z(z: float, direction: str) -> float:
    if direction == 'negative':
        return max(-z, 0.0)
    return abs(z)


def _hr(ch='═', n=70): return ch * n


# ══════════════════════════════════════════════════════════════════════════════
# 1. z 계산 경로 목록 (코드 분석)
# ══════════════════════════════════════════════════════════════════════════════

def section1_inventory():
    print(f"\n{_hr()}")
    print("  [1] z-score 계산 경로 목록")
    print(_hr())

    print("""
  # 파일 / 함수                              기준        적용 대상             부호 처리           baseline 구간
  ─ ─────────────────────────────────────── ─────────── ───────────────────── ─────────────────── ────────────────
  1 evidence_package.py / _build_sensor_    전역 100대  24h 창 내 원값 평균  z = (mean - gμ)/gσ  전 기간 (2015)
    evidence()                                                                  hyp: abs/neg 방향
  2 measure_distributions.py / effective_z  전역 100대  24h 롤링 평균         abs(z) / max(-z,0)  전 기간 (2015)
    ()  [feat_df 입력]
  3 report_generator.py / _all_candidates() EP 필드 읽기 (z 직접 계산 없음)   abs/방향 표시용     N/A
  4 report_generator_v1_suppression.py /    EP 필드 읽기 (z 직접 계산 없음)   abs/방향 표시용     N/A
    _get_suppression_evidence()
  5 baseline_model.py                       없음 — 롤링 mean/std 피처로 학습,   N/A               N/A
                                            z 계산 없음

  ※ 실제 계산 지점: #1(EP), #2(분포 측정) 두 곳.
  ※ #1과 #2의 기준값 차이:
     #1: window_data[SENSORS].mean() — 해당 24h 창 원값 직접 평균
     #2: feat_df[sensor_mean24h]     — build_features()의 rolling(24) 결과
     이 두 값은 완전한 시계열에서 동일. 결측 시 차이 발생 가능.

  ※ baseline (gμ, gσ):
     두 곳 모두 compute_global_baseline(tel) → telemetry[SENSORS].agg(['mean','std'])
     → 876,100행 원값(시간별 raw)의 mean/std. 학습/검증 구간 구분 없음.
     → 롤링 평균의 std ≠ 원값의 std  (핵심 의문점 → 섹션 3에서 검증)
""")


# ══════════════════════════════════════════════════════════════════════════════
# 2. FP 케이스 조합별 z 값 재현
# ══════════════════════════════════════════════════════════════════════════════

def section2_fp_z_comparison(tel, fails, mach, baseline_global, feat_df):
    print(f"\n{_hr()}")
    print("  [2] FP 케이스 조합별 z 값 — 불일치 원인 특정")
    print(_hr())

    # 이전 실행(demo_fp_analysis.py)의 FP = 장비 33 @ 2015-12-24 19:00
    # 현재 실행(demo_v3.py)의 FP   = 장비 4  @ 2015-10-13 06:00
    cases = {
        'demo_fp_analysis FP (장비 33 @ 2015-12-24 19:00)': (33, pd.Timestamp('2015-12-24 19:00')),
        'demo_v3 FP         (장비 4  @ 2015-10-13 06:00)': ( 4, pd.Timestamp('2015-10-13 06:00')),
    }

    # Per-machine baseline (기기별 전 기간 mean/std)
    mach_bl = tel.groupby('machineID')[SENSORS].agg(['mean', 'std'])

    # Training-only baseline
    tel_train = tel[tel['datetime'] < TRAIN_CUTOFF]
    bl_train  = compute_global_baseline(tel_train)

    print("\n  분석 방법:")
    print("   A. 전역 baseline(전 기간 원값 876k행) + 24h 창 원값 평균  [현재 EP 방식]")
    print("   B. 기기별 baseline(기기 전 기간 원값 평균·분산) + 24h 창 원값 평균")
    print("   C. 전역 baseline + 단일 시점 원값(raw)")
    print("   D. 학습 구간 baseline(2015-10-01 이전) + 24h 창 원값 평균")
    print("   E. 전역 baseline + feat_df 롤링 평균 [분포 측정 스크립트 방식]")

    for case_name, (mid, ts) in cases.items():
        print(f"\n  ── {case_name}")
        ws = ts - pd.Timedelta(hours=WINDOW_HOURS)

        # 24h 창 원값 평균 (EP 방식)
        win = tel[(tel['machineID'] == mid) & (tel['datetime'] > ws) & (tel['datetime'] <= ts)]
        n_rows = len(win)
        if win.empty:
            print("    창 내 데이터 없음")
            continue

        # 단일 시점 원값
        raw_row = tel[(tel['machineID'] == mid) & (tel['datetime'] == ts)]
        has_raw = not raw_row.empty

        # feat_df 롤링 평균
        feat_row = feat_df[(feat_df['machineID'] == mid) & (feat_df['datetime'] == ts)]
        has_feat = not feat_row.empty and not feat_row[FEATURE_COLS].isna().any(axis=1).iloc[0]

        print(f"    창 내 행 수: {n_rows}  raw row 존재: {has_raw}  feat row 존재: {has_feat}")
        print(f"\n    {'센서':10s}  {'A(EP)':>8s}  {'B(기기별)':>10s}  {'C(raw)':>8s}  "
              f"{'D(학습BL)':>10s}  {'E(feat)':>8s}  [24h창 원값평균]")
        print(f"    {'─'*10}  {'─'*8}  {'─'*10}  {'─'*8}  {'─'*10}  {'─'*8}  {'─'*14}")

        for s in SENSORS:
            win_mean = float(win[s].mean())

            gm   = float(baseline_global.loc['mean', s])
            gs   = float(baseline_global.loc['std',  s])
            z_A  = (win_mean - gm) / gs

            pm   = float(mach_bl.loc[mid, (s, 'mean')])
            pst  = float(mach_bl.loc[mid, (s, 'std')])
            z_B  = (win_mean - pm) / pst if pst > 0 else float('nan')

            z_C  = ((float(raw_row[s].iloc[0]) - gm) / gs) if has_raw else float('nan')

            tm   = float(bl_train.loc['mean', s])
            tst  = float(bl_train.loc['std',  s])
            z_D  = (win_mean - tm) / tst

            z_E_str = '?'
            if has_feat:
                feat_mean = float(feat_row[f'{s}_mean24h'].iloc[0])
                z_E = (feat_mean - gm) / gs
                z_E_str = f"{z_E:+.3f}"

            print(f"    {s:10s}  {z_A:+8.3f}  {z_B:+10.3f}  {z_C:+8.3f}  "
                  f"{z_D:+10.3f}  {z_E_str:>8s}  [{win_mean:.3f}]")

    # 핵심 결론: 장비 33과 장비 4는 다른 케이스
    print(f"""
  ─── 불일치 원인 판단 ───
  '이전 실행'과 '현재 실행'의 FP는 서로 다른 장비다.
  demo_fp_analysis.py → 장비 33 @ 2015-12-24 19:00  (선정 기준: 확률 최고값)
  demo_fp_v2.py 이후  → 장비 4  @ 2015-10-13 06:00  (선정 기준: 억제 근거 최소)
  → 동일 케이스에서 z 값이 달라진 것이 아니다. 케이스 자체가 바뀌었다.
""")


# ══════════════════════════════════════════════════════════════════════════════
# 3. 정상 구간 z 분포 재확인
# ══════════════════════════════════════════════════════════════════════════════

def section3_normal_zone(tel, fails, baseline_global, feat_df):
    print(f"\n{_hr()}")
    print("  [3] 정상 구간 정의 문제 여부")
    print(_hr())

    sensor = 'pressure'
    comp   = 'comp3'
    gm     = float(baseline_global.loc['mean', sensor])
    gs     = float(baseline_global.loc['std',  sensor])

    comp_fails = fails[fails['failure'] == comp]
    valid      = feat_df.dropna(subset=FEATURE_COLS)

    mid_arr = valid['machineID'].values
    dt_arr  = valid['datetime'].values

    # 레이블 배열
    labels = np.full(len(valid), 'normal', dtype=object)

    for _, fr in comp_fails.iterrows():
        mid = fr['machineID']
        ft  = fr['datetime']
        ws  = ft - pd.Timedelta(hours=24)
        xs  = ft - pd.Timedelta(days=7)
        pe  = ft + pd.Timedelta(days=7)   # 고장 이후 7일 (현재 정의에서 'normal')

        mid_m = mid_arr == mid
        labels[mid_m & (dt_arr > ws.to_datetime64()) & (dt_arr <= ft.to_datetime64())] = 'failure'
        labels[mid_m & (dt_arr > xs.to_datetime64()) & (dt_arr <= ws.to_datetime64())] = 'pre_excl'
        labels[mid_m & (dt_arr > ft.to_datetime64()) & (dt_arr <= pe.to_datetime64())] = 'post_excl'

    valid_c  = valid.copy()
    valid_c['_label'] = labels

    raw_z = (valid_c[f'{sensor}_mean24h'] - gm) / gs

    def _describe(mask, name):
        n   = mask.sum()
        if n == 0:
            print(f"  {name:40s}: {n:>8d}건")
            return
        zv  = raw_z[mask].values
        mn  = zv.mean()
        sd  = zv.std()
        ab2 = (np.abs(zv) >= 2.0).sum()
        ab1 = (np.abs(zv) >= 1.75).sum()
        print(f"  {name:40s}: {n:>8,}건  μ={mn:+.3f}  σ={sd:.3f}  |z|≥2.0:{ab2:>6,}({ab2/n*100:.1f}%)  |z|≥1.75:{ab1:>6,}({ab1/n*100:.1f}%)")

    print()
    print("  pressure/comp3 롤링 평균 z 분포 (전역 baseline 기준):")
    print()
    _describe(valid_c['_label'] == 'failure',   'failure (고장 24h 전)')
    _describe(valid_c['_label'] == 'pre_excl',  'pre_excl (7일~24h 전 제외)')
    _describe(valid_c['_label'] == 'post_excl', 'post_excl (고장 후 7일 — 현재 normal에 포함!)')
    _describe(valid_c['_label'] == 'normal',    'normal (현재 정의: 7일 밖 + 고장 후)')

    # 고장 후 행 제외한 진정한 정상
    clean_normal_mask = (
        (valid_c['_label'] == 'normal') &
        ~(valid_c['_label'] == 'post_excl')
    )
    _describe(clean_normal_mask, '순수 정상 (post_excl 제외 시)')

    # 현재 'normal' 중 post_excl 비중
    n_normal   = (valid_c['_label'] == 'normal').sum()
    n_post     = (valid_c['_label'] == 'post_excl').sum()
    print(f"\n  post_excl이 현재 normal에 포함됨: {n_post:,}행  (정상 총 {n_normal:,}행 기준 {n_post/n_normal*100:.1f}%)")
    print("  → 고장 후 회복 구간이 정상에 섞이는 효과가 있는지 위 분포로 판단할 것.")

    # 롤링 평균 std vs 원값 std
    print(f"\n  baseline 비교:")
    print(f"    원값(raw) 전체 std              : {tel[sensor].std():.4f}")
    print(f"    롤링 24h 평균 전체 std           : {feat_df[f'{sensor}_mean24h'].dropna().std():.4f}")
    print(f"    비율 (rolling/raw)              : {feat_df[f'{sensor}_mean24h'].dropna().std() / tel[sensor].std():.4f}")
    print(f"  → 이론(iid): 1/√24 = {1/24**0.5:.4f}  |  실제 비율이 클수록 자기상관 높음")
    print(f"\n  현재 compute_global_baseline 이 사용하는 std = 원값 std = {gs:.4f}")
    print(f"  롤링 평균 자체의 std              = {feat_df[f'{sensor}_mean24h'].dropna().std():.4f}")
    print(f"  → z 계산 분모로 원값 std를 쓰면 롤링 평균의 실제 변동보다 {gs / feat_df[f'{sensor}_mean24h'].dropna().std():.1f}배 크게 잡힘")
    print(f"     → z가 상대적으로 '작게' 나옴 → 임계 초과 케이스가 줄어듦")


# ══════════════════════════════════════════════════════════════════════════════
# 4. 이벤트 미탐 판정 기준
# ══════════════════════════════════════════════════════════════════════════════

def section4_event_miss(tel, fails, baseline_global, feat_df):
    print(f"\n{_hr()}")
    print("  [4] 이벤트 미탐 판정 기준 확인")
    print(_hr())

    comp   = 'comp3'
    sensor = 'pressure'
    gm     = float(baseline_global.loc['mean', sensor])
    gs     = float(baseline_global.loc['std',  sensor])

    # 기기별 baseline
    mach_bl = tel.groupby('machineID')[[sensor]].agg(['mean', 'std'])

    comp_fails = fails[fails['failure'] == comp]
    mid_cache  = {mid: grp for mid, grp in feat_df.groupby('machineID')}

    rows = []
    for _, fr in comp_fails.iterrows():
        mid = int(fr['machineID'])
        ft  = fr['datetime']
        ws  = ft - pd.Timedelta(hours=24)

        grp = mid_cache.get(mid, pd.DataFrame())
        if grp.empty:
            continue
        win = grp[(grp['datetime'] > ws) & (grp['datetime'] <= ft)]
        if win.empty:
            continue

        col = f'{sensor}_mean24h'
        win_vals = win[col].dropna().values
        if len(win_vals) == 0:
            continue

        win_z_global   = (win_vals - gm) / gs
        win_ez_global  = np.abs(win_z_global)   # 'both' direction

        # 기기별 baseline
        pm  = float(mach_bl.loc[mid, (sensor, 'mean')])
        pst = float(mach_bl.loc[mid, (sensor, 'std')])
        win_z_mach = (win_vals - pm) / pst if pst > 0 else win_z_global * np.nan
        win_ez_mach = np.abs(win_z_mach)

        # z at exactly ft
        val_at_ft = win[win['datetime'] == ft][col].values
        z_at_ft_g = float((val_at_ft[0] - gm) / gs) if len(val_at_ft) > 0 else np.nan
        z_at_ft_m = float((val_at_ft[0] - pm) / pst) if (len(val_at_ft) > 0 and pst > 0) else np.nan

        rows.append({
            'mid': mid, 'ft': ft,
            'max_ez_global':  float(win_ez_global.max()),
            'mean_ez_global': float(win_ez_global.mean()),
            'z_at_ft_global': abs(z_at_ft_g),
            'max_ez_mach':    float(win_ez_mach.max()) if pst > 0 else np.nan,
            'z_at_ft_mach':   abs(z_at_ft_m) if pst > 0 else np.nan,
        })

    df = pd.DataFrame(rows)
    n  = len(df)
    print(f"\n  comp3 고장 {n}건 분석 (feat_df 롤링 평균 기반)\n")

    thresholds = [1.5, 1.75, 2.0, 2.25, 2.5]
    methods = {
        'max_ez_global  (현재 분포측정 방식)': 'max_ez_global',
        'mean_ez_global (창 평균 z)':          'mean_ez_global',
        'z_at_ft_global (고장 시점 z만)':       'z_at_ft_global',
        'max_ez_mach    (기기별 baseline)':    'max_ez_mach',
        'z_at_ft_mach   (기기별+고장시점)':     'z_at_ft_mach',
    }

    print(f"  {'방법':40s}  " + "  ".join(f"T={t}" for t in thresholds))
    print(f"  {'─'*40}  " + "  ".join("─────" for _ in thresholds))
    for label, col in methods.items():
        col_data = df[col].dropna()
        miss_rates = []
        for t in thresholds:
            miss_pct = (col_data < t).mean() * 100
            miss_rates.append(f"{miss_pct:5.1f}%")
        print(f"  {label:40s}  " + "  ".join(miss_rates))

    print(f"""
  ─── 이벤트 미탐 판정 기준 설명 ───
  현재 measure_distributions.py: 창 내 임의 1행이라도 임계 초과이면 '탐지'
    = max_ez_global < T → 미탐
  초기 검증(추정): 24h 창 평균값으로 판정 (= z_at_ft_global, 창 끝 시점 값)
    → 두 방법의 미탐율 차이를 위 표에서 확인할 것.
  기기별 baseline: z_at_ft_mach 방식이 초기 검증 '장비별 기준'에 해당할 가능성 높음.
""")


# ══════════════════════════════════════════════════════════════════════════════
# 5. 결론
# ══════════════════════════════════════════════════════════════════════════════

def section5_conclusion(tel, baseline_global, feat_df):
    print(f"\n{_hr('═')}")
    print("  [5] 원인 규명 결론")
    print(_hr('═'))

    sensor = 'pressure'
    gm = float(baseline_global.loc['mean', sensor])
    gs = float(baseline_global.loc['std',  sensor])
    roll_std = feat_df[f'{sensor}_mean24h'].dropna().std()

    print(f"""
  ─── 불일치 (1): 초기 검증 100% vs 분포 측정 49% 미탐 ───

  두 측정에 사용된 'z'의 분모가 다르다.

  현재 compute_global_baseline():
    분모 = std(원값 876k행) = {gs:.4f}
  롤링 24h 평균의 실제 표준편차:
    분모 후보 = std(롤링평균 전체) = {roll_std:.4f}
  비율: {gs/roll_std:.2f}배 차이

  초기 검증이 '장비별 기준' = 기기별 baseline (기기 자체 mean/std)을 썼다면
    → 분모가 기기별 롤링 평균의 std (기기 내 변동폭)
    → 기기 내 변동이 전역 std보다 작으므로 z가 훨씬 크게 나옴
    → 고장 직전 z가 전역 기준보다 훨씬 높게 측정됨
    → 100% 탐지도 가능

  초기 검증이 '24h 창 std'를 분모로 썼다면
    → 해당 24h 창 내부 표준편차 (노이즈 추정)
    → 매우 작은 분모 → 매우 큰 z → 100% 탐지 가능

  결론: 두 측정의 분모 정의가 다르다. 비교 불가.

  ─── 불일치 (2): FP 케이스 z 값 변화 ───

  'FP 장비 4'에서 압력 z가 바뀐 것이 아니다.
  demo_fp_analysis.py의 FP = 장비 33 (z=+2.12)
  demo_fp_v2.py 이후의 FP = 장비 4  (z=-0.12)
  → 케이스 재선정으로 FP 케이스 자체가 바뀜.
  → 동일 장비·동일 시각에 대해 z 계산이 달라진 것 아님.

  ─── 불일치 (3): 정상 구간 z ≥ 2.0이 많음 ───

  현재 label_component_windows()의 'normal' 정의:
    고장 7일 이전 + 고장 이후 전체
  고장 이후 7일 이내 행도 'normal'에 포함됨.
  고장 후 센서 값이 정상 복귀 전 높은 상태이면 z ≥ 2.0 행이 normal에 섞임.
  → 정상 구간 정밀도를 낮추는 효과 (FP처럼 보이는 행 증가).
  실제 오염 정도는 섹션 3의 post_excl 분포에서 확인할 것.
""")

    print("  ─── 정본 제안 ───")
    print("""
  baseline 정의:
    권장: 학습 구간(2015-10-01 이전)의 전역 mean/std
    근거: 검증 구간 정보 누수 방지. 단, 학습/전 기간 차이가 미미할 수 있음.

  분모:
    현재: std(원값 876k행) — 안정적이나 롤링 평균보다 크게 잡힘
    대안: std(롤링 24h 평균) — 실제 관측 분포에 맞지만 계산 기준이 복잡함
    현재 정의를 유지하되 롤링 평균 기반으로 재정의 시 전 측정 무효화

  정상 구간:
    고장 7일 이전 + 고장 7일 이후도 제외해야 오염 최소화
    현재 정의(이후 미제외)는 정밀도를 낮게 추정할 가능성 있음

  이벤트 미탐 판정:
    현재: 창 내 max_z < T → 미탐
    초기 검증과 비교하려면 동일 기준 적용 필요

  ─── 기존 측정치 유효성 ───

  항목                        현재 정의로 유효한가   비고
  임계별 trade-off 표         조건부 유효             분모=원값std, 정상=이후포함
  z 대역별 정밀도             조건부 유효             같은 조건
  억제 규칙 분포 TP/FP 50건   계산 정의와 무관       EP에서 읽은 z 사용 → 일관됨

  '조건부 유효' = 계산 정의를 변경하면 수치 바뀜. 내부 일관성은 있음.
  초기 검증(장비별 기준)과의 비교는 분모 정의가 달라 직접 비교 불가.
""")


# ══════════════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("데이터 로딩...")
    tel, errs, fails, maint, mach = load_data(DATA_DIR)
    baseline_global = compute_global_baseline(tel)
    print("완료.\n")

    print("피처 구축 중 (롤링 평균 필요)...")
    feat_df = build_features(tel)
    print("완료.")

    section1_inventory()
    section2_fp_z_comparison(tel, fails, mach, baseline_global, feat_df)
    section3_normal_zone(tel, fails, baseline_global, feat_df)
    section4_event_miss(tel, fails, baseline_global, feat_df)
    section5_conclusion(tel, baseline_global, feat_df)


if __name__ == '__main__':
    main()
