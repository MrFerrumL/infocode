/* A teacher moves a task to another level or exam source right on its card; saved at once. */
(() => {
  'use strict';
  document.addEventListener('change', async event => {
    const box = event.target.closest('[data-classify]');
    if (!box || !event.target.dataset.field) return;
    event.stopPropagation();
    const state = box.querySelector('[data-classify-state]');
    const data = new FormData();
    data.set(event.target.dataset.field, event.target.value);
    data.set('csrfmiddlewaretoken', box.dataset.csrf);
    state.textContent = 'Сохраняем…';
    try {
      const response = await fetch(box.dataset.classify, {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не сохранилось.');
      box.querySelector('[data-field=difficulty]').value = result.difficulty;
      state.textContent = '✓ Сохранено';
    } catch (error) { state.textContent = error.message || 'Нет связи.'; }
  }, true);
})();
