import * as g from '../src/pages/grouping.js';
import * as k from '../src/pages/kits.js';
import * as m from '../src/pages/mining.js';
import {state} from '../src/stores/session.js';
globalThis.document={querySelector:()=>({innerHTML:''})};
state.user={role:'admin'};
state.group={
 jobs:[{id:1,code:'J1',equipment:'泵组'},{id:2,code:'J2',equipment:'泵组'},{id:3,code:'C1',equipment:'压缩机'}],
 links:[{id:1,left_job_id:1,right_job_id:2,status:'active'},{id:2,left_job_id:1,right_job_id:2,status:'revoked'}],
 draft:{id:7,groups:[{group_id:1,job_ids:[1,2],equipment:'泵组',parts:['轴承','润滑脂'],members:[{code:'J1'},{code:'J2'}]},{group_id:2,job_ids:[3],equipment:'压缩机',parts:['滤芯'],members:[{code:'C1'}]}]},
 active:{id:6,status:'active',groups:[],published:'2026-11-17T00:00:00Z'},
 archived:[{id:5,status:'archived',published:'2026-11-16',groups:[{group_id:1,job_ids:[1,2],equipment:'泵组',parts:['轴承'],members:[{code:'J1'},{code:'J2'}]}]}]
};
state.reconcile={active:{id:6},draft:{id:7},links:state.group.links,
 kits:[{id:1,name:'暂停包',status:'suspended',parts:['轴承','润滑脂'],note:'n',evidence:{group_version_id:6,group_count:3,support:0.1,confidence:0.2,lift:0.5,supported:false,reasons:['支持度低于来源批次阈值']}},
       {id:2,name:'在架包',status:'approved',parts:['垫片'],note:'n',evidence:{group_version_id:6,group_count:3,support:0.8,confidence:0.9,lift:2.1,supported:true,reasons:[]}}]};
state.kits=state.reconcile.kits;state.rules=[];
const html=g.render();
for(const needle of ['故障事件归组','撤销拆组','发布草稿为生效版','备件包联用证据对账','归档版 #5','已撤销','连单'])
 if(!html.includes(needle))throw new Error('missing '+needle);
g.renderReconcile();
const kh=k.render();
if(!kh.includes('已暂停')||!kh.includes('禁止签核')||!kh.includes('失支持'))throw new Error('suspended kit render missing');
state.selected={id:1,name:'批次',state:'done',equipment:'泵组',min_support:.15,min_confidence:.6,min_lift:1,group_version_id:6,grouping:{transactions:3},result:{valid_jobs:3,frequent:[1,2],rules:[1],method:'m'}};
const mh=m.render();
if(!mh.includes('归组版 #6')||!mh.includes('有效事件事务'))throw new Error('mining render missing grouping context');
// viewer 分支可渲染
state.user.role='viewer';g.render();k.render();
console.log('all populated frontend render paths ok');
