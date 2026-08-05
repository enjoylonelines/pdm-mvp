"""
역할별 리포트 생성기 v2 — 정보 제시 방식. 억제 로직 없음.

변경 이력:
  v1 (report_generator_v1_suppression.py): S1~S5 억제 규칙 보유
  v2 (현재): 억제 로직 전면 제거. 판단 대신 사실 제시.
             임계 1.75로 변경. 임계 미달 항목도 목록에 포함.

판단은 리포트가 대신하지 않는다. 사람이 판단할 재료만 나열한다.

입력:  Evidence Package dict (generate_evidence_package() 출력)
출력:  {"machine_id": int, "timestamp": str,
        "manager": [블록, ...], "engineer": [블록, ...]}
"""

from __future__ import annotations

from z_baseline import (
    load_thresholds_full, load_thresholds,
    DEFAULT_THRESHOLD_PATH,
)

# 부품별 임계 (JSON 에서 로드)
try:
    _THRESHOLDS:      dict[str, float] = load_thresholds(DEFAULT_THRESHOLD_PATH)
    _THRESHOLDS_FULL: dict[str, dict]  = load_thresholds_full(DEFAULT_THRESHOLD_PATH)
except Exception:
    _THRESHOLDS      = {'comp1': 3.75, 'comp2': 4.00, 'comp3': 5.00, 'comp4': 4.25}
    _THRESHOLDS_FULL = {c: {'threshold': t} for c, t in _THRESHOLDS.items()}


# 등급 경계값 (evaluate_metrics.py 검증 결과 기반)
# excess_ratio 구간별 P(failure): <0.8=0.01%, 0.8-1.0=15.7%, >=1.0=24.0%
# 0.8에서 P(failure)가 2.2% → 15.7%로 7배 증가 → 관찰/정상 경계로 타당
GRADE_ALARM = 1.0   # excess_ratio ≥ 1.0  → 알람
GRADE_WATCH = 0.8   # 0.8 ≤ er < 1.0      → 관찰
# er < 0.8 → 정상

def _grade(excess_ratio: float) -> str:
    if excess_ratio >= GRADE_ALARM: return '알람'
    if excess_ratio >= GRADE_WATCH: return '관찰'
    return '정상'


# ── 상수 ─────────────────────────────────────────────────────────────────────

SENSOR_KO: dict[str, str] = {
    'volt':      '전압',
    'rotate':    '회전수',
    'pressure':  '압력',
    'vibration': '진동',
}

COMP_KO: dict[str, str] = {
    'comp1': '부품1(comp1)',
    'comp2': '부품2(comp2)',
    'comp3': '부품3(comp3)',
    'comp4': '부품4(comp4)',
}

_SENSOR_DIRECTION: dict[str, str] = {
    'volt':      'both',
    'rotate':    'negative',
    'pressure':  'both',
    'vibration': 'both',
}

_SENSOR_COMP: dict[str, str] = {
    'volt': 'comp1', 'rotate': 'comp2',
    'pressure': 'comp3', 'vibration': 'comp4',
}

_SENSOR_ORDER = ['volt', 'rotate', 'pressure', 'vibration']

# 부품별 임계 (thresholds.json 에서 로드됨)
# 센서별 배율이 달라(2.33~3.28) 직접 비교 대신 "임계 대비 초과 배수"로 정렬
def _comp_threshold(comp: str) -> float:
    return _THRESHOLDS.get(comp, 5.00)


# ── 보조 함수 ─────────────────────────────────────────────────────────────────

def _pct_label(pct: float) -> str:
    if pct >= 95: return '동급 최상위 5%'
    if pct >= 90: return '동급 상위 10%'
    if pct >= 75: return '동급 상위 25%'
    if pct <= 5:  return '동급 최하위 5%'
    if pct <= 10: return '동급 하위 10%'
    if pct <= 25: return '동급 하위 25%'
    return '동급 중간 범위'


def _z_flag(z: float, direction: str, sensor: str = '') -> str:
    from z_baseline import SENSOR_COMP
    comp = SENSOR_COMP.get(sensor, '')
    thr  = _comp_threshold(comp) if comp else 1.75
    if direction == 'negative':
        return '↓초과' if z <= -thr else '정상'
    return '↑초과' if z >= thr else ('↓초과' if z <= -thr else '정상')


def _block(type_: str, title: str, text: str, source_fields: list[str]) -> dict:
    return {'type': type_, 'title': title, 'text': text, 'source_fields': source_fields}


def _no_data(type_: str, title: str, reason: str) -> dict:
    return {'type': type_, 'title': title,
            'text': f'[근거 부족: {reason}]', 'source_fields': []}


def _all_candidates(pkg: dict) -> list[dict]:
    """
    전 센서-부품 쌍을 "임계 대비 초과 배수(excess_ratio)" 내림차순 정렬.
    excess_ratio = effective_z / comp_threshold
    센서별 배율(2.33~3.28)이 달라 z 직접 비교가 공정하지 않으므로 배수로 정렬.
    임계 미달 항목도 포함(above=False 표기).
    """
    sensors = pkg['sensor_evidence']['sensors']
    result  = []

    for s in _SENSOR_ORDER:
        direction = _SENSOR_DIRECTION[s]
        comp      = _SENSOR_COMP[s]
        threshold = _comp_threshold(comp)
        z         = sensors[s].get('z_score')

        if z is None:
            result.append({
                'sensor': s, 'comp': comp, 'direction': direction,
                'z': None, 'effective_z': 0.0,
                'above': False, 'threshold': threshold, 'excess_ratio': 0.0,
            })
            continue

        if direction == 'negative':
            eff_z = max(-z, 0.0)
            above = z <= -threshold
        else:
            eff_z = abs(z)
            above = eff_z >= threshold

        excess_ratio = eff_z / threshold if threshold > 0 else 0.0
        grade        = _grade(excess_ratio)

        result.append({
            'sensor': s, 'comp': comp, 'direction': direction,
            'z': z, 'effective_z': eff_z,
            'above': above, 'threshold': threshold,
            'excess_ratio': excess_ratio, 'grade': grade,
        })

    result.sort(key=lambda x: x['excess_ratio'], reverse=True)
    return result


# ════════════════════════════════════════════════════════════════════════════
# 매니저 블록 — 판단 중심 어휘, 수치 최소, 해석 없음
# ════════════════════════════════════════════════════════════════════════════

def _mgr_executive_summary(pkg: dict) -> dict | None:
    flags   = pkg['status_flags']
    hyps    = pkg['component_hypotheses']  # evidence_package z>=1.75
    err_cnt = pkg['error_context']['count']
    mid     = pkg['machine_id']
    ts      = pkg['timestamp'][:16]

    lines = [f"장비 {mid}  ·  이벤트 시각 {ts}"]

    if flags['insufficient_data']:
        lines.append("관측 데이터 부족으로 평가 불가.")
    elif not hyps:
        lines.append("모든 센서가 임계 미만. 이상 후보 없음.")
    else:
        n      = len(hyps)
        clist  = '·'.join(COMP_KO[h['component']] for h in hyps)
        plural = f"{n}건: " if n > 1 else ""
        lines.append(f"임계 초과 센서 감지. 점검 후보 {plural}{clist}.")

    if err_cnt > 0:
        lines.append(f"직전 24h 에러 이벤트 {err_cnt}건 선행.")
    elif hyps:
        lines.append("직전 24h 에러 이벤트: 없음.")

    mp = pkg.get('model_prediction', {})
    if mp.get('available'):
        alarm = mp.get('alarm_triggered')
        prob  = mp.get('probability', '?')
        thr   = mp.get('threshold', '?')
        status = "알람" if alarm else "알람 없음"
        lines.append(f"모델: {status}  (확률 {prob:.3f} / 임계 {thr:.3f})")

    source = ['machine_id', 'timestamp', 'status_flags',
              'component_hypotheses', 'error_context.count', 'model_prediction']
    return _block('executive_summary', '상황 요약', '\n'.join(lines), source)


def _mgr_prior_error_status(pkg: dict) -> dict | None:
    """
    선행 에러가 없을 때만 생성.
    억제 근거가 아니라 '드문 패턴' 이라는 사실 표기.
    전체 761건 중 15건(2.0%)만 해당 — 선행 경고 없이 발생하므로
    자동 에스컬레이션 기준이 적용되지 않는 상태임을 명시.
    """
    if not pkg['status_flags']['no_prior_error']:
        return None
    if not pkg['component_hypotheses']:
        return None  # 이상도 에러도 없음 → 정상

    text = (
        "직전 24h 에러 이벤트: 없음\n"
        "전체 고장 761건 중 15건(2.0%)이 선행 경고 없이 발생한 사례입니다.\n"
        "표준 에스컬레이션 기준이 자동 적용되지 않는 상태입니다."
    )
    source = ['status_flags.no_prior_error', 'error_context.count', 'component_hypotheses']
    return _block('prior_error_status', '선행 경고 없음', text, source)


def _mgr_error_conversion_risk(pkg: dict) -> dict | None:
    errors = pkg['error_context']['errors']
    if not errors:
        return None

    lines  = []
    source = []
    for e in errors:
        eid   = e['errorID']
        rate  = e['failure_conversion_rate_24h']
        total = e['basis']['total_occurrences_all_time']
        conv  = e['basis']['converted_to_failure_24h']

        if rate is None or total == 0:
            lines.append(f"• {eid}: 전환율 산출 근거 없음")
            continue

        pct = round(rate * 100, 1)
        lines.append(f"• {eid}: 전체 {total:,}건 중 24h 내 고장 전환 {conv:,}건 ({pct}%)")
        source += [
            f"error_context.errors[{eid}].failure_conversion_rate_24h",
            f"error_context.errors[{eid}].basis.total_occurrences_all_time",
            f"error_context.errors[{eid}].basis.converted_to_failure_24h",
        ]

    if not lines:
        return _no_data('error_conversion_risk', '에러 유형별 고장 전환 이력', '전환율 근거 없음')

    return _block('error_conversion_risk', '에러 유형별 고장 전환 이력',
                  '\n'.join(lines), source)


def _mgr_component_candidates(pkg: dict) -> dict | None:
    """
    전 부품 후보 — 임계 대비 초과 배수 순, 임계 미달 포함.
    excess_ratio = effective_z / comp_threshold 로 정렬
    (센서별 z-scale이 달라 z 직접 비교 공정하지 않음).
    """
    if pkg['status_flags']['insufficient_data']:
        return _no_data('component_candidates', '부품 이상 후보', '데이터 불충분')

    cands = _all_candidates(pkg)
    lines = ["임계 대비 초과 배수 순 (부품별 임계 상이):"]

    for c in cands:
        comp_ko   = COMP_KO[c['comp']]
        sensor_ko = SENSOR_KO[c['sensor']]
        z   = c['z']
        thr = c['threshold']
        er  = c['excess_ratio']

        if z is None:
            lines.append(f"  {comp_ko} / {sensor_ko}: N/A  [데이터 없음]")
        else:
            gr   = c['grade']
            lines.append(
                f"  [{gr}] {comp_ko} / {sensor_ko}: z={z:+.2f}  {er:.2f}×  (임계 {thr:.2f})"
            )

    lines.append(f"  (알람≥1.0× / 관찰0.8~1.0× / 정상<0.8×)")
    source = [f"sensor_evidence.sensors.{s}.z_score" for s in _SENSOR_ORDER]
    return _block('component_candidates', '부품 이상 후보', '\n'.join(lines), source)


def _mgr_maintenance_status(pkg: dict) -> dict | None:
    maint  = pkg['maintenance_context']
    lines  = []
    source = []

    for comp, info in maint.items():
        comp_ko = COMP_KO[comp]
        if info['last_replacement'] is None:
            lines.append(f"• {comp_ko}: 교체 이력 없음")
            source.append(f"maintenance_context.{comp}.last_replacement")
            continue

        dt    = info['last_replacement'][:10]
        days  = info['days_elapsed']
        mtype = '사후(reactive)' if info['type'] == 'reactive' else '예방(preventive)'
        lines.append(f"• {comp_ko}: {dt} 교체 / {days:.0f}일 경과 / {mtype}")
        source += [
            f"maintenance_context.{comp}.last_replacement",
            f"maintenance_context.{comp}.days_elapsed",
            f"maintenance_context.{comp}.type",
        ]

    if not lines:
        return None

    return _block('maintenance_status', '부품별 교체 현황', '\n'.join(lines), source)


def _mgr_data_quality(pkg: dict) -> dict | None:
    if not pkg['status_flags']['insufficient_data']:
        return None
    rows = pkg['sensor_evidence']['window_rows']
    text = f"24h 창 내 유효 데이터 {rows}행 (최소 기준 12행 미달). 신뢰도 낮음."
    return _block('data_quality', '데이터 품질 경고', text,
                  ['status_flags.insufficient_data', 'sensor_evidence.window_rows'])


# ════════════════════════════════════════════════════════════════════════════
# 엔지니어 블록 — 현상 중심, 수치 직접 제시
# ════════════════════════════════════════════════════════════════════════════

def _eng_model_prediction(pkg: dict) -> dict | None:
    """모델 판정 상세 — 사실만. 해석 없음."""
    mp = pkg.get('model_prediction', {})
    if not mp.get('available'):
        return None

    prob  = mp['probability']
    thr   = mp['threshold']
    alarm = mp['alarm_triggered']
    fc    = mp.get('feature_contribution') or {}

    status = "알람 발생" if alarm else "알람 없음"
    lines  = [f"모델 판정: {status}  (확률 {prob:.4f} / 임계 {thr:.4f})"]

    sensor_pct = fc.get('sensor_pct', {})
    valid_pcts = {k: v for k, v in sensor_pct.items() if v is not None and v > 0}
    if valid_pcts:
        lines.append(f"센서별 기여도 ({fc.get('method', 'global_imp×magnitude')})")
        for s in _SENSOR_ORDER:
            pct = valid_pcts.get(s)
            if pct:
                lines.append(f"  {SENSOR_KO[s]}: {pct:.1f}%")

    source = [
        'model_prediction.probability', 'model_prediction.threshold',
        'model_prediction.alarm_triggered', 'model_prediction.feature_contribution',
    ]
    return _block('model_prediction', '모델 판정 상세', '\n'.join(lines), source)


def _eng_sensor_deviation(pkg: dict) -> dict | None:
    sensors  = pkg['sensor_evidence']['sensors']
    win_rows = pkg['sensor_evidence']['window_rows']

    if win_rows == 0:
        return _no_data('sensor_deviation', '센서 편차 현황', '창 내 데이터 없음')

    lines  = [f"기준: 전 장비·전 기간 글로벌 baseline  (창 내 {win_rows}행)"]
    source = ['sensor_evidence.reference_frame', 'sensor_evidence.window_rows']

    for s in _SENSOR_ORDER:
        z = sensors[s].get('z_score')
        if z is None:
            lines.append(f"  {SENSOR_KO[s]:4s}: z=N/A")
            continue
        flag = _z_flag(z, _SENSOR_DIRECTION[s], s)
        lines.append(f"  {SENSOR_KO[s]:4s}: z={z:+.3f}  [{flag}]")
        source.append(f"sensor_evidence.sensors.{s}.z_score")

    return _block('sensor_deviation', '센서 편차 현황', '\n'.join(lines), source)


def _eng_peer_comparison(pkg: dict) -> dict | None:
    pc = pkg['peer_comparison']

    if 'error' in pc:
        return _no_data('peer_comparison', '동급 장비 비교', pc['error'])

    peer_count = pc.get('peer_count', 0)
    if peer_count == 0:
        return _no_data('peer_comparison', '동급 장비 비교', '비교 가능한 동급 장비 없음')

    basis     = pc.get('basis', {})
    model_str = basis.get('model', '?')
    age_range = basis.get('age_range', '?')

    lines  = [f"동급: {model_str} / 연령 {age_range}년 / 비교 대상 {peer_count}대"]
    source = ['peer_comparison.peer_count', 'peer_comparison.basis']

    for s in _SENSOR_ORDER:
        pdata = pc['percentile_by_sensor'].get(s)
        if pdata is None:
            lines.append(f"  {SENSOR_KO[s]:4s}: 비교 데이터 없음")
            continue
        pct     = pdata['percentile']
        tgt_z   = pdata['target_z']
        peers_n = pdata['peers_with_data']
        label   = _pct_label(pct)
        lines.append(
            f"  {SENSOR_KO[s]:4s}: {peers_n}대 중 백분위 {pct:.1f}% ({label})"
            f"  자기 z={tgt_z:+.3f}"
        )
        source += [
            f"peer_comparison.percentile_by_sensor.{s}.percentile",
            f"peer_comparison.percentile_by_sensor.{s}.peers_with_data",
        ]

    return _block('peer_comparison', '동급 장비 비교', '\n'.join(lines), source)


def _eng_component_candidates(pkg: dict) -> dict | None:
    """
    전 센서-부품 쌍 — excess_ratio 순, 임계 미달 포함.
    방향 제약, z 값, 부품별 임계, 초과 배수를 함께 표기.
    """
    if pkg['status_flags']['insufficient_data']:
        return _no_data('component_candidates', '부품 이상 후보', '데이터 불충분')

    cands   = _all_candidates(pkg)
    n_above = sum(1 for c in cands if c['above'])

    if n_above == 0:
        header = "임계 초과 항목 없음 (부품별 임계 상이). 전 항목 열거:"
    elif n_above > 1:
        header = f"임계 초과 {n_above}건 (복수 후보 — 단일 확정 불가):"
    else:
        header = "임계 초과 1건:"

    lines = [header]
    for c in cands:
        comp_ko   = COMP_KO[c['comp']]
        sensor_ko = SENSOR_KO[c['sensor']]
        direction = c['direction']
        z   = c['z']
        thr = c['threshold']
        er  = c['excess_ratio']
        dir_ko = '양방향' if direction == 'both' else '하락 방향만'

        if z is None:
            lines.append(f"  {comp_ko} ({c['comp']}): {sensor_ko} z=N/A  [{dir_ko}]")
        else:
            gr  = c['grade']
            lines.append(
                f"  [{gr}] {comp_ko} ({c['comp']}): {sensor_ko} z={z:+.3f}  "
                f"{er:.3f}×  임계 {thr:.2f}  [{dir_ko}]"
            )

    lines.append(f"  (알람≥1.0× / 관찰0.8~1.0× / 정상<0.8× | 정렬: excess_ratio 내림차순)")
    source = [f"sensor_evidence.sensors.{s}.z_score" for s in _SENSOR_ORDER]
    return _block('component_candidates', '부품 이상 후보', '\n'.join(lines), source)


def _eng_prior_error_status(pkg: dict) -> dict | None:
    """선행 에러 없을 때만. 엔지니어용 — 기술적 표기."""
    if not pkg['status_flags']['no_prior_error']:
        return None
    if not pkg['component_hypotheses']:
        return None

    hyps       = pkg['component_hypotheses']
    sensor_str = '·'.join(SENSOR_KO[h['associated_sensor']] for h in hyps)
    text = (
        f"직전 24h 에러 로그: 없음  ({sensor_str} 임계 초과 있음)\n"
        "전체 고장 761건 중 15건(2.0%)이 선행 에러 없이 발생한 사례입니다.\n"
        "물리 점검 및 센서 교정 상태를 함께 확인하십시오."
    )
    source = ['status_flags.no_prior_error', 'error_context.count',
              'component_hypotheses[].associated_sensor']
    return _block('prior_error_status', '선행 에러 없는 이상', text, source)


def _eng_error_events(pkg: dict) -> dict | None:
    errors = pkg['error_context']['errors']
    count  = pkg['error_context']['count']

    if count == 0:
        return _block('error_events', '직전 24h 에러 이벤트',
                      '에러 이벤트 없음.', ['error_context.count'])

    lines  = [f"에러 이벤트 {count}건:"]
    source = ['error_context.count']

    for e in errors:
        eid   = e['errorID']
        hhmm  = e['datetime'][11:16]
        rate  = e['failure_conversion_rate_24h']
        total = e['basis']['total_occurrences_all_time']
        conv  = e['basis']['converted_to_failure_24h']

        if rate is not None and total > 0:
            pct      = round(rate * 100, 1)
            conv_str = f"  전환율 {total:,}건 중 {conv:,}건({pct}%)"
        else:
            conv_str = "  전환율 근거 없음"

        lines.append(f"  • {eid} @ {hhmm}{conv_str}")
        source += [
            f"error_context.errors[].errorID",
            f"error_context.errors[].failure_conversion_rate_24h",
        ]

    return _block('error_events', '직전 24h 에러 이벤트', '\n'.join(lines), source)


def _eng_maintenance_detail(pkg: dict) -> dict | None:
    maint  = pkg['maintenance_context']
    lines  = []
    source = []

    for comp, info in maint.items():
        comp_ko = COMP_KO[comp]

        if info['last_replacement'] is None:
            lines.append(f"• {comp_ko}: 이력 없음")
            source.append(f"maintenance_context.{comp}.last_replacement")
            continue

        dt    = info['last_replacement'][:10]
        days  = info['days_elapsed']
        mtype = '사후(reactive)' if info['type'] == 'reactive' else '예방(preventive)'
        basis = info['basis']

        reactive_note = ''
        if isinstance(basis, dict) and basis.get('failure_within_24h_before_maint'):
            reactive_note = '  ← 교체 전 24h 내 고장 기록'

        lines.append(
            f"• {comp_ko}: {dt} / {days:.0f}일 경과 / {mtype}{reactive_note}"
        )
        source += [
            f"maintenance_context.{comp}.last_replacement",
            f"maintenance_context.{comp}.days_elapsed",
            f"maintenance_context.{comp}.type",
            f"maintenance_context.{comp}.basis.failure_within_24h_before_maint",
        ]

    if not lines:
        return None

    return _block('maintenance_detail', '부품별 교체 이력', '\n'.join(lines), source)


def _eng_data_quality(pkg: dict) -> dict | None:
    if not pkg['status_flags']['insufficient_data']:
        return None
    rows = pkg['sensor_evidence']['window_rows']
    text = (
        f"24h 창 내 수집 데이터: {rows}행 (최소 기준 12행 미달).\n"
        "z-score·백분위 값 신뢰도 낮음. 텔레메트리 수집 상태 확인 필요."
    )
    return _block('data_quality', '데이터 불충분 경고', text,
                  ['status_flags.insufficient_data', 'sensor_evidence.window_rows'])


# ════════════════════════════════════════════════════════════════════════════
# 파이프라인 — 함수 순서 = 블록 출력 순서
# ════════════════════════════════════════════════════════════════════════════

_MANAGER_PIPELINE = [
    _mgr_executive_summary,       # 1. 상황 요약 (알람 포함)
    _mgr_prior_error_status,      # 2. 선행 경고 없음 (해당 시만)
    _mgr_error_conversion_risk,   # 3. 에러 전환 이력
    _mgr_component_candidates,    # 4. 부품 이상 후보 (z순, 임계 미달 포함)
    _mgr_maintenance_status,      # 5. 교체 현황
    _mgr_data_quality,            # 6. 데이터 품질 (해당 시만)
]

_ENGINEER_PIPELINE = [
    _eng_model_prediction,        # 1. 모델 판정 상세
    _eng_sensor_deviation,        # 2. 센서 편차 (z-score)
    _eng_peer_comparison,         # 3. 동급 장비 비교
    _eng_component_candidates,    # 4. 부품 이상 후보 (z순, 임계 미달 포함)
    _eng_prior_error_status,      # 5. 선행 에러 없음 (해당 시만)
    _eng_error_events,            # 6. 에러 이벤트 상세
    _eng_maintenance_detail,      # 7. 교체 이력 상세
    _eng_data_quality,            # 8. 데이터 불충분 (해당 시만)
]


# ── 공개 API ──────────────────────────────────────────────────────────────────

def generate_role_reports(pkg: dict) -> dict:
    """
    Evidence Package → 매니저·엔지니어 리포트.
    억제 로직 없음. 사실 제시만.
    """
    manager_blocks  = [b for fn in _MANAGER_PIPELINE  if (b := fn(pkg)) is not None]
    engineer_blocks = [b for fn in _ENGINEER_PIPELINE if (b := fn(pkg)) is not None]

    return {
        'machine_id': pkg['machine_id'],
        'timestamp':  pkg['timestamp'],
        'manager':    manager_blocks,
        'engineer':   engineer_blocks,
    }
