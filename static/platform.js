/* Progressive enhancement: all core forms and ordering arrows work without JS. */
(() => {
  'use strict';
  const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const send = (url, body, method='POST') => fetch(url, {method, credentials:'same-origin', headers:{'X-CSRFToken':csrf()}, body});
  document.querySelectorAll('.platform-nav a').forEach(a => { if (location.pathname === new URL(a.href).pathname) a.setAttribute('aria-current','page'); });
  document.querySelectorAll('form[data-autosave]').forEach(async form => {
    const url='/teacher/draft/?path='+encodeURIComponent(location.pathname);
    let revision=0, timer, ready=false, stopping=false, changed=false, queue=Promise.resolve();
    const bar=document.createElement('div'); bar.className='draft-bar'; bar.setAttribute('role','status');
    const label=document.createElement('span'); label.textContent='Черновик: подключение…'; bar.append(label); form.prepend(bar);
    const set = text => {label.textContent=text;};
    const optionsRoot=form.querySelector('.option-editor');
    const addOption=() => {
      const total=form.querySelector('[name="options-TOTAL_FORMS"]');
      if (!total || !optionsRoot || +total.value>=50) return;
      const index=+total.value, last=optionsRoot.lastElementChild;
      if (!last) return;
      const row=last.cloneNode(true);
      row.querySelector('.option-number').textContent=index+1;
      row.querySelectorAll('input,textarea,label').forEach(el=>{
        ['name','id','for'].forEach(attr=>{if(el.hasAttribute(attr))el.setAttribute(attr,el.getAttribute(attr).replace(/options-\d+-/g,'options-'+index+'-'));});
        if(el.matches('input,textarea')){el.value='';el.checked=false;}
      });
      row.querySelectorAll('.errorlist').forEach(el=>el.remove());
      optionsRoot.append(row);total.value=index+1;
    };
    const plus=form.querySelector('[value=add_option]');
    if(plus){plus.type='button';plus.addEventListener('click',()=>{addOption();form.dispatchEvent(new Event('input'));});
      const help=plus.nextElementSibling;if(help?.textContent.includes('файлы'))help.textContent='Добавляйте варианты без перезагрузки страницы. До 50 строк.';}
    function collect(){
      const data={};
      for(const [key,value] of new FormData(form)) {
        if(value instanceof File || ['csrfmiddlewaretoken','action','submit_token'].includes(key))continue;
        (data[key] ||= []).push(value);
      }
      // Keep unchecked checkbox/radio names, otherwise restore cannot turn them off.
      form.querySelectorAll('input[type=checkbox],input[type=radio]').forEach(el=>{data[el.name] ||= [];});
      return data;
    }
    function save(){
      if(!ready || stopping)return queue;
      const data=collect();
      queue=queue.then(async()=>{
        set('Сохраняю черновик…');
        const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify({revision,data})});
        const result=await response.json();
        if(!response.ok){if(response.status===409)ready=false;throw Error(result.error||'Не удалось сохранить');}
        revision=result.revision;changed=false;set('Черновик сохранён на сервере · файлы — после кнопки «Сохранить»');
      }).catch(e=>set(e.message+' · проверьте соединение. Основная кнопка сохранения доступна.'));
      return queue;
    }
    form.addEventListener('input',()=>{changed=true;clearTimeout(timer);if(ready)timer=setTimeout(save,1000);});
    let releasing=false;
    form.addEventListener('submit',async event=>{
      if(releasing)return;
      const submitter=event.submitter;
      event.preventDefault(); clearTimeout(timer);
      if(changed && ready)save();stopping=true;await queue;
      // A resolved draft queue resumes in the original submit dispatch. Browsers
      // suppress a reentrant requestSubmit; schedule a new task instead.
      releasing=true;setTimeout(()=>form.requestSubmit(submitter),0);
    });
    try {
      const response=await fetch(url,{credentials:'same-origin'});
      if(!response.ok)throw Error('Черновик временно недоступен');
      const saved=await response.json();revision=saved.revision;
      if(Object.keys(saved.data).length){
        set('Есть несохранённый черновик от '+new Date(saved.updated_at).toLocaleString('ru-RU'));
        const restore=document.createElement('button');restore.type='button';restore.textContent='Восстановить';
        const discard=document.createElement('button');discard.type='button';discard.textContent='Оставить текущую форму';
        bar.append(restore,discard);
        restore.onclick=()=>{
          form.dispatchEvent(new CustomEvent('draft:prepare', {detail:saved.data}));
          const wanted=Number(saved.data['options-TOTAL_FORMS']?.[0]||0);
          let total=form.querySelector('[name="options-TOTAL_FORMS"]');
          while(total && +total.value<wanted && +total.value<50 && optionsRoot) addOption();
          form.querySelectorAll('input,textarea,select').forEach(el=>{
            if(['file','submit','button'].includes(el.type)||['csrfmiddlewaretoken','submit_token'].includes(el.name))return;
            const values=saved.data[el.name];if(!values)return;
            if(['checkbox','radio'].includes(el.type))el.checked=values.includes(el.value);
            else if(el.multiple)Array.from(el.options).forEach(o=>o.selected=values.includes(o.value));
            else el.value=values[0]||'';
          });
          form.dispatchEvent(new Event('draft:restored'));
          ready=true;restore.remove();discard.remove();set('Черновик восстановлен. Для вложений выберите файлы заново.');
        };
        discard.onclick=async()=>{const r=await send(url,null,'DELETE');if(r.ok){revision=0;ready=true;restore.remove();discard.remove();set('Текущая форма сохранится как новый черновик при изменении.');}else set('Не удалось удалить черновик.');};
      } else {ready=true;set('Автосохранение текста включено · публикация только кнопкой «Сохранить»');}
    }catch(e){set(e.message);}
  });
  document.querySelectorAll('[data-reorder]').forEach(container=>{
    let dragged=null;
    const status=document.createElement('p');status.className='reorder-state';status.setAttribute('role','status');container.before(status);
    const rows=()=>Array.from(container.children).filter(el=>el.dataset.rowId);
    rows().forEach(row=>{
      const handle=document.createElement('span');handle.className='drag-handle';handle.textContent='⠿';handle.title='Перетащите для изменения порядка; стрелки доступны рядом';handle.draggable=true;row.prepend(handle);
      handle.addEventListener('dragstart',e=>{e.stopPropagation();dragged=row;row.classList.add('dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',row.dataset.rowId);});
      handle.addEventListener('dragend',()=>{row.classList.remove('dragging');dragged=null;});
      row.addEventListener('dragover',e=>{if(dragged && dragged!==row){e.preventDefault();e.stopPropagation();}});
      row.addEventListener('drop',async e=>{
        if(!dragged || dragged===row)return;e.preventDefault();e.stopPropagation();
        const before=e.clientY<row.getBoundingClientRect().top+row.getBoundingClientRect().height/2;
        container.insertBefore(dragged,before?row:row.nextSibling);
        const data=new URLSearchParams({kind:container.dataset.reorder,parent:container.dataset.parent});rows().forEach(el=>data.append('ids',el.dataset.rowId));
        status.textContent='Сохраняю порядок…';
        try{const r=await send('/teacher/reorder/',data);if(!r.ok)throw Error();status.textContent='Порядок сохранён';}catch{status.textContent='Порядок не сохранён. Обновите страницу и попробуйте ещё раз.';}
      });
    });
  });
  document.querySelectorAll('video[data-position-url]').forEach(async video=>{
    let last=0, restored=false;
    try{const r=await fetch(video.dataset.positionUrl);const data=await r.json();const resume=()=>{if(!restored && data.seconds>0 && data.seconds<video.duration){video.currentTime=data.seconds;}restored=true;};if(video.readyState>=1)resume();else video.addEventListener('loadedmetadata',resume,{once:true});}catch{/* Player stays usable offline. */}
    const save=()=>{if(!restored)return;last=Date.now();send(video.dataset.positionUrl,new URLSearchParams({seconds:String(Math.floor(video.currentTime))})).catch(()=>{});};
    video.addEventListener('pause',save);video.addEventListener('timeupdate',()=>{if(Date.now()-last>15000)save();});
  });
})();
