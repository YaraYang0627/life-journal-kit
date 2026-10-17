'use strict';
const data = JSON.parse(document.getElementById('journal-data').textContent);
const $ = id => document.getElementById(id);
const entries = [...data.entries].sort((a,b)=>a.date.localeCompare(b.date));
const byDate = new Map(entries.map(e=>[e.date,e]));
const months = [...new Set(entries.map(e=>e.date.slice(0,7)))];
const today = new Date();
$('month').value = months.at(-1) || `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}`;
$('demo-banner').hidden = !data.demo;
let activeDate = null;
function el(tag, cls, text) {const e=document.createElement(tag); if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function empty(parent,title,detail){const e=el('div','empty');e.append(el('strong','',title),el('span','',detail));parent.append(e);}
function paragraphs(entry){return entry.sections.flatMap(s=>s.paragraphs);}
function openDay(target){
  activeDate=target;const entry=byDate.get(target);$('reader-date').textContent=target;
  $('reader-title').textContent=entry?entry.title:'这一天还没有终稿';
  const content=$('reader-content');content.replaceChildren();
  if(!entry){content.append(el('p','','可以在 Codex 中补充这个日期的经历。地图不会用其他日子的记录填补这里。'));}
  else {
    for(const section of entry.sections){content.append(el('h3','',section.heading));for(const p of section.paragraphs){content.append(el('p','',p.text));}}
    const details=el('details');details.append(el('summary','',`查看来源 · ${entry.sources.length} 条原始素材`));
    for(const r of entry.sources){const item=el('div','source-item');item.append(el('div','',`${r.date} · ${r.source}`),el('p','',r.text),el('small','',r.id));details.append(item);}
    content.append(details);
  }
  const i=entries.findIndex(e=>e.date===target);
  $('previous-entry').disabled=i<=0;$('next-entry').disabled=i<0||i===entries.length-1;
  if(!$('reader').open)$('reader').showModal();
  $('reader').scrollTop=0;
}
function moveEntry(offset){const i=entries.findIndex(e=>e.date===activeDate);if(entries[i+offset])openDay(entries[i+offset].date);}
$('previous-entry').onclick=()=>moveEntry(-1);$('next-entry').onclick=()=>moveEntry(1);
$('close-reader').onclick=()=>$('reader').close();
$('guide-button').onclick=()=>$('guide').showModal();$('close-guide').onclick=()=>$('guide').close();
function render(){
  const m=$('month').value;if(!/^\d{4}-\d{2}$/.test(m))return;
  const [year,number]=m.split('-').map(Number);
  const subset=entries.filter(e=>e.date.startsWith(m));
  const query=$('search').value.trim().toLocaleLowerCase();
  const filtered=subset.filter(e=>(e.title+' '+paragraphs(e).map(p=>p.text).join(' ')+' '+e.sources.map(r=>r.text).join(' ')).toLocaleLowerCase().includes(query));
  $('period').textContent=`${year} / ${String(number).padStart(2,'0')} · PERSONAL ATLAS`;
  $('title').textContent=`${number}月，留下来的日子`;
  $('coverage').textContent=subset.length?`${subset.length} 天已有终稿 · 最后记录于 ${subset.at(-1).date}${query?` · 找到 ${filtered.length} 篇`:''}`:'这个月还没有终稿。从一段经历开始就好。';
  const cal=$('calendar');cal.replaceChildren();
  for(const name of ['一','二','三','四','五','六','日'])cal.append(el('div','weekday',name));
  const start=(new Date(year,number-1,1).getDay()+6)%7;
  for(let i=0;i<start;i++)cal.append(el('span'));
  const total=new Date(year,number,0).getDate();
  for(let d=1;d<=total;d++){
    const target=`${m}-${String(d).padStart(2,'0')}`;const exists=byDate.has(target);
    const b=el('button',exists?'recorded':'',String(d));b.setAttribute('aria-label',`${target} ${exists?'阅读日记':'暂无终稿'}`);b.onclick=()=>openDay(target);cal.append(b);
  }
  const list=$('day-list');list.replaceChildren();
  for(const e of [...filtered].reverse()){
    const b=el('button','day-card');const num=el('span','day-num',e.date.slice(-2));num.append(el('small','',`${number}月 · 终稿`));
    const copy=el('div','day-copy');copy.append(el('h3','',e.title),el('p','',paragraphs(e)[0]?.text||''));b.append(num,copy,el('span','arrow','↗'));b.onclick=()=>openDay(e.date);list.append(b);
  }
  if(!filtered.length)empty(list,query?'没有找到这段记忆':'留一个位置给今天',query?'换一个词试试；搜索仅覆盖当月终稿和来源。':'先补充经历、确认初稿，再更新地图。');
  const feelings=$('feeling-list');feelings.replaceChildren();
  const threads=$('thread-list');threads.replaceChildren();
  // Only show kinds explicitly supplied by the recorder. Never infer emotion or progress.
  for(const e of filtered){for(const r of e.sources){
    if(r.kind==='feeling'){const b=el('button','feeling-card');b.append(el('blockquote','',r.text),el('span','stamp',`${e.date} · 看这一天 ↗`));b.onclick=()=>openDay(e.date);feelings.append(b);}
    if(r.kind==='thread'||r.kind==='plan'){const row=el('div','thread-row');row.append(el('time','',e.date.slice(5)));const b=el('button');b.append(el('span','tag',r.kind==='plan'?'当时的计划':'当时未完成'),el('p','',r.text));b.onclick=()=>openDay(e.date);row.append(b);threads.append(row);}
  }}
  if(!feelings.children.length)empty(feelings,'没有明确记录的感受',query?'当前搜索结果没有感受素材。':'有日记也可以没有感受记录；这里不推测心情。');
  if(!threads.children.length)empty(threads,'没有记录计划或未完成事项','不会把缺少进展记录解释为事情已经结束。');
  const review=$('review-body');review.replaceChildren();const report=data.reviews[m];
  $('review-count').textContent=report&&!report.stale?`${report.count} 天 · 截至 ${report.through}`:'';
  if(!report)empty(review,'这个月还没有回顾','在 Codex 中请求生成本月回顾，再更新地图。月度回顾不受上方搜索筛选影响。');
  else if(report.stale)empty(review,'月度回顾需要更新','终稿发生了变化。请重新生成回顾，再更新地图，避免沿用旧结论。');
  else{const panel=el('div','review-panel');for(const s of report.sections){panel.append(el('h3','',s.heading));for(const p of s.paragraphs){const b=el('button','review-line',p.text);b.append(el('small','',`${p.date} · 回到来源 ↗`));b.onclick=()=>openDay(p.date);panel.append(b);}}review.append(panel);}
}
function shift(offset){const [y,m]=$('month').value.split('-').map(Number);const d=new Date(y,m-1+offset,1);$('month').value=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;render();}
$('prev').onclick=()=>shift(-1);$('next').onclick=()=>shift(1);$('month').onchange=render;$('search').oninput=render;
render();
