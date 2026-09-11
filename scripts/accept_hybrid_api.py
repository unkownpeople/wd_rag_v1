"""实际API混合检索验收：与网页使用同一Service路径，只跳过答案生成。"""
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen
from evaluate_hybrid_module import CASES, relevant

ROOT = Path(__file__).resolve().parents[1]


def main():
    chunks = [json.loads(line) for path in (ROOT / 'data/processed/annual_reports/v1_single_company_chunks').glob('*.chunks.jsonl')
              for line in path.read_text(encoding='utf-8').splitlines()]
    labels = {name: [c['chunk_id'] for c in chunks if c.get('company_id') == company and c.get('fiscal_year') == year
                     and relevant(str(c.get('chunk_text') or ''), terms, values)]
              for name, group, company, year, question, terms, values in CASES}
    report = {'status': 'running', 'label_method': '固定术语和原文金额的本地代理标签，非独立人工标注',
              'gold_chunk_ids': labels, 'results': []}
    path = ROOT / 'data/evaluation/hybrid_api_acceptance.json'
    # 两次重复；保留失败，不修改题目或标签。
    for repeat in range(2):
        for name, group, company, year, question, terms, values in CASES:
            begin = time.perf_counter()
            payload = {'query': question, 'company_id': company, 'fiscal_year': year,
                       'generate': False, 'plan_retrieval': True, 'top_k': 8, 'mode': 'hybrid'}
            try:
                request = Request('http://127.0.0.1:3000/api/rag/query', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
                with urlopen(request, timeout=180) as response:
                    result = json.load(response)
                ids = [e['citation']['chunk_id'] for e in result['evidence']]
                rank = next((i for i, cid in enumerate(ids, 1) if cid in labels[name]), 0)
                row = {'case': name, 'repeat': repeat, 'group': group, 'rank': rank,
                       'hit_at_5': bool(rank and rank<=5), 'mrr_at_10': 1/rank if 0<rank<=10 else 0,
                       'request_id': result['generation'].get('request_id'),
                       'status': result['generation']['status'],
                       'rerank': result['retrieval']['planner'].get('semantic_rerank', {}),
                       'coverage': result['retrieval']['planner'].get('coverage_check', {})}
            except Exception as exc:
                row = {'case': name, 'repeat': repeat, 'group': group, 'rank': 0, 'hit_at_5': False,
                       'mrr_at_10': 0, 'status': 'error', 'error_type': type(exc).__name__}
            row['elapsed_ms'] = round((time.perf_counter()-begin)*1000, 2)
            report['results'].append(row)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(name, repeat, row['rank'], row['status'], flush=True)
    rows=report['results']
    report['status']='completed'
    report['summary']={'n':len(rows),'hit_at_5':sum(r['hit_at_5'] for r in rows),
        'mrr_at_10':sum(r['mrr_at_10'] for r in rows)/len(rows),
        'failures':[{'case':r['case'],'repeat':r['repeat'],'rank':r['rank']} for r in rows if not r['hit_at_5']],
        'elapsed_ms_range':[min(r['elapsed_ms'] for r in rows),max(r['elapsed_ms'] for r in rows)]}
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False))


if __name__ == '__main__':
    main()
