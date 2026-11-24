"""Exact-bounds reconciliation and guardrail tests (run with: python -I -m tests.test_reconciliation)."""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.analysis import analyze
from src.services.reconciliation import ReconciliationError, reconcile
from src.services.validation import parse_finance_rows, parse_rows

DEMO = Path(__file__).resolve().parents[1] / 'demo'
SNAPSHOT = '2026-11-23'
PARAMS = {'snapshot_date': SNAPSHOT, 'min_per_arm': 30, 'alpha': 0.05,
          'min_lift_pp': 10, 'max_refund_pct': 15}


def checkout(reader, day, variant='B', paid=1, refund='-'):
    return {'reader_id': reader, 'checkout_day': day, 'variant': variant, 'paid': paid,
            'refund_day': refund}


def finance(day, count, reader=None):
    return {'finance_day': day, 'refund_count': count, 'reader_id': reader}


def run_bounds(rows, ledger):
    snapshot = date.fromisoformat(SNAPSHOT)
    identified = sum(
        1 for row in rows
        if row['paid'] == 1 and row['variant'] == 'B' and row['refund_day'] != '-'
        and row['checkout_day'] <= SNAPSHOT
        and (date.fromisoformat(row['refund_day']) - date.fromisoformat(row['checkout_day'])).days <= 7
        and (snapshot - date.fromisoformat(row['checkout_day'])).days >= 7
    )
    return reconcile(rows, ledger, snapshot, identified)


def test_demo_bounds_and_split_guardrail():
    rows = parse_rows((DEMO / 'sample.csv').read_text(encoding='utf-8-sig'))
    ledger = parse_finance_rows((DEMO / 'finance_ledger.csv').read_text(encoding='utf-8-sig'))
    result = analyze(rows, PARAMS, finance_rows=ledger)
    assert result['refund_interval'] == {
        'lower': 0, 'upper': 2, 'lower_gate_ok': True, 'upper_gate_ok': False,
        'guardrail': 'indeterminate', 'reconciled': True,
    }
    assert '暂缓放量' in result['tables'][1]['rows'][3][3]
    # Every finance day reconciles exactly: identified + anonymous = total, one allocation per order.
    for day in result['tables'][2]['rows']:
        assert day[1] == day[2] + day[3]
        for orders_text in (day[5], day[6]):
            ids = orders_text.split('、') if orders_text != '—' else []
            assert len(ids) == day[3] and len(ids) == len(set(ids))
    print('ok demo bounds 0-2 with split guardrail -> 暂缓放量')


def test_late_refunds_consume_slots():
    # ZY-005's late refund (day 8) is identified; the anonymous slot the same day
    # must still be allocated and cannot disappear from the reconciliation.
    rows = [
        checkout('B1', '2026-11-10', refund='2026-11-18'),   # late, window ends 11-17
        checkout('B2', '2026-11-10'),
        checkout('A1', '2026-11-10', variant='A'),
    ]
    ledger = [finance('2026-11-18', 2), finance('2026-11-18', 1, 'B1')]
    outcome = run_bounds(rows, ledger)
    assert outcome['anonymous_total'] == 1
    # The only anonymous slot lands on day 8, which is outside every order's window.
    assert outcome['lower_bound'] == 0 and outcome['upper_bound'] == 0
    day = outcome['days'][0]
    assert day['identified_orders'][0]['reader_id'] == 'B1'
    assert day['identified_orders'][0]['in_window_mature_b'] is False
    assert day['anonymous_slots'] == 1
    print('ok late refund stays in the ledger and only one anonymous slot remains')


def test_refund_before_payment_rejected():
    rows = [checkout('B1', '2026-11-15')]
    ledger = [finance('2026-11-14', 1)]
    try:
        run_bounds(rows, ledger)
    except ReconciliationError as error:
        assert '无法全部' in str(error) or '对账不平' in str(error)
        print('ok infeasible ledger (no order payable that early) is rejected')
        return
    raise AssertionError('expected rejection')


def test_identified_order_takes_no_anonymous_slot():
    rows = [
        checkout('B1', '2026-11-10', refund='2026-11-12'),
        checkout('B2', '2026-11-10'),
        checkout('A1', '2026-11-10', variant='A'),
    ]
    ledger = [finance('2026-11-12', 2), finance('2026-11-12', 1, 'B1')]
    outcome = run_bounds(rows, ledger)
    for allocation in ('lower_orders', 'upper_orders'):
        ids = [item['reader_id'] for item in outcome['days'][0][allocation]]
        assert 'B1' not in ids and len(ids) == 1
    print('ok identified-refund order excluded from anonymous allocation')


def test_identified_mismatch_rejected():
    rows = [checkout('B1', '2026-11-10')]
    ledger = [finance('2026-11-12', 1), finance('2026-11-12', 1, 'B1')]
    try:
        run_bounds(rows, ledger)
    except ReconciliationError as error:
        assert '不一致' in str(error)
        print('ok finance id not present in locked checkout ledger is rejected')
        return
    raise AssertionError('expected rejection')


def test_oversubscribed_ledger_rejected():
    rows = [checkout('B1', '2026-11-10'), checkout('B2', '2026-11-10')]
    ledger = [finance('2026-11-12', 3)]
    try:
        run_bounds(rows, ledger)
    except ReconciliationError as error:
        assert '对账不平' in str(error)
        print('ok more anonymous refunds than payable orders is rejected')
        return
    raise AssertionError('expected rejection')


def test_both_endpoints_pass_and_fail():
    rows = [checkout(f'B{i}', '2026-11-10') for i in range(50)]
    rows += [checkout(f'A{i}', '2026-11-10', variant='A') for i in range(50)]
    # One anonymous refund on 11-12: it may land on A (lower) or B (upper).
    ledger = [finance('2026-11-12', 1)]
    result = analyze(rows, {'snapshot_date': SNAPSHOT, 'min_per_arm': 1, 'alpha': 0.05,
                            'min_lift_pp': 0, 'max_refund_pct': 15}, finance_rows=ledger)
    assert result['refund_interval']['lower'] == 0
    assert result['refund_interval']['upper'] == 1
    # Upper endpoint CP bound for 1/50 (~8.8%) is within the 15% cap, so both pass.
    assert result['refund_interval']['guardrail'] == 'pass'
    # Cap of 1% makes even the lower endpoint (0/50 CP ~5.8%) fail.
    result_fail = analyze(rows, {'snapshot_date': SNAPSHOT, 'min_per_arm': 1, 'alpha': 0.05,
                                 'min_lift_pp': 0, 'max_refund_pct': 1}, finance_rows=ledger)
    assert result_fail['refund_interval']['guardrail'] == 'fail'
    print('ok both-endpoints pass and both-endpoints fail guardrails')


def test_without_finance_ledger_keeps_original_behavior():
    rows = parse_rows((DEMO / 'sample.csv').read_text(encoding='utf-8-sig'))
    result = analyze(rows, PARAMS)
    assert result['refund_interval']['reconciled'] is False
    assert result['refund_interval']['lower'] == 0 and result['refund_interval']['upper'] == 0
    # Original headline figures remain available.
    cohort = result['tables'][0]['rows'][1]
    assert cohort[1] == 36 and cohort[2] == 24 and cohort[5] == 24
    print('ok run without finance ledger preserves the original single-point behavior')


def test_duplicate_finance_detail_rejected():
    rows = [
        checkout('B1', '2026-11-10', refund='2026-11-12'),
        checkout('B2', '2026-11-10'),
    ]
    ledger = [finance('2026-11-12', 3), finance('2026-11-12', 1, 'B1'),
              finance('2026-11-12', 1, 'B1')]
    try:
        run_bounds(rows, ledger)
    except ReconciliationError as error:
        assert '重复退款' in str(error)
        print('ok duplicate identified refund on the same finance day is rejected')
        return
    raise AssertionError('expected rejection')


def test_identified_b_refund_is_fixed_in_both_bounds():
    rows = [
        checkout('B1', '2026-11-10', refund='2026-11-12'),   # identified mature in-window B
        checkout('B2', '2026-11-10'),
        checkout('A1', '2026-11-10', variant='A'),
    ]
    ledger = [finance('2026-11-12', 2), finance('2026-11-12', 1, 'B1')]
    outcome = run_bounds(rows, ledger)
    # One fixed B in-window refund plus one anonymous slot that can go to A or B.
    assert outcome['lower_bound'] == 1 and outcome['upper_bound'] == 2
    assert outcome['identified_b_mature_in_window'] == 1
    print('ok identified B in-window refund is fixed in both endpoints')


def test_assignment_respects_payment_day_and_window():
    rows = [
        checkout('B_old', '2026-11-10'),                      # window 11-10..11-17
        checkout('B_new', '2026-11-16'),                      # mature on snapshot, window to 11-23
        checkout('A_old', '2026-11-10', variant='A'),
    ]
    # Two anonymous slots: 11-12 (only old orders eligible) and 11-23 (all eligible).
    ledger = [finance('2026-11-12', 1), finance('2026-11-23', 1)]
    outcome = run_bounds(rows, ledger)
    # Upper bound can put both on B: B_old@11-12 (in window), B_new@11-23 (in window).
    upper = {item['reader_id']: day['day'] for day in outcome['days'] for item in day['upper_orders']}
    assert upper.get('B_old') == '2026-11-12'
    assert upper.get('B_new') == '2026-11-23'
    assert outcome['upper_bound'] == 2
    # Lower bound keeps both out of B in-window: A_old@11-12, B_old@11-23 (its window ended 11-17).
    lower = {item['reader_id']: day['day'] for day in outcome['days'] for item in day['lower_orders']}
    assert lower.get('A_old') == '2026-11-12'
    assert lower.get('B_old') == '2026-11-23'
    assert outcome['lower_bound'] == 0
    # No assignment ever precedes the payment day.
    for day in outcome['days']:
        for item in day['lower_orders'] + day['upper_orders']:
            assert item['checkout_day'] <= day['day']
    print('ok allocations never precede payment and honor the seven-day window')


if __name__ == '__main__':
    test_demo_bounds_and_split_guardrail()
    test_late_refunds_consume_slots()
    test_refund_before_payment_rejected()
    test_identified_order_takes_no_anonymous_slot()
    test_duplicate_finance_detail_rejected()
    test_identified_b_refund_is_fixed_in_both_bounds()
    test_identified_mismatch_rejected()
    test_oversubscribed_ledger_rejected()
    test_both_endpoints_pass_and_fail()
    test_without_finance_ledger_keeps_original_behavior()
    test_assignment_respects_payment_day_and_window()
    print('\nall reconciliation tests passed')
