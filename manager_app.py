"""
매니저 대시보드 v3 — Azure PdM
매니저 결정 순서:
  ① 얼마나 급한가 → ② 나빠지고 있나 → ③ 얼마나 확실한가
  → ④ 미루면 뭐가 나빠지는가 → ⑤ 어느 부품인가

계산 로직: evidence_package.py / report_generator.py 호출만.
이 파일에 계산 로직 없음. LLM 없음.
"""

import sys
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import altair as alt

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from evidence_package import load_data, compute_global_baseline, generate_evidence_package
from report_generator import GRADE_ALARM, GRADE_WATCH, _all_candidates
from z_baseline import load_baseline, load_thresholds, DEFAULT_BASELINE_PATH, DEFAULT_THRESHOLD_PATH
from display_names import COMP_DISPLAY, COMP_SHORT, ERROR_DISPLAY

# ────────────────────────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────────────────────────

SENSORS          = ['volt', 'rotate', 'pressure', 'vibration']
SENSOR_COMP      = {'volt': 'comp1', 'rotate': 'comp2', 'pressure': 'comp3', 'vibration': 'comp4'}
COMP_SENSOR      = {'comp1': 'volt', 'comp2': 'rotate', 'comp3': 'pressure', 'comp4': 'vibration'}
SENSOR_DIRECTION = {'volt': 'both', 'rotate': 'negative', 'pressure': 'both', 'vibration': 'both'}

GRADE_COLOR  = {'알람': '#c53030', '관찰': '#b7791f', '정상': '#276749'}
GRADE_BG     = {'알람': '#fff5f5', '관찰': '#fffbeb', '정상': '#f0fff4'}
GRADE_BORDER = {'알람': '#fc8181', '관찰': '#f6ad55', '정상': '#68d391'}

# 화면 표시명 — 내부 코드는 알람/관찰/정상 유지, 표시만 교체
GRADE_LABEL  = {'알람': '즉시 교체 필요', '관찰': '관찰 필요', '정상': '정상'}

# 등급별 과거 24시간 내 고장 발생 비율 (evaluate_metrics.py 검증 결과)
GRADE_FAILURE_RATE = {'알람': 24.0, '관찰': 15.7, '정상': 0.01}

# 추이 판정 기준 (화면에도 표시)
# 최근 24시간 평균이 직전 24시간 평균 대비 이 %를 넘으면 "상승 중"으로 표기
TREND_CHANGE_THRESHOLD_PCT = 20

# ────────────────────────────────────────────────────────────────────────────
# 데이터 로딩 (캐시)
# ────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _load_raw_data():
    return load_data(ROOT / 'archive')

@st.cache_data(show_spinner=False)
def _load_feat_df():
    p = ROOT / 'cache' / 'feat_df.pkl'
    if not p.exists():
        return None
    with open(p, 'rb') as f:
        return pickle.load(f)

@st.cache_data(show_spinner=False)
def _load_baseline_dict():
    return load_baseline(DEFAULT_BASELINE_PATH)

@st.cache_data(show_spinner=False)
def _load_baseline_df():
    return compute_global_baseline()

@st.cache_data(show_spinner=False)
def _load_thresholds():
    return load_thresholds(DEFAULT_THRESHOLD_PATH)

# ────────────────────────────────────────────────────────────────────────────
# 정비 이력 통계 — 교체 유형별 다음 교체까지 경과일 중앙값
# ────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def compute_maint_interval_stats(_maint_df: pd.DataFrame, _fails_df: pd.DataFrame) -> dict:
    """
    전체 정비 이력에서 교체 유형별 '다음 교체까지 경과일' 중앙값 산출.

    교체 유형 판정:
      reactive   = 해당 부품의 고장이 교체 24시간 전 이내에 기록된 경우
      preventive = 그 외

    반환: {'preventive': {'median_days': float, 'n': int},
            'reactive':   {'median_days': float, 'n': int}}
    """
    intervals: dict[str, list[float]] = {'preventive': [], 'reactive': []}

    for (mid, comp), grp in _maint_df.groupby(['machineID', 'comp']):
        grp = grp.sort_values('datetime').reset_index(drop=True)
        comp_fails = _fails_df[
            (_fails_df['machineID'] == mid) & (_fails_df['failure'] == comp)
        ]['datetime']

        for i in range(len(grp)):
            mdt = grp.iloc[i]['datetime']
            if not comp_fails.empty:
                is_reactive = bool(
                    ((comp_fails > mdt - pd.Timedelta(hours=24)) & (comp_fails <= mdt)).any()
                )
            else:
                is_reactive = False

            mtype = 'reactive' if is_reactive else 'preventive'

            if i + 1 < len(grp):
                gap = (grp.iloc[i + 1]['datetime'] - mdt).days
                if gap > 0:
                    intervals[mtype].append(float(gap))

    stats = {}
    for mtype, vals in intervals.items():
        if vals:
            arr = np.array(vals)
            stats[mtype] = {'median_days': float(np.median(arr)), 'n': len(arr)}
        else:
            stats[mtype] = {'median_days': None, 'n': 0}
    return stats


@st.cache_data(show_spinner=False)
def compute_equipment_maint_counts(_maint_df: pd.DataFrame, _fails_df: pd.DataFrame) -> dict:
    """
    장비별 · 장비×부품별 예방/사후 교체 건수 집계.

    반환:
    {
        'by_machine':      {machineID: {'preventive': int, 'reactive': int}},
        'by_machine_comp': {(machineID, comp): {'preventive': int, 'reactive': int}},
        'global':          {'preventive': int, 'reactive': int},
        'by_comp_global':  {comp: {'preventive': int, 'reactive': int}},
    }
    """
    by_machine: dict[int, dict[str, int]]    = {}
    by_machine_comp: dict[tuple, dict[str, int]] = {}

    for (mid, comp), grp in _maint_df.groupby(['machineID', 'comp']):
        grp = grp.sort_values('datetime').reset_index(drop=True)
        comp_fails = _fails_df[
            (_fails_df['machineID'] == mid) & (_fails_df['failure'] == comp)
        ]['datetime']

        p_cnt = r_cnt = 0
        for _, row in grp.iterrows():
            mdt = row['datetime']
            is_reactive = (not comp_fails.empty) and bool(
                ((comp_fails > mdt - pd.Timedelta(hours=24)) & (comp_fails <= mdt)).any()
            )
            if is_reactive:
                r_cnt += 1
            else:
                p_cnt += 1

        by_machine_comp[(mid, comp)] = {'preventive': p_cnt, 'reactive': r_cnt}
        if mid not in by_machine:
            by_machine[mid] = {'preventive': 0, 'reactive': 0}
        by_machine[mid]['preventive'] += p_cnt
        by_machine[mid]['reactive']   += r_cnt

    g_p = sum(v['preventive'] for v in by_machine.values())
    g_r = sum(v['reactive']   for v in by_machine.values())

    by_comp_global: dict[str, dict[str, int]] = {}
    for (mid, comp), v in by_machine_comp.items():
        if comp not in by_comp_global:
            by_comp_global[comp] = {'preventive': 0, 'reactive': 0}
        by_comp_global[comp]['preventive'] += v['preventive']
        by_comp_global[comp]['reactive']   += v['reactive']

    return {
        'by_machine':      by_machine,
        'by_machine_comp': by_machine_comp,
        'global':          {'preventive': g_p, 'reactive': g_r},
        'by_comp_global':  by_comp_global,
    }

# ────────────────────────────────────────────────────────────────────────────
# Top-K 계산 (추이 포함)
# ────────────────────────────────────────────────────────────────────────────

def _excess_ratio_series(sub: pd.DataFrame, sensor: str, baseline: dict, thresholds: dict) -> pd.Series:
    """feat_df 부분집합에서 해당 센서의 excess_ratio 시계열 반환."""
    col       = f'{sensor}_mean24h'
    comp      = SENSOR_COMP[sensor]
    direction = SENSOR_DIRECTION[sensor]
    thr       = thresholds.get(comp, 5.0)
    bl_mean   = baseline[sensor]['mean']
    bl_std    = baseline[sensor]['std']

    if col not in sub.columns or bl_std == 0:
        return pd.Series(np.nan, index=sub.index)

    z     = (sub[col] - bl_mean) / bl_std
    eff_z = (-z).clip(lower=0.0) if direction == 'negative' else z.abs()
    return (eff_z / thr).where(sub[col].notna(), other=np.nan)


def _trend_label(sub: pd.DataFrame, sensor: str, ts: pd.Timestamp,
                 baseline: dict, thresholds: dict) -> str:
    """
    최근 24h 평균과 직전 24h 평균을 비교해 추이 라벨 반환.
    기준: TREND_CHANGE_THRESHOLD_PCT% 이상 변화 시 상승/하락.
    """
    er = _excess_ratio_series(sub, sensor, baseline, thresholds)
    sub = sub.copy()
    sub['_er'] = er.values

    t24h = ts - pd.Timedelta(hours=24)
    t48h = ts - pd.Timedelta(hours=48)

    r_mean = sub.loc[sub['datetime'] > t24h, '_er'].dropna().mean()
    p_mean = sub.loc[(sub['datetime'] > t48h) & (sub['datetime'] <= t24h), '_er'].dropna().mean()

    thr = TREND_CHANGE_THRESHOLD_PCT / 100.0
    if pd.isna(r_mean) or pd.isna(p_mean) or p_mean < 0.01:
        return '판단 불가'
    chg = (r_mean - p_mean) / p_mean
    if chg > thr:
        return '상승 중'
    if chg < -thr:
        return '하락 중'
    return '보합'


@st.cache_data(show_spinner=False)
def compute_topk_table(
    timestamp_str: str,
    K: int,
    _feat_df,
    _baseline: dict,
    _thresholds: dict,
    _errors_df,
) -> tuple[pd.DataFrame, str | None]:
    if _feat_df is None:
        return pd.DataFrame(), "사전 계산 파일(feat_df.pkl) 없음"

    ts     = pd.Timestamp(timestamp_str)
    subset = _feat_df[_feat_df['datetime'] == ts]
    if subset.empty:
        return pd.DataFrame(), f"해당 시각({timestamp_str}) 데이터 없음 — 매 정시 단위"

    records = []
    for _, row in subset.iterrows():
        mid = int(row['machineID'])
        for sensor in SENSORS:
            col = f'{sensor}_mean24h'
            if col not in row.index or pd.isna(row[col]):
                continue
            bl_mean   = _baseline[sensor]['mean']
            bl_std    = _baseline[sensor]['std']
            if bl_std == 0:
                continue
            z         = (float(row[col]) - bl_mean) / bl_std
            comp      = SENSOR_COMP[sensor]
            direction = SENSOR_DIRECTION[sensor]
            thr       = _thresholds.get(comp, 5.0)
            eff_z     = max(-z, 0.0) if direction == 'negative' else abs(z)
            er        = eff_z / thr if thr > 0 else 0.0
            records.append({'machineID': mid, 'comp': comp, 'sensor': sensor,
                            'excess_ratio': er})

    if not records:
        return pd.DataFrame(), "계산 결과 없음"

    df = pd.DataFrame(records)
    df['grade'] = df['excess_ratio'].apply(
        lambda er: '알람' if er >= GRADE_ALARM else ('관찰' if er >= GRADE_WATCH else '정상')
    )

    win_start = ts - pd.Timedelta(hours=24)
    mids_err  = set(
        _errors_df[
            (_errors_df['datetime'] > win_start) & (_errors_df['datetime'] <= ts)
        ]['machineID'].tolist()
    )
    df['has_prior_error'] = df['machineID'].isin(mids_err)
    df = df.sort_values('excess_ratio', ascending=False).head(K).reset_index(drop=True)

    # 추이 배치 계산 (선택 장비 1대 × 7일 단위 조회보다 적은 범위)
    t7d  = ts - pd.Timedelta(days=7)
    mids = df['machineID'].tolist()
    batch = _feat_df[
        (_feat_df['machineID'].isin(mids)) &
        (_feat_df['datetime'] > t7d) &
        (_feat_df['datetime'] <= ts)
    ]

    trend_labels = []
    for _, row in df.iterrows():
        mid    = int(row['machineID'])
        sensor = row['sensor']
        sub    = batch[batch['machineID'] == mid]
        if sub.empty:
            trend_labels.append('판단 불가')
        else:
            trend_labels.append(_trend_label(sub, sensor, ts, _baseline, _thresholds))

    df['trend'] = trend_labels
    return df, None

# ────────────────────────────────────────────────────────────────────────────
# Evidence Package (선택 장비)
# ────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def get_evidence_pkg(
    machine_id: int, timestamp_str: str,
    _tel, _errs, _fails, _maint, _mach, _baseline_df,
) -> dict:
    return generate_evidence_package(
        machine_id, timestamp_str,
        _tel, _errs, _fails, _maint, _mach,
        baseline=_baseline_df,
    )

# ────────────────────────────────────────────────────────────────────────────
# 추이 계산 — 선택 장비 1대 × 직전 7일 (성능 제한)
# ────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def compute_trend_7d(
    machine_id: int,
    timestamp_str: str,
    _feat_df,
    _baseline: dict,
    _thresholds: dict,
) -> pd.DataFrame | None:
    """
    선택 장비 1대의 직전 7일 부품별 이상 수준 시계열.
    이상 수준 = 유효 편차 / 부품별 판정 기준선 (1.0 = 기준선 도달).
    계산 범위를 1대 × 168시간으로 제한.
    """
    if _feat_df is None:
        return None
    ts      = pd.Timestamp(timestamp_str)
    t_start = ts - pd.Timedelta(days=7)

    sub = _feat_df[
        (_feat_df['machineID'] == machine_id) &
        (_feat_df['datetime'] > t_start) &
        (_feat_df['datetime'] <= ts)
    ].copy().sort_values('datetime').reset_index(drop=True)

    if sub.empty:
        return None

    result = pd.DataFrame({'datetime': sub['datetime'].values})
    for sensor in SENSORS:
        comp = SENSOR_COMP[sensor]
        result[comp] = _excess_ratio_series(sub, sensor, _baseline, _thresholds).values

    return result


def _trend_summaries(trend_df: pd.DataFrame, ts_str: str) -> dict[str, str]:
    """
    부품별 추이 요약.
    판정 기준: 최근 24시간 평균이 직전 24시간 평균 대비
               TREND_CHANGE_THRESHOLD_PCT% 이상 변화 시 상승/하락.
    """
    if trend_df is None or trend_df.empty:
        return {c: '데이터 없음' for c in COMP_SHORT}

    ts   = pd.Timestamp(ts_str)
    t24h = ts - pd.Timedelta(hours=24)
    t48h = ts - pd.Timedelta(hours=48)

    recent = trend_df[trend_df['datetime'] > t24h]
    prev   = trend_df[(trend_df['datetime'] > t48h) & (trend_df['datetime'] <= t24h)]
    thr    = TREND_CHANGE_THRESHOLD_PCT / 100.0

    result = {}
    for comp in COMP_SHORT:
        if comp not in trend_df.columns:
            result[comp] = '데이터 없음'
            continue
        r_mean = recent[comp].dropna().mean()
        p_mean = prev[comp].dropna().mean()

        if pd.isna(r_mean) or pd.isna(p_mean) or p_mean < 0.01:
            result[comp] = '판단 불가'
            continue

        chg = (r_mean - p_mean) / p_mean
        if chg > thr:
            result[comp] = '상승 중'
        elif chg < -thr:
            result[comp] = '하락 중'
        else:
            result[comp] = '보합'
    return result

# ────────────────────────────────────────────────────────────────────────────
# UI 헬퍼
# ────────────────────────────────────────────────────────────────────────────

def badge_html(grade: str) -> str:
    """grade = 내부 코드(알람/관찰/정상). 표시는 GRADE_LABEL로 변환."""
    label = GRADE_LABEL.get(grade, grade)
    c  = GRADE_COLOR.get(grade, '#718096')
    bg = GRADE_BG.get(grade, '#f7fafc')
    bd = GRADE_BORDER.get(grade, '#cbd5e0')
    return (
        f'<span style="display:inline-block;background:{bg};color:{c};'
        f'border:1.5px solid {bd};border-radius:4px;padding:1px 8px;'
        f'font-weight:700;font-size:0.85em;white-space:nowrap;">{label}</span>'
    )

_GRADE_LABEL_REV = {v: k for k, v in {'알람': '즉시 교체 필요', '관찰': '관찰 필요', '정상': '정상'}.items()}

def grade_cell_style(val: str) -> str:
    """val = 표시명(즉시 교체 필요 등). 내부 코드로 역변환해 색 조회."""
    grade = _GRADE_LABEL_REV.get(val, val)
    c  = GRADE_COLOR.get(grade, '#718096')
    bg = GRADE_BG.get(grade, '#f7fafc')
    return f'color:{c};background-color:{bg};font-weight:700'


def trend_cell_style(val: str) -> str:
    if '상승' in val:
        return 'color:#c53030;font-weight:700'
    if '하락' in val:
        return 'color:#276749'
    return ''


def _section_card(num: int, question: str, border: str = '#cbd5e0') -> None:
    idx   = num - 1
    icons = ['①', '②', '③', '④', '⑤']
    icon  = icons[idx] if idx < len(icons) else str(num)
    st.markdown(
        f'<div style="border-left:4px solid {border};padding:6px 12px 4px 14px;'
        f'background:#f8f9fa;margin:14px 0 6px 0;border-radius:0 4px 4px 0;">'
        f'<span style="font-size:0.78em;color:#999;">{icon}</span> '
        f'<strong style="font-size:1.0em;">{question}</strong></div>',
        unsafe_allow_html=True,
    )


def _basis_expander(label: str, items: list[tuple[str, str]], paths: list[str] = None) -> None:
    """근거 expander — 한글 설명+값 / 개발자용 경로는 한 단계 더 접음."""
    with st.expander(f"근거 보기 — {label}", expanded=False):
        for desc, val in items:
            st.markdown(f"- **{desc}**: {val}" if val else f"- **{desc}**")
        if paths:
            st.markdown(
                f'<details style="margin-top:6px;">'
                f'<summary style="color:#aaa;font-size:0.78em;cursor:pointer;">'
                f'개발자용 데이터 경로</summary>'
                f'<pre style="color:#bbb;font-size:0.75em;background:#f9f9f9;'
                f'padding:6px;border-radius:4px;overflow-x:auto;">'
                + '\n'.join(paths) +
                f'</pre></details>',
                unsafe_allow_html=True,
            )

# ────────────────────────────────────────────────────────────────────────────
# ① 얼마나 급한가
# ────────────────────────────────────────────────────────────────────────────

def render_situation(pkg: dict, candidates: list) -> None:
    top        = next((c for c in candidates if c.get('grade') != '정상'), None)
    top_grade  = top['grade'] if top else '정상'
    border_clr = GRADE_BORDER.get(top_grade, '#cbd5e0')
    _section_card(1, "얼마나 급한가", border_clr)

    mid      = pkg['machine_id']
    ts       = pkg['timestamp'][:16]
    err_cnt  = pkg['error_context']['count']
    rate     = GRADE_FAILURE_RATE.get(top_grade, 0)

    st.markdown(f"**장비 {mid}** &nbsp;·&nbsp; {ts}")

    if top_grade != '정상':
        comp_name = COMP_DISPLAY.get(top['comp'], top['comp'])
        st.markdown(
            f"현재 등급: {badge_html(top_grade)} &nbsp;({comp_name} 기준)  \n"
            f"이 등급의 과거 사례에서 **24시간 안에 실제 고장으로 이어진 경우: 약 {rate:.0f}%**",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"현재 등급: {badge_html('정상')}  \n"
            "모든 계통에서 이상 신호가 감지되지 않았습니다.",
            unsafe_allow_html=True,
        )

    if err_cnt > 0:
        st.markdown(f"직전 24시간 경고 **{err_cnt}건** 선행.")
    elif top_grade != '정상':
        st.markdown("직전 24시간 경고: **없음**.")

    _basis_expander(
        "상황 요약",
        [
            ('장비 번호',                str(mid)),
            ('이벤트 시각',              ts),
            ('현재 최고 등급',           GRADE_LABEL.get(top_grade, top_grade)),
            ('해당 등급 24시간 내 고장 비율', f'약 {rate:.0f}%'),
            ('직전 24시간 경고 건수',    f'{err_cnt}건'),
        ],
        ['machine_id', 'timestamp', 'component_hypotheses', 'error_context.count'],
    )

# ────────────────────────────────────────────────────────────────────────────
# ② 나빠지고 있나 — 직전 7일 추이
# ────────────────────────────────────────────────────────────────────────────

def render_trend(trend_df: pd.DataFrame | None, ts_str: str) -> None:
    _section_card(2, "나빠지고 있나 — 직전 7일 추이")

    if trend_df is None or trend_df.empty:
        st.info("이 시각 기준 7일치 이력이 없어 추이를 계산할 수 없습니다.")
        return

    summaries = _trend_summaries(trend_df, ts_str)

    col_chart, col_sum = st.columns([3, 1])

    with col_chart:
        # 롱 포맷으로 변환
        comp_cols = [c for c in ['comp1', 'comp2', 'comp3', 'comp4'] if c in trend_df.columns]
        long_rows = []
        for comp in comp_cols:
            tmp = trend_df[['datetime', comp]].copy()
            tmp.columns = ['datetime', '이상수준']
            tmp['계통'] = COMP_SHORT[comp]
            long_rows.append(tmp)

        long_df = pd.concat(long_rows, ignore_index=True).dropna(subset=['이상수준'])

        if long_df.empty:
            st.info("유효한 차트 데이터가 없습니다.")
        else:
            y_ceil = max(float(long_df['이상수준'].max()) * 1.15, 1.3)

            color_scale = alt.Scale(
                domain=[COMP_SHORT[c] for c in comp_cols],
                range=['#4299e1', '#48bb78', '#ed8936', '#9f7aea'][:len(comp_cols)],
            )

            lines = (
                alt.Chart(long_df)
                .mark_line(strokeWidth=2, opacity=0.9)
                .encode(
                    x=alt.X('datetime:T', title='날짜',
                             axis=alt.Axis(format='%m-%d', labelAngle=-30, tickCount=7)),
                    y=alt.Y('이상수준:Q', title='이상 수준 (기준선=1.0)',
                             scale=alt.Scale(domain=[0, y_ceil])),
                    color=alt.Color('계통:N', scale=color_scale),
                    tooltip=[
                        alt.Tooltip('datetime:T', title='시각', format='%m-%d %H:%M'),
                        alt.Tooltip('계통:N'),
                        alt.Tooltip('이상수준:Q', title='이상 수준', format='.2f'),
                    ],
                )
            )

            # 알람 기준선 (1.0) — 빨간 점선
            alarm_ref = (
                alt.Chart(pd.DataFrame({'y': [1.0], 'text': ['알람 기준 (고장 비율 약 24%)']}))
                .mark_rule(strokeDash=[5, 3], color='#e53e3e', strokeWidth=1.5)
                .encode(y='y:Q', tooltip=alt.Tooltip('text:N'))
            )
            # 관찰 기준선 (0.8) — 주황 점선
            watch_ref = (
                alt.Chart(pd.DataFrame({'y': [0.8], 'text': ['관찰 기준 (고장 비율 약 16%)']}))
                .mark_rule(strokeDash=[5, 3], color='#d97706', strokeWidth=1.5)
                .encode(y='y:Q', tooltip=alt.Tooltip('text:N'))
            )

            chart = (
                (lines + alarm_ref + watch_ref)
                .properties(title='직전 7일 계통별 이상 수준 추이', height=260)
                .configure_axis(labelFontSize=11, titleFontSize=11)
                .configure_title(fontSize=12)
            )
            st.altair_chart(chart, use_container_width=True)
            st.caption("빨간 점선: 알람 기준(1.0) / 주황 점선: 관찰 기준(0.8)  ·  기준선에 가까울수록 주의 필요")

    with col_sum:
        st.markdown("**이상수준 추이**")
        for comp, comp_name in COMP_SHORT.items():
            summary = summaries.get(comp, '판단 불가')
            if '상승' in summary:
                icon, color = '▲', '#c53030'
            elif '하락' in summary:
                icon, color = '▼', '#276749'
            else:
                icon, color = '–', '#718096'
            st.markdown(
                f"**{comp_name}**  \n"
                f'<span style="font-size:0.83em;color:{color};">{icon} {summary}</span>',
                unsafe_allow_html=True,
            )
            st.markdown(' ')

    st.caption(
        "이상수준 = 센서 편차 ÷ 해당 계통 판정 기준선. 1.0이면 기준선 도달(알람).  \n"
        f"추이 판정: 최근 24시간 이상수준 평균이 직전 24시간 대비 "
        f"{TREND_CHANGE_THRESHOLD_PCT}% 넘게 올랐으면 상승 중, 그 이상 내렸으면 하락 중."
    )

# ────────────────────────────────────────────────────────────────────────────
# ③ 얼마나 확실한가 — 판단 근거의 신뢰도
# ────────────────────────────────────────────────────────────────────────────

def render_certainty(pkg: dict) -> None:
    _section_card(3, "얼마나 확실한가 — 판단 근거의 신뢰도")

    errors   = pkg['error_context']['errors']
    err_cnt  = pkg['error_context']['count']
    no_prior = pkg['status_flags']['no_prior_error']
    hyps     = pkg['component_hypotheses']

    if err_cnt > 0:
        st.markdown(f"**직전 24시간 경고 {err_cnt}건** — 과거 이력:")
        basis_items = [('직전 24시간 경고 건수', f'{err_cnt}건')]
        paths = ['error_context.count']

        for e in errors:
            eid      = e['errorID']
            eid_disp = ERROR_DISPLAY.get(eid, eid)
            total    = e['basis']['total_occurrences_all_time']
            conv     = e['basis']['converted_to_failure_24h']
            rate     = e['failure_conversion_rate_24h']
            if rate is not None and total > 0:
                pct = round(rate * 100, 1)
                st.markdown(
                    f"- **{eid_disp}** 발생 — "
                    f"과거 {total:,}번 중 {conv:,}번이 24시간 안에 고장"
                )
                basis_items.append(
                    (f'{eid_disp} 24시간 내 고장', f'{total:,}번 중 {conv:,}번 ({pct}%)')
                )
            else:
                st.markdown(f"- **{eid_disp}**: 과거 이력 산출 근거 없음")
                basis_items.append((f'{eid_disp}', '이력 없음'))
            paths += [
                f'error_context.errors[{eid}].failure_conversion_rate_24h',
                f'error_context.errors[{eid}].basis.total_occurrences_all_time',
                f'error_context.errors[{eid}].basis.converted_to_failure_24h',
            ]
        _basis_expander("판단 근거", basis_items, paths)

    elif no_prior and hyps:
        st.warning(
            "**직전 24시간 경고 없음** — 판단 근거가 제한적입니다.  \n"
            "전체 고장 761건 중 이 패턴(경고 없이 이상 신호만 있는 상태)은  \n"
            "**15건(2.0%)**에서만 관찰됐습니다.  \n"
            "자동 알림 기준에 해당하지 않는 상태입니다.",
            icon="ℹ️",
        )
        _basis_expander(
            "판단 근거",
            [
                ('직전 24시간 경고 건수', '0건'),
                ('경고 없는 고장 사례', '전체 761건 중 15건 (2.0%)'),
            ],
            ['error_context.count', 'status_flags.no_prior_error', 'component_hypotheses'],
        )
    else:
        st.markdown("직전 24시간 경고 없음. 이상 신호도 없음. **판단할 근거 없음.**")
        _basis_expander(
            "판단 근거",
            [('직전 24시간 경고 건수', '0건'), ('이상 신호', '없음')],
            ['error_context.count', 'status_flags.no_prior_error'],
        )

# ────────────────────────────────────────────────────────────────────────────
# ④ 미루면 뭐가 나빠지는가 — 과거 이력 기반 비교
# ────────────────────────────────────────────────────────────────────────────

def _single_bar_chart(label: str, p_cnt: int, r_cnt: int) -> alt.Chart:
    """장비 1대의 미리 교체 / 고장 후 교체 가로 누적 막대."""
    df = pd.DataFrame([
        {'구분': label, '유형': '미리 교체',    '건수': p_cnt},
        {'구분': label, '유형': '고장 후 교체', '건수': r_cnt},
    ])
    return (
        alt.Chart(df)
        .mark_bar(size=32)
        .encode(
            x=alt.X('건수:Q', stack='normalize',
                     axis=alt.Axis(format='%', title=None, tickCount=5,
                                   labelFont='sans-serif')),
            y=alt.Y('구분:N', sort=None,
                    axis=alt.Axis(title=None, labelLimit=300,
                                  labelFont='sans-serif')),
            color=alt.Color(
                '유형:N',
                scale=alt.Scale(
                    domain=['미리 교체', '고장 후 교체'],
                    range=['#3182ce', '#e53e3e'],
                ),
                legend=alt.Legend(title=None, orient='top',
                                  labelFont='sans-serif'),
            ),
            tooltip=[
                alt.Tooltip('유형:N',  title='유형'),
                alt.Tooltip('건수:Q',  title='건수', format=','),
            ],
        )
        .properties(height=alt.Step(60))
    )


def render_delay(
    pkg: dict,
    maint_stats: dict,
    candidates: list,
    equip_counts: dict,
) -> None:
    _section_card(4, "미루면 뭐가 나빠지는가 — 과거 이력 기반 비교")

    maint_ctx = pkg['maintenance_context']
    mid       = pkg['machine_id']

    # 이상 등급이 있는 부품 우선, 없으면 교체 이력이 있는 부품
    priority = [c['comp'] for c in candidates if c.get('grade') != '정상']
    if not priority:
        priority = [c for c, info in maint_ctx.items() if info.get('last_replacement')]

    basis_items: list[tuple[str, str]] = []
    paths: list[str] = []

    for comp in priority[:2]:
        comp_name = COMP_DISPLAY.get(comp, comp)
        info      = maint_ctx.get(comp, {})
        days      = info.get('days_elapsed')
        mtype     = info.get('type')
        last_dt   = info.get('last_replacement')

        if last_dt and days is not None:
            mtype_ko = '미리 교체' if mtype == 'preventive' else '고장 후 교체'
            st.markdown(
                f"**{comp_name}** — 마지막 교체 후 **{days:.0f}일** 경과  "
                f"({last_dt[:10]}, {mtype_ko})"
            )
            basis_items += [
                (f'{comp_name} 마지막 교체일', last_dt[:10]),
                (f'{comp_name} 경과일',        f'{days:.0f}일'),
                (f'{comp_name} 교체 유형',     mtype_ko),
            ]
            paths += [
                f'maintenance_context.{comp}.last_replacement',
                f'maintenance_context.{comp}.days_elapsed',
                f'maintenance_context.{comp}.type',
            ]
        else:
            st.markdown(f"**{comp_name}** — 교체 이력 없음")
            basis_items.append((f'{comp_name} 교체 이력', '없음'))
            paths.append(f'maintenance_context.{comp}.last_replacement')

    # ── 장비 교체 이력 차트 ──────────────────────────────────────────────
    st.markdown("")
    st.markdown("**교체 이력 — 미리 교체 vs 고장 후 교체**")

    machine_cnt = equip_counts['by_machine'].get(mid, {'preventive': 0, 'reactive': 0})
    m_p, m_r    = machine_cnt['preventive'], machine_cnt['reactive']
    m_total     = m_p + m_r

    # 100대 중 고장 후 교체 건수 순위
    all_reactive = sorted(
        equip_counts['by_machine'].items(),
        key=lambda kv: kv[1]['reactive'],
        reverse=True,
    )
    rank = next((i + 1 for i, (m, _) in enumerate(all_reactive) if m == mid), None)

    if m_total < 5:
        st.markdown(
            f"미리 교체 **{m_p}건** / 고장 후 교체 **{m_r}건** (계 {m_total}건)  \n"
            "**이력이 적어 비교가 어려움**"
        )
        basis_items.append(
            (f'장비 {mid} 교체 이력',
             f'미리 교체 {m_p}건 / 고장 후 교체 {m_r}건 (계 {m_total}건) — 이력 부족')
        )
    else:
        st.altair_chart(
            _single_bar_chart(f'장비 {mid}', m_p, m_r),
            use_container_width=True,
        )
        st.caption(f"미리 교체 {m_p:,}건 / 고장 후 교체 {m_r:,}건 (계 {m_total:,}건)")
        if rank is not None:
            st.caption(f"100대 중 고장 후 교체가 {rank}번째로 많음")
        basis_items += [
            (f'장비 {mid} 미리 교체 건수',    f'{m_p:,}건'),
            (f'장비 {mid} 고장 후 교체 건수', f'{m_r:,}건'),
            (f'장비 {mid} 고장 후 교체 순위', f'100대 중 {rank}위' if rank else '산출 불가'),
        ]

    # ── 계통 단위 (건수 5건 이상인 경우만) ──────────────────────────────
    shown_comp_chart = False
    for comp in priority[:2]:
        comp_disp = COMP_DISPLAY.get(comp, comp)
        comp_cnt  = equip_counts['by_machine_comp'].get(
            (mid, comp), {'preventive': 0, 'reactive': 0}
        )
        c_p, c_r = comp_cnt['preventive'], comp_cnt['reactive']
        c_total  = c_p + c_r
        if c_total < 5:
            continue

        if not shown_comp_chart:
            st.markdown("**계통별 상세**")
            shown_comp_chart = True

        st.altair_chart(
            _single_bar_chart(comp_disp, c_p, c_r),
            use_container_width=True,
        )
        st.caption(f"{comp_disp}: 미리 교체 {c_p:,}건 / 고장 후 교체 {c_r:,}건 (계 {c_total:,}건)")
        basis_items += [
            (f'{comp_disp} 미리 교체',    f'{c_p:,}건 (계 {c_total:,}건)'),
            (f'{comp_disp} 고장 후 교체', f'{c_r:,}건 (계 {c_total:,}건)'),
        ]

    # ── 교체 간격 요약 ───────────────────────────────────────────────────
    p_stat = maint_stats.get('preventive', {})
    r_stat = maint_stats.get('reactive',   {})
    p_med  = p_stat.get('median_days')
    r_med  = r_stat.get('median_days')
    p_n    = p_stat.get('n', 0)
    r_n    = r_stat.get('n', 0)

    if p_med is not None and r_med is not None:
        diff = int(round(p_med - r_med))
        st.caption(
            f"다음 교체까지 — "
            f"미리 교체한 경우 절반은 {p_med:.0f}일 안에 다시 교체 ({p_n:,}건 기준) · "
            f"고장 후 교체한 경우 절반은 {r_med:.0f}일 안에 다시 교체 ({r_n:,}건 기준).  \n"
            f"과거 이력상 미리 교체 쪽이 {diff}일 더 오래 유지됐습니다."
        )
        basis_items += [
            ('미리 교체 후 다음 교체까지 (절반 기준)',    f'{p_med:.0f}일 (간격 이력 {p_n:,}건)'),
            ('고장 후 교체 후 다음 교체까지 (절반 기준)', f'{r_med:.0f}일 (간격 이력 {r_n:,}건)'),
            ('두 유형 차이', f'{diff}일'),
        ]
    elif p_med is not None:
        st.caption(f"미리 교체 후 절반은 {p_med:.0f}일 안에 다시 교체 ({p_n:,}건 기준)")
    elif r_med is not None:
        st.caption(f"고장 후 교체 후 절반은 {r_med:.0f}일 안에 다시 교체 ({r_n:,}건 기준)")

    st.caption(
        "과거 이력에서 관찰된 수치이며 인과관계를 나타내지 않습니다.  \n"
        "교체 주기가 짧은 장비가 고장 후 교체 그룹에 더 많이 포함되었을 수 있습니다."
    )

    _basis_expander("조치 지연 비교", basis_items, paths)

# ────────────────────────────────────────────────────────────────────────────
# ⑤ 어느 부품인가 — 점검 대상 목록
# ────────────────────────────────────────────────────────────────────────────

def render_target(candidates: list) -> None:
    _section_card(5, "어느 계통인가 — 점검 대상")

    if not candidates:
        st.markdown("이상 신호가 감지된 계통 없음.")
        return

    for c in candidates:
        grade     = c.get('grade', '정상')
        comp_name = COMP_DISPLAY.get(c['comp'], c['comp'])
        st.markdown(
            f"{badge_html(grade)} &nbsp;**{comp_name}**",
            unsafe_allow_html=True,
        )

    st.caption(
        "등급 안내 — 같은 상태의 과거 사례에서 24시간 안에 고장으로 이어진 경우:  \n"
        "즉시 교체 필요 약 24% / 관찰 필요 약 16% / 정상 약 0.01%"
    )
    st.caption("센서 상세, 편차 수치는 엔지니어 화면에서 확인하십시오.")

    _basis_expander(
        "점검 대상",
        [(f'{COMP_DISPLAY.get(c["comp"], c["comp"])} 등급',
          GRADE_LABEL.get(c.get('grade', '정상'), c.get('grade', '정상')))
         for c in candidates],
        [f'sensor_evidence.sensors.{c.get("sensor", "?")}.z_score' for c in candidates],
    )

# ────────────────────────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(page_title="장비 점검 대상 목록", layout="wide")
    st.title("장비 점검 대상 목록")
    st.caption("이상 신호 감지 보조 도구 · 자동 판단 없음 · 현장 점검과 반드시 병행")

    with st.spinner("데이터 준비 중…"):
        tel, errs, fails, maint, mach = _load_raw_data()
        feat_df     = _load_feat_df()
        baseline    = _load_baseline_dict()
        thresholds  = _load_thresholds()
        baseline_df = _load_baseline_df()

    with st.spinner("정비 이력 분석 중…"):
        maint_stats  = compute_maint_interval_stats(maint, fails)
        equip_counts = compute_equipment_maint_counts(maint, fails)

    # ── 사이드바 ────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("조회 설정")
        sel_date = st.date_input(
            "조회 날짜",
            value=pd.Timestamp('2015-10-01').date(),
            min_value=pd.Timestamp('2015-01-02').date(),
            max_value=pd.Timestamp('2015-12-31').date(),
        )
        sel_hour = st.selectbox(
            "기준 시각",
            options=list(range(0, 24)),
            index=17,
            format_func=lambda h: f"{h:02d}:00",
        )
        K = st.slider("목록 건수", min_value=1, max_value=20, value=5)

        ts_str       = f"{sel_date} {sel_hour:02d}:00:00"
        win_start_ts = pd.Timestamp(ts_str) - pd.Timedelta(hours=24)

        st.markdown(f"**기준 시각:** {sel_date} {sel_hour:02d}:00")
        st.markdown(
            f"**분석 구간:** {win_start_ts.strftime('%m-%d %H:%M')} "
            f"~ {sel_date} {sel_hour:02d}:00 (이전 24시간)"
        )
        st.markdown("---")
        st.markdown("**이상신호 등급**")
        st.markdown(
            f"{badge_html('알람')} 기준선 초과 — 과거 같은 상태에서 24시간 안에 고장 **약 24%**  \n"
            f"{badge_html('관찰')} 기준선 근접 — 과거 같은 상태에서 24시간 안에 고장 **약 16%**  \n"
            f"{badge_html('정상')} 기준선 이하 — 과거 같은 상태에서 24시간 안에 고장 **약 0.01%**",
            unsafe_allow_html=True,
        )
        st.caption("같은 등급 안에서 목록 순서가 위험도 순위를 보장하지 않습니다.")
        st.markdown("---")
        st.markdown("**기준선이란?**")
        st.markdown(
            "과거 이력을 바탕으로,  \n"
            "\"이 신호 수준에서 24시간 안에  \n"
            "고장이 약 24% 발생했다\"는 지점입니다.  \n"
            "계통마다 신호 크기가 달라 기준선 수치는 다르지만,  \n"
            "기준선에 도달했을 때 고장 비율은  \n"
            "모든 계통이 같게 맞췄습니다."
        )
        st.markdown("---")
        st.markdown("**이상신호 등급 vs 경고 이벤트**")
        st.markdown(
            "둘은 별개의 감지 경로입니다.  \n\n"
            "**이상신호 등급** — 센서 측정값(전압·회전수·압력·진동)이 정상 범위에서 "
            "얼마나 벗어났는지 연속으로 계산한 수치.  \n\n"
            "**경고 이벤트(error1~5)** — 시스템이 특정 조건 발생 시 별도로 기록한 이진 알림. "
            "센서 수치와 독립적으로 발생합니다.  \n\n"
            "고장 직전에 두 신호가 함께 나타나는 경우가 많지만, "
            "한쪽이 없어도 다른 쪽이 있을 수 있습니다."
        )
        st.markdown("---")
        st.markdown("**계통·경고 표시명 안내**")
        st.caption(
            "부품과 경고의 실제 정체는 원본 데이터에  \n"
            "정의되어 있지 않아, 센서 특성을 바탕으로  \n"
            "이름을 붙였습니다.  \n"
            "괄호 안 코드(comp1~4, error1~5)가 원래 식별자입니다."
        )

    # ── 상단: Top-K 목록 ────────────────────────────────────────────────
    st.header(f"우선 확인 대상 장비 (상위 {K}건)")

    col_l, col_r = st.columns([5, 3])
    with col_l:
        st.markdown(
            f"**{sel_date} {sel_hour:02d}:00** 기준 전체 100대 × 4계통 조합(400건) 분석 →"
            f" 이상 신호 수준 높은 순 **{K}건** 선별"
        )
    with col_r:
        st.warning(
            "이 목록은 참고용 후보 목록입니다.  \n"
            "등급(알람·관찰)을 기준으로 판단하고  \n"
            "현장 점검과 병행하십시오.",
            icon="⚠️",
        )

    with st.spinner("분석 중…"):
        topk_df, err_msg = compute_topk_table(
            ts_str, K, feat_df, baseline, thresholds, errs
        )

    if err_msg:
        st.error(f"오류: {err_msg}")
        return
    if topk_df.empty:
        st.info("해당 시각에 표시할 데이터가 없습니다.")
        return

    disp_rows = []
    for i, row in topk_df.iterrows():
        disp_rows.append({
            '#':                  i + 1,
            '장비':               f"장비 {int(row['machineID'])}",
            '계통':               COMP_DISPLAY.get(row['comp'], row['comp']),
            '이상신호 등급':       GRADE_LABEL.get(row['grade'], row['grade']),
            '이상수준 추이 (7일)': row.get('trend', '판단 불가'),
            '직전 24시간 경고':    '있음' if row['has_prior_error'] else '없음',
        })

    disp_df = pd.DataFrame(disp_rows)
    styled  = (
        disp_df.style
        .map(grade_cell_style,  subset=['이상신호 등급'])
        .map(trend_cell_style,  subset=['이상수준 추이 (7일)'])
        .map(lambda v: 'color:#c53030;font-weight:700' if v == '있음' else '', subset=['직전 24시간 경고'])
    )
    st.caption(
        "**이상신호 등급**: 센서 편차가 판정 기준선을 넘은 정도 — "
        "알람(과거 사례 24% 고장) · 관찰(16%) · 정상(0.01%)  \n"
        "**이상수준 추이**: 최근 24시간의 이상신호 수준이 직전 24시간 대비 "
        f"{TREND_CHANGE_THRESHOLD_PCT}% 이상 올랐으면 '상승 중', 그 이상 내렸으면 '하락 중'  \n"
        "**직전 24시간 경고**: 센서 이상과 별개로 경고 이벤트(error1~5)가 발생했는지 여부"
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── 장비 선택 ───────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("선택 장비 상세 리포트")

    select_opts = [
        f"장비 {int(r['machineID'])} / {COMP_DISPLAY.get(r['comp'], r['comp'])} [{r['grade']}]"
        for _, r in topk_df.iterrows()
    ]
    sel_label = st.selectbox("확인할 장비를 선택하세요", options=select_opts, index=0)
    sel_idx   = select_opts.index(sel_label)
    sel_row   = topk_df.iloc[sel_idx]
    sel_mid   = int(sel_row['machineID'])

    # ── 리포트 5블록 ────────────────────────────────────────────────────
    with st.spinner(f"장비 {sel_mid} 분석 중…"):
        pkg        = get_evidence_pkg(sel_mid, ts_str, tel, errs, fails, maint, mach, baseline_df)
        candidates = _all_candidates(pkg)
        trend_df   = compute_trend_7d(sel_mid, ts_str, feat_df, baseline, thresholds)

    st.markdown(
        f"**장비 {sel_mid}** &nbsp;·&nbsp; {sel_date} {sel_hour:02d}:00 기준  \n"
        f"분석 구간: {win_start_ts.strftime('%Y-%m-%d %H:%M')} ~ {ts_str}",
        unsafe_allow_html=True,
    )

    render_situation(pkg, candidates)
    render_trend(trend_df, ts_str)
    render_certainty(pkg)
    render_delay(pkg, maint_stats, candidates, equip_counts)
    render_target(candidates)

    # ── 데이터 한계 ─────────────────────────────────────────────────────
    st.markdown("---")
    st.caption(
        "공개 합성 데이터. 물리 단위와 설비 종류가 원본에 정의되어 있지 않아 상대 편차로 표현함.  \n"
        "합성 데이터 특성상 교체·고장 빈도가 실제 설비보다 높습니다 "
        "(계통 하나당 연간 약 8회 교체, 장비당 연간 고장 약 7건).  \n"
        "부품과 경고의 실제 정체는 원본에 정의되어 있지 않아 계통명은 센서 특성을 바탕으로 한 가정입니다."
    )


if __name__ == '__main__':
    main()
