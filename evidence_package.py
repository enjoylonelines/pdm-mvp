"""
Azure PdM Evidence Package Generator
입력: machine_id, timestamp
출력: dict (LLM 없음, DB 없음, pandas only)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path
from typing import Union

from z_baseline import (
    load_baseline, load_thresholds, baseline_to_dataframe,
    compute_z as _compute_z,
    effective_z as _effective_z,
    DEFAULT_BASELINE_PATH, DEFAULT_THRESHOLD_PATH,
    TRAIN_CUTOFF,
)

# 센서 → 부품 대응 + 방향성
# direction: 'both' = 양방향 이탈, 'negative' = 하락만
SENSOR_COMP_MAP: dict[str, tuple[str, str]] = {
    'volt':      ('comp1', 'both'),
    'rotate':    ('comp2', 'negative'),
    'pressure':  ('comp3', 'both'),
    'vibration': ('comp4', 'both'),
}

SENSORS = ['volt', 'rotate', 'pressure', 'vibration']
COMPONENTS = ['comp1', 'comp2', 'comp3', 'comp4']
WINDOW_HOURS = 24
MIN_ROWS = 12

# 부품별 임계 (thresholds.json 에서 로드. 없으면 fallback)
try:
    _COMP_THRESHOLDS: dict[str, float] = load_thresholds(DEFAULT_THRESHOLD_PATH)
except Exception:
    _COMP_THRESHOLDS = {'comp1': 3.75, 'comp2': 4.00, 'comp3': 5.00, 'comp4': 4.25}

# 하위 호환: 단일 임계 상수 (deprecated, 리포트 코드 등에서 참조용으로만 유지)
Z_THRESHOLD = _COMP_THRESHOLDS.get('comp3', 5.00)


# ---------------------------------------------------------------------------
# 데이터 로더 (순수 함수 외부에서 한 번만 호출)
# ---------------------------------------------------------------------------

def compute_global_baseline(
    telemetry: pd.DataFrame | None = None,
    path: Path = DEFAULT_BASELINE_PATH,
) -> pd.DataFrame:
    """
    학습 구간 24h 롤링 평균 baseline 로드 (baseline_constants.json).
    telemetry 인수는 하위 호환을 위해 유지하나 더 이상 사용하지 않는다.
    JSON 이 없으면 FileNotFoundError → setup_baseline.py 먼저 실행할 것.
    반환: DataFrame(index=['mean','std'], columns=SENSORS)
    """
    bl = load_baseline(path)
    return baseline_to_dataframe(bl)


def load_data(data_dir: str | Path = '.') -> tuple[
    pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame
]:
    """CSV를 읽어 (telemetry, errors, failures, maint, machines) 튜플 반환."""
    d = Path(data_dir)
    tel   = pd.read_csv(d / 'PdM_telemetry.csv', parse_dates=['datetime'])
    errs  = pd.read_csv(d / 'PdM_errors.csv',    parse_dates=['datetime'])
    fails = pd.read_csv(d / 'PdM_failures.csv',  parse_dates=['datetime'])
    maint = pd.read_csv(d / 'PdM_maint.csv',     parse_dates=['datetime'])
    mach  = pd.read_csv(d / 'PdM_machines.csv')
    return tel, errs, fails, maint, mach


# ---------------------------------------------------------------------------
# 기준선 계산 (전체 데이터, 호출자가 캐시)
# ---------------------------------------------------------------------------



def _compute_error_conversion_rates(
    errors_df: pd.DataFrame,
    failures_df: pd.DataFrame,
) -> dict[str, dict]:
    """
    errorID별 과거 24h 내 고장 전환율 계산.
    2014년 데이터는 failures_df 쪽에 없으므로 별도 필터 불필요.
    반환: {errorID: {'rate': float, 'total': int, 'converted': int}}
    """
    result: dict[str, dict] = {}
    if errors_df.empty or failures_df.empty:
        return result

    fail_idx = failures_df.set_index('machineID')

    for eid in errors_df['errorID'].unique():
        occurrences = errors_df[errors_df['errorID'] == eid]
        total = len(occurrences)
        converted = 0

        for _, row in occurrences.iterrows():
            mid = row['machineID']
            edt = row['datetime']
            if mid not in fail_idx.index:
                continue
            machine_fails = fail_idx.loc[[mid], 'datetime']
            # datetime이 Series인 경우 처리
            if isinstance(machine_fails, pd.Series):
                ftimes = machine_fails
            else:
                ftimes = machine_fails.squeeze()

            mask = (ftimes > edt) & (ftimes <= edt + pd.Timedelta(hours=WINDOW_HOURS))
            if mask.any():
                converted += 1

        result[eid] = {
            'rate': round(converted / total, 4) if total else None,
            'total_occurrences': total,
            'converted_to_failure': converted,
        }
    return result


# ---------------------------------------------------------------------------
# 메인 순수 함수
# ---------------------------------------------------------------------------

def generate_evidence_package(
    machine_id: int,
    timestamp: Union[str, pd.Timestamp],
    telemetry: pd.DataFrame,
    errors: pd.DataFrame,
    failures: pd.DataFrame,
    maint: pd.DataFrame,
    machines: pd.DataFrame,
    *,
    baseline: pd.DataFrame | None = None,
) -> dict:
    """
    이상 이벤트에 대한 Evidence Package 생성.

    Args:
        machine_id: 대상 장비 ID
        timestamp:  이벤트 시각 (24h 창의 끝)
        telemetry, errors, failures, maint, machines: 사전 로드된 DataFrame
        baseline:   compute_global_baseline() 결과 (없으면 내부 계산)

    Returns:
        근거 항목과 상태 플래그를 담은 dict
    """
    ts = pd.Timestamp(timestamp)
    window_start = ts - pd.Timedelta(hours=WINDOW_HOURS)

    if baseline is None:
        baseline = compute_global_baseline(telemetry)

    # ── 창 내 대상 장비 텔레메트리 ──────────────────────────────────────────
    tel_m = telemetry[telemetry['machineID'] == machine_id]
    window_data = tel_m[
        (tel_m['datetime'] > window_start) & (tel_m['datetime'] <= ts)
    ].copy()
    row_count = len(window_data)
    insufficient = row_count < MIN_ROWS

    # ── 1. sensor_evidence ──────────────────────────────────────────────────
    sensor_evidence = _build_sensor_evidence(window_data, baseline, row_count, ts, window_start)

    # ── 2. peer_comparison ──────────────────────────────────────────────────
    peer_comparison = _build_peer_comparison(
        machine_id, ts, window_start, window_data, row_count,
        telemetry, machines, baseline
    )

    # ── 3. error_context ────────────────────────────────────────────────────
    error_context, no_prior_error = _build_error_context(
        machine_id, ts, window_start, errors, failures
    )

    # ── 4. maintenance_context ──────────────────────────────────────────────
    # 2014년 maint 400건: 경과일 계산에는 포함, 학습(type 판정)에는 2015+ 사용
    maintenance_context = _build_maintenance_context(
        machine_id, ts, maint, failures
    )

    # ── 5. component_hypotheses ─────────────────────────────────────────────
    hypotheses = _build_hypotheses(window_data, baseline, row_count)
    multiple_candidates = len(hypotheses) >= 2

    return {
        'machine_id': machine_id,
        'timestamp': str(ts),
        'window_start': str(window_start),
        'sensor_evidence': sensor_evidence,
        'peer_comparison': peer_comparison,
        'error_context': error_context,
        'maintenance_context': maintenance_context,
        'component_hypotheses': hypotheses,
        'status_flags': {
            'no_prior_error': no_prior_error,
            'multiple_candidates': multiple_candidates,
            'insufficient_data': insufficient,
        },
    }


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _z(value: float, mean: float, std: float) -> float | None:
    if std <= 0:
        return None
    return round((value - mean) / std, 4)


def _build_sensor_evidence(
    window_data: pd.DataFrame,
    baseline: pd.DataFrame,
    row_count: int,
    ts: pd.Timestamp,
    window_start: pd.Timestamp,
) -> dict:
    sensors_out: dict = {}
    if row_count > 0:
        means = window_data[SENSORS].mean()
        for s in SENSORS:
            gm = baseline.loc['mean', s]
            gs = baseline.loc['std', s]
            sensors_out[s] = {
                'mean_24h': round(float(means[s]), 4),
                'z_score': _z(float(means[s]), gm, gs),
                'basis': {
                    'global_mean': round(float(gm), 4),
                    'global_std':  round(float(gs), 4),
                    'global_n':    int(len(window_data.index)),  # 해당 창의 행 수
                },
            }
    else:
        for s in SENSORS:
            sensors_out[s] = {'mean_24h': None, 'z_score': None, 'basis': None}

    return {
        'sensors': sensors_out,
        'window_rows': row_count,
        'reference_frame': 'rolling_mean_std_training_only_lt_2015-10-01',
        'window': {'start': str(window_start), 'end': str(ts)},
    }


def _build_peer_comparison(
    machine_id: int,
    ts: pd.Timestamp,
    window_start: pd.Timestamp,
    window_data: pd.DataFrame,
    row_count: int,
    telemetry: pd.DataFrame,
    machines: pd.DataFrame,
    baseline: pd.DataFrame,
) -> dict:
    minfo = machines[machines['machineID'] == machine_id]
    if minfo.empty:
        return {'error': 'machine_not_found', 'peer_count': 0,
                'percentile_by_sensor': {s: None for s in SENSORS}}

    model = minfo.iloc[0]['model']
    age   = int(minfo.iloc[0]['age'])

    peer_ids = machines[
        (machines['model'] == model) &
        (machines['age'] >= age - 3) &
        (machines['age'] <= age + 3) &
        (machines['machineID'] != machine_id)
    ]['machineID'].tolist()

    peer_count = len(peer_ids)

    if peer_count == 0 or row_count == 0:
        return {
            'percentile_by_sensor': {s: None for s in SENSORS},
            'peer_count': peer_count,
            'basis': {
                'model': model,
                'age_range': f'{age - 3}–{age + 3}',
                'reference_frame': 'global_z_same_window',
            },
        }

    peer_tel = telemetry[
        telemetry['machineID'].isin(peer_ids) &
        (telemetry['datetime'] > window_start) &
        (telemetry['datetime'] <= ts)
    ]
    peer_means = peer_tel.groupby('machineID')[SENSORS].mean()
    peers_with_data = len(peer_means)

    target_means = window_data[SENSORS].mean()
    percentiles: dict = {}
    for s in SENSORS:
        gm = baseline.loc['mean', s]
        gs = baseline.loc['std', s]
        if gs <= 0 or peers_with_data == 0:
            percentiles[s] = None
            continue
        tgt_z = (float(target_means[s]) - gm) / gs
        peer_zs = (peer_means[s] - gm) / gs
        pct = float((peer_zs < tgt_z).sum()) / len(peer_zs) * 100
        percentiles[s] = {
            'percentile': round(pct, 1),
            'target_z': round(tgt_z, 4),
            'peers_with_data': peers_with_data,
        }

    return {
        'percentile_by_sensor': percentiles,
        'peer_count': peer_count,
        'basis': {
            'model': model,
            'age_range': f'{age - 3}–{age + 3}',
            'reference_frame': 'global_z_same_window',
        },
    }


def _build_error_context(
    machine_id: int,
    ts: pd.Timestamp,
    window_start: pd.Timestamp,
    errors: pd.DataFrame,
    failures: pd.DataFrame,
) -> tuple[dict, bool]:
    window_errs = errors[
        (errors['machineID'] == machine_id) &
        (errors['datetime'] > window_start) &
        (errors['datetime'] <= ts)
    ].copy()

    no_prior_error = window_errs.empty

    if no_prior_error:
        return {'errors': [], 'count': 0}, True

    # 전환율은 전체 데이터 기준으로 errorID별 계산
    conv_rates = _compute_error_conversion_rates(errors, failures)

    error_list = []
    for _, row in window_errs.sort_values('datetime').iterrows():
        eid = row['errorID']
        conv = conv_rates.get(eid, {'rate': None, 'total_occurrences': 0, 'converted_to_failure': 0})
        error_list.append({
            'errorID': eid,
            'datetime': str(row['datetime']),
            'failure_conversion_rate_24h': conv['rate'],
            'basis': {
                'total_occurrences_all_time': conv['total_occurrences'],
                'converted_to_failure_24h':   conv['converted_to_failure'],
            },
        })

    return {'errors': error_list, 'count': len(error_list)}, False


def _build_maintenance_context(
    machine_id: int,
    ts: pd.Timestamp,
    maint: pd.DataFrame,
    failures: pd.DataFrame,
) -> dict:
    """
    각 부품의 마지막 교체 시각, 경과일, 유형(preventive/reactive) 반환.
    - 경과일 계산: 2014년 maint 포함 전체 사용
    - type 판정: 해당 교체 전후 24h 내 같은 장비 고장 여부
      maint에만 있고 failures에 없으면 preventive
    """
    maint_m = maint[maint['machineID'] == machine_id]
    fail_m  = failures[failures['machineID'] == machine_id]

    out: dict = {}
    for comp in COMPONENTS:
        comp_maint_before = maint_m[
            (maint_m['comp'] == comp) & (maint_m['datetime'] <= ts)
        ].sort_values('datetime')

        if comp_maint_before.empty:
            out[comp] = {
                'last_replacement': None,
                'days_elapsed': None,
                'type': None,
                'basis': 'no_maintenance_record_before_timestamp',
            }
            continue

        last_maint_dt = comp_maint_before.iloc[-1]['datetime']
        days_elapsed = round((ts - last_maint_dt).total_seconds() / 86400, 1)

        # reactive 판정: 해당 교체 전 24h 내 같은 장비의 failure 존재 여부
        # (사후 정비 = 고장 직후 교체)
        # 경계 (last_maint_dt - 24h, last_maint_dt] — 끝 포함.
        # 원본 시각이 시간 단위로 반올림돼 고장과 교체가 같은 시각에 기록된다.
        comp_fail = fail_m[fail_m['failure'] == comp]
        reactive = not comp_fail[
            (comp_fail['datetime'] > last_maint_dt - pd.Timedelta(hours=24)) &
            (comp_fail['datetime'] <= last_maint_dt)
        ].empty

        out[comp] = {
            'last_replacement': str(last_maint_dt),
            'days_elapsed': days_elapsed,
            'type': 'reactive' if reactive else 'preventive',
            'basis': {
                'maint_datetime': str(last_maint_dt),
                'failure_within_24h_before_maint': reactive,
            },
        }
    return out


def _build_hypotheses(
    window_data: pd.DataFrame,
    baseline: pd.DataFrame,
    row_count: int,
) -> list[dict]:
    """
    부품별 임계를 넘는 센서-부품 후보 목록.
    임계는 _COMP_THRESHOLDS (thresholds.json)에서 부품별로 로드.
    rotate는 하락(z <= -threshold)만, 나머지는 양방향.
    단일 확정 금지: 후보 목록 형식.
    """
    if row_count == 0:
        return []

    means = window_data[SENSORS].mean()
    hypotheses: list[dict] = []

    for sensor, (comp, direction) in SENSOR_COMP_MAP.items():
        gm = float(baseline.loc['mean', sensor])
        gs = float(baseline.loc['std', sensor])
        if gs <= 0:
            continue
        z         = (float(means[sensor]) - gm) / gs
        threshold = _COMP_THRESHOLDS.get(comp, 5.00)

        triggered = False
        if direction == 'both':
            triggered = abs(z) >= threshold
        elif direction == 'negative':
            triggered = z <= -threshold

        if triggered:
            hypotheses.append({
                'component': comp,
                'associated_sensor': sensor,
                'sensor_z_score': round(z, 4),
                'z_threshold': threshold if direction == 'both' else f'<= -{threshold}',
                'direction': direction,
                'association': f'sensor_anomaly_associated_with_{comp}_candidate',
            })

    return hypotheses
