"""Exercise real read-only SQLite and HTTP handling; fixtures remain in artifacts."""
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts/wechat_monitor'))
from core import configuration
from resident import Store
from wechat_dashboard.data import BadQuery, DashboardData, keyword_matches
from wechat_dashboard.server import DashboardServer

AT = 1793808000000


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'artifacts/wechat-dashboard-tests' / uuid.uuid4().hex
        (self.root / 'config').mkdir(parents=True)
        self.cfg = configuration()
        self.legacy_reports = self.root / 'previous-location/reports'
        self.cfg['resident']['previousReportDirectories'] = [str(self.legacy_reports)]
        (self.root / 'config/wechat-monitor.json').write_text(json.dumps(self.cfg), 'utf-8')
        self.path = self.root / self.cfg['resident']['stateDirectory']
        self.reports = self.root / self.cfg['resident']['reportsDirectory']
        self.reports.mkdir(parents=True)
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.store.initialize(self.cfg, AT - 3 * 86400000)
        for index, records in ((0, [('old', AT - 2 * 86400000, '收 历史'), ('first', AT - 1000, '收 UA1000 <img src=x onerror=alert(1)>')]),
                               (1, [('second', AT - 500, '收 Guang'), ('plain', AT - 200, '普通聊天')])):
            source = self.cfg['sources'][index]
            for identity, when, text in records:
                self.store.commit_poll(source['id'], [self.message(identity, when, text, index)], when+1, when, self.cfg['keywords'])
        self.notice('WKR-111111111111', 0, 'first', 'shown')
        self.notice('WKR-222222222222', 1, 'second', 'uncertain')
        self.path.joinpath('control.jsonl').write_text(json.dumps({'at_ms': AT-2000, 'enabled': True})+'\n', 'utf-8')
        self.path.joinpath('health.jsonl').write_text(json.dumps({'at_ms': AT-100, 'state': 'healthy', 'pid': os.getpid()})+'\n', 'utf-8')
        self.data = DashboardData(self.root, clock=lambda: AT, alive=lambda pid: bool(pid))

    def message(self, identity, when, text, index=0):
        source = self.cfg['sources'][index]
        return {'id': identity, 'source_id': source['id'], 'source_name': source['name'], 'time_ms': when,
                'time': '测试时间', 'sender': '测试成员', 'type': '文字', 'text': text}

    def notice(self, identity, group, message, state):
        report = self.reports / (identity + '.md')
        report.write_text('# 本地测试报告\n', 'utf-8', newline='\n')
        with self.store.db:
            self.store.db.execute('INSERT INTO notices VALUES (?,?,?,?)', (identity, AT-100, str(report), '模拟提醒'))
            self.store.db.execute('INSERT INTO notice_items VALUES (?,?,?)', (self.cfg['sources'][group]['id'], message, identity))
            self.store.db.execute('INSERT INTO receipts(notice_id,at_ms,state,detail) VALUES (?,?,?,?)', (identity, AT-100, state, '{}'))

    def test_real_counts_range_and_source_scope(self):
        with self.store.db:
            self.store.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', ('removed@chatroom', 'denied', 'fake', AT-50, 1, '{}'))
        snapshot = self.data.snapshot()
        self.assertEqual(snapshot['metrics'], {'messages': 4, 'hits': 3, 'shown': 1, 'pending': 1, 'uncertain': 1})
        self.assertEqual(snapshot['hits']['total'], 2)
        self.assertEqual(sum(item['count'] for item in snapshot['trend']), 2)
        self.assertEqual(snapshot['monitor']['state'], 'healthy')
        self.assertEqual(self.data.snapshot({'range':['all']})['hits']['total'], 3)
        self.assertNotIn('removed@chatroom', json.dumps(snapshot))

    def test_group_keyword_and_literal_search(self):
        self.assertEqual(self.data.snapshot({'keyword':['UA']})['hits']['total'], 1)
        self.assertEqual(self.data.snapshot({'q':["' OR 1=1 --"]})['hits']['total'], 0)
        self.assertEqual(self.data.snapshot({'q':['Guang']})['hits']['total'], 1)
        self.assertEqual(self.data.snapshot({'group':[self.cfg['sources'][0]['id']]})['hits']['total'], 1)
        self.assertEqual(keyword_matches('收UA1000 Guang', ['收','UA']), ['收','UA'])

    def test_late_old_message_is_filtered_by_discovery_time(self):
        item = self.message('late', AT - 2 * 86400000, 'UA 补读')
        self.store.commit_poll(self.cfg['sources'][0]['id'], [item], AT, AT, self.cfg['keywords'])
        snapshot = self.data.snapshot()
        self.assertEqual(snapshot['hits']['total'], 3)
        self.assertEqual(snapshot['hits']['items'][0]['messageAt'], item['time_ms'])
        self.assertEqual(snapshot['hits']['items'][0]['discoveredAt'], AT)

    def test_receipt_filters_and_cooldown_pending(self):
        self.assertEqual(self.data.snapshot({'state':['shown']})['hits']['items'][0]['receipt'], 'shown')
        self.assertEqual(self.data.snapshot({'state':['uncertain']})['hits']['items'][0]['receipt'], 'uncertain')
        self.assertEqual(self.data.snapshot({'range':['all'],'state':['pending']})['hits']['total'], 1)

    def test_sqlite_write_is_rejected(self):
        with self.data.database(self.cfg) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("INSERT INTO metadata VALUES ('forbidden','1')")
        self.assertEqual(self.store.meta('forbidden'), None)
        self.assertEqual(self.data.snapshot()['metrics']['messages'], 4)

    def test_pagination_and_full_detail(self):
        self.store.commit_poll(self.cfg['sources'][0]['id'], [self.message('extra'+str(i), AT-500, 'UA '+str(i)) for i in range(25)], AT, AT-1, self.cfg['keywords'])
        first, second = self.data.snapshot(), self.data.snapshot({'page':['2']})
        self.assertEqual(first['hits']['total'], 27)
        self.assertEqual(len(first['hits']['items']), 20)
        self.assertEqual(len(second['hits']['items']), 7)
        keys = {row['key'] for row in first['hits']['items']}
        self.assertTrue(keys.isdisjoint(row['key'] for row in second['hits']['items']))
        detail = self.data.hit_detail(first['hits']['items'][0]['key'])
        self.assertIn('UA', detail['text'])

    def test_business_filter_review_and_excluded_keep_complete_raw_text(self):
        policy={'schemaVersion':1,'version':'test-cx','enabled':True,'unknownAction':'dashboard-only',
                'cx':{'quantityPerPerson':88000,'minimumCnyPerWan':985}}
        with (self.root/'config/wechat-business-rules.json').open('x',encoding='utf-8') as stream:json.dump(policy,stream)
        for identity,text in [('bad','收CX3万 960/万'),('unknown','收CX8.8万'),('good','收CX每人8.8万 985元/万')]:
            self.store.commit_poll(self.cfg['sources'][0]['id'],[self.message(identity,AT-100,text)],AT,AT,self.cfg['keywords'])
        review=self.data.snapshot({'state':['review']})
        self.assertEqual(review['hits']['total'],1)
        self.assertEqual(review['hits']['items'][0]['text'],'收CX8.8万')
        self.assertEqual(review['businessRules']['review'],1)
        self.assertEqual(review['businessRules']['excluded'],1)
        self.assertEqual(review['businessRules']['eligible'],1)
        filtered=self.data.snapshot({'state':['filtered']})
        self.assertEqual(filtered['hits']['total'],1)
        self.assertIn('单人规格',filtered['hits']['items'][0]['decision']['reason'])
        self.assertEqual(self.data.hit_detail(filtered['hits']['items'][0]['key'])['text'],'收CX3万 960/万')
        self.assertIn('业务信息待核实',self.data.export({'state':['review']}).decode('utf-8'))

    def test_rolling_corrupt_health_tail_and_offline(self):
        with self.path.joinpath('health.jsonl').open('a', encoding='utf-8') as stream:
            stream.write('{"incomplete":')
        self.assertEqual(self.data.snapshot()['monitor']['state'], 'healthy')
        offline = DashboardData(self.root, clock=lambda: AT, alive=lambda pid: False)
        self.assertEqual(offline.snapshot()['monitor']['state'], 'offline')
        delayed = DashboardData(self.root, clock=lambda: AT+30000, alive=lambda pid: True)
        self.assertEqual(delayed.snapshot()['monitor']['state'], 'delayed')

    def test_report_whitelist_and_path_escape(self):
        self.assertEqual(self.data.report('WKR-111111111111'), '# 本地测试报告\n'.encode())
        for identity in ('../control.jsonl', 'WKR-not-a-report', 'WKR-333333333333'):
            with self.assertRaises(FileNotFoundError):
                self.data.report(identity)
        outside = self.root / 'artifacts/outside.md'
        outside.write_text('must not serve', 'utf-8')
        with self.store.db:
            self.store.db.execute('INSERT INTO notices VALUES (?,?,?,?)', ('WKR-444444444444', AT, str(outside), 'wrong path'))
            self.store.db.execute('INSERT INTO notice_items VALUES (?,?,?)', (self.cfg['sources'][0]['id'], 'outside', 'WKR-444444444444'))
        with self.assertRaises(FileNotFoundError):
            self.data.report('WKR-444444444444')

    def test_relocated_report_preserves_journal_and_rejects_escape(self):
        identity = 'WKR-111111111111'
        original = str(self.legacy_reports / (identity + '.md'))
        with self.store.db:
            self.store.db.execute('UPDATE notices SET report=? WHERE id=?', (original, identity))
        self.assertEqual(self.data.report(identity), '# 本地测试报告\n'.encode())
        self.assertEqual(self.store.db.execute('SELECT report FROM notices WHERE id=?', (identity,)).fetchone()[0], original)
        with self.store.db:
            self.store.db.execute('UPDATE notices SET report=? WHERE id=?',
                                 (str(self.legacy_reports / '../outside.md'), identity))
        with self.assertRaises(FileNotFoundError):
            self.data.report(identity)

    def test_export_preserves_full_text_and_escapes_html(self):
        result = self.data.export({}).decode('utf-8')
        self.assertIn('UA1000', result)
        self.assertIn('&lt;img', result)
        self.assertNotIn('<img', result)
        self.assertIn('2 / 2', result)

    def test_new_reader_preserves_progress_and_missing_db_is_empty(self):
        before = self.store.meta('activation')
        self.assertEqual(DashboardData(self.root, clock=lambda: AT).snapshot()['activationAt'], before)
        empty = self.root / 'empty'
        (empty / 'config').mkdir(parents=True)
        (empty / 'config/wechat-monitor.json').write_text(json.dumps(self.cfg), 'utf-8')
        snapshot = DashboardData(empty, clock=lambda: AT).snapshot()
        self.assertEqual(snapshot['metrics']['hits'], 0)
        self.assertEqual(len(snapshot['groups']), 2)

    def test_invalid_query_is_rejected(self):
        for query in ({'group':['removed@chatroom']},{'range':['forever']},{'page':['0']},{'state':['read']},{'q':['a','b']}):
            with self.assertRaises(BadQuery):
                self.data.snapshot(query)

    def test_forwarding_receipts_are_separate_and_read_only(self):
        from wechat_forward.forward import configuration as forward_configuration, Ledger
        cfg = json.loads((ROOT/'config/wechat-wecom-forward.json').read_text('utf-8'))
        (self.root/'config/wechat-wecom-forward.json').write_text(json.dumps(cfg),'utf-8')
        forward_cfg = forward_configuration(self.root)
        with closing(Ledger(forward_cfg)) as ledger:
            with ledger.db:
                ledger.db.execute('INSERT INTO items VALUES (?,?,?,?,?)',(10,'allowed','example','hash',1))
                ledger.db.execute('INSERT INTO parts VALUES (?,?,?,?)',('part',10,1,'must not expose original in summary'))
                ledger.db.execute('INSERT INTO batches VALUES (?,?,?)',('batch',AT-100,'must not expose original in summary'))
                ledger.db.execute('INSERT INTO batch_parts VALUES (?,?)',('part','batch'))
                ledger.db.execute('INSERT INTO attempts VALUES (?,?)',('batch',AT-100))
                ledger.db.execute('INSERT INTO receipts(batch_id,at_ms,state,detail) VALUES (?,?,?,?)',('batch',AT-50,'accepted','{}'))
        state=forward_cfg['_state']
        (state/'control.jsonl').write_text(json.dumps({'enabled':True})+'\n','utf-8')
        (state/'health.jsonl').write_text(json.dumps({'pid':os.getpid(),'at_ms':AT-10,'state':'healthy'})+'\n','utf-8')
        snapshot=self.data.snapshot()
        channel=snapshot['forwarding']
        self.assertEqual(channel['state'],'healthy')
        self.assertEqual(channel['completedMessages'],1)
        self.assertEqual(channel['acceptedBatches'],1)
        self.assertEqual(channel['recent'][0]['state'],'accepted')
        self.assertNotIn('must not expose',json.dumps(channel))
        self.assertEqual(snapshot['metrics']['shown'],1)

    def test_forwarding_crash_is_visible_without_success(self):
        from wechat_forward.forward import configuration as forward_configuration, Ledger
        cfg = json.loads((ROOT/'config/wechat-wecom-forward.json').read_text('utf-8'))
        (self.root/'config/wechat-wecom-forward.json').write_text(json.dumps(cfg),'utf-8')
        forward_cfg=forward_configuration(self.root)
        with closing(Ledger(forward_cfg)) as ledger:
            with ledger.db:
                ledger.db.execute('INSERT INTO batches VALUES (?,?,?)',('unknown',AT-100,'private content'))
                ledger.db.execute('INSERT INTO attempts VALUES (?,?)',('unknown',AT-100))
        snapshot=self.data.snapshot()
        self.assertEqual(snapshot['forwarding']['uncertainBatches'],1)
        self.assertEqual(snapshot['forwarding']['acceptedBatches'],0)
        self.assertEqual(snapshot['forwarding']['state'],'disabled')

    def test_totals_are_not_capped_at_notification_display_limit(self):
        for index in range(105):
            self.notice('WKR-'+format(index+100,'012x'), 0, 'count'+str(index), 'shown')
        snapshot = self.data.snapshot()
        self.assertEqual(snapshot['metrics']['shown'], 106)
        self.assertEqual(len(snapshot['notices']), 100)

    def test_http_host_origin_methods_json_and_download(self):
        server = DashboardServer(('127.0.0.1',0), self.data)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def request(path, method='GET', headers=None):
            with closing(http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)) as client:
                client.request(method, path, headers=headers or {})
                response = client.getresponse()
                return response.status, dict(response.getheaders()), response.read()
        try:
            code, headers, body = request('/api/snapshot')
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)['hits']['total'], 2)
            self.assertIn('application/json', headers['Content-Type'])
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(request('/api/snapshot', headers={'Origin':'https://evil.example'})[0], 403)
            self.assertEqual(request('/api/snapshot', headers={'Host':'evil.example'})[0], 403)
            self.assertEqual(request('/api/snapshot', method='POST')[0], 405)
            self.assertEqual(request('/api/snapshot?range=invalid')[0], 400)
            self.assertEqual(request('/api/snapshot?'+'&'.join('a'+str(i)+'=b' for i in range(9)))[0], 400)
            self.assertEqual(request('/config/wechat-monitor.json')[0], 404)
            self.assertEqual(request('/api/hit/999999')[0], 404)
            code, headers, body = request('/api/report/WKR-111111111111')
            self.assertEqual(code, 200)
            self.assertIn('attachment', headers['Content-Disposition'])
            code, headers, body = request('/')
            self.assertEqual(code, 200)
            self.assertIn('群消息监测台', body.decode('utf-8'))
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
