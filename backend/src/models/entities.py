from sqlalchemy import Column,Integer,String,Float,JSON,Text,ForeignKey,UniqueConstraint
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True,nullable=False);password=Column(String(200));role=Column(String(20))
class Job(Base):
 __tablename__='jobs'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);equipment=Column(String(40));parts=Column(JSON);note=Column(Text,default='')
class JobLink(Base):
 __tablename__='job_links'
 id=Column(Integer,primary_key=True);left_job_id=Column(Integer,ForeignKey('jobs.id'),nullable=False);right_job_id=Column(Integer,ForeignKey('jobs.id'),nullable=False);status=Column(String(20),default='active');created=Column(String(40));revoked=Column(String(40),nullable=True)
 __table_args__=(UniqueConstraint('left_job_id','right_job_id'),)
class GroupVersion(Base):
 __tablename__='group_versions'
 id=Column(Integer,primary_key=True);status=Column(String(20),default='draft');groups=Column(JSON);link_ids=Column(JSON);created=Column(String(40));published=Column(String(40),nullable=True);note=Column(Text,default='')
class MiningRun(Base):
 __tablename__='mining_runs'
 id=Column(Integer,primary_key=True);name=Column(String(100));equipment=Column(String(40));min_support=Column(Float);min_confidence=Column(Float);min_lift=Column(Float);state=Column(String(20),default='queued');result=Column(JSON,nullable=True);error=Column(Text,default='');group_version_id=Column(Integer,nullable=True);grouping=Column(JSON,nullable=True)
class AssociationRule(Base):
 __tablename__='association_rules'
 id=Column(Integer,primary_key=True);run_id=Column(Integer,ForeignKey('mining_runs.id'));signature=Column(String(200));antecedent=Column(JSON);consequent=Column(JSON);support=Column(Float);confidence=Column(Float);lift=Column(Float);count=Column(Integer)
 __table_args__=(UniqueConstraint('run_id','signature'),)
class Kit(Base):
 __tablename__='kits'
 id=Column(Integer,primary_key=True);name=Column(String(100));rule_id=Column(Integer,ForeignKey('association_rules.id'));parts=Column(JSON);note=Column(Text);status=Column(String(20),default='draft');evidence=Column(JSON,nullable=True)
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
