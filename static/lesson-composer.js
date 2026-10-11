/* Lesson structure and assessment selection; content blocks keep their own editor. */
(() => {
  'use strict';
  const planForm = document.querySelector('[data-lesson-composer]');
  if (planForm) {
    const specs = JSON.parse(document.querySelector('#slide-types-data').textContent);
    const types = new Map(specs.map(spec => [spec.key, spec]));
    const input = planForm.elements.slide_plan;
    const root = planForm.querySelector('[data-slide-plan]');
    const status = planForm.querySelector('[data-plan-status]');
    let slides = [], dragged = null, dirty = false;
    const button = (label, text, action) => {
      const el = document.createElement('button');
      el.type = 'button'; el.textContent = text; el.setAttribute('aria-label', label);
      el.addEventListener('click', action); return el;
    };
    function sync() {
      input.value = JSON.stringify(slides); dirty = true;
      planForm.dispatchEvent(new Event('input', {bubbles: true}));
    }
    function move(from, to) {
      if (to < 0 || to >= slides.length || from === to) return;
      slides.splice(to, 0, slides.splice(from, 1)[0]); sync(); render();
      root.children[to]?.querySelector('input')?.focus();
      status.textContent = `Слайд перемещён на позицию ${to + 1}.`;
    }
    function render() {
      root.replaceChildren();
      planForm.querySelector('[data-plan-empty]').hidden = slides.length > 0;
      planForm.querySelector('[data-plan-count]').textContent = `${slides.length} / 60 слайдов`;
      slides.forEach((slide, i) => {
        const row = document.createElement('li'); row.className = 'slide-plan-row';
        const handle = document.createElement('span'); handle.textContent = '⠿';
        handle.className = 'drag-handle'; handle.draggable = true; handle.title = 'Перетащить слайд';
        handle.addEventListener('dragstart', event => {
          dragged = i; event.dataTransfer.effectAllowed = 'move';
          event.dataTransfer.setData('text/plain', String(i)); row.classList.add('dragging');
        });
        handle.addEventListener('dragend', () => {dragged = null; row.classList.remove('dragging');});
        row.addEventListener('dragover', event => {if (dragged !== null) event.preventDefault();});
        row.addEventListener('drop', event => {
          if (dragged === null) return;
          event.preventDefault(); const from = dragged; dragged = null; move(from, i);
        });
        const number = document.createElement('span'); number.className = 'step-type'; number.textContent = i + 1;
        const select = document.createElement('select'); select.setAttribute('aria-label', `Тип слайда ${i + 1}`);
        specs.forEach(spec => {const opt = document.createElement('option'); opt.value = spec.key; opt.textContent = spec.label; select.append(opt);});
        select.value = slide.type;
        select.addEventListener('change', () => {slide.type = select.value; sync();});
        const title = document.createElement('input'); title.type = 'text'; title.maxLength = 180;
        title.value = slide.title; title.placeholder = 'Название (необязательно)';
        title.setAttribute('aria-label', `Название слайда ${i + 1}`);
        title.addEventListener('input', () => {slide.title = title.value; sync();});
        const controls = document.createElement('div'); controls.className = 'plan-row-actions';
        const up = button('Слайд вверх', '↑', () => move(i, i - 1)); up.disabled = i === 0;
        const down = button('Слайд вниз', '↓', () => move(i, i + 1)); down.disabled = i === slides.length - 1;
        const copy = button('Повторить слайд', 'Копия', () => {
          if (slides.length >= 60) {status.textContent = 'Можно создать до 60 слайдов за один раз. Остальные добавьте после сохранения урока.'; return;}
          slides.splice(i + 1, 0, {...slide}); sync(); render();
        });
        const remove = button('Убрать слайд из плана', '×', () => {slides.splice(i, 1); sync(); render();});
        controls.append(up, down, copy, remove); row.append(handle, number, select, title, controls); root.append(row);
      });
    }
    function restore() {
      try {
        const parsed = JSON.parse(input.value || '[]');
        if (!Array.isArray(parsed) || parsed.length > 60 || parsed.some(row => !row || !types.has(row.type) || typeof row.title !== 'string' || row.title.length > 180)) throw Error();
        slides = parsed.map(row => ({type: row.type, title: row.title})); status.textContent = '';
      } catch {
        slides = []; status.textContent = 'Список слайдов не прочитан. Выберите слайды заново. Название урока сохранено в форме.';
      }
      render();
    }
    planForm.querySelectorAll('[data-add-slide]').forEach(el => el.addEventListener('click', () => {
      if (slides.length >= 60) {status.textContent = 'До 60 слайдов за один раз. Сохраните урок, чтобы добавить ещё.'; return;}
      slides.push({type: el.dataset.addSlide, title: ''}); sync(); render();
      status.textContent = `Добавлен слайд ${slides.length}: ${types.get(el.dataset.addSlide).label}.`;
    }));
    planForm.addEventListener('draft:restored', () => {restore(); dirty = true;});
    planForm.addEventListener('input', () => {dirty = true;});
    planForm.addEventListener('submit', () => {dirty = false;});
    window.addEventListener('beforeunload', event => {if (dirty) {event.preventDefault(); event.returnValue = '';}});
    restore();
  }

  const form = document.querySelector('[data-slide-editor]');
  if (!form) return;
  const kind = form.elements.kind;
  const assessed = value => ['quiz', 'answer', 'numeric', 'gaps', 'matching'].includes(value);
  let previous = kind.value;
  function refresh(defaultPoints = false) {
    form.querySelectorAll('[data-assessment-panel]').forEach(panel => {
      const active = panel.dataset.assessmentPanel.split(' ').includes(kind.value);
      panel.hidden = !active;
      panel.querySelectorAll('input,select,textarea,button').forEach(el => {el.disabled = !active;});
    });
    if (defaultPoints && !assessed(previous) && assessed(kind.value) && form.elements.points.value === '0') form.elements.points.value = '1';
    previous = kind.value;
    form.querySelectorAll('[data-set-assessment]').forEach(el => {
      el.setAttribute('aria-pressed', String(el.dataset.setAssessment === kind.value || (el.dataset.setAssessment === 'text' && !assessed(kind.value))));
    });
    form.querySelector('[data-assessment-summary]').textContent = assessed(kind.value)
      ? 'Материал и задание появятся на одной странице. Настройте правильные ответы и баллы ниже.'
      : 'Свободный материал без проверки ответа. Можно добавить текст, фото, видео и файлы в любом порядке.';
  }
  kind.addEventListener('change', () => {refresh(true); form.dispatchEvent(new Event('input', {bubbles: true}));});
  form.querySelectorAll('[data-set-assessment]').forEach(el => el.addEventListener('click', () => {
    kind.value = el.dataset.setAssessment; kind.dispatchEvent(new Event('change', {bubbles: true}));
  }));
  form.addEventListener('draft:restored', () => refresh());
  refresh();
})();
