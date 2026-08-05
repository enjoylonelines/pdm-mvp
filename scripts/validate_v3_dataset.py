"""
v3 데이터셋 연결 검증 스크립트

확인 항목:
  1. v3 경로 존재 여부
  2. dataset_version 확인 (canonical-ai4i-physics-v3.1)
  3. 필수 파일 존재 여부
  4. result_artifact.jsonl 최소 1건 파싱 가능 여부
  5. evaluation_truth 미사용 원칙 (파일은 있어도 import/참조 금지 확인)
  6. 어댑터 변환 최소 동작 확인

사용법:
  python3 scripts/validate_v3_dataset.py
  python3 scripts/validate_v3_dataset.py --v3-path /custom/path
"""

from __future__ import annotations

import json
import os
import sys
import argparse
from pathlib import Path

# 절대 경로를 코드에 넣지 않는다. 환경변수 > 기본값 순.
# 기준본은 v3.1 이며, 같은 이름의 _v3/ 폴더는 과거 작업 경로가 섞여 있어 기준본이 아니다.
DEFAULT_V3_PATH = Path(
    os.environ.get(
        "PDM_CANONICAL_PATH",
        Path.home() / "Downloads" / "predictive_maintenance_canonical_v3.1",
    )
)
DATASET_VERSION = "canonical-ai4i-physics-v3.1"

REQUIRED_FILES = [
    "canonical/dataset/asset_master.csv",
    "canonical/dataset/compressor_sensor_observation.csv",
    "canonical/dataset/cnc_sensor_observation.csv",
    "canonical/dataset/maintenance_event.csv",
    "canonical/model_outputs/model_contract.json",
    "canonical/model_outputs/result_artifact.jsonl",
    "canonical/model_outputs/prediction_snapshot.jsonl",
    "canonical/model_outputs/prediction_factor.jsonl",
]

EVAL_TRUTH_PATHS = [
    "canonical/evaluation_truth",
]


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    msg = f"[{status}] {label}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)
    return ok


def run_checks(v3_path: Path) -> bool:
    all_pass = True

    # 1. 경로 존재
    ok = v3_path.exists() and v3_path.is_dir()
    all_pass &= check("v3 경로 존재", ok, str(v3_path))

    if not ok:
        print("\n경로가 없으므로 이후 검사를 건너뜁니다.")
        return False

    # 2. dataset_version 확인
    manifest_path = v3_path / "canonical" / "dataset" / "dataset_manifest.json"
    contract_path = v3_path / "canonical" / "model_outputs" / "model_contract.json"
    version_ok = False
    version_found = None
    for fp in [manifest_path, contract_path]:
        if fp.exists():
            try:
                data = json.loads(fp.read_text())
                v = data.get("dataset_version") or data.get("generator_version")
                if v == DATASET_VERSION:
                    version_ok = True
                    version_found = v
                    break
                # model_contract has nested check
                for artifact in data.get("canonical_input_sha256", {}).keys():
                    pass  # model_contract uses dataset_version at top level
                v2 = data.get("dataset_version")
                if v2 == DATASET_VERSION:
                    version_ok = True
                    version_found = v2
                    break
            except Exception:
                pass
    all_pass &= check(
        f"dataset_version == {DATASET_VERSION}",
        version_ok,
        f"found: {version_found}" if version_found else "dataset_version 키를 찾지 못했거나 불일치",
    )

    # 3. 필수 파일 존재
    for rel_path in REQUIRED_FILES:
        fp = v3_path / rel_path
        ok = fp.exists()
        all_pass &= check(f"파일 존재: {rel_path}", ok)

    # 4. result_artifact.jsonl 파싱
    ra_path = v3_path / "canonical" / "model_outputs" / "result_artifact.jsonl"
    if ra_path.exists():
        try:
            records = []
            with open(ra_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            ok = len(records) >= 1
            all_pass &= check(
                "result_artifact.jsonl 파싱 가능",
                ok,
                f"총 {len(records)}건 로드 성공",
            )
            # 필수 필드 확인
            required_fields = {
                "artifact_id", "asset_id", "asset_type", "observed_at",
                "failure_probability", "status_grade", "top_factors",
            }
            first = records[0]
            missing = required_fields - set(first.keys())
            all_pass &= check(
                "result_artifact 필수 필드 존재",
                len(missing) == 0,
                f"누락 필드: {missing}" if missing else f"artifact_id={first.get('artifact_id')}",
            )
        except Exception as e:
            all_pass &= check("result_artifact.jsonl 파싱 가능", False, str(e))
    else:
        all_pass &= check("result_artifact.jsonl 파싱 가능", False, "파일 없음")

    # 5. evaluation_truth 미사용 원칙 확인
    eval_paths = [v3_path / ep for ep in EVAL_TRUTH_PATHS]
    eval_exists = any(ep.exists() for ep in eval_paths)
    # 여기서는 경로 존재 자체는 OK — "사용 금지" 원칙 문서 확인
    # azure-pdm 프로젝트 소스에서 evaluation_truth 참조 여부 검사
    project_root = Path(__file__).parent.parent
    eval_refs = []
    for py_file in list(project_root.glob("**/*.py")) + list(project_root.glob("scripts/*.py")):
        if "validate_v3_dataset" in py_file.name:
            continue
        try:
            content = py_file.read_text()
            if "evaluation_truth" in content:
                eval_refs.append(str(py_file.relative_to(project_root)))
        except Exception:
            pass
    no_eval_ref = len(eval_refs) == 0
    all_pass &= check(
        "evaluation_truth 미사용 (프로젝트 소스 참조 없음)",
        no_eval_ref,
        f"참조 파일: {eval_refs}" if eval_refs else "프로젝트 소스 파일에 evaluation_truth 참조 없음",
    )

    # 6. 어댑터 변환 최소 동작
    try:
        adapter_path = Path(__file__).parent / "load_v3_result_artifacts.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("load_v3", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        packages = mod.build_evidence_packages(v3_path, status_filter=["critical"])
        ok = len(packages) >= 1 and "model_prediction" in packages[0]
        all_pass &= check(
            "어댑터 변환 (critical 케이스 1건 이상)",
            ok,
            f"변환된 패키지: {len(packages)}건, model_prediction 존재: {'model_prediction' in packages[0] if packages else False}",
        )
    except Exception as e:
        all_pass &= check("어댑터 변환 최소 동작", False, str(e))

    return all_pass


def main():
    p = argparse.ArgumentParser(description="v3 데이터셋 연결 검증")
    p.add_argument("--v3-path", default=str(DEFAULT_V3_PATH))
    args = p.parse_args()

    v3_path = Path(args.v3_path)
    print(f"=== v3 데이터셋 검증 ===")
    print(f"경로: {v3_path}")
    print(f"기준 버전: {DATASET_VERSION}")
    print()

    ok = run_checks(v3_path)

    print()
    if ok:
        print("=== 전체 PASS ===")
    else:
        print("=== 일부 FAIL — 위 FAIL 항목 확인 필요 ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
