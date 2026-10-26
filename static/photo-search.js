/* Search by photo: a chosen or pasted (Ctrl+V) picture goes to the server, its text comes back into the search
   field, the search switches to «по смыслу» and runs. Used by the catalogue and the КР picker. */
(() => {
  'use strict';
  const boxes = [...document.querySelectorAll('[data-photo-search]')];
  if (!boxes.length) return;

  const scopeOf = box => box.closest('form, [data-picker], dialog') || document;
  const send = async (box, file) => {
    const scope = scopeOf(box);
    const status = box.querySelector('[data-photo-status]');
    const token = box.querySelector('[name=csrfmiddlewaretoken]') || document.querySelector('[name=csrfmiddlewaretoken]');
    const data = new FormData();
    data.append('photo', file, file.name || 'photo.png');
    status.textContent = 'Распознаём текст…';
    status.classList.remove('error');
    try {
      const response = await fetch(box.dataset.url, {method: 'POST', body: data,
        headers: {'X-CSRFToken': token ? token.value : '', 'X-Requested-With': 'XMLHttpRequest'}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось распознать текст.');
      const field = scope.querySelector('[data-photo-target]');
      field.value = result.text;
      const mode = scope.querySelector('[data-photo-mode]');
      if (mode) mode.value = 'meaning';
      status.textContent = 'Текст распознан — ищем похожие. Его можно поправить и поискать снова.';
      if (scope.tagName === 'FORM') scope.requestSubmit();
      else (scope.querySelector('[data-filter-go]') || {click() {}}).click();
    } catch (error) {
      status.textContent = error.message || 'Нет связи с сервером.';
      status.classList.add('error');
    }
  };

  boxes.forEach(box => {
    const input = box.querySelector('[data-photo-file]');
    box.querySelector('[data-photo-pick]').addEventListener('click', () => input.click());
    input.addEventListener('change', () => { if (input.files[0]) send(box, input.files[0]); input.value = ''; });
  });
  // A picture pasted anywhere on the page (or in the open picker) searches by photo.
  document.addEventListener('paste', event => {
    const item = [...(event.clipboardData || {items: []}).items].find(i => i.type.startsWith('image/'));
    if (!item) return;
    const open = document.querySelector('dialog[open] [data-photo-search]');
    const box = open || boxes.find(b => b.offsetParent !== null);
    if (!box) return;
    event.preventDefault();
    send(box, item.getAsFile());
  });
})();
