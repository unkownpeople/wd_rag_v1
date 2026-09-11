"""与模型无关的标识保护、保守近重复合并和排名诊断。"""
import re
import unicodedata

IDENTIFIER = re.compile(r'(?<![\w])(?:[A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)+|[vV]\d+(?:\.\d+){1,3})(?![\w])')


def identifiers(text):
    return list(dict.fromkeys(m.group() for m in IDENTIFIER.finditer(text)
                             if any(c.isdigit() for c in m.group()) and m.group().lower() not in {'10-k', '10-q'}))


def contains_identifiers(text, required):
    return all(re.search(r'(?<![\w])' + re.escape(value) + r'(?![\w])', text, re.I) for value in required)


def deduplicate(hits, threshold=0.9):
    """仅同文档同范围且数字序列相同的近重复可合并；不吞掉跨年差异。"""
    selected, fingerprints, removed = [], [], []
    for hit in hits:
        p = hit.get('payload') or {}
        c = hit.get('citation') or p
        text = unicodedata.normalize('NFKC', str(hit.get('text') or p.get('chunk_text') or '')).lower()
        tokens = re.findall(r'\w+|[^\w\s]', text)
        shingles = {tuple(tokens[i:i + 4]) for i in range(max(len(tokens) - 3, 0))}
        numbers = re.findall(r'-?\d+(?:[,.]\d+)*', text)
        scope = tuple(c.get(k) for k in ('document_id', 'source_revision', 'statement_scope', 'unit'))
        duplicate = None
        for index, (old_scope, old_numbers, old_shingles, old_text) in enumerate(fingerprints):
            similarity = len(shingles & old_shingles) / max(len(shingles | old_shingles), 1)
            if text and scope[0] and scope == old_scope and numbers == old_numbers and (text == old_text or len(shingles) >= 20 and similarity >= threshold):
                duplicate = index
                break
        if duplicate is None:
            selected.append(hit)
            fingerprints.append((scope, numbers, shingles, text))
        else:
            kept = selected[duplicate]
            kept['_retrieval_task_ids'] = list(dict.fromkeys(kept.get('_retrieval_task_ids', []) + hit.get('_retrieval_task_ids', [])))
            removed.append({'removed': hit.get('chunk_id'), 'kept': kept.get('chunk_id')})
    return selected, removed


def ranking(hits):
    return [{'chunk_id': h.get('chunk_id'), 'position': i, 'dense_rank': h.get('dense_rank'),
             'bm25_rank': h.get('bm25_rank'), 'rrf_score': h.get('rrf_score')}
            for i, h in enumerate(hits, 1)]
