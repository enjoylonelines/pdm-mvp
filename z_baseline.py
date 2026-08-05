"""
z-score 공통 기준값 모듈.

evidence_package.py 와 measure_distributions.py 양쪽이 이 모듈을 사용한다.

정본 정의:
  baseline: 전역 100대, 학습 구간(2015-10-01 이전), 24h 롤링 평균의 mean/std
  부호:     rotate만 하락(negative), 나머지 양방향(both)
  정상:     고장 전후 7일 모두 제외

JSON 포맷 (baseline_constants.json):
  {
    "computed_on": "YYYY-MM-DD",
    "training_cutoff": "2015-10-01T00:00:00",
    "window_hours": 24,
    "description": "...",
    "sensors": {
      "volt": {"mean": float, "std": float, "n_samples": int},
      ...
    }
  }

JSON 포맷 (thresholds.json):
  {
    "computed_on": "YYYY-MM-DD",
    "selection_rule": "...",
    "miss_target_pct": 5.0,
    "components": {
      "comp1": {"sensor": str, "direction": str, "threshold": float, ...},
      ...
    }
  }
"""

from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd

# ── 상수 ─────────────────────────────────────────────────────────────────────

SENSORS = ['volt', 'rotate', 'pressure', 'vibration']

SENSOR_DIRECTION: dict[str, str] = {
    'volt':      'both',
    'rotate':    'negative',
    'pressure':  'both',
    'vibration': 'both',
}

SENSOR_COMP: dict[str, str] = {
    'volt': 'comp1', 'rotate': 'comp2',
    'pressure': 'comp3', 'vibration': 'comp4',
}

COMP_SENSOR: dict[str, str] = {v: k for k, v in SENSOR_COMP.items()}

TRAIN_CUTOFF  = pd.Timestamp('2015-10-01')
WINDOW_HOURS  = 24
MIN_PERIODS   = 12
MISS_TARGET   = 5.0   # percent

DEFAULT_BASELINE_PATH  = Path(__file__).parent / 'baseline_constants.json'
DEFAULT_THRESHOLD_PATH = Path(__file__).parent / 'thresholds.json'


# ── baseline 계산 (setup_baseline.py 에서 한 번만 호출) ──────────────────────

def compute_training_baseline(
    feat_df: pd.DataFrame,
    cutoff: pd.Timestamp = TRAIN_CUTOFF,
) -> dict[str, dict]:
    """
    학습 구간 24h 롤링 평균의 전역 mean/std.
    feat_df: build_features() 출력 (rolling mean 컬럼 포함).
    """
    train = feat_df[feat_df['datetime'] < cutoff].dropna(
        subset=[f'{s}_mean24h' for s in SENSORS]
    )
    result = {}
    for s in SENSORS:
        col = f'{s}_mean24h'
        arr = train[col].values
        result[s] = {
            'mean':      float(np.mean(arr)),
            'std':       float(np.std(arr, ddof=1)),
            'n_samples': int(len(arr)),
        }
    return result


# ── JSON 저장/로드 ────────────────────────────────────────────────────────────

def save_baseline(baseline: dict, path: Path = DEFAULT_BASELINE_PATH):
    data = {
        'computed_on':     pd.Timestamp.now().strftime('%Y-%m-%d'),
        'training_cutoff': str(TRAIN_CUTOFF),
        'window_hours':    WINDOW_HOURS,
        'min_periods':     MIN_PERIODS,
        'description': (
            '24h 롤링 평균의 mean/std. '
            '학습 구간(2015-10-01 이전) 전체 100대 통합. ddof=1'
        ),
        'sensors': baseline,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"baseline 저장 → {path}")


def load_baseline(path: Path = DEFAULT_BASELINE_PATH) -> dict[str, dict]:
    """Returns {'volt': {'mean': ..., 'std': ...}, ...}"""
    data = json.loads(path.read_text())
    return data['sensors']


def save_thresholds(thresholds: dict, path: Path = DEFAULT_THRESHOLD_PATH):
    data = {
        'computed_on':     pd.Timestamp.now().strftime('%Y-%m-%d'),
        'training_cutoff': str(TRAIN_CUTOFF),
        'selection_rule': (
            '1순위 이벤트 미탐율 ≤ 5% 조건 내 정밀도 최대. '
            '달성 불가 시 최소 미탐 임계 선택.'
        ),
        'miss_target_pct': MISS_TARGET,
        'components': thresholds,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"thresholds 저장 → {path}")


def load_thresholds(path: Path = DEFAULT_THRESHOLD_PATH) -> dict[str, float]:
    """Returns {comp: threshold}"""
    data = json.loads(path.read_text())
    return {comp: info['threshold'] for comp, info in data['components'].items()}


def load_thresholds_full(path: Path = DEFAULT_THRESHOLD_PATH) -> dict[str, dict]:
    """Returns full component threshold data."""
    data = json.loads(path.read_text())
    return data['components']


# ── z-score 계산 ─────────────────────────────────────────────────────────────

def compute_z(
    mean_24h: float,
    sensor: str,
    baseline: dict[str, dict],
) -> float | None:
    """z = (rolling_mean - baseline_mean) / baseline_std."""
    info = baseline.get(sensor, {})
    m    = info.get('mean')
    s    = info.get('std')
    if m is None or s is None or s <= 0:
        return None
    return (mean_24h - m) / s


def effective_z(z: float | None, direction: str) -> float:
    """방향별 유효 z: both → |z|, negative → max(-z, 0)."""
    if z is None:
        return 0.0
    return abs(z) if direction != 'negative' else max(-z, 0.0)


def baseline_to_dataframe(baseline: dict) -> pd.DataFrame:
    """
    evidence_package.py 인터페이스 호환용.
    DataFrame 인덱스: ['mean', 'std'], 컬럼: SENSORS.
    """
    return pd.DataFrame(
        {s: [baseline[s]['mean'], baseline[s]['std']] for s in SENSORS},
        index=['mean', 'std'],
    )
