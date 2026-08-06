"""
v3 Result Artifact → azure-pdm Evidence Package 어댑터

입력:
  canonical/model_outputs/result_artifact.jsonl
  canonical/dataset/compressor_sensor_observation.csv  (선택)
  canonical/dataset/maintenance_event.csv              (선택)

출력:
  azure-pdm Evidence Package 형태의 dict
  (generate_evidence_package + add_model_prediction 출력과 동일한 최상위 키 구조)

사용법:
  python3 scripts/load_v3_result_artifacts.py --v3-path /path/to/predictive_maintenance_canonical_v3
  python3 scripts/load_v3_result_artifacts.py --v3-path ... --asset-id CMP-S03-L03-01 --out samples/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional
import csv
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import failure_type_rules  # noqa: E402

DATASET_VERSION = "canonical-ai4i-physics-v3.1"
MODEL_VERSION   = "independent-logreg-v3.1"
WINDOW_HOURS    = 24

# 모델의 4단계 status_grade → 화면 3단계 등급 매핑.
#
# 등급별 24시간 내 실제 고장률을 재서 경계를 정했다 (68,208행 · 고장 1,793건).
#
#   critical   38.38%  기저 14.6배
#   warning     2.95%  기저  1.1배
#   attention   1.05%  기저  0.4배
#   normal      1.00%  기저  0.4배
#
# critical 과 warning 을 함께 alarm 으로 묶으면 14.6배와 1.1배가 한 등급이 되어
# 알람 고장률이 8.63% 로 희석되고, 밀려난 attention 이 normal 과 구분되지 않는다.
# 실제로 그런 상태였고 관찰 1.05% 대 정상 1.00% 로 변별력이 사라져 있었다.
#
# 아래 매핑에서는 인접 등급 간 격차가 13.0배 / 2.9배로 벌어진다.
STATUS_MAP = {
    "critical":  "alarm",
    "warning":   "watch",
    "attention": "normal",
    "normal":    "normal",
}

# 소수점 보정 helper
def _rf(v: float, n: int = 6) -> float:
    return round(float(v), n)


# ---------------------------------------------------------------------------
# 데이터 로더 (선택적 — 없어도 동작)
# ---------------------------------------------------------------------------

# 자산 유형별 센서 컬럼. v3.1은 유형마다 관측 스키마가 다르며 공통 필드가 없다.
SENSOR_COLS_BY_TYPE: dict[str, list[str]] = {
    "compressor": ["voltage_raw", "rotation_raw", "pressure_raw", "vibration_raw"],
    "cnc": [
        "air_temperature_k",
        "process_temperature_k",
        "rotational_speed_rpm",
        "torque_nm",
        "tool_wear_min",
    ],
}

SENSOR_CSV_BY_TYPE: dict[str, str] = {
    "compressor": "compressor_sensor_observation.csv",
    "cnc": "cnc_sensor_observation.csv",
}

# CSV 전체 스캔을 자산별로 반복하지 않기 위한 프로세스 내 캐시.
_SENSOR_CACHE: dict[str, dict[str, list[dict]]] = {}


def _index_sensor_csv(sensor_csv: Path) -> dict[str, list[dict]]:
    """센서 CSV를 asset_id 기준으로 한 번만 색인한다."""
    key = str(sensor_csv)
    if key in _SENSOR_CACHE:
        return _SENSOR_CACHE[key]

    index: dict[str, list[dict]] = {}
    if sensor_csv.exists():
        with open(sensor_csv, newline="") as f:
            for row in csv.DictReader(f):
                index.setdefault(row.get("asset_id", ""), []).append(row)
    _SENSOR_CACHE[key] = index
    return index


def _load_sensor_window(
    sensor_csv: Path,
    asset_id: str,
    observed_at: str,
    window_hours: int = WINDOW_HOURS,
) -> list[dict]:
    """센서 CSV에서 해당 자산의 관측 창 레코드를 반환한다.

    창 경계는 `(observed_at - window_hours, observed_at]` — 끝 포함.
    `maintenance_rules`의 관측 창 규칙과 같은 방향이다.
    """
    index = _index_sensor_csv(sensor_csv)
    if asset_id not in index:
        return []

    obs_ts = datetime.fromisoformat(observed_at)
    window_start = obs_ts - timedelta(hours=window_hours)
    rows = []
    for row in index[asset_id]:
        try:
            ts = datetime.fromisoformat(row["observed_at"])
        except (ValueError, KeyError):
            continue
        if window_start < ts <= obs_ts:
            rows.append(row)
    return rows


def _build_sensor_evidence_from_window(
    rows: list[dict],
    asset_type: str = "compressor",
) -> dict:
    """센서 창 레코드 → sensor_evidence 구조. 센서 목록은 자산 유형이 결정한다."""
    SENSOR_COLS = SENSOR_COLS_BY_TYPE.get(asset_type, SENSOR_COLS_BY_TYPE["compressor"])
    sensors_out: dict = {}

    if not rows:
        for s in SENSOR_COLS:
            sensors_out[s] = {"mean_window": None, "z_score": None, "basis": None}
        return {
            "sensors": sensors_out,
            "window_rows": 0,
            "reference_frame": "rolling_mean_std_training_only_canonical_v3",
            "window": {"start": None, "end": None},
        }

    # float 변환
    float_rows = []
    for row in rows:
        fr = {}
        for s in SENSOR_COLS:
            try:
                fr[s] = float(row[s]) if row.get(s) not in (None, "", "nan") else None
            except ValueError:
                fr[s] = None
        float_rows.append(fr)

    for s in SENSOR_COLS:
        vals = [r[s] for r in float_rows if r[s] is not None]
        mean_val = sum(vals) / len(vals) if vals else None
        rel_z = None
        if rows[0].get("relative_vibration_z") is not None and s == "vibration_raw":
            try:
                rel_z = float(rows[-1].get("relative_vibration_z", 0))
            except (ValueError, TypeError):
                rel_z = None
        sensors_out[s] = {
            "mean_window": round(mean_val, 4) if mean_val is not None else None,
            "z_score": None,  # 전역 기준선 없음 — relative_vibration_z 대체 가능
            "basis": {
                "window_rows_for_sensor": len(vals),
                "relative_vibration_z": rel_z if s == "vibration_raw" else None,
                "reference": f"{DATASET_VERSION} {SENSOR_CSV_BY_TYPE.get(asset_type, '?')}",
            },
        }

    timestamps = sorted(row["observed_at"] for row in rows)
    return {
        "sensors": sensors_out,
        "window_rows": len(rows),
        "reference_frame": "rolling_mean_std_training_only_canonical_v3",
        "window": {"start": timestamps[0], "end": timestamps[-1]},
    }


# ---------------------------------------------------------------------------
# 자산 마스터 · 동종 집단
# ---------------------------------------------------------------------------

_ASSET_CACHE: dict[str, dict[str, dict]] = {}


def _load_asset_master(asset_csv: Path) -> dict[str, dict]:
    """asset_master.csv → {asset_id: {asset_type, site_id, cell_id}}."""
    key = str(asset_csv)
    if key in _ASSET_CACHE:
        return _ASSET_CACHE[key]
    out: dict[str, dict] = {}
    if asset_csv.exists():
        with open(asset_csv, newline="") as f:
            for row in csv.DictReader(f):
                out[row["asset_id"]] = row
    _ASSET_CACHE[key] = out
    return out


def _build_peer_comparison(
    asset_id: str,
    observed_at: str,
    sensor_csv: Path,
    asset_master: dict[str, dict],
    asset_type: str,
    min_peers: int = 5,
) -> dict:
    """
    동종 집단 대비 백분위.

    Azure PdM은 `(model, 연식 ±3)`으로 집단을 정의했으나 v3.1에는 모델·연식이 없다.
    대신 자산 마스터의 **같은 cell 안 같은 유형**을 동종으로 본다.
    셀 단위 표본이 부족하면 site 단위로 넓힌다.
    """
    me = asset_master.get(asset_id)
    if not me:
        return {"error": "asset_not_found_in_master", "peer_count": 0,
                "percentile_by_sensor": {}}

    def _members(scope: str) -> list[str]:
        return [
            aid for aid, r in asset_master.items()
            if r.get("asset_type") == asset_type and r.get(scope) == me.get(scope)
        ]

    scope = "cell_id"
    peer_ids = _members(scope)
    if len(peer_ids) < min_peers:
        scope = "site_id"
        peer_ids = _members(scope)

    basis = {
        "grouping": scope,
        "scope_value": me.get(scope),
        "asset_type": asset_type,
        "reference_frame": f"same_{scope}_same_asset_type_window_mean",
    }
    if len(peer_ids) <= 1:
        return {"peer_count": 0, "percentile_by_sensor": {}, "basis": basis,
                "sufficient_peers": False}

    cols = SENSOR_COLS_BY_TYPE.get(asset_type, [])
    peer_means: dict[str, dict[str, float]] = {}
    for pid in peer_ids:
        rows = _load_sensor_window(sensor_csv, pid, observed_at)
        if not rows:
            continue
        m: dict[str, float] = {}
        for c in cols:
            vals = []
            for r in rows:
                try:
                    v = r.get(c)
                    if v not in (None, "", "nan"):
                        vals.append(float(v))
                except ValueError:
                    pass
            if vals:
                m[c] = sum(vals) / len(vals)
        if m:
            peer_means[pid] = m

    pct_by_sensor: dict[str, Optional[dict]] = {}
    for c in cols:
        vals = {p: m[c] for p, m in peer_means.items() if c in m}
        target = vals.get(asset_id)
        if target is None or len(vals) < 2:
            pct_by_sensor[c] = None
            continue
        below = sum(1 for v in vals.values() if v < target)
        pct_by_sensor[c] = {
            "percentile": round(below / (len(vals) - 1) * 100, 1),
            "target_value": round(target, 4),
            "peers_with_data": len(vals),
        }

    return {
        "peer_count": len(peer_ids),
        "percentile_by_sensor": pct_by_sensor,
        "basis": basis,
        # 표본 부족 시 백분위를 판단 근거로 쓰지 않는다는 표시
        "sufficient_peers": len(peer_ids) >= min_peers,
    }


# ---------------------------------------------------------------------------
# 정비 문맥
# ---------------------------------------------------------------------------

# v3.1은 정비 유형을 데이터로 제공한다. 역추정하지 않는다(결정 019 참조).
MAINTENANCE_TYPE_MAP = {
    "failure_recovery": "reactive",
    "planned_tool_change": "preventive",
}


def _load_maintenance_context(
    maint_csv: Path,
    asset_id: str,
    observed_at: str,
) -> dict:
    """maintenance_event.csv에서 해당 장비의 최근 정비 이력 반환."""
    if not maint_csv.exists():
        return {"note": "maintenance_event.csv not found"}

    obs_ts = datetime.fromisoformat(observed_at)
    events = []
    with open(maint_csv, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("asset_id") != asset_id:
                continue
            try:
                completed_ts = datetime.fromisoformat(row.get("completed_at", ""))
            except (ValueError, KeyError):
                continue
            if completed_ts <= obs_ts:
                events.append(row)

    # 정비 단위 키. Azure PdM은 계통(comp1~4)이 단위였으나 v3.1은 자산 자체가 단위다.
    # 리포트 블록이 {단위: 정보} 구조를 순회하므로 형태를 맞춘다.
    unit_key = asset_id

    if not events:
        return {
            unit_key: {
                "display_name": asset_id,
                "last_replacement": None,
                "days_elapsed": None,
                "type": None,
                "basis": "no_maintenance_record_before_observation",
            }
        }

    # 가장 최근 정비
    last = max(events, key=lambda r: r.get("completed_at", ""))
    last_ts = datetime.fromisoformat(last["completed_at"])
    days_elapsed = round((obs_ts - last_ts).total_seconds() / 86400, 1)
    raw_type = last.get("maintenance_type")

    return {
        unit_key: {
            "display_name": asset_id,
            "last_replacement": last["completed_at"],
            "days_elapsed": days_elapsed,
            "type": MAINTENANCE_TYPE_MAP.get(raw_type),
            "tool_replaced": last.get("tool_replaced") == "1",
            "basis": {
                "maintenance_id": last.get("maintenance_id"),
                "maintenance_type_raw": raw_type,
                "completed_at": last["completed_at"],
                "source_event_id": last.get("source_event_id") or None,
                # v3.1은 유형을 데이터로 제공한다. 24시간 창 역추정이 필요 없다.
                "type_source": "canonical_field",
            },
        }
    }


# ---------------------------------------------------------------------------
# 핵심 변환 함수
# ---------------------------------------------------------------------------

_CYCLE_CACHE: dict[str, dict[str, list[tuple[str, str]]]] = {}


def _load_product_type(
    cycle_csv: Path, asset_id: str, observed_at: str
) -> Optional[str]:
    """관측 시각 기준 직전 생산 사이클의 제품군.

    과부하(OSF) 임계가 제품군마다 다르다(L 11000 / M 12000 / H 13000).
    모르면 `None` 을 돌려주고, 규칙 모듈이 중간 등급으로 가정한다.
    가정 여부는 `threshold.product_type_assumed` 에 표시된다.
    """
    key = str(cycle_csv)
    if key not in _CYCLE_CACHE:
        index: dict[str, list[tuple[str, str]]] = {}
        try:
            with open(cycle_csv, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    aid = row.get("cnc_asset_id")
                    if not aid:
                        continue
                    index.setdefault(aid, []).append(
                        (row.get("cycle_started_at", ""), row.get("product_type", ""))
                    )
        except OSError:
            index = {}
        for aid in index:
            index[aid].sort(key=lambda t: t[0])
        _CYCLE_CACHE[key] = index

    rows = _CYCLE_CACHE[key].get(asset_id) or []
    latest = None
    for started_at, product_type in rows:
        if started_at <= observed_at:
            latest = product_type
        else:
            break
    return latest or None


def result_artifact_to_evidence_package(
    artifact: dict,
    *,
    sensor_csv: Optional[Path] = None,
    maint_csv:  Optional[Path] = None,
    asset_master: Optional[dict] = None,
    cycle_csv: Optional[Path] = None,
) -> dict:
    """
    v3 Result Artifact 1건 → azure-pdm Evidence Package 형태.

    sensor_csv / maint_csv가 주어지면 실제 센서 창 + 정비 이력을 붙인다.
    없으면 해당 필드는 null/빈 구조로 채운다.
    """
    asset_id    = artifact["asset_id"]
    observed_at = artifact["observed_at"]
    status_grade = artifact.get("status_grade", "normal")
    pred_status = STATUS_MAP.get(status_grade, "normal")

    asset_type = artifact.get("asset_type", "")

    # ── sensor_evidence ───────────────────────────────────────────────────────
    # 자산 유형이 센서 스키마를 결정한다. 압축기와 CNC는 공통 센서 필드가 없다.
    sensor_rows: list[dict] = []
    if sensor_csv and asset_type in SENSOR_COLS_BY_TYPE:
        sensor_rows = _load_sensor_window(sensor_csv, asset_id, observed_at)
        sensor_evidence = _build_sensor_evidence_from_window(sensor_rows, asset_type)
    else:
        sensor_evidence = {
            "sensors": {},
            "window_rows": 0,
            "reference_frame": "rolling_mean_std_training_only_canonical_v3",
            "window": {"start": None, "end": None},
            "note": f"sensor observation not loaded (asset_type={asset_type or 'unknown'})",
        }

    # ── peer_comparison ───────────────────────────────────────────────────────
    if sensor_csv and asset_master and asset_type in SENSOR_COLS_BY_TYPE:
        peer_comparison = _build_peer_comparison(
            asset_id, observed_at, sensor_csv, asset_master, asset_type
        )
    else:
        peer_comparison = {
            "error": "asset_master or sensor csv not provided",
            "peer_count": 0,
            "percentile_by_sensor": {},
        }

    # ── component_hypotheses (top_factors → 후보 목록) ────────────────────────
    hypotheses = []
    for tf in artifact.get("top_factors", []):
        if tf.get("direction") == "risk_up":
            hypotheses.append({
                "feature": tf["feature"],
                "feature_value": tf.get("feature_value"),
                "signed_contribution": tf.get("signed_contribution"),
                "direction": tf.get("direction"),
                "explanation_method": tf.get("explanation_method"),
                "association": f"feature_anomaly_risk_up_candidate_rank{tf.get('rank','')}",
            })

    # ── failure_type_candidates (측정값 → 규칙 판정) ──────────────────────────
    #
    # 모델은 "위험 있음/없음" 2값만 준다. 어느 유형인지는 모델이 아니라
    # 측정값에 대한 물리 조건으로 판정한다. 학습하지 않는다.
    # 근거: failure_type_rules 모듈 주석.
    if sensor_rows:
        latest_row = max(sensor_rows, key=lambda r: r.get("observed_at", ""))
        product_type = (
            _load_product_type(cycle_csv, asset_id, observed_at) if cycle_csv else None
        )
        failure_type = failure_type_rules.evaluate(
            latest_row, asset_type=asset_type, product_type=product_type
        )
        failure_type["basis"] = {
            "observed_at": latest_row.get("observed_at"),
            "source": "canonical sensor observation (창 내 최신 관측)",
            "method": "rule_based_physical_condition",
            "not_model_output": True,
        }
    else:
        failure_type = {
            "available": False,
            "reason": "센서 관측이 없어 유형을 판정할 수 없습니다",
            "candidates": [], "matched": [], "conclusion": None,
        }

    # ── maintenance_context ───────────────────────────────────────────────────
    maint_ctx = {}
    if maint_csv:
        maint_ctx = _load_maintenance_context(maint_csv, asset_id, observed_at)
    else:
        maint_ctx = {"note": "maintenance_event.csv not provided"}

    # ── recommended_actions ───────────────────────────────────────────────────
    rec_action_raw = artifact.get("recommended_action", {})
    recommended_actions = []
    if rec_action_raw:
        recommended_actions.append({
            "action": rec_action_raw.get("action"),
            "priority": rec_action_raw.get("priority"),
            "basis": "result_artifact.recommended_action",
        })

    # ── status_flags ──────────────────────────────────────────────────────────
    status_flags = {
        "no_prior_error": None,   # 경고 이벤트 개념 없음 — 판정 불가
        "status_grade": status_grade,
        "predicted_failure_type": artifact.get("predicted_failure_type"),
        "multiple_risk_factors": len(hypotheses) >= 2,
        "insufficient_data": sensor_evidence.get("window_rows", 0) == 0,
    }

    # ── lineage ───────────────────────────────────────────────────────────────
    prov = artifact.get("provenance", {})
    lineage = {
        "evidence_id": artifact.get("artifact_id"),
        "artifact_type": artifact.get("artifact_type"),
        "schema_version": artifact.get("schema_version"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": prov.get("dataset_version", DATASET_VERSION),
        "model_version": prov.get("model_version", MODEL_VERSION),
        "prediction_id": prov.get("prediction_id"),
        "data_sources": [
            SENSOR_CSV_BY_TYPE.get(artifact.get("asset_type", ""), "sensor_observation.csv"),
            "maintenance_event.csv",
            "asset_master.csv",
            "result_artifact.jsonl",
        ],
        "canonical_source_mutated": prov.get("canonical_source_mutated", False),
    }

    return {
        # ── 식별 ──
        # machine_id / timestamp 는 Azure PdM 계약과의 호환 별칭이다.
        # 리포트 블록이 이 이름을 참조하므로 함께 제공한다.
        "machine_id": asset_id,
        "timestamp": observed_at,
        "asset_id": asset_id,
        "asset_type": artifact.get("asset_type"),
        "observed_at": observed_at,
        "prediction_horizon_hours": artifact.get("prediction_horizon_hours", WINDOW_HOURS),
        # ── 센서 근거 ──
        "sensor_evidence": sensor_evidence,
        # ── 동종 집단 비교 ──
        "peer_comparison": peer_comparison,
        # ── 경고 이벤트 ──
        # v3.1에는 비치명 경고 이벤트 개념이 없다. 없음을 명시해 블록이
        # 근거 없이 문장을 만들지 않도록 한다(결정 002).
        "error_context": {
            "count": 0,
            "errors": [],
            "available": False,
            "note": "canonical v3.1 has no non-fatal alert event source",
        },
        # ── 모델 판정 ──
        "model_prediction": {
            # 리포트 블록이 이 플래그로 판정 근거 유무를 확인한다
            "available": artifact.get("failure_probability") is not None,
            "probability": _rf(artifact.get("failure_probability", 0.0)),
            "confidence": _rf(artifact.get("confidence", 0.0)),
            "status": pred_status,
            "status_grade": status_grade,
            "predicted_failure_type": artifact.get("predicted_failure_type"),
            "prediction_task": artifact.get("prediction_task"),
            "model_version": prov.get("model_version", MODEL_VERSION),
        },
        # ── 이상 후보 (top_factors 중 risk_up 방향) ──
        "component_hypotheses": hypotheses,
        # ── top_factors 원본 (모든 방향 포함) ──
        "top_factors": artifact.get("top_factors", []),
        # ── 고장 유형 후보 (규칙 판정. 모델 출력 아님) ──
        "failure_type_candidates": failure_type,
        # ── 정비 문맥 ──
        "maintenance_context": maint_ctx,
        # ── 권고 행동 ──
        "recommended_actions": recommended_actions,
        # ── 상태 플래그 ──
        "status_flags": status_flags,
        # ── 계보 ──
        "lineage": lineage,
    }


# ---------------------------------------------------------------------------
# 일괄 로더
# ---------------------------------------------------------------------------

def load_all_result_artifacts(v3_path: Path) -> list[dict]:
    """result_artifact.jsonl 전체 로드."""
    fpath = v3_path / "canonical" / "model_outputs" / "result_artifact.jsonl"
    records = []
    with open(fpath) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_evidence_packages(
    v3_path: Path,
    *,
    asset_id: Optional[str] = None,
    status_filter: Optional[list[str]] = None,
) -> list[dict]:
    """
    v3 전체 Result Artifact → Evidence Package 변환.

    asset_id:      특정 장비만 필터 (None = 전체)
    status_filter: status_grade 목록 필터 (예: ['critical', 'warning'])
    """
    ds = v3_path / "canonical" / "dataset"
    maint_csv = ds / "maintenance_event.csv"
    cycle_csv = ds / "cnc_production_cycle.csv"   # 제품군 → 과부하 임계
    asset_master = _load_asset_master(ds / "asset_master.csv")

    artifacts = load_all_result_artifacts(v3_path)

    if asset_id:
        artifacts = [a for a in artifacts if a.get("asset_id") == asset_id]
    if status_filter:
        artifacts = [a for a in artifacts if a.get("status_grade") in status_filter]

    packages = []
    for artifact in artifacts:
        # 센서 CSV는 자산 유형이 결정한다. 압축기와 CNC는 서로 다른 파일·스키마다.
        csv_name = SENSOR_CSV_BY_TYPE.get(artifact.get("asset_type", ""))
        sensor_csv = ds / csv_name if csv_name else None

        ep = result_artifact_to_evidence_package(
            artifact,
            sensor_csv=sensor_csv if sensor_csv and sensor_csv.exists() else None,
            maint_csv=maint_csv if maint_csv.exists() else None,
            asset_master=asset_master or None,
            cycle_csv=cycle_csv if cycle_csv.exists() else None,
        )
        packages.append(ep)
    return packages


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    p = argparse.ArgumentParser(description="v3 Result Artifact → Evidence Package 변환")
    p.add_argument("--v3-path", required=True, help="predictive_maintenance_canonical_v3 루트 경로")
    p.add_argument("--asset-id", default=None, help="특정 asset_id 필터")
    p.add_argument("--status", nargs="*", help="status_grade 필터 (예: critical warning)")
    p.add_argument("--out", default=None, help="출력 디렉토리 (지정 시 JSON 파일 저장)")
    p.add_argument("--limit", type=int, default=None, help="출력 건수 제한")
    return p.parse_args()


def main():
    args = _parse_args()
    v3_path = Path(args.v3_path)

    if not v3_path.exists():
        print(f"ERROR: v3 경로를 찾을 수 없습니다: {v3_path}", file=sys.stderr)
        sys.exit(1)

    packages = build_evidence_packages(
        v3_path,
        asset_id=args.asset_id,
        status_filter=args.status,
    )

    if args.limit:
        packages = packages[: args.limit]

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        for ep in packages:
            fname = f"ep_{ep['asset_id']}_{ep['observed_at'].replace(':', '').replace('+', '_')}.json"
            fpath = out_dir / fname
            fpath.write_text(json.dumps(ep, indent=2, ensure_ascii=False))
        print(f"저장 완료: {len(packages)}건 → {out_dir}")
    else:
        for ep in packages:
            print(json.dumps(ep, indent=2, ensure_ascii=False))

    return packages


if __name__ == "__main__":
    main()
