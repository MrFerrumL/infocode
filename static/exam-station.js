/* KEGE-style exam station: one task on screen, numbered navigation, answer grid saved per task.
   Without JavaScript the same form shows every task and saves them all at once. */
(() => {
  'use strict';
  const station = document.querySelector('[data-station]');
  if (!station) return;
  const form = station.querySelector('[data-station-form]');
  const tasks = [...station.querySelectorAll('[data-task]')];
  const links = [...station.querySelectorAll('[data-goto]')];
  const counter = station.querySelector('[data-answered]');
  const timer = station.querySelector('[data-timer]');
  const deadline = new Date(station.dataset.deadline);
  let current = 0;
  let submitting = false;
  station.classList.add('js');

  const filled = task => [...task.querySelectorAll('.answer-grid input')].some(input => input.value.trim());
  const refreshCount = () => { counter.textContent = tasks.filter(task => task.dataset.saved === 'yes').length; };
  tasks.forEach(task => { task.dataset.saved = filled(task) ? 'yes' : 'empty'; });
  refreshCount();

  const show = index => {
    current = Math.max(0, Math.min(tasks.length - 1, index));
    tasks.forEach((task, i) => { task.hidden = i !== current; });
    links.forEach((link, i) => link.classList.toggle('current', i === current));
    const input = tasks[current].querySelector('.answer-grid input');
    history.replaceState(null, '', '#' + tasks[current].id);
    if (input) input.focus({preventScroll: true});
    tasks[current].scrollIntoView({block: 'nearest'});
  };

  const save = async task => {
    const status = task.querySelector('[data-status]');
    const data = new FormData();
    data.append('csrfmiddlewaretoken', form.elements.csrfmiddlewaretoken.value);
    data.append('item', task.dataset.item);
    task.querySelectorAll('.answer-grid input').forEach(input => data.append(input.name, input.value));
    status.textContent = 'Сохраняем…';
    try {
      const response = await fetch(station.dataset.saveUrl, {method: 'POST', body: data, credentials: 'same-origin',
        headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw Error(result.error || 'Не удалось сохранить ответ. Проверьте связь и попробуйте ещё раз.');
      task.dataset.dirty = '0';
      task.dataset.saved = result.value ? 'yes' : 'empty';
      links[tasks.indexOf(task)].classList.toggle('answered', Boolean(result.value));
      counter.textContent = result.answered;
      status.textContent = result.value ? 'Ответ сохранён.' : 'Ответ очищен.';
    } catch (error) {
      status.textContent = error.message;
    }
  };

  const leave = () => { const task = tasks[current]; if (task.dataset.dirty === '1') save(task); };
  links.forEach((link, i) => link.addEventListener('click', event => { event.preventDefault(); leave(); show(i); }));
  station.addEventListener('click', event => {
    const task = event.target.closest('[data-task]');
    if (!task) return;
    if (event.target.closest('[data-save]')) save(task);
    if (event.target.closest('[data-clear]')) { task.querySelectorAll('.answer-grid input').forEach(input => { input.value = ''; }); save(task); }
    if (event.target.closest('[data-prev]')) { leave(); show(current - 1); }
    if (event.target.closest('[data-next]')) { leave(); show(current + 1); }
  });
  station.addEventListener('input', event => {
    const task = event.target.closest('[data-task]');
    if (task && event.target.closest('.answer-grid')) {
      task.dataset.dirty = '1';
      task.querySelector('[data-status]').textContent = 'Не сохранено — нажмите «Сохранить ответ».';
    }
  });
  station.addEventListener('keydown', event => {
    const task = event.target.closest('[data-task]');
    if (task && event.key === 'Enter' && event.target.closest('.answer-grid')) { event.preventDefault(); save(task); }
  });
  // Pasting "a b" or several lines fills the grid like the station does.
  station.addEventListener('paste', event => {
    const input = event.target.closest('.answer-grid input');
    const text = (event.clipboardData || window.clipboardData).getData('text');
    if (!input || !/[\s\t]/.test(text.trim())) return;
    event.preventDefault();
    const grid = [...input.closest('tbody').rows].map(row => [...row.querySelectorAll('input')]);
    const start = grid.findIndex(row => row.includes(input));
    text.trim().split(/\r?\n/).forEach((line, r) => line.trim().split(/[\s\t;]+/).forEach((value, c) => {
      const cell = (grid[start + r] || [])[grid[start].indexOf(input) + c];
      if (cell) cell.value = value;
    }));
    input.dispatchEvent(new Event('input', {bubbles: true}));
  });

  form.addEventListener('submit', event => {
    if (submitting) return;
    const finishing = event.submitter && event.submitter.matches('[data-finish]');
    if (finishing && !confirm('Завершить экзамен? Будут проверены все ответы, введённые в поля.')) event.preventDefault();
  });

  const tick = () => {
    const left = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
    const h = Math.floor(left / 3600), m = Math.floor(left % 3600 / 60), s = left % 60;
    timer.textContent = `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    station.classList.toggle('time-low', left < 600);
    if (!left && !submitting) {
      submitting = true;
      const action = document.createElement('input');
      action.type = 'hidden'; action.name = 'action'; action.value = 'submit';
      form.append(action);
      form.submit();
    }
  };
  if (!Number.isNaN(deadline.getTime())) { tick(); setInterval(tick, 1000); }

  const start = tasks.findIndex(task => '#' + task.id === location.hash);
  show(start >= 0 ? start : 0);
})();
