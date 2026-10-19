"""Autonomous simulation. Only server-generated fictional data enters the model."""
import json, os, re
from decimal import Decimal
from io import BytesIO
import httpx
from fastapi import BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import select, Text, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from app.main import Base, DB, current, database, stamp, word_response

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
        if status!='running':r.slot=None
        db.commit()

async def execute(id):
    with DB() as db:data=json.loads(db.get(DemoRun,id).data)
    stage='manager'
    try:
        chat=await assistant();index=0
        for step in range(12):
            stage=STAGES[index];save(id,stage,'running',data)
            answer=await ask(chat,stage,data['card'],data['events'])
            data['events'].append({'stage':stage,'actor':NAMES[stage],'decision':answer.decision,'conclusion':answer.conclusion,'checks':answer.checks,'at':stamp()})
            if answer.decision=='reject':save(id,stage,'rejected',data);return
            if answer.decision=='return':
                index=max(0,index-1);save(id,STAGES[index],'running',data);continue
            index+=1
            if index==len(STAGES):
                p=data['card']['payroll_input'];gross=(Decimal(data['card']['salary'])*Decimal(p['worked_days'])/Decimal(p['scheduled_days'])+Decimal(p['bonus'])).quantize(Decimal('.01'))
                data['payroll']={**p,'gross':str(gross),'net':str(gross-Decimal(p['deduction']))}
                data['payment']={'reference':'DEMO-SIMULATION-'+str(id),'paid_on':p['payment_due'],'simulated':True}
                data['events'].append({'stage':'accountant','actor':NAMES['accountant'],'decision':'simulated_payment','conclusion':'Демонстрационный расчёт завершён. Записана только имитация выплаты; деньги не переводились.','checks':['Начислено: '+str(gross),'К выплате: '+data['payroll']['net']],'at':stamp()})
                save(id,'completed','completed',data);return
            save(id,STAGES[index],'running',data)
        data['error']='Достигнут предел повторных проверок. Запустите новую демонстрацию.';save(id,stage,'stopped',data)
    except Exception:
        data['error']='Не удалось получить корректное решение Swipies. Проверьте доступность модели и подключение, затем запустите демонстрацию заново.'
        save(id,stage,'error',data)

def recover():
    with DB() as db:
        for r in db.scalars(select(DemoRun).where(DemoRun.status=='running')):
            d=json.loads(r.data);d['error']='Сервер перезапущен. Для продолжения запустите новую демонстрацию.';r.data=json.dumps(d,ensure_ascii=False);r.status='interrupted';r.slot=None
        db.commit()

def register(app):
    @app.get('/api/demo/runs')
    def runs(u=Depends(current),db=Depends(database)):
        return [output(r) for r in db.scalars(select(DemoRun).order_by(DemoRun.id.desc()).limit(20))]
    @app.post('/api/demo/runs')
    def launch(tasks:BackgroundTasks,u=Depends(current),db=Depends(database)):
        if not os.getenv('RAGFLOW_API_KEY'):raise HTTPException(503,'Не настроен ключ Swipies')
        r=DemoRun(status='running',slot=1,stage='manager',data=json.dumps({'card':fictional(),'events':[]},ensure_ascii=False));db.add(r)
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
