import os, secrets, hashlib, hmac, json
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import unquote
import httpx
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import create_engine, String, Text, ForeignKey, select, event, LargeBinary
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from sqlalchemy.orm.exc import StaleDataError
from fastapi.responses import JSONResponse

def now(): return datetime.now(timezone.utc)
def stamp(): return now().isoformat()
engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:///./hr.db'), **({'connect_args': {'check_same_thread': False}} if os.getenv('DATABASE_URL','sqlite:').startswith('sqlite:') else {}))
DB = sessionmaker(engine, expire_on_commit=False)
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__='users'
    id: Mapped[int]=mapped_column(primary_key=True)
    email: Mapped[str]=mapped_column(String(200),unique=True)
    password: Mapped[str]=mapped_column(Text)
    role: Mapped[str]=mapped_column(String(30))
    department: Mapped[str]=mapped_column(String(100))
    active: Mapped[bool]=mapped_column(default=True)
class LoginSession(Base):
    __tablename__='sessions'
    token: Mapped[str]=mapped_column(String(64),primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    expires: Mapped[str]=mapped_column(String(50))
    csrf: Mapped[str]=mapped_column(String(100))
class Case(Base):
    __tablename__='cases'
    id: Mapped[int]=mapped_column(primary_key=True)
    owner: Mapped[int]=mapped_column(ForeignKey('users.id'))
    department: Mapped[str]=mapped_column(String(100))
    state: Mapped[str]=mapped_column(String(30),default='manager')
    data: Mapped[str]=mapped_column(Text)
    version: Mapped[int]=mapped_column(default=1)
    __mapper_args__={'version_id_col':version}
class Audit(Base):
    __tablename__='audit'
    id: Mapped[int]=mapped_column(primary_key=True)
    case_id: Mapped[int | None]=mapped_column(ForeignKey('cases.id'),nullable=True)
    actor: Mapped[int]=mapped_column(ForeignKey('users.id'))
    action: Mapped[str]=mapped_column(String(60))
    at: Mapped[str]=mapped_column(String(50),default=stamp)
    detail: Mapped[str]=mapped_column(Text)
class Attempt(Base):
    __tablename__='login_attempts'
    key: Mapped[str]=mapped_column(String(64),primary_key=True)
    count: Mapped[int]=mapped_column(default=0)
    until: Mapped[str]=mapped_column(String(50))
class Attachment(Base):
    __tablename__='attachments'
    id: Mapped[int]=mapped_column(primary_key=True)
    case_id: Mapped[int]=mapped_column(ForeignKey('cases.id'),index=True)
    actor: Mapped[int]=mapped_column(ForeignKey('users.id'))
    name: Mapped[str]=mapped_column(String(200))
    content: Mapped[bytes]=mapped_column(LargeBinary)
    at: Mapped[str]=mapped_column(String(50),default=stamp)

ROLES=['manager','hr','ceo','accountant','superadmin']
def password_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
def verify(password, encoded): return hmac.compare_digest(password_hash(password,encoded.split(':')[0]),encoded)
def audit(db,user,action,case=None,detail=None): db.add(Audit(actor=user.id,case_id=case.id if case else None,action=action,detail=json.dumps(detail or {},ensure_ascii=False)))
def database():
    with DB() as db: yield db
def current(request:Request,db:Session=Depends(database)):
    token=hashlib.sha256(request.cookies.get('session','').encode()).hexdigest()
    s=db.get(LoginSession,token)
    if not s or s.expires<stamp(): raise HTTPException(401,'Войдите в систему')
    u=db.get(User,s.user_id)
    if not u or not u.active: raise HTTPException(401,'Аккаунт отключён')
    if request.method not in ['GET','HEAD'] and not hmac.compare_digest(request.headers.get('x-csrf-token',''),s.csrf): raise HTTPException(403,'CSRF token')
    return u
def require(u,*roles):
    if u.role!='superadmin' and u.role not in roles: raise HTTPException(403,'Недостаточно прав')
def getcase(db,u,id):
    c=db.scalar(select(Case).where(Case.id==id).with_for_update())
    if not c or (u.role=='manager' and c.department!=u.department): raise HTTPException(404,'Заявка не найдена')
    return c
def view(c): return {**json.loads(c.data),'id':c.id,'department':c.department,'state':c.state,'version':c.version}
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    from app.demo import recover
    recover()
    yield
app=FastAPI(title='DOMPLAN HR',lifespan=lifespan)
@app.exception_handler(StaleDataError)
async def stale_handler(request,exc):
    return JSONResponse(status_code=409,content={'detail':'Заявка изменена другим пользователем. Обновите страницу'})
@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request)
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin','Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"})
    return response
class Login(BaseModel):
    email:str=Field(max_length=200)
    password:str=Field(max_length=200)
@app.post('/api/login')
def login(p:Login,request:Request,response:Response,db:Session=Depends(database)):
    key=hashlib.sha256(p.email.lower().encode()).hexdigest()
    a=db.get(Attempt,key)
    if a and a.until>stamp() and a.count>=8: raise HTTPException(429,'Повторите через 15 минут')
    u=db.scalar(select(User).where(User.email==p.email.lower()))
    if not u or not u.active or not verify(p.password,u.password):
        if not a: a=Attempt(key=key,count=0,until=(now()+timedelta(minutes=15)).isoformat()); db.add(a)
        if a.until<stamp(): a.count=0; a.until=(now()+timedelta(minutes=15)).isoformat()
        a.count+=1; db.commit(); raise HTTPException(401,'Неверные данные')
    if a: db.delete(a)
    token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
    db.add(LoginSession(token=hashlib.sha256(token.encode()).hexdigest(),user_id=u.id,csrf=csrf,expires=(now()+timedelta(hours=8)).isoformat()))
    audit(db,u,'login'); db.commit()
    response.set_cookie('session',token,httponly=True,samesite='strict',secure=os.getenv('COOKIE_SECURE','true').lower()=='true',max_age=28800)
    return {'csrf':csrf}
@app.get('/api/me')
def me(request:Request,u=Depends(current),db:Session=Depends(database)):
    s=db.get(LoginSession,hashlib.sha256(request.cookies['session'].encode()).hexdigest())
    return dict(id=u.id,email=u.email,role=u.role,department=u.department,csrf=s.csrf)
@app.post('/api/logout')
def logout(request:Request,response:Response,u=Depends(current),db:Session=Depends(database)):
    db.delete(db.get(LoginSession,hashlib.sha256(request.cookies['session'].encode()).hexdigest())); db.commit(); response.delete_cookie('session'); return {'ok':True}
class Candidate(BaseModel):
    name:str=Field(min_length=2,max_length=200)
    position:str=Field(min_length=2,max_length=200)
    department:str=Field(min_length=1,max_length=100)
    salary:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
    start_date:date
    conclusion:str=Field(min_length=3,max_length=5000)
@app.post('/api/cases')
def create(p:Candidate,u=Depends(current),db:Session=Depends(database)):
    require(u,'manager')
    if u.role=='manager' and p.department!=u.department: raise HTTPException(403,'Другой отдел')
    c=Case(owner=u.id,department=p.department,data=p.model_dump_json()); db.add(c); db.flush(); audit(db,u,'create',c,p.model_dump(mode='json')); db.commit(); return view(c)
@app.get('/api/cases')
def cases(u=Depends(current),db:Session=Depends(database)):
    q=select(Case).order_by(Case.id.desc())
    if u.role=='manager': q=q.where(Case.department==u.department)
    return [view(c) for c in db.scalars(q)]
@app.get('/api/cases/{id}/history')
def history(id:int,u=Depends(current),db:Session=Depends(database)):
    getcase(db,u,id)
    return [dict(id=a.id,actor=db.get(User,a.actor).email,action=a.action,at=a.at,detail=json.loads(a.detail)) for a in db.scalars(select(Audit).where(Audit.case_id==id).order_by(Audit.id))]
class Decision(BaseModel):
    action:str=Field(pattern='^(approve|return|reject)$')
    comment:str=Field(min_length=3,max_length=5000)
    version:int
@app.post('/api/cases/{id}/decision')
def decision(id:int,p:Decision,u=Depends(current),db:Session=Depends(database)):
    c=getcase(db,u,id)
    if c.version!=p.version: raise HTTPException(409,'Заявка изменена. Обновите страницу')
    if c.state not in ['manager','hr','ceo','accountant']: raise HTTPException(409,'Решение недоступно')
    require(u,c.state)
    old=c.state
    if p.action=='reject': c.state='rejected'
    elif p.action=='return':
        if old=='manager': raise HTTPException(409,'Нет предыдущего этапа')
        c.state={'hr':'manager','ceo':'hr','accountant':'ceo'}[old]
    else: c.state={'manager':'hr','hr':'ceo','ceo':'accountant','accountant':'payroll'}[old]
    audit(db,u,p.action,c,dict(from_state=old,to_state=c.state,comment=p.comment)); db.commit(); return view(c)
@app.put('/api/cases/{id}')
def edit(id:int,p:Candidate,version:int,u=Depends(current),db:Session=Depends(database)):
    c=getcase(db,u,id); require(u,'manager')
    if c.state!='manager' or c.version!=version: raise HTTPException(409,'Редактирование доступно на этапе руководителя с актуальной версией')
    if p.department!=c.department: raise HTTPException(422,'Отдел заявки менять нельзя')
    c.data=p.model_dump_json(); audit(db,u,'edit',c,p.model_dump(mode='json')); db.commit(); return view(c)
class Payroll(BaseModel):
    period:str=Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    scheduled_days:int=Field(ge=1,le=31)
    worked_days:int=Field(ge=0,le=31)
    bonus:Decimal=Field(ge=0,max_digits=14,decimal_places=2,default=0)
    deduction:Decimal=Field(ge=0,max_digits=14,decimal_places=2,default=0)
    payment_due:date
    version:int
    @model_validator(mode='after')
    def valid(self):
        if self.worked_days>self.scheduled_days: raise ValueError('Отработанные дни не могут превышать рабочие дни по графику')
        return self
def calculate(salary,p):
    gross=(Decimal(str(salary))*p.worked_days/p.scheduled_days+p.bonus).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    if p.deduction>gross: raise HTTPException(422,'Удержания больше начисления')
    return {'gross':str(gross),'net':str(gross-p.deduction)}
@app.post('/api/cases/{id}/payroll')
def payroll(id:int,p:Payroll,u=Depends(current),db:Session=Depends(database)):
    require(u,'accountant'); c=getcase(db,u,id)
    if c.state not in ['payroll','calculated'] or c.version!=p.version: raise HTTPException(409,'Сначала получите согласования или обновите заявку')
    data=json.loads(c.data)
    if p.period<data['start_date'][:7]: raise HTTPException(422,'Период раньше начала работы')
    data['payroll']={**p.model_dump(mode='json'),**calculate(data['salary'],p)}
    c.data=json.dumps(data); c.state='calculated'; audit(db,u,'calculate',c,data['payroll']); db.commit(); return view(c)
class Payment(BaseModel):
    reference:str=Field(min_length=3,max_length=200)
    paid_on:date
    version:int
@app.post('/api/cases/{id}/payment')
def payment(id:int,p:Payment,u=Depends(current),db:Session=Depends(database)):
    require(u,'accountant'); c=getcase(db,u,id)
    if c.state!='calculated' or c.version!=p.version: raise HTTPException(409,'Выплата уже записана или зарплата не рассчитана')
    data=json.loads(c.data)
    if p.paid_on>now().date() or p.paid_on<date.fromisoformat(data['start_date']): raise HTTPException(422,'Некорректная дата выплаты')
    data['payment']=p.model_dump(mode='json'); c.data=json.dumps(data); c.state='paid'; audit(db,u,'record_external_payment',c,data['payment']); db.commit(); return view(c)
class NewUser(BaseModel):
    email:str=Field(pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$',max_length=200)
    password:str=Field(min_length=12,max_length=200)
    role:str=Field(pattern='^(manager|hr|ceo|accountant|superadmin)$')
    department:str=Field(min_length=1,max_length=100)
@app.get('/api/users')
def users(u=Depends(current),db:Session=Depends(database)):
    require(u); return [dict(id=x.id,email=x.email,role=x.role,department=x.department,active=x.active) for x in db.scalars(select(User))]
@app.post('/api/users')
def adduser(p:NewUser,u=Depends(current),db:Session=Depends(database)):
    require(u)
    if db.scalar(select(User).where(User.email==p.email.lower())): raise HTTPException(409,'Email уже существует')
    n=User(email=p.email.lower(),password=password_hash(p.password),role=p.role,department=p.department); db.add(n); audit(db,u,'create_user',detail={'email':n.email,'role':n.role}); db.commit(); return {'id':n.id}
@app.post('/api/users/{id}/toggle')
def toggle(id:int,u=Depends(current),db:Session=Depends(database)):
    require(u)
    n=db.get(User,id)
    if not n: raise HTTPException(404)
    if n.id==u.id: raise HTTPException(422,'Нельзя отключить себя')
    n.active=not n.active; audit(db,u,'toggle_user',detail={'user_id':id,'active':n.active}); db.commit(); return {'active':n.active}
@app.get('/api/audit')
def all_audit(u=Depends(current),db:Session=Depends(database)):
    require(u); return [dict(id=a.id,case_id=a.case_id,actor=db.get(User,a.actor).email,action=a.action,at=a.at,detail=json.loads(a.detail)) for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(1000))]

async def rag(method,path,**kwargs):
    url=os.getenv('RAGFLOW_URL','').rstrip('/'); key=os.getenv('RAGFLOW_API_KEY',''); dataset=os.getenv('RAGFLOW_DATASET_ID','')
    if not all([url,key,dataset]): raise HTTPException(503,'Заполните RAGFLOW_URL, RAGFLOW_API_KEY, RAGFLOW_DATASET_ID')
    try:
        async with httpx.AsyncClient(timeout=60,follow_redirects=False) as client:
            r=await client.request(method,url+'/api/v1/datasets/'+dataset+path,headers={'Authorization':'Bearer '+key},**kwargs)
            r.raise_for_status(); body=r.json()
            if body.get('code')!=0: raise ValueError('RAGFlow error')
            return body.get('data')
    except (httpx.HTTPError,ValueError): raise HTTPException(502,'RAGFlow недоступен или отклонил запрос')
@app.post('/api/cases/{id}/documents')
async def document(id:int,request:Request,u=Depends(current),db:Session=Depends(database)):
    require(u,'hr'); c=getcase(db,u,id)
    filename=unquote(request.headers.get('x-filename','document.pdf'))
    if Path(filename).suffix.lower() not in ['.pdf','.txt','.docx']: raise HTTPException(422,'PDF, TXT или DOCX')
    content=bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content)>10*1024*1024: raise HTTPException(413,'Максимум 10 MB')
    if not content: raise HTTPException(422,'Пустой файл')
    docs=await rag('POST','/documents',files={'file':(Path(filename).name,bytes(content),'application/octet-stream')})
    doc=docs[0]; data=json.loads(c.data); data.setdefault('documents',[]).append({'id':doc['id'],'name':Path(filename).name})
    c.data=json.dumps(data); audit(db,u,'document_upload',c,{'document_id':doc['id']}); db.commit()
    await rag('POST','/chunks',json={'document_ids':[doc['id']]})
    return {'id':doc['id'],'message':'Разбор запущен; результат появится после обработки'}
@app.get('/api/cases/{id}/documents/{docid}')
async def document_result(id:int,docid:str,u=Depends(current),db:Session=Depends(database)):
    c=getcase(db,u,id)
    if docid not in [d['id'] for d in json.loads(c.data).get('documents',[])]: raise HTTPException(404)
    return await rag('GET','/documents/'+docid+'/chunks',params={'page_size':30})
@app.get('/api/health')
def health(db:Session=Depends(database)):
    db.execute(select(1)); return {'status':'ok','ragflow_configured':all(os.getenv(k) for k in ['RAGFLOW_URL','RAGFLOW_API_KEY','RAGFLOW_DATASET_ID'])}

def word_response(content,name):
    return Response(content,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="'+name+'"'})
@app.get('/api/workflow-template.docx')
def template(u=Depends(current)):
    from app.documents import build_workflow
    return word_response(build_workflow(),'onboarding-template.docx')
@app.get('/api/cases/{id}/workflow.docx')
def workflow_word(id:int,u=Depends(current),db:Session=Depends(database)):
    from app.documents import build_workflow
    c=getcase(db,u,id)
    history=[dict(at=a.at,actor=db.get(User,a.actor).email,action=a.action,detail=a.detail) for a in db.scalars(select(Audit).where(Audit.case_id==id).order_by(Audit.id))]
    files=[dict(name=a.name,at=a.at) for a in db.scalars(select(Attachment).where(Attachment.case_id==id))]
    return word_response(build_workflow(view(c),history,files),'onboarding-'+str(id)+'.docx')
@app.get('/api/cases/{id}/attachments')
def attachments(id:int,u=Depends(current),db:Session=Depends(database)):
    getcase(db,u,id)
    return [dict(id=a.id,name=a.name,at=a.at,actor=db.get(User,a.actor).email) for a in db.scalars(select(Attachment).where(Attachment.case_id==id).order_by(Attachment.id))]
@app.post('/api/cases/{id}/attachments')
async def attach(id:int,request:Request,u=Depends(current),db:Session=Depends(database)):
    c=getcase(db,u,id)
    if u.role!='superadmin' and u.role!=c.state and not (u.role=='accountant' and c.state in ['payroll','calculated']): raise HTTPException(403,'Загрузка доступна ответственному текущего этапа')
    name=Path(unquote(request.headers.get('x-filename','document.docx')).replace('\\','/')).name
    if len(name)>200 or Path(name).suffix.lower() not in ['.pdf','.docx','.txt']:raise HTTPException(422,'PDF, DOCX или TXT; имя до 200 символов')
    content=bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content)>10*1024*1024:raise HTTPException(413,'Максимум 10 MB')
    if not content:raise HTTPException(422,'Пустой файл')
    a=Attachment(case_id=id,actor=u.id,name=name,content=bytes(content));db.add(a);db.flush()
    audit(db,u,'attach_file',c,{'attachment_id':a.id,'name':name});db.commit()
    return {'id':a.id,'name':name}
def getattachment(db,u,id,aid):
    getcase(db,u,id);a=db.get(Attachment,aid)
    if not a or a.case_id!=id:raise HTTPException(404,'Файл не найден')
    return a
@app.get('/api/cases/{id}/attachments/{aid}/download')
def download_attachment(id:int,aid:int,u=Depends(current),db:Session=Depends(database)):
    a=getattachment(db,u,id,aid)
    from urllib.parse import quote
    return Response(a.content,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(a.name)})
@app.post('/api/cases/{id}/attachments/{aid}/analyze')
async def analyze_attachment(id:int,aid:int,u=Depends(current),db:Session=Depends(database)):
    require(u,'hr');a=getattachment(db,u,id,aid);c=getcase(db,u,id)
    docs=await rag('POST','/documents',files={'file':(a.name,a.content,'application/octet-stream')})
    doc=docs[0];data=json.loads(c.data);data.setdefault('documents',[]).append({'id':doc['id'],'name':a.name,'attachment_id':a.id})
    c.data=json.dumps(data);audit(db,u,'send_attachment_to_ragflow',c,{'attachment_id':a.id,'document_id':doc['id']});db.commit()
    await rag('POST','/chunks',json={'document_ids':[doc['id']]})
    return {'id':doc['id']}
from app.demo import register
register(app)
app.mount('/',StaticFiles(directory=Path(__file__).parent/'static',html=True),name='frontend')
