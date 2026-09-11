import unittest
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from .retrieval_policy import identifiers, contains_identifiers, deduplicate
from .retrieve import BM25Index, QdrantRetriever
from rag_api.rerank import SemanticReranker
from rag_api.settings import Settings
from rag_api.service import RAGService


class HybridPolicyTests(unittest.TestCase):
    def test_growth_comparison_queries_keep_entities_separate(self):
        queries = RAGService._augment_task_queries(request_query='compare growth', task={
            'requirement': {'metrics': ['Widget revenue growth rate', 'Cloud segment revenue growth rate']},
            'queries': ['Widget versus Cloud explanation']})
        self.assertEqual(queries[:2], ['Widget revenue grew increased', 'Cloud revenue grew increased'])
        self.assertLessEqual(len(queries), 4)

    def test_exact_identifier_boundaries(self):
        self.assertEqual(identifiers('ERR_5032 v2.1 Form 10-K'), ['ERR_5032', 'v2.1'])
        self.assertFalse(contains_identifiers('ERR_50320', ['ERR_5032']))
        index = BM25Index([{'chunk_id': 'a', 'chunk_text': 'ERR_5032 export failed'},
                           {'chunk_id': 'b', 'chunk_text': 'ERR_5039 export failed'},
                           {'chunk_id': 'c', 'chunk_text': 'export failed'}])
        self.assertEqual([h['chunk_id'] for h in index.search('ERR_5032 export')], ['a'])
        self.assertEqual(index.search('ERR_9999 export'), [])

    def test_rewrite_cannot_change_identifier(self):
        queries = RAGService._augment_task_queries(request_query='ERR_5032 导出失败',
            task={'requirement': {'topic': 'ERR_5032 导出失败'}, 'queries': ['ERR_5039 export failure']})
        self.assertTrue(all('ERR_5032' in q for q in queries))

    def test_near_duplicates_preserve_numerical_and_document_differences(self):
        base = ' '.join('word' + chr(97 + i % 26) for i in range(80))
        def hit(key, doc, amount, suffix=''):
            return {'chunk_id': key, 'text': base + ' revenue ' + amount + suffix,
                    'citation': {'document_id': doc, 'unit': 'USD'}, '_retrieval_task_ids': [key]}
        result, removed = deduplicate([hit('a', 'doc', '100'), hit('b', 'doc', '100', ' end'),
                                       hit('c', 'doc', '101'), hit('d', 'other', '100')])
        self.assertEqual([h['chunk_id'] for h in result], ['a', 'c', 'd'])
        self.assertEqual(result[0]['_retrieval_task_ids'], ['a', 'b'])
        self.assertEqual(len(removed), 1)

    def test_two_recall_branches_run_in_parallel(self):
        barrier = Barrier(2, timeout=2)
        def dense(query, **kwargs):
            barrier.wait()
            return [{'chunk_id': 'a', 'rank': 1, 'score': .8, 'payload': {'chunk_id': 'a', 'chunk_text': 'text'}}]
        def sparse(query, **kwargs):
            barrier.wait()
            return [{'chunk_id': 'a', 'rank': 1, 'score': 100, 'payload': {'chunk_id': 'a', 'chunk_text': 'text'}}]
        retriever = object.__new__(QdrantRetriever)
        retriever.search = dense
        retriever.bm25 = SimpleNamespace(search=sparse, table_matrices={})
        hits = retriever.search_hybrid('test')
        self.assertEqual(hits[0]['dense_rank'], 1)
        self.assertEqual(hits[0]['bm25_rank'], 1)
        self.assertIn('parallel_recall_ms', retriever.last_hybrid_meta)

    def test_rerank_permutation_validation(self):
        self.assertEqual(SemanticReranker.validate('{"order":[1,0]}', 2), [1,0])
        for bad in ('{"order":[0,0]}', '{"order":[0,9]}', '{"order":[true,0]}'):
            with self.assertRaises(ValueError):
                SemanticReranker.validate(bad, 2)

    def test_rerank_timeout_fallback_and_cap(self):
        settings = Settings(semantic_rerank_candidates=10, semantic_rerank_trigger=2)
        hits = [{'chunk_id': str(i), 'text': 'evidence'} for i in range(30)]
        fake = MagicMock()
        fake.__enter__.return_value = fake
        fake.chat.completions.create.side_effect = TimeoutError()
        with patch('openai.OpenAI', return_value=fake) as constructor:
            ranked, meta = SemanticReranker(settings).rank('test', hits)
        self.assertIs(ranked, hits)
        self.assertEqual(meta['status'], 'fallback')
        self.assertEqual(meta['candidate_count'], 10)
        self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)
        self.assertLessEqual(constructor.call_args.kwargs['timeout'], 8)
        self.assertEqual(SemanticReranker(settings).rank('test', hits[:2])[1]['status'], 'not_triggered')

    def test_valid_rerank_preserves_tail_and_ids(self):
        settings = Settings(semantic_rerank_candidates=3, semantic_rerank_trigger=2)
        hits = [{'chunk_id': str(i), 'text': 'evidence'} for i in range(5)]
        fake = MagicMock()
        fake.__enter__.return_value = fake
        fake.chat.completions.create.return_value = SimpleNamespace(usage=None, choices=[SimpleNamespace(
            finish_reason='stop', message=SimpleNamespace(content='{"order":[2,0,1]}'))])
        with patch('openai.OpenAI', return_value=fake):
            ranked, meta = SemanticReranker(settings).rank('test', hits)
        self.assertEqual([h['chunk_id'] for h in ranked], ['2','0','1','3','4'])
        self.assertEqual(meta['status'], 'applied')

    def test_scope_filters_equal_on_both_parallel_branches(self):
        seen = []
        def branch(query, **kwargs):
            seen.append(kwargs['filters'])
            return []
        retriever = object.__new__(QdrantRetriever)
        retriever.search = branch
        retriever.bm25 = SimpleNamespace(search=branch, table_matrices={})
        scope = {'company_id': 'apple', 'document_id': 'doc'}
        self.assertEqual(retriever.search_hybrid('query', filters=scope), [])
        self.assertEqual(seen, [scope, scope])


if __name__ == '__main__':
    unittest.main()
