import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from src.config.database import db
from src.models.entities import User,Job,JobLink,MiningRun,AssociationRule,Kit,GroupVersion,Audit
from src.schemas.business import Login,JobInput,RunInput,KitInput,KitEdit,LinkInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.jobs import save_job
from src.controllers.business import ensure_run,ensure_rule,ensure_kit
from src.services import grouping
router=APIRouter()
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/jobs')
def jobs(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Job).order_by(Job.id).all()]
@router.post('/jobs')
def create_job(data:JobInput,u=Depends(editor),s=Depends(db)):
 r=save_job(s,data);audit(s,u,'录入作业 '+r['code']);return r
@router.put('/jobs/{i}')
def edit_job(i:int,data:JobInput,u=Depends(editor),s=Depends(db)):
 r=save_job(s,data,i);audit(s,u,'修订作业 '+str(i));return r
# ---- 故障事件归组 ----
@router.get('/group/overview')
def group_overview(u=Depends(current),s=Depends(db)):
 links=[dump(l) for l in s.query(JobLink).order_by(JobLink.id).all()]
 jobs_map={j.id:dump(j) for j in s.query(Job).all()}
 draft=grouping.get_draft(s);active=grouping.get_active(s)
 return {'links':links,'jobs':list(jobs_map.values()),
         'draft':dump(draft) if draft else None,
         'active':dump(active) if active else None,
         'archived':[dump(v) for v in s.query(GroupVersion).filter_by(status='archived').order_by(GroupVersion.id.desc()).all()]}
@router.post('/job-links')
def add_job_link(data:LinkInput,u=Depends(editor),s=Depends(db)):
 link=grouping.add_link(s,data.left_job_id,data.right_job_id);s.commit()
 audit(s,u,f'连单 {link.left_job_id}-{link.right_job_id}')
 return dump(link)
@router.post('/job-links/{i}/revoke')
def revoke_job_link(i:int,u=Depends(editor),s=Depends(db)):
 link=grouping.revoke_link(s,i);s.commit()
 audit(s,u,f'拆组撤销关联 {i}')
 return dump(link)
@router.post('/group-versions/publish')
def publish_group(u=Depends(editor),s=Depends(db)):
 before=grouping.get_active(s)
 version,outcome=grouping.publish(s)
 s.commit();audit(s,u,f'发布归组版本 {version.id}（{outcome}）')
 return {'version':dump(version),'outcome':outcome,'previous_id':before.id if before else None}
@router.get('/group-versions/reconcile')
def reconcile(u=Depends(current),s=Depends(db)):
 active=grouping.get_active(s);draft=grouping.get_draft(s)
 kits=[dump(k) for k in s.query(Kit).order_by(Kit.id).all()]
 return {'active':dump(active) if active else None,'draft':dump(draft) if draft else None,
         'kits':kits,'links':[dump(l) for l in s.query(JobLink).order_by(JobLink.id).all()]}
# ---- 挖掘 ----
@router.get('/runs')
def runs(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(MiningRun).order_by(MiningRun.id.desc()).all()]
@router.post('/runs')
def run(data:RunInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Job).order_by(Job.id).all() if data.equipment=='全部' or r.equipment==data.equipment]
 # 冻结同一版组与用件：并发修订作业、挖掘或改关联时，本批次始终解释这一版
 gv_id,transactions=grouping.freeze_context(s,data.equipment)
 r=MiningRun(**data.model_dump(),group_version_id=gv_id,
             grouping={'group_version_id':gv_id,'transactions':len(transactions)},
             result={'snapshot':rows,'transactions':transactions})
 s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('affinity:runs',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请恢复后新建挖掘';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交挖掘 '+str(r.id));return dump(r)
@router.get('/runs/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_run(s,i))
@router.get('/rules')
def rules(run_id:int,u=Depends(current),s=Depends(db)):
 ensure_run(s,run_id);return [dump(r) for r in s.query(AssociationRule).filter_by(run_id=run_id).order_by(AssociationRule.lift.desc()).all()]
@router.get('/runs/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_run(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功挖掘可以导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="parts-affinity-{i}.json"'})
# ---- 备件包 ----
@router.get('/kits')
def kits(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Kit).order_by(Kit.id.desc()).all()]
@router.post('/kits')
def kit(data:KitInput,u=Depends(editor),s=Depends(db)):
 rule=ensure_rule(s,data.rule_id)
 run=ensure_run(s,rule.run_id)
 if run.state!='done':raise HTTPException(409,'来源规则尚未完成')
 r=Kit(**data.model_dump(),parts=sorted(set(rule.antecedent+rule.consequent)))
 active=grouping.get_active(s)
 if active:
  threshold={'min_support':run.min_support,'min_confidence':run.min_confidence,'min_lift':run.min_lift}
  r.evidence=grouping.attach_verdict(grouping.evaluate_evidence(active,rule.antecedent,rule.consequent,run.equipment),threshold)
  r.evidence['equipment']=run.equipment
  if not r.evidence['supported']:r.status='suspended'
 s.add(r);s.commit();audit(s,u,'起草备件包 '+str(r.id));return dump(r)
@router.put('/kits/{i}')
def edit_kit(i:int,data:KitEdit,u=Depends(editor),s=Depends(db)):
 r=ensure_kit(s,i)
 if r.status=='approved':raise HTTPException(409,'已签核备件包不可覆盖，需新建方案')
 if r.status=='suspended':raise HTTPException(409,'备件包已因联用证据失支持暂停，不能编辑；待重新获得支持后再修订')
 r.name=data.name;r.note=data.note;s.commit();audit(s,u,'修改备件包 '+str(i));return dump(r)
@router.post('/kits/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=ensure_kit(s,i)
 if r.status=='approved':raise HTTPException(409,'已签核方案不可重复签核')
 if r.status=='suspended':raise HTTPException(409,'暂停使用的备件包不可签核：其联用证据在当前生效归组下已失支持')
 r.status='approved';s.commit();audit(s,u,'签核备件包 '+str(i));return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
