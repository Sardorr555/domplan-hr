let demoTimer;
const demoStages={manager:'Руководитель',hr:'HR',ceo:'Директор',accountant:'Бухгалтер',completed:'Завершено'};
const demoStatuses={waiting_document:'Ожидает документ от человека',waiting_confirmation:'Ждёт подтверждения человека',running:'Агент работает',completed:'Демонстрация завершена',rejected:'Отклонено агентом в симуляции',error:'Нужен повторный запуск',stopped:'Предел повторных проверок',interrupted:'Прервано перезапуском'};
function hideDemo(){clearTimeout(demoTimer);document.querySelector('#demopanel').hidden=true}
async function demoHome(){
    clearTimeout(demoTimer);$('#workspace').hidden=true;$('#adminpanel').hidden=true;$('#demopanel').hidden=false;
    const runs=await api('/demo/runs'),panel=$('#demoresults'),rendered=[];
    $('#demostart').disabled=runs.some(r=>['running','waiting_confirmation','waiting_document'].includes(r.status));
    if(!runs.length)panel.append(el('p','Нажмите «Запустить агента». Он сам проведёт вымышленного сотрудника через все этапы.'));
    for(const run of runs.slice(0,5)){
        const box=el('section');box.append(el('span','ДЕМО · ТОЛЬКО ВЫМЫШЛЕННЫЕ ДАННЫЕ','badge'),el('h2','Запуск № '+run.id+' · '+demoStatuses[run.status]),el('p',run.card.name+' · '+run.card.position));
        box.append(agentFlow('run-'+run.id,run.stage,run.status,run.events));if(run.pending||run.status==='waiting_document')box.append(humanReview({...run,document_text:(run.card.documents||[]).join('\n\n')}));
        if(run.error)box.append(el('p',run.error));
        for(const event of run.events){const item=el('article',undefined,'audit-entry');item.append(el('h3',event.actor+' · '+({approve:'Передано дальше',return:'Возвращено на проверку',reject:'Отклонено',simulated_payment:'Имитация выплаты'}[event.decision])),el('p',readableDate(event.at,true),'audit-meta'),el('p',event.conclusion));const list=el('ul');for(const check of event.checks)list.append(el('li',check));item.append(list);box.append(item)}
        if(run.payroll){const p=run.payroll;box.append(el('h2','Демо: к выплате '+readableValue('net',p.net)),el('p','Начислено '+readableValue('gross',p.gross)+' · Удержания '+readableValue('deduction',p.deduction)),el('p','Выплата только имитирована. Деньги не переводились.'))}
        const link=el('a','Скачать учебный документ с решениями ИИ');link.href='/api/demo/runs/'+run.id+'/document.docx';box.append(link);rendered.push(box)
    }
    panel.replaceChildren(...rendered);
    if(runs.some(r=>['running','waiting_confirmation','waiting_document'].includes(r.status)))demoTimer=setTimeout(()=>demoHome().catch(message),2500)
}
$('#demonav').onclick=()=>demoHome().catch(message);
$('#demostart').onclick=async()=>{const button=$('#demostart');button.disabled=true;try{await api('/demo/runs','POST');await demoHome()}catch(e){message(e);button.disabled=false}};

$('#agentstart').onclick=async()=>{const b=$('#agentstart');b.disabled=true;try{const run=await api('/demo/cases/draft','POST');selected=run.case_id;await reload()}catch(e){message(e)}finally{b.disabled=cases.some(c=>c.autonomous_demo&&['running','waiting_confirmation','waiting_document'].includes(c.ai_status))}};
let agentPolling=false;
setInterval(async()=>{if(!me||$('#workspace').hidden||agentPolling||document.hidden)return;agentPolling=true;try{if(cases.some(c=>c.autonomous_demo&&['running','waiting_confirmation','waiting_document'].includes(c.ai_status)))await reload();$('#agentstart').disabled=cases.some(c=>c.autonomous_demo&&['running','waiting_confirmation','waiting_document'].includes(c.ai_status))}catch(e){message(e)}finally{agentPolling=false}},2500);
const agentRoles=['manager','hr','ceo','accountant'];
const agentFlowNodes=new Map();
function agentFlow(key,stage,status,events=[]){
    let flow=agentFlowNodes.get(key);
    if(!flow){
        flow=el('div',undefined,'agent-flow');flow.setAttribute('role','group');flow.setAttribute('aria-label','Передача документа между агентами Swipies');
        const track=el('div',undefined,'agent-track');
        agentRoles.forEach((role,index)=>{
            const node=el('div',undefined,'agent-node');node.dataset.role=role;
            node.append(el('div',String(index+1),'agent-circle'),el('b',demoStages[role]),el('span','Ожидает','agent-state'));track.append(node);
            if(index<3){const wire=el('div',undefined,'agent-wire');wire.append(el('span','▤','agent-document'));track.append(wire)}
        });
        flow.append(el('h3','Документ в работе у агентов'),track,el('p','','agent-caption'));agentFlowNodes.set(key,flow);
    }
    const active=agentRoles.indexOf(stage),signature=JSON.stringify([stage,status,events.map(e=>[e.stage,e.decision])]);
    if(flow.dataset.signature===signature)return flow;
    flow.dataset.signature=signature;
    const nodes=flow.querySelectorAll('.agent-node'),wires=flow.querySelectorAll('.agent-wire');
    nodes.forEach((node,index)=>{
        const last=events.filter(e=>e.stage===agentRoles[index]).at(-1);
        const done=status==='completed'||last?.decision==='approve'||last?.decision==='simulated_payment';
        const working=status==='running'&&index===active;
        node.className='agent-node'+(working?' is-active':done?' is-done':index===active&&status!=='running'?' is-paused':'');
        node.querySelector('.agent-circle').textContent=done&&!working?'✓':String(index+1);
        node.querySelector('.agent-state').textContent=working?'Анализирует документ':status==='waiting_confirmation'&&index===active?'Ждёт человека':done?'Передал дальше':index===active&&status==='rejected'?'Отклонил':index===active&&status!=='running'?'Остановлен':'Ожидает';
        if(status==='completed'&&index===3)node.querySelector('.agent-state').textContent='Расчёт завершён';
    });
    wires.forEach((wire,index)=>{wire.className='agent-wire'+(status==='completed'||active>index?' is-done':'')+(status==='running'&&active===index+1?' is-transferring':'')});
    flow.querySelector('.agent-caption').textContent=status==='running'?'Сейчас работает '+(demoStages[stage]||'агент')+'. После анализа потребуется подтверждение человека.':status==='completed'?'Все четыре агента завершили работу. Документ готов, выплата имитирована.':(demoStatuses[status]||'Процесс ожидает запуска');
    return flow;
}
function humanReview(run){
    const box=el('div',undefined,'human-review'),p=run.pending;
    if(me.role===run.stage||me.role==='superadmin')box.append(documentEditor(run));
    if(!p){box.prepend(el('h3','Загрузите свой документ или введите текст'));return box;}
    box.append(el('h3','Требуется подтверждение человека'),el('p',(demoStages[run.stage]||'Агент')+' предлагает: '+({approve:'передать документ дальше',return:'вернуть на предыдущий этап',reject:'отклонить заявку'}[p.decision])),el('p',p.conclusion));
    const list=el('ul');for(const check of p.checks)list.append(el('li',check));box.append(list);
    if(run.case_id){const doc=el('a','Скачать документ перед подтверждением');doc.href='/api/cases/'+run.case_id+'/workflow.docx';box.append(doc)}
    if(me.role!==run.stage&&me.role!=='superadmin'){box.append(el('p','Подтверждает '+demoStages[run.stage]+' или суперадминистратор.'));return box}
    const form=el('form');field(form,'comment','Комментарий к решению','textarea','',false);const controls=el('div',undefined,'actions');
    for(const [action,title] of [['approve','Подтвердить решение и продолжить'],['reject','Не подтверждать — остановить']]){const button=el('button',title);button.type='button';button.onclick=async()=>{controls.querySelectorAll('button').forEach(b=>b.disabled=true);try{await api('/demo/runs/'+run.id+'/confirm','POST',{token:p.token,action,comment:form.elements.comment.value});lastCasesSnapshot='';if(run.case_id)await reload();else await demoHome()}catch(e){message(e);controls.querySelectorAll('button').forEach(b=>b.disabled=false)}};controls.append(button)}form.append(controls);box.append(form);return box;
}
function finalOutcome(c){
    const box=el('div',undefined,'final-outcome');box.append(el('h2','Итог по сотруднику'));
    const rows=[['ФИО',c.name],['Должность',c.position],['Отдел',c.department],['Дата начала',readableDate(c.start_date)],['Результат',c.ai_status==='completed'?'Учебный приём согласован на всех этапах':c.ai_status==='rejected'?'Заявка отклонена; приём не согласован':'Приём ещё не согласован'],['Месячный оклад',readableValue('salary',c.salary)],['Начислено',c.payroll?readableValue('gross',c.payroll.gross):'Расчёт не завершён'],['Удержания',c.payroll?readableValue('deduction',c.payroll.deduction):'—'],['К выплате',c.payroll?readableValue('net',c.payroll.net):'—'],['Фактически перечислено','Перевод не выполнялся. Выплата в демо только имитируется.']];
    const dl=el('dl',undefined,'summary');for(const [key,value] of rows)dl.append(el('dt',key),el('dd',value));box.append(dl);
    const zip=el('a','Скачать все документы и итоговую карточку (ZIP)');zip.href='/api/demo/cases/'+c.id+'/documents.zip';box.append(zip);return box;
}

function documentEditor(run){
    const wrap=el('div',undefined,'document-editor');wrap.append(el('h3','Документ пользователя'),el('p','Загрузите DOCX/TXT или исправьте текст ниже. Сохранение запустит проверку новой версии с первого этапа; прежнее подтверждение использовать нельзя. Используйте учебные данные.'));
    const after=async()=>{lastCasesSnapshot='';if(run.case_id)await reload();else await demoHome()};
    const token=run.revision_token||run.pending?.token;
    const upload=el('form');const file=field(upload,'file','Загрузить свой документ (DOCX / TXT, до 10 MB)','file');file.accept='.docx,.txt';upload.append(el('button','Загрузить и отправить агенту на проверку'));
    handle(upload,async()=>{const doc=file.files[0];const r=await fetch('/api/demo/runs/'+run.id+'/revision-file',{method:'POST',headers:{'X-CSRF-Token':csrf,'X-Revision-Token':token,'X-Filename':encodeURIComponent(doc.name)},body:doc});const body=await r.json();if(!r.ok)throw Error(errorText(body.detail));await after()});wrap.append(upload);
    const edit=el('form');const text=field(edit,'text','Текст документа — можно редактировать','textarea',run.document_text||'');text.maxLength=50000;text.minLength=3;text.rows=12;edit.append(el('button','Сохранить исправления и повторить проверку'));handle(edit,async d=>{await api('/demo/runs/'+run.id+'/revision','POST',{token,text:d.text});await after()});wrap.append(edit);return wrap;
}
