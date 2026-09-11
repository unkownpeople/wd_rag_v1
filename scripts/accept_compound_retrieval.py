"""复合题仅验收实际API召回，不把生成层错误归给检索层。"""
import json
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    1: ['394328','383285','391035','119437','114301','123216','99803','96995','93736',
        '122151','110543','118254','10708','10959','9447'],
    2: ['53915','63364','69274','59728','74965','87907','54445','59941','54734',
        '24351','29690','34189','26471','33203','37884','19094','20490','16450'],
}


def main():
    sections=(ROOT/'docs/V2复合问题压力测试.md').read_text(encoding='utf-8').split('\n## ')
    rows=[]
    for number, values in EXPECTED.items():
        question=next(s for s in sections if s.startswith(f'{number}.')).split('\n\n')[1]
        begin=time.perf_counter()
        request=Request('http://127.0.0.1:3000/api/rag/query', data=json.dumps({
            'query':question,'generate':False,'plan_retrieval':True,'mode':'hybrid','top_k':8}).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=180) as response:
            result=json.load(response)
        evidence='\n'.join(e['text'] for e in result['evidence']).replace(',','')
        missing=[v for v in values if not re.search(r'(?<!\d)'+v+r'(?!\d)',evidence)]
        row={'case':number,'elapsed_seconds':round(time.perf_counter()-begin,2),
             'missing_values':missing,'request_id':result['generation'].get('request_id'),
             'status':result['generation']['status'],'response':result}
        rows.append(row)
        print(json.dumps({k:v for k,v in row.items() if k!='response'},ensure_ascii=False),flush=True)
        (ROOT/'data/evaluation/compound_retrieval_acceptance.json').write_text(json.dumps({
            'note':'数值存在筛查，不等价于行列语义和最终答案正确','results':rows},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
