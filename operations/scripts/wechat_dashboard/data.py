"""Read-only dashboard projections over the resident monitor's existing journal."""
from __future__ import annotations
from contextlib import contextmanager, closing
from datetime import datetime, timezone, timedelta
import ctypes
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'wechat_monitor'))
import business_rules

ZONE = timezone(timedelta(hours=8))
RANGES = {'24h': 86400000, '7d': 604800000, 'all': None}


class BadQuery(ValueError):
    pass


def now_ms():
    return int(time.time() * 1000)


def read_json(path):
    return json.loads(path.read_text('utf-8-sig'))


def tail_rows(path, maximum=65536):
    if not path.is_file():
        return []
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - maximum))
        lines = stream.read(maximum).splitlines()
    result = []
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                result.append(row)
        except (ValueError, UnicodeError):
            pass
    return result


def keyword_matches(text, keywords):
    folded = text.casefold()
    return [word for word in keywords if
            (bool(re.search(r'(?<![a-z])' + re.escape(word.casefold()) + r'(?![a-z])', folded))
             if word.isascii() and word.isalpha() else word.casefold() in folded)]


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != 'nt':
        return Path('/proc', str(pid)).exists()
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def query_options(values, config, at):
    allowed = {'range', 'group', 'q', 'keyword', 'state', 'page'}
    if set(values) - allowed or any(len(v) != 1 for v in values.values()):
        raise BadQuery('INVALID_QUERY')
    get = lambda key, default: values.get(key, [default])[0]
    span, group, keyword, state, query = (get('range', '24h'), get('group', 'all'),
        get('keyword', 'all'), get('state', 'all'), get('q', '').strip())
    if span not in RANGES or len(query) > 200:
        raise BadQuery('INVALID_RANGE_OR_QUERY')
    if group != 'all' and group not in {g['id'] for g in config['sources']}:
        raise BadQuery('GROUP_NOT_ALLOWLISTED')
    if keyword != 'all' and keyword not in config['keywords']:
        raise BadQuery('INVALID_KEYWORD')
    if state not in {'all', 'shown', 'pending', 'uncertain', 'review', 'filtered'}:
        raise BadQuery('INVALID_RECEIPT_STATE')
    try:
        page = int(get('page', '1'))
    except ValueError:
        raise BadQuery('INVALID_PAGE') from None
    if not 1 <= page <= 100000:
        raise BadQuery('INVALID_PAGE')
    return {'range': span, 'group': group, 'keyword': keyword, 'state': state,
            'q': query, 'page': page, 'since': at - RANGES[span] if RANGES[span] else 0}


class DashboardData:
    def __init__(self, root, clock=now_ms, alive=process_alive):
        self.root, self.clock, self.alive = Path(root).resolve(), clock, alive

    def config(self):
        cfg = read_json(self.root / 'config/wechat-monitor.json')
        if cfg['apiSendingEnabled'] or cfg['destination']['id'] != 'filehelper':
            raise ValueError('UNEXPECTED_MONITOR_POLICY')
        return cfg

    def paths(self, cfg):
        result = {}
        for key in ('stateDirectory', 'reportsDirectory'):
            path = (self.root / cfg['resident'][key]).resolve()
            if not path.is_relative_to(self.root / 'artifacts'):
                raise ValueError('DATA_PATH_OUTSIDE_ARTIFACTS')
            result[key] = path
        return result

    @contextmanager
    def database(self, cfg):
        path = self.paths(cfg)['stateDirectory'] / 'monitor.sqlite3'
        if not path.is_file():
            yield None
            return
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.create_function('contains_message', 2, lambda body, q:
            int(any(q.casefold() in str(json.loads(body).get(k, '')).casefold()
                    for k in ('text', 'sender', 'source_name'))), deterministic=True)
        connection.create_function('matches_word', 2, lambda body, word:
            int(bool(keyword_matches(json.loads(body).get('text', ''), [word]))), deterministic=True)
        rules=business_rules.load(self.root)
        connection.create_function('business_state',1,lambda body:business_rules.evaluate(json.loads(body).get('text',''),rules)['state'],deterministic=True)
        try:
            connection.execute('BEGIN')
            yield connection
        finally:
            connection.close()

    def scope(self, cfg):
        ids = [source['id'] for source in cfg['sources']]
        return ids, ','.join('?' for unused in ids)

    def hit_where(self, cfg, options):
        ids, marks = self.scope(cfg)
        where = f'm.hit=1 AND m.source IN ({marks}) AND m.first_seen>=?'
        args = [*ids, options['since']]
        if options['group'] != 'all':
            where += ' AND m.source=?'
            args.append(options['group'])
        if options['q']:
            where += ' AND contains_message(m.body,?)=1'
            args.append(options['q'])
        if options['keyword'] != 'all':
            where += ' AND matches_word(m.body,?)=1'
            args.append(options['keyword'])
        return where, args

    @staticmethod
    def receipt_sql():
        return "CASE WHEN ni.notice_id IS NOT NULL THEN CASE WHEN EXISTS (SELECT 1 FROM receipts r WHERE r.notice_id=ni.notice_id AND r.state='shown') THEN 'shown' ELSE 'uncertain' END WHEN business_state(m.body)='review' THEN 'review' WHEN business_state(m.body)='excluded' THEN 'filtered' ELSE 'pending' END"

    def hits(self, cfg, options, db, *, limit=20, offset=None):
        if db is None:
            return {'items': [], 'total': 0, 'page': options['page'], 'pageSize': limit}
        where, args = self.hit_where(cfg, options)
        state = self.receipt_sql()
        joined = 'messages m LEFT JOIN notice_items ni ON ni.source=m.source AND ni.message_id=m.id'
        if options['state'] != 'all':
            where += f' AND ({state})=?'
            args.append(options['state'])
        total = db.execute(f'SELECT COUNT(*) FROM {joined} WHERE {where}', args).fetchone()[0]
        offset = (options['page'] - 1) * limit if offset is None else offset
        rows = db.execute(f'SELECT m.rowid AS row_key,m.first_seen,m.body,ni.notice_id,{state} AS receipt FROM {joined} WHERE {where} ORDER BY m.first_seen DESC,m.rowid DESC LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        items = []
        for row in rows:
            body = json.loads(row['body'])
            text = str(body.get('text', ''))
            items.append({'key': row['row_key'], 'groupId': body['source_id'], 'groupName': body['source_name'],
                'sender': str(body.get('sender', '')), 'messageAt': body['time_ms'], 'discoveredAt': row['first_seen'],
                'text': text[:800], 'truncated': len(text) > 800, 'keywords': keyword_matches(text, cfg['keywords']),
                'receipt': row['receipt'], 'noticeId': row['notice_id'], 'decision':business_rules.evaluate(text,business_rules.load(self.root))})
        return {'items': items, 'total': total, 'page': options['page'], 'pageSize': limit}

    def notifications(self, cfg, options, db):
        if db is None:
            return []
        ids, marks = self.scope(cfg)
        query = f'''SELECT n.id,n.created,n.text,(SELECT COUNT(*) FROM notice_items i WHERE i.notice_id=n.id) AS hits,
          CASE WHEN EXISTS(SELECT 1 FROM receipts r WHERE r.notice_id=n.id AND r.state='shown') THEN 'shown' ELSE 'uncertain' END AS receipt
          FROM notices n WHERE n.created>=? AND EXISTS(SELECT 1 FROM notice_items i WHERE i.notice_id=n.id AND i.source IN ({marks}))
          AND NOT EXISTS(SELECT 1 FROM notice_items i WHERE i.notice_id=n.id AND i.source NOT IN ({marks}))'''
        args = [options['since'], *ids, *ids]
        if options['group'] != 'all':
            query += ' AND EXISTS(SELECT 1 FROM notice_items i WHERE i.notice_id=n.id AND i.source=?)'
            args.append(options['group'])
        query += ' ORDER BY n.created DESC LIMIT 100'
        return [{'id': row['id'], 'at': row['created'], 'text': row['text'], 'hitCount': row['hits'],
                 'receipt': row['receipt'], 'reportUrl': '/api/report/' + row['id']}
                for row in db.execute(query, args)]

    def snapshot(self, values=None):
        cfg, at = self.config(), self.clock()
        options = query_options(values or {}, cfg, at)
        paths = self.paths(cfg)
        history = tail_rows(paths['stateDirectory'] / 'health.jsonl')
        control = tail_rows(paths['stateDirectory'] / 'control.jsonl')
        health, desired = (history[-1] if history else {}), (control[-1] if control else {})
        enabled = bool(desired.get('enabled'))
        running = self.alive(health.get('pid'))
        age = max(0, at - health.get('at_ms', 0)) if health else None
        state = ('paused' if not enabled else 'offline' if not running else
                 'degraded' if health.get('state') == 'degraded' else
                 'delayed' if age is None or age > max(10000, cfg['pollSeconds'] * 4000) else
                 'healthy' if health.get('state') == 'healthy' else 'connecting')
        metrics = {'messages': 0, 'hits': 0, 'shown': 0, 'pending': 0, 'uncertain': 0}
        rules=business_rules.load(self.root)
        rule_counts={'review':0,'excluded':0,'eligible':0}
        activation, groups, checkpoints = None, [], {}
        timeline = []
        ids, marks = self.scope(cfg)
        with self.database(cfg) as db:
            if db is not None:
                meta = db.execute("SELECT value FROM metadata WHERE key='activation'").fetchone()
                activation = json.loads(meta[0]) if meta else None
                totals = db.execute(f'SELECT COUNT(*),COALESCE(SUM(hit),0) FROM messages WHERE source IN ({marks})', ids).fetchone()
                metrics.update(messages=totals[0], hits=totals[1])
                for decision in rule_counts:
                    rule_counts[decision]=db.execute(f'SELECT COUNT(*) FROM messages m WHERE hit=1 AND source IN ({marks}) AND business_state(body)=?',[*ids,decision]).fetchone()[0]
                metrics['pending'] = db.execute(f'''SELECT COUNT(*) FROM messages m WHERE m.hit=1 AND business_state(m.body) NOT IN ('review','excluded') AND m.source IN ({marks}) AND NOT EXISTS
                    (SELECT 1 FROM notice_items ni WHERE ni.source=m.source AND ni.message_id=m.id)''', ids).fetchone()[0]
                # Counts do not inherit the 100-row display cap.
                for receipt in ('shown', 'uncertain'):
                    metrics[receipt] = db.execute(f'''SELECT COUNT(*) FROM notices n WHERE
                        EXISTS(SELECT 1 FROM notice_items i WHERE i.notice_id=n.id AND i.source IN ({marks}))
                        AND NOT EXISTS(SELECT 1 FROM notice_items i WHERE i.notice_id=n.id AND i.source NOT IN ({marks}))
                        AND {'EXISTS' if receipt == 'shown' else 'NOT EXISTS'}(SELECT 1 FROM receipts r WHERE r.notice_id=n.id AND r.state='shown')''', [*ids, *ids]).fetchone()[0]
                for source in cfg['sources']:
                    row = db.execute('SELECT COUNT(*),COALESCE(SUM(hit),0),MAX(first_seen) FROM messages WHERE source=?', (source['id'],)).fetchone()
                    cursor = db.execute('SELECT cutoff FROM checkpoints WHERE source=? ORDER BY seq DESC LIMIT 1', (source['id'],)).fetchone()
                    checkpoints[source['id']] = cursor[0] if cursor else None
                    groups.append({**source, 'messages': row[0], 'hits': row[1], 'lastSeen': row[2], 'checkpoint': cursor[0] if cursor else None})
            hits = self.hits(cfg, options, db)
            notices = self.notifications(cfg, options, db)
            span = RANGES[options['range']] or max(86400000, at - (activation or at))
            step = max(1, math.ceil(span / 24))
            start = at - span
            buckets = [{'at': start + i * step, 'count': 0} for i in range(24)]
            if db is not None:
                where, args = self.hit_where(cfg, {**options, 'since': start})
                for row in db.execute(f'SELECT MIN(23,MAX(0,CAST((m.first_seen-?)/? AS INTEGER))) AS bucket,COUNT(*) AS n FROM messages m WHERE {where} GROUP BY bucket', [start, step, *args]):
                    buckets[row['bucket']]['count'] = row['n']
            for notice in notices[:8]:
                timeline.append({'at': notice['at'], 'kind': 'notice', 'title': '本机提醒已显示' if notice['receipt'] == 'shown' else '提醒结果待核对',
                                 'detail': f"合并 {notice['hitCount']} 条命中", 'noticeId': notice['id']})
        prior = None
        for entry in control:
            value = bool(entry.get('enabled'))
            if value != prior:
                timeline.append({'at': entry.get('at_ms', at), 'kind': 'control',
                                 'title': '监控已启用' if value else '监控已暂停', 'detail': '保留历史读取进度'})
                prior = value
        if activation:
            timeline.append({'at': activation, 'kind': 'start', 'title': '开始持续记录', 'detail': '首次启用后新增消息进入记录'})
        seen, unique = set(), []
        for entry in sorted(timeline, key=lambda e: e['at'], reverse=True):
            key = (entry['at'], entry['title'])
            if key not in seen:
                seen.add(key)
                unique.append(entry)
        return {'generatedAt': at, 'activationAt': activation,
                'monitor': {'enabled': enabled, 'running': running, 'state': state, 'lastCheckAt': health.get('at_ms'),
                            'pollSeconds': cfg['pollSeconds'], 'keywords': cfg['keywords'], 'error': health.get('error'),
                            'notificationIntervalSeconds': cfg['resident']['notificationMinimumIntervalSeconds']},
                'metrics': metrics, 'groups': groups or [{**source, 'messages': 0, 'hits': 0, 'lastSeen': None, 'checkpoint': None} for source in cfg['sources']],
                'hits': hits, 'notices': notices, 'trend': buckets, 'timeline': unique[:12],
                'filters': {k: v for k, v in options.items() if k != 'since'},
                'forwarding': self.forwarding(),
                'businessRules':{'enabled':rules.get('enabled',False),'version':rules.get('version'),'cx':rules.get('cx'),**rule_counts}}

    def forwarding(self):
        config_path = self.root / 'config/wechat-wecom-forward.json'
        if not config_path.is_file():
            return None
        cfg = read_json(config_path)
        state = (self.root / cfg['stateDirectory']).resolve()
        if not state.is_relative_to(self.root / 'artifacts'):
            raise ValueError('FORWARD_STATE_OUTSIDE_PROJECT')
        health = tail_rows(state / 'health.jsonl')
        controls = tail_rows(state / 'control.jsonl')
        current = health[-1] if health else {}
        enabled = bool(controls and controls[-1].get('enabled'))
        running = self.alive(current.get('pid')) and self.clock()-current.get('at_ms',0)<90000
        result = {'enabled':enabled,'state':'disabled' if not enabled else current.get('state','connecting') if running else 'offline',
                  'botName':cfg['botName'],'recipient':cfg['recipient']['name'],'lastCheckAt':current.get('at_ms'),
                  'completedMessages':0,'messages':0,'pendingParts':0,'acceptedBatches':0,'uncertainBatches':0,'rejectedBatches':0,'recent':[]}
        path = state / 'forward.sqlite3'
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                result['messages'] = db.execute('SELECT COUNT(*) FROM items').fetchone()[0]
                result['completedMessages'] = db.execute('''SELECT COUNT(*) FROM items i WHERE i.parts>0 AND NOT EXISTS(SELECT 1 FROM parts p WHERE p.source_row=i.source_row
                    AND NOT EXISTS(SELECT 1 FROM batch_parts b JOIN receipts r ON r.batch_id=b.batch_id WHERE b.part_key=p.key AND r.state='accepted'))''').fetchone()[0]
                result['pendingParts'] = db.execute('SELECT COUNT(*) FROM parts p WHERE NOT EXISTS(SELECT 1 FROM batch_parts b WHERE b.part_key=p.key)').fetchone()[0]
                result['filteredMessages']=db.execute("SELECT COUNT(*) FROM items i WHERE i.parts=0 OR EXISTS(SELECT 1 FROM parts p JOIN batch_parts b ON b.part_key=p.key JOIN receipts r ON r.batch_id=b.batch_id WHERE p.source_row=i.source_row AND r.state='suppressed')").fetchone()[0]
                for target, receipt in (('acceptedBatches','accepted'),('rejectedBatches','rejected')):
                    result[target] = db.execute('SELECT COUNT(*) FROM receipts WHERE state=?',(receipt,)).fetchone()[0]
                result['uncertainBatches'] = db.execute("SELECT COUNT(*) FROM attempts a WHERE NOT EXISTS(SELECT 1 FROM receipts r WHERE r.batch_id=a.batch_id AND r.state IN ('accepted','rejected'))").fetchone()[0]
                result['recent'] = [dict(row) for row in db.execute('''SELECT b.at_ms AS at,COALESCE(r.state,'uncertain') AS state,
                    (SELECT COUNT(*) FROM batch_parts p WHERE p.batch_id=b.id) AS parts FROM batches b
                    LEFT JOIN receipts r ON r.seq=(SELECT MAX(r2.seq) FROM receipts r2 WHERE r2.batch_id=b.id) ORDER BY b.at_ms DESC LIMIT 8''')]
        return result

    def hit_detail(self, key):
        cfg = self.config()
        ids, marks = self.scope(cfg)
        with self.database(cfg) as db:
            row = db.execute(f'SELECT body FROM messages WHERE rowid=? AND hit=1 AND source IN ({marks})', [key, *ids]).fetchone() if db else None
            if row is None:
                raise FileNotFoundError('HIT_NOT_FOUND')
            body = json.loads(row[0])
            text = str(body.get('text', ''))
            return {'groupName': body['source_name'], 'sender': body.get('sender', ''), 'messageAt': body['time_ms'],
                    'text': text[:200000], 'truncated': len(text) > 200000, 'keywords': keyword_matches(text, cfg['keywords']),
                    'decision':business_rules.evaluate(text,business_rules.load(self.root))}

    def report(self, identity):
        if not re.fullmatch(r'WKR-[a-f0-9]{12}', identity):
            raise FileNotFoundError('REPORT_NOT_FOUND')
        cfg = self.config()
        ids, marks = self.scope(cfg)
        with self.database(cfg) as db:
            row = db.execute(f'''SELECT report FROM notices n WHERE n.id=? AND EXISTS
                (SELECT 1 FROM notice_items i WHERE i.notice_id=n.id AND i.source IN ({marks})) AND NOT EXISTS
                (SELECT 1 FROM notice_items i WHERE i.notice_id=n.id AND i.source NOT IN ({marks}))''', [identity, *ids, *ids]).fetchone() if db else None
            if row is None:
                raise FileNotFoundError('REPORT_NOT_FOUND')
            reports = self.paths(cfg)['reportsDirectory']
            path = Path(row[0]).resolve()
            # Keep old journal rows intact after a local directory relocation.
            # Only explicitly recorded report roots can map into the current root.
            if not path.exists():
                for previous in cfg['resident'].get('previousReportDirectories', []):
                    legacy = Path(previous).resolve()
                    if path.is_relative_to(legacy):
                        path = reports / path.relative_to(legacy)
                        break
            path = path.resolve(strict=True)
            if not path.is_relative_to(reports) or path.suffix != '.md':
                raise FileNotFoundError('REPORT_NOT_FOUND')
            if path.stat().st_size > 10 * 1024 * 1024:
                raise BadQuery('REPORT_TOO_LARGE')
            return path.read_bytes()

    def export(self, values):
        cfg, at = self.config(), self.clock()
        options = query_options(values, cfg, at)
        def escaped(value):
            return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '\n> ')
        lines = ['# 群消息关键词记录', '', '导出时间：' + datetime.fromtimestamp(at / 1000, ZONE).isoformat(timespec='seconds'), '']
        with self.database(cfg) as db:
            hits = self.hits(cfg, options, db, limit=5000, offset=0)
            for hit in hits['items']:
                row = db.execute('SELECT body FROM messages WHERE rowid=?', (hit['key'],)).fetchone()
                text = json.loads(row[0])['text']
                lines += ['## ' + escaped(hit['groupName']), '',
                    '- 发现时间：' + datetime.fromtimestamp(hit['discoveredAt'] / 1000, ZONE).isoformat(timespec='seconds'),
                    '- 发言人：' + escaped(hit['sender']), '- 关键词：' + '、'.join(hit['keywords']),
                    '- 提醒状态：' + {'shown': '系统已显示', 'pending': '等待合并提醒', 'uncertain': '结果待核对','review':'业务信息待核实，未提醒','filtered':'不符合业务规则，未提醒'}[hit['receipt']],
                    '- 业务判断：' + escaped(hit['decision']['reason']),
                    '', '> ' + escaped(text), '']
        lines += [f"本次导出 {len(hits['items'])} / {hits['total']} 条；最多导出 5000 条。", '']
        return '\n'.join(lines).encode('utf-8')
