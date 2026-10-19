let demoTimer;
const demoStages={manager:'Руководитель',hr:'HR',ceo:'Директор',accountant:'Бухгалтер',completed:'Завершено'};
const demoStatuses={running:'Агент работает',completed:'Демонстрация завершена',rejected:'Отклонено агентом в симуляции',error:'Нужен повторный запуск',stopped:'Предел повторных проверок',interrupted:'Прервано перезапуском'};
function hideDemo(){clearTimeout(demoTimer);document.querySelector('#demopanel').hidden=true}
async function demoHome(){
    clearTimeout(demoTimer);$('#workspace').hidden=true;$('#adminpanel').hidden=true;$('#demopanel').hidden=false;
    const runs=await api('/demo/runs'),panel=$('#demoresults');panel.replaceChildren();
    $('#demostart').disabled=runs.some(r=>r.status==='running');
    if(!runs.length)panel.append(el('p','Нажмите «Запустить агента». Он сам проведёт вымышленного сотрудника через все этапы.'));
    for(const run of runs.slice(0,5)){
        const box=el('section');box.append(el('span','ДЕМО · ТОЛЬКО ВЫМЫШЛЕННЫЕ ДАННЫЕ','badge'),el('h2','Запуск № '+run.id+' · '+demoStatuses[run.status]),el('p',run.card.name+' · '+run.card.position));
        const progress=el('div',undefined,'demo-progress');for(const [key,name] of Object.entries(demoStages)){if(key==='completed')continue;let seen=run.events.filter(e=>e.stage===key&&e.decision!=='simulated_payment'),last=seen[seen.length-1];progress.append(el('span',name+' · '+(run.status==='running'&&run.stage===key?'анализирует…':last?{approve:'проверено',return:'возвращено',reject:'отклонено'}[last.decision]:'ожидает'),'badge'))}box.append(progress);
        if(run.error)box.append(el('p',run.error));
        for(const event of run.events){const item=el('article',undefined,'audit-entry');item.append(el('h3',event.actor+' · '+({approve:'Передано дальше',return:'Возвращено на проверку',reject:'Отклонено',simulated_payment:'Имитация выплаты'}[event.decision])),el('p',readableDate(event.at,true),'audit-meta'),el('p',event.conclusion));const list=el('ul');for(const check of event.checks)list.append(el('li',check));item.append(list);box.append(item)}
        if(run.payroll){const p=run.payroll;box.append(el('h2','Демо: к выплате '+readableValue('net',p.net)),el('p','Начислено '+readableValue('gross',p.gross)+' · Удержания '+readableValue('deduction',p.deduction)),el('p','Выплата только имитирована. Деньги не переводились.'))}
        const link=el('a','Скачать учебный документ с решениями ИИ');link.href='/api/demo/runs/'+run.id+'/document.docx';box.append(link);panel.append(box)
    }
    if(runs.some(r=>r.status==='running'))demoTimer=setTimeout(()=>demoHome().catch(message),2500)
}
$('#demonav').onclick=()=>demoHome().catch(message);
$('#demostart').onclick=async()=>{const button=$('#demostart');button.disabled=true;try{await api('/demo/runs','POST');await demoHome()}catch(e){message(e);button.disabled=false}};
