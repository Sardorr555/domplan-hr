let demoTimer;
const demoStages={manager:'Руководитель',hr:'HR',ceo:'Директор',accountant:'Бухгалтер',completed:'Завершено'};
const demoStatuses={running:'Агент работает',completed:'Демонстрация завершена',rejected:'Отклонено агентом в симуляции',error:'Нужен повторный запуск',stopped:'Предел повторных проверок',interrupted:'Прервано перезапуском'};
function hideDemo(){clearTimeout(demoTimer);document.querySelector('#demopanel').hidden=true}
async function demoHome(){
    clearTimeout(demoTimer);$('#workspace').hidden=true;$('#adminpanel').hidden=true;$('#demopanel').hidden=false;
    const runs=await api('/demo/runs'),panel=$('#demoresults'),rendered=[];
    $('#demostart').disabled=runs.some(r=>r.status==='running');
    if(!runs.length)panel.append(el('p','Нажмите «Запустить агента». Он сам проведёт вымышленного сотрудника через все этапы.'));
    for(const run of runs.slice(0,5)){
        const box=el('section');box.append(el('span','ДЕМО · ТОЛЬКО ВЫМЫШЛЕННЫЕ ДАННЫЕ','badge'),el('h2','Запуск № '+run.id+' · '+demoStatuses[run.status]),el('p',run.card.name+' · '+run.card.position));
        box.append(agentFlow('run-'+run.id,run.stage,run.status,run.events));
        if(run.error)box.append(el('p',run.error));
        for(const event of run.events){const item=el('article',undefined,'audit-entry');item.append(el('h3',event.actor+' · '+({approve:'Передано дальше',return:'Возвращено на проверку',reject:'Отклонено',simulated_payment:'Имитация выплаты'}[event.decision])),el('p',readableDate(event.at,true),'audit-meta'),el('p',event.conclusion));const list=el('ul');for(const check of event.checks)list.append(el('li',check));item.append(list);box.append(item)}
        if(run.payroll){const p=run.payroll;box.append(el('h2','Демо: к выплате '+readableValue('net',p.net)),el('p','Начислено '+readableValue('gross',p.gross)+' · Удержания '+readableValue('deduction',p.deduction)),el('p','Выплата только имитирована. Деньги не переводились.'))}
        const link=el('a','Скачать учебный документ с решениями ИИ');link.href='/api/demo/runs/'+run.id+'/document.docx';box.append(link);rendered.push(box)
    }
    panel.replaceChildren(...rendered);
    if(runs.some(r=>r.status==='running'))demoTimer=setTimeout(()=>demoHome().catch(message),2500)
}
$('#demonav').onclick=()=>demoHome().catch(message);
$('#demostart').onclick=async()=>{const button=$('#demostart');button.disabled=true;try{await api('/demo/runs','POST');await demoHome()}catch(e){message(e);button.disabled=false}};

$('#agentstart').onclick=async()=>{const b=$('#agentstart');b.disabled=true;try{const run=await api('/demo/cases','POST');selected=run.case_id;await reload()}catch(e){message(e)}finally{b.disabled=cases.some(c=>c.autonomous_demo&&c.ai_status==='running')}};
let agentPolling=false;
setInterval(async()=>{if(!me||$('#workspace').hidden||agentPolling||document.hidden)return;agentPolling=true;try{if(cases.some(c=>c.autonomous_demo&&c.ai_status==='running'))await reload();$('#agentstart').disabled=cases.some(c=>c.autonomous_demo&&c.ai_status==='running')}catch(e){message(e)}finally{agentPolling=false}},2500);
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
        node.querySelector('.agent-state').textContent=working?'Анализирует документ':done?'Передал дальше':index===active&&status==='rejected'?'Отклонил':index===active&&status!=='running'?'Остановлен':'Ожидает';
        if(status==='completed'&&index===3)node.querySelector('.agent-state').textContent='Расчёт завершён';
    });
    wires.forEach((wire,index)=>{wire.className='agent-wire'+(status==='completed'||active>index?' is-done':'')+(status==='running'&&active===index+1?' is-transferring':'')});
    flow.querySelector('.agent-caption').textContent=status==='running'?'Сейчас работает '+(demoStages[stage]||'агент')+'. Следующий этап запустится автоматически.':status==='completed'?'Все четыре агента завершили работу. Документ готов, выплата имитирована.':(demoStatuses[status]||'Процесс ожидает запуска');
    return flow;
}
