"""Catalog, constrained public search, and latest-task continuations."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from full_app import FullApplication
from full_catalogue import catalogue_request,normalize_search,catalog_page,overview_items
from catalog_labels_zh import CATEGORY_LABELS
from common import WORKSPACE,read_jsonl


class CatalogFollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=WORKSPACE/'cache/tmp');self.addCleanup(self.tmp.cleanup)
        self.app=FullApplication('B',report_root=Path(self.tmp.name),audit=False);self.addCleanup(self.app.close)
        self.s=self.app.session();self.app.conversation=Mock()
        self.app.conversation.route.return_value={'intent':'general','reason':'test_spy'}
        self.app.conversation.reply.return_value='普通话题的接续回答'
    def row(self):return read_jsonl(self.s.out/'results.jsonl')[-1]
    def select(self):self.s.respond('B005FIG90C@amazon.co.jp')
    def test_catalog_questions_use_real_categories(self):
        for q in ['你包含哪些商品？','你们知识库都有哪些品类？','这里可以查哪些商品？','除了鞋子还收录哪些类目？']:
            v=self.s.respond(q);self.assertEqual(v['action'],'catalog');self.assertIn('147,702',v['answer'])
            self.assertIn('SHOES',v['answer']);self.assertIn('12,965',v['answer'])
        self.app.conversation.reply.assert_not_called()
    def test_catalog_continuation_reads_next_categories(self):
        self.select();self.s.respond('你包含哪些商品？');first=self.row()['catalog_categories']
        v=self.s.respond('还有呢');second=self.row()['catalog_categories']
        self.assertEqual(v['action'],'catalog')
        self.assertFalse({r['product_type'] for r in first}&{r['product_type'] for r in second})
    def test_directory_is_bilingual_and_lists_every_source_type_once(self):
        manifest = self.app.retriever.manifest
        self.assertEqual(set(CATEGORY_LABELS), set(manifest['product_types']))
        offset, seen = 0, set()
        while True:
            answer, offset, categories = catalog_page(manifest, offset)
            if not categories:
                break
            for item in categories:
                kind, label = item['product_type'], item['label']
                self.assertNotIn(kind, seen)
                self.assertRegex(label, r'[\u4e00-\u9fff]')
                self.assertIn(f'{label}（{kind}）：', answer)
                self.assertEqual(item['records'], manifest['product_types'][kind])
                seen.add(kind)
        self.assertEqual(seen, set(manifest['product_types']))
        self.assertEqual(offset, 576)

    def test_directory_followups_advance_and_restart(self):
        phrases = (
            ('你有哪些商品可以告诉我呢？', 0, 14),
            ('除了上面这些，还有哪些商品', 14, 28),
            ('再展示没有列过的类目', 28, 42),
            ('下一页', 42, 56),
            ('回到第一页', 0, 14),
        )
        for question, start, end in phrases:
            with self.subTest(question=question):
                response = self.s.respond(question)
                self.assertEqual(response['action'], 'catalog')
                trace = self.row()['retrieval_trace']
                self.assertEqual((trace['category_offset'], trace['next_category_offset']), (start, end))
        self.s.respond('换个话题，给我写首诗')
        self.assertEqual(self.s.respond('还有呢')['action'], 'clarify')

    def test_directory_translation_can_be_used_as_a_category_query(self):
        self.s.respond('你有哪些商品可以告诉我呢？')
        self.assertIn('宠物用品（PET_SUPPLIES）', self.s.respond('除了上面这些，还有哪些商品')['answer'])
        result = self.s.respond('搜索宠物用品')
        self.assertEqual(result['action'], 'search_results')
        self.assertTrue(result['candidates'])
        self.assertEqual({r['product_type'] for r in result['candidates']}, {'PET_SUPPLIES'})

    def test_semantic_catalog_followup_uses_current_page(self):
        self.s.respond('商品列表')
        self.app.conversation.route.return_value = {'intent': 'catalog_next', 'reason': 'test_spy'}
        result = self.s.respond('能多介绍一点吗？')
        self.assertEqual(result['action'], 'catalog')
        self.assertEqual(self.row()['retrieval_trace']['category_offset'], 14)

    def test_shoe_search_normalizes_task_words_and_has_hard_types(self):
        plan=normalize_search('我想查询鞋子类的商品',self.app.retriever.manifest)
        self.assertEqual(plan['query'],'shoes');self.assertNotIn('商品',plan['query'])
        v=self.s.respond('我想查询鞋子类的商品')
        self.assertEqual(v['action'],'search_results')
        self.assertTrue(all(r['product_type'] in {'SHOES','BOOT','SANDAL','TECHNICAL_SPORT_SHOE'} for r in v['candidates']))
    def test_makeup_search_keeps_the_real_catalog_types(self):
        response = self.s.respond('我想查询化妆品')
        self.assertEqual(response['action'], 'search_results')
        self.assertTrue(response['candidates'])
        allowed = {'FACE_SHAPING_MAKEUP', 'MASCARA', 'COSMETIC_POWDER', 'BEAUTY', 'ABIS_BEAUTY'}
        self.assertTrue(all(item['product_type'] in allowed for item in response['candidates']))
    def test_category_family_filters_are_parameterized(self):
        v=self.app.retriever.search('shoes',filters={'product_types':['SHOES','BOOT'],'domain':"x' OR 1=1 --"})
        self.assertEqual(v['items'],[])
        with self.assertRaises(ValueError):self.app.retriever.search('shoes',filters={'product_types':["SHOES' OR 1=1 --"]})
    def test_overview_more_is_source_grounded_and_exhausts(self):
        self.select();self.s.respond('给我这个商品的相关信息');first=self.row()
        self.assertEqual(first['action'],'answer');self.assertIn('B005FIG90C',first['answer']);self.assertNotIn('B005F1G90C',first['answer'])
        self.s.respond('还有呢');second=self.row()
        self.assertEqual(second['action'],'answer');self.assertTrue(second['claims'])
        self.assertFalse({r['item_id'] for r in first['claims']}&{r['item_id'] for r in second['claims']})
        self.assertTrue(any(r['source_field']=='bullet_point' for r in second['claims']))
        self.s.respond('我说的还有什么相关信息');third=self.row()
        self.assertEqual(third['action'],'answer');self.assertTrue(third['retrieval_trace']['overview_exhausted'])
        self.assertIn('展示完',third['answer']);self.app.conversation.reply.assert_not_called()
        country=next(c for c in second['claims'] if c['source_field']=='country')
        self.assertTrue(country['citation'].endswith(':metadata'))
    def test_general_chat_does_not_capture_subsequent_product_more(self):
        self.s.respond('你会什么');self.select();self.s.respond('给我这个商品的相关信息')
        v=self.s.respond('还有呢');self.assertEqual(v['action'],'answer');self.assertEqual(self.s.last_task['kind'],'overview')
        self.assertNotIn('写代码',v['answer'])

    def test_full_details_keep_the_listing_and_then_exhaust(self):
        key = 'B07SSCD71B@amazon.com.au'
        self.s.respond(key)
        self.s.respond('帮我看一下这个属性'+key)
        self.app.conversation.route.return_value = {'intent':'catalog','reason':'test_ambiguous_model'}
        result = self.s.respond('还有呢给我完整的展示出来')
        row = self.row()
        self.assertEqual(result['action'], 'answer')
        self.assertEqual(row['current_listing_keys_after'], [key])
        self.assertTrue(row['retrieval_trace']['complete'])
        product = self.app.retriever.get(*key.split('@'))[0]
        expected = overview_items(product, {'pages':[]}, include_title_dimension=True)
        self.assertEqual(row['claims'], expected)
        self.assertGreater(len(expected), 6)
        self.assertTrue(all(item['listing_key']==key for item in row['claims']))
        self.assertEqual(self.s.respond('还有什么')['action'], 'answer')
        self.assertTrue(self.row()['retrieval_trace']['overview_exhausted'])
        self.app.conversation.route.assert_not_called()

    def test_remaining_details_follow_the_source_items_already_shown(self):
        self.select()
        self.s.respond('这款的属性')
        first = {claim['item_id'] for claim in self.row()['claims']}
        result = self.s.respond('把剩下的都列出来')
        row = self.row()
        self.assertEqual(result['action'], 'answer')
        self.assertTrue(row['retrieval_trace']['complete'])
        self.assertTrue(row['retrieval_trace']['more'])
        self.assertTrue(row['claims'])
        self.assertTrue(first.isdisjoint({claim['item_id'] for claim in row['claims']}))

    def test_selected_details_allow_explicit_catalog_and_new_listing(self):
        self.select()
        self.s.respond('这款的属性')
        self.assertEqual(self.s.respond('下一页')['action'], 'answer')
        self.assertEqual(self.s.respond('商品列表')['action'], 'catalog')
        self.assertEqual(self.s.respond('下一页')['action'], 'catalog')
        self.assertEqual(self.row()['retrieval_trace']['category_offset'], 14)
        self.assertEqual(self.s.respond('列出 B07SSCD71B@amazon.com.au 的所有属性')['action'], 'answer')
        self.assertTrue(self.row()['retrieval_trace']['complete'])
        self.assertEqual(self.row()['current_listing_keys_after'], ['B07SSCD71B@amazon.com.au'])

    def test_semantic_directory_label_cannot_replace_a_selected_product_without_a_cue(self):
        self.select()
        self.app.conversation.route.return_value = {'intent':'catalog','reason':'test_ambiguous_model'}
        result = self.s.respond('能不能展开讲讲？')
        self.assertEqual(result['action'], 'clarify')
        self.assertEqual(self.row()['routing']['reason'], 'catalogue_scope_unclear')
    def test_explicit_off_topic_switch_does_not_resume_product_context(self):
        self.select();self.s.respond('给我这个商品的相关信息')
        self.assertEqual(self.s.respond('换个话题，帮我写一首诗')['action'],'out_of_scope')
        self.assertEqual(self.s.respond('还有呢')['action'],'out_of_scope')
        self.assertEqual(self.s.last_task['lane'],'scope')
    def test_catalog_and_generic_concepts_are_separate(self):
        self.assertFalse(catalogue_request('什么是商品分类？'))
        self.assertFalse(catalogue_request('这个商品包含哪些配件？'))
        self.assertEqual(self.s.respond('给我这个商品的相关信息')['action'],'clarify')


if __name__=='__main__':unittest.main()
