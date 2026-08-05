"""
역할별 리포트 생성기 — LLM 없음, 템플릿과 규칙만 사용.

입력:  Evidence Package dict (generate_evidence_package() 출력)
출력:  {"machine_id": int, "timestamp": str,
        "manager": [블록, ...], "engineer": [블록, ...]}

각 블록: {
    "type":          str,
    "title":         str,
    "text":          str,
    "source_fields": [str, ...]   # Evidence Package 경로
}

표현 제약 (전 블록 공통):
  - 인과 표현 금지: "원인" → "연관", "후보"
  - 물리 단위 금지: z-score / 백분위만
  - 모든 비율에 분모 병기: "N건 중 M건(X%)"
  - 근거 없는 값은 문장 생성 불가 → [근거 부족: ...] 표기 또는 블록 생략
"""

from __future__ import annotations

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

# 각 센서의 임계 방향 (evidence_package.py SENSOR_COMP_MAP과 동기)
_SENSOR_DIRECTION: dict[str, str] = {
    'volt':      'both',
    'rotate':    'negative',
    'pressure':  'both',
    'vibration': 'both',
}

_SENSOR_ORDER = ['volt', 'rotate', 'pressure', 'vibration']

# 센서 → 연관 부품 (evidence_package.py SENSOR_COMP_MAP과 동기)
_SENSOR_TO_COMP: dict[str, str] = {
    'volt':      'comp1',
    'rotate':    'comp2',
    'pressure':  'comp3',
    'vibration': 'comp4',
}


# ── 보조 함수 ─────────────────────────────────────────────────────────────────

def _pct_label(pct: float) -> str:
    """백분위 → 매니저용 간결 레이블."""
    if pct >= 95:
        return '동급 최상위 5% 이내'
    if pct >= 90:
        return '동급 상위 10% 이내'
    if pct >= 75:
        return '동급 상위 25% 이내'
    if pct <= 5:
        return '동급 최하위 5% 이내'
    if pct <= 10:
        return '동급 하위 10% 이내'
    if pct <= 25:
        return '동급 하위 25% 이내'
    return '동급 중간 범위'


def _z_flag(z: float, direction: str) -> str:
    """엔지니어용 임계 초과 표시."""
    if direction == 'negative':
        return '↓ 초과' if z <= -2.0 else '정상'
    return '↑ 초과' if z >= 2.0 else ('↓ 초과' if z <= -2.0 else '정상')


def _block(type_: str, title: str, text: str, source_fields: list[str]) -> dict:
    return {'type': type_, 'title': title, 'text': text,
            'source_fields': source_fields}


def _no_data(type_: str, title: str, reason: str) -> dict:
    return {'type': type_, 'title': title,
            'text': f'[근거 부족: {reason}]', 'source_fields': []}


# ════════════════════════════════════════════════════════════════════════════
# 매니저 블록 함수
# 목표: 판단과 영향 중심 / 센서 수치 최소화 / 과거 실적 비율 우선
# ════════════════════════════════════════════════════════════════════════════

def _mgr_executive_summary(pkg: dict) -> dict | None:
    """상황 요약 — 센서 z-score 미노출, 판단 언어."""
    flags   = pkg['status_flags']
    hyps    = pkg['component_hypotheses']
    err_cnt = pkg['error_context']['count']
    mid     = pkg['machine_id']
    ts      = pkg['timestamp'][:16]

    lines = [f"장비 {mid}  ·  이벤트 시각 {ts}"]

    if flags['insufficient_data']:
        lines.append("관측 데이터 부족으로 정상 평가가 불가능합니다.")
    elif not hyps:
        lines.append("모든 센서가 기준 범위 내에 있습니다. 현재 이상 후보 없음.")
    else:
        n = len(hyps)
        comp_list = '·'.join(COMP_KO[h['component']] for h in hyps)
        if n == 1:
            lines.append(f"센서 이상 패턴이 감지되었습니다. 점검 후보: {comp_list}.")
        else:
            lines.append(
                f"복수 센서에서 이상 패턴이 동시 감지되었습니다.\n"
                f"점검 후보 {n}건: {comp_list}."
            )

    if err_cnt > 0:
        lines.append(f"직전 24h 내 에러 이벤트 {err_cnt}건 선행.")
    elif hyps:
        lines.append("직전 24h 에러 이벤트 없음 — 무경고 발생.")

    source = [
        'machine_id', 'timestamp',
        'status_flags', 'component_hypotheses',
        'error_context.count',
    ]
    return _block('executive_summary', '상황 요약', '\n'.join(lines), source)


def _mgr_no_prior_warning(pkg: dict) -> dict | None:
    """선행 경고 없이 감지 — 별도 블록, 주의 강조.
    이상이 없는 정상 구간에서는 생성하지 않는다."""
    if not pkg['status_flags']['no_prior_error']:
        return None
    if not pkg['component_hypotheses']:
        return None

    text = (
        "직전 24h 에러 이벤트가 없는 상태에서 센서 이상이 감지되었습니다.\n"
        "에러→고장 전환 패턴에 해당하지 않으므로 표준 에스컬레이션 기준이\n"
        "자동 적용되지 않습니다. 현장 점검 지시가 필요합니다."
    )
    source = [
        'status_flags.no_prior_error',
        'error_context.count',
        'component_hypotheses',
    ]
    return _block('no_prior_warning', '⚠ 선행 경고 없이 감지됨', text, source)


def _mgr_error_conversion_risk(pkg: dict) -> dict | None:
    """에러 유형별 고장 전환 이력 — 분모 병기, 판단 근거 제공."""
    errors = pkg['error_context']['errors']
    if not errors:
        return None

    lines = []
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
        risk = '높음' if rate >= 0.3 else ('중간' if rate >= 0.1 else '낮음')
        lines.append(
            f"• {eid}: 전체 {total:,}건 중 24h 내 고장 전환 {conv:,}건 ({pct}%) — 위험도 {risk}"
        )
        source += [
            f"error_context.errors[{eid}].failure_conversion_rate_24h",
            f"error_context.errors[{eid}].basis.total_occurrences_all_time",
            f"error_context.errors[{eid}].basis.converted_to_failure_24h",
        ]

    if not lines:
        return _no_data('error_conversion_risk', '에러 유형별 고장 전환 이력',
                        '전환율 산출 근거 없음')

    return _block('error_conversion_risk', '에러 유형별 고장 전환 이력',
                  '\n'.join(lines), source)


def _mgr_component_risk(pkg: dict) -> dict | None:
    """부품 이상 후보 — 단일 확정 금지, 영향 언어."""
    hyps  = pkg['component_hypotheses']
    flags = pkg['status_flags']

    if not hyps:
        return None

    lines = []
    for h in hyps:
        comp_ko   = COMP_KO[h['component']]
        sensor_ko = SENSOR_KO[h['associated_sensor']]
        z         = h['sensor_z_score']
        # 상대 크기만 언급 — 절댓값 노출 금지
        magnitude = '현저한' if abs(z) >= 3.0 else '유의한'
        lines.append(f"• {comp_ko}: {sensor_ko} {magnitude} 편차와 연관된 점검 후보")

    if flags['multiple_candidates']:
        lines.append(
            f"\n※ 복수 후보 {len(hyps)}건 — 단일 부품으로 확정할 수 없음.\n"
            "   점검 범위를 전 후보 부품으로 확장하여 진행하십시오."
        )

    source = [
        'component_hypotheses[].component',
        'component_hypotheses[].associated_sensor',
        'component_hypotheses[].sensor_z_score',
        'status_flags.multiple_candidates',
    ]
    return _block('component_risk', '부품 이상 후보', '\n'.join(lines), source)


def _mgr_maintenance_status(pkg: dict) -> dict | None:
    """교체 이력 — 판단 중심 (reactive 강조, 일수 + 유형)."""
    maint = pkg['maintenance_context']
    lines = []
    source = []

    for comp, info in maint.items():
        comp_ko = COMP_KO[comp]
        if info['last_replacement'] is None:
            lines.append(f"• {comp_ko}: 교체 이력 없음")
            source.append(f"maintenance_context.{comp}.last_replacement")
            continue

        dt    = info['last_replacement'][:10]
        days  = info['days_elapsed']
        mtype = info['type']

        mtype_ko = '사후(reactive) 교체' if mtype == 'reactive' else '예방(preventive) 교체'
        lines.append(f"• {comp_ko}: 최근 {dt} ({days:.0f}일 경과) — {mtype_ko}")
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
    text = (
        f"24h 창 내 유효 데이터 {rows}행 (최소 기준 12행 미달).\n"
        "이 리포트의 신뢰도가 낮습니다. 텔레메트리 수집 상태를 확인하십시오."
    )
    return _block('data_quality', '데이터 품질 경고', text,
                  ['status_flags.insufficient_data', 'sensor_evidence.window_rows'])


# ════════════════════════════════════════════════════════════════════════════
# 엔지니어 블록 함수
# 목표: 현상·점검 대상 중심 / 센서 편차·부품 후보 우선 / 형제 장비 비교 포함
# ════════════════════════════════════════════════════════════════════════════

def _eng_sensor_deviation(pkg: dict) -> dict | None:
    """센서별 z-score + 임계 초과 여부 — 엔지니어 우선 블록."""
    sensors  = pkg['sensor_evidence']['sensors']
    win_rows = pkg['sensor_evidence']['window_rows']

    if win_rows == 0:
        return _no_data('sensor_deviation', '센서 편차 현황', '창 내 데이터 없음')

    lines  = [f"기준: 전 장비·전 기간 글로벌 baseline (창 내 {win_rows}행)"]
    source = ['sensor_evidence.reference_frame', 'sensor_evidence.window_rows']

    for s in _SENSOR_ORDER:
        data = sensors[s]
        z    = data.get('z_score')
        if z is None:
            lines.append(f"  {SENSOR_KO[s]:4s}: z-score 계산 불가 (σ=0)")
            continue
        flag = _z_flag(z, _SENSOR_DIRECTION[s])
        lines.append(f"  {SENSOR_KO[s]:4s}: z={z:+.3f}  [{flag}]")
        source.append(f"sensor_evidence.sensors.{s}.z_score")

    return _block('sensor_deviation', '센서 편차 현황', '\n'.join(lines), source)


def _eng_peer_comparison(pkg: dict) -> dict | None:
    """형제 장비 대비 백분위 — 엔지니어 두 번째 블록."""
    pc = pkg['peer_comparison']

    if 'error' in pc:
        return _no_data('peer_comparison', '동급 장비 비교', pc['error'])

    peer_count = pc.get('peer_count', 0)
    if peer_count == 0:
        return _no_data('peer_comparison', '동급 장비 비교', '비교 가능한 동급 장비 없음')

    basis     = pc.get('basis', {})
    model     = basis.get('model', '?')
    age_range = basis.get('age_range', '?')

    lines  = [f"동급: {model} / 연령 {age_range}년 / 비교 대상 {peer_count}대"]
    source = ['peer_comparison.peer_count', 'peer_comparison.basis']

    for s in _SENSOR_ORDER:
        pdata = pc['percentile_by_sensor'].get(s)
        if pdata is None:
            lines.append(f"  {SENSOR_KO[s]:4s}: 비교 데이터 없음")
            continue
        pct      = pdata['percentile']
        tgt_z    = pdata['target_z']
        peers_n  = pdata['peers_with_data']
        label    = _pct_label(pct)
        lines.append(
            f"  {SENSOR_KO[s]:4s}: {peers_n}대 중 백분위 {pct:.1f}% ({label})"
            f"  자기 z={tgt_z:+.3f}"
        )
        source += [
            f"peer_comparison.percentile_by_sensor.{s}.percentile",
            f"peer_comparison.percentile_by_sensor.{s}.peers_with_data",
            f"peer_comparison.percentile_by_sensor.{s}.target_z",
        ]

    return _block('peer_comparison', '동급 장비 비교', '\n'.join(lines), source)


def _eng_component_hypotheses(pkg: dict) -> dict | None:
    """부품-센서 연관 후보 상세 목록."""
    hyps  = pkg['component_hypotheses']
    flags = pkg['status_flags']

    if not hyps:
        return _block(
            'component_hypotheses', '부품 이상 후보',
            '임계 초과 센서 없음 — 부품 점검 후보 해당 없음.',
            ['component_hypotheses'],
        )

    if flags['multiple_candidates']:
        lines = [f"복수 후보 {len(hyps)}건 — 단일 부품 확정 불가:"]
    else:
        lines = ['이상 연관 후보:']

    for h in hyps:
        comp_ko   = COMP_KO[h['component']]
        sensor_ko = SENSOR_KO[h['associated_sensor']]
        z         = h['sensor_z_score']
        thr       = h['z_threshold']
        dir_ko    = '양방향' if h['direction'] == 'both' else '하락 방향만'
        lines.append(
            f"  → {comp_ko}: {sensor_ko} z={z:+.3f}"
            f"  (임계 {thr}, {dir_ko})"
        )

    source = [
        'component_hypotheses[].component',
        'component_hypotheses[].associated_sensor',
        'component_hypotheses[].sensor_z_score',
        'component_hypotheses[].z_threshold',
        'component_hypotheses[].direction',
        'status_flags.multiple_candidates',
    ]
    return _block('component_hypotheses', '부품 이상 후보', '\n'.join(lines), source)


def _eng_no_prior_warning(pkg: dict) -> dict | None:
    """선행 에러 없는 센서 이상 — 기술적 주의, 엔지니어용."""
    if not pkg['status_flags']['no_prior_error']:
        return None
    if not pkg['component_hypotheses']:
        return None

    hyps       = pkg['component_hypotheses']
    sensor_str = '·'.join(
        SENSOR_KO[h['associated_sensor']] for h in hyps
    )
    text = (
        f"직전 24h 에러 로그 없음. {sensor_str} 임계 초과가 에러 이벤트 없이 발생.\n"
        "간헐적 하드웨어 결함 또는 센서 자체 오류 가능성을 포함합니다.\n"
        "물리 점검과 함께 센서 교정 이력을 확인하십시오."
    )
    source = [
        'status_flags.no_prior_error',
        'error_context.count',
        'component_hypotheses[].associated_sensor',
    ]
    return _block('no_prior_warning', '선행 에러 없는 이상 (주의)', text, source)


def _eng_error_events(pkg: dict) -> dict | None:
    """에러 이벤트 상세 + 전환율."""
    errors = pkg['error_context']['errors']
    count  = pkg['error_context']['count']

    if count == 0:
        return _block(
            'error_events', '직전 24h 에러 이벤트',
            '에러 이벤트 없음.',
            ['error_context.count'],
        )

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
            conv_str = f"  ·  전환율 {total:,}건 중 {conv:,}건({pct}%)"
        else:
            conv_str = "  ·  전환율 근거 없음"

        lines.append(f"  • {eid} @ {hhmm}{conv_str}")
        source += [
            f"error_context.errors[].errorID",
            f"error_context.errors[].failure_conversion_rate_24h",
            f"error_context.errors[].basis.total_occurrences_all_time",
        ]

    return _block('error_events', '직전 24h 에러 이벤트', '\n'.join(lines), source)


def _eng_maintenance_detail(pkg: dict) -> dict | None:
    """부품별 교체 이력 상세."""
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
        mtype = info['type']
        basis = info['basis']

        mtype_ko = '사후(reactive)' if mtype == 'reactive' else '예방(preventive)'
        reactive_note = ''
        if isinstance(basis, dict) and basis.get('failure_within_24h_before_maint'):
            reactive_note = '  ← 교체 전 24h 내 고장 기록 있음'

        lines.append(
            f"• {comp_ko}: {dt} 교체 / {days:.0f}일 경과 / {mtype_ko}{reactive_note}"
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
        "z-score·백분위 값의 신뢰도가 낮습니다. "
        "텔레메트리 수집 파이프라인을 확인하십시오."
    )
    return _block('data_quality', '데이터 불충분 경고', text,
                  ['status_flags.insufficient_data', 'sensor_evidence.window_rows'])


# ════════════════════════════════════════════════════════════════════════════
# 모델 알람 컨텍스트 블록 (model_prediction 필드가 있을 때만 활성화)
# ════════════════════════════════════════════════════════════════════════════

def _get_suppression_evidence(pkg: dict) -> list[dict]:
    """
    alarm_triggered=True일 때 알람 긴급도를 낮춰볼 근거 목록.
    각 항목: {'rule': str, 'text': str, 'strength': int}
    strength: 1=약, 2=강

    규칙 (v2):
      S1. no_prior_error=True                  → strength 1
      S2. 이상 센서의 연관 부품이 30일 내 교체   → strength 2
          (비연관 부품 교체는 억제 근거 아님)
      S3. 이상이 단일 센서에만 국한              → strength 1
      S4. 이상 센서 동급 백분위 < 75%            → strength 1
          단, |z| ≥ 3.0이면 S4 무효
          (절대 편차가 극단적이면 동급 비교로 억제 불가)
      S5. 예측 확률이 임계 근접 (< threshold+0.10) → strength 1

    없는 근거로 억제를 정당화하지 않는다.
    """
    mp = pkg.get('model_prediction', {})
    if not mp.get('available') or not mp.get('alarm_triggered'):
        return []

    prob  = mp['probability']
    thr   = mp['threshold']
    flags = pkg['status_flags']
    maint = pkg['maintenance_context']
    hyps  = pkg['component_hypotheses']
    pc    = pkg['peer_comparison']

    evidence: list[dict] = []

    # 이상 센서와 연관된 부품 집합
    anomalous_comps = {_SENSOR_TO_COMP[h['associated_sensor']] for h in hyps}

    # S1: 선행 에러 없음
    if flags['no_prior_error']:
        evidence.append({
            'rule': 'S1', 'strength': 1,
            'text': "[S1] 직전 24h 에러 이벤트 없음 — 에러→고장 전환 패턴 아님",
        })

    # S2: 이상 센서 연관 부품의 최근 교체 (매칭 전용, strength 2)
    for comp, info in maint.items():
        if comp not in anomalous_comps:
            continue  # 비연관 부품 → 억제 근거 아님
        days = info.get('days_elapsed')
        if days is not None and days < 30:
            comp_ko   = COMP_KO.get(comp, comp)
            sensor_ko = next(
                (SENSOR_KO[s] for s, c in _SENSOR_TO_COMP.items() if c == comp),
                comp,
            )
            evidence.append({
                'rule': 'S2', 'strength': 2,
                'text': (
                    f"[S2★] {comp_ko} 최근 교체 {days:.0f}일 경과 "
                    f"· 이상 센서({sensor_ko})와 연관 부품 일치 "
                    "→ 교체 후 안정화 가능성"
                ),
            })

    # S3: 단일 센서 이상
    if hyps and not flags['multiple_candidates']:
        evidence.append({
            'rule': 'S3', 'strength': 1,
            'text': "[S3] 이상 신호 단일 센서 국한 — 복수 부품 동시 이상 아님",
        })

    # S4: 동급 백분위 < 75% (단, |z| < 3.0일 때만)
    pct_by_sensor = pc.get('percentile_by_sensor', {})
    for h in hyps:
        s   = h['associated_sensor']
        z   = h['sensor_z_score']
        if abs(z) >= 3.0:
            continue  # 극단적 절대 편차 → 동급 비교로 억제 불가
        pdata = pct_by_sensor.get(s)
        if pdata and pdata['percentile'] < 75:
            sensor_ko = SENSOR_KO.get(s, s)
            pct       = pdata['percentile']
            peers_n   = pdata['peers_with_data']
            evidence.append({
                'rule': 'S4', 'strength': 1,
                'text': (
                    f"[S4] {sensor_ko} 동급 백분위 {pct:.1f}% ({peers_n}대, z={z:+.2f}) "
                    "— 동급 대비 상위 이탈 아님"
                ),
            })

    # S5: 경계선 케이스
    if prob < thr + 0.10:
        margin = prob - thr
        evidence.append({
            'rule': 'S5', 'strength': 1,
            'text': (
                f"[S5] 예측 확률 {prob:.3f} / 임계 {thr:.3f} (여유 {margin:+.3f}) "
                "— 경계선 케이스"
            ),
        })

    return evidence


def _supp_total_strength(evidence: list[dict]) -> int:
    return sum(e['strength'] for e in evidence)


def _mgr_alarm_context(pkg: dict) -> dict | None:
    """매니저용 — 모델 알람 판정 + 억제 근거 강도 (알람 발생 시에만)."""
    mp = pkg.get('model_prediction', {})
    if not mp.get('available') or not mp.get('alarm_triggered'):
        return None

    prob       = mp['probability']
    thr        = mp['threshold']
    supp       = _get_suppression_evidence(pkg)
    total_str  = _supp_total_strength(supp)

    lines = [f"모델 예측 확률 {prob:.3f}  (임계 {thr:.3f})  →  알람 발생", ""]

    if supp:
        lines.append(f"알람 긴급도를 낮춰볼 근거  (억제 강도 합계: {total_str})")
        for e in supp:
            mark = "★★" if e['strength'] >= 2 else "·"
            lines.append(f"  {mark} {e['text']}")
    else:
        lines.append(
            "억제 근거 없음: 현 데이터로는 알람 긴급도를 낮출 규칙 기반 근거 없음.\n"
            f"모델 확률 {prob:.3f}를 참고하되 현장 확인이 필요함."
        )

    source = [
        'model_prediction.probability',
        'model_prediction.threshold',
        'model_prediction.alarm_triggered',
        'status_flags.no_prior_error',
        'maintenance_context',
        'peer_comparison.percentile_by_sensor',
    ]
    return _block('alarm_context', '모델 알람 및 억제 근거', '\n'.join(lines), source)


def _eng_alarm_context(pkg: dict) -> dict | None:
    """엔지니어용 — 모델 판정 상세 + 센서별 기여도 + 억제 신호 강도."""
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
        lines.append(f"센서별 기여도 ({fc.get('method', 'global_imp × magnitude')})")
        for s in _SENSOR_ORDER:
            pct = valid_pcts.get(s)
            if pct is not None:
                lines.append(f"  {SENSOR_KO[s]}: {pct:.1f}%")

    if alarm:
        supp      = _get_suppression_evidence(pkg)
        total_str = _supp_total_strength(supp)
        lines.append("")
        if supp:
            lines.append(f"억제 신호  (강도 합계: {total_str})")
            for e in supp:
                mark = "★★" if e['strength'] >= 2 else "·"
                lines.append(f"  {mark} {e['text']}")
        else:
            lines.append(
                "억제 신호 없음: 현 데이터로는 이 알람을 낮출 규칙 기반 근거 없음."
            )

    source = [
        'model_prediction.probability',
        'model_prediction.threshold',
        'model_prediction.alarm_triggered',
        'model_prediction.feature_contribution',
    ]
    return _block('alarm_context', '모델 예측 상세', '\n'.join(lines), source)


# ════════════════════════════════════════════════════════════════════════════
# 파이프라인 정의 — 함수 순서 = 블록 출력 순서
# ════════════════════════════════════════════════════════════════════════════

# 매니저: 판단·영향 우선 / 에러 전환율·교체 유형별 실적 선행 배치
_MANAGER_PIPELINE = [
    _mgr_executive_summary,      # 1. 상황 요약 (센서 수치 없음)
    _mgr_alarm_context,          # 2. 모델 알람 + 억제 근거 (알람 시만)
    _mgr_no_prior_warning,       # 3. 선행 경고 없음 경보 (해당 시만)
    _mgr_error_conversion_risk,  # 4. 에러 전환 이력 (분모 포함)
    _mgr_component_risk,         # 5. 부품 이상 후보 (단일 확정 금지)
    _mgr_maintenance_status,     # 6. 교체 현황
    _mgr_data_quality,           # 7. 데이터 품질 (해당 시만)
]

# 엔지니어: 센서 편차·부품 후보 우선 / 형제 장비 비교 포함
_ENGINEER_PIPELINE = [
    _eng_alarm_context,          # 1. 모델 예측 상세 (있을 때만)
    _eng_sensor_deviation,       # 2. 센서 편차 (z-score)
    _eng_peer_comparison,        # 3. 동급 장비 비교 (백분위)
    _eng_component_hypotheses,   # 4. 부품 이상 후보 (센서 연관)
    _eng_no_prior_warning,       # 5. 선행 에러 없는 이상 (해당 시만)
    _eng_error_events,           # 6. 에러 이벤트 상세
    _eng_maintenance_detail,     # 7. 교체 이력 상세
    _eng_data_quality,           # 8. 데이터 불충분 (해당 시만)
]


# ── 공개 API ──────────────────────────────────────────────────────────────────

def generate_role_reports(pkg: dict) -> dict:
    """
    Evidence Package 하나에서 매니저·엔지니어 리포트를 동시 생성.

    Args:
        pkg: generate_evidence_package() 의 반환값

    Returns:
        {"machine_id": int, "timestamp": str,
         "manager": [...], "engineer": [...]}
    """
    manager_blocks  = [b for fn in _MANAGER_PIPELINE  if (b := fn(pkg)) is not None]
    engineer_blocks = [b for fn in _ENGINEER_PIPELINE if (b := fn(pkg)) is not None]

    return {
        'machine_id': pkg['machine_id'],
        'timestamp':  pkg['timestamp'],
        'manager':    manager_blocks,
        'engineer':   engineer_blocks,
    }
