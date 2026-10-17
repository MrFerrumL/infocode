/* Same-page practice; ordinary POST remains available when JavaScript is disabled. */
(() => {
  'use strict';
  document.addEventListener('submit', async event => {
    const form = event.target.closest('form[data-catalog-answer]');
    if (!form || !window.fetch) return;
    event.preventDefault();
    if (form.dataset.sending || !form.reportValidity()) return;
    // A 19–21 card holds several answer forms; each question has its own scope.
    const card = form.closest('[data-catalog-scope]') || form.closest('.catalog-condition');
    const status = form.querySelector('.catalog-form-status');
    const button = form.querySelector('button[type="submit"]');
    const data = new FormData(form);
    form.dataset.sending = '1'; button.disabled = true; status.textContent = 'Проверяем…';
    try {
      const response = await fetch(form.action, {method: 'POST', body: data, credentials: 'same-origin',
        headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}});
      if (response.redirected || !response.ok || !(response.headers.get('Content-Type') || '').includes('application/json')) {
        throw Error('Не удалось проверить ответ. Обновите страницу и войдите в аккаунт, если сессия закончилась.');
      }
      const result = await response.json();
      // Only the authenticated, CSRF-protected server renders this escaped HTML fragment.
      card.querySelector('.catalog-feedback').innerHTML = result.feedback;
      form.elements.submit_token.value = result.submit_token;
      status.textContent = Object.values(result.errors).flat().join(' ');
      const meta = card.querySelector('.task-meta');
      let solved = meta.querySelector('[data-catalog-solved]');
      if (result.solved && !solved) {
        solved = document.createElement('span'); solved.dataset.catalogSolved = '';
        solved.className = 'success-text'; solved.textContent = '✓ Решено';
        meta.append(solved);
      }
      if (!Object.keys(result.errors).length) status.textContent = 'Ответ сохранён.';
    } catch (error) {
      status.textContent = error.message || 'Нет связи с сервером. Попробуйте проверить ответ ещё раз.';
    } finally { delete form.dataset.sending; button.disabled = false; }
  });
})();
