import json
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2] / 'demo'
BUSINESS = json.loads((DEMO / 'business.json').read_text(encoding='utf-8'))
TYPES = {'str': 'string', 'float': 'number', 'choice': 'select'}


def api_fields(items):
    return [{**item, 'type': TYPES.get(item['type'], item['type'])} for item in items]


business = BUSINESS
ledger, run, result, delivery = business['pages']
method = business['method']
SPEC = {
    'title': business['title'], 'short_name': 'hxwl-solo2611-3', 'subtitle': business['description'],
    'organization': business['team'], 'workflow': business['description'], 'pains': business['pain_points'],
    'steps': business['script'], 'methodology': method if isinstance(method, list) else [method],
    'sample_name': business['dataset'], 'default_dataset': business['dataset'].removesuffix('.csv'),
    'finance_sample': business.get('finance_sample'),
    'finance_help': business.get('finance_help'),
    'entity': ledger, 'run': run, 'decision': '业务处理决定', 'action': '保存复核决定',
    'decision_guide': business['decision'], 'recipients': business['recipients'],
    'pages': ['值班概览', ledger, run, result, '业务复核', delivery],
    'fields': api_fields(business['fields']),
    'finance_fields': api_fields(business.get('finance_fields', [])),
    'params': api_fields(business['parameters']),
    'scheduled': True, 'schedule_date_param': business.get('schedule_date_param'),
    'strict_test_params': business.get('strict_test_params'),
}
