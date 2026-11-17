"""端到端：HTTP 全流程 + 队列 worker 同路径执行，覆盖连单/拆组/发布/对账/竞争。"""
import os,tempfile
_DB=os.path.join(tempfile.gettempdir(),'affinity_e2e_test.sqlite')
if os.path.exists(_DB):os.remove(_DB)
os.environ.setdefault('JWT_SECRET','test-secret-3108')
os.environ.setdefault('DATABASE_URL','sqlite:///'+_DB)
os.environ.setdefault('REDIS_URL','memory://')
import sys
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fakeredis_shim  # noqa: F401
import redis
from fastapi.testclient import TestClient
from src.config.database import Base,engine,Session
from src.models.entities import User,Job,GroupVersion,Kit,JobLink
from src.utils.security import hash_password
from src.main import app
from src.services.runs import execute_run

client=TestClient(app)

def setup():
 Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
 with Session() as s:
  for role in ('admin','analyst','viewer'):
   s.add(User(email=role+'@demo.local',role=role,password=hash_password('Demo123!')))
  rows=[('E1','泵组',['轴承','润滑脂']),('E2','泵组',['轴承','垫片']),('E3','泵组',['润滑脂','滤芯']),
        ('E4','泵组',['叶轮']),('E5','压缩机',['轴承']),('E6','泵组',['轴承','润滑脂','清洗剂'])]
  for c,e,p in rows:s.add(Job(code=c,equipment=e,parts=p,note='e2e'))
  s.commit()

def tok(r):return client.post('/auth/login',json={'email':r+'@demo.local','password':'Demo123!'}).json()['token']
def H(t):return {'Authorization':'Bearer '+t}

def test_full_day_flow_with_worker_queue():
 setup();t=tok('analyst');adm=tok('admin');q=redis.Redis.from_url('memory://')
 # 1. 跨设备拒绝 + 自关联拒绝
 assert client.post('/job-links',json={'left_job_id':1,'right_job_id':5},headers=H(t)).status_code==409
 assert client.post('/job-links',json={'left_job_id':2,'right_job_id':2},headers=H(t)).status_code==400
 # 2. 连单（含环），并查集归为一个事件组
 for a,b in [(1,2),(2,3),(3,1)]:
  assert client.post('/job-links',json={'left_job_id':a,'right_job_id':b},headers=H(t)).status_code==200
 ov=client.get('/group/overview',headers=H(t)).json()
 big=[g for g in ov['draft']['groups'] if g['job_ids']==[1,2,3]][0]
 assert big['parts']==sorted(['轴承','润滑脂','垫片','滤芯'])
 # 3. 发布；重复发布不新增版本
 p1=client.post('/group-versions/publish',headers=H(t)).json();assert p1['version']['id']==1
 assert client.post('/group-versions/publish',headers=H(t)).status_code==409
 # 4. 走真实队列路径：rpush 后 worker 风格 blpop + execute_run
 r=client.post('/runs',json={'name':'E2E批次','equipment':'泵组','min_support':0.01,'min_confidence':0.01,'min_lift':0},headers=H(t)).json()
 assert r['group_version_id']==1 and r['grouping']['transactions']==3  # 5 张泵组作业并成 3 个事件组
 item=q.blpop('affinity:runs',timeout=1)
 assert item is not None
 with Session() as s:execute_run(s,int(item[1]))
 done=client.get('/runs/'+str(r['id']),headers=H(t)).json()
 assert done['state']=='done' and done['result']['valid_jobs']==3
 # 5. 起草备件包 + admin 签核
 rules=client.get('/rules',params={'run_id':r['id']},headers=H(t)).json();assert rules
 kit=client.post('/kits',json={'name':'E2E联用包','rule_id':rules[0]['id'],'note':'兼容性限制说明至少八个字'},headers=H(t)).json()
 assert kit['evidence'] and kit['evidence']['group_version_id']==1
 if kit['status']=='draft':
  assert client.post('/kits/'+str(kit['id'])+'/approve',headers=H(adm)).status_code==200
 # 6. 撤销环上两条边后仅余 2-3，草稿拆组 -> 再发布，旧版归档，证据重算并对账
 client.post('/job-links/1/revoke',headers=H(t))
 client.post('/job-links/3/revoke',headers=H(t))
 ov2=client.get('/group/overview',headers=H(t)).json()
 assert sorted(tuple(g['job_ids']) for g in ov2['draft']['groups'] if len(g['job_ids'])>1)==[(2,3)]
 p2=client.post('/group-versions/publish',headers=H(t)).json()
 assert p2['version']['id']==2 and p2['previous_id']==1
 recon=client.get('/group-versions/reconcile',headers=H(t)).json()
 assert recon['active']['id']==2 and [v['id'] for v in client.get('/group/overview',headers=H(t)).json()['archived']]==[1]
 krow=[k for k in recon['kits'] if k['id']==kit['id']][0]
 assert krow['evidence']['group_version_id']==2
 # 7. 旧批次不可变：仍按归组版 #1 的 4 事务解释
 assert client.get('/runs/'+str(r['id']),headers=H(t)).json()['group_version_id']==1
 # 8. 审计留痕
 audits=client.get('/audits',headers=H(adm)).json()
 actions=' '.join(a['action'] for a in audits)
 assert '连单' in actions and '拆组' in actions and '发布归组版本' in actions

def test_concurrent_revision_does_not_change_frozen_batch():
 setup();t=tok('analyst')
 r=client.post('/runs',json={'name':'冻结批次','equipment':'泵组','min_support':0.01,'min_confidence':0.01,'min_lift':0},headers=H(t)).json()
 with Session() as s:execute_run(s,r['id'])
 before=client.get('/runs/'+str(r['id']),headers=H(t)).json()['result']['checksum']
 # 并发修订作业（改件、改说明）后重查，批次快照与校验和不变
 client.put('/jobs/1',json={'code':'E1','equipment':'泵组','parts':['紧固件','传感器'],'note':'并发修订'},headers=H(t))
 client.put('/jobs/4',json={'code':'E4','equipment':'泵组','parts':['清洗剂'],'note':'并发修订2'},headers=H(t))
 after=client.get('/runs/'+str(r['id']),headers=H(t)).json()
 assert after['result']['checksum']==before and after['result']['valid_jobs']==5
