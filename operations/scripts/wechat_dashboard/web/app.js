'use strict';
const $ = (query) => document.querySelector(query);
const NS = 'http://www.w3.org/2000/svg';
const labels = {overview: ['监控总览', '关注的消息、命中与提醒，汇集在这里。'], hits: ['命中记录', '按群聊、关键词和提醒结果查找记录。'], notices: ['提醒记录', '查看本机通知回执，下载对应的完整摘录。']};
const receipts = {shown: '系统已显示', pending: '等待合并', uncertain: '结果待核对',review:'业务待核实',filtered:'不符合业务规则'};
const states = {healthy: '正在监控', paused: '监控已暂停', offline: '读取进程未运行', delayed: '检查有所延迟', degraded: '正在恢复连接', connecting: '正在连接监控'};
const state = {view: 'overview', range: '24h', group: 'all', keyword: 'all', q: '', receipt: 'all', page: 1, snapshot: null, scopeKey: ''};
let timer, toastTimer, searchTimer, controller, lastSuccess;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function icon(name, className) {
  const svg = document.createElementNS(NS, 'svg');
  if (className) svg.setAttribute('class', className);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(NS, 'use'); use.setAttribute('href', '#i-' + name); svg.append(use); return svg;
}
function date(at, full = false) {
  if (!at) return '—';
  return new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', ...(full ? {month:'2-digit',day:'2-digit'} : {}),hour:'2-digit',minute:'2-digit',...(full ? {} : {second:'2-digit'}),hour12:false}).format(new Date(at));
}
function count(value) { return new Intl.NumberFormat('zh-CN').format(value ?? 0); }
function metric(id, value, unit = '') { const node = $(id); node.replaceChildren(document.createTextNode(count(value))); if (unit) node.append(el('span', '', unit)); }
function notify(text) { clearTimeout(toastTimer); $('#toast').textContent = text; $('#toast').hidden = false; toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 3500); }
function empty(title, detail, type = 'message') { const node = el('div','empty'); node.append(icon(type),el('strong','',title),el('p','',detail)); return node; }
function pill(value) { return el('span', 'state-pill ' + (receipts[value] ? value : 'uncertain'), receipts[value] || '结果待核对'); }

function highlighted(text, words) {
  const fragment = document.createDocumentFragment();
  const ranges = [];
  for (const word of words) {
    const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const expression = /^[a-z]+$/i.test(word) ? new RegExp('(?<![a-z])' + escaped + '(?![a-z])','gi') : new RegExp(escaped,'gi');
    for (const match of text.matchAll(expression)) if (match[0].length) ranges.push([match.index, match.index + match[0].length]);
  }
  ranges.sort((a,b) => a[0] - b[0]);
  let at = 0;
  for (const [start, end] of ranges) { if (start < at) continue; fragment.append(document.createTextNode(text.slice(at,start)), el('mark','',text.slice(start,end))); at = end; }
  fragment.append(document.createTextNode(text.slice(at))); return fragment;
}

function table(items, compact = false) {
  if (!items.length) return empty(state.q || state.keyword !== 'all' || state.receipt !== 'all' ? '没有符合筛选的记录' : '还没有关键词命中', '新发现的「收 / UA」命中会自动归档到这里。');
  const wrap = el('div','table-wrap'), table = el('table','records-table'), head = el('thead'), tr = el('tr');
  for (const title of ['群聊与消息','关键词','提醒状态','发现时间']) tr.append(el('th','',title));
  head.append(tr); table.append(head); const body = el('tbody');
  for (const hit of items.slice(0, compact ? 6 : 20)) {
    const row = el('tr');
    const message = el('td'), meta = el('div','message-meta'); meta.append(el('span','',hit.groupName),el('span','sender','· ' + (hit.sender || '未命名成员')));
    const button = el('button','message-preview'); button.dataset.key = String(hit.key); button.setAttribute('aria-label','查看命中详情：' + hit.groupName); button.append(highlighted(hit.text, hit.keywords));
    message.append(meta,button);
    const keywords = el('td'); hit.keywords.forEach(word => keywords.append(el('span','keyword-pill',word)));
    const status = el('td'); status.append(pill(hit.receipt));
    if(hit.decision&&hit.decision.state!=='not_applicable')status.append(el('div','rule-note',hit.decision.reason));
    const time = el('td','record-time',date(hit.discoveredAt)); time.title = date(hit.discoveredAt,true);
    row.append(message,keywords,status,time); body.append(row);
  }
  table.append(body); wrap.append(table); return wrap;
}

function chart(buckets) {
  const svg = document.createElementNS(NS,'svg'); svg.setAttribute('viewBox','0 0 600 160'); svg.setAttribute('role','img');
  const total = buckets.reduce((sum,b) => sum+b.count,0); svg.setAttribute('aria-label','所选时间范围共有 ' + total + ' 条关键词命中');
  function shape(tag, attrs, text) { const item = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([key,value]) => item.setAttribute(key,String(value))); if (text !== undefined) item.textContent = text; svg.append(item); return item; }
  const max = Math.max(3,...buckets.map(b => b.count));
  for (let i=0;i<4;i++) { const y = 12+i*35; shape('line',{x1:32,y1:y,x2:590,y2:y,stroke:'#eaf0eb','stroke-dasharray':'3 4','stroke-width':1}); shape('text',{x:22,y:y+3,'text-anchor':'end'},Math.round(max*(3-i)/3)); }
  buckets.forEach((bucket,i) => { const height = bucket.count ? Math.max(3,bucket.count/max*104) : 1; const rect = shape('rect',{x:39+i*23,y:117-height,width:12,height,rx:3,fill:bucket.count ? '#86af96' : '#e7efe9'}); const title = document.createElementNS(NS,'title'); title.textContent = date(bucket.at,true)+' · '+bucket.count+' 条命中'; rect.append(title); if (bucket.count) shape('text',{x:45+i*23,y:111-height,'text-anchor':'middle',fill:'#5d876b'},bucket.count); });
  [0,6,12,18,23].forEach(i => { shape('text',{x:45+i*23,y:143,'text-anchor':'middle'},new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(buckets[i].at))); });
  $('#trend-chart').replaceChildren(svg); $('#trend-total').textContent = '共 ' + count(total) + ' 条命中';
}

function sources(data) {
  const scopeKey = JSON.stringify([data.groups.map(g=>[g.id,g.name]),data.monitor.keywords]);
  if (state.scopeKey !== scopeKey) {
    state.scopeKey = scopeKey;
    $('#sidebar-groups').replaceChildren(); $('#group-filter').replaceChildren(new Option('全部群聊','all')); $('#keyword-filter').replaceChildren(new Option('全部关键词','all'));
    data.groups.forEach(group => { const node=el('div','scope-item'); const name=el('span','',group.name); name.title=group.name; node.append(el('i'),name); $('#sidebar-groups').append(node); $('#group-filter').append(new Option(group.name,group.id)); });
    data.monitor.keywords.forEach(word => $('#keyword-filter').append(new Option(word,word)));
    $('#group-filter').value=state.group; $('#keyword-filter').value=state.keyword;
  }
  $('#source-count').textContent = data.groups.length + ' 个';
  $('#group-cards').replaceChildren(...data.groups.map((group,i) => { const item=el('div','group-card'); const avatar=el('div','group-avatar'+(i ? ' air' : ''),i ? '航' : '票'); const info=el('div','group-info'), title=el('strong','',group.name); title.title=group.name; info.append(title,el('p','',group.checkpoint ? '已检查至 '+date(group.checkpoint) : '等待首次检查')); const tally=el('div','group-tally',count(group.hits)); tally.append(el('small','','累计命中')); item.append(avatar,info,tally); return item; }));
}

function activities(items) {
  $('#activity-feed').replaceChildren(...items.slice(0,5).map(entry => { const node=el('div','activity-entry'); node.append(el('strong','',entry.title),el('time','',date(entry.at)),el('p','',entry.detail)); return node; }));
  if (!items.length) $('#activity-feed').append(empty('等待运行记录','启停和提醒将在这里留下记录。','activity'));
}

function notices(items) {
  $('#notice-list').replaceChildren();
  if (!items.length) { $('#notice-list').append(empty('尚无本机提醒','首次发现命中后会立即请求本机提醒，并在这里保存回执。','bell')); return; }
  for (const item of items) {
    const card=el('article','notice-card'), top=el('div','notice-card-top'); top.append(pill(item.receipt),el('time','',date(item.at,true)));
    card.append(top,el('h3','','关键词合并提醒 · '+item.hitCount+' 条'),el('p','',item.text));
    const bottom=el('div','notice-card-bottom'), download=el('a','button secondary','下载摘录'); download.href='/api/report/'+encodeURIComponent(item.id); download.download=item.id+'.md'; download.prepend(icon('file'));
    bottom.append(el('span','',item.id),download); card.append(bottom); $('#notice-list').append(card);
  }
}

function render(data) {
  state.snapshot=data; lastSuccess=Date.now();
  metric('#metric-messages',data.metrics.messages); metric('#metric-hits',data.metrics.hits,'条'); metric('#metric-shown',data.metrics.shown,'次'); metric('#metric-pending',data.metrics.pending,'条');
  $('#nav-count').textContent=count(data.metrics.hits); $('#keyword-summary').textContent=data.monitor.keywords.join(' / ')+' · 任一命中';
  $('#pending-summary').classList.toggle('attention',data.metrics.uncertain>0); $('#pending-summary').textContent=data.metrics.uncertain ? data.metrics.uncertain+' 次提醒结果待核对' : '密集命中合并，避免连续弹窗';
  $('#live-state').textContent=states[data.monitor.state] || '状态待核对';
  $('#live-orbit').className='live-orbit'+(data.monitor.state==='healthy'?'':data.monitor.state==='paused'||data.monitor.state==='offline'?' offline':' warning');
  $('#live-rule').textContent='每 '+data.monitor.pollSeconds+' 秒检查 · 首条命中即提醒';
  $('#last-updated').textContent='最近检查 '+date(data.monitor.lastCheckAt);
  $('#recording-since').textContent=data.activationAt ? '开始记录于 '+date(data.activationAt,true) : '等待首次启用监控';
  $('#error-banner').hidden=true;
  if (data.monitor.error) { $('#error-banner').textContent='监控正在恢复连接（'+data.monitor.error+'）。已保存的记录仍可查看。'; $('#error-banner').hidden=false; }
  sources(data); chart(data.trend); activities(data.timeline); notices(data.notices); forwarding(data.forwarding);
  const rules=data.businessRules, panel=$('#business-rules-panel');panel.hidden=!rules?.enabled;
  if(rules?.enabled)panel.textContent='CX：每人 '+(rules.cx.quantityPerPerson/10000)+' 万规格，≥'+rules.cx.minimumCnyPerWan+' 元/万 · 待核实 '+count(rules.review)+' 条 · 不符合 '+count(rules.excluded)+' 条（只保留在看板）';
  $('#recent-hits').replaceChildren(table(data.hits.items,true)); $('#all-hits').replaceChildren(table(data.hits.items));
  const pages=Math.max(1,Math.ceil(data.hits.total/data.hits.pageSize));
  $('#page-summary').textContent='共 '+count(data.hits.total)+' 条符合条件的记录'; $('#page-number').textContent=state.page+' / '+pages;
  $('#previous').disabled=state.page<=1; $('#next').disabled=state.page>=pages;
}

function forwarding(channel) {
  $('#forwarding-panel').hidden=!channel;
  if(!channel)return;
  const labels={healthy:'持续转发中',disabled:'已停用',paused_by_monitor:'随监控暂停',degraded:'连接待恢复',attention:'发送结果待核对',offline:'转发进程未运行',connecting:'正在启动'};
  $('#forwarding-target').textContent=channel.botName+' → '+channel.recipient+' · 原文完整保留，超长分段';
  $('#forwarding-state').textContent=labels[channel.state]||'状态待核对';
  const issues=channel.uncertainBatches+channel.rejectedBatches;
  $('#forwarding-state').classList.toggle('warning',issues>0||['degraded','offline'].includes(channel.state));
  const accepted=el('span','','接口已接受 '),total=el('strong','',count(channel.completedMessages));accepted.append(total,document.createTextNode(' 条完整命中'));
  $('#forwarding-counts').replaceChildren(accepted,el('span','','待发送 '+count(channel.pendingParts)+' 段'),el('span','','业务规则过滤 '+count(channel.filteredMessages)+' 条'),el('span','',issues?'待核对 '+issues+' 批':'无结果不确定的派发'));
  $('#forwarding-history').replaceChildren(...channel.recent.map(item=>{const row=el('div','forwarding-event');row.append(el('time','',date(item.at,true)),el('span','',item.parts+' 段'),el('span','',({accepted:'企微接口已接受',rejected:'接口拒绝，需处理',uncertain:'结果未知，未重试',suppressed:'业务规则过滤，未发送'})[item.state]||'状态待核对'));return row;}));
  if(!channel.recent.length)$('#forwarding-history').append(el('p','','尚未发生派发。'));
}

function params() { return new URLSearchParams({range:state.range,group:state.group,q:state.q,keyword:state.keyword,state:state.receipt,page:String(state.page)}); }
async function refresh() {
  clearTimeout(timer); if (controller) controller.abort(); const active=new AbortController(); controller=active;
  $('#refresh').classList.add('refreshing');
  try { const response=await fetch('/api/snapshot?'+params(),{cache:'no-store',signal:active.signal}); if(!response.ok) throw new Error('records unavailable'); const data=await response.json(); render(data); }
  catch(error) { if(error.name==='AbortError') return; $('#error-banner').hidden=false; $('#error-banner').textContent='暂时无法更新看板，正在重试。'+(lastSuccess?' 上次同步 '+date(lastSuccess)+'，已显示的记录保留。':' 请确认看板服务正在运行。'); $('#live-orbit').className='live-orbit warning'; }
  finally { if(controller===active){$('#refresh').classList.remove('refreshing'); timer=setTimeout(refresh,document.hidden?5000:2000);} }
}

function view() {
  const selected=location.hash.slice(1); state.view=Object.hasOwn(labels,selected)?selected:'overview';
  Object.keys(labels).forEach(name=>{$('#'+name+'-view').hidden=name!==state.view;});
  document.querySelectorAll('[data-view]').forEach(link=>{link.classList.toggle('active',link.dataset.view===state.view); if(link.dataset.view===state.view) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');});
  $('#page-title').textContent=labels[state.view][0]; $('#crumb').textContent=labels[state.view][0]; $('#page-description').textContent=labels[state.view][1];
  $('.section-caption').textContent={overview:'记录概览',hits:'全部命中记录',notices:'本机提醒回执'}[state.view];
  if(state.view!=='hits'){state.q='';state.keyword='all';state.receipt='all';state.page=1;$('#search').value='';$('#keyword-filter').value='all';$('#state-filter').value='all';}
  refresh();
}

async function detail(key) {
  try {
    const response=await fetch('/api/hit/'+encodeURIComponent(key)); if(!response.ok) throw new Error('unavailable'); const data=await response.json();
    const meta=el('div','detail-meta'); meta.append(el('strong','',data.groupName),el('span','',data.sender),el('span','',date(data.messageAt,true)));
    const text=el('div','detail-text',data.text); const foot=el('div','detail-foot','关键词：'+data.keywords.join(' / ')+(data.truncated?' · 内容较长，仅展示前 200,000 字符':''));
    const decision=el('div','rule-note',data.decision&&data.decision.state!=='not_applicable'?data.decision.label+'：'+data.decision.reason:'沿用关键词规则');
    $('#detail-content').replaceChildren(meta,decision,text,foot); $('#detail-dialog').showModal();
  } catch(error) { notify('暂时无法读取这条记录，请稍后重试。'); }
}

async function exportRecords() {
  const button=$('#export'); button.disabled=true;
  try { const response=await fetch('/api/export.md?'+params()); if(!response.ok) throw new Error('unavailable'); const blob=await response.blob(); const url=URL.createObjectURL(blob); const anchor=el('a'); anchor.href=url; anchor.download='群消息关键词记录.md'; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(()=>URL.revokeObjectURL(url),5000); notify('记录已导出为 Markdown。'); }
  catch(error) { notify('导出未完成，请稍后重试。'); }
  finally { button.disabled=false; }
}

$('#today').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'long',day:'numeric',weekday:'long'}).format(new Date());
$('#refresh').addEventListener('click',refresh); $('#export').addEventListener('click',exportRecords);
for(const [id,key] of [['group-filter','group'],['range-filter','range'],['keyword-filter','keyword'],['state-filter','receipt']]) $('#'+id).addEventListener('change',event=>{state[key]=event.target.value;state.page=1;refresh();});
$('#search').addEventListener('input',event=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.q=event.target.value;state.page=1;refresh();},250);});
$('#previous').addEventListener('click',()=>{state.page=Math.max(1,state.page-1);refresh();}); $('#next').addEventListener('click',()=>{state.page++;refresh();});
document.addEventListener('click',event=>{const button=event.target.closest('[data-key]');if(button)detail(button.dataset.key);});
$('#close-dialog').addEventListener('click',()=>$('#detail-dialog').close()); $('#detail-dialog').addEventListener('click',event=>{if(event.target===$('#detail-dialog')){const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)event.target.close();}});
const businessRulePanel=el('section','business-rule-panel');businessRulePanel.id='business-rules-panel';businessRulePanel.hidden=true;businessRulePanel.setAttribute('aria-label','业务筛选规则');$('#forwarding-panel').after(businessRulePanel);
$('#state-filter').append(new Option('业务待核实','review'),new Option('不符合业务规则','filtered'));
window.addEventListener('hashchange',view); document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
view();
