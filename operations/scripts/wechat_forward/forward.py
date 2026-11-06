"""Durable full-text forwarding through the verified official CLI. No new collector."""
from __future__ import annotations
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
ZONE = timezone(timedelta(hours=8))
sys.path.insert(0,str(ROOT/'scripts/wechat_monitor'))
import business_rules


def now_ms():
    return int(time.time() * 1000)


def hashed(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def read_json(path):
    return json.loads(path.read_text('utf-8-sig'))


def tail(path):
    if not path.is_file():
        return {}
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 65536))
        rows = stream.read().splitlines()
    for line in reversed(rows):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except (ValueError, UnicodeError):
            pass
    return {}


def append(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at_ms': now_ms(), **value}, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def configuration(root=ROOT):
    cfg = read_json(root / 'config/wechat-wecom-forward.json')
    monitor = read_json(root / 'config/wechat-monitor.json')
    if cfg['sources'] != monitor['sources'] or cfg['keywords'] != monitor['keywords']:
        raise ValueError('FORWARD_SOURCE_POLICY_CHANGED')
    if cfg['recipient']['kind'] != 'authorizer' or cfg['contentMode'] != 'full-record' or not cfg['includeExistingHits']:
        raise ValueError('FORWARD_POLICY_REQUIRES_REVIEW')
    if not 1 <= cfg['pollSeconds'] <= 60 or cfg['minimumBatchIntervalSeconds'] < 60 or not 2000 <= cfg['maxMarkdownBytes'] <= 12000:
        raise ValueError('FORWARD_LIMITS_INVALID')
    for relative in (cfg['stateDirectory'], monitor['resident']['stateDirectory']):
        if not (root / relative).resolve().is_relative_to((root / 'artifacts').resolve()):
            raise ValueError('FORWARD_PATH_OUTSIDE_PROJECT')
    cfg['_root'] = root
    cfg['_monitorState'] = root / monitor['resident']['stateDirectory']
    cfg['_state'] = root / cfg['stateDirectory']
    return cfg


def control(cfg, value):
    append(cfg['_state'] / 'control.jsonl', {'enabled': value})
    return {'enabled': value}


def enabled(cfg):
    return bool(tail(cfg['_state'] / 'control.jsonl').get('enabled'))


def monitor_enabled(cfg):
    return bool(tail(cfg['_monitorState'] / 'control.jsonl').get('enabled'))


@contextmanager
def owner(cfg):
    cfg['_state'].mkdir(parents=True, exist_ok=True)
    path = cfg['_state'] / 'owner.lock'
    if not path.exists():
        try:
            with path.open('xb') as stream:
                stream.write(b'1')
        except FileExistsError:
            pass
    with path.open('r+b') as stream:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def busy(cfg):
    try:
        with owner(cfg):
            return False
    except OSError:
        return True


class CLIError(RuntimeError):
    def __init__(self, phase, code=None, rejected=False):
        super().__init__('WECOM_' + phase.upper() + '_FAILED')
        self.phase, self.code, self.rejected = phase, code, rejected


class WeComCLI:
    def __init__(self, cfg):
        self.cfg, self.calls = cfg, 0
        self.exe = Path(cfg['cliExecutable'])
        if not self.exe.is_file() or hashlib.sha256(self.exe.read_bytes()).hexdigest() != cfg['cliSha256']:
            raise ValueError('WECOM_CLI_BINARY_CHANGED')
        self.env = os.environ.copy()
        for key in list(self.env):
            if key == 'WECOM_CLI_ACCESS_TOKEN' or key.startswith('WECOM_CLI_ADDITIONAL_HEADERS') or key in ('WECOM_CLI_LOG_DIR', 'WECOM_CLI_LOG_LEVEL'):
                self.env.pop(key, None)
        self.env['WECOM_CLI_CONFIG_DIR'] = cfg['cliConfigDirectory']
        version = self.invoke(['--version'], 'version', text=True)
        match = re.search(r'^wecom-cli (\d+)\.(\d+)\.(\d+)', version)
        if not match or tuple(map(int, match.groups())) < (1, 2, 1):
            raise ValueError('WECOM_CLI_VERSION_UNSUPPORTED')
        if self.invoke(['auth','show','--status'], 'auth', text=True).strip() != 'authorized':
            raise ValueError('WECOM_NOT_AUTHORIZED')

    def invoke(self, args, phase, text=False):
        if hashlib.sha256(self.exe.read_bytes()).hexdigest() != self.cfg['cliSha256']:
            raise ValueError('WECOM_CLI_BINARY_CHANGED')
        self.calls += 1
        try:
            result = subprocess.run([str(self.exe), *args], env=self.env, cwd=self.cfg['_root'],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=40,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            raise CLIError(phase) from None
        try:
            raw = result.stdout
            if phase == 'dry_run' and raw.startswith('=== Dry Run ===\n'):
                raw = raw.split('\n',1)[1]
            value = json.loads(raw) if not text else raw
        except ValueError:
            raise CLIError(phase) from None
        if result.returncode != 0 or (isinstance(value, dict) and ('error' in value or value.get('errcode', 0) != 0)):
            error = value.get('error', {}) if isinstance(value, dict) else {}
            code = error.get('code', value.get('errcode')) if isinstance(error, dict) else None
            raise CLIError(phase, code if isinstance(code, int) else None, rejected=isinstance(code, int))
        return value

    def target(self):
        value = self.invoke(['identity','whoami'], 'identity')
        context = value.get('extra_identity_context', '')
        pattern = r'机器人身份：\s*名字：([^\r\n]+)\s*ID：([^\s<]+)\s*授权真人用户身份：\s*名字：([^\r\n]+)\s*ID：([^\s<]+)'
        matches = list(re.finditer(pattern, context))
        if len(matches) != 1:
            raise ValueError('WECOM_IDENTITY_SCHEMA_CHANGED')
        bot, bot_id, person, person_id = (value.strip() for value in matches[0].groups())
        if bot != self.cfg['botName'] or person != self.cfg['recipient']['name']:
            raise ValueError('WECOM_AUTHORIZER_CHANGED')
        return {'botName': bot, 'name': person, 'botHash': hashed(bot_id), 'recipientHash': hashed(person_id), 'chat': person_id}

    def send(self, target, content, dry_run=False):
        if not content or len(content.encode('utf-8')) > self.cfg['maxMarkdownBytes']:
            raise ValueError('WECOM_CONTENT_LIMIT')
        payload = {'chat_id': target['chat'], 'msg_type': 'markdown', 'markdown': {'content': content}}
        args = ['message','aibot','send','--json',json.dumps(payload, ensure_ascii=False)]
        if dry_run:
            args.append('--dry-run')
        if len(subprocess.list2cmdline([str(self.exe), *args]).encode('utf-16-le')) > 60000:
            raise ValueError('WECOM_COMMAND_LINE_LIMIT')
        result = self.invoke(args, 'dry_run' if dry_run else 'send')
        if dry_run and (result.get('method') != 'POST' or result.get('url') != '/message/aibot/send' or result.get('payload') != payload):
            raise ValueError('WECOM_DRY_RUN_CONTRACT_CHANGED')
        if not dry_run and result.get('success') is not True:
            raise CLIError('send')
        return {'accepted': True, 'dryRun': dry_run}


class Source:
    def __init__(self, cfg):
        self.cfg = cfg

    @contextmanager
    def db(self):
        path = (self.cfg['_monitorState'] / 'monitor.sqlite3').resolve(strict=True)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            yield db

    def binding(self):
        with self.db() as db:
            values = {row['key']: json.loads(row['value']) for row in db.execute('SELECT key,value FROM metadata')}
        if values.get('policy') != {'sources': self.cfg['sources'], 'keywords': self.cfg['keywords']}:
            raise ValueError('SOURCE_BINDING_CHANGED')
        return {'activation': values['activation'], 'accountHash': values['account']}

    def read(self, after):
        ids = [source['id'] for source in self.cfg['sources']]
        with self.db() as db:
            return [dict(row) for row in db.execute(f'SELECT rowid AS source_row,source,id,first_seen,body FROM messages WHERE rowid>? AND hit=1 AND source IN ({",".join("?" for unused in ids)}) ORDER BY rowid LIMIT 200', [after,*ids])]

    def records(self, keys):
        ids=[source['id'] for source in self.cfg['sources']]
        with self.db() as db:
            return {row['source_row']:dict(row) for row in db.execute(f'SELECT rowid AS source_row,source,id,first_seen,body FROM messages WHERE rowid IN ({",".join("?" for unused in keys)}) AND hit=1 AND source IN ({",".join("?" for unused in ids)})',[*keys,*ids])}


def quote(value):
    value = str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    return re.sub(r'([\\`*_\[\]()~])', r'\\\1', value)


def timestamp(at):
    return datetime.fromtimestamp(at / 1000, ZONE).strftime('%Y-%m-%d %H:%M:%S')


def render_parts(cfg, row, activation):
    item = json.loads(row['body'])
    sources = {s['id']: s['name'] for s in cfg['sources']}
    if item['source_id'] != row['source'] or item['id'] != row['id'] or item['source_name'] != sources.get(row['source']):
        raise ValueError('FORWARD_RECORD_IDENTITY_MISMATCH')
    sys.path.insert(0, str(ROOT / 'scripts/wechat_monitor'))
    from core import matches
    words = matches(item['text'], cfg['keywords'])
    if not words:
        raise ValueError('FORWARD_RECORD_NO_LONGER_MATCHES')
    title = '历史命中补转' if row['first_seen'] < activation else '群聊关键词命中'
    header = ('### ' + title + '\n' +
        '**群聊**：' + quote(item['source_name']) + '\n' +
        '**发言人**：' + quote(item['sender']) + '\n' +
        '**消息时间**：' + timestamp(item['time_ms']) + '\n' +
        '**发现时间**：' + timestamp(row['first_seen']) + '\n' +
        '**关键词**：' + ' / '.join(words) + '　**类型**：' + quote(item.get('type','')) + '\n')
    budget = cfg['maxMarkdownBytes'] - 800
    if len(header.encode('utf-8')) > budget - 100:
        raise ValueError('FORWARD_METADATA_EXCEEDS_LIMIT')
    text, cursor, chunks = item['text'], 0, []
    while cursor < len(text) or not chunks:
        low, high = 1, max(1, len(text) - cursor)
        def content(length):
            return header + '\n> ' + quote(text[cursor:cursor+length]).replace('\n','\n> ')
        while low < high:
            mid = (low + high + 1) // 2
            if len(content(mid).encode('utf-8')) <= budget:
                low = mid
            else:
                high = mid - 1
        part = text[cursor:cursor+low]
        chunks.append((content(low), part))
        cursor += len(part)
        if not part:
            break
    result = [content + f'\n\n（完整原文分段 {index+1}/{len(chunks)}）' for index,(content,raw) in enumerate(chunks)]
    if ''.join(raw for content,raw in chunks) != text or any(len(content.encode('utf-8')) > cfg['maxMarkdownBytes'] for content in result):
        raise ValueError('FORWARD_SPLIT_INCOMPLETE')
    return result


class Ledger:
    def __init__(self, cfg):
        self.cfg = cfg
        cfg['_state'].mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cfg['_state'] / 'forward.sqlite3', timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS items(source_row INTEGER PRIMARY KEY,source TEXT,id TEXT,fingerprint TEXT,parts INTEGER,UNIQUE(source,id));
          CREATE TABLE IF NOT EXISTS parts(key TEXT PRIMARY KEY,source_row INTEGER,number INTEGER,content TEXT);
          CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,at_ms INTEGER,content TEXT);
          CREATE TABLE IF NOT EXISTS batch_parts(part_key TEXT PRIMARY KEY,batch_id TEXT);
          CREATE TABLE IF NOT EXISTS attempts(batch_id TEXT PRIMARY KEY,at_ms INTEGER);
          CREATE TABLE IF NOT EXISTS receipts(seq INTEGER PRIMARY KEY,batch_id TEXT,at_ms INTEGER,state TEXT,detail TEXT);
        ''')

    def close(self):
        self.db.close()

    def get(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def bind(self, key, value):
        old = self.get(key)
        if old is not None and old != value:
            raise ValueError('FORWARD_' + key.upper() + '_CHANGED')
        if old is None:
            with self.db:
                self.db.execute('INSERT INTO meta VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False)))

    def initialize(self, source, at):
        self.bind('policy', {key:self.cfg[key] for key in ('sources','keywords','recipient','botName','contentMode','includeExistingHits','cliConfigDirectory')})
        self.bind('source', source.binding())
        if self.get('activation') is None:
            self.bind('activation', at)

    def ingest(self, source):
        row = self.db.execute('SELECT COALESCE(MAX(source_row),0) FROM items').fetchone()
        records = source.read(row[0])
        rules=business_rules.load(self.cfg['_root'])
        if rules.get('enabled'):self.bind('businessRule:'+rules['version'],rules)
        with self.db:
            for record in records:
                parts = render_parts(self.cfg, record, self.get('activation'))
                if not business_rules.alert_allowed(business_rules.evaluate(json.loads(record['body'])['text'],rules)):parts=[]
                self.db.execute('INSERT INTO items VALUES (?,?,?,?,?)',(record['source_row'],record['source'],record['id'],hashed(record['body']),len(parts)))
                for index,content in enumerate(parts):
                    key = hashed(record['source']+'\0'+record['id']+'\0'+str(index))
                    self.db.execute('INSERT INTO parts VALUES (?,?,?,?)',(key,record['source_row'],index+1,content))
        return len(records)

    def target(self, target):
        self.bind('target', {k:target[k] for k in ('botName','name','botHash','recipientHash')})

    def counts(self):
        return {'messages':self.db.execute('SELECT COUNT(*) FROM items').fetchone()[0],
            'completedMessages':self.db.execute('''SELECT COUNT(*) FROM items i WHERE i.parts>0 AND NOT EXISTS(SELECT 1 FROM parts p WHERE p.source_row=i.source_row AND NOT EXISTS
                (SELECT 1 FROM batch_parts b JOIN receipts r ON r.batch_id=b.batch_id WHERE b.part_key=p.key AND r.state='accepted'))''').fetchone()[0],
            'pendingParts':self.db.execute('SELECT COUNT(*) FROM parts p WHERE NOT EXISTS(SELECT 1 FROM batch_parts b WHERE b.part_key=p.key)').fetchone()[0],
            'acceptedBatches':self.db.execute("SELECT COUNT(*) FROM receipts WHERE state='accepted'").fetchone()[0],
            'uncertainBatches':self.db.execute("SELECT COUNT(*) FROM attempts a WHERE NOT EXISTS(SELECT 1 FROM receipts r WHERE r.batch_id=a.batch_id AND r.state IN ('accepted','rejected'))").fetchone()[0],
            'rejectedBatches':self.db.execute("SELECT COUNT(*) FROM receipts WHERE state='rejected'").fetchone()[0],
            'filteredMessages':self.db.execute("SELECT COUNT(*) FROM items i WHERE i.parts=0 OR EXISTS(SELECT 1 FROM parts p JOIN batch_parts b ON b.part_key=p.key JOIN receipts r ON r.batch_id=b.batch_id WHERE p.source_row=i.source_row AND r.state='suppressed')").fetchone()[0]}

    def apply_pending_rules(self):
        rows=self.db.execute('SELECT p.*,i.fingerprint FROM parts p JOIN items i ON i.source_row=p.source_row WHERE NOT EXISTS(SELECT 1 FROM batch_parts b WHERE b.part_key=p.key) ORDER BY p.source_row,p.number LIMIT 500').fetchall()
        if not rows:return
        originals=Source(self.cfg).records(sorted({r['source_row'] for r in rows}))
        rules=business_rules.load(self.cfg['_root'])
        rejected=[]
        for row in rows:
            original=originals.get(row['source_row'])
            if original is None or hashed(original['body'])!=row['fingerprint']:raise ValueError('FORWARD_PENDING_SOURCE_CHANGED')
            if not business_rules.alert_allowed(business_rules.evaluate(json.loads(original['body'])['text'],rules)):rejected.append(row)
        if rejected:
            identity='WCF-'+uuid.uuid4().hex[:12]
            with self.db:
                self.db.execute('INSERT INTO batches VALUES (?,?,?)',(identity,now_ms(),'Local business filter; no external send.'))
                for row in rejected:self.db.execute('INSERT INTO batch_parts VALUES (?,?)',(row['key'],identity))
                self.db.execute('INSERT INTO receipts(batch_id,at_ms,state,detail) VALUES (?,?,?,?)',(identity,now_ms(),'suppressed',json.dumps({'ruleVersion':rules.get('version'),'parts':len(rejected)})))

    def flush(self, client, at):
        counts = self.counts()
        if counts['uncertainBatches'] or counts['rejectedBatches']:
            raise ValueError('FORWARD_DELIVERY_REQUIRES_REVIEW')
        self.apply_pending_rules()
        last = self.db.execute('SELECT MAX(at_ms) FROM attempts').fetchone()[0]
        if last is not None and at-last < self.cfg['minimumBatchIntervalSeconds']*1000:
            return None
        rows = self.db.execute('SELECT p.* FROM parts p WHERE NOT EXISTS(SELECT 1 FROM batch_parts b WHERE b.part_key=p.key) ORDER BY source_row,number LIMIT 500').fetchall()
        if not rows:
            return None
        target = client.target()  # Re-resolve authorizer before each separate batch.
        self.target(target)
        content, selected = '', []
        for row in rows:
            candidate = content + ('\n\n---\n\n' if content else '') + row['content']
            if len(candidate.encode('utf-8')) > self.cfg['maxMarkdownBytes']:
                break
            content = candidate
            selected.append(row)
        if not selected:
            raise ValueError('FORWARD_BATCH_TOO_LARGE')
        identity = 'WCF-' + uuid.uuid4().hex[:12]
        # The send attempt is durably reserved before invoking the external CLI.
        with self.db:
            self.db.execute('INSERT INTO batches VALUES (?,?,?)',(identity,at,content))
            for row in selected:
                self.db.execute('INSERT INTO batch_parts VALUES (?,?)',(row['key'],identity))
            self.db.execute('INSERT INTO attempts VALUES (?,?)',(identity,at))
        start = time.monotonic()
        try:
            reply = client.send(target, content)
            if reply.get('accepted') is not True:
                raise CLIError('send')
            state, detail = 'accepted', {'milliseconds':round((time.monotonic()-start)*1000), 'partCount':len(selected)}
        except CLIError as error:
            state = 'rejected' if error.rejected else 'uncertain'
            detail = {'error':str(error),'code':error.code}
        except Exception as error:
            state, detail = 'uncertain', {'error':type(error).__name__}
        with self.db:
            self.db.execute('INSERT INTO receipts(batch_id,at_ms,state,detail) VALUES (?,?,?,?)',(identity,now_ms(),state,json.dumps(detail)))
        return {'batch':identity, 'state':state, **detail}


def status(cfg):
    health = tail(cfg['_state'] / 'health.jsonl')
    running = busy(cfg)
    result = {'enabled':enabled(cfg),'running':running,
              'healthy':bool(running and health.get('state') in ('healthy','paused_by_monitor') and now_ms()-health.get('at_ms',0)<90000),
              'botName':cfg['botName'],'recipient':cfg['recipient']['name'], 'health':health}
    path = cfg['_state'] / 'forward.sqlite3'
    if path.is_file():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
            result['acceptedBatches'] = db.execute("SELECT COUNT(*) FROM receipts WHERE state='accepted'").fetchone()[0]
    return result


def cycle(cfg, ledger, client):
    if not enabled(cfg):
        return {'state':'disabled'}
    if not monitor_enabled(cfg):
        return {'state':'paused_by_monitor', **ledger.counts()}
    source = Source(cfg)
    ledger.initialize(source, now_ms())
    ledger.ingest(source)
    reply = ledger.flush(client, now_ms())
    counts = ledger.counts()
    return {'state':'attention' if counts['uncertainBatches'] or counts['rejectedBatches'] else 'healthy',
            'botName':cfg['botName'],'recipient':cfg['recipient']['name'],**counts,'lastDispatch':reply}


def run(cfg, once=False):
    acquired = False
    try:
        with owner(cfg), closing(Ledger(cfg)) as ledger:
            acquired = True
            client = None
            while enabled(cfg):
                try:
                    if client is None:
                        client = WeComCLI(cfg)
                    result = cycle(cfg, ledger, client)
                    delay = cfg['pollSeconds']
                except Exception as error:
                    result = {'state':'degraded','error':str(error) if isinstance(error,(CLIError,ValueError)) else type(error).__name__,**ledger.counts()}
                    client = None
                    delay = 30
                append(cfg['_state'] / 'health.jsonl', {'pid':os.getpid(),**result})
                if once:
                    return result
                end = time.monotonic()+delay
                while time.monotonic()<end and enabled(cfg):
                    time.sleep(.5)
            result = {'state':'stopped',**ledger.counts()}
            append(cfg['_state']/'health.jsonl', {'pid':os.getpid(),**result})
            return result
    except OSError as error:
        if not acquired and isinstance(error, PermissionError):
            return {'state':'already_running'}
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['serve','once','enable','disable','status','preflight','preview'])
    args = parser.parse_args()
    cfg = configuration()
    if args.command in ('enable','disable'):
        result = control(cfg, args.command=='enable')
    elif args.command=='status':
        result = status(cfg)
    elif args.command=='preflight':
        client = WeComCLI(cfg)
        target = client.target()
        result = {'result':'WECOM_TARGET_VERIFIED','botName':target['botName'],'recipient':target['name']}
    elif args.command=='preview':
        rows = Source(cfg).read(0)
        rules=business_rules.load(cfg['_root'])
        parts = [p for row in rows if business_rules.alert_allowed(business_rules.evaluate(json.loads(row['body'])['text'],rules)) for p in render_parts(cfg,row,now_ms())]
        directory = ROOT/'artifacts'/('wechat-wecom-preview-'+uuid.uuid4().hex[:8])
        directory.mkdir()
        with (directory/'preview.md').open('x',encoding='utf-8') as stream:
            stream.write('\n\n---\n\n'.join(parts))
        client = WeComCLI(cfg)
        target = client.target()
        if parts:
            client.send(target,parts[0],dry_run=True)
        result = {'result':'WECOM_DRY_RUN_OK','messages':len(rows),'parts':len(parts),'preview':str(directory/'preview.md'),'sendCalls':0}
    else:
        result = run(cfg,once=args.command=='once')
    if sys.stdout is not None:
        print(json.dumps(result,ensure_ascii=True),flush=True)
    return 0 if result.get('state') not in ('degraded','attention') else 1


if __name__=='__main__':
    raise SystemExit(main())
