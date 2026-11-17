"""测试用 redis 内存补丁：提供 Redis.from_url/rpush/blpop/ping，避免依赖真实 Redis。"""
import sys,types,collections
class _FakeRedis:
 _queues=collections.defaultdict(list)
 def __init__(self,*a,**k):pass
 def rpush(self,k,v):self._queues[k].append(v);return len(self._queues[k])
 def blpop(self,k,timeout=0):
  q=self._queues[k]
  return (k,q.pop(0)) if q else None
 def ping(self):return True
 @classmethod
 def from_url(cls,url,*a,**k):return cls()
class _RedisError(Exception):pass
mod=types.ModuleType('redis')
mod.Redis=_FakeRedis;mod.RedisError=_RedisError
sys.modules['redis']=mod
