# 데이터분석가 재료 정의

- 작성일: 2026-08-05
- 상태: **제안** — 역할 정의는 멘토 확인 필요
- 범위: `pdm-mvp`가 **어떤 필드를 공급하는가**를 정한다. 화면은 프로토타입이 만든다

## 역할 해석

킥오프(결정 000)는 3개 역할을 지정했으나 데이터분석가의 정의가 없다. 다른 두 역할과 **관측 단위**가 갈리는 지점에서 정의한다.

| 역할 | 보는 단위 | 묻는 것 |
|---|---|---|
| 운영 매니저 | 사건 하나 | 지금 세울까, 미룰까 |
| 공정 엔지니어 | 사건 하나 | 어느 센서가 왜 이상한가 |
| **데이터분석가** | **모집단 전체** | **판정 기준이 맞게 잡혔나** |

엔지니어는 "이 사건에서 모델이 뭐라 했나"를 보고(`_eng_model_prediction`이 이미 제공), 분석가는 "그 판정 기준 자체가 타당한가"를 본다. 단건과 모집단이라는 축이 다르므로 화면이 겹치지 않는다.

---

## 공급 가능한 재료 — v3.1에서 실제로 산출됨

### 1. 모델 성능 — `model_metrics.json`

자산 유형별로 제공되며, **site 단위 leave-one-out 교차검증**이 이미 들어 있다.

```
CNC        rows 54,567 · positive 1,312 · prevalence 2.40%
           leave_one_site_out_roc_auc  0.813
           average_precision           0.530
           top_5pct_precision          0.288 · top_5pct_recall 0.598
           folds  S01 0.840 / S02 0.783 / S03 0.809 / S04 …
```

**fold별 편차가 분석가의 핵심 재료다.** S01 0.840 대 S02 0.783 — 사이트마다 성능이 다르다는 뜻이고, 특정 사이트에서 판정이 덜 믿을 만하다는 신호다.

### 2. 등급별 실제 고장률 — 재측정 완료

| 등급 | 표본 | 24h 내 고장 |
|---|---:|---:|
| 알람 | 14,339 | **8.63%** |
| 관찰 | 32,382 | **1.05%** |
| 정상 | 21,487 | **1.00%** |

전체 기저율 **2.63%**.

> **관찰 등급의 변별력이 없다.** 관찰 1.05%와 정상 1.00%의 차이가 0.05%p이고, 둘 다 기저율보다 낮다. **결정 012(3단계 등급 체계) 재검토 근거이며, 분석가 화면의 1순위 항목이다.**
>
> 산출: `prediction_timeline.jsonl` 68,208행 × `maintenance_event.csv`의 `failure_recovery` 76건. `evaluation_truth` 미사용.

### 3. 기여 요인 분포 — `prediction_factor.jsonl`

```
explanation_method   linear_logit_contribution (전건)
상위 feature         rotational_speed_rpm_6h_abs_mean · process_temperature_k_6h_mean
                     air_temperature_k_6h_mean · tool_wear_min_current
```

특정 feature가 판정을 지배하는지, 자산 유형별로 다른지를 본다.

### 4. 모델 계약 — `model_contract.json`

```
cnc_model_uses                   5개 센서
compressor_model_uses            5개 (relative_vibration_z 포함)
asset_relation_used_as_feature   false   ← SUPPLIES_AIR_TO 를 학습에 쓰지 않음
maintenance_rows_excluded        true    ← 정비 중 관측 제외
explanation_methods              linear_logit_contribution
canonical_input_sha256           입력 무결성
```

**`asset_relation_used_as_feature: false`가 중요하다.** 압축기→CNC 관계가 데이터에 있으나 모델은 쓰지 않는다. 인과 주장 금지(`causal_claim_allowed: no`)와 일관되며, 분석가가 이 사실을 확인할 수 있어야 한다.

### 5. 표본 충분성 분포

`pdm-mvp`가 산출한다.

```
peer_comparison.sufficient_peers      동종 5대 미만 자산 수
sensor_evidence.window_rows           관측 창 행 수 분포
status_flags.insufficient_data        데이터 부족 판정 건수
```

---

## 공급하지 않는 것

| 항목 | 이유 |
|---|---|
| `evaluation_truth/` 직접 노출 | 미사용 원칙. `validate_v3_dataset.py`가 소스 참조를 검사한다 |
| 모델 재학습·튜닝 | 결정 000 — 모델 성능 개선에 시간을 쓰지 않는다 |
| 임계값 자동 조정 | 사람이 판단한다. 분석가는 근거를 보고 제안한다 |

---

## `pdm-mvp`가 만들 것

화면이 아니라 **집계 함수**다.

| 함수 | 반환 |
|---|---|
| `grade_failure_rate_table()` | 등급별 고장률 · 표본 · 기저율 · 변별력 판정 |
| `model_metrics_by_asset_type()` | 자산 유형별 지표와 fold 편차 |
| `factor_distribution()` | feature별 기여 빈도·평균 |
| `sample_sufficiency_summary()` | 표본 부족 자산 수와 사유 |

`policy.py`에 등급별 고장률과 변별력 판정이 이미 들어갔다. 나머지 셋은 미구현이다.

---

## 미결

| | 항목 | 확인 대상 |
|---|---|---|
| 1 | 이 역할 해석(모집단 검증자)이 맞는지 | 멘토 |
| 2 | 관찰 등급 변별력 없음 — 3단계를 2단계로 줄일지 | 팀 (결정 012 재검토) |
| 3 | fold별 성능 편차를 화면에 낼지 | 팀 |
