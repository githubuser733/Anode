"""Real SQLite outbox, CLI adapter contracts and complete Unicode message forwarding."""
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'scripts/wechat_monitor'))
from core import configuration as monitor_configuration
from resident import Store
from wechat_forward import forward

T = 1793815000000


class FakeCLI:
    def __init__(self):
        self.sent = []
        self.mode = 'ok'
        self.identity = {'botName':'狐狸的机器人','name':'张虹','botHash':'bot-hash','recipientHash':'person-hash','chat':'internal'}
    def target(self):
        if self.mode=='offline_identity':
            raise forward.CLIError('identity')
        return dict(self.identity)
    def send(self,target,content):
        self.sent.append(content)
        if self.mode=='unknown':
            raise forward.CLIError('send')
        if self.mode=='rejected':
            raise forward.CLIError('send',12345,True)
        return {'accepted':True}


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'artifacts/wechat-forward-tests'/uuid.uuid4().hex
        (self.root/'config').mkdir(parents=True)
        monitor=monitor_configuration()
        self.root.joinpath('config/wechat-monitor.json').write_text(json.dumps(monitor),'utf-8')
        cfg=json.loads((ROOT/'config/wechat-wecom-forward.json').read_text('utf-8'))
        self.root.joinpath('config/wechat-wecom-forward.json').write_text(json.dumps(cfg),'utf-8')
        self.cfg=forward.configuration(self.root)
        self.source=forward.Source(self.cfg)
        self.store=Store(self.cfg['_monitorState'])
        self.store.initialize(monitor,T-10000)
        self.store.bind_account('fake-account')
        forward.append(self.cfg['_monitorState']/'control.jsonl',{'enabled':True})
        self.ledger=forward.Ledger(self.cfg)
        self.ledger.initialize(self.source,T)
        self.client=FakeCLI()
        self.addCleanup(self.ledger.close)
        self.addCleanup(self.store.close)

    def add(self,name='first',text='收 UA1000',group=0,at=T+1):
        source=self.cfg['sources'][group]
        message={'id':name,'source_id':source['id'],'source_name':source['name'],'time_ms':at-1,
                 'time':'测试时间','sender':'发言人','type':'文字','text':text}
        self.store.commit_poll(source['id'],[message],at+1,at,self.cfg['keywords'])
        self.ledger.ingest(self.source)
        return message

    def test_initial_existing_hits_and_complete_metadata(self):
        self.add(at=T-1000)
        reply=self.ledger.flush(self.client,T+100)
        self.assertEqual(reply['state'],'accepted')
        content=self.client.sent[0]
        for expected in ('历史命中补转','星创票务 收分群','发言人','消息时间','发现时间','关键词','UA1000','类型'):
            self.assertIn(expected,content)
        self.assertEqual(self.ledger.counts()['completedMessages'],1)

    def enable_business_rules(self):
        policy={'schemaVersion':1,'version':'cx-88000-985-v1','enabled':True,'unknownAction':'dashboard-only',
                'cx':{'quantityPerPerson':88000,'minimumCnyPerWan':985}}
        with (self.root/'config/wechat-business-rules.json').open('x',encoding='utf-8') as stream:json.dump(policy,stream)

    def test_cx_excluded_and_incomplete_stay_local_eligible_is_sent(self):
        self.enable_business_rules()
        self.add('low','收CX3万 960/万')
        self.add('unknown','收CX8.8万')
        self.add('eligible','收CX每人8.8万 985元/万')
        self.ledger.flush(self.client,T+100)
        self.assertEqual(len(self.client.sent),1)
        self.assertIn('985',self.client.sent[0])
        self.assertNotIn('960',self.client.sent[0])
        self.assertEqual(self.ledger.counts()['completedMessages'],1)
        self.assertEqual(self.ledger.counts()['filteredMessages'],2)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],3)
        with closing(forward.Ledger(self.cfg)) as resumed:
            self.assertEqual(resumed.ingest(self.source),0)
            self.assertIsNone(resumed.flush(self.client,T+99999))
        self.assertEqual(len(self.client.sent),1)

    def test_pending_quote_is_rechecked_before_send_after_rule_enable(self):
        self.add('queued','收CX3万 960/万')
        self.assertEqual(self.ledger.counts()['pendingParts'],1)
        self.enable_business_rules()
        self.assertIsNone(self.ledger.flush(self.client,T+100))
        self.assertEqual(self.client.sent,[])
        counts=self.ledger.counts()
        self.assertEqual((counts['completedMessages'],counts['pendingParts'],counts['uncertainBatches'],counts['filteredMessages']),(0,0,0,1))
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM parts').fetchone()[0],1)
        self.assertEqual(self.ledger.db.execute('SELECT state FROM receipts').fetchone()[0],'suppressed')

    def test_restart_dedup_and_pending_recovery(self):
        self.add()
        self.ledger.flush(self.client,T+100)
        self.add('offline',group=1,at=T+200)
        with closing(forward.Ledger(self.cfg)) as resumed:
            resumed.initialize(self.source,T+9999)
            self.assertEqual(resumed.get('activation'),T)
            self.assertEqual(resumed.ingest(self.source),0)
            self.assertIsNone(resumed.flush(self.client,T+5000))
            self.assertEqual(resumed.flush(self.client,T+60100)['state'],'accepted')
        self.assertEqual(len(self.client.sent),2)

    def test_multiple_groups_merge_into_single_batch(self):
        self.add('one')
        self.add('two',group=1)
        self.ledger.flush(self.client,T+100)
        self.assertEqual(len(self.client.sent),1)
        self.assertEqual(self.ledger.counts()['completedMessages'],2)
        self.assertIn('方舟',self.client.sent[0])

    def test_cooldown_keeps_all_new_hits(self):
        self.add('one')
        self.ledger.flush(self.client,T+100)
        self.add('two',at=T+200)
        self.add('three',at=T+300)
        self.assertIsNone(self.ledger.flush(self.client,T+400))
        self.assertEqual(self.ledger.counts()['pendingParts'],2)
        self.ledger.flush(self.client,T+60100)
        self.assertEqual(self.ledger.counts()['completedMessages'],3)

    def test_unknown_result_is_never_retried_and_blocks_later_sends(self):
        self.add()
        self.client.mode='unknown'
        self.assertEqual(self.ledger.flush(self.client,T+100)['state'],'uncertain')
        self.client.mode='ok'
        with closing(forward.Ledger(self.cfg)) as resumed:
            with self.assertRaisesRegex(ValueError,'REQUIRES_REVIEW'):
                resumed.flush(self.client,T+120100)
        self.assertEqual(len(self.client.sent),1)

    def test_business_rejection_is_not_success_or_retry(self):
        self.add()
        self.client.mode='rejected'
        self.assertEqual(self.ledger.flush(self.client,T+100)['state'],'rejected')
        self.assertEqual(self.ledger.counts()['rejectedBatches'],1)
        self.assertEqual(self.ledger.counts()['completedMessages'],0)
        with self.assertRaisesRegex(ValueError,'REQUIRES_REVIEW'):
            self.ledger.flush(self.client,T+60100)

    def test_crash_after_reserved_attempt_is_uncertain_without_replay(self):
        self.add()
        self.client.send=mock.Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.ledger.flush(self.client,T+100)
        self.assertEqual(self.ledger.counts()['uncertainBatches'],1)
        with self.assertRaisesRegex(ValueError,'REQUIRES_REVIEW'):
            self.ledger.flush(self.client,T+60100)

    def test_preflight_failure_leaves_pending_unreserved(self):
        self.add()
        self.client.mode='offline_identity'
        with self.assertRaises(forward.CLIError):
            self.ledger.flush(self.client,T+100)
        self.assertEqual(self.ledger.counts()['pendingParts'],1)
        self.assertEqual(self.ledger.counts()['uncertainBatches'],0)
        self.client.mode='ok'
        self.assertEqual(self.ledger.flush(self.client,T+200)['state'],'accepted')

    def test_authorizer_change_never_changes_destination(self):
        self.add()
        self.ledger.flush(self.client,T+100)
        self.add('two',at=T+200)
        self.client.identity['recipientHash']='other-person'
        with self.assertRaisesRegex(ValueError,'TARGET_CHANGED'):
            self.ledger.flush(self.client,T+60100)
        self.assertEqual(len(self.client.sent),1)

    def test_source_binding_and_policy_changes_require_review(self):
        self.cfg['keywords']=['different']
        with self.assertRaisesRegex(ValueError,'POLICY_CHANGED'):
            self.ledger.initialize(self.source,T)
        with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):
            self.ledger.bind('source',{'activation':1,'accountHash':'different'})

    def test_unknown_group_excluded_and_keywords_rechecked(self):
        with self.store.db:
            self.store.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',('removed@chatroom','wrong','fake',T,1,'{}'))
        self.assertEqual(self.ledger.ingest(self.source),0)
        self.add('ordinary',text='Guang 普通消息')
        self.assertEqual(self.ledger.counts()['messages'],0)

    def test_full_unicode_long_message_is_split_not_truncated(self):
        text='收 UA '+('中文😀\n`[]()*\\<&>'*4000)
        message=self.add(text=text)
        parts=self.ledger.db.execute('SELECT content FROM parts ORDER BY number').fetchall()
        self.assertGreater(len(parts),2)
        self.assertTrue(all(len(p[0].encode('utf-8'))<=self.cfg['maxMarkdownBytes'] for p in parts))
        recovered=[]
        import html,re
        for row in parts:
            raw=row[0].split('\n\n> ',1)[1].rsplit('\n\n（完整原文分段 ',1)[0].replace('\n> ','\n')
            recovered.append(html.unescape(re.sub(r'\\([\\`*_\[\]()~])',r'\1',raw)))
        self.assertEqual(''.join(recovered),text)
        self.assertNotIn('<&>',parts[0][0])

    def test_monitor_pause_prevents_external_send(self):
        self.add()
        forward.control(self.cfg,True)
        forward.append(self.cfg['_monitorState']/'control.jsonl',{'enabled':False})
        self.assertEqual(forward.cycle(self.cfg,self.ledger,self.client)['state'],'paused_by_monitor')
        self.assertEqual(self.client.sent,[])

    def test_sender_stop_and_owner_exclusion(self):
        forward.control(self.cfg,True)
        with forward.owner(self.cfg):
            self.assertTrue(forward.busy(self.cfg))
        self.assertFalse(forward.busy(self.cfg))
        forward.control(self.cfg,False)
        self.assertFalse(forward.enabled(self.cfg))
        self.assertEqual(forward.cycle(self.cfg,self.ledger,self.client)['state'],'disabled')

    def test_child_environment_is_independent_and_no_shell(self):
        cfg=self.cfg
        replies=[subprocess.CompletedProcess([],0,'wecom-cli 1.3.4',''),subprocess.CompletedProcess([],0,'authorized','')]
        with mock.patch.dict(os.environ,{'WECOM_CLI_ACCESS_TOKEN':'do-not-use','WECOM_CLI_ADDITIONAL_HEADERS_1':'do-not-use'}),mock.patch.object(forward.subprocess,'run',side_effect=replies) as run:
            client=forward.WeComCLI(cfg)
        for call in run.call_args_list:
            self.assertNotIn('shell',call.kwargs)
            self.assertEqual(call.kwargs['env']['WECOM_CLI_CONFIG_DIR'],cfg['cliConfigDirectory'])
            self.assertNotIn('WECOM_CLI_ACCESS_TOKEN',call.kwargs['env'])
            self.assertNotIn('WECOM_CLI_ADDITIONAL_HEADERS_1',call.kwargs['env'])
            self.assertEqual(call.kwargs['creationflags'],subprocess.CREATE_NO_WINDOW)

    def test_cli_dry_run_banner_and_business_success_contract(self):
        client=object.__new__(forward.WeComCLI)
        client.cfg=self.cfg;client.exe=Path(self.cfg['cliExecutable']);client.env={};client.calls=0
        target={'chat':'not-a-real-id'}
        content='收 本地测试'
        payload={'chat_id':target['chat'],'msg_type':'markdown','markdown':{'content':content}}
        output='=== Dry Run ===\n'+json.dumps({'method':'POST','url':'/message/aibot/send','payload':payload})
        with mock.patch.object(forward.subprocess,'run',return_value=subprocess.CompletedProcess([],0,output,'')):
            self.assertTrue(client.send(target,content,True)['dryRun'])
        with mock.patch.object(forward.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"success":false}','')):
            with self.assertRaises(forward.CLIError):
                client.send(target,content)
        with mock.patch.object(forward.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"success":true}','')):
            self.assertTrue(client.send(target,content)['accepted'])


if __name__=='__main__':
    unittest.main(verbosity=2)
