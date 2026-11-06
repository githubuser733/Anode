"""Local, conservative quote filtering. Message text is data, never an instruction."""
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata

ROOT=Path(__file__).resolve().parents[2]
NUMBER=r'(?<![\d.])(?P<number>\d+(?:\.\d+)?)'
QUANTITY=re.compile(NUMBER+r'\s*(?P<unit>万分|万|w|千分|千|k|积分|分)(?![a-z])',re.I)
PRICE=re.compile(NUMBER+r'\s*(?:元|人民币|rmb|cny|块)?\s*/\s*(?:1\s*)?(?P<unit>万分|万|w|积分|分)(?![a-z])',re.I)
PRICE_REVERSE=re.compile(r'每\s*(?:1\s*)?(?P<unit>万分|万|w|积分|分)\s*(?P<number>\d+(?:\.\d+)?)\s*(?:元|人民币|rmb|cny|块)',re.I)
PER_PERSON=re.compile(r'每(?:个)?(?:人|位|账户|账号)|单(?:人|账户|账号)|一人|1人|每位乘客|各')

@lru_cache(maxsize=8)
def _cached(path,modified,size):
    value=json.loads(Path(path).read_text('utf-8-sig'))
    if value.get('schemaVersion')!=1 or value.get('unknownAction')!='dashboard-only':
        raise ValueError('BUSINESS_RULE_POLICY_INVALID')
    rule=value['cx']
    if not isinstance(value.get('enabled'),bool) or Decimal(str(rule['quantityPerPerson']))<=0 or Decimal(str(rule['minimumCnyPerWan']))<=0:
        raise ValueError('BUSINESS_RULE_LIMIT_INVALID')
    return value

def load(root=ROOT):
    path=Path(root)/'config/wechat-business-rules.json'
    if not path.is_file():return {'enabled':False}
    stat=path.stat()
    return _cached(str(path),stat.st_mtime_ns,stat.st_size)

def evaluate(text,policy):
    result={'state':'not_applicable','label':'沿用关键词规则','reason':'非 CX 消息或业务规则未启用'}
    if not policy.get('enabled'):return result
    value=unicodedata.normalize('NFKC',str(text)).replace('\u200b','').casefold()
    value=re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))','',value)
    if not re.search(r'(?<![a-z])cx(?![a-z])',value):return result
    result={'state':'review','label':'待核实','reason':'CX 报价信息不完整','program':'CX','ruleVersion':policy['version']}
    def finish(state,reason,**extra):
        return {**result,'state':state,'label':{'eligible':'符合 CX 规则','excluded':'不符合 CX 规则','review':'待核实'}[state],'reason':reason,**extra}
    if len(value)>200000:return finish('review','原文过长，未自动判断')
    if re.search(r'\d(?:\.\d+)?\s*[-~～至到]\s*\d',value):return finish('review','存在价格或数量范围，需确认具体报价')
    if re.search(r'(?:不收|暂停收|停止收|已收满|收完了|已成交|取消收)',value):return finish('excluded','明确否定、停止或已完成的收购')
    if not re.search(r'收购|求购|求收|收(?!到|件|款|货|益|费)',value):return finish('review','未明确为收购需求')
    if re.search(r'(?<![a-z])(?:ua|aa|ba|sq)(?![a-z])|美元|美金|港元|港币|usdt|hkd|usd|eur|gbp|€|£',value):
        return finish('review','混合品种或非人民币报价，需人工确认对应关系')
    prices=list(PRICE.finditer(value))+list(PRICE_REVERSE.finditer(value))
    numbers={Decimal(m['number'])*(10000 if m['unit'] in ('分','积分') else 1) for m in prices}
    if not numbers:return finish('review','缺少明确的人民币每万单价；不把裸数字或总价当单价')
    if len(numbers)!=1:return finish('review','有多个不同单价，需确认对应规格')
    price=numbers.pop()
    quantities=[]
    for item in QUANTITY.finditer(value):
        if any(item.start()<p.end() and p.start()<item.end() for p in prices):continue
        unit=item['unit']
        amount=Decimal(item['number'])*(10000 if unit in ('万','万分','w') else 1000 if unit in ('千','千分','k') else 1)
        before=value[max(0,item.start()-12):item.start()]
        after=value[item.end():item.end()+6]
        per_person=bool(PER_PERSON.search(before) or re.match(r'\s*(?:/人|每人|一人|/位)',after))
        approximate=bool(re.search(r'约\s*$|大概\s*$|至少\s*$|至多\s*$',before) or re.match(r'\s*(?:左右|以上|以下|起|到|至|[-~～])',after))
        total=bool(re.search(r'共|合计|总量|总计|一共',before))
        quantities.append((amount,per_person,approximate,total))
    explicit=[q for q in quantities if q[1]]
    if not explicit and re.search(r'(?:[2-9]\d*|[两二三四五六七八九十多几])\s*(?:人|位|个账户|个账号)',value):
        return finish('review','多人报价未明确每人数量，不能把合计当单人规格',priceCnyPerWan=str(price))
    chosen=explicit or quantities
    if not chosen:return finish('review','缺少可确认的单人规格',priceCnyPerWan=str(price))
    amounts={q[0] for q in chosen}
    if len(amounts)!=1 or any(q[2] for q in chosen):return finish('review','数量有多个规格或范围，需确认单人规格',priceCnyPerWan=str(price))
    if not explicit and any(q[3] for q in chosen):return finish('review','只说明多人合计，不能当作单人规格',priceCnyPerWan=str(price))
    amount=amounts.pop()
    expected=Decimal(str(policy['cx']['quantityPerPerson']))
    minimum=Decimal(str(policy['cx']['minimumCnyPerWan']))
    extra={'quantityPerPerson':str(amount),'priceCnyPerWan':str(price)}
    if amount!=expected:return finish('excluded',f'单人规格不是 {expected/10000:g} 万',**extra)
    if price<minimum:return finish('excluded',f'每万单价低于 {minimum:g} 元',**extra)
    return finish('eligible',f'单人 {expected/10000:g} 万，单价 {price:g} 元/万',**extra)

def alert_allowed(decision):
    return decision['state'] in ('eligible','not_applicable')
