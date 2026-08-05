"""
순위 변별력 측정 스크립트.
순위 공식 만들지 않음. 측정과 보고만.

분석 대상: 알람 발생 행 (excess_ratio ≥ 1.0, 학습 구간)
  positive: 고장 24h 창 안의 행
  negative: 정상 구간의 행 (고장 전후 7일 제외)

변수 목록:
  (a) excess_ratio          — 현재 정렬 기준. 기준선
  (b) has_prior_error       — 직전 24h 에러 유무
  (c) max_error_rate        — 선행 에러 중 최고 전환율 (전반 고장)
  (d) sum_error_rate        — 선행 에러 전환율 합계
  (e) max_error_comp_assoc  — 선행 에러 중 해당 부품 특이도 최댓값
  (f) days_since_replacement— 마지막 교체 경과일 (없으면 999)
  (g) last_repl_reactive    — 마지막 교체가 사후(1) / 예방(0) / 없음(-1)
  (h) n_other_alarms        — 같은 장비에서 동시 알람 중인 다른 부품 수
  (i) machine_age           — 장비 연식
  (j) machine_model         — 장비 모델 (범주형 → 더미)
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from evidence_package import load_data, SENSOR_COMP_MAP
from z_baseline import (
    load_baseline, load_thresholds,
    DEFAULT_BASELINE_PATH, DEFAULT_THRESHOLD_PATH, TRAIN_CUTOFF,
)
from evaluate_metrics import build_long_df, label_windows

DATA_DIR  = Path(__file__).parent / 'archive'
CACHE_DIR = Path(__file__).parent / 'cache'

ALARM_THR = 1.0
K         = 5
NO_REPL   = 999.0   # sentinel: 교체 이력 없음


# ── 데이터 준비 ───────────────────────────────────────────────────────────────

def build_alarm_df(long_df: pd.DataFrame, period: str) -> pd.DataFrame:
    if period == 'train':
        df = long_df[long_df['datetime'] < TRAIN_CUTOFF]
    else:
        df = long_df[long_df['datetime'] >= TRAIN_CUTOFF]
    return df[
        (df['excess_ratio'] >= ALARM_THR) & df['label'].isin(['failure', 'normal'])
    ].copy().reset_index(drop=True)


# ── 선행 에러 특이도 (학습 구간 기준) ──────────────────────────────────────────

def compute_error_comp_specificity(
    errors: pd.DataFrame,
    failures: pd.DataFrame,
) -> dict[tuple, float]:
    """
    P(comp_failure in 24h | errorID occurred) — 학습 구간 데이터만 사용.
    반환: {(errorID, comp): float}
    """
    errors   = errors[errors['datetime']   < TRAIN_CUTOFF]
    failures = failures[failures['datetime'] < TRAIN_CUTOFF]
    result   = {}

    for eid in sorted(errors['errorID'].unique()):
        eid_errs = (errors[errors['errorID'] == eid]
                    [['machineID', 'datetime']]
                    .reset_index(drop=True)
                    .rename(columns={'datetime': 'err_dt'}))
        eid_errs['_idx'] = eid_errs.index
        n_total = len(eid_errs)

        for comp in sorted(failures['failure'].unique()):
            comp_fails = (failures[failures['failure'] == comp]
                          [['machineID', 'datetime']]
                          .rename(columns={'datetime': 'fail_dt'}))

            if comp_fails.empty or n_total == 0:
                result[(eid, comp)] = 0.0
                continue

            merged = eid_errs.merge(comp_fails, on='machineID', how='left')
            dt_diff = merged['fail_dt'] - merged['err_dt']
            merged['hit'] = (
                (dt_diff > pd.Timedelta(0)) &
                (dt_diff <= pd.Timedelta(hours=24))
            ).fillna(False)

            converted = int(merged.groupby('_idx')['hit'].any().sum())
            result[(eid, comp)] = converted / n_total

    return result


def compute_general_error_rates(
    errors: pd.DataFrame,
    failures: pd.DataFrame,
) -> dict[str, float]:
    """P(any_failure in 24h | errorID) — 학습 구간."""
    errors   = errors[errors['datetime']   < TRAIN_CUTOFF]
    failures = failures[failures['datetime'] < TRAIN_CUTOFF]
    rates    = {}

    for eid in sorted(errors['errorID'].unique()):
        eid_errs = (errors[errors['errorID'] == eid]
                    [['machineID', 'datetime']]
                    .reset_index(drop=True)
                    .rename(columns={'datetime': 'err_dt'}))
        eid_errs['_idx'] = eid_errs.index
        n_total = len(eid_errs)

        merged = eid_errs.merge(
            failures[['machineID', 'datetime']].rename(columns={'datetime': 'fail_dt'}),
            on='machineID', how='left'
        )
        dt_diff = merged['fail_dt'] - merged['err_dt']
        merged['hit'] = (
            (dt_diff > pd.Timedelta(0)) &
            (dt_diff <= pd.Timedelta(hours=24))
        ).fillna(False)

        converted = int(merged.groupby('_idx')['hit'].any().sum())
        rates[eid] = converted / n_total if n_total > 0 else 0.0

    return rates


# ── 특성 계산 ─────────────────────────────────────────────────────────────────

def add_error_features(
    alarm_df: pd.DataFrame,
    errors: pd.DataFrame,
    general_rates: dict,
    specificity: dict,
) -> pd.DataFrame:
    """선행 에러 관련 특성 추가."""
    error_by_machine = {
        int(mid): grp.sort_values('datetime')
        for mid, grp in errors.groupby('machineID')
    }

    has_err, max_rate, sum_rate, max_assoc = [], [], [], []

    for _, row in alarm_df.iterrows():
        mid  = int(row['machineID'])
        dt   = row['datetime']
        comp = row['comp']
        ws   = dt - pd.Timedelta(hours=24)

        errs_m = error_by_machine.get(mid, pd.DataFrame())
        prior  = (errs_m[(errs_m['datetime'] > ws) & (errs_m['datetime'] <= dt)]
                  if not errs_m.empty else pd.DataFrame())

        if prior.empty:
            has_err.append(0.0)
            max_rate.append(0.0)
            sum_rate.append(0.0)
            max_assoc.append(0.0)
        else:
            eids = prior['errorID'].tolist()
            has_err.append(1.0)
            r = [general_rates.get(e, 0.0) for e in eids]
            a = [specificity.get((e, comp), 0.0) for e in eids]
            max_rate.append(max(r))
            sum_rate.append(sum(r))
            max_assoc.append(max(a))

    alarm_df = alarm_df.copy()
    alarm_df['has_prior_error']      = has_err
    alarm_df['max_error_rate']       = max_rate
    alarm_df['sum_error_rate']       = sum_rate
    alarm_df['max_error_comp_assoc'] = max_assoc
    return alarm_df


def add_maint_features(
    alarm_df: pd.DataFrame,
    maint: pd.DataFrame,
    failures: pd.DataFrame,
) -> pd.DataFrame:
    """교체 이력 특성 추가. 학습 구간 maint/failures 사용."""
    maint_tr = maint[maint['datetime'] < TRAIN_CUTOFF].copy()
    fails_tr = failures[failures['datetime'] < TRAIN_CUTOFF]

    # 교체 유형 계산 (재활용을 위해 precompute)
    type_list = []
    for _, mr in maint_tr.iterrows():
        mid  = mr['machineID']
        comp = mr['comp']
        mdt  = mr['datetime']
        ws   = mdt - pd.Timedelta(hours=24)
        cf   = fails_tr[
            (fails_tr['machineID'] == mid) &
            (fails_tr['failure'] == comp) &
            (fails_tr['datetime'] >= ws) &
            (fails_tr['datetime'] < mdt)
        ]
        type_list.append(1 if not cf.empty else 0)
    maint_tr['reactive'] = type_list

    maint_by_mc = {
        (int(mid), comp): grp.sort_values('datetime')
        for (mid, comp), grp in maint_tr.groupby(['machineID', 'comp'])
    }

    days_list, react_list = [], []
    for _, row in alarm_df.iterrows():
        key  = (int(row['machineID']), row['comp'])
        dt   = row['datetime']
        grp  = maint_by_mc.get(key, pd.DataFrame())
        past = grp[grp['datetime'] <= dt] if not grp.empty else pd.DataFrame()

        if past.empty:
            days_list.append(NO_REPL)
            react_list.append(-1.0)    # -1 = 이력 없음
        else:
            last = past.iloc[-1]
            days_list.append((dt - last['datetime']).total_seconds() / 86400)
            react_list.append(float(last['reactive']))

    alarm_df = alarm_df.copy()
    alarm_df['days_since_replacement']  = days_list
    alarm_df['last_repl_reactive']      = react_list
    return alarm_df


def add_other_alarms_feature(
    alarm_df: pd.DataFrame,
    long_df: pd.DataFrame,
) -> pd.DataFrame:
    """같은 장비에서 동시 알람 중인 다른 부품 수."""
    counts = (
        long_df[long_df['excess_ratio'] >= ALARM_THR]
        .groupby(['machineID', 'datetime'])['comp']
        .count()
        .rename('n_alarm_total')
        .reset_index()
    )
    alarm_df = alarm_df.merge(counts, on=['machineID', 'datetime'], how='left')
    alarm_df['n_other_alarms'] = (alarm_df['n_alarm_total'].fillna(1) - 1)
    return alarm_df


def add_machine_features(
    alarm_df: pd.DataFrame,
    machines: pd.DataFrame,
) -> pd.DataFrame:
    alarm_df = alarm_df.merge(
        machines[['machineID', 'age', 'model']], on='machineID', how='left'
    )
    model_map = {f'model{i}': i for i in range(1, 5)}
    alarm_df['model_num'] = alarm_df['model'].map(model_map).fillna(0).astype(int)
    return alarm_df


# ── AUC 계산 ─────────────────────────────────────────────────────────────────

def auc_for_var(alarm_df: pd.DataFrame, col: str, fill_na=0.0) -> float:
    """positive=failure 기준 AUC. NaN은 fill_na로 대체."""
    sub = alarm_df[['label', col]].copy()
    sub[col] = sub[col].fillna(fill_na)
    y = (sub['label'] == 'failure').astype(int).values
    s = sub[col].values
    if y.sum() == 0 or y.sum() == len(y):
        return np.nan
    return float(roc_auc_score(y, s))


def dist_stats(alarm_df: pd.DataFrame, col: str, is_binary: bool = False):
    """두 집단 분포 요약."""
    pos = alarm_df.loc[alarm_df['label'] == 'failure', col].dropna()
    neg = alarm_df.loc[alarm_df['label'] == 'normal',  col].dropna()
    if is_binary:
        pos_stat = f"{pos.mean()*100:.1f}%"
        neg_stat = f"{neg.mean()*100:.1f}%"
    else:
        pos_stat = f"μ={pos.mean():.2f} med={pos.median():.2f}"
        neg_stat = f"μ={neg.mean():.2f} med={neg.median():.2f}"
    return pos_stat, neg_stat


# ── P@K (LR 기반 재순위) ─────────────────────────────────────────────────────

def compute_pak_with_lr(
    long_df_period: pd.DataFrame,
    alarm_df: pd.DataFrame,
    lr_probs: np.ndarray,
    k: int = 5,
) -> float:
    scored = alarm_df[['machineID', 'comp', 'datetime']].copy()
    scored['lr_score'] = lr_probs

    fn_mask = long_df_period['label'].isin(['failure', 'normal'])
    p5_list = []

    for ts, grp in long_df_period[fn_mask].groupby('datetime'):
        alarm_at    = grp[grp['excess_ratio'] >= ALARM_THR]
        non_alarm   = grp[grp['excess_ratio'] <  ALARM_THR]

        if alarm_at.empty and non_alarm.empty:
            continue

        scored_at = alarm_at.merge(
            scored, on=['machineID', 'comp', 'datetime'], how='left'
        )
        # fallback to excess_ratio if LR score missing
        scored_at['lr_score'] = scored_at['lr_score'].fillna(
            scored_at['excess_ratio']
        )

        all_scores = np.concatenate([
            scored_at['lr_score'].values,
            non_alarm['excess_ratio'].values - 2.0,  # 알람 행을 항상 위에 유지
        ])
        all_labels = np.concatenate([
            (scored_at['label'] == 'failure').values,
            (non_alarm['label'] == 'failure').values,
        ])

        top_idx = np.argsort(-all_scores)[:k]
        p5_list.append(all_labels[top_idx].mean())

    return float(np.mean(p5_list)) * 100 if p5_list else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 섹션별 보고
# ══════════════════════════════════════════════════════════════════════════════

def _hr(ch='═', n=70): return ch * n


def section1_auc_table(alarm_train: pd.DataFrame):
    print(f"\n{_hr()}")
    print("  [1] 변수별 변별력 (AUC, 알람 집단 내부, 학습 구간)")
    print(_hr())
    print(f"  집단 정의: positive={( alarm_train['label']=='failure').sum():,}행 "
          f"/ negative={(alarm_train['label']=='normal').sum():,}행 "
          f"/ 합계={len(alarm_train):,}행\n")

    VARS = [
        ('excess_ratio',          False, 0.0, '(a) 현재 정렬 기준'),
        ('has_prior_error',       True,  0.0, '(b) 선행 에러 유무'),
        ('max_error_rate',        False, 0.0, '(c) 최고 선행 에러 전환율'),
        ('sum_error_rate',        False, 0.0, '(d) 선행 에러 전환율 합계'),
        ('max_error_comp_assoc',  False, 0.0, '(e) 해당 부품 에러 특이도'),
        ('days_since_replacement',False, NO_REPL, '(f) 마지막 교체 경과일'),
        ('last_repl_reactive',    False, -1.0, '(g) 마지막 교체 유형 (reactive=1)'),
        ('n_other_alarms',        False, 0.0, '(h) 동시 알람 중인 다른 부품 수'),
        ('machine_age',           False, 0.0, '(i) 장비 연식'),
        ('model_num',             False, 0.0, '(j) 장비 모델'),
    ]

    results = []
    for col, is_binary, fill, label in VARS:
        if col not in alarm_train.columns:
            continue
        auc = auc_for_var(alarm_train, col, fill_na=fill)
        pos_stat, neg_stat = dist_stats(alarm_train, col, is_binary)
        results.append((auc, col, label, pos_stat, neg_stat))

    results.sort(key=lambda x: -abs(x[0] - 0.5) if not np.isnan(x[0]) else 0)

    print(f"  {'변수':30s}  {'AUC':>6s}  {'positive':>30s}  {'negative':>30s}")
    print(f"  {'─'*30}  {'─'*6}  {'─'*30}  {'─'*30}")
    for auc, col, label, pos_s, neg_s in results:
        auc_s = f"{auc:.3f}" if not np.isnan(auc) else "  N/A"
        flag  = '' if abs(auc - 0.5) > 0.05 else '  ← 무변별'
        print(f"  {label:30s}  {auc_s}{flag}")
        print(f"  {'→ positive':30s}  {'':6s}  {pos_s:>30s}")
        print(f"  {'→ negative':30s}  {'':6s}  {neg_s:>30s}")
        print()

    return results


def section2_non_discriminative(results: list):
    print(f"\n{_hr()}")
    print("  [2] 무변별 변수 (AUC 0.45~0.55)")
    print(_hr())
    weak = [(auc, col, label) for auc, col, label, _, _ in results
            if not np.isnan(auc) and abs(auc - 0.5) <= 0.05]
    if weak:
        for auc, col, label in weak:
            print(f"  {label:30s}  AUC={auc:.3f}")
    else:
        print("  없음")
    print()


def section3_correlation(alarm_train: pd.DataFrame, results: list):
    print(f"\n{_hr()}")
    print("  [3] 변별력 있는 변수 간 상관관계")
    print(_hr())

    discriminative = [col for auc, col, _, _, _ in results
                      if not np.isnan(auc) and abs(auc - 0.5) > 0.05]

    if len(discriminative) < 2:
        print("  변별력 있는 변수가 1개 이하 — 상관 분석 불필요")
        return

    corr_df = alarm_train[discriminative].fillna(0).corr()
    print(f"  {'':30s}" + "".join(f"  {c[:8]:>10s}" for c in discriminative))
    for r in discriminative:
        row_str = "".join(f"  {corr_df.loc[r, c]:>10.3f}" for c in discriminative)
        print(f"  {r[:30]:30s}{row_str}")
    print()
    print("  |corr| > 0.7 인 쌍 (중복 변수 후보):")
    for i, r in enumerate(discriminative):
        for c in discriminative[i+1:]:
            cv = abs(corr_df.loc[r, c])
            if cv > 0.7:
                print(f"    {r} × {c}: {cv:.3f}")
    print()


def section4_lr_upper_bound(
    alarm_train: pd.DataFrame,
    long_train: pd.DataFrame,
    long_val: pd.DataFrame,
    errors_full: pd.DataFrame,
    general_rates: dict,
    specificity: dict,
    maint: pd.DataFrame,
    failures: pd.DataFrame,
    machines: pd.DataFrame,
    baseline_pak5: float,
):
    print(f"\n{_hr()}")
    print("  [4] 로지스틱 회귀 조합 — P@5 상한 추정")
    print(_hr())

    FEAT_COLS = [
        'excess_ratio', 'has_prior_error', 'max_error_rate',
        'max_error_comp_assoc', 'days_since_replacement',
        'n_other_alarms', 'machine_age',
    ]
    available = [c for c in FEAT_COLS if c in alarm_train.columns]

    # 변수 impute
    X_train = alarm_train[available].fillna(0).values
    y_train = (alarm_train['label'] == 'failure').astype(int).values

    if y_train.sum() == 0:
        print("  training positive 없음 — LR 학습 불가")
        return

    scaler  = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)

    lr = LogisticRegression(max_iter=1000, random_state=42)
    lr.fit(X_train_s, y_train)
    train_probs = lr.predict_proba(X_train_s)[:, 1]

    train_auc = roc_auc_score(y_train, train_probs)

    # P@5 (in-sample, 낙관적 상한)
    train_p5_lr = compute_pak_with_lr(long_train, alarm_train, train_probs, k=K)

    # 검증 구간 (out-of-sample)
    alarm_val = build_alarm_df(long_val, period='val')
    # 검증 데이터에도 같은 특성 계산
    alarm_val = add_error_features(alarm_val, errors_full, general_rates, specificity)
    alarm_val = add_maint_features(alarm_val, maint, failures)
    alarm_val = add_other_alarms_feature(alarm_val, long_val)
    alarm_val = add_machine_features(alarm_val, machines)

    X_val   = alarm_val[available].fillna(0).values
    y_val   = (alarm_val['label'] == 'failure').astype(int).values
    X_val_s = scaler.transform(X_val)

    val_probs = lr.predict_proba(X_val_s)[:, 1]
    val_auc   = roc_auc_score(y_val, val_probs) if y_val.sum() > 0 else float('nan')
    val_p5_lr = compute_pak_with_lr(long_val, alarm_val, val_probs, k=K)

    print(f"\n  사용 변수: {available}\n")
    print(f"  {'':25s}  {'학습(in-sample)':>16s}  {'검증(out-of-sample)':>20s}")
    print(f"  {'─'*25}  {'─'*16}  {'─'*20}")
    print(f"  {'LR AUC':25s}  {train_auc:>16.3f}  {val_auc:>20.3f}")
    print(f"  {'P@5 (현재 excess_ratio)':25s}  {baseline_pak5:>15.1f}%  {'(동일)':>20s}")
    print(f"  {'P@5 (LR 재순위)':25s}  {train_p5_lr:>15.1f}%  {val_p5_lr:>19.1f}%")
    print(f"  {'P@5 개선폭':25s}  {train_p5_lr - baseline_pak5:>+14.1f}%p  "
          f"{val_p5_lr - baseline_pak5:>+19.1f}%p")

    print(f"""
  ── 판단 근거 ──
  현재 P@5 = {baseline_pak5:.1f}%
  LR P@5 (in-sample) = {train_p5_lr:.1f}%  (과적합 포함, 낙관적 상한)
  LR P@5 (out-of-sample) = {val_p5_lr:.1f}%  (현실적 상한)

  출력 판단 기준: 순위 보강이 가치 있으려면 out-of-sample 개선폭 > 5%p 이어야 함.
  현재 개선폭: {val_p5_lr - baseline_pak5:+.1f}%p
""")

    # 계수 출력
    coef_df = pd.DataFrame({
        'variable': available,
        'coef': lr.coef_[0],
        'abs_coef': np.abs(lr.coef_[0]),
    }).sort_values('abs_coef', ascending=False)
    print("  LR 계수 (절대값 순):")
    for _, r in coef_df.iterrows():
        print(f"    {r['variable']:30s}: {r['coef']:+.3f}")


# ══════════════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("데이터 로딩...")
    tel, errs, fails, maint, mach = load_data(DATA_DIR)
    baseline   = load_baseline(DEFAULT_BASELINE_PATH)
    thresholds = load_thresholds(DEFAULT_THRESHOLD_PATH)

    print("피처 로드...")
    try:
        feat_df = pd.read_pickle(CACHE_DIR / 'feat_df.pkl')
    except Exception:
        from baseline_model import build_features
        feat_df = build_features(tel)

    print("Long-format DataFrame 구축 중...")
    long_df = build_long_df(feat_df, fails, baseline, thresholds)
    long_train = long_df[long_df['datetime'] <  TRAIN_CUTOFF]
    long_val   = long_df[long_df['datetime'] >= TRAIN_CUTOFF]

    print("알람 집단 구성 중...")
    alarm_train = build_alarm_df(long_df, period='train')
    print(f"  학습 알람: {len(alarm_train):,}행  "
          f"(positive={( alarm_train['label']=='failure').sum():,} "
          f"negative={(alarm_train['label']=='normal').sum():,})")

    print("에러 특이도 계산 중 (학습 구간)...")
    specificity   = compute_error_comp_specificity(errs, fails)
    general_rates = compute_general_error_rates(errs, fails)
    print("  완료:", {k: f"{v:.3f}" for k, v in list(specificity.items())[:4]})

    print("특성 추가 중...")
    alarm_train = add_error_features(alarm_train, errs, general_rates, specificity)
    alarm_train = add_maint_features(alarm_train, maint, fails)
    alarm_train = add_other_alarms_feature(alarm_train, long_train)
    alarm_train = add_machine_features(alarm_train, mach)
    print("완료.")

    # 기준선 P@5 (excess_ratio 정렬, evaluate_metrics 에서 확인된 값)
    baseline_pak5 = 24.3

    # 섹션 실행
    results = section1_auc_table(alarm_train)
    section2_non_discriminative(results)
    section3_correlation(alarm_train, results)
    section4_lr_upper_bound(
        alarm_train, long_train, long_val,
        errs, general_rates, specificity,
        maint, fails, mach,
        baseline_pak5,
    )


if __name__ == '__main__':
    main()
