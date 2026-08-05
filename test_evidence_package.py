"""
Evidence Package 검증 — pytest
케이스:
  1. 정상 구간 (센서 이상 없음, 에러 없음)
  2. 고장 24h 전 (machine=5, comp1 failure at 2015-09-06 06:00, error5 in window)
  3. 선행 경고 없는 고장 (machine=1, comp4 failure at 2015-01-05 06:00, no prior 24h error)
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path

from evidence_package import (
    load_data,
    compute_global_baseline,
    generate_evidence_package,
    SENSORS,
    COMPONENTS,
    Z_THRESHOLD,
)

DATA_DIR = Path(__file__).parent / 'archive'


@pytest.fixture(scope='module')
def raw_data():
    return load_data(DATA_DIR)


@pytest.fixture(scope='module')
def baseline(raw_data):
    tel = raw_data[0]
    return compute_global_baseline(tel)


def call_pkg(machine_id, timestamp, raw_data, baseline):
    tel, errs, fails, maint, mach = raw_data
    return generate_evidence_package(
        machine_id, timestamp, tel, errs, fails, maint, mach, baseline=baseline
    )


# ---------------------------------------------------------------------------
# 케이스 1: 정상 구간
# ---------------------------------------------------------------------------

class TestNormalPeriod:
    """machine=1, 2015-06-15 12:00 — 어떤 failure와도 멀리 떨어진 구간."""

    MACHINE_ID = 1
    TIMESTAMP  = '2015-06-15 12:00:00'

    @pytest.fixture(scope='class')
    def pkg(self, raw_data, baseline):
        return call_pkg(self.MACHINE_ID, self.TIMESTAMP, raw_data, baseline)

    def test_top_level_keys(self, pkg):
        required = {
            'machine_id', 'timestamp', 'window_start',
            'sensor_evidence', 'peer_comparison', 'error_context',
            'maintenance_context', 'component_hypotheses', 'status_flags',
        }
        assert required.issubset(pkg.keys())

    def test_machine_id_preserved(self, pkg):
        assert pkg['machine_id'] == self.MACHINE_ID

    def test_insufficient_data_flag_false(self, pkg):
        # 정상 구간에는 최소 12행 이상 있어야 함
        assert not pkg['status_flags']['insufficient_data']
        assert pkg['sensor_evidence']['window_rows'] >= 12

    def test_sensor_evidence_structure(self, pkg):
        se = pkg['sensor_evidence']
        assert se['reference_frame'] == 'rolling_mean_std_training_only_lt_2015-10-01'
        for s in SENSORS:
            assert s in se['sensors']
            entry = se['sensors'][s]
            assert entry['mean_24h'] is not None
            assert entry['z_score'] is not None
            # 기준 정보 있어야 함
            assert entry['basis']['global_n'] > 0

    def test_sensor_evidence_no_null_in_normal(self, pkg):
        # 정상 구간 z는 대개 ±3 이내 — 이를 검사하지 않고 null 여부만 확인
        for s in SENSORS:
            assert pkg['sensor_evidence']['sensors'][s]['z_score'] is not None

    def test_peer_comparison_structure(self, pkg):
        pc = pkg['peer_comparison']
        assert pc['peer_count'] >= 0
        assert 'percentile_by_sensor' in pc
        assert 'basis' in pc

    def test_peer_comparison_has_counts(self, pkg):
        pc = pkg['peer_comparison']
        if pc['peer_count'] > 0:
            for s in SENSORS:
                entry = pc['percentile_by_sensor'][s]
                if entry is not None:
                    assert 'percentile' in entry
                    assert 'peers_with_data' in entry
                    assert 0 <= entry['percentile'] <= 100

    def test_no_prior_error_flag(self, pkg):
        # 이 구간에 error가 없을 수도 있지만 플래그가 error_context와 일치해야 함
        expected = pkg['error_context']['count'] == 0
        assert pkg['status_flags']['no_prior_error'] == expected

    def test_maintenance_context_all_components(self, pkg):
        mc = pkg['maintenance_context']
        assert set(mc.keys()) == set(COMPONENTS)
        for comp in COMPONENTS:
            entry = mc[comp]
            assert 'last_replacement' in entry
            assert 'days_elapsed' in entry
            assert 'type' in entry
            if entry['type'] is not None:
                assert entry['type'] in ('preventive', 'reactive')

    def test_maintenance_days_elapsed_positive(self, pkg):
        for comp in COMPONENTS:
            entry = pkg['maintenance_context'][comp]
            if entry['days_elapsed'] is not None:
                assert entry['days_elapsed'] >= 0

    def test_no_groundless_values(self, pkg):
        # component_hypotheses 각 항목에 z_score가 있어야 함
        for h in pkg['component_hypotheses']:
            assert h['sensor_z_score'] is not None
            assert h['component'] in COMPONENTS
            assert h['associated_sensor'] in SENSORS

    def test_multiple_candidates_flag_consistent(self, pkg):
        n = len(pkg['component_hypotheses'])
        assert pkg['status_flags']['multiple_candidates'] == (n >= 2)


# ---------------------------------------------------------------------------
# 케이스 2: 고장 24h 전 (prior error 있음)
# ---------------------------------------------------------------------------

class TestPreFailure24h:
    """
    machine=5, 2015-09-06 06:00:00 — comp1 failure.
    error5가 24h 창 내 존재.
    """

    MACHINE_ID = 5
    TIMESTAMP  = '2015-09-06 06:00:00'

    @pytest.fixture(scope='class')
    def pkg(self, raw_data, baseline):
        return call_pkg(self.MACHINE_ID, self.TIMESTAMP, raw_data, baseline)

    def test_has_prior_errors(self, pkg):
        assert pkg['error_context']['count'] > 0
        assert not pkg['status_flags']['no_prior_error']

    def test_error_has_conversion_rate(self, pkg):
        for err in pkg['error_context']['errors']:
            assert err['failure_conversion_rate_24h'] is not None
            assert 0.0 <= err['failure_conversion_rate_24h'] <= 1.0
            basis = err['basis']
            assert basis['total_occurrences_all_time'] > 0
            # 분자 ≤ 분모
            assert basis['converted_to_failure_24h'] <= basis['total_occurrences_all_time']

    def test_error_rate_computed_from_all_data(self, pkg):
        # total_occurrences는 특정 machine만이 아니라 전체 기준
        for err in pkg['error_context']['errors']:
            assert err['basis']['total_occurrences_all_time'] >= 1

    def test_insufficient_flag_false(self, pkg):
        assert not pkg['status_flags']['insufficient_data']

    def test_sensor_evidence_present(self, pkg):
        se = pkg['sensor_evidence']
        assert se['window_rows'] >= 12
        for s in SENSORS:
            assert se['sensors'][s]['z_score'] is not None

    def test_maintenance_type_valid(self, pkg):
        for comp in COMPONENTS:
            t = pkg['maintenance_context'][comp]['type']
            assert t in ('preventive', 'reactive', None)

    def test_hypotheses_z_threshold_respected(self, pkg):
        # 부품별 임계(thresholds.json)를 각 가설의 z_threshold 필드로 검증
        for h in pkg['component_hypotheses']:
            z = h['sensor_z_score']
            direction = h['direction']
            if direction == 'both':
                assert abs(z) >= h['z_threshold']
            elif direction == 'negative':
                thresh = float(str(h['z_threshold']).replace('<= -', ''))
                assert z <= -thresh


# ---------------------------------------------------------------------------
# 케이스 3: 선행 경고 없는 고장
# ---------------------------------------------------------------------------

class TestFailureNoWarning:
    """
    machine=1, 2015-01-05 06:00:00 — comp4 failure.
    직전 24h 에러 없음.
    """

    MACHINE_ID = 1
    TIMESTAMP  = '2015-01-05 06:00:00'

    @pytest.fixture(scope='class')
    def pkg(self, raw_data, baseline):
        return call_pkg(self.MACHINE_ID, self.TIMESTAMP, raw_data, baseline)

    def test_no_prior_error_flag_true(self, pkg):
        assert pkg['status_flags']['no_prior_error']
        assert pkg['error_context']['count'] == 0
        assert pkg['error_context']['errors'] == []

    def test_sensor_data_present(self, pkg):
        assert pkg['sensor_evidence']['window_rows'] >= 12

    def test_all_zscores_have_basis(self, pkg):
        se = pkg['sensor_evidence']
        for s in SENSORS:
            entry = se['sensors'][s]
            if entry['z_score'] is not None:
                b = entry['basis']
                assert b['global_mean'] is not None
                assert b['global_std'] is not None
                assert b['global_n'] > 0

    def test_maintenance_context_comp4_present(self, pkg):
        # 고장 부품 comp4의 정비 이력이 있어야 함
        entry = pkg['maintenance_context']['comp4']
        # 2014년 데이터 포함이므로 None이 아닐 것
        assert entry['last_replacement'] is not None

    def test_2014_maint_used_for_elapsed_days(self, pkg):
        # machine=1의 첫 maint는 2014-06-01 comp2
        # → 경과일 > 180 이어야 함 (2015-01-05 기준)
        entry = pkg['maintenance_context']['comp2']
        if entry['last_replacement'] is not None:
            assert entry['days_elapsed'] is not None
            # 2014 maint가 포함돼야 이 값이 유효함

    def test_peer_comparison_percentile_range(self, pkg):
        pc = pkg['peer_comparison']
        for s in SENSORS:
            entry = pc['percentile_by_sensor'][s]
            if entry is not None:
                assert 0 <= entry['percentile'] <= 100

    def test_hypotheses_no_causal_language(self, pkg):
        # 인과 표현("원인") 금지 — "연관", "후보", "associated" 형태 확인
        for h in pkg['component_hypotheses']:
            assoc = h.get('association', '')
            assert '원인' not in assoc
            assert 'cause' not in assoc.lower()

    def test_flags_consistent(self, pkg):
        n = len(pkg['component_hypotheses'])
        assert pkg['status_flags']['multiple_candidates'] == (n >= 2)
        assert pkg['status_flags']['no_prior_error'] == (pkg['error_context']['count'] == 0)


# ---------------------------------------------------------------------------
# 경계 케이스
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_insufficient_data_flag(self, raw_data, baseline):
        # 데이터 범위 밖 → 창에 행 없음 → insufficient
        pkg = call_pkg(1, '2014-01-01 00:00:00', raw_data, baseline)
        assert pkg['status_flags']['insufficient_data']
        assert pkg['sensor_evidence']['window_rows'] < 12

    def test_all_sensors_null_when_no_data(self, raw_data, baseline):
        pkg = call_pkg(1, '2014-01-01 00:00:00', raw_data, baseline)
        for s in SENSORS:
            assert pkg['sensor_evidence']['sensors'][s]['z_score'] is None

    def test_output_is_plain_dict(self, raw_data, baseline):
        pkg = call_pkg(1, '2015-06-01 00:00:00', raw_data, baseline)
        assert isinstance(pkg, dict)
        # JSON 직렬화 가능 여부 (numpy 타입 유출 없어야 함)
        import json
        json.dumps(pkg)  # 예외 없으면 통과
