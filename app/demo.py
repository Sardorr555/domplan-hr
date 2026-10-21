"""Autonomous simulation. Only server-generated fictional data enters the model."""
import json, os, re, secrets
from decimal import Decimal
from io import BytesIO
import httpx
from fastapi import BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import select, update, Text, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from app.main import Base, DB, current, database, stamp, word_response, Case, Audit, Attachment, view, getcase, require

STAGES=['manager','hr','ceo','accountant']
NAMES={'manager':'ИИ · Руководитель','hr':'ИИ · HR','ceo':'ИИ · Директор','accountant':'ИИ · Бухгалтер'}
class DemoRun(Base):
    __tablename__='demo_runs'
    id:Mapped[int]=mapped_column(primary_key=True)
    status:Mapped[str]=mapped_column(String(30),default='running')
    slot:Mapped[int|None]=mapped_column(unique=True,nullable=True)
    stage:Mapped[str]=mapped_column(String(30),default='manager')
    data:Mapped[str]=mapped_column(Text)
    created:Mapped[str]=mapped_column(String(50),default=stamp)
    updated:Mapped[str]=mapped_column(String(50),default=stamp)

class Answer(BaseModel):
    decision:Literal['approve','return','reject']
    conclusion:str=Field(min_length=5,max_length=4000)
    checks:list[str]=Field(default_factory=list,max_length=10)

def fictional():
    return {'name':'ДЕМО — Вымышленный сотрудник 001','position':'Демонстрационный специалист','department':'Учебный отдел','salary':'22000.00','start_date':'2026-10-01','conclusion':'Полностью синтетическая карточка; все документы условные, данные согласованы для демонстрации.','documents':['Учебное резюме: опыт соответствует вымышленной должности.','Учебный чек-лист HR: комплект полный, сведения согласованы.','Учебный проект условий: оклад 22000, начало 01.10.2026.'],'payroll_input':{'period':'2026-10','scheduled_days':22,'worked_days':11,'bonus':'500.00','deduction':'1000.00','payment_due':'2026-10-18'}}

def output(r):return {'id':r.id,'status':r.status,'stage':r.stage,'created':r.created,'updated':r.updated,**json.loads(r.data)}

async def remote(method,path,**kwargs):
    url=os.getenv('RAGFLOW_URL','').rstrip('/');key=os.getenv('RAGFLOW_API_KEY','')
    if not url or not key:raise RuntimeError('Не настроено подключение Swipies')
    async with httpx.AsyncClient(timeout=120,follow_redirects=False) as c:
        response=await c.request(method,url+'/api/v1'+path,headers={'Authorization':'Bearer '+key},**kwargs)
        response.raise_for_status();body=response.json()
        if body.get('code',0)!=0:raise RuntimeError('Swipies отклонил запрос')
        return body

async def assistant():
    configured=os.getenv('SWIPIES_DEMO_CHAT_ID','')
    if configured:return configured
    body=await remote('GET','/chats',params={'name':'DOMPLAN Autonomous Demo'})
    items=body.get('data',[])
    if isinstance(items,dict):items=items.get('chats',[])
    match=next((x for x in items if x.get('name')=='DOMPLAN Autonomous Demo'),None)
    if match:return match['id']
    created=await remote('POST','/chats',json={'name':'DOMPLAN Autonomous Demo','dataset_ids':[], 'llm_setting':{'temperature':0.1},'prompt_config':{'system':'You simulate an HR workflow using fictional data only. Follow the user task. Output valid JSON only.','parameters':[],'prologue':'Автономная демонстрация на вымышленных данных.'}})
    return created['data']['id']

def parse_answer(text):
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
    return Answer.model_validate_json(text)

async def ask(chat,stage,card,events):
    prompt=('Это изолированная учебная симуляция, все данные вымышленные. Ты '+NAMES[stage]+'. '
        'Проанализируй учебные документы и предыдущие заключения, прими решение своего этапа. '
        'Руководитель оценивает согласованность должности, резюме и оклада; HR — комплектность условных документов; '
        'директор — согласованность предыдущих одобрений и условий; бухгалтер — арифметику первой зарплаты. '
        'В этой полной учебной карточке не требуются реальные паспорта, договоры и подтверждения: они условно проверены. '
        'approve — передать дальше, return — вернуть предыдущему агенту для повторной проверки, reject — завершить отклонением. '
        'Ответь строго объектом с полями decision (approve/return/reject), conclusion (русский текст), checks (список коротких русских проверок). '
        'Не выполняй реальные кадровые действия или переводы.\nУчебная карточка:\n'+json.dumps(card,ensure_ascii=False)+
        '\nПредыдущие события:\n'+json.dumps(events,ensure_ascii=False))
    body=await remote('POST','/chats/'+chat+'/completions',json={'stream':False,'question':prompt})
    return parse_answer(body['data']['answer'])

def save(id,stage,status,data):
    with DB() as db:
        r=db.get(DemoRun,id);r.stage=stage;r.status=status;r.updated=stamp();r.data=json.dumps(data,ensure_ascii=False)
        if status not in ['running','waiting_confirmation']:r.slot=None
        if data.get('case_id'):
            c=db.get(Case,data['case_id']);card=json.loads(c.data)
            previous=card.get('ai_event_count',0)
            for e in data['events'][previous:]:
                db.add(Audit(actor=c.owner,case_id=c.id,action='ai_'+e['decision'],at=e['at'],detail=json.dumps({'ai_role':e['actor'],'stage':e['stage'],'conclusion':e['conclusion'],'comment':' • '.join(e['checks'])},ensure_ascii=False)))
            card.update(ai_run_id=id,ai_pending=data.get('pending'),ai_status=status,ai_stage=stage,ai_event_count=len(data['events']),ai_error=data.get('error'))
            for key in ['payroll','payment']:
                if key in data:card[key]=data[key]
            c.state='paid' if status=='completed' else 'rejected' if status=='rejected' else stage if stage in STAGES else c.state
            c.data=json.dumps(card,ensure_ascii=False)
            from app.documents import build_workflow
            history=[dict(at=e['at'],actor=e['actor'],action=e['decision'],detail=e['conclusion']+'\n'+'\n'.join(e['checks'])) for e in data['events']]
            a=db.scalar(select(Attachment).where(Attachment.case_id==c.id,Attachment.name=='Swipies-workflow.docx'))
            if a:a.content=build_workflow(view(c),history);a.at=stamp()
            for event_index,e in enumerate(data['events'][previous:],start=previous+1):
                snapshot_history=history[:event_index]
                snapshot={**view(c),'state':e['stage']}
                if e['decision']!='simulated_payment':snapshot.pop('payment',None);snapshot.pop('payroll',None)
                db.add(Attachment(case_id=c.id,actor=c.owner,name=f"AI-step-{event_index}-{e['stage']}.docx",content=build_workflow(snapshot,snapshot_history)))
        db.commit()

async def execute(id):
    with DB() as db:
        run=db.get(DemoRun,id)
        if not run or run.status!='running':return
        data=json.loads(run.data);stage=run.stage
    try:
        if data.pop('human_rejected',False):
            data.pop('pending',None);save(id,stage,'rejected',data);return
        proposal=data.pop('confirmed',None)
        index=STAGES.index(stage)
        if proposal:
            answer=Answer.model_validate(proposal)
            data['events'].append({'stage':stage,'actor':NAMES[stage],'decision':answer.decision,'conclusion':answer.conclusion,'checks':answer.checks,'at':stamp()})
            if answer.decision=='reject':save(id,stage,'rejected',data);return
            if answer.decision=='return':index=max(0,index-1)
            else:index+=1
            if index==len(STAGES):
                p=data['card']['payroll_input'];gross=(Decimal(data['card']['salary'])*Decimal(p['worked_days'])/Decimal(p['scheduled_days'])+Decimal(p['bonus'])).quantize(Decimal('.01'))
                data['payroll']={**p,'gross':str(gross),'net':str(gross-Decimal(p['deduction']))}
                data['payment']={'reference':'DEMO-SIMULATION-'+str(id),'paid_on':p['payment_due'],'simulated':True}
                data['events'].append({'stage':'accountant','actor':NAMES['accountant'],'decision':'simulated_payment','conclusion':'Учебный приём согласован человеком на всех этапах. Выплата только имитирована; деньги не переводились.','checks':['Начислено: '+str(gross),'К выплате: '+data['payroll']['net']],'at':stamp()})
                save(id,'completed','completed',data);return
        if len(data['events'])>=12:
            data['error']='Достигнут предел повторных проверок.';save(id,stage,'stopped',data);return
        stage=STAGES[index];save(id,stage,'running',data)
        chat=await assistant();answer=await ask(chat,stage,data['card'],data['events'])
        data['pending']={**answer.model_dump(),'token':secrets.token_urlsafe(24),'stage':stage,'at':stamp()}
        save(id,stage,'waiting_confirmation',data)
    except Exception:
        data['error']='Не удалось получить корректное решение Swipies. Проверьте подключение и доступность модели.'
        save(id,stage,'error',data)

class Confirmation(BaseModel):
    token:str=Field(min_length=10,max_length=100)
    action:Literal['approve','reject']
    comment:str=Field(default='',max_length=2000)

def recover():
    with DB() as db:
        for r in db.scalars(select(DemoRun).where(DemoRun.status=='running')):
            d=json.loads(r.data);d['error']='Сервер перезапущен. Для продолжения запустите новую демонстрацию.';r.data=json.dumps(d,ensure_ascii=False);r.status='interrupted';r.slot=None
            if d.get('case_id'):
                c=db.get(Case,d['case_id']);card=json.loads(c.data);card.update(ai_status='interrupted',ai_error=d['error']);c.data=json.dumps(card,ensure_ascii=False)
        db.commit()

def register(app):
    @app.post('/api/demo/cases')
    def launch_case(tasks:BackgroundTasks,u=Depends(current),db=Depends(database)):
        if not os.getenv('RAGFLOW_API_KEY'):raise HTTPException(503,'Не настроен ключ Swipies')
        card=fictional();card['department']=u.department
        c=Case(owner=u.id,department=u.department,data=json.dumps({**card,'documents':[], 'autonomous_demo':True,'ai_status':'running','ai_stage':'manager'},ensure_ascii=False));db.add(c);db.flush()
        r=DemoRun(status='running',slot=1,stage='manager',data=json.dumps({'case_id':c.id,'owner_id':u.id,'card':card,'events':[]},ensure_ascii=False));db.add(r)
        from app.documents import build_workflow
        db.add(Attachment(case_id=c.id,actor=u.id,name='Swipies-workflow.docx',content=build_workflow(view(c))))
        db.add(Audit(actor=u.id,case_id=c.id,action='ai_start',detail=json.dumps({'conclusion':'Запущены агенты Swipies. Все данные учебные; выплата только имитируется.'},ensure_ascii=False)))
        try:db.commit()
        except IntegrityError:db.rollback();raise HTTPException(409,'Агенты уже выполняют заявку. Дождитесь завершения.')
        tasks.add_task(execute,r.id);return output(r)
    @app.post('/api/demo/runs/{id}/confirm')
    def confirm(id:int,p:Confirmation,tasks:BackgroundTasks,u=Depends(current),db=Depends(database)):
        r=db.get(DemoRun,id)
        if not r:raise HTTPException(404,'Запуск не найден')
        data=json.loads(r.data)
        if data.get('case_id'):getcase(db,u,data['case_id'])
        require(u,r.stage)
        pending=data.get('pending')
        if r.status!='waiting_confirmation' or not pending or not secrets.compare_digest(p.token,pending['token']):raise HTTPException(409,'Решение уже подтверждено или изменилось. Обновите карточку.')
        before=r.data;proposal=data.pop('pending')
        if p.action=='approve':data['confirmed']={k:proposal[k] for k in ['decision','conclusion','checks']}
        else:data['human_rejected']=True
        changed=db.execute(update(DemoRun).where(DemoRun.id==id,DemoRun.status=='waiting_confirmation',DemoRun.data==before).values(status='running',data=json.dumps(data,ensure_ascii=False),updated=stamp()).execution_options(synchronize_session=False))
        if changed.rowcount!=1:db.rollback();raise HTTPException(409,'Решение уже обработано')
        if data.get('case_id'):
            db.add(Audit(actor=u.id,case_id=data['case_id'],action='human_confirm' if p.action=='approve' else 'human_reject',detail=json.dumps({'stage':r.stage,'conclusion':proposal['conclusion'],'comment':p.comment or ('Передача подтверждена человеком' if p.action=='approve' else 'Процесс остановлен человеком')},ensure_ascii=False)))
        db.commit();tasks.add_task(execute,id);return {'status':'running'}
    @app.get('/api/demo/cases/{id}/documents.zip')
    def bundle(id:int,u=Depends(current),db=Depends(database)):
        from zipfile import ZipFile, ZIP_DEFLATED
        from fastapi.responses import Response
        from app.documents import build_workflow
        c=getcase(db,u,id);files=list(db.scalars(select(Attachment).where(Attachment.case_id==id)))
        history=[dict(at=a.at,actor=str(a.actor),action=a.action,detail=a.detail) for a in db.scalars(select(Audit).where(Audit.case_id==id).order_by(Audit.id))]
        out=BytesIO()
        with ZipFile(out,'w',ZIP_DEFLATED) as archive:
            archive.writestr('Final-workflow.docx',build_workflow(view(c),history,[dict(name=a.name,at=a.at) for a in files]))
            for a in files:
                from pathlib import Path
                archive.writestr(str(a.id)+'-'+Path(a.name.replace('\\','/')).name,a.content)
        return Response(out.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="domplan-{id}-documents.zip"'})
    @app.get('/api/demo/runs')
    def runs(u=Depends(current),db=Depends(database)):
        return [output(r) for r in db.scalars(select(DemoRun).order_by(DemoRun.id.desc()).limit(20))]
    @app.post('/api/demo/runs')
    def launch(tasks:BackgroundTasks,u=Depends(current),db=Depends(database)):
        if not os.getenv('RAGFLOW_API_KEY'):raise HTTPException(503,'Не настроен ключ Swipies')
        r=DemoRun(status='running',slot=1,stage='manager',data=json.dumps({'owner_id':u.id,'card':fictional(),'events':[]},ensure_ascii=False));db.add(r)
        try:db.commit()
        except IntegrityError:db.rollback();raise HTTPException(409,'Демонстрация уже выполняется. Дождитесь завершения.')
        tasks.add_task(execute,r.id);return output(r)
    @app.get('/api/demo/runs/{id}/document.docx')
    def document(id:int,u=Depends(current),db=Depends(database)):
        from docx import Document
        r=db.get(DemoRun,id)
        if not r:raise HTTPException(404,'Демонстрация не найдена')
        data=json.loads(r.data);d=Document();d.add_heading('DOMPLAN • АВТОНОМНОЕ ДЕМО ИИ',0)
        d.add_paragraph('Все данные вымышленные. Документ не создаёт реальных кадровых решений и платежей.')
        card=data['card'];d.add_heading(card['name'],1)
        for text in [card['position'],card['department'],'Условный оклад: '+card['salary'],*card['documents']]:d.add_paragraph(text)
        for a in data['events']:
            d.add_heading(a['actor'],1);d.add_paragraph(a['at'][:16].replace('T',' '))
            d.add_paragraph({'approve':'Передано дальше','return':'Возвращено на повторную проверку','reject':'Отклонено в симуляции','simulated_payment':'Имитация выплаты'}.get(a['decision'],'Проверено'))
            d.add_paragraph(a['conclusion'])
            for check in a['checks']:d.add_paragraph(check,style='List Bullet')
        if data.get('payroll'):d.add_heading('К выплате в симуляции: '+data['payroll']['net'],1)
        out=BytesIO();d.save(out);return word_response(out.getvalue(),'demo-agent-'+str(id)+'.docx')
