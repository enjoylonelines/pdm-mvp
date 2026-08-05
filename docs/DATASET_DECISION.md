# Dataset Decision — Predictive Maintenance Canonical v3

Date: 2026-08-04

## Decision

The fixed dataset for W1 and team sharing is:

`/Users/hb/Downloads/predictive_maintenance_canonical_v3`

Dataset version:

`canonical-ai4i-physics-v3.0`

This replaces the earlier review targets, including `predictive_maintenance_canonical_v2 2` and the prior fourth-review folder, as the team baseline.

## Why This Dataset Is Fixed

The v3 package resolves the blocking issues found in earlier generations.

Validation status:

- `package_validation.valid`: `true`
- `model_contract`: `pass`
- `model_dataset_binding`: `pass`
- `result_artifact_rows`: `100`
- `prediction_timeline_rows`: `68,233`

AI4I physics checks:

- `corr(air_temperature_k, process_temperature_k)`: `0.920151`, pass
- `corr(rotational_speed_rpm, torque_nm)`: `-0.844947`, pass
- `process_temperature < air_temperature`: `0` rows, pass
- Air temperature std: `1.957508`, target band pass
- Process temperature std: `1.516022`, target band pass
- RPM std: `185.958634`, target band pass
- Torque std: `10.545322`, target band pass

Failure-mode condition checks:

- `PWF`: `14 / 14` pass
- `HDF`: `21 / 21` pass
- `OSF`: `11 / 11` pass
- `TWF`: `6 / 6` pass
- `RNF`: `4 / 4` pass

Experiment observability:

- Public agent cases: `20`
- Positive upstream cases: `16`
- Negative local-only cases: `4`
- `negative_control_case_count`: `4`
- Pressure direction pass: `true`
- Torque direction pass: `true`
- Causal claim allowed: `no`

## Source-Of-Truth Files

Use these files as the official W1 inputs.

| Purpose | File |
|---|---|
| Asset/equipment master | `canonical/dataset/asset_master.csv` |
| Equipment relations | `canonical/dataset/asset_relation.csv` |
| Compressor sensor observations | `canonical/dataset/compressor_sensor_observation.csv` |
| CNC sensor observations | `canonical/dataset/cnc_sensor_observation.csv` |
| CNC production cycles | `canonical/dataset/cnc_production_cycle.csv` |
| Maintenance events | `canonical/dataset/maintenance_event.csv` |
| Model contract | `canonical/model_outputs/model_contract.json` |
| Model metrics | `canonical/model_outputs/model_metrics.json` |
| Core Result Artifact stream | `canonical/model_outputs/result_artifact.jsonl` |
| Prediction snapshots | `canonical/model_outputs/prediction_snapshot.jsonl` |
| Prediction factors | `canonical/model_outputs/prediction_factor.jsonl` |
| Prediction timeline | `canonical/model_outputs/prediction_timeline.jsonl` |
| Package validation | `canonical/validation/package_validation.json` |
| Agent evaluation cases | `experiments/connected_air_supply/public_case_index.json` |

## Usage Policy

`canonical/model_outputs/result_artifact.jsonl` is the first integration target.

The dashboard, API, and LLM report layer should consume Result Artifact records first, then join supporting evidence only when needed.

`canonical/evaluation_truth/*` is evaluation-only. It must not be passed into the dashboard, report generator, LLM prompt, or user-facing explanation path.

`experiments/connected_air_supply/*` is for agent evaluation and connected-air-supply reasoning tests. It is not the primary dashboard source.

## W1 Team Impact

Team lead:

- Freeze this dataset path and version in shared docs.
- Keep the Result Artifact contract as the common agreement point.
- Track unresolved field decisions such as `recommended_decision`, `confidence`, and evidence IDs.

Data/model member:

- Build the adapter from v3 model outputs.
- Start with `result_artifact.jsonl`, then add `prediction_snapshot.jsonl` and `prediction_factor.jsonl`.
- Keep evaluation truth isolated from inference and report generation.

Dashboard/API member:

- Bind list/detail screens to Result Artifact records.
- Treat `status`, `failure_probability`, `predicted_failure_type`, `top_factors`, `equipment`, and `generated_at` as the minimum display contract.

LLM/report member:

- Map Result Artifact plus supporting factors into Evidence Package and report text.
- Keep LLM output limited to explanation, summary, and recommendation formatting.
- Do not let the LLM infer labels from hidden truth files.

## Next Required Edits

Update these project docs/code to point to v3:

- `README.md`
- `TEAM_SHARE_W1.md`
- `RESULT_ARTIFACT_SCHEMA.md`
- `EVIDENCE_PACKAGE_MAPPING.md`
- Any sample JSON that still says it is structure-only or manually approximated

Implementation should prioritize:

1. Read `canonical/model_outputs/result_artifact.jsonl`.
2. Convert one warning/critical case into the azure-pdm Evidence Package shape.
3. Render that case in the dashboard/API/report path.
4. Add a small validation script that checks the dataset path, dataset version, and required files.
