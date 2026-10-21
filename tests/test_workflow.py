import os
os.environ['DATABASE_URL']='sqlite://'
os.environ['COOKIE_SECURE']='false'
from sqlalchemy import create_engine,select
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import pytest
from app import main as m

@pytest.fixture(autouse=True)
def database(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    monkeypatch.setattr(m,'engine',engine); m.DB.configure(bind=engine);m.Base.metadata.create_all(engine)
    with m.DB() as db:
        for role in m.ROLES:
            db.add(m.User(email=role+'@demo.local',password=m.password_hash('TestPassword123'),role=role,department='Operations'))
        db.add(m.User(email='other@demo.local',password=m.password_hash('TestPassword123'),role='manager',department='Other'))
        db.commit()
    yield
    engine.dispose()
def client(role):
    c=TestClient(m.app)
    r=c.post('/api/login',json={'email':role+'@demo.local','password':'TestPassword123'})
    assert r.status_code==200
    c.headers['x-csrf-token']=r.json()['csrf'];return c
def new(c):
    r=c.post('/api/cases',json=dict(name='Demo Person',position='Engineer',department='Operations',salary='22000.00',start_date='2026-01-01',conclusion='Recommended'))
    assert r.status_code==200;return r.json()
def decide(c,case,action='approve'):
    return c.post(f"/api/cases/{case['id']}/decision",json=dict(action=action,comment='Reviewed by responsible person',version=case['version']))
def test_complete_workflow():
    manager=client('manager'); c=new(manager)
    for role in ['manager','hr','ceo','accountant']:
        r=decide(client(role),c);assert r.status_code==200,r.text;c=r.json()
    accountant=client('accountant')
    p=dict(period='2026-01',scheduled_days=22,worked_days=11,bonus='500',deduction='1000',payment_due='2026-02-01',version=c['version'])
    r=accountant.post(f"/api/cases/{c['id']}/payroll",json=p);assert r.status_code==200;c=r.json();assert c['payroll']['net']=='10500.00'
    payload=dict(reference='BANK-001',paid_on='2026-02-01',version=c['version'])
    assert accountant.post(f"/api/cases/{c['id']}/payment",json=payload).json()['state']=='paid'
    assert accountant.post(f"/api/cases/{c['id']}/payment",json=payload).status_code==409
    assert len(manager.get(f"/api/cases/{c['id']}/history").json())==7
def test_scope_rbac_csrf_and_order():
    c=client('manager');case=new(c)
    assert decide(client('hr'),case).status_code==403
    assert client('other').get('/api/cases').json()==[]
    assert client('other').get(f"/api/cases/{case['id']}/history").status_code==404
    assert c.get('/api/users').status_code==403
    assert TestClient(m.app).get('/api/cases').status_code==401
    c.headers.pop('x-csrf-token');assert decide(c,case).status_code==403
def test_superadmin_returns_edits_and_stale_version():
    c=client('superadmin');case=new(c);old=case.copy()
    case=decide(c,case).json();assert decide(c,old).status_code==409
    case=decide(c,case,'return').json();assert case['state']=='manager'
    case=decide(c,case,'reject').json();assert case['state']=='rejected'
    assert decide(c,case).status_code==409
def test_invalid_payroll_and_approval_gate():
    c=client('superadmin');case=new(c)
    payload=dict(period='2026-01',scheduled_days=22,worked_days=11,bonus=0,deduction=0,payment_due='2026-02-01',version=case['version'])
    assert c.post(f"/api/cases/{case['id']}/payroll",json=payload).status_code==409
    for _ in range(4):case=decide(c,case).json()
    payload.update(version=case['version'],worked_days=23)
    assert c.post(f"/api/cases/{case['id']}/payroll",json=payload).status_code==422
    payload.update(worked_days=11,deduction=999999)
    assert c.post(f"/api/cases/{case['id']}/payroll",json=payload).status_code==422
def test_sessions_disable_and_rate_limit():
    admin=client('superadmin');hr=client('hr'); users=admin.get('/api/users').json();uid=next(x['id'] for x in users if x['role']=='hr')
    assert admin.post(f'/api/users/{uid}/toggle').status_code==200
    assert hr.get('/api/me').status_code==401
    c=TestClient(m.app)
    for _ in range(8):assert c.post('/api/login',json={'email':'unknown','password':'wrong'}).status_code==401
    assert c.post('/api/login',json={'email':'unknown','password':'wrong'}).status_code==429
def test_unconfigured_ragflow():
    c=client('superadmin');case=new(c)
    r=c.post(f"/api/cases/{case['id']}/documents",content=b'demo',headers={'x-filename':'resume.txt'})
    assert r.status_code==503

def test_ragflow_contract_and_document_scope(monkeypatch):
    calls=[]
    async def fake(method,path,**kwargs):
        calls.append((method,path,kwargs))
        if method=='POST' and path=='/documents':return [{'id':'doc-001'}]
        if method=='GET':return {'chunks':[{'content':'Candidate resume'}]}
        return True
    monkeypatch.setattr(m,'rag',fake)
    c=client('superadmin');case=new(c)
    r=c.post(f"/api/cases/{case['id']}/documents",content=b'resume',headers={'x-filename':'resume.txt'})
    assert r.status_code==200
    assert calls[1][1]=='/chunks' and calls[1][2]['json']=={'document_ids':['doc-001']}
    assert c.get(f"/api/cases/{case['id']}/documents/doc-001").json()['chunks'][0]['content']=='Candidate resume'
    assert c.get(f"/api/cases/{case['id']}/documents/unknown").status_code==404
    assert client('accountant').post(f"/api/cases/{case['id']}/documents",content=b'a',headers={'x-filename':'a.txt'}).status_code==403

def test_frontend_and_logout():
    c=client('manager')
    for path in ['/','/app.js','/style.css']:
        r=c.get(path);assert r.status_code==200;assert r.headers['x-content-type-options']=='nosniff'
    assert c.post('/api/logout').status_code==200
    assert c.get('/api/me').status_code==401

def test_local_documents_follow_case_and_export():
    from io import BytesIO
    from docx import Document
    manager=client('manager');case=new(manager);cid=case['id']
    r=manager.post(f'/api/cases/{cid}/attachments',content=b'local resume',headers={'x-filename':'resume.txt'})
    assert r.status_code==200;aid=r.json()['id']
    assert client('other').get(f'/api/cases/{cid}/attachments/{aid}/download').status_code==404
    assert client('hr').post(f'/api/cases/{cid}/attachments',content=b'a',headers={'x-filename':'a.txt'}).status_code==403
    assert decide(manager,case).status_code==200
    hr=client('hr');assert hr.get(f'/api/cases/{cid}/attachments/{aid}/download').content==b'local resume'
    result=hr.get(f'/api/cases/{cid}/workflow.docx');assert result.status_code==200
    doc=Document(BytesIO(result.content));text='\n'.join(p.text for p in doc.paragraphs)
    assert 'manager@demo.local' in text and 'resume.txt' in text
    assert manager.get('/api/workflow-template.docx').status_code==200
    assert hr.post(f'/api/cases/{cid}/attachments',content=b'bad',headers={'x-filename':'a.exe'}).status_code==422

@pytest.mark.parametrize('role', ['manager','hr','ceo','accountant'])
def test_admin_routes_forbidden_for_non_admin(role):
    c=client(role)
    for path in ['/api/users','/api/audit']:assert c.get(path).status_code==403
    assert c.post('/api/users',json={'email':'new@test.local','password':'LongTestPass123','role':'superadmin','department':'Operations'}).status_code==403
    assert c.post('/api/users/1/toggle').status_code==403

@pytest.mark.parametrize('stage', ['manager','hr','ceo','accountant'])
def test_only_current_role_can_approve(stage):
    admin=client('superadmin');case=new(admin)
    while case['state']!=stage:case=decide(admin,case).json()
    for role in ['manager','hr','ceo','accountant']:
        if role!=stage:assert decide(client(role),case).status_code==403
    assert decide(client(stage),case).status_code==200

@pytest.mark.parametrize('stage,previous',[('hr','manager'),('ceo','hr'),('accountant','ceo')])
def test_returns_preserve_history_and_require_reapproval(stage,previous):
    admin=client('superadmin');case=new(admin)
    while case['state']!=stage:case=decide(admin,case).json()
    role=client(stage);r=decide(role,case,'return');assert r.status_code==200
    case=r.json();assert case['state']==previous
    assert decide(role,case).status_code==403
    history=admin.get(f"/api/cases/{case['id']}/history").json()
    assert history[-1]['action']=='return' and history[-1]['actor']==stage+'@demo.local'

@pytest.mark.parametrize('stage', ['manager','hr','ceo','accountant'])
def test_rejection_is_final_at_each_approval_stage(stage):
    admin=client('superadmin');case=new(admin)
    while case['state']!=stage:case=decide(admin,case).json()
    case=decide(client(stage),case,'reject').json();assert case['state']=='rejected'
    assert decide(admin,case).status_code==409
    assert admin.post(f"/api/cases/{case['id']}/payment",json={'reference':'TEST','paid_on':'2026-02-01','version':case['version']}).status_code==409

def test_edit_restricted_to_manager_stage_and_version():
    c=client('manager');case=new(c);cid=case['id']
    data={k:case[k] for k in ['name','position','department','salary','start_date','conclusion']}
    data['salary']='25000.00'
    edited=c.put(f'/api/cases/{cid}?version={case["version"]}',json=data);assert edited.status_code==200
    assert c.put(f'/api/cases/{cid}?version={case["version"]}',json=data).status_code==409
    case=edited.json();case=decide(c,case).json()
    assert c.put(f'/api/cases/{cid}?version={case["version"]}',json=data).status_code==409
    assert client('hr').put(f'/api/cases/{cid}?version={case["version"]}',json=data).status_code==403

def test_payroll_rounding_recalculation_and_payment_permissions():
    admin=client('superadmin');case=new(admin)
    for _ in range(4):case=decide(admin,case).json()
    cid=case['id'];body={'period':'2026-01','scheduled_days':3,'worked_days':1,'bonus':'0.01','deduction':'0.00','payment_due':'2026-02-01','version':case['version']}
    for role in ['manager','hr','ceo']:assert client(role).post(f'/api/cases/{cid}/payroll',json=body).status_code==403
    ac=client('accountant');r=ac.post(f'/api/cases/{cid}/payroll',json=body);assert r.status_code==200;case=r.json()
    assert case['payroll']['net']=='7333.34'
    assert ac.post(f'/api/cases/{cid}/payroll',json=body).status_code==409
    body.update(version=case['version'],worked_days=2)
    case=ac.post(f'/api/cases/{cid}/payroll',json=body).json();assert case['payroll']['net']=='14666.68'
    payment={'reference':'TEST-ONLY','paid_on':'2026-02-01','version':case['version']}
    for role in ['manager','hr','ceo']:assert client(role).post(f'/api/cases/{cid}/payment',json=payment).status_code==403
    assert ac.post(f'/api/cases/{cid}/payment',json={**payment,'paid_on':'2099-01-01'}).status_code==422
    assert ac.post(f'/api/cases/{cid}/payment',json=payment).status_code==200

def test_admin_account_validation_and_self_disable():
    c=client('superadmin');u={'email':'qa@example.local','password':'StrongPassword123','role':'manager','department':'QA'}
    assert c.post('/api/users',json={**u,'password':'short'}).status_code==422
    assert c.post('/api/users',json={**u,'role':'root'}).status_code==422
    assert c.post('/api/users',json=u).status_code==200
    assert c.post('/api/users',json=u).status_code==409
    me=c.get('/api/me').json();assert c.post(f'/api/users/{me["id"]}/toggle').status_code==422
    assert c.post('/api/users/999999/toggle').status_code==404

def test_attachment_limits_csrf_and_cross_case_access():
    c=client('superadmin');case=new(c);other=new(c);cid=case['id']
    assert c.post(f'/api/cases/{cid}/attachments',content=b'',headers={'x-filename':'a.txt'}).status_code==422
    assert c.post(f'/api/cases/{cid}/attachments',content=b'x'*(10*1024*1024+1),headers={'x-filename':'a.txt'}).status_code==413
    r=c.post(f'/api/cases/{cid}/attachments',content=b'percent file',headers={'x-filename':'100%25-test.txt'})
    assert r.status_code==200;aid=r.json()['id'];assert r.json()['name']=='100%-test.txt'
    assert c.get(f'/api/cases/{other["id"]}/attachments/{aid}/download').status_code==404
    c.headers.pop('x-csrf-token')
    assert c.post(f'/api/cases/{cid}/attachments',content=b'bad',headers={'x-filename':'a.txt'}).status_code==403

def test_expired_sessions_and_forged_cookie_rejected():
    from datetime import timedelta
    c=client('manager')
    with m.DB() as db:
        session=db.scalar(select(m.LoginSession));session.expires=(m.now()-timedelta(seconds=1)).isoformat();db.commit()
    assert c.get('/api/me').status_code==401
    forged=TestClient(m.app);forged.cookies.set('session','made-up-token');assert forged.get('/api/cases').status_code==401

def test_ragflow_failure_preserves_local_attachment(monkeypatch):
    async def fail(*args,**kwargs):raise m.HTTPException(502,'Test upstream failure')
    monkeypatch.setattr(m,'rag',fail)
    c=client('superadmin');case=new(c);cid=case['id']
    a=c.post(f'/api/cases/{cid}/attachments',content=b'local data',headers={'x-filename':'a.txt'}).json()
    assert c.post(f'/api/cases/{cid}/attachments/{a["id"]}/analyze').status_code==502
    assert c.get(f'/api/cases/{cid}/attachments/{a["id"]}/download').content==b'local data'


def confirm_demo_until_finished():
    admin=client('superadmin')
    for _ in range(15):
        run=admin.get('/api/demo/runs').json()[0]
        if run['status']!='waiting_confirmation':return run
        response=admin.post(f"/api/demo/runs/{run['id']}/confirm",json={'token':run['pending']['token'],'action':'approve','comment':'Проверено человеком'})
        assert response.status_code==200,response.text
    raise AssertionError('Too many approvals')

def test_demo_autonomous_route_is_synthetic_and_separate(monkeypatch):
    from app import demo
    monkeypatch.setenv('RAGFLOW_API_KEY','test-key')
    async def assistant():return 'test-chat'
    calls=[]
    async def ask(chat,stage,card,events):
        calls.append(stage);assert card['name'].startswith('ДЕМО')
        return demo.Answer(decision='approve',conclusion='Учебная проверка пройдена',checks=['Синтетические данные'])
    monkeypatch.setattr(demo,'assistant',assistant);monkeypatch.setattr(demo,'ask',ask)
    c=client('superadmin');r=c.post('/api/demo/runs',json={'name':'This must be ignored','case_id':1})
    assert r.status_code==200
    waiting=c.get('/api/demo/runs').json()[0];assert waiting['status']=='waiting_confirmation' and calls==['manager']
    run=confirm_demo_until_finished()
    assert run['status']=='completed' and calls==demo.STAGES
    assert run['payroll']['net']=='10500.00' and run['payment']['simulated'] is True
    assert c.get('/api/cases').json()==[]
    assert c.get(f'/api/demo/runs/{run["id"]}/document.docx').status_code==200

def test_demo_reject_and_upstream_failure(monkeypatch):
    from app import demo
    monkeypatch.setenv('RAGFLOW_API_KEY','test-key')
    async def assistant():return 'test-chat'
    async def reject(*args):return demo.Answer(decision='reject',conclusion='Симуляция отклонения',checks=[])
    monkeypatch.setattr(demo,'assistant',assistant);monkeypatch.setattr(demo,'ask',reject)
    c=client('hr');assert c.post('/api/demo/runs').status_code==200
    assert confirm_demo_until_finished()['status']=='rejected'
    async def fail(*args):raise RuntimeError('upstream')
    monkeypatch.setattr(demo,'ask',fail);assert c.post('/api/demo/runs').status_code==200
    assert c.get('/api/demo/runs').json()[0]['status']=='error'

def test_demo_return_loop_is_bounded_and_needs_auth(monkeypatch):
    from app import demo
    monkeypatch.setenv('RAGFLOW_API_KEY','test-key')
    async def assistant():return 'test-chat'
    async def returns(*args):return demo.Answer(decision='return',conclusion='Повторить учебную проверку',checks=[])
    monkeypatch.setattr(demo,'assistant',assistant);monkeypatch.setattr(demo,'ask',returns)
    assert TestClient(m.app).post('/api/demo/runs').status_code==401
    c=client('ceo');assert c.post('/api/demo/runs').status_code==200
    run=confirm_demo_until_finished();assert run['status']=='stopped' and len(run['events'])==12

def test_demo_uses_swipies_dialogue_contract(monkeypatch):
    import asyncio
    from app import demo
    async def remote(method,path,**kwargs):
        assert path=='/chats/test-chat/completions' and kwargs['json']['stream'] is False
        assert 'вымышленные' in kwargs['json']['question']
        return {'code':0,'data':{'answer':'```json\n{"decision":"approve","conclusion":"Учебная проверка пройдена","checks":["Комплект проверен"]}\n```'}}
    monkeypatch.setattr(demo,'remote',remote)
    answer=asyncio.run(demo.ask('test-chat','hr',demo.fictional(),[]))
    assert answer.decision=='approve' and answer.checks==['Комплект проверен']
    with pytest.raises(Exception):demo.parse_answer('not a decision')

def test_agents_execute_in_original_case(monkeypatch):
    from app import demo
    monkeypatch.setenv('RAGFLOW_API_KEY','test')
    async def assistant():return 'test-chat'
    async def ask(chat,stage,card,events):
        assert card['department']=='Operations'
        return demo.Answer(decision='approve',conclusion='Учебные документы проверены агентом',checks=['Данные согласованы'])
    monkeypatch.setattr(demo,'assistant',assistant);monkeypatch.setattr(demo,'ask',ask)
    c=client('manager');r=c.post('/api/demo/cases');assert r.status_code==200,r.text
    assert c.get('/api/cases').json()[0]['ai_status']=='waiting_confirmation'
    confirm_demo_until_finished()
    id=r.json()['case_id'];case=c.get('/api/cases').json()[0]
    assert case['id']==id and case['state']=='paid' and case['ai_status']=='completed'
    assert case['payment']['simulated'] is True and case['payroll']['net']=='10500.00'
    hist=c.get(f'/api/cases/{id}/history').json()
    assert len([e for e in hist if e['action']=='ai_approve'])==4
    assert all(e['detail'].get('ai_role') for e in hist if e['action']=='ai_approve')
    assert c.post(f'/api/cases/{id}/payment',json={}).status_code==409
    files=c.get(f'/api/cases/{id}/attachments').json();assert len(files)==6
    assert len([f for f in files if f['name'].startswith('AI-step-')])==5
    upload=c.post(f'/api/cases/{id}/attachments',content=b'Updated fictional document',headers={'X-Filename':'revision.txt'})
    assert upload.status_code==200
    assert len(c.get(f'/api/cases/{id}/attachments').json())==7
    doc=c.get(f"/api/cases/{id}/attachments/{files[0]['id']}/download")
    assert doc.status_code==200 and doc.content[:2]==b'PK'
    assert client('other').get('/api/cases').json()==[]

def test_linked_agent_failure_and_rejection(monkeypatch):
    from app import demo
    monkeypatch.setenv('RAGFLOW_API_KEY','test')
    async def assistant():return 'test'
    async def reject(*args):return demo.Answer(decision='reject',conclusion='Учебный комплект отклонён',checks=[])
    monkeypatch.setattr(demo,'assistant',assistant);monkeypatch.setattr(demo,'ask',reject)
    c=client('manager');assert c.post('/api/demo/cases').status_code==200
    confirm_demo_until_finished()
    case=c.get('/api/cases').json()[0];assert case['state']=='rejected' and 'payment' not in case
    assert TestClient(m.app).post(f"/api/cases/{case['id']}/decision",json={}).status_code==401
    async def failure(*args):raise RuntimeError('remote unavailable')
    monkeypatch.setattr(demo,'ask',failure)
    assert c.post('/api/demo/cases').status_code==200
    case=c.get('/api/cases').json()[0];assert case['ai_status']=='error' and 'payment' not in case


def test_confirmation_rbac_replay_and_final_bundle(monkeypatch):
    from app import demo
    monkeypatch.setenv('RAGFLOW_API_KEY','test')
    calls=[]
    async def assistant():return 'test'
    async def ask(chat,stage,*args):
        calls.append(stage)
        return demo.Answer(decision='approve',conclusion='Документ проверен агентом',checks=['Комплект полный'])
    monkeypatch.setattr(demo,'assistant',assistant);monkeypatch.setattr(demo,'ask',ask)
    manager=client('manager');r=manager.post('/api/demo/cases');assert r.status_code==200
    run=manager.get('/api/demo/runs').json()[0];id=run['id'];caseid=run['case_id']
    assert run['status']=='waiting_confirmation' and calls==['manager']
    assert 'payment' not in manager.get('/api/cases').json()[0]
    payload={'token':run['pending']['token'],'action':'approve'}
    assert client('hr').post(f'/api/demo/runs/{id}/confirm',json=payload).status_code==403
    demo.recover()
    assert manager.get('/api/demo/runs').json()[0]['status']=='waiting_confirmation'
    assert manager.post(f'/api/demo/runs/{id}/confirm',json=payload).status_code==200
    assert calls==['manager','hr']
    assert client('superadmin').post(f'/api/demo/runs/{id}/confirm',json=payload).status_code==409
    confirm_demo_until_finished()
    result=manager.get('/api/cases').json()[0];assert result['ai_status']=='completed' and result['payment']['simulated']
    hist=manager.get(f'/api/cases/{caseid}/history').json()
    assert len([e for e in hist if e['action']=='human_confirm'])==4
    bundle=manager.get(f'/api/demo/cases/{caseid}/documents.zip');assert bundle.status_code==200
    from zipfile import ZipFile
    from io import BytesIO
    with ZipFile(BytesIO(bundle.content)) as archive:assert 'Final-workflow.docx' in archive.namelist() and len(archive.namelist())==7
    assert client('other').get(f'/api/demo/cases/{caseid}/documents.zip').status_code==404

def test_human_can_stop_before_any_handoff(monkeypatch):
    from app import demo
    monkeypatch.setenv('RAGFLOW_API_KEY','test')
    async def assistant():return 'test'
    async def ask(*args):return demo.Answer(decision='approve',conclusion='Документы проверены',checks=[])
    monkeypatch.setattr(demo,'assistant',assistant);monkeypatch.setattr(demo,'ask',ask)
    c=client('manager');c.post('/api/demo/cases');run=c.get('/api/demo/runs').json()[0]
    r=c.post(f"/api/demo/runs/{run['id']}/confirm",json={'token':run['pending']['token'],'action':'reject','comment':'Нужны исправления'})
    assert r.status_code==200
    case=c.get('/api/cases').json()[0];assert case['ai_status']=='rejected' and 'payment' not in case
