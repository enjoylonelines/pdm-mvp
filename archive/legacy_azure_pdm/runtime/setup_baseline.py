"""
baseline_constants.json 과 thresholds.json 을 한 번 생성하는 스크립트.

실행 결과:
  baseline_constants.json — 학습 구간 롤링 평균 mean/std
  thresholds.json         — 부품별 최적 임계 (학습 구간 기준)
"""
from __future__ import annotations
from pathlib import Path

import json
import numpy as np
import pandas as pd

from evidence_package import load_data
from baseline_model   import build_features, FEATURE_COLS, TRAIN_CUTOFF
from z_baseline import (
    SENSORS, SENSOR_DIRECTION, SENSOR_COMP,
    MISS_TARGET, WINDOW_HOURS,
    compute_training_baseline, save_baseline, save_thresholds,
    load_baseline, baseline_to_dataframe, effective_z, compute_z,
)

DATA_DIR = Path(__file__).parent / 'archive'

# 임계 스캔 범위 (새 z 기준, rolling_mean_std 분모)
THRESHOLD_CANDIDATES = np.round(np.arange(0.5, 8.01, 0.25), 2).tolist()

# 정상 구간 정의: 고장 전후 7일 모두 제외
EXCL_PRE_DAYS  = 7
EXCL_POST_DAYS = 7
FAIL_HOURS     = 24


def label_windows(feat_df: pd.DataFrame, failures: pd.DataFrame, comp: str) -> np.ndarray:
    """
    failure  : 고장 24h 전 창 (ft-24h, ft]
    pre_excl : 고장 7일~24h 전  (ft-7d, ft-24h]
    post_excl: 고장 후 7일      (ft, ft+7d]
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


def select_thresholds(
    feat_df: pd.DataFrame,
    failures: pd.DataFrame,
    baseline: dict[str, dict],
    period_name: str,
    cutoff_start: pd.Timestamp | None = None,
    cutoff_end:   pd.Timestamp | None = None,
) -> dict[str, dict]:
    """
    주어진 기간에서 부품별 임계 스캔 결과를 반환한다.
    period_name: 'train' or 'val'
    """
    # 기간 필터
    mask = pd.Series(True, index=feat_df.index)
    if cutoff_start is not None:
        mask &= feat_df['datetime'] >= cutoff_start
    if cutoff_end is not None:
        mask &= feat_df['datetime'] < cutoff_end

    period_feat  = feat_df[mask].dropna(subset=FEATURE_COLS)
    period_fails = failures.copy()
    if cutoff_start is not None:
        period_fails = period_fails[period_fails['datetime'] >= cutoff_start]
    if cutoff_end is not None:
        period_fails = period_fails[period_fails['datetime'] < cutoff_end]

    mid_cache = {mid: grp for mid, grp in period_feat.groupby('machineID')}

    results: dict[str, dict] = {}

    for sensor in SENSORS:
        comp      = SENSOR_COMP[sensor]
        direction = SENSOR_DIRECTION[sensor]
        col       = f'{sensor}_mean24h'
        gm        = baseline[sensor]['mean']
        gs        = baseline[sensor]['std']

        comp_fails = period_fails[period_fails['failure'] == comp]
        n_events   = len(comp_fails)

        # 고장 이벤트별 최대 effective_z 사전 계산
        max_ez_per_event: list[float] = []
        for _, fr in comp_fails.iterrows():
            mid = int(fr['machineID'])
            ft  = fr['datetime']
            ws  = ft - pd.Timedelta(hours=FAIL_HOURS)
            grp = mid_cache.get(mid, pd.DataFrame())
            if grp.empty:
                max_ez_per_event.append(0.0)
                continue
            win  = grp[(grp['datetime'] > ws) & (grp['datetime'] <= ft)]
            vals = win[col].dropna().values
            if len(vals) == 0:
                max_ez_per_event.append(0.0)
                continue
            z   = (vals - gm) / gs
            ez  = np.array([effective_z(zi, direction) for zi in z])
            max_ez_per_event.append(float(ez.max()))
        max_ez_arr = np.array(max_ez_per_event)

        # 행 레이블 (정밀도 계산용)
        labels  = label_windows(period_feat, period_fails, comp)
        z_all   = (period_feat[col].values - gm) / gs
        ez_all  = np.array([effective_z(zi, direction) for zi in z_all])
        fail_m  = labels == 'failure'
        norm_m  = labels == 'normal'

        # 임계 스캔
        scan: list[tuple] = []
        for T in THRESHOLD_CANDIDATES:
            missed    = int((max_ez_arr < T).sum())
            miss_pct  = missed / n_events * 100 if n_events > 0 else 0.0
            fail_rows = int(((ez_all >= T) & fail_m).sum())
            norm_rows = int(((ez_all >= T) & norm_m).sum())
            prec      = fail_rows / (fail_rows + norm_rows) * 100 if (fail_rows + norm_rows) > 0 else 0.0
            scan.append((T, missed, miss_pct, fail_rows, norm_rows, prec))

        # 임계 선정
        achievable = [(T, m, mp, fr, nr, pr) for T, m, mp, fr, nr, pr in scan if mp <= MISS_TARGET]
        if achievable:
            best  = max(achievable, key=lambda x: x[5])   # 정밀도 최대
            note  = f"이벤트 미탐 {MISS_TARGET:.0f}% 이하 달성 가능."
        else:
            best  = min(scan, key=lambda x: x[2])         # 최소 미탐
            note  = f"이벤트 미탐 {MISS_TARGET:.0f}% 달성 불가. 최소 미탐 임계 선택. 센서만으로 조기 탐지 어려움."

        results[comp] = {
            'sensor':          sensor,
            'direction':       direction,
            'threshold':       float(best[0]),
            f'{period_name}_n_events':  n_events,
            f'{period_name}_miss_n':    int(best[1]),
            f'{period_name}_miss_pct':  round(float(best[2]), 1),
            f'{period_name}_fail_rows': int(best[3]),
            f'{period_name}_norm_rows': int(best[4]),
            f'{period_name}_prec_pct':  round(float(best[5]), 1),
            'note': note,
        }

    return results


def main():
    print("데이터 로딩...")
    tel, _, fails, _, _ = load_data(DATA_DIR)
    print("완료.\n")

    print("피처 구축 중 (전 기간 롤링 평균)...")
    feat_df = build_features(tel)
    print("완료.\n")

    # ── 1. Training baseline 계산 및 저장 ────────────────────────────────────
    print("학습 구간 baseline 계산 중...")
    baseline = compute_training_baseline(feat_df, cutoff=TRAIN_CUTOFF)
    save_baseline(baseline)
    print()
    print("  센서별 baseline (학습 구간, 롤링 평균 기준):")
    print(f"  {'센서':10s}  {'mean':>10s}  {'std(rolling)':>12s}  {'std(raw)비율':>12s}  {'n_samples':>10s}")
    for s in SENSORS:
        raw_std = float(tel[s].std())
        b = baseline[s]
        print(f"  {s:10s}  {b['mean']:>10.4f}  {b['std']:>12.4f}  "
              f"{raw_std/b['std']:>11.3f}×  {b['n_samples']:>10,}")
    print()

    # ── 2. 학습 구간 임계 선정 ───────────────────────────────────────────────
    print("학습 구간 임계 스캔 중...")
    train_results = select_thresholds(
        feat_df, fails, baseline,
        period_name='train',
        cutoff_end=TRAIN_CUTOFF,
    )
    print("완료.\n")

    # ── 3. 검증 구간 적용 (과적합 확인) ────────────────────────────────────
    print("검증 구간 성능 확인 중...")
    val_results = select_thresholds(
        feat_df, fails, baseline,
        period_name='val',
        cutoff_start=TRAIN_CUTOFF,
    )
    print("완료.\n")

    # ── 결과 병합 및 저장 ───────────────────────────────────────────────────
    final: dict[str, dict] = {}
    for comp in ['comp1', 'comp2', 'comp3', 'comp4']:
        tr = train_results[comp]
        vl = val_results[comp]
        final[comp] = {
            'sensor':       tr['sensor'],
            'direction':    tr['direction'],
            'threshold':    tr['threshold'],
            'note':         tr['note'],
            'train_n_events':  tr['train_n_events'],
            'train_miss_n':    tr['train_miss_n'],
            'train_miss_pct':  tr['train_miss_pct'],
            'train_fail_rows': tr['train_fail_rows'],
            'train_norm_rows': tr['train_norm_rows'],
            'train_prec_pct':  tr['train_prec_pct'],
            'val_n_events':    vl['val_n_events'],
            'val_miss_n':      vl['val_miss_n'],
            'val_miss_pct':    vl['val_miss_pct'],
            'val_fail_rows':   vl['val_fail_rows'],
            'val_norm_rows':   vl['val_norm_rows'],
            'val_prec_pct':    vl['val_prec_pct'],
        }

    save_thresholds(final)

    # ── 결과 리포트 ────────────────────────────────────────────────────────
    print("══════════════════════════════════════════════════════════════════════")
    print("  부품별 확정 임계 및 성능 (이벤트 단위)")
    print("══════════════════════════════════════════════════════════════════════")
    print(f"\n  {'부품':6s}  {'센서':10s}  {'임계':>6s}  "
          f"{'학습미탐':>8s}  {'학습정밀':>8s}  "
          f"{'검증미탐':>8s}  {'검증정밀':>8s}  비고")
    print(f"  {'─'*6}  {'─'*10}  {'─'*6}  "
          f"{'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*30}")
    for comp in ['comp1', 'comp2', 'comp3', 'comp4']:
        d = final[comp]
        print(f"  {comp:6s}  {d['sensor']:10s}  {d['threshold']:>6.2f}  "
              f"{d['train_miss_pct']:>7.1f}%  {d['train_prec_pct']:>7.1f}%  "
              f"{d['val_miss_pct']:>7.1f}%  {d['val_prec_pct']:>7.1f}%  "
              f"{d['note'][:40]}")

    print(f"""
  ─── 과적합 판단 기준 ───
  학습/검증 미탐율 차이 < 5%p → 정상
  학습/검증 정밀도 차이 < 5%p → 정상
  그 이상 → 임계 재조정 고려 (이번 실행에서는 조정하지 않음. 결과 보고만)

  ─── 행 정밀도 = 이벤트 판단과 다름 ───
  행 정밀도: (고장창 행 중 임계 초과) / (고장창 + 정상 행 중 임계 초과)
  이벤트 미탐: 고장 이벤트 중 24h 창 안에서 임계를 한 번도 안 넘은 건수
""")


if __name__ == '__main__':
    main()
