import os,tempfile
_DB=os.path.join(tempfile.gettempdir(),'affinity_grouping_test.sqlite')
if os.path.exists(_DB):os.remove(_DB)
os.environ.setdefault('JWT_SECRET','test-secret-3108')
os.environ.setdefault('DATABASE_URL','sqlite:///'+_DB)
os.environ.setdefault('REDIS_URL','memory://')
import sys
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis_shim  # noqa: F401  必须在导入应用前补丁 redis
from fastapi.testclient import TestClient
from src.config.database import Base,engine,Session
from src.models.entities import User,Job,JobLink,GroupVersion,Kit,AssociationRule,MiningRun
from src.utils.security import hash_password
from src.main import app
from src.services.runs import execute_run

client=TestClient(app)

def setup_db():
 Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
 with Session() as s:
  s.add(User(email='admin@demo.local',role='admin',password=hash_password('Demo123!')))
  s.add(User(email='analyst@demo.local',role='analyst',password=hash_password('Demo123!')))
  s.add(User(email='viewer@demo.local',role='viewer',password=hash_password('Demo123!')))
  # 泵组：同一故障拆出的返修单 j1/j2/j3，j4 独立；压缩机 j5 用于跨设备拒绝
  data=[('YH-T-001','泵组',['轴承','密封圈','润滑脂']),
        ('YH-T-002','泵组',['轴承','垫片']),
        ('YH-T-003','泵组',['润滑脂','滤芯']),
        ('YH-T-004','泵组',['叶轮','阀片']),
        ('YH-T-005','压缩机',['轴承','润滑脂']),
        ('YH-T-006','泵组',['轴承','润滑脂','清洗剂']),
        ('YH-T-007','泵组',['轴承','润滑脂','传感器'])]
  for code,eq,parts in data:s.add(Job(code=code,equipment=eq,parts=parts,note='t'))
  s.commit()

def token(role):
 r=client.post('/auth/login',json={'email':role+'@demo.local','password':'Demo123!'})
 assert r.status_code==200,r.text
 return r.json()['token']

def h(t):return {'Authorization':'Bearer '+t}

def overview(t):return client.get('/group/overview',headers=h(t)).json()

def test_cross_equipment_link_rejected():
 setup_db();t=token('analyst')
 r=client.post('/job-links',json={'left_job_id':1,'right_job_id':5},headers=h(t))
 assert r.status_code==409 and '跨设备' in r.json()['detail']
 assert overview(t)['links']==[]

def test_connect_groups_union_and_publish():
 setup_db();t=token('analyst')
 # 1-2、2-3 连通成一组（含桥边 2），并集 5 件，只计一次
 for a,b in [(1,2),(2,3)]:
  r=client.post('/job-links',json={'left_job_id':a,'right_job_id':b},headers=h(t));assert r.status_code==200,r.text
 ov=overview(t);d=ov['draft'];assert ov['active'] is None
 groups={tuple(g['job_ids']):g for g in d['groups']}
 assert (1,2,3) in groups
 assert groups[(1,2,3)]['parts']==sorted({'轴承','密封圈','润滑脂','垫片','滤芯'})
 assert (4,) in groups and (6,) in groups  # 单事件组仍存在
 # 幂等：重复连单拒绝，反向同样拒绝
 r=client.post('/job-links',json={'left_job_id':2,'right_job_id':1},headers=h(t));assert r.status_code==409
 pub=client.post('/group-versions/publish',headers=h(t));assert pub.status_code==200
 assert pub.json()['outcome']=='published'
 vid=pub.json()['version']['id']
 ov=overview(t);assert ov['active']['id']==vid and ov['active']['status']=='active'
 assert ov['draft'] is None  # 草稿已转生效

def test_bridge_revoke_splits_group():
 setup_db();t=token('analyst')
 for a,b in [(1,2),(2,3)]:client.post('/job-links',json={'left_job_id':a,'right_job_id':b},headers=h(t))
 client.post('/group-versions/publish',headers=h(t))
 # 撤销桥边 1-2（link id=1）
 r=client.post('/job-links/1/revoke',headers=h(t));assert r.status_code==200
 d=overview(t)['draft']
 ids=sorted(tuple(g['job_ids']) for g in d['groups'])
 assert (1,) in ids and (2,3) in ids
 # 生效版不可变，仍是三作业同组
 active=overview(t)['active'];assert tuple(sorted(active['groups'][0]['job_ids']))==(1,2,3)
 assert client.post('/job-links/1/revoke',headers=h(t)).status_code==409  # 重复撤销拒绝
 # 重新发布产生新版本，旧版归档保留
 pub=client.post('/group-versions/publish',headers=h(t)).json()
 assert pub['outcome']=='published' and pub['previous_id']==1 and pub['version']['id']==2
 arch=client.get('/group/overview',headers=h(t)).json()['archived']
 assert [v['id'] for v in arch]==[1]

def test_cycle_is_one_group():
 setup_db();t=token('analyst')
 for a,b in [(1,2),(2,3),(3,1)]:
  assert client.post('/job-links',json={'left_job_id':a,'right_job_id':b},headers=h(t)).status_code==200
 groups=overview(t)['draft']['groups']
 big=[g for g in groups if len(g['job_ids'])==3]
 assert len(big)==1 and big[0]['parts']==sorted({'轴承','密封圈','润滑脂','垫片','滤芯'})

def test_publish_failure_keeps_old_version_and_retry_no_new_version():
 setup_db();t=token('analyst')
 client.post('/job-links',json={'left_job_id':1,'right_job_id':2},headers=h(t))
 first=client.post('/group-versions/publish',headers=h(t)).json()
 assert first['version']['id']==1
 # 无草稿时重复发布：拒绝且不新增版本（发布竞争保护）
 r=client.post('/group-versions/publish',headers=h(t))
 assert r.status_code==409
 with Session() as s:assert s.query(GroupVersion).count()==1
 # 制造发布失败：发布过程中证据重算抛错，整体回滚，旧版仍 active、草稿仍 draft
 client.post('/job-links',json={'left_job_id':6,'right_job_id':7},headers=h(t))
 import src.services.grouping as G
 orig=G.reevaluate_kits
 def boom(s,v):raise RuntimeError('模拟发布中断')
 G.reevaluate_kits=boom
 try:
  try:
   client.post('/group-versions/publish',headers=h(t));raised=False
  except RuntimeError:raised=True
  assert raised,'发布中断应向上抛出并由全局处理器转为 500'
 finally:
  G.reevaluate_kits=orig
 with Session() as s:
  assert s.query(GroupVersion).filter_by(status='active').one().id==1
  draft=s.query(GroupVersion).filter_by(status='draft').one()
  assert draft.link_ids  # 草稿仍在，失败未留下多余版本
 # 修复后重试成功，且只新增一个版本（v2）
 ok=client.post('/group-versions/publish',headers=h(t)).json()
 assert ok['version']['id']==2 and ok['outcome']=='published'
 with Session() as s:assert s.query(GroupVersion).count()==2

def test_mining_uses_grouped_transactions_and_freezes_context():
 setup_db();t=token('analyst')
 # 先在无归组状态挖一版：旧批次按逐作业解释（6 条泵组事务）
 r=client.post('/runs',json={'name':'旧批次','equipment':'泵组','min_support':0.01,'min_confidence':0.01,'min_lift':0},headers=h(t))
 old=r.json();assert old['group_version_id'] is None
 with Session() as s:execute_run(s,old['id'])
 old_done=client.get('/runs/'+str(old['id']),headers=h(t)).json()
 assert old_done['state']=='done' and old_done['result']['valid_jobs']==6
 # 发布归组：1,2,3 合一 -> 泵组事务由 6 降为 4
 for a,b in [(1,2),(2,3)]:client.post('/job-links',json={'left_job_id':a,'right_job_id':b},headers=h(t))
 client.post('/group-versions/publish',headers=h(t))
 r=client.post('/runs',json={'name':'归组批次','equipment':'泵组','min_support':0.01,'min_confidence':0.01,'min_lift':0},headers=h(t))
 new=r.json();assert new['group_version_id']==1
 with Session() as s:execute_run(s,new['id'])
 new_done=client.get('/runs/'+str(new['id']),headers=h(t)).json()
 assert new_done['result']['valid_jobs']==4
 # 旧批次不可变：仍按原组解释
 old_again=client.get('/runs/'+str(old['id']),headers=h(t)).json()
 assert old_again['result']['valid_jobs']==6 and old_again['group_version_id'] is None
 # 修订作业不影响已冻结批次
 client.put('/jobs/4',json={'code':'YH-T-004','equipment':'泵组','parts':['轴承'],'note':'改'},headers=h(t))
 old_again=client.get('/runs/'+str(old['id']),headers=h(t)).json()
 assert old_again['result']['valid_jobs']==6

def test_kit_evidence_suspension_and_approval_guard():
 setup_db();t=token('analyst');adm=token('admin')
 r=client.post('/runs',json={'name':'证据批次','equipment':'泵组','min_support':0.3,'min_confidence':0.5,'min_lift':0},headers=h(t))
 rid=r.json()['id']
 with Session() as s:execute_run(s,rid)
 rules=client.get('/rules',params={'run_id':rid},headers=h(t)).json()
 assert rules,'低阈值下应产生规则'
 kr=client.post('/kits',json={'name':'测试联用包','rule_id':rules[0]['id'],'note':'兼容性限制说明至少八个字'},headers=h(t))
 kid=kr.json()['id'];assert kr.json()['status']=='draft'
 assert client.post(f'/kits/{kid}/approve',headers=h(adm)).status_code==200
 # 归组发布后每个备件包都挂载当前生效版证据
 for a,b in [(1,2),(2,3),(6,7)]:client.post('/job-links',json={'left_job_id':a,'right_job_id':b},headers=h(t))
 client.post('/group-versions/publish',headers=h(t))
 recon=client.get('/group-versions/reconcile',headers=h(t)).json()
 krow=[k for k in recon['kits'] if k['id']==kid][0]
 assert krow['evidence'] and krow['evidence']['group_version_id']==1
 assert set(krow['evidence'])>={'support','confidence','lift','supported','reasons'}
 # 暂停包：禁止签核、禁止编辑
 with Session() as s:
  k=s.get(Kit,kid);k.status='suspended';k.evidence={**(k.evidence or {}),'supported':False,'reasons':['模拟失支持']};s.commit()
 assert client.post(f'/kits/{kid}/approve',headers=h(adm)).status_code==409
 assert client.put(f'/kits/{kid}',json={'name':'暂停包','note':'修订说明至少八个字哦'},headers=h(t)).status_code==409
 # 证据重算恢复支持时，suspended 回到 draft
 with Session() as s:
  from src.services.grouping import get_active,reevaluate_kits
  reevaluate_kits(s,get_active(s));s.commit()
 row=[k for k in client.get('/kits',headers=h(t)).json() if k['id']==kid][0]
 assert row['status'] in ('draft','suspended')

def test_approved_kit_suspends_on_publish_and_recovers_to_draft():
 setup_db();t=token('analyst');adm=token('admin')
 r=client.post('/runs',json={'name':'v1批次','equipment':'泵组','min_support':0.3,'min_confidence':0.5,'min_lift':0},headers=h(t))
 rid=r.json()['id']
 with Session() as s:execute_run(s,rid)
 rule=client.get('/rules',params={'run_id':rid},headers=h(t)).json()[0]
 kid=client.post('/kits',json={'name':'签核后失支持包','rule_id':rule['id'],'note':'兼容性限制说明至少八个字'},headers=h(t)).json()['id']
 assert client.post(f'/kits/{kid}/approve',headers=h(adm)).status_code==200
 # 发布归组（改变事件组口径），并把来源批次阈值抬到无法满足 -> 已签核包也必须暂停
 for a,b in [(1,2),(2,3)]:client.post('/job-links',json={'left_job_id':a,'right_job_id':b},headers=h(t))
 with Session() as s:
  run=s.get(MiningRun,rid);run.min_support=0.99;run.min_confidence=0.99;run.min_lift=49;s.commit()
 client.post('/group-versions/publish',headers=h(t))
 row=[k for k in client.get('/kits',headers=h(t)).json() if k['id']==kid][0]
 assert row['status']=='suspended' and row['evidence']['supported'] is False
 assert client.post(f'/kits/{kid}/approve',headers=h(adm)).status_code==409
 # 阈值恢复后再次发布重算：回到 draft 而非自动 approved，需人工重新签核
 with Session() as s:
  run=s.get(MiningRun,rid);run.min_support=0.3;run.min_confidence=0.5;run.min_lift=0;s.commit()
 client.post('/job-links/2/revoke',headers=h(t))  # 产生新草稿（边 2-3 撤销）
 client.post('/group-versions/publish',headers=h(t))
 row=[k for k in client.get('/kits',headers=h(t)).json() if k['id']==kid][0]
 assert row['status']=='draft' and row['evidence']['supported'] is True
 assert client.post(f'/kits/{kid}/approve',headers=h(adm)).status_code==200

def test_new_kit_created_unsupported_is_suspended():
 setup_db();t=token('analyst');adm=token('admin')
 r=client.post('/runs',json={'name':'证据批次','equipment':'泵组','min_support':0.3,'min_confidence':0.5,'min_lift':0},headers=h(t))
 rid=r.json()['id']
 with Session() as s:execute_run(s,rid)
 rules=client.get('/rules',params={'run_id':rid},headers=h(t)).json()
 assert rules
 # 归组把 6 条泵组事务并成 4 个事件组
 for a,b in [(1,2),(2,3),(6,7)]:client.post('/job-links',json={'left_job_id':a,'right_job_id':b},headers=h(t))
 client.post('/group-versions/publish',headers=h(t))
 # 取一条规则，人工把其阈值抬到当前证据无法满足的水平，验证新建即暂停且禁签核
 target=rules[0]
 with Session() as s:
  run=s.get(MiningRun,rid);run.min_support=0.99;run.min_confidence=0.99;run.min_lift=49;s.commit()
 kr=client.post('/kits',json={'name':'失支持联用包','rule_id':target['id'],'note':'兼容性限制说明至少八个字'},headers=h(t))
 body=kr.json();assert body['status']=='suspended'
 assert body['evidence']['supported'] is False and body['evidence']['reasons']
 assert client.post(f'/kits/{body["id"]}/approve',headers=h(adm)).status_code==409
 assert client.put(f'/kits/{body["id"]}',json={'name':'失支持联用包','note':'修订说明至少八个字哦'},headers=h(t)).status_code==409
 # 阈值恢复后同规则新建的包可正常进入草稿
 with Session() as s:
  run=s.get(MiningRun,rid);run.min_support=0.3;run.min_confidence=0.5;run.min_lift=0;s.commit()
 ok=client.post('/kits',json={'name':'在架联用包','rule_id':target['id'],'note':'兼容性限制说明至少八个字'},headers=h(t)).json()
 assert ok['status']=='draft' and ok['evidence']['supported'] is True

def test_evidence_service_verdict_is_deterministic():
 setup_db()
 from src.services.grouping import _snapshot,add_link,publish,evaluate_evidence,attach_verdict
 with Session() as s:
  add_link(s,1,2);add_link(s,2,3)
  version,_=publish(s);s.commit()
  # 组内并集包含 5 件，全样本 N=5（含未分组作业 4/5/6/7 共 5 个组）
  ev=evaluate_evidence(version,['轴承'],['润滑脂'])
  verdict=attach_verdict(ev,{'min_support':0.9,'min_confidence':0.9,'min_lift':50})
  assert verdict['supported'] is False and verdict['reasons']
  verdict2=attach_verdict(ev,{'min_support':0.01,'min_confidence':0.01,'min_lift':0})
  assert verdict2['supported'] is True and verdict2['reasons']==[]

def test_viewer_read_only():
 setup_db();t=token('viewer')
 assert client.post('/job-links',json={'left_job_id':1,'right_job_id':2},headers=h(t)).status_code==403
 assert client.post('/group-versions/publish',headers=h(t)).status_code==403
 assert client.get('/group/overview',headers=h(t)).status_code==200

def test_equipment_change_blocked_with_active_link():
 setup_db();t=token('analyst')
 client.post('/job-links',json={'left_job_id':1,'right_job_id':2},headers=h(t))
 r=client.put('/jobs/1',json={'code':'YH-T-001','equipment':'压缩机','parts':['轴承'],'note':'改设备'},headers=h(t))
 assert r.status_code==409 and '关联' in r.json()['detail']
 client.post('/job-links/1/revoke',headers=h(t))
 assert client.put('/jobs/1',json={'code':'YH-T-001','equipment':'压缩机','parts':['轴承'],'note':'改设备'},headers=h(t)).status_code==200

def test_unchanged_draft_publish_is_idempotent():
 setup_db();t=token('analyst')
 client.post('/job-links',json={'left_job_id':1,'right_job_id':2},headers=h(t))
 first=client.post('/group-versions/publish',headers=h(t)).json()
 # 模拟同内容草稿仍在的重复发布：直接调服务层，应返回同版且不新增
 from src.services import grouping
 with Session() as s:
  active=grouping.get_active(s)
  from src.models.entities import GroupVersion
  s.add(GroupVersion(status='draft',groups=active.groups,link_ids=active.link_ids,created='retry'));s.commit()
  v,outcome=grouping.publish(s);s.commit()
  assert outcome=='unchanged' and v.id==first['version']['id']
  # 未产生新生效版：active 仍只有原来一版
 with Session() as s:assert len(s.query(GroupVersion).filter_by(status='active').all())==1

def test_publish_race_single_active():
 setup_db();t=token('analyst')
 client.post('/job-links',json={'left_job_id':1,'right_job_id':2},headers=h(t))
 client.post('/group-versions/publish',headers=h(t))
 client.post('/job-links',json={'left_job_id':2,'right_job_id':3},headers=h(t))
 # 两个会话同时发布：条件更新保证只有一版 active，后到者 409
 from src.services import grouping
 from src.config.database import Session as S
 s1=S();s2=S()
 race_error=None
 try:
  grouping.publish(s1);s1.commit()
  try:
   grouping.publish(s2);s2.commit()
  except Exception as e:
   race_error=e;s2.rollback()
 finally:
  s1.close();s2.close()
 assert race_error is not None and '409' in str(race_error)
 with Session() as s:
  assert len(s.query(GroupVersion).filter_by(status='active').all())==1
