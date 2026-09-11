"""为分部财务任务补充带年份列的可比表，保留原文与跨块来源。"""
import re


def requested_years(text):
    years = {int(x) for x in re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', text)}
    for a, b in re.findall(r'((?:19|20)\d{2})\s*[-—–至到~]\s*(?:FY\s*)?((?:19|20)\d{2})', text, re.I):
        if 0 < int(b) - int(a) <= 10:
            years.update(range(int(a), int(b) + 1))
    return years


def is_segment_table(text):
    return bool(re.search(r'segment', text, re.I) and
                re.search(r'revenue', text, re.I) and
                re.search(r'operating\s+income|operating\s+profit', text, re.I) and
                len(re.findall(r'\d{1,3}(?:,\d{3})+', text)) >= 6)


def segment_comparatives(payloads, task, filters, question):
    requirement = task.get('requirement') or {}
    topic = ' '.join(str(requirement.get(key) or '') for key in ('topic', 'metrics', 'group_by'))
    if re.search(r'原因|区别|不同|不可|互换|definition', topic, re.I) and not re.search(r'逐年|列示|CAGR|营业利润', topic, re.I):
        return []
    if not re.search(r'分部|segment', topic, re.I) or not re.search(r'收入|利润|增长|revenue|income|profit|CAGR|margin', topic, re.I):
        return []
    # 沿用原请求全部范围约束，不能因补检索越过文档或公司边界。
    scoped = [p for p in payloads.values() if all(v is None or p.get(k) == v for k, v in filters.items())]
    by_position = {(p.get('document_id'), p.get('chunk_index')): p for p in scoped}
    years = requested_years(question)
    candidates = []
    for original in scoped:
        text = str(original.get('chunk_text') or '')
        if not is_segment_table(text):
            continue
        p = dict(original)
        context_ids = [p['chunk_id']]
        # 文本表跨块时，上一块的末尾指标标题属于下一块的数值行。
        if re.search(r'operating\s+(?:income|profit)\s*$', text, re.I):
            index = p.get('chunk_index')
            neighbor = by_position.get((p.get('document_id'), index + 1)) if isinstance(index, int) else None
            if neighbor and re.search(r'\d{1,3}(?:,\d{3})+', str(neighbor.get('chunk_text') or '')):
                text += '\n\n' + str(neighbor['chunk_text'])
                context_ids.append(neighbor['chunk_id'])
                p['page_end'] = neighbor.get('page_end') or p.get('page_end')
        p['chunk_text'] = text
        p['context_chunk_ids'] = context_ids
        covered = {int(y) for y in p.get('period_years') or []} & years
        p['comparison_years'] = sorted(covered)
        candidates.append(p)
    candidates.sort(key=lambda p: (len(p['comparison_years']), int(p.get('fiscal_year') or 0), len(p['context_chunk_ids'])), reverse=True)
    # 完整期间的同源比较表优先；没有完整表时保留多个财年的候选供明确缺口。
    if candidates and years and set(candidates[0]['comparison_years']) == years:
        best = candidates[0]
        return [best] + [p for p in candidates[1:] if p.get('document_id') == best.get('document_id')][:1]
    return candidates[:3]
