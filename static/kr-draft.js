/* КР draft: open the catalogue picker to replace a task or add one to a variant. */
(() => {
  'use strict';
  const dialog = document.querySelector('[data-picker-dialog]');
  if (!dialog) return;
  const list = dialog.querySelector('[data-pick-list]');
  const form = dialog.querySelector('[data-pick-form]');
  const number = dialog.querySelector('[data-filter-number]');
  const query = dialog.querySelector('[data-filter-q]');
  let target = {};

  const load = async (page = 1) => {
    const params = new URLSearchParams({number: number.value, q: query.value, page});
    list.innerHTML = '<p class="muted">Загружаем задачи…</p>';
    try {
      const response = await fetch(`${dialog.dataset.url}&${params}`, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
      list.innerHTML = response.ok ? await response.text() : '<p class="muted">Не удалось загрузить задачи.</p>';
      list.querySelectorAll('[data-pick]').forEach(b => { b.textContent = target.mode === 'put' ? 'Поставить вместо' : '+ В вариант'; });
    } catch { list.innerHTML = '<p class="muted">Нет связи с сервером.</p>'; }
  };

  document.querySelectorAll('[data-open-picker]').forEach(button => button.addEventListener('click', () => {
    target = {mode: button.dataset.mode, v: button.dataset.v, i: button.dataset.i || ''};
    const select = button.closest('form').querySelector('select[name=number]');
    number.value = button.dataset.number || (select ? select.value : '');
    query.value = '';
    dialog.querySelector('[data-picker-title]').textContent = target.mode === 'put'
      ? `Заменить задачу ${+target.i + 1} в варианте ${+target.v + 1}` : `Добавить задачу в вариант ${+target.v + 1}`;
    dialog.showModal();
    load();
  }));
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  number.addEventListener('change', () => load());
  dialog.querySelector('[data-filter-go]').addEventListener('click', () => load());
  query.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); load(); } });
  list.addEventListener('click', event => {
    const page = event.target.closest('[data-page]');
    if (page) { load(page.dataset.page); return; }
    const more = event.target.closest('[data-more]');
    if (more) { more.closest('.kr-pick-card').classList.toggle('is-open'); return; }
    const pick = event.target.closest('[data-pick]');
    if (!pick) return;
    form.elements.action.value = target.mode;
    form.elements.v.value = target.v;
    form.elements.i.value = target.i;
    form.elements.task.value = pick.dataset.pick;
    form.submit();
  });
  document.querySelectorAll('.kr-draft-task [data-more]').forEach(more => more.addEventListener('click', () => {
    more.closest('.kr-pick-card').classList.toggle('is-open');
  }));
})();
