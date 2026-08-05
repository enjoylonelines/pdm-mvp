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

DATASET_VERSION = "canonical-ai4i-physics-v3.1"
MODEL_VERSION   = "independent-logreg-v3.0"
WINDOW_HOURS    = 24

# status_grade → azure-pdm prediction.status 매핑
STATUS_MAP = {
    "critical":  "alarm",
    "warning":   "alarm",
    "attention": "watch",
    "normal":    "normal",
}

# 소수점 보정 helper
def _rf(v: float, n: int = 6) -> float:
    return round(float(v), n)


# ---------------------------------------------------------------------------
# 데이터 로더 (선택적 — 없어도 동작)
# ---------------------------------------------------------------------------

def _load_sensor_window(
    sensor_csv: Path,
    asset_id: str,
    observed_at: str,
    window_hours: int = WINDOW_HOURS,
) -> list[dict]:
    """compressor_sensor_observation.csv에서 해당 장비의 24h 창 레코드 반환."""
    if not sensor_csv.exists():
        return []

    obs_ts = datetime.fromisoformat(observed_at)
    window_start = obs_ts - timedelta(hours=window_hours)
    rows = []
    with open(sensor_csv, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("asset_id") != asset_id:
                continue
            try:
                ts = datetime.fromisoformat(row["observed_at"])
            except (ValueError, KeyError):
                continue
            if window_start < ts <= obs_ts:
                rows.append(row)
    return rows


def _build_sensor_evidence_from_window(rows: list[dict]) -> dict:
    """센서 창 레코드 → sensor_evidence 구조."""
    SENSOR_COLS = ["voltage_raw", "rotation_raw", "pressure_raw", "vibration_raw"]
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
                "reference": "canonical-ai4i-physics-v3.1 compressor_sensor_observation",
            },
        }

    timestamps = sorted(row["observed_at"] for row in rows)
    return {
        "sensors": sensors_out,
        "window_rows": len(rows),
        "reference_frame": "rolling_mean_std_training_only_canonical_v3",
        "window": {"start": timestamps[0], "end": timestamps[-1]},
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

    if not events:
        return {
            "last_maintenance": None,
            "days_elapsed": None,
            "maintenance_type": None,
            "basis": "no_maintenance_record_before_observation",
        }

    # 가장 최근 정비
    last = max(events, key=lambda r: r.get("completed_at", ""))
    last_ts = datetime.fromisoformat(last["completed_at"])
    days_elapsed = round((obs_ts - last_ts).total_seconds() / 86400, 1)

    return {
        "last_maintenance": last["completed_at"],
        "days_elapsed": days_elapsed,
        "maintenance_type": last.get("maintenance_type"),
        "tool_replaced": last.get("tool_replaced") == "1",
        "basis": {
            "maintenance_id": last.get("maintenance_id"),
            "completed_at": last["completed_at"],
        },
    }


# ---------------------------------------------------------------------------
# 핵심 변환 함수
# ---------------------------------------------------------------------------

def result_artifact_to_evidence_package(
    artifact: dict,
    *,
    sensor_csv: Optional[Path] = None,
    maint_csv:  Optional[Path] = None,
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

    # ── sensor_evidence ───────────────────────────────────────────────────────
    if sensor_csv and artifact.get("asset_type") == "compressor":
        sensor_rows = _load_sensor_window(sensor_csv, asset_id, observed_at)
        sensor_evidence = _build_sensor_evidence_from_window(sensor_rows)
    else:
        sensor_evidence = {
            "sensors": {},
            "window_rows": 0,
            "reference_frame": "rolling_mean_std_training_only_canonical_v3",
            "window": {"start": None, "end": None},
            "note": "sensor observation not loaded (asset_type may not be compressor or csv not provided)",
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
            "compressor_sensor_observation.csv",
            "maintenance_event.csv",
            "result_artifact.jsonl",
        ],
        "canonical_source_mutated": prov.get("canonical_source_mutated", False),
    }

    return {
        # ── 식별 ──
        "asset_id": asset_id,
        "asset_type": artifact.get("asset_type"),
        "observed_at": observed_at,
        "prediction_horizon_hours": artifact.get("prediction_horizon_hours", WINDOW_HOURS),
        # ── 센서 근거 ──
        "sensor_evidence": sensor_evidence,
        # ── 모델 판정 ──
        "model_prediction": {
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
    sensor_csv = v3_path / "canonical" / "dataset" / "compressor_sensor_observation.csv"
    maint_csv  = v3_path / "canonical" / "dataset" / "maintenance_event.csv"

    artifacts = load_all_result_artifacts(v3_path)

    if asset_id:
        artifacts = [a for a in artifacts if a.get("asset_id") == asset_id]
    if status_filter:
        artifacts = [a for a in artifacts if a.get("status_grade") in status_filter]

    packages = []
    for artifact in artifacts:
        ep = result_artifact_to_evidence_package(
            artifact,
            sensor_csv=sensor_csv if sensor_csv.exists() else None,
            maint_csv=maint_csv  if maint_csv.exists()  else None,
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
