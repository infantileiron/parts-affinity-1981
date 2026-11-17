"""故障事件归组领域服务。

口径：作业间关联(active)构成无向图，并查集求连通组；同一故障事件拆成的多张
返修单落在同一组，组内用件取并集，每组只计一次。跨设备关联在写入处拒绝，
因此连通组内设备天然一致。归组先沉淀为草稿，发布后才成为不可变生效版。
"""
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import update
from src.models.entities import Job,JobLink,GroupVersion,Kit

def now():return datetime.now(timezone.utc).isoformat()

def _dsu(links):
 parent={}
 def find(x):
  parent.setdefault(x,x)
  while parent[x]!=x:
   parent[x]=parent[parent[x]];x=parent[x]
  return x
 def union(a,b):
  ra,rb=find(a),find(b)
  if ra!=rb:parent[rb]=ra
 for a,b in links:union(a,b)
 clusters={}
 for x in list(parent):clusters.setdefault(find(x),[]).append(x)
 return [sorted(v) for v in clusters.values()]

def _snapshot(s):
 """按当前有效关联求连通组，并冻结每组的作业与用件并集。"""
 rows=s.query(Job).order_by(Job.id).all()
 jobs={r.id:r for r in rows}
 links=[(l.left_job_id,l.right_job_id) for l in s.query(JobLink).filter_by(status='active').order_by(JobLink.id).all()]
 clusters=_dsu(links)
 grouped=set()
 groups=[]
 for member_ids in clusters:
  grouped.update(member_ids)
  members=[];parts=set()
  for jid in member_ids:
   j=jobs[jid];members.append({'job_id':jid,'code':j.code,'equipment':j.equipment})
   parts.update(j.parts or [])
  groups.append({'group_id':len(groups)+1,'job_ids':member_ids,'members':members,
                 'equipment':jobs[member_ids[0]].equipment,'parts':sorted(parts)})
 # 未参与关联的作业各自成单事件组，保证全量作业都可作为事务参与挖掘
 for j in rows:
  if j.id not in grouped:
   groups.append({'group_id':len(groups)+1,'job_ids':[j.id],
                  'members':[{'job_id':j.id,'code':j.code,'equipment':j.equipment}],
                  'equipment':j.equipment,'parts':sorted(j.parts or [])})
 groups.sort(key=lambda g:g['job_ids'])
 for i,g in enumerate(groups):g['group_id']=i+1
 link_ids=[l.id for l in s.query(JobLink).filter_by(status='active').order_by(JobLink.id).all()]
 return groups,link_ids

def get_draft(s):
 return s.query(GroupVersion).filter_by(status='draft').order_by(GroupVersion.id.desc()).first()

def get_active(s):
 return s.query(GroupVersion).filter_by(status='active').order_by(GroupVersion.id.desc()).first()

def recompute_draft(s):
 """关联任何变动后重算草稿；不存在则创建。每次重算覆盖同一草稿行，不新增版本。"""
 groups,link_ids=_snapshot(s)
 d=get_draft(s)
 created=False
 if not d:
  d=GroupVersion(status='draft',created=now());s.add(d);created=True
 d.groups=groups;d.link_ids=link_ids
 s.flush()
 return d,created

def add_link(s,left_id,right_id):
 if left_id==right_id:raise HTTPException(400,'不能将作业与自身关联')
 a=s.get(Job,left_id);b=s.get(Job,right_id)
 if not a or not b:raise HTTPException(404,'维保作业不存在')
 if a.equipment!=b.equipment:raise HTTPException(409,'跨设备关联拒绝：故障事件归组仅限同一设备类型的作业')
 lo,hi=sorted((left_id,right_id))
 existing=s.query(JobLink).filter_by(left_job_id=lo,right_job_id=hi).first()
 if existing:
  if existing.status=='active':raise HTTPException(409,'两作业已在同一故障事件关联中')
  existing.status='active';existing.revoked=None;existing.created=now()
 else:
  existing=JobLink(left_job_id=lo,right_job_id=hi,status='active',created=now());s.add(existing)
 s.flush()
 recompute_draft(s)
 return existing

def revoke_link(s,link_id):
 link=s.get(JobLink,link_id)
 if not link:raise HTTPException(404,'关联不存在')
 if link.status!='active':raise HTTPException(409,'关联已撤销，无需重复操作')
 link.status='revoked';link.revoked=now()
 s.flush()
 # 桥边撤销后草稿按剩余有效关联重算，连通组随之拆开
 recompute_draft(s)
 return link

def publish(s):
 """草稿发布为生效版。

 单事务完成：旧生效版归档、草稿转生效、证据重算与暂停。任一步失败整体回滚，
 旧版完整保留。条件更新保证同时只有一版生效，发布竞争后到者得到409；
 对同一草稿重试直接返回当前生效版，不新增归组版本。
 """
 draft=get_draft(s)
 current=get_active(s)
 if not draft:
  # 没有草稿说明已被并发发布拿走：发布竞争下后到者拒绝，不新增归组版本
  raise HTTPException(409,'没有可发布的归组草稿：可能已被其他操作发布，请刷新后重试')
 if current and set(current.link_ids or [])==set(draft.link_ids or []) and current.groups==draft.groups:
  return current,'unchanged'
 if current:
  won=s.execute(update(GroupVersion).where(GroupVersion.id==current.id,GroupVersion.status=='active').values(status='archived')).rowcount
  if won==0:s.rollback();raise HTTPException(409,'归组版本正在被其他发布占用，请刷新后重试')
 # 草稿转生效同样做条件更新：并发首发时只有一个会话能把 draft 置为 active
 promoted=s.execute(update(GroupVersion).where(GroupVersion.id==draft.id,GroupVersion.status=='draft').values(status='active',published=now())).rowcount
 if promoted==0:s.rollback();raise HTTPException(409,'归组草稿已被其他发布占用，请刷新后重试')
 s.expire(draft);draft=s.get(GroupVersion,draft.id)
 s.flush()
 reevaluate_kits(s,draft)
 return draft,'published'

def _group_transactions(version,equipment=None):
 """把一个生效/草稿版本解释成挖掘事务：每组一条事务，用件取并集。"""
 tx=[]
 for g in version.groups:
  if equipment and equipment!='全部' and g['equipment']!=equipment:continue
  if g['parts']:tx.append({'job_ids':g['job_ids'],'equipment':g['equipment'],'parts':sorted(g['parts'])})
 return tx

def freeze_context(s,equipment):
 """挖掘提交时冻结同一版组与用件；尚无生效版时按逐作业单事件解释。"""
 active=get_active(s)
 if active:
  return active.id,_group_transactions(active,equipment)
 return None,_legacy_transactions(s,equipment)

def _legacy_transactions(s,equipment):
 rows=s.query(Job).order_by(Job.id).all()
 out=[]
 for j in rows:
  if equipment and equipment!='全部' and j.equipment!=equipment:continue
  if j.parts:out.append({'job_ids':[j.id],'equipment':j.equipment,'parts':sorted(set(j.parts))})
 return out

def reevaluate_kits(s,version):
 """生效时更新受影响备件包的联用证据，失去支持的包暂停使用。"""
 from src.models.entities import AssociationRule,MiningRun
 for kit in s.query(Kit).all():
  rule=s.get(AssociationRule,kit.rule_id)
  run=s.get(MiningRun,rule.run_id) if rule else None
  if not rule or not run:continue
  threshold={'min_support':run.min_support,'min_confidence':run.min_confidence,'min_lift':run.min_lift}
  ev=attach_verdict(evaluate_evidence(version,rule.antecedent,rule.consequent,run.equipment),threshold)
  ev['equipment']=run.equipment
  kit.evidence=ev
  if not ev['supported']:
   # 无论原是草稿还是已签核，失去支持即暂停；已签核包同样不可继续使用
   kit.status='suspended'
  elif kit.status=='suspended':
   # 重新获得支持后回到待签核草稿，需人工重新签核，不自动恢复为已签核
   kit.status='draft'

def evaluate_evidence(version,antecedent,consequent,equipment='全部'):
 """按指定版本的组解释当前用件，重算 support/confidence/lift；与来源批次同设备口径。"""
 groups=[g for g in version.groups if g['parts'] and (equipment=='全部' or g['equipment']==equipment)]
 tx=[set(g['parts']) for g in groups]
 n=len(tx);a=set(antecedent);b=set(consequent);ab=a|b
 cnt_a=sum(a<=t for t in tx);cnt_ab=sum(ab<=t for t in tx);cnt_b=sum(b<=t for t in tx)
 support=cnt_ab/n if n else 0
 confidence=cnt_ab/cnt_a if cnt_a else 0
 base=cnt_b/n if n else 0
 lift=confidence/base if base>0 else 0
 return {'group_version_id':version.id,'group_count':n,'count_a':cnt_a,'count_ab':cnt_ab,'count_b':cnt_b,
         'support':support,'confidence':confidence,'lift':lift,'supported':False,'reasons':[]}

def attach_verdict(ev,threshold):
 reasons=[]
 if ev['support']+1e-12<threshold['min_support']:reasons.append('支持度低于来源批次阈值')
 if ev['confidence']+1e-12<threshold['min_confidence']:reasons.append('置信度低于来源批次阈值')
 if ev['lift']+1e-12<threshold['min_lift']:reasons.append('提升度低于来源批次阈值')
 ev['supported']=not reasons;ev['reasons']=reasons;ev['threshold']=threshold
 return ev
