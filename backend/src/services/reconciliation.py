"""Exact bounds for anonymous refunds by reconciling the locked finance daily ledger.

Finance only supplies a daily refund *total*.  Refunds carrying an order id are
deducted first, per finance day.  The remaining anonymous slots are allocated to
paid orders with each order used at most once and never earlier than its payment
day.  A minimum-cost / maximum-cost flow produces the exact lower and upper bound
on how many anonymous slots must / can land on B mature orders inside their
seven-day refund window.  Refunds outside the window are still consumed as slots
and therefore participate fully in the reconciliation.

The returned allocations are concrete order-to-day assignments so every finance
day can be reconciled line by line.  When the ledger cannot be fully allocated
the run is rejected (see :class:`ReconciliationError`).
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import date


REFUND_WINDOW_DAYS = 7


class ReconciliationError(ValueError):
    """Raised when the locked finance ledger cannot be reconciled to the checkout ledger."""


@dataclass
class _Edge:
    target: int
    reverse: int
    capacity: int
    cost: int
    flow: int = 0


class _MinCostFlow:
    """Small min-cost max-flow over integer capacities using SPFA shortest paths."""

    def __init__(self, size):
        self.graph = [[] for _ in range(size)]

    def add_edge(self, source, target, capacity, cost):
        forward = _Edge(target, len(self.graph[target]), capacity, cost)
        backward = _Edge(source, len(self.graph[source]), 0, -cost)
        self.graph[source].append(forward)
        self.graph[target].append(backward)
        return forward

    def flow(self, source, sink, required):
        total = 0
        size = len(self.graph)
        while total < required:
            distance = [None] * size
            previous_node = [-1] * size
            previous_edge = [-1] * size
            distance[source] = 0
            queue = deque([source])
            in_queue = [False] * size
            in_queue[source] = True
            while queue:
                node = queue.popleft()
                in_queue[node] = False
                for edge_index, edge in enumerate(self.graph[node]):
                    if edge.capacity <= 0:
                        continue
                    candidate = distance[node] + edge.cost
                    if distance[edge.target] is None or candidate < distance[edge.target]:
                        distance[edge.target] = candidate
                        previous_node[edge.target] = node
                        previous_edge[edge.target] = edge_index
                        if not in_queue[edge.target]:
                            queue.append(edge.target)
                            in_queue[edge.target] = True
            if distance[sink] is None:
                break
            node = sink
            while node != source:
                edge = self.graph[previous_node[node]][previous_edge[node]]
                edge.capacity -= 1
                reverse = self.graph[node][edge.reverse]
                reverse.capacity += 1
                edge.flow += 1
                reverse.flow -= 1
                node = previous_node[node]
            total += 1
        return total


@dataclass
class Order:
    reader_id: str
    variant: str
    checkout_day: date
    mature: bool

    @property
    def in_window_deadline(self):
        from datetime import timedelta

        return self.checkout_day + timedelta(days=REFUND_WINDOW_DAYS)


@dataclass
class FinanceDay:
    day: date
    total: int
    identified_reader_ids: list = field(default_factory=list)


@dataclass
class DayReconciliation:
    day: str
    finance_total: int
    identified_count: int
    anonymous_slots: int
    identified_orders: list
    lower_orders: list
    upper_orders: list


def _parse_day(value, label):
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ReconciliationError(f'{label}须使用 YYYY-MM-DD 格式的有效日期。') from None
    if result.isoformat() != value:
        raise ReconciliationError(f'{label}须使用 YYYY-MM-DD 格式的有效日期。')
    return result


def _is_b_mature_in_window(order, refund_day):
    return (
        order.variant == 'B'
        and order.mature
        and order.checkout_day <= refund_day <= order.in_window_deadline
    )


def _build_orders(rows, snapshot):
    """Paid checkout orders, keyed by reader id, with the same maturity rule as the analysis."""
    orders = {}
    for index, row in enumerate(rows, start=1):
        label = f'第 {index} 条结账记录'
        reader_id = (row.get('reader_id') or '').strip() if isinstance(row.get('reader_id'), str) else ''
        if not reader_id:
            raise ReconciliationError(f'{label}缺少有效的读者编号。')
        if reader_id in orders:
            raise ReconciliationError(f'{label}的读者 {reader_id} 重复，无法按订单对账。')
        if row.get('variant') not in ('A', 'B') or row.get('paid') != 1:
            continue
        checkout_day = _parse_day(row['checkout_day'], f'{label}的结账日')
        if checkout_day > snapshot:
            continue
        mature = (snapshot - checkout_day).days >= REFUND_WINDOW_DAYS
        orders[reader_id] = Order(reader_id, row['variant'], checkout_day, mature)
    return orders


def _build_finance_days(ledger_rows, snapshot, orders, rows):
    """Aggregate the locked finance ledger into per-day totals and identified refund ids."""
    totals = {}
    detail = {}
    known_by_day = {}
    for row in rows:
        refund_value = row.get('refund_day')
        if refund_value in (None, '', '-') or row.get('paid') != 1:
            continue
        refund_day = _parse_day(refund_value, f'读者 {row.get("reader_id")} 的退款日')
        if refund_day <= snapshot:
            known_by_day.setdefault(refund_day, set()).add(row['reader_id'].strip())

    for index, row in enumerate(ledger_rows, start=1):
        label = f'财务日账第 {index} 行'
        day = _parse_day(row['finance_day'], f'{label}的财务日')
        if day > snapshot:
            continue
        count = row['refund_count']
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ReconciliationError(f'{label}的退款总笔数须为非负整数。')
        reader_id = row.get('reader_id')
        if reader_id:
            reader_id = reader_id.strip()
            if reader_id not in orders:
                raise ReconciliationError(f'{label}的单号 {reader_id} 找不到对应支付订单。')
            if orders[reader_id].checkout_day > day:
                raise ReconciliationError(f'{label}的退款早于读者 {reader_id} 的支付日。')
            if reader_id not in known_by_day.get(day, set()):
                raise ReconciliationError(
                    f'{label}的有单号退款 {reader_id} 与结账台账锁定的退款记录不一致。'
                )
            bucket = detail.setdefault(day, set())
            if reader_id in bucket:
                raise ReconciliationError(f'{label}的单号 {reader_id} 在 {day} 重复退款。')
            bucket.add(reader_id)
        else:
            totals[day] = totals.get(day, 0) + count

    days = []
    for day in sorted(set(totals) | set(detail) | set(known_by_day)):
        identified = sorted(detail.get(day, set()))
        identified_from_checkout = sorted(known_by_day.get(day, set()))
        if set(identified) != set(identified_from_checkout):
            raise ReconciliationError(
                f'{day.isoformat()} 的有单号退款与结账台账不一致：'
                f'财务日账 {len(identified)} 笔，结账台账 {len(identified_from_checkout)} 笔。'
            )
        total = totals.get(day)
        if total is None:
            if identified:
                raise ReconciliationError(
                    f'{day.isoformat()} 只有有单号退款，缺少财务每日退款总笔数，无法对账。'
                )
            continue
        if len(identified) > total:
            raise ReconciliationError(
                f'{day.isoformat()} 有单号退款 {len(identified)} 笔，超过财务总笔数 {total}。'
            )
        days.append(FinanceDay(day, total, identified))
    return days


def _solve(orders, finance_days, maximize, excluded_reader_ids):
    """Return (objective_value, order_id -> refund_day) for one extreme of the window count.

    Orders already carrying an identified refund are excluded: every order may be
    refunded at most once, so they cannot absorb an anonymous slot.
    """
    order_list = sorted(
        (order for order in orders.values() if order.reader_id not in excluded_reader_ids),
        key=lambda item: item.reader_id,
    )
    day_list = sorted(finance_days, key=lambda item: item.day)
    source = 0
    order_base = 1
    day_base = order_base + len(order_list)
    sink = day_base + len(day_list)
    network = _MinCostFlow(sink + 1)

    required = 0
    order_edges = {}
    for order_index, order in enumerate(order_list):
        node = order_base + order_index
        network.add_edge(source, node, 1, 0)
        order_edges[order.reader_id] = []
        for day_index, finance_day in enumerate(day_list):
            if order.checkout_day <= finance_day.day:
                in_window = _is_b_mature_in_window(order, finance_day.day)
                cost = -1 if (maximize and in_window) else (1 if (not maximize and in_window) else 0)
                edge = network.add_edge(node, day_base + day_index, 1, cost)
                order_edges[order.reader_id].append((finance_day.day, edge))

    day_edges = []
    for day_index, finance_day in enumerate(day_list):
        slots = finance_day.total - len(finance_day.identified_reader_ids)
        required += slots
        network.add_edge(day_base + day_index, sink, slots, 0)

    achieved = network.flow(source, sink, required)
    if achieved != required:
        raise ReconciliationError(
            f'财务匿名退款 {required} 笔无法全部分配：仅 {achieved} 笔能匹配到合格订单，对账不平，拒绝发布。'
        )

    assignment = {}
    for order in order_list:
        for day, edge in order_edges[order.reader_id]:
            if edge.flow > 0:
                assignment[order.reader_id] = day
                break
    slots_by_day = {
        finance_day.day: finance_day.total - len(finance_day.identified_reader_ids)
        for finance_day in day_list
    }
    flow_by_day = {day: 0 for day in slots_by_day}
    for day in assignment.values():
        flow_by_day[day] += 1
    for day, flow in flow_by_day.items():
        if flow != slots_by_day[day]:
            raise ReconciliationError(f'{day.isoformat()} 分配笔数与财务日账不平，拒绝发布。')

    objective = sum(
        1
        for reader_id, day in assignment.items()
        if _is_b_mature_in_window(orders[reader_id], day)
    )
    return objective, assignment


def reconcile(rows, ledger_rows, snapshot, identified_b_mature_in_window):
    """Reconcile the locked finance ledger and return exact bounds and both extreme allocations.

    ``identified_b_mature_in_window`` counts order-id refunds already known to be
    B mature in-window refunds; it is added to both endpoints because those
    refunds are fixed, not anonymous.
    """
    orders = _build_orders(rows, snapshot)
    finance_days = _build_finance_days(ledger_rows, snapshot, orders, rows)
    if not finance_days:
        raise ReconciliationError('财务日账在复盘截止日前没有任何记录，无法对账。')

    # Orders with an identified (order-id) refund cannot take an anonymous slot.
    excluded_reader_ids = {
        reader_id
        for finance_day in finance_days
        for reader_id in finance_day.identified_reader_ids
    }
    anonymous_total = sum(day.total - len(day.identified_reader_ids) for day in finance_days)
    available_orders = len(orders) - len(excluded_reader_ids)
    if anonymous_total > available_orders:
        raise ReconciliationError(
            f'财务匿名退款 {anonymous_total} 笔超过无退款记录的可分配订单 {available_orders} 个，'
            '对账不平，拒绝发布。'
        )

    lower_anonymous, lower_assignment = _solve(
        orders, finance_days, maximize=False, excluded_reader_ids=excluded_reader_ids
    )
    upper_anonymous, upper_assignment = _solve(
        orders, finance_days, maximize=True, excluded_reader_ids=excluded_reader_ids
    )
    if lower_anonymous > upper_anonymous:
        raise ReconciliationError('退款上下界求解异常，下界大于上界，拒绝发布。')

    lower_bound = identified_b_mature_in_window + lower_anonymous
    upper_bound = identified_b_mature_in_window + upper_anonymous
    if upper_bound > sum(1 for order in orders.values() if order.variant == 'B' and order.mature):
        raise ReconciliationError('B 成熟订单七天内退款上界超过成熟支付数，拒绝发布。')

    day_rows = []
    for finance_day in finance_days:
        identified_orders = [
            {
                'reader_id': reader_id,
                'variant': orders[reader_id].variant,
                'checkout_day': orders[reader_id].checkout_day.isoformat(),
                'in_window_mature_b': _is_b_mature_in_window(orders[reader_id], finance_day.day),
            }
            for reader_id in finance_day.identified_reader_ids
        ]

        def _slot_orders(assignment):
            chosen = [
                reader_id
                for reader_id, day in assignment.items()
                if day == finance_day.day
            ]
            return [
                {
                    'reader_id': reader_id,
                    'variant': orders[reader_id].variant,
                    'checkout_day': orders[reader_id].checkout_day.isoformat(),
                    'in_window_mature_b': _is_b_mature_in_window(orders[reader_id], finance_day.day),
                }
                for reader_id in sorted(chosen)
            ]

        slots = finance_day.total - len(finance_day.identified_reader_ids)
        lower_orders = _slot_orders(lower_assignment)
        upper_orders = _slot_orders(upper_assignment)
        if len(lower_orders) != slots or len(upper_orders) != slots:
            raise ReconciliationError(
                f'{finance_day.day.isoformat()} 逐日分配笔数 {len(lower_orders)}/{len(upper_orders)} '
                f'与剩余笔数 {slots} 不平，拒绝发布。'
            )
        day_rows.append(
            DayReconciliation(
                day=finance_day.day.isoformat(),
                finance_total=finance_day.total,
                identified_count=len(finance_day.identified_reader_ids),
                anonymous_slots=slots,
                identified_orders=identified_orders,
                lower_orders=lower_orders,
                upper_orders=upper_orders,
            )
        )

    return {
        'lower_bound': lower_bound,
        'upper_bound': upper_bound,
        'identified_b_mature_in_window': identified_b_mature_in_window,
        'anonymous_total': anonymous_total,
        'days': [
            {
                'day': item.day,
                'finance_total': item.finance_total,
                'identified_count': item.identified_count,
                'anonymous_slots': item.anonymous_slots,
                'identified_orders': item.identified_orders,
                'lower_orders': item.lower_orders,
                'upper_orders': item.upper_orders,
            }
            for item in day_rows
        ],
    }
