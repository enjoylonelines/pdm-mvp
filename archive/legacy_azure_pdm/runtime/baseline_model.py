"""
베이스라인 분류 모델 — 튜닝 없음, 성능 개선 금지.

피처:  센서 4종의 24h 롤링 mean/std (8개)
라벨:  이후 24h 내 고장 발생 여부
분할:  2015-10-01 이전 학습 / 이후 검증
모델:  HistGradientBoostingClassifier (default 파라미터)

주의: maint/failures 이력은 피처에서 제외 (라벨 누수)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, precision_recall_curve

SENSORS = ['volt', 'rotate', 'pressure', 'vibration']
WINDOW_HOURS = 24
TRAIN_CUTOFF = pd.Timestamp('2015-10-01')

FEATURE_COLS = (
    [f'{s}_mean24h' for s in SENSORS] +
    [f'{s}_std24h'  for s in SENSORS]
)


# ── 피처 엔지니어링 ───────────────────────────────────────────────────────────

def build_features(telemetry: pd.DataFrame) -> pd.DataFrame:
    """
    장비별 독립적으로 24h 롤링 mean/std 계산.
    출력: (machineID, datetime, volt_mean24h, ..., vibration_std24h)
    최소 12행 미만 구간은 NaN — 학습/검증에서 제외됨.
    """
    chunks = []
    for mid, grp in telemetry.sort_values('datetime').groupby('machineID'):
        g = grp.set_index('datetime').sort_index()[SENSORS].copy()
        for s in SENSORS:
            g[f'{s}_mean24h'] = g[s].rolling(WINDOW_HOURS, min_periods=12).mean()
            g[f'{s}_std24h']  = g[s].rolling(WINDOW_HOURS, min_periods=12).std()
        g = g.drop(columns=SENSORS).reset_index()
        g['machineID'] = mid
        chunks.append(g)
    return pd.concat(chunks, ignore_index=True)


def build_labels(
    features_df: pd.DataFrame,
    failures: pd.DataFrame,
    horizon_hours: int = 24,
) -> pd.DataFrame:
    """
    각 행에 라벨 추가: 해당 장비에서 이후 horizon_hours 내 고장 발생 = 1.
    maint 이력은 사용하지 않는다 (failures 기준).
    """
    df = features_df.copy()
    df['label'] = 0

    for _, fr in failures.iterrows():
        mid       = fr['machineID']
        fail_ts   = fr['datetime']
        win_start = fail_ts - pd.Timedelta(hours=horizon_hours)
        mask = (
            (df['machineID'] == mid) &
            (df['datetime'] > win_start) &
            (df['datetime'] <= fail_ts)
        )
        df.loc[mask, 'label'] = 1

    return df


# ── 모델 학습 ─────────────────────────────────────────────────────────────────

def _find_optimal_threshold(probs: np.ndarray, labels: np.ndarray) -> float:
    """PR 곡선에서 F1 최대화 임계값."""
    precision, recall, thresholds = precision_recall_curve(labels, probs)
    # thresholds 길이 = precision/recall 길이 - 1
    f1 = 2 * precision[:-1] * recall[:-1] / (precision[:-1] + recall[:-1] + 1e-8)
    return float(thresholds[np.argmax(f1)])


def train_baseline(
    features_labels: pd.DataFrame,
    train_cutoff: pd.Timestamp = TRAIN_CUTOFF,
) -> tuple:
    """
    베이스라인 모델 학습.

    Returns:
        model          — 학습된 HistGBC
        threshold      — 검증 세트 F1 최적 임계값
        val_df         — 검증 세트 (prob, alarm 컬럼 추가)
        metrics        — PR-AUC, precision, recall, F1, 혼동행렬
        feat_imp       — {feature_col: importance} (없으면 None)
    """
    valid = features_labels.dropna(subset=FEATURE_COLS)
    train = valid[valid['datetime'] <  train_cutoff]
    val   = valid[valid['datetime'] >= train_cutoff].copy()

    X_tr, y_tr = train[FEATURE_COLS].values, train['label'].values
    X_va, y_va = val[FEATURE_COLS].values,   val['label'].values

    model = HistGradientBoostingClassifier(random_state=42)
    model.fit(X_tr, y_tr)

    val_probs = model.predict_proba(X_va)[:, 1]
    threshold  = _find_optimal_threshold(val_probs, y_va)

    val['prob']  = val_probs
    val['alarm'] = val_probs >= threshold

    # 혼동행렬 + 지표
    y_pred = val['alarm'].values.astype(int)
    tp = int(((y_pred == 1) & (y_va == 1)).sum())
    fp = int(((y_pred == 1) & (y_va == 0)).sum())
    fn = int(((y_pred == 0) & (y_va == 1)).sum())
    tn = int(((y_pred == 0) & (y_va == 0)).sum())
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1   = 2 * prec * rec / (prec + rec + 1e-8)

    metrics = {
        'pr_auc':    round(average_precision_score(y_va, val_probs), 4),
        'threshold': round(threshold, 4),
        'precision': round(prec, 4),
        'recall':    round(rec, 4),
        'f1':        round(f1, 4),
        'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
        'val_positives': int(y_va.sum()),
        'val_total':     int(len(y_va)),
    }

    try:
        feat_imp = dict(zip(FEATURE_COLS, model.feature_importances_))
    except AttributeError:
        feat_imp = {f: None for f in FEATURE_COLS}

    return model, threshold, val, metrics, feat_imp


# ── Evidence Package 확장 ─────────────────────────────────────────────────────

def add_model_prediction(
    pkg: dict,
    model,
    features_df: pd.DataFrame,
    threshold: float,
    feat_imp: dict,
) -> dict:
    """
    Evidence Package에 model_prediction 필드를 추가 (in-place + return).

    sensor_evidence(z-score 통계 편차)와 완전히 분리된 필드.
    z-score  = 전 기기 기준 통계 편차 (규칙 기반)
    model_prediction = 피처 → 모델 판정 (학습 기반)
    """
    mid = pkg['machine_id']
    ts  = pd.Timestamp(pkg['timestamp'])

    row = features_df[
        (features_df['machineID'] == mid) &
        (features_df['datetime'] == ts)
    ].dropna(subset=FEATURE_COLS)

    if row.empty:
        pkg['model_prediction'] = {
            'available':          False,
            'reason':             'no_features_at_timestamp',
            'probability':        None,
            'threshold':          round(threshold, 4),
            'alarm_triggered':    None,
            'feature_contribution': None,
            'feature_values':     None,
            'data_cutoff':        str(ts),
        }
        return pkg

    feat_vals = row[FEATURE_COLS].iloc[0].to_dict()
    prob      = float(model.predict_proba(row[FEATURE_COLS])[0][1])

    # 센서별 기여도 = 전역 feature_importance × 피처 절댓값 (근사치, SHAP 아님)
    sensor_raw: dict[str, float] = {}
    for s in SENSORS:
        imp_m = feat_imp.get(f'{s}_mean24h') or 0.0
        imp_s = feat_imp.get(f'{s}_std24h')  or 0.0
        val_m = abs(feat_vals.get(f'{s}_mean24h', 0.0))
        val_s = abs(feat_vals.get(f'{s}_std24h',  0.0))
        sensor_raw[s] = float(imp_m * val_m + imp_s * val_s)

    total = sum(sensor_raw.values())
    if total > 0:
        sensor_pct: dict[str, float | None] = {
            k: round(v / total * 100, 1) for k, v in sensor_raw.items()
        }
    else:
        sensor_pct = {k: None for k in SENSORS}

    pkg['model_prediction'] = {
        'available':       True,
        'probability':     round(prob, 4),
        'threshold':       round(threshold, 4),
        'alarm_triggered': bool(prob >= threshold),
        'feature_contribution': {
            'method':     'global_importance × feature_magnitude (SHAP 아님)',
            'sensor_pct': sensor_pct,
        },
        'feature_values': {k: round(v, 4) for k, v in feat_vals.items()},
        'data_cutoff':    str(ts),
    }
    return pkg


# ── 케이스 탐색 ───────────────────────────────────────────────────────────────

def find_cases_v2(
    val_df: pd.DataFrame,
    fails: pd.DataFrame,
    generate_pkg_fn,   # callable(mid, ts) → pkg (Evidence Package + model_prediction 포함)
    count_supp_fn,     # callable(pkg) → (n_rules, total_strength)
    threshold: float,
    train_cutoff: pd.Timestamp = TRAIN_CUTOFF,
    fp_scan_size: int = 150,
) -> dict:
    """
    개선된 케이스 선정 (v2).

    TP  실제 고장 12~24h 전 · alarm=True · z이상 후보 ≥1
    FP  alarm=True · label=0 · 억제 근거 0개 (없으면 최소 개수)
    FN  TP와 다른 장비·다른 고장 이벤트 · 12~24h 전 · alarm=False
        prob < threshold-0.03 (경계선 아님)
    TN  alarm=False · label=0 (이전과 동일)

    Returns:
        {
            'TP': {'mid': int, 'ts': Timestamp, 'hours_to_failure': float, 'fail_ts': Timestamp},
            'FP': {'mid': int, 'ts': Timestamp, 'n_rules': int, 'total_strength': int,
                   'zero_supp_count': int},
            'FN': {'mid': int, 'ts': Timestamp, 'hours_to_failure': float, 'fail_ts': Timestamp},
            'TN': {'mid': int, 'ts': Timestamp},
        }
    """
    val_fails = fails[fails['datetime'] >= train_cutoff].sort_values('datetime')

    # ── TP ──────────────────────────────────────────────────────────────────
    tp_result = None
    for _, fr in val_fails.iterrows():
        mid     = int(fr['machineID'])
        fail_ts = fr['datetime']

        cands = val_df[
            (val_df['machineID'] == mid) &
            (val_df['alarm']) &
            (val_df['datetime'] >= fail_ts - pd.Timedelta(hours=24)) &
            (val_df['datetime'] <  fail_ts - pd.Timedelta(hours=12))
        ].sort_values('datetime', ascending=False)

        for _, c in cands.iterrows():
            pkg = generate_pkg_fn(mid, c['datetime'])
            if len(pkg['component_hypotheses']) >= 1:
                tp_result = {
                    'mid':             mid,
                    'ts':              c['datetime'],
                    'hours_to_failure': (fail_ts - c['datetime']).total_seconds() / 3600,
                    'fail_ts':         fail_ts,
                }
                break
        if tp_result:
            break

    # ── FN ──────────────────────────────────────────────────────────────────
    fn_result = None
    tp_mid    = tp_result['mid']     if tp_result else None
    tp_fail   = tp_result['fail_ts'] if tp_result else None

    for _, fr in val_fails.iterrows():
        mid     = int(fr['machineID'])
        fail_ts = fr['datetime']

        if mid == tp_mid and fail_ts == tp_fail:
            continue

        cands = val_df[
            (val_df['machineID'] == mid) &
            (~val_df['alarm']) &
            (val_df['prob'] < threshold - 0.03) &
            (val_df['datetime'] >= fail_ts - pd.Timedelta(hours=24)) &
            (val_df['datetime'] <  fail_ts - pd.Timedelta(hours=12))
        ]

        if not cands.empty:
            c = cands.iloc[0]
            fn_result = {
                'mid':             int(mid),
                'ts':              c['datetime'],
                'hours_to_failure': (fail_ts - c['datetime']).total_seconds() / 3600,
                'fail_ts':         fail_ts,
            }
            break

    # ── FP: 억제 근거 최소 개수 ───────────────────────────────────────────
    # S5가 발화하지 않도록 prob > threshold+0.10 필터 후 샘플링
    fp_pool = val_df[
        (val_df['alarm']) &
        (val_df['label'] == 0) &
        (val_df['prob'] >= threshold + 0.10)
    ].sample(min(fp_scan_size, sum(
        (val_df['alarm']) & (val_df['label'] == 0) & (val_df['prob'] >= threshold + 0.10)
    )), random_state=42)

    fp_scored: list[tuple] = []
    for _, row in fp_pool.iterrows():
        mid = int(row['machineID'])
        ts  = row['datetime']
        pkg = generate_pkg_fn(mid, ts)
        n, st = count_supp_fn(pkg)
        fp_scored.append((n, st, mid, ts))

    fp_scored.sort(key=lambda x: (x[0], x[1]))
    zero_supp_count = sum(1 for n, _, _, _ in fp_scored if n == 0)

    best_fp = fp_scored[0]
    fp_result = {
        'mid':            best_fp[2],
        'ts':             best_fp[3],
        'n_rules':        best_fp[0],
        'total_strength': best_fp[1],
        'zero_supp_count': zero_supp_count,
        'scan_size':      len(fp_scored),
    }

    # ── TN ──────────────────────────────────────────────────────────────────
    tn_cands = val_df[(~val_df['alarm']) & (val_df['label'] == 0)]
    tn_row   = tn_cands.iloc[0]
    tn_result = {'mid': int(tn_row['machineID']), 'ts': tn_row['datetime']}

    return {'TP': tp_result, 'FP': fp_result, 'FN': fn_result, 'TN': tn_result}


def find_four_cases(val_df: pd.DataFrame) -> dict[str, tuple]:
    """
    검증 구간에서 TP/FP/FN/TN 각 1개 선택.
    FP: 예측 확률이 가장 높은 것 (모델이 가장 확신했는데 틀린 케이스).
    반환: {'TP': (machineID, datetime), 'FP': ..., 'FN': ..., 'TN': ...}
    """
    def _pick(df: pd.DataFrame, sort_col: str | None = None,
              ascending: bool = True) -> tuple[int, pd.Timestamp] | tuple[None, None]:
        if df.empty:
            return None, None
        if sort_col:
            df = df.sort_values(sort_col, ascending=ascending)
        row = df.iloc[0]
        return int(row['machineID']), row['datetime']

    tp_cands = val_df[(val_df['alarm']) & (val_df['label'] == 1)]
    fp_cands = val_df[(val_df['alarm']) & (val_df['label'] == 0)]
    fn_cands = val_df[(~val_df['alarm']) & (val_df['label'] == 1)]
    tn_cands = val_df[(~val_df['alarm']) & (val_df['label'] == 0)]

    return {
        'TP': _pick(tp_cands),
        'FP': _pick(fp_cands, 'prob', ascending=False),  # 최고 확률 FP
        'FN': _pick(fn_cands),
        'TN': _pick(tn_cands),
    }
