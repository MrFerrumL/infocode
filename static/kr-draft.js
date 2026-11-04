/* КР draft: open the catalogue picker to replace a task or add tasks to variants.
   Adding keeps the picker open with its filters and page, so several tasks can be added in a row, and one click adds a task to
   every variant ticked at the top («Вариант 1», «Вариант 2»…); the page behind is refreshed once, when the picker is closed. */
(() => {
  'use strict';
  const dialog = document.querySelector('[data-picker-dialog]');
  if (!dialog) return;
  const list = dialog.querySelector('[data-pick-list]');
  const form = dialog.querySelector('[data-pick-form]');
  const status = dialog.querySelector('[data-pick-status]');
  const number = dialog.querySelector('[data-filter-number]');
  const query = dialog.querySelector('[data-filter-q]');
  const tuple = dialog.querySelector('[data-filter-tuple]');
  const filters = {number, q: query, tuple, level: dialog.querySelector('[data-filter-level]'), source: dialog.querySelector('[data-filter-source]'),
                   year: dialog.querySelector('[data-filter-year]'), sort: dialog.querySelector('[data-filter-sort]'), mode: dialog.querySelector('[data-photo-mode]')};
  const STORE = 'kr-draft-picker';
  const targetsBox = dialog.querySelector('[data-pick-targets]');
  let target = {};
  let targets = new Set();  // the variants (0, 1, …) a task is added to
  let changed = false;
  let anchor = 0;

  // The filters are kept for the next time the picker is opened, also after the page behind has been refreshed.
  const remember = () => {
    try { sessionStorage.setItem(STORE, JSON.stringify(Object.fromEntries(Object.entries(filters).filter(([, el]) => el).map(([key, el]) => [key, el.value])))); } catch { /* private mode */ }
  };
  const restore = () => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORE) || '{}');
      Object.entries(saved).forEach(([key, value]) => { if (filters[key]) filters[key].value = value; });
    } catch { /* nothing saved */ }
  };
  const say = (text, bad = false) => { status.textContent = text; status.classList.toggle('is-bad', bad); };

  // The buttons say where a click puts the task.
  const labels = () => list.querySelectorAll('[data-pick]:not(:disabled)').forEach(b => {
    b.textContent = target.mode === 'put' ? 'Поставить вместо' : targets.size > 1 ? `+ В выбранные (${targets.size})` : '+ В вариант';
  });
  const syncTargets = () => {
    targetsBox.querySelectorAll('[data-target]').forEach(b => b.setAttribute('aria-pressed', String(targets.has(+b.dataset.target))));
    labels();
  };

  const load = async (to = 1) => {
    const params = new URLSearchParams({number: number.value, q: query.value, page: to, mode: filters.mode ? filters.mode.value : '',
                                        level: filters.level.value, source: filters.source.value, year: filters.year.value, tuple: tuple.value,
                                        sort: filters.sort.value, draft: 1});
    remember();
    list.innerHTML = '<p class="muted">Загружаем задачи…</p>';
    try {
      const response = await fetch(`${dialog.dataset.url}&${params}`, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
      list.innerHTML = response.ok ? await response.text() : '<p class="muted">Не удалось загрузить задачи.</p>';
      labels();
    } catch { list.innerHTML = '<p class="muted">Нет связи с сервером.</p>'; }
  };

  document.querySelectorAll('[data-open-picker]').forEach(button => button.addEventListener('click', () => {
    target = {mode: button.dataset.mode, v: button.dataset.v, i: button.dataset.i || ''};
    const select = button.closest('form').querySelector('select[name=number]');
    restore();
    // Replacing a task: its own number. Adding: the last number looked at, or the one chosen next to the button.
    if (button.dataset.number) { number.value = button.dataset.number; query.value = ''; }
    else if (!number.value) number.value = select ? select.value : '';
    if (number.value !== '17') tuple.value = '';
    say('');
    targets = new Set([+target.v]);
    targetsBox.hidden = target.mode === 'put' || targetsBox.querySelectorAll('[data-target]').length < 2;  // one variant: nothing to choose
    syncTargets();
    dialog.querySelector('[data-picker-title]').textContent = target.mode === 'put'
      ? `Заменить задачу ${+target.i + 1} в варианте ${+target.v + 1}` : 'Добавить задачи в варианты';
    dialog.showModal();
    load();
  }));
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  targetsBox.addEventListener('click', event => {
    const chip = event.target.closest('[data-target]');
    if (!chip) return;
    const at = +chip.dataset.target;
    if (!targets.has(at)) targets.add(at);
    else if (targets.size > 1) targets.delete(at);  // at least one variant stays ticked
    syncTargets();
  });
  // The page behind shows the added tasks only after the picker is closed (Esc included).
  dialog.addEventListener('close', () => {
    if (!changed) return;
    changed = false;
    location.hash = `#v${anchor + 1}`;
    location.reload();
  });
  // Pairs, triples and quadruples exist only in № 17: choosing one shows that number, leaving № 17 drops the choice.
  // These listeners come first so that the search below already sees the corrected filters.
  tuple.addEventListener('change', () => { if (tuple.value) number.value = '17'; });
  number.addEventListener('change', () => { if (number.value !== '17') tuple.value = ''; });
  dialog.querySelectorAll('[data-filter-number], [data-filter-level], [data-filter-source], [data-filter-year], [data-filter-tuple], [data-filter-sort]').forEach(el => el.addEventListener('change', () => load()));
  dialog.querySelector('[data-filter-go]').addEventListener('click', () => load());
  query.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); load(); } });

  // Adds the task to every ticked variant, one after another; a variant that has it already is named, the others still get it.
  const add = async pick => {
    pick.disabled = true;
    const done = [], refused = [];
    let counts = '';
    for (const at of [...targets].sort((a, b) => a - b)) {
      const body = new FormData(form);
      body.set('action', 'add');
      body.set('v', at);
      body.set('i', '');
      body.set('task', pick.dataset.pick);
      try {
        // Not form.action: the form has an input named «action», which hides that property.
        const response = await fetch(location.pathname, {method: 'POST', body, credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}});
        const result = await response.json();
        if (result.ok) { done.push(result.variant); counts = `${counts}${counts ? ', ' : ''}в варианте ${result.variant} задач: ${result.count}`; }
        else refused.push(`вариант ${at + 1}: ${result.error || 'не удалось добавить'}`);
      } catch {
        refused.push(`вариант ${at + 1}: нет связи с сервером`);
      }
    }
    if (!done.length) { pick.disabled = false; say(refused.join('; ') || 'Не удалось добавить задачу.', true); return; }
    changed = true;
    anchor = done[0] - 1;
    pick.textContent = '✓ Добавлена';
    const card = pick.closest('.kr-pick-card');
    let note = card.querySelector('.kr-in-draft');
    if (!note) { note = document.createElement('small'); note.className = 'kr-in-draft'; card.querySelector('header > div').append(note); }
    note.textContent = `✓ добавлена в ${done.length > 1 ? 'варианты' : 'вариант'} ${done.join(', ')}`;
    say(`Добавлено: ${counts}.${refused.length ? ' Не добавлено: ' + refused.join('; ') + '.' : ' Можно выбирать дальше.'}`, refused.length > 0);
  };

  list.addEventListener('click', event => {
    const to = event.target.closest('[data-page]');
    if (to) { load(to.dataset.page); return; }
    const more = event.target.closest('[data-more]');
    if (more) { more.closest('.kr-pick-card').classList.toggle('is-open'); return; }
    const pick = event.target.closest('[data-pick]');
    if (!pick) return;
    if (target.mode !== 'put') { add(pick); return; }
    form.elements.action.value = target.mode;  // a replacement is one choice: the page reloads with the new task in place
    form.elements.v.value = target.v;
    form.elements.i.value = target.i;
    form.elements.task.value = pick.dataset.pick;
    form.submit();
  });
  document.querySelectorAll('.kr-draft-task [data-more]').forEach(more => more.addEventListener('click', () => {
    more.closest('.kr-pick-card').classList.toggle('is-open');
  }));
})();
