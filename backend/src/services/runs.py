"""挖掘执行：从冻结的归组上下文构造事务并落库规则，worker 与同步测试共用。"""
from src.models.entities import MiningRun,AssociationRule
from src.services.analysis import mine,checksum
def execute_run(s,run_id,artifact_dir=None):
 import json,logging
 row=s.get(MiningRun,run_id)
 if not row or row.state!='queued':return row
 # 兼容升级前入队的旧批次：无冻结组事务时回退到作业快照，逐作业解释
 transactions=row.result.get('transactions') or [
  {'job_ids':[j['id']],'equipment':j['equipment'],'parts':sorted(set(j['parts']))}
  for j in row.result.get('snapshot',[]) if j.get('parts')]
 row.state='running';s.commit()
 try:
  result=mine(transactions,row.min_support,row.min_confidence,row.min_lift)
  result['checksum']=checksum(transactions)
  s.query(AssociationRule).filter_by(run_id=row.id).delete()
  for rule in result['rules']:s.add(AssociationRule(run_id=row.id,**rule))
  row.result={**result,'transactions':transactions,'snapshot':row.result.get('snapshot',[]),'grouping':row.grouping};row.state='done';row.error=''
  if artifact_dir:
   from pathlib import Path
   Path(artifact_dir,str(row.id)+'.json').write_text(json.dumps(row.result,ensure_ascii=False))
 except Exception as e:
  row.state='failed';row.error=str(e);logging.exception('mining failed')
 s.commit()
 return row
