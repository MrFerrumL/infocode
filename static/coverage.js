/* «Пройденные номера»: a click on a cell applies the chosen tool without reloading the page. Plain POST works without JS. */
(() => {
  'use strict';
  const form = document.querySelector('[data-coverage-form]');
  if (!form || !window.fetch) return;
  form.addEventListener('click', async event => {
    const button = event.target.closest('button[name="cell"]');
    if (!button) return;
    event.preventDefault();
    if (button.dataset.sending) return;
    const data = new FormData(form);
    data.set('cell', button.value);
    button.dataset.sending = '1';
    try {
      const response = await fetch(form.action, {method: 'POST', body: data, credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok) throw Error();
      const result = await response.json();
      const cell = button.closest('td');
      cell.classList.remove('yellow', 'red');
      if (result.color) cell.classList.add(result.color);
      button.querySelector('.cov-box').classList.toggle('is-done', result.done);
      const label = button.getAttribute('aria-label').replace(/: .*$/, '');
      button.setAttribute('aria-label', `${label}: ${result.done ? 'пройдено' : 'не пройдено'}`);
      const total = cell.closest('tr').querySelector('[data-row-done]');
      total.textContent = total.textContent.replace(/^\d+/, result.row_done);
    } catch {
      button.closest('td').classList.add('cov-error');
      setTimeout(() => button.closest('td').classList.remove('cov-error'), 1500);
    } finally { delete button.dataset.sending; }
  });
})();
