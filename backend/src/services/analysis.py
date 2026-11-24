"""Analyze randomized checkout assignments at a fixed end-of-day snapshot."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from math import comb, exp, fsum, lgamma, log, log1p


REFUND_WINDOW_DAYS = 7


@dataclass
class Cohort:
    assigned: int = 0
    paid: int = 0
    mature_paid: int = 0
    mature_refunds: int = 0
    known_refunds: int = 0
    late_refunds: int = 0

    @property
    def pending(self):
        return self.paid - self.mature_paid

    @property
    def conversion_pct(self):
        return Fraction(100 * self.paid, self.assigned)

    @property
    def mature_refund_pct(self):
        if self.mature_paid:
            return Fraction(100 * self.mature_refunds, self.mature_paid)
        return None


def _iso_day(value, label):
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}须使用 YYYY-MM-DD 格式的有效日期。') from None
    if result.isoformat() != value:
        raise ValueError(f'{label}须使用 YYYY-MM-DD 格式的有效日期。')
    return result


def _number(params, key, default, label):
    try:
        value = params.get(key, default)
        if isinstance(value, bool):
            raise ValueError
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        raise ValueError(f'{label}须为有限数值。') from None


def _cohorts(rows, snapshot):
    cohorts = {arm: Cohort() for arm in ('A', 'B')}
    seen = set()
    excluded = 0
    for index, row in enumerate(rows, start=1):
        label = f'第 {index} 条记录'
        try:
            reader_id, variant, paid = (row[key] for key in ('reader_id', 'variant', 'paid'))
            checkout_day = _iso_day(row['checkout_day'], f'{label}的结账日')
            refund_value = row['refund_day']
        except (TypeError, KeyError) as error:
            raise ValueError(f'{label}缺少结账台账的必填字段。') from error
        if not isinstance(reader_id, str) or not reader_id.strip():
            raise ValueError(f'{label}缺少有效的读者编号。')
        reader_id = reader_id.strip()
        if reader_id in seen:
            raise ValueError(f'{label}的读者 {reader_id} 重复入组，每位读者只能出现一次。')
        seen.add(reader_id)
        if variant not in ('A', 'B'):
            raise ValueError(f'{label}的报价组须为 A 或 B。')
        if isinstance(paid, bool) or paid not in (0, 1, '0', '1'):
            raise ValueError(f'{label}的支付标记须为 0 或 1。')
        paid = int(paid)
        refund_day = None
        if refund_value not in ('-', ''):
            refund_day = _iso_day(refund_value, f'{label}的退款日')
            if not paid:
                raise ValueError(f'{label}未支付却有退款日期。')
            if refund_day < checkout_day:
                raise ValueError(f'{label}的退款日早于结账日。')

        if checkout_day > snapshot:
            excluded += 1
            continue
        group = cohorts[variant]
        group.assigned += 1
        group.paid += paid
        mature = (snapshot - checkout_day).days >= REFUND_WINDOW_DAYS
        if paid and mature:
            group.mature_paid += 1
        if refund_day is not None and refund_day <= snapshot:
            if (refund_day - checkout_day).days <= REFUND_WINDOW_DAYS:
                group.known_refunds += 1
                group.mature_refunds += int(mature)
            else:
                group.late_refunds += 1
    return cohorts, excluded


def _fisher_two_sided(a_paid, a_total, b_paid, b_total):
    """Sum exact hypergeometric weights at or below the observed weight."""
    paid = a_paid + b_paid
    first = max(0, paid - b_total)
    last = min(a_total, paid)
    observed_weight = comb(a_total, a_paid) * comb(b_total, b_paid)
    weight = comb(a_total, first) * comb(b_total, paid - first)
    extreme_weight = 0
    for count in range(first, last + 1):
        if weight <= observed_weight:
            extreme_weight += weight
        if count < last:
            numerator = weight * (a_total - count) * (paid - count)
            denominator = (count + 1) * (b_total - paid + count + 1)
            weight = numerator // denominator
    return Fraction(extreme_weight, comb(a_total + b_total, paid))


def _refund_upper_pct(refunds, paid):
    """Single-sided 95% Clopper–Pearson upper bound via binomial tail inversion."""
    if not paid:
        return None
    if refunds == paid:
        return 100.0
    log_factorial = lgamma(paid + 1)
    log_coefficients = [
        log_factorial - lgamma(count + 1) - lgamma(paid - count + 1)
        for count in range(refunds + 1)
    ]
    low, high = 0.0, 1.0
    for _ in range(53):
        probability = (low + high) / 2
        if probability == low or probability == high:
            break
        log_p = log(probability)
        log_q = log1p(-probability)
        lower_tail = fsum(
            exp(coefficient + count * log_p + (paid - count) * log_q)
            for count, coefficient in enumerate(log_coefficients)
        )
        if lower_tail > 0.05:
            low = probability
        else:
            high = probability
    return 100 * high


def _percent(value):
    return f'{float(value):.2f}%' if value is not None else '待观察'


def _rounded(value):
    return round(float(value), 2) if value is not None else None


def analyze(rows, params):
    snapshot = _iso_day(params.get('snapshot_date', '2026-11-23'), '复盘截止日')
    minimum = _number(params, 'min_per_arm', 30, '每组最少入组人数')
    alpha = _number(params, 'alpha', 0.05, '转化检验显著性水平')
    lift_floor = _number(params, 'min_lift_pp', 10, 'B 最小转化提升')
    refund_cap = _number(params, 'max_refund_pct', 15, 'B 七天退款率上限')
    if minimum < 1 or minimum.denominator != 1:
        raise ValueError('每组最少入组人数须为正整数。')
    if not 0 < alpha < 1:
        raise ValueError('转化检验显著性水平须大于 0 且小于 1。')
    if not 0 <= lift_floor <= 100 or not 0 <= refund_cap <= 100:
        raise ValueError('转化提升门槛和退款率上限须在 0 到 100 之间。')

    cohorts, excluded = _cohorts(rows, snapshot)
    a, b = cohorts['A'], cohorts['B']
    if not a.assigned or not b.assigned:
        raise ValueError('复盘截止日须至少有一位 A 组读者和一位 B 组读者入组。')
    lift = b.conversion_pct - a.conversion_pct
    p_value = _fisher_two_sided(a.paid, a.assigned, b.paid, b.assigned)
    minimum_refund_pct = Fraction(100 * b.known_refunds, b.paid) if b.paid else None
    refund_upper = _refund_upper_pct(b.mature_refunds, b.mature_paid)
    sample_ok = min(a.assigned, b.assigned) >= minimum
    significant = p_value <= alpha
    lift_ok = lift > 0 and lift >= lift_floor
    breached = minimum_refund_pct is not None and minimum_refund_pct > refund_cap
    refund_ok = b.pending == 0 and refund_upper is not None and refund_upper <= float(refund_cap)

    refund_evidence = (
        f'B 成熟 {b.mature_paid}/{b.paid} 笔，待满七天 {b.pending} 笔，'
        f'七天内已知退款 {b.known_refunds} 笔，成熟退款率 {_percent(b.mature_refund_pct)}，'
        f'单侧 95% 上界 {_percent(refund_upper)}，护栏上限 {_percent(refund_cap)}。'
    )
    if breached:
        action = '暂停 B，恢复 A，并由客服质检核对退款原因'
        refund_status = f'已触发，已知退款下限 {_percent(minimum_refund_pct)}'
    else:
        if b.pending:
            refund_status = '待确认，仍有支付未满七天'
        elif refund_upper is None:
            refund_status = '待确认，缺少 B 支付样本'
        else:
            refund_status = '通过' if refund_ok else '待确认，退款上界超限'
        if sample_ok and significant and not lift_ok:
            action = '保留 A，结束本轮报价试点'
        elif sample_ok and significant and lift_ok and refund_ok:
            action = '安排 B 覆盖 25% 的全站合格结账流量'
        else:
            action = '维持试点范围，待预定复盘日补齐证据'

    return {
        'summary': (
            f'建议{action}。A 转化 {a.paid}/{a.assigned}（{_percent(a.conversion_pct)}），'
            f'B 转化 {b.paid}/{b.assigned}（{_percent(b.conversion_pct)}），'
            f'B 相对 A {float(lift):+.2f} 个百分点，双侧 Fisher p = {float(p_value):.6g}。'
            f'{refund_evidence}'
        ),
        'metrics': [
            {'label': '随机入组读者', 'value': a.assigned + b.assigned,
             'detail': f'截至 {snapshot.isoformat()} 日终，截止日后入组 {excluded} 人'},
            {'label': 'B 转化提升（百分点）', 'value': _rounded(lift),
             'detail': f'A {_percent(a.conversion_pct)}，B {_percent(b.conversion_pct)}'},
            {'label': '双侧 Fisher p 值', 'value': float(p_value),
             'detail': f'预设显著性水平 {float(alpha):.6g}'},
            {'label': 'B 七天退款率上界（%）', 'value': _rounded(refund_upper),
             'detail': f'单侧 95% 上界，成熟支付 {b.mature_paid} 笔，待满七天 {b.pending} 笔'},
        ],
        'tables': [
            {
                'title': '按报价组核对转化和退款分母',
                'columns': ['报价组', '入组', '支付', '未支付', '转化率（%）', '成熟支付',
                            '待满七天', '七天内已知退款', '成熟订单退款', '成熟退款率（%）', '窗口外退款'],
                'rows': [
                    [arm, c.assigned, c.paid, c.assigned - c.paid, _rounded(c.conversion_pct),
                     c.mature_paid, c.pending, c.known_refunds, c.mature_refunds,
                     _rounded(c.mature_refund_pct), c.late_refunds]
                    for arm, c in cohorts.items()
                ],
            },
            {
                'title': '预设决策门槛',
                'columns': ['条件', '实测证据', '预设门槛', '结果'],
                'rows': [
                    ['样本量', f'A {a.assigned} 人，B {b.assigned} 人', f'每组 ≥ {minimum} 人',
                     '通过' if sample_ok else '人数不足'],
                    ['转化检验', float(p_value), f'p ≤ {float(alpha):.6g}',
                     '通过' if significant else '证据不足'],
                    ['最小转化提升（百分点）', _rounded(lift),
                     f'正向且 ≥ {float(lift_floor):g}', '通过' if lift_ok else '未达门槛'],
                    ['B 七天退款护栏', refund_evidence,
                     f'成熟覆盖全部支付，单侧上界 ≤ {_percent(refund_cap)}', refund_status],
                    ['后续行动', action, '执行方', '发行运营组、订阅客服质检组'],
                ],
            },
        ],
        'notes': [
            '转化分母为随机入组读者，退款订单保留在支付计数。Fisher 检验使用固定边际的双侧概率，最小提升门槛按观测转化差判断。',
            '退款窗口为北京时间结账日到第七天日终。未满七天的支付计为待观察，窗口外退款单列。',
            '先检查 B 已知退款下限是否超限，再结合样本、转化和成熟退款上界决定下一步。',
            '按预定复盘日和阈值判断转化证据，持续跟踪续订与单位收入。',
        ],
        'series': [
            {'label': f'{arm} 结账转化率（%）', 'value': _rounded(cohort.conversion_pct)}
            for arm, cohort in cohorts.items()
        ],
    }
