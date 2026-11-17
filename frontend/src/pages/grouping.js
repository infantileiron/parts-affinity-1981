import {state} from '../stores/session.js';
import {esc} from '../utils/html.js';
import {badge} from '../components/common/status.js';
import {select} from '../components/common/fields.js';

function jobName(id){const j=state.group.jobs.find(x=>x.id===id);return j?`${j.code}（${j.equipment}）`:`#${id}`}
function pct(v){return (v*100).toFixed(1)+'%'}

export function render(){
 const g=state.group;
 if(!g)return '<p class="notice">归组数据加载中。</p>';
 const active=g.active,draft=g.draft;
 const groups=(draft||active)?.groups||[];
 const multi=groups.filter(x=>x.job_ids.length>1);
 return `<div class="split">
 <section class="panel">
  <h2>故障事件归组 ${active?badge('生效版 #'+active.id):badge('尚未发布')} ${draft?badge('草稿待发布'):''}</h2>
  <p class="notice">同一故障拆成的多张返修单通过作业间关联连为同一事件：并查集求连通组，组内用件取并集，每组只计一次；跨设备关联会被拒绝。关联可撤销，撤销后草稿自动拆组重算。</p>
  <div class="metrics">
   <div><strong>${g.links.filter(l=>l.status==='active').length}</strong>有效关联</div>
   <div><strong>${multi.length}</strong>多单事件组</div>
   <div><strong>${groups.length}</strong>事件组总数</div>
   <div><strong>${active?'#'+active.id:'—'}</strong>当前生效版</div>
  </div>
  <h3>草稿连通组（${draft?'草稿版 #'+draft.id:'无草稿，发布后生效'}）</h3>
  ${groups.length?`<div class="scroll"><table><thead><tr><th>事件组</th><th>设备</th><th>关联返修单</th><th>组内用件并集</th></tr></thead><tbody>
   ${groups.map(gr=>`<tr><td>#${gr.group_id}${gr.job_ids.length>1?' <span class="tag">连单</span>':''}</td><td>${esc(gr.equipment)}</td><td>${gr.members.map(m=>esc(m.code)).join('、')}</td><td>${esc(gr.parts.join(' / '))}</td></tr>`).join('')}
  </tbody></table></div>`:'<p class="notice">暂无关联，每张作业按独立故障事件解释。</p>'}
  <h3>作业间关联</h3>
  <div class="scroll"><table><thead><tr><th>关联</th><th>作业 A</th><th>作业 B</th><th>状态</th><th>操作</th></tr></thead><tbody>
   ${g.links.map(l=>`<tr><td>#${l.id}</td><td>${esc(jobName(l.left_job_id))}</td><td>${esc(jobName(l.right_job_id))}</td><td>${badge(l.status==='active'?'有效':'已撤销')}</td><td>${l.status==='active'?`<button data-revoke="${l.id}">撤销拆组</button>`:'—'}</td></tr>`).join('')||'<tr><td colspan="5">暂无关联记录。</td></tr>'}
  </tbody></table></div>
  <h3>历史版本（旧批次按原组解释）</h3>
  ${g.archived.length?g.archived.map(v=>`<details class="study"><summary>归档版 #${v.id} · ${v.groups.length} 个事件组 · 发布于 ${esc(v.published||'')}</summary><table><thead><tr><th>事件组</th><th>设备</th><th>返修单</th><th>用件并集</th></tr></thead><tbody>${v.groups.map(gr=>`<tr><td>#${gr.group_id}</td><td>${esc(gr.equipment)}</td><td>${gr.members.map(m=>esc(m.code)).join('、')}</td><td>${esc(gr.parts.join(' / '))}</td></tr>`).join('')}</tbody></table></details>`).join(''):'<p class="notice">尚无归档版本。发布新版后旧生效版自动归档并完整保留。</p>'}
 </section>
 <aside class="side">
  <h2>连单 / 发布 / 对账</h2>
  <form id="link">
   ${select('返修单 A（同设备）','left_job_id',g.jobs.map(j=>[j.id,`${j.code} · ${j.equipment}`]))}
   ${select('返修单 B（同设备）','right_job_id',g.jobs.map(j=>[j.id,`${j.code} · ${j.equipment}`]))}
   <button class="primary">建立关联（连单）</button>
   <p>跨设备、自关联与重复关联都会被拒绝；环与桥边由连通组自动归并。</p>
  </form>
  <p><button id="publish" class="primary" ${draft?'':'disabled'}>发布草稿为生效版</button></p>
  <p><button id="reconcile">备件包联用证据对账</button></p>
  <div id="reconcile-box"></div>
 </aside>
</div>`;
}

export function renderReconcile(){
 const box=document.querySelector('#reconcile-box');
 if(!box||!state.reconcile)return;
 const rc=state.reconcile;
 box.innerHTML=`<h3>对账结果</h3>
  <p class="notice">生效版 ${rc.active?'#'+rc.active.id:'无'} · 草稿 ${rc.draft?'#'+rc.draft.id:'无'} · 关联 ${rc.links.filter(l=>l.status==='active').length} 条</p>
  ${rc.kits.map(k=>`<article class="rec"><h3>${esc(k.name)} ${badge(k.status)}</h3>${k.evidence?`<p>证据版 #${k.evidence.group_version_id} · N=${k.evidence.group_count} · support ${pct(k.evidence.support)} · confidence ${pct(k.evidence.confidence)} · lift ${Number(k.evidence.lift).toFixed(3)}</p><p>${k.evidence.supported?'<span class="tag">证据支持</span>':esc('失支持：'+k.evidence.reasons.join('；'))}</p>`:'<p class="notice">发布归组前创建，尚无新版证据。</p>'}</article>`).join('')||'<p class="notice">暂无备件包。</p>'}`;
}
