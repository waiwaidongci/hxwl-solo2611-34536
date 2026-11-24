from src.domain.spec import DEMO


def demo_rows():
    from src.services.validation import parse_rows
    return parse_rows((DEMO / 'sample.csv').read_text(encoding='utf-8-sig'))


def demo_finance_rows():
    from src.services.validation import parse_finance_rows
    return parse_finance_rows((DEMO / 'finance_ledger.csv').read_text(encoding='utf-8-sig'))
