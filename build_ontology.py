#!/usr/bin/env python3
"""
build_ontology.py — Azure PdM 온톨로지 정적 객체 생성기

설계: ONTOLOGY_DESIGN.md
스타일: evidence_package.py (순수 함수, 부수효과는 write_all 한 곳)
의존성: pandas, 표준 라이브러리, display_names.py, z_baseline.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from display_names import COMP_DISPLAY, ERROR_DISPLAY
from z_baseline import (
    SENSOR_COMP,
    SENSOR_DIRECTION,
    DEFAULT_BASELINE_PATH,
    DEFAULT_THRESHOLD_PATH,
)

# ── 상수 ──────────────────────────────────────────────────────────────────────

ARCHIVE = Path("archive")
COMPS = ["comp1", "comp2", "comp3", "comp4"]
SENSORS = ["volt", "rotate", "pressure", "vibration"]

DEFAULT_AS_OF = pd.Timestamp("2016-01-01 00:00:00")
TRAINING_SCOPE_START = pd.Timestamp("2015-01-01")

COMP_SENSOR: dict[str, str] = {v: k for k, v in SENSOR_COMP.items()}

ERROR_COMP_ASSOC: dict[str, list[str]] = {
    "error1": ["comp1"],
    "error2": ["comp2"],
    "error3": ["comp2"],
    "error4": ["comp3"],
    "error5": ["comp4"],
}


# ── JSON 직렬화 헬퍼 ───────────────────────────────────────────────────────────

class _NpEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _cv(val: Any) -> Any:
    """numpy/pandas 스칼라 → Python 기본 타입. NaN/NaT → None."""
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.floating):
        return None if np.isnan(val) else float(val)
    if isinstance(val, np.bool_):
        return bool(val)
    if isinstance(val, float) and np.isnan(val):
        return None
    if val is pd.NaT:
        return None
    return val


def _clean(obj: Any) -> Any:
    """딕셔너리·리스트를 재귀적으로 정제."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return _cv(obj)


def _dumps(obj: Any) -> str:
    return json.dumps(_clean(obj), ensure_ascii=False)


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_raw_data() -> tuple:
    """CSV 4개와 JSON 2개 로드. 텔레메트리는 읽지 않는다."""
    errors = pd.read_csv(ARCHIVE / "PdM_errors.csv", parse_dates=["datetime"])
    failures = pd.read_csv(ARCHIVE / "PdM_failures.csv", parse_dates=["datetime"])
    maint = pd.read_csv(ARCHIVE / "PdM_maint.csv", parse_dates=["datetime"])
    machines = pd.read_csv(ARCHIVE / "PdM_machines.csv")
    baseline_raw = json.loads(DEFAULT_BASELINE_PATH.read_text())
    thresholds_raw = json.loads(DEFAULT_THRESHOLD_PATH.read_text())
    return errors, failures, maint, machines, baseline_raw, thresholds_raw


# ── 정비 유형 분류 ─────────────────────────────────────────────────────────────

def classify_maintenance(
    maint: pd.DataFrame,
    failures: pd.DataFrame,
) -> pd.DataFrame:
    """
    reactive: 교체 24시간 이내에 같은 설비·계통 고장 존재
    preventive: 그 외

    경계 조건: (mdt - 24h, mdt] — 시작 배제, 끝 **포함**.
    원본 시각이 시간 단위로 반올림돼 있어 고장과 그에 따른 교체가
    같은 시각에 기록된다. 끝 경계를 배제하면 사후 정비를 전부 놓친다.
    `manager_app.compute_maint_interval_stats`와 동일한 규칙이며
    결정 004의 measured 값(예방 2,543 / 사후 743)을 재현한다.
    """
    fail_lookup: dict[tuple, list] = {}
    for _, row in failures.iterrows():
        key = (int(row["machineID"]), str(row["failure"]))
        fail_lookup.setdefault(key, []).append(row["datetime"])

    types: list[str] = []
    flags: list[bool] = []

    for _, row in maint.iterrows():
        mid = int(row["machineID"])
        comp = str(row["comp"])
        mdt = row["datetime"]
        window_start = mdt - pd.Timedelta(hours=24)

        fail_times = fail_lookup.get((mid, comp), [])
        reactive = any(window_start < ft <= mdt for ft in fail_times)

        types.append("reactive" if reactive else "preventive")
        flags.append(reactive)

    result = maint.copy()
    result["type"] = types
    result["failure_within_24h_before"] = flags
    return result


def add_maint_intervals(maint_classified: pd.DataFrame) -> pd.DataFrame:
    """각 (machineID, comp) 내 이전 교체와의 간격(일) 추가."""
    df = maint_classified.sort_values(["machineID", "comp", "datetime"]).copy()
    df["interval_since_previous_days"] = (
        df.groupby(["machineID", "comp"])["datetime"]
        .transform(lambda x: x.diff().dt.total_seconds() / 86400)
    )
    df["interval_since_previous_days"] = df["interval_since_previous_days"].round(1)
    return df


def compute_comp_median_intervals(maint_with_intervals: pd.DataFrame) -> dict[str, float | None]:
    """계통 코드별 교체 간격 중앙값 (전체 설비 합산)."""
    result: dict[str, float | None] = {}
    for comp in COMPS:
        vals = maint_with_intervals.loc[
            (maint_with_intervals["comp"] == comp) &
            maint_with_intervals["interval_since_previous_days"].notna(),
            "interval_since_previous_days",
        ].values
        result[comp] = float(np.median(vals)) if len(vals) > 0 else None
    return result


# ── 인스턴스 빌더 ─────────────────────────────────────────────────────────────

def build_baseline_profile(baseline_raw: dict) -> list[dict]:
    sensors = baseline_raw["sensors"]
    return [{
        "version": "v1",
        "computed_on": baseline_raw["computed_on"],
        "training_cutoff": baseline_raw["training_cutoff"],
        "window_hours": baseline_raw["window_hours"],
        "min_periods": baseline_raw["min_periods"],
        "sensors": {
            s: {
                "mean": sensors[s]["mean"],
                "std": sensors[s]["std"],
                "n_samples": sensors[s]["n_samples"],
            }
            for s in SENSORS
        },
        "_source": "원본",
    }]


def build_threshold_policy(thresholds_raw: dict) -> list[dict]:
    return [{
        "version": "v1",
        "computed_on": thresholds_raw["computed_on"],
        "training_cutoff": thresholds_raw["training_cutoff"],
        "selection_rule": thresholds_raw["selection_rule"],
        "miss_target_pct": thresholds_raw["miss_target_pct"],
        "components": thresholds_raw["components"],
        "_source": "원본",
    }]


def build_sensor_channels() -> list[dict]:
    return [
        {
            "sensor_code": s,
            "direction": SENSOR_DIRECTION[s],
            "unit": None,
            "associated_component_code": SENSOR_COMP[s],
            "_source": {
                "sensor_code": "원본",
                "direction": "계산",
                "unit": "신규",
                "associated_component_code": "계산",
            },
        }
        for s in SENSORS
    ]


def build_component_classes(
    maint_with_intervals: pd.DataFrame,
    failures: pd.DataFrame,
    comp_median_intervals: dict[str, float | None],
) -> list[dict]:
    fail_by_comp = failures["failure"].value_counts().to_dict()
    maint_2015 = maint_with_intervals[maint_with_intervals["datetime"].dt.year == 2015]

    result = []
    for comp in COMPS:
        annual = int((maint_2015["comp"] == comp).sum())
        result.append({
            "comp_code": comp,
            "display_name": COMP_DISPLAY[comp],
            "name_is_inferred": True,
            "median_interval_days": comp_median_intervals.get(comp),
            "total_failures": int(fail_by_comp.get(comp, 0)),
            "annual_replacements": annual,
            "_source": {
                "comp_code": "원본",
                "display_name": "계산",
                "name_is_inferred": "신규",
                "median_interval_days": "신규",
                "total_failures": "신규",
                "annual_replacements": "신규",
            },
        })
    return result


def build_machine_models(
    machines: pd.DataFrame,
    failures: pd.DataFrame,
) -> list[dict]:
    fail_by_machine = failures.groupby("machineID").size().to_dict()
    result = []
    for model_code in sorted(machines["model"].unique()):
        grp = machines[machines["model"] == model_code]
        mc = int(len(grp))
        total_fails = sum(int(fail_by_machine.get(mid, 0)) for mid in grp["machineID"])
        fpm = round(total_fails / mc, 1) if mc > 0 else None
        result.append({
            "model_code": model_code,
            "machine_count": mc,
            "failure_rate_per_machine": fpm,
            "mean_age": round(float(grp["age"].mean()), 1),
            "_source": {
                "model_code": "원본",
                "machine_count": "신규",
                "failure_rate_per_machine": "신규",
                "mean_age": "신규",
            },
        })
    return result


def build_machines(
    machines: pd.DataFrame,
    maint_classified: pd.DataFrame,
    failures: pd.DataFrame,
    as_of: pd.Timestamp,
) -> list[dict]:
    fail_by_machine = failures.groupby("machineID").size().to_dict()

    maint_type_counts = (
        maint_classified.groupby(["machineID", "type"]).size()
        .unstack(fill_value=0)
        .reindex(columns=["preventive", "reactive"], fill_value=0)
    )
    reactive_series = maint_type_counts.get("reactive", pd.Series(dtype=int))
    fleet_ranks = reactive_series.rank(ascending=False, method="min").astype(int)

    result = []
    for _, row in machines.iterrows():
        mid = int(row["machineID"])
        fc = int(fail_by_machine.get(mid, 0))

        prev_cnt = int(maint_type_counts.loc[mid, "preventive"]) if mid in maint_type_counts.index else 0
        reac_cnt = int(maint_type_counts.loc[mid, "reactive"]) if mid in maint_type_counts.index else 0
        total_maint = prev_cnt + reac_cnt
        reactive_ratio = round(reac_cnt / total_maint, 4) if total_maint > 0 else None

        rank = int(fleet_ranks.loc[mid]) if mid in fleet_ranks.index else None

        mach_fails = failures[failures["machineID"] == mid].sort_values("datetime")
        if len(mach_fails) <= 1:
            mtbf = None
        else:
            diffs = mach_fails["datetime"].diff().dropna().dt.total_seconds() / 86400
            mtbf = round(float(diffs.mean()), 1)

        if len(mach_fails) > 0:
            last_fail = mach_fails.iloc[-1]["datetime"]
            tsf = round((as_of - last_fail).total_seconds() / 86400, 1)
        else:
            tsf = None

        result.append({
            "machine_id": mid,
            "model_code": str(row["model"]),
            "age": int(row["age"]),
            "failure_count": fc,
            "preventive_count": prev_cnt,
            "reactive_count": reac_cnt,
            "reactive_ratio": reactive_ratio,
            "fleet_rank_reactive": rank,
            "mtbf_days": mtbf,
            "time_since_last_failure_days": tsf,
            "criticality": None,
            "downtime_cost_per_hour": None,
            "location": None,
            "assigned_engineer": None,
            "_source": {
                "machine_id": "원본",
                "model_code": "원본",
                "age": "원본",
                "failure_count": "신규",
                "preventive_count": "계산",
                "reactive_count": "계산",
                "reactive_ratio": "신규",
                "fleet_rank_reactive": "계산",
                "mtbf_days": "신규",
                "time_since_last_failure_days": "신규",
                "criticality": "합성",
                "downtime_cost_per_hour": "합성",
                "location": "합성",
                "assigned_engineer": "합성",
            },
        })
    return result


def build_components(
    machines: pd.DataFrame,
    maint_classified: pd.DataFrame,
    failures: pd.DataFrame,
    comp_median_intervals: dict[str, float | None],
    as_of: pd.Timestamp,
) -> list[dict]:
    result = []

    # 미리 인덱싱
    maint_grouped = dict(tuple(maint_classified.groupby(["machineID", "comp"])))
    fail_grouped = dict(tuple(failures.groupby(["machineID", "failure"])))

    for _, mrow in machines.iterrows():
        mid = int(mrow["machineID"])

        for comp in COMPS:
            cid = f"{mid}-{comp}"

            comp_maint = maint_grouped.get((mid, comp), pd.DataFrame())
            if not comp_maint.empty:
                comp_maint = comp_maint.sort_values("datetime")
                before_asof = comp_maint[comp_maint["datetime"] <= as_of]
            else:
                before_asof = pd.DataFrame()

            if not before_asof.empty:
                last_row = before_asof.iloc[-1]
                last_at = last_row["datetime"]
                days_since = round((as_of - last_at).total_seconds() / 86400, 1)
                last_type = str(last_row["type"])
                fail_flag = bool(last_row["failure_within_24h_before"])
            else:
                last_at = None
                days_since = None
                last_type = None
                fail_flag = None

            if not comp_maint.empty:
                prev_cnt = int((comp_maint["type"] == "preventive").sum())
                reac_cnt = int((comp_maint["type"] == "reactive").sum())
            else:
                prev_cnt = 0
                reac_cnt = 0

            comp_fails = fail_grouped.get((mid, comp), pd.DataFrame())
            fc = int(len(comp_fails))

            if fc <= 1:
                comp_mtbf = None
            else:
                comp_fails_sorted = comp_fails.sort_values("datetime")
                diffs = comp_fails_sorted["datetime"].diff().dropna().dt.total_seconds() / 86400
                comp_mtbf = round(float(diffs.mean()), 1)

            med_int = comp_median_intervals.get(comp)
            if days_since is not None and med_int is not None and med_int > 0:
                life_ratio = round(days_since / med_int, 4)
            else:
                life_ratio = None

            result.append({
                "component_id": cid,
                "machine_id": mid,
                "comp_code": comp,
                "display_name": COMP_DISPLAY[comp],
                "last_replacement_at": str(last_at) if last_at is not None else None,
                "days_since_replacement": days_since,
                "last_replacement_type": last_type,
                "failure_within_24h_before_maint": fail_flag,
                "replacement_count_preventive": prev_cnt,
                "replacement_count_reactive": reac_cnt,
                "median_interval_days": med_int,
                "life_ratio": life_ratio,
                "failure_count": fc,
                "mtbf_days": comp_mtbf,
                "_source": {
                    "component_id": "신규",
                    "comp_code": "원본",
                    "display_name": "계산",
                    "last_replacement_at": "계산",
                    "days_since_replacement": "계산",
                    "last_replacement_type": "계산",
                    "failure_within_24h_before_maint": "계산",
                    "replacement_count_preventive": "계산",
                    "replacement_count_reactive": "계산",
                    "median_interval_days": "신규",
                    "life_ratio": "신규",
                    "failure_count": "신규",
                    "mtbf_days": "신규",
                },
            })
    return result


def build_model_component_profiles(
    machines: pd.DataFrame,
    failures: pd.DataFrame,
    comp_median_intervals: dict[str, float | None],
) -> list[dict]:
    model_map = machines.set_index("machineID")["model"].to_dict()
    model_counts = machines["model"].value_counts().to_dict()

    failures_with_model = failures.copy()
    failures_with_model["model_code"] = failures_with_model["machineID"].map(model_map)

    fail_cross = (
        failures_with_model.groupby(["model_code", "failure"]).size()
        .unstack(fill_value=0)
    )

    result = []
    for model_code in sorted(machines["model"].unique()):
        mc = int(model_counts.get(model_code, 0))
        for comp in COMPS:
            pid = f"{model_code}-{comp}"
            total_fails = 0
            if model_code in fail_cross.index and comp in fail_cross.columns:
                total_fails = int(fail_cross.loc[model_code, comp])
            fpm = round(total_fails / mc, 1) if mc > 0 else None
            result.append({
                "profile_id": pid,
                "model_code": model_code,
                "comp_code": comp,
                "machine_count": mc,
                "total_failures": total_fails,
                "failures_per_machine": fpm,
                "median_interval_days": comp_median_intervals.get(comp),
                "_source": {
                    "profile_id": "신규",
                    "model_code": "원본",
                    "comp_code": "원본",
                    "machine_count": "신규",
                    "total_failures": "신규",
                    "failures_per_machine": "신규",
                    "median_interval_days": "신규",
                },
            })
    return result


def build_part_demand_profiles(maint_classified: pd.DataFrame) -> list[dict]:
    maint_2015 = maint_classified[maint_classified["datetime"].dt.year == 2015].copy()
    maint_2015 = maint_2015.copy()
    maint_2015["month"] = maint_2015["datetime"].dt.to_period("M")
    all_months = pd.period_range("2015-01", "2015-12", freq="M")

    result = []
    for comp in COMPS:
        cm = maint_2015[maint_2015["comp"] == comp]
        monthly = cm.groupby("month").size().reindex(all_months, fill_value=0)

        total = int(monthly.sum())
        monthly_mean = round(float(monthly.mean()), 1)
        monthly_median = float(monthly.median())
        monthly_peak = int(monthly.max())

        preventive_share = (
            round((cm["type"] == "preventive").sum() / len(cm), 4)
            if len(cm) > 0 else None
        )

        result.append({
            "comp_code": comp,
            "total_2015": total,
            "monthly_mean": monthly_mean,
            "monthly_median": monthly_median,
            "monthly_peak": monthly_peak,
            "preventive_share": preventive_share,
            "_source": {
                "comp_code": "원본",
                "total_2015": "신규",
                "monthly_mean": "신규",
                "monthly_median": "신규",
                "monthly_peak": "신규",
                "preventive_share": "신규",
            },
        })
    return result


def build_peer_groups(machines: pd.DataFrame) -> list[dict]:
    seen: dict[str, dict] = {}
    for _, row in machines.iterrows():
        model = str(row["model"])
        age = int(row["age"])
        gid = f"{model}-{age - 3}-{age + 3}"
        if gid not in seen:
            members = machines[
                (machines["model"] == model) &
                (machines["age"] >= age - 3) &
                (machines["age"] <= age + 3)
            ]
            seen[gid] = {
                "group_id": gid,
                "model_code": model,
                "age_min": age - 3,
                "age_max": age + 3,
                "member_count": int(len(members)),
                "_source": {
                    "group_id": "신규",
                    "model_code": "계산",
                    "age_min": "계산",
                    "age_max": "계산",
                    "member_count": "계산",
                },
            }
    return list(seen.values())


def build_error_types(errors: pd.DataFrame, failures: pd.DataFrame) -> list[dict]:
    fail_lookup: dict[int, list] = {}
    for _, row in failures.iterrows():
        mid = int(row["machineID"])
        fail_lookup.setdefault(mid, []).append(row["datetime"])

    result = []
    for ecode in sorted(errors["errorID"].unique()):
        occs = errors[errors["errorID"] == ecode]
        total = int(len(occs))
        converted = 0
        for _, row in occs.iterrows():
            mid = int(row["machineID"])
            edt = row["datetime"]
            for fdt in fail_lookup.get(mid, []):
                if edt < fdt <= edt + pd.Timedelta(hours=24):
                    converted += 1
                    break

        result.append({
            "error_code": ecode,
            "display_name": ERROR_DISPLAY.get(ecode, ecode),
            "total_occurrences": total,
            "converted_to_failure_24h": converted,
            "failure_conversion_rate_24h": round(converted / total, 4) if total > 0 else None,
            "_source": {
                "error_code": "원본",
                "display_name": "계산",
                "total_occurrences": "계산",
                "converted_to_failure_24h": "계산",
                "failure_conversion_rate_24h": "계산",
            },
        })
    return result


def build_error_events(errors: pd.DataFrame) -> list[dict]:
    result = []
    for i, (_, row) in enumerate(errors.reset_index(drop=True).iterrows()):
        result.append({
            "event_id": f"err-{int(row['machineID'])}-{i:05d}",
            "machine_id": int(row["machineID"]),
            "occurred_at": str(row["datetime"]),
            "error_code": str(row["errorID"]),
            "_source": {"occurred_at": "원본", "error_code": "원본", "machine_id": "원본"},
        })
    return result


def build_failure_events(failures: pd.DataFrame) -> list[dict]:
    result = []
    for i, (_, row) in enumerate(failures.reset_index(drop=True).iterrows()):
        result.append({
            "event_id": f"fail-{int(row['machineID'])}-{i:05d}",
            "machine_id": int(row["machineID"]),
            "occurred_at": str(row["datetime"]),
            "comp_code": str(row["failure"]),
            "_source": {"occurred_at": "원본", "comp_code": "원본", "machine_id": "원본"},
        })
    return result


def build_maintenance_records(maint_with_intervals: pd.DataFrame) -> list[dict]:
    result = []
    for i, (_, row) in enumerate(maint_with_intervals.reset_index(drop=True).iterrows()):
        performed_at = row["datetime"]
        in_scope = bool(performed_at >= TRAINING_SCOPE_START)
        interval = row["interval_since_previous_days"]
        interval = None if pd.isna(interval) else round(float(interval), 1)
        result.append({
            "record_id": f"maint-{int(row['machineID'])}-{i:05d}",
            "machine_id": int(row["machineID"]),
            "performed_at": str(performed_at),
            "comp_code": str(row["comp"]),
            "type": str(row["type"]),
            "failure_within_24h_before": bool(row["failure_within_24h_before"]),
            "interval_since_previous_days": interval,
            "in_training_scope": in_scope,
            "_source": {
                "performed_at": "원본",
                "comp_code": "원본",
                "type": "계산",
                "failure_within_24h_before": "계산",
                "interval_since_previous_days": "신규",
                "in_training_scope": "신규",
            },
        })
    return result


# ── 링크 빌더 ─────────────────────────────────────────────────────────────────

def build_links(
    machines_list: list[dict],
    machine_models_list: list[dict],
    components_list: list[dict],
    component_classes_list: list[dict],
    model_component_profiles_list: list[dict],
    part_demand_profiles_list: list[dict],
    sensor_channels_list: list[dict],
    peer_groups_list: list[dict],
    error_types_list: list[dict],
    error_events_list: list[dict],
    failure_events_list: list[dict],
    maintenance_records_list: list[dict],
    machines_df: pd.DataFrame,
) -> dict[str, list[dict]]:
    def lnk(from_id: Any, to_id: Any) -> dict:
        return {"from": from_id, "to": to_id}

    links: dict[str, list[dict]] = {}

    # machine_has_model
    links["machine_has_model"] = [
        lnk(m["machine_id"], m["model_code"]) for m in machines_list
    ]

    # machine_has_component
    links["machine_has_component"] = [
        lnk(c["machine_id"], c["component_id"]) for c in components_list
    ]

    # component_has_class
    links["component_has_class"] = [
        lnk(c["component_id"], c["comp_code"]) for c in components_list
    ]

    # model_component_profile_of_model
    links["model_component_profile_of_model"] = [
        lnk(p["profile_id"], p["model_code"]) for p in model_component_profiles_list
    ]

    # model_component_profile_of_class
    links["model_component_profile_of_class"] = [
        lnk(p["profile_id"], p["comp_code"]) for p in model_component_profiles_list
    ]

    # component_class_has_demand_profile
    demand_codes = {d["comp_code"] for d in part_demand_profiles_list}
    links["component_class_has_demand_profile"] = [
        lnk(cc["comp_code"], cc["comp_code"])
        for cc in component_classes_list
        if cc["comp_code"] in demand_codes
    ]

    # component_monitored_by (Component → SensorChannel)
    links["component_monitored_by"] = [
        lnk(c["component_id"], COMP_SENSOR[c["comp_code"]])
        for c in components_list
        if c["comp_code"] in COMP_SENSOR
    ]

    # machine_belongs_to_peer_group
    age_map = dict(zip(machines_df["machineID"], machines_df["age"]))
    model_map = dict(zip(machines_df["machineID"], machines_df["model"]))
    links["machine_belongs_to_peer_group"] = [
        lnk(
            m["machine_id"],
            f"{model_map[m['machine_id']]}-{age_map[m['machine_id']] - 3}-{age_map[m['machine_id']] + 3}",
        )
        for m in machines_list
    ]

    # machine_had_error
    links["machine_had_error"] = [
        lnk(e["machine_id"], e["event_id"]) for e in error_events_list
    ]

    # error_event_has_type
    links["error_event_has_type"] = [
        lnk(e["event_id"], e["error_code"]) for e in error_events_list
    ]

    # error_type_associated_with_component (ErrorType → ComponentClass)
    et_links = []
    for ecode, comps in ERROR_COMP_ASSOC.items():
        for comp in comps:
            et_links.append(lnk(ecode, comp))
    links["error_type_associated_with_component"] = et_links

    # component_had_failure (Component → FailureEvent)
    links["component_had_failure"] = [
        lnk(f"{f['machine_id']}-{f['comp_code']}", f["event_id"])
        for f in failure_events_list
    ]

    # component_had_maintenance (Component → MaintenanceRecord)
    links["component_had_maintenance"] = [
        lnk(f"{m['machine_id']}-{m['comp_code']}", m["record_id"])
        for m in maintenance_records_list
    ]

    # maintenance_followed_failure (reactive MaintenanceRecord → FailureEvent)
    fail_by_comp: dict[tuple, list] = {}
    for fe in failure_events_list:
        key = (fe["machine_id"], fe["comp_code"])
        fail_by_comp.setdefault(key, []).append(
            (pd.Timestamp(fe["occurred_at"]), fe["event_id"])
        )

    mf_links = []
    for mr in maintenance_records_list:
        if mr["type"] != "reactive":
            continue
        mid = mr["machine_id"]
        comp = mr["comp_code"]
        mdt = pd.Timestamp(mr["performed_at"])
        window_start = mdt - pd.Timedelta(hours=24)

        # 경계 (mdt - 24h, mdt] — classify_maintenance 와 동일해야 한다
        candidates = [
            (dt, eid)
            for dt, eid in fail_by_comp.get((mid, comp), [])
            if window_start < dt <= mdt
        ]
        if candidates:
            latest_dt, latest_eid = max(candidates, key=lambda x: x[0])
            mf_links.append(lnk(mr["record_id"], latest_eid))

    links["maintenance_followed_failure"] = mf_links

    return links


# ── 스키마 정의 ───────────────────────────────────────────────────────────────

OBJECT_TYPES = [
    {"type": "Machine", "interfaces": ["Monitorable"], "id_field": "machine_id", "instance_file": "machine.jsonl"},
    {"type": "MachineModel", "interfaces": ["FleetAggregate"], "id_field": "model_code", "instance_file": "machine_model.jsonl"},
    {"type": "Component", "interfaces": ["Monitorable", "Maintainable"], "id_field": "component_id", "instance_file": "component.jsonl"},
    {"type": "ComponentClass", "interfaces": ["FleetAggregate"], "id_field": "comp_code", "instance_file": "component_class.jsonl"},
    {"type": "ModelComponentProfile", "interfaces": ["FleetAggregate"], "id_field": "profile_id", "instance_file": "model_component_profile.jsonl"},
    {"type": "PartDemandProfile", "interfaces": ["FleetAggregate"], "id_field": "comp_code", "instance_file": "part_demand_profile.jsonl"},
    {"type": "SensorChannel", "interfaces": ["Monitorable"], "id_field": "sensor_code", "instance_file": "sensor_channel.jsonl"},
    {"type": "PeerGroup", "interfaces": [], "id_field": "group_id", "instance_file": "peer_group.jsonl"},
    {"type": "ErrorType", "interfaces": [], "id_field": "error_code", "instance_file": "error_type.jsonl"},
    {"type": "ErrorEvent", "interfaces": ["TemporalEvent"], "id_field": "event_id", "instance_file": "error_event.jsonl"},
    {"type": "FailureEvent", "interfaces": ["TemporalEvent"], "id_field": "event_id", "instance_file": "failure_event.jsonl"},
    {"type": "MaintenanceRecord", "interfaces": ["TemporalEvent", "Auditable"], "id_field": "record_id", "instance_file": "maintenance_record.jsonl"},
    {"type": "BaselineProfile", "interfaces": ["Versioned"], "id_field": "version", "instance_file": "baseline_profile.jsonl"},
    {"type": "ThresholdPolicy", "interfaces": ["Versioned"], "id_field": "version", "instance_file": "threshold_policy.jsonl"},
]

LINK_TYPES = [
    {"type": "machine_has_model", "from_type": "Machine", "to_type": "MachineModel", "cardinality": "many-to-one", "source": "원본"},
    {"type": "machine_has_component", "from_type": "Machine", "to_type": "Component", "cardinality": "one-to-many", "source": "신규"},
    {"type": "component_has_class", "from_type": "Component", "to_type": "ComponentClass", "cardinality": "many-to-one", "source": "신규"},
    {"type": "model_component_profile_of_model", "from_type": "ModelComponentProfile", "to_type": "MachineModel", "cardinality": "many-to-one", "source": "신규"},
    {"type": "model_component_profile_of_class", "from_type": "ModelComponentProfile", "to_type": "ComponentClass", "cardinality": "many-to-one", "source": "신규"},
    {"type": "component_class_has_demand_profile", "from_type": "ComponentClass", "to_type": "PartDemandProfile", "cardinality": "one-to-one", "source": "신규"},
    {"type": "component_monitored_by", "from_type": "Component", "to_type": "SensorChannel", "cardinality": "many-to-one", "source": "계산", "note": "설비당 계통 단위: 각 Component는 정확히 1개 SensorChannel에 연결"},
    {"type": "machine_belongs_to_peer_group", "from_type": "Machine", "to_type": "PeerGroup", "cardinality": "many-to-many", "source": "계산"},
    {"type": "machine_had_error", "from_type": "Machine", "to_type": "ErrorEvent", "cardinality": "one-to-many", "source": "원본"},
    {"type": "error_event_has_type", "from_type": "ErrorEvent", "to_type": "ErrorType", "cardinality": "many-to-one", "source": "원본"},
    {"type": "error_type_associated_with_component", "from_type": "ErrorType", "to_type": "ComponentClass", "cardinality": "many-to-many", "source": "계산", "note": "설계 문서는 Component로 표기하나 연관은 클래스 수준이므로 ComponentClass로 구현"},
    {"type": "component_had_failure", "from_type": "Component", "to_type": "FailureEvent", "cardinality": "one-to-many", "source": "원본"},
    {"type": "component_had_maintenance", "from_type": "Component", "to_type": "MaintenanceRecord", "cardinality": "one-to-many", "source": "원본"},
    {"type": "maintenance_followed_failure", "from_type": "MaintenanceRecord", "to_type": "FailureEvent", "cardinality": "many-to-one", "source": "계산", "note": "reactive 기록만. 선행 고장 중 가장 최근 1건에 연결"},
]


# ── 무결성 검사 ───────────────────────────────────────────────────────────────

def _get_id_sets(instances: dict[str, list[dict]]) -> dict[str, set]:
    """객체 타입별 ID 집합."""
    type_to_field = {ot["type"]: ot["id_field"] for ot in OBJECT_TYPES}
    type_to_file = {ot["instance_file"].replace(".jsonl", ""): ot["type"] for ot in OBJECT_TYPES}

    id_sets: dict[str, set] = {}
    for fname, objs in instances.items():
        otype = type_to_file.get(fname)
        if otype and otype in type_to_field:
            field = type_to_field[otype]
            id_sets[otype] = {o[field] for o in objs}
    return id_sets


def _type_from_link_name(link_name: str) -> tuple[str, str]:
    for lt in LINK_TYPES:
        if lt["type"] == link_name:
            return lt["from_type"], lt["to_type"]
    return ("Unknown", "Unknown")


def check_integrity(instances: dict[str, list[dict]], links: dict[str, list[dict]]) -> dict:
    id_sets = _get_id_sets(instances)

    # 링크 타입 → (from_type, to_type) 대응
    lt_map = {lt["type"]: (lt["from_type"], lt["to_type"]) for lt in LINK_TYPES}

    # 역방향 id_sets: ComponentClass는 comp_code, PartDemandProfile도 comp_code
    id_sets["ComponentClass"] = id_sets.get("ComponentClass", set())
    id_sets["PartDemandProfile"] = id_sets.get("PartDemandProfile", set())

    dangling = 0
    dangling_details: list[str] = []

    for link_name, link_list in links.items():
        from_type, to_type = lt_map.get(link_name, ("?", "?"))
        from_ids = id_sets.get(from_type, set())
        to_ids = id_sets.get(to_type, set())

        for lnk in link_list:
            fv = lnk["from"]
            tv = lnk["to"]
            if from_ids and fv not in from_ids:
                dangling += 1
                dangling_details.append(f"{link_name}: from={fv} not in {from_type}")
            if to_ids and tv not in to_ids:
                dangling += 1
                dangling_details.append(f"{link_name}: to={tv} not in {to_type}")

    # 중복 ID 검사
    dup_count = 0
    dup_details: list[str] = []
    for fname, objs in instances.items():
        type_map = {ot["instance_file"].replace(".jsonl", ""): ot for ot in OBJECT_TYPES}
        ot = type_map.get(fname)
        if ot:
            field = ot["id_field"]
            ids = [o[field] for o in objs]
            unique_ids = set(ids)
            if len(ids) != len(unique_ids):
                dups = len(ids) - len(unique_ids)
                dup_count += dups
                dup_details.append(f"{fname}: {dups} 중복 ID")

    # component_monitored_by one-to-one 확인 (각 Component→SensorChannel은 1개)
    cbm = links.get("component_monitored_by", [])
    from_counts = pd.Series([l["from"] for l in cbm]).value_counts()
    cbm_violations = int((from_counts > 1).sum())

    return {
        "dangling_references": dangling,
        "dangling_details": dangling_details[:20],
        "duplicate_ids": dup_count,
        "duplicate_details": dup_details,
        "component_monitored_by_violations": cbm_violations,
        "passed": dangling == 0 and dup_count == 0 and cbm_violations == 0,
    }


# ── 검증 ─────────────────────────────────────────────────────────────────────

EXPECTED = {
    "instance_counts": {
        "machine": 100,
        "component": 400,
        "error_event": 3919,
        "failure_event": 761,
        "maintenance_record": 3286,
        "peer_group": 56,
    },
    "model_counts": {"model3": 35, "model4": 32, "model2": 17, "model1": 16},
    "age_range": {"min": 0, "max": 20},
    # 결정 004 measured — 정비 유형 분해와 유형별 다음 교체까지 중앙값.
    # 경계 (mdt - 24h, mdt] 로 판정해야 재현된다. 끝 경계를 배제하면 reactive 가 0 이 된다.
    "maint_type_counts": {"preventive": 2543, "reactive": 743},
    "maint_type_median_intervals": {"preventive": 45.0, "reactive": 30.0},
    "maintenance_followed_failure_links": 743,
    # 아래는 계통별 중앙값이며 위의 유형별 중앙값과 다른 축이다 (우연히 값이 겹침)
    "comp_median_intervals": {"comp1": 45.0, "comp2": 30.0, "comp3": 45.0, "comp4": 45.0},
    "comp_total_failures": {"comp1": 192, "comp2": 259, "comp3": 131, "comp4": 179},
    "part_demand_total_2015": {"comp1": 702, "comp2": 761, "comp3": 706, "comp4": 710},
    "part_demand_monthly_mean": {"comp1": 58.5, "comp2": 63.4, "comp3": 58.8, "comp4": 59.2},
    "part_demand_monthly_peak": {"comp1": 67, "comp2": 76, "comp3": 75, "comp4": 71},
    "model_failure_rate": {"model1": 11.8, "model2": 9.9, "model3": 6.3, "model4": 5.7},
    "model_comp_failures_per_machine": {
        "model1": {"comp1": 2.1, "comp2": 2.9, "comp3": 4.2, "comp4": 2.6},
        "model2": {"comp1": 1.8, "comp2": 2.4, "comp3": 3.7, "comp4": 2.0},
        "model3": {"comp1": 1.9, "comp2": 2.5, "comp3": 0.0, "comp4": 1.8},
        "model4": {"comp1": 1.9, "comp2": 2.6, "comp3": 0.0, "comp4": 1.2},
    },
    "machine_failure_dist": {"min": 2, "median": 7, "max": 19, "zero_failure_count": 2},
}


def build_actual_stats(
    instances: dict[str, list[dict]],
    links: dict[str, list[dict]],
) -> dict:
    machines = instances["machine"]
    components = instances["component"]
    machine_models = instances["machine_model"]
    comp_classes = instances["component_class"]
    pdp = instances["part_demand_profile"]
    mcp = instances["model_component_profile"]
    peer_groups = instances["peer_group"]

    fc_list = [m["failure_count"] for m in machines]
    nonzero_fc = [f for f in fc_list if f > 0]

    model_counts = {}
    for m in machines:
        model_counts[m["model_code"]] = model_counts.get(m["model_code"], 0) + 1

    comp_medians = {cc["comp_code"]: cc["median_interval_days"] for cc in comp_classes}
    comp_failures = {cc["comp_code"]: cc["total_failures"] for cc in comp_classes}

    demand_total = {d["comp_code"]: d["total_2015"] for d in pdp}
    demand_mean = {d["comp_code"]: d["monthly_mean"] for d in pdp}
    demand_peak = {d["comp_code"]: d["monthly_peak"] for d in pdp}

    model_fpm = {mm["model_code"]: mm["failure_rate_per_machine"] for mm in machine_models}

    mcp_fpm: dict[str, dict[str, float | None]] = {}
    for p in mcp:
        mc = p["model_code"]
        cc = p["comp_code"]
        mcp_fpm.setdefault(mc, {})[cc] = p["failures_per_machine"]

    return {
        "instance_counts": {
            "machine": len(instances.get("machine", [])),
            "machine_model": len(instances.get("machine_model", [])),
            "component": len(instances.get("component", [])),
            "component_class": len(instances.get("component_class", [])),
            "model_component_profile": len(instances.get("model_component_profile", [])),
            "part_demand_profile": len(instances.get("part_demand_profile", [])),
            "sensor_channel": len(instances.get("sensor_channel", [])),
            "peer_group": len(instances.get("peer_group", [])),
            "error_type": len(instances.get("error_type", [])),
            "error_event": len(instances.get("error_event", [])),
            "failure_event": len(instances.get("failure_event", [])),
            "maintenance_record": len(instances.get("maintenance_record", [])),
            "baseline_profile": len(instances.get("baseline_profile", [])),
            "threshold_policy": len(instances.get("threshold_policy", [])),
        },
        "model_counts": model_counts,
        "age_range": {
            "min": min(m["age"] for m in machines),
            "max": max(m["age"] for m in machines),
        },
        "comp_median_intervals": comp_medians,
        "comp_total_failures": comp_failures,
        "part_demand_total_2015": demand_total,
        "part_demand_monthly_mean": demand_mean,
        "part_demand_monthly_peak": demand_peak,
        "model_failure_rate": model_fpm,
        "model_comp_failures_per_machine": mcp_fpm,
        "machine_failure_dist": {
            "min": min(nonzero_fc) if nonzero_fc else None,
            "median": float(np.median(nonzero_fc)) if nonzero_fc else None,
            "max": max(nonzero_fc) if nonzero_fc else None,
            "zero_failure_count": sum(1 for f in fc_list if f == 0),
            "all_median": float(np.median(fc_list)),
        },
        "link_counts": {k: len(v) for k, v in links.items()},
    }


def verify_against_expected(actual: dict) -> list[str]:
    """기대값과 실측 비교. 불일치 항목 목록 반환."""
    issues: list[str] = []

    def chk(label: str, got: Any, expected: Any, tol: float = 0.05) -> None:
        if got is None or expected is None:
            if got != expected:
                issues.append(f"{label}: got={got} expected={expected}")
            return
        if isinstance(expected, float) or isinstance(got, float):
            if abs(float(got) - float(expected)) > tol:
                issues.append(f"{label}: got={got} expected={expected}")
        elif got != expected:
            issues.append(f"{label}: got={got} expected={expected}")

    # 인스턴스 수
    for key, exp_val in EXPECTED["instance_counts"].items():
        got_val = actual["instance_counts"].get(key)
        chk(f"instance_count.{key}", got_val, exp_val)

    # 모델 분포
    for model, exp_cnt in EXPECTED["model_counts"].items():
        chk(f"model_count.{model}", actual["model_counts"].get(model), exp_cnt)

    # 연식 범위
    chk("age_range.min", actual["age_range"]["min"], EXPECTED["age_range"]["min"])
    chk("age_range.max", actual["age_range"]["max"], EXPECTED["age_range"]["max"])

    # 계통별 교체 간격 중앙값
    for comp, exp_val in EXPECTED["comp_median_intervals"].items():
        chk(f"comp_median_interval.{comp}", actual["comp_median_intervals"].get(comp), exp_val)

    # 계통별 총 고장
    for comp, exp_val in EXPECTED["comp_total_failures"].items():
        chk(f"comp_total_failures.{comp}", actual["comp_total_failures"].get(comp), exp_val)

    # 계통별 2015 교체 건수
    for comp, exp_val in EXPECTED["part_demand_total_2015"].items():
        chk(f"part_demand_total_2015.{comp}", actual["part_demand_total_2015"].get(comp), exp_val)

    # 월평균
    for comp, exp_val in EXPECTED["part_demand_monthly_mean"].items():
        chk(f"part_demand_monthly_mean.{comp}", actual["part_demand_monthly_mean"].get(comp), exp_val, tol=0.1)

    # 월 최대
    for comp, exp_val in EXPECTED["part_demand_monthly_peak"].items():
        chk(f"part_demand_monthly_peak.{comp}", actual["part_demand_monthly_peak"].get(comp), exp_val)

    # 모델별 대당 고장
    for model, exp_val in EXPECTED["model_failure_rate"].items():
        chk(f"model_failure_rate.{model}", actual["model_failure_rate"].get(model), exp_val, tol=0.1)

    # 모델 × 계통 대당 고장
    for model, comp_dict in EXPECTED["model_comp_failures_per_machine"].items():
        for comp, exp_val in comp_dict.items():
            got_val = actual["model_comp_failures_per_machine"].get(model, {}).get(comp)
            chk(f"model_comp_fpm.{model}.{comp}", got_val, exp_val, tol=0.1)

    # 설비별 고장 분포
    dist = actual["machine_failure_dist"]
    chk("machine_failure_dist.min", dist.get("min"), EXPECTED["machine_failure_dist"]["min"])
    chk("machine_failure_dist.max", dist.get("max"), EXPECTED["machine_failure_dist"]["max"])
    chk("machine_failure_dist.zero_failure_count", dist.get("zero_failure_count"), EXPECTED["machine_failure_dist"]["zero_failure_count"])

    return issues


# ── 파일 출력 ─────────────────────────────────────────────────────────────────

def write_all(
    instances: dict[str, list[dict]],
    links: dict[str, list[dict]],
    actual_stats: dict,
    integrity: dict,
    verify_issues: list[str],
    out_dir: Path = Path("ontology"),
) -> None:
    inst_dir = out_dir / "instances"
    link_dir = out_dir / "links"
    inst_dir.mkdir(parents=True, exist_ok=True)
    link_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "object_types.json").write_text(
        json.dumps(OBJECT_TYPES, ensure_ascii=False, indent=2)
    )
    (out_dir / "link_types.json").write_text(
        json.dumps(LINK_TYPES, ensure_ascii=False, indent=2)
    )

    for fname, objs in instances.items():
        path = inst_dir / f"{fname}.jsonl"
        path.write_text("\n".join(_dumps(o) for o in objs) + "\n")

    for lname, llist in links.items():
        path = link_dir / f"{lname}.jsonl"
        path.write_text("\n".join(_dumps(l) for l in llist) + "\n")

    report = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "actual_stats": actual_stats,
        "integrity": integrity,
        "verification": {
            "issues": verify_issues,
            "passed": len(verify_issues) == 0,
        },
        "overall_passed": integrity["passed"] and len(verify_issues) == 0,
    }
    (out_dir / "build_report.json").write_text(
        json.dumps(_clean(report), ensure_ascii=False, indent=2)
    )


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Azure PdM 온톨로지 빌더")
    parser.add_argument("--as-of", default="2016-01-01 00:00:00",
                        help="기준 시각 (기본값: 2016-01-01 00:00:00)")
    parser.add_argument("--verify", action="store_true",
                        help="기대값과 대조하여 불일치 시 종료 코드 1 반환")
    parser.add_argument("--out", default="ontology",
                        help="출력 디렉터리 (기본값: ontology)")
    args = parser.parse_args()

    as_of = pd.Timestamp(args.as_of)
    out_dir = Path(args.out)

    print(f"[build_ontology] 기준 시각: {as_of}")
    print("[build_ontology] 데이터 로드 중...")
    errors, failures, maint, machines, baseline_raw, thresholds_raw = load_raw_data()

    print("[build_ontology] 정비 유형 분류 중...")
    maint_classified = classify_maintenance(maint, failures)
    maint_with_intervals = add_maint_intervals(maint_classified)
    comp_median_intervals = compute_comp_median_intervals(maint_with_intervals)

    print("[build_ontology] 인스턴스 생성 중...")
    baseline_profiles = build_baseline_profile(baseline_raw)
    threshold_policies = build_threshold_policy(thresholds_raw)
    sensor_channels = build_sensor_channels()
    component_classes = build_component_classes(maint_with_intervals, failures, comp_median_intervals)
    machine_models = build_machine_models(machines, failures)
    machines_list = build_machines(machines, maint_classified, failures, as_of)
    components = build_components(machines, maint_classified, failures, comp_median_intervals, as_of)
    model_component_profiles = build_model_component_profiles(machines, failures, comp_median_intervals)
    part_demand_profiles = build_part_demand_profiles(maint_with_intervals)
    peer_groups = build_peer_groups(machines)
    error_types = build_error_types(errors, failures)
    error_events = build_error_events(errors)
    failure_events = build_failure_events(failures)
    maintenance_records = build_maintenance_records(maint_with_intervals)

    instances = {
        "machine": machines_list,
        "machine_model": machine_models,
        "component": components,
        "component_class": component_classes,
        "model_component_profile": model_component_profiles,
        "part_demand_profile": part_demand_profiles,
        "sensor_channel": sensor_channels,
        "peer_group": peer_groups,
        "error_type": error_types,
        "error_event": error_events,
        "failure_event": failure_events,
        "maintenance_record": maintenance_records,
        "baseline_profile": baseline_profiles,
        "threshold_policy": threshold_policies,
    }

    print("[build_ontology] 링크 생성 중...")
    links = build_links(
        machines_list, machine_models, components, component_classes,
        model_component_profiles, part_demand_profiles, sensor_channels,
        peer_groups, error_types, error_events, failure_events,
        maintenance_records, machines,
    )

    print("[build_ontology] 무결성 검사 중...")
    integrity = check_integrity(instances, links)

    print("[build_ontology] 통계 산출 중...")
    actual_stats = build_actual_stats(instances, links)

    verify_issues: list[str] = []
    if args.verify:
        verify_issues = verify_against_expected(actual_stats)
    else:
        verify_issues = verify_against_expected(actual_stats)

    print("[build_ontology] 파일 출력 중...")
    write_all(instances, links, actual_stats, integrity, verify_issues, out_dir)

    # 결과 요약
    counts = actual_stats["instance_counts"]
    print(f"\n=== 생성 완료 ===")
    for k, v in counts.items():
        print(f"  {k:30s}: {v}")
    print(f"\n무결성: dangling={integrity['dangling_references']} dup_id={integrity['duplicate_ids']}")
    print(f"검증 이슈: {len(verify_issues)}건")

    if integrity["passed"] and len(verify_issues) == 0:
        print("✓ 모든 검증 통과")
        return 0
    else:
        if not integrity["passed"]:
            print("✗ 무결성 검사 실패", file=sys.stderr)
            for d in integrity.get("dangling_details", []):
                print(f"  {d}", file=sys.stderr)
        if verify_issues:
            print("✗ 검증 불일치:", file=sys.stderr)
            for issue in verify_issues:
                print(f"  {issue}", file=sys.stderr)
        if args.verify:
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
