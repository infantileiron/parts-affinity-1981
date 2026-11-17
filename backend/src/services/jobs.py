from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_
from fastapi import HTTPException
from src.models.entities import Job,JobLink
from src.services.business import dump
from src.services.grouping import recompute_draft
def save_job(s,data,i=None):
 r=s.get(Job,i) if i else Job()
 if not r:raise HTTPException(404,'维保作业不存在')
 if i and data.equipment!=r.equipment:
  touched=s.query(JobLink).filter(JobLink.status=='active',
   or_(JobLink.left_job_id==i,JobLink.right_job_id==i)).count()
  if touched:raise HTTPException(409,'该作业存在同设备故障事件关联，不能改为其他设备类型；请先撤销关联再修订')
 for k,v in data.model_dump().items():setattr(r,k,v)
 s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'作业编号重复')
 # 作业用件修订后，草稿组内并集随之重算；已发布生效版不可变、不受影响
 if s.query(JobLink).filter_by(status='active').count():recompute_draft(s);s.commit()
 return dump(r)
