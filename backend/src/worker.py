import os,time,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import MiningRun
from src.services.runs import execute_run
r=redis.Redis.from_url(os.environ['REDIS_URL'])
with Session() as recovery:
 for pending in recovery.query(MiningRun).filter(MiningRun.state.in_(['queued','running'])).all():pending.state='queued';r.rpush('affinity:runs',pending.id)
 recovery.commit()
while True:
 Path('/tmp/heartbeat').touch()
 try:
  item=r.blpop('affinity:runs',timeout=3)
  if not item:continue
  with Session() as s:execute_run(s,int(item[1]),os.environ.get('ARTIFACT_DIR','/artifacts'))
 except Exception:logging.exception('worker');time.sleep(2)
