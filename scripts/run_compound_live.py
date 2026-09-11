"""执行复合题并保留原始响应；不以HTTP状态替代质量评分。"""
import json
import sys
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

root = Path(__file__).resolve().parents[1]
number = int(sys.argv[1]) if len(sys.argv) > 1 else 1
sections = (root / 'docs/V2复合问题压力测试.md').read_text(encoding='utf-8').split('\n## ')
section = next(x for x in sections if x.startswith(f'{number}.'))
query = section.split('\n\n')[1]
variant = '--variant' in sys.argv
if variant:
    query = query.replace('FY2021—FY2023', 'FY2021—2023').replace('FY2022、FY2023、FY2024', 'FY2022—2024')
    query = '请按原始数据、计算、解释分别回答，不能遗漏子问题。' + query
request = Request('http://127.0.0.1:8001/api/rag/query',
    data=json.dumps({'query': query, 'generate': True, 'mode': 'hybrid', 'top_k': 8}).encode(),
    headers={'Content-Type': 'application/json'})
started = time.monotonic()
try:
    with urlopen(request, timeout=600) as response:
        result = json.load(response)
except HTTPError as exc:
    detail = exc.read().decode('utf-8', errors='replace')
    failure = {'status': exc.code, 'detail': detail, 'elapsed_seconds': round(time.monotonic()-started, 2)}
    failure_path = root / 'data/evaluation' / f'compound_live_{number}.failed_run.json'
    failure_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(failure, ensure_ascii=False))
    raise SystemExit(1)
elapsed = round(time.monotonic() - started, 2)
dest = root / 'data/evaluation' / (f'compound_live_{number}' + ('_variant' if variant else ''))
archive = root / 'data/evaluation/compound_runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
archive.mkdir(parents=True, exist_ok=True)
archive.joinpath(f'case_{number}.json').write_text(json.dumps({'elapsed_seconds': elapsed, 'response': result}, ensure_ascii=False, indent=2), encoding='utf-8')
dest.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
dest.with_suffix('.md').write_text(query + '\n\n' + result['answer'], encoding='utf-8')
print(json.dumps({'answerable': result['answerable'], 'status': result['generation']['status'],
    'removed_claim_count': result['generation'].get('removed_claim_count', 0),
    'finish_reason': result['generation'].get('finish_reason'),
    'evidence_count': len(result['evidence']), 'elapsed_seconds': elapsed, 'artifact': str(dest)}, ensure_ascii=False))
if number == 1:
    expected_rows = {
        '总净销售额': ['394328','383285','391035'],
        '营业利润': ['119437','114301','123216'],
        '净利润': ['99803','96995','93736'],
        '经营活动现金流': ['122151','110543','118254'],
        '购建固定资产': ['10708','10959','9447'],
    }
    lines = result['answer'].replace(',', '').splitlines()
    row_checks = {label: any(label in line and all(n in line for n in values) for line in lines)
                  for label, values in expected_rows.items()}
    audit = {'numeric_rows': row_checks, 'calculation_lines': result['answer'].count('计算：'),
             'unremoved_answer': result['generation']['status'] == 'generated',
             'manual_review_required': ['表格行或标题来源绑定', '年份列映射和单位', '产品合计公式', '所得税归因', '增长利润现金三方面结论', '财年周数差异'],
             'note': '这些检查只作自动筛查，不代表整题语义通过。'}
    dest.with_suffix('.audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False))
elif number == 2:
    values = ['53915', '63364', '69274', '59728', '74965', '87907', '54445', '59941', '54734',
              '24351', '29690', '34189', '26471', '33203', '37884', '19094', '20490', '16450']
    body = result['answer'].replace(',', '')
    audit = {'missing_comparable_values': [v for v in values if not re.search(r'(?<!\d)' + v + r'(?!\d)', body)],
             'unremoved_answer': result['generation']['status'] == 'generated',
             'manual_review_required': ['18项数值与分部和年份映射', '统一CAGR起点口径', '独立合计与公司金额勾稽', '单位换算与敏感性', '分部与Azure定义'],
             'note': '数值存在只是筛查，不能替代语义验收。'}
    dest.with_suffix('.audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False))
