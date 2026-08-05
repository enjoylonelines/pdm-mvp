# Dataset Decision — Predictive Maintenance Canonical v3.1

- Date: 2026-08-04 (v3.0) · **2026-08-05 갱신 (v3.1)**
- 결정 로그: `final/docs/decisions.md` **020** — 004(Azure PdM)를 대체

## Decision

기준 데이터셋 버전:

```
canonical-ai4i-physics-v3.1
model       independent-logreg-v3.1
experiment  relation-reasoning-agent-eval-v3.1
period      2026-08-01 ~ 2026-08-31 (KST) · seed 42 · profile balanced_demo
```

경로는 환경변수로 받는다. 절대 경로를 문서·코드에 넣지 않는다.

```bash
export PDM_CANONICAL_PATH=~/Downloads/predictive_maintenance_canonical_v3.1
```

**배포 기준본은 `predictive_maintenance_canonical_v3.1/` 폴더와 내부 `dist/` ZIP이다.**
같은 이름의 `_v3/` 폴더는 과거 작업 경로가 섞여 있어 기준본으로 보지 않는다.

이 결정은 이전 검토 대상(`predictive_maintenance_canonical_v2 2`, 4차 리뷰 폴더)과
Azure PdM(결정 004)을 모두 대체한다.

## v3.0 → v3.1 변경

두 차례 팀 리뷰에서 확인된 공구 마모 상태 전이·검증·에이전트 증거·문서 정합성
문제를 수정한 배포 기준본이다. AI4I 물리 계약과 Result Artifact 구조는 유지된다.

| 항목 | 내용 |
|---|---|
| 공구 마모 초기화 시점 | 교체를 결정한 `running` 행은 기존 wear 유지. 초기화는 `maintenance_event.started_at`과 같은 tick에서만 |
| Tool wear continuity gate | `running → running` reset 0건, 모든 reset이 `tool_replaced=1` 이벤트와 정렬 |
| Agent evidence | `evidence_observations[]`가 `sensor` · `maintenance` 두 유형 지원. 잘못된 maintenance ID는 점수 0 |
| 문서 정합성 | RPM 생성식(0.30 inverse-power blend), PWF low-power 분기 순서 명시 |

## 구조 (measured)

| 항목 | 수 |
|---|---:|
| Assets (압축기 20 + CNC 80) | 100 |
| Relations `SUPPLIES_AIR_TO` | 80 |
| Sites / Cells | 4 / 20 |
| Compressor observations | 86,400 |
| CNC observations | 345,600 |
| Production cycles | 170,875 |
| Maintenance events | 790 |
| — `failure_recovery` / `planned_tool_change` | 76 / 714 |
| Prediction timeline | 68,208 |
| Result Artifact | 100 |

관측 스키마가 자산 유형마다 다르다.

```
CNC       air_temperature_k · process_temperature_k · rotational_speed_rpm
          · torque_nm · tool_wear_min
압축기     voltage_raw · rotation_raw · pressure_raw · vibration_raw
          · relative_vibration_z · relative_vibration_zone
```

## Azure PdM(004) 대비 해소된 제약

| | Azure PdM | canonical v3.1 |
|---|---|---|
| 설비 종류 | 미정의. 리포트에 설비명 표기 금지 | 압축기 · CNC 명시 |
| 물리 단위 | 미정의. 상대값만 사용 | AI4I 물리 계약 준수 |
| 자산 관계 | 없음 | `SUPPLIES_AIR_TO` 80건 |
| 계층 | 없음 | site → cell → asset |
| 정비 유형 | 24시간 창으로 역추정 | `maintenance_type` 필드 |
| 정비의 원인 사건 | 없음 | `source_event_id` |
| 공구 교체 | 알 수 없음 | `tool_replaced` 플래그 |
| 생산 맥락 | 없음 | 제품별 사이클 170,875행 |

## 유지되는 제약

- **합성 데이터다.** 생성 규칙과 검증이 패키지 안에 있다는 점이 다를 뿐이다
- **`causal_claim_allowed: no`** — `SUPPLIES_AIR_TO` 관계가 있어도 인과 주장은 금지한다.
  결정 008(억제 규칙 폐기, 정보 제시로 전환)의 원칙을 유지한다

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
