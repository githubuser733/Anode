"""Bounded local rule benchmark and existing WeCom receipts; never sends or queries Alpha."""
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/wechat_monitor'))
import business_rules

POLICY={'schemaVersion':1,'version':'cx-88000-985-v1','enabled':True,'unknownAction':'dashboard-only',
        'cx':{'quantityPerPerson':88000,'minimumCnyPerWan':985}}
CASES=['收CX3万 960/万','收CX每人8.8万 985元/万','收CX8.8万 @1000','收CX两人合计17.6万 990元/万',
       '收CX3万 1000元/万；收CX8.8万 960元/万','收UA8万 1350元/万']

def summary(samples):
    ordered=sorted(samples)
    return {'samples':len(ordered),'medianMs':round(statistics.median(ordered),6),
            'p95Ms':round(ordered[min(len(ordered)-1,int(len(ordered)*.95))],6),'maxMs':round(max(ordered),6)} if ordered else None

def main():
    durations=[]
    for i in range(6000):
        start=time.perf_counter_ns()
        business_rules.evaluate(CASES[i%len(CASES)],POLICY)
        durations.append((time.perf_counter_ns()-start)/1e6)
    records=[{'input':text,'decision':business_rules.evaluate(text,POLICY)} for text in CASES]
    large=[]
    text=('备注是普通群聊文本，继续保持原文。'*600)+'收CX每人8.8万 985元/万'
    for unused in range(100):
        start=time.perf_counter_ns();business_rules.evaluate(text,POLICY);large.append((time.perf_counter_ns()-start)/1e6)
    database=ROOT/'artifacts/wechat-wecom/state/forward.sqlite3'
    receipts=[]
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            for at,state,detail in db.execute('SELECT at_ms,state,detail FROM receipts ORDER BY seq'):
                parsed=json.loads(detail)
                receipts.append({'at_ms':at,'state':state,'sendCommandMs':parsed.get('milliseconds'),'parts':parsed.get('partCount')})
    result={'result':'CX_RULE_AND_EXISTING_FORWARD_TIMING_OK','atUtc':datetime.now(timezone.utc).isoformat(),
            'shortQuotes':summary(durations),'longQuote':summary(large),'longQuoteCharacters':len(text),
            'cases':records,'existingForwardReceipts':receipts,
            'limits':['CLI timing excludes identity lookup and polling.','Existing first batch was historical replay, not a natural new-arrival latency sample.',
                      'Phone arrival and user read are not measured.'],'sendCalls':0,'modelCalls':0,'alphaCalls':0}
    directory=ROOT/'artifacts'/('wechat-cx-timing-'+uuid.uuid4().hex[:8]);directory.mkdir()
    with (directory/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({**result,'evidence':str(directory/'result.json')},ensure_ascii=True))

if __name__=='__main__':main()
