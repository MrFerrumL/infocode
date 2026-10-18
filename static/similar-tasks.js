/* "Похожие" under a task card: loads similar tasks below it (they are solved in place, like the card itself). */
(() => {
  'use strict';
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-similar]');
    if (!button || !window.fetch) return;
    event.preventDefault();
    // The same task can be on the page twice (in the list and among similar ones), so no ids here.
    const box = button.closest('article').querySelector(':scope > .similar-box');
    const open = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', String(!open));
    button.textContent = open ? '≈ Похожие' : '× Скрыть похожие';
    box.hidden = open;
    if (open || box.dataset.loaded) return;
    box.textContent = 'Подбираем похожие задачи…';
    try {
      const response = await fetch(button.dataset.similar, {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok || response.redirected) throw Error();
      // Rendered by the authenticated server from escaped templates.
      box.innerHTML = await response.text();
      box.dataset.loaded = '1';
    } catch {
      box.textContent = 'Не удалось загрузить похожие задачи. Попробуйте ещё раз.';
      button.setAttribute('aria-expanded', 'false'); button.textContent = '≈ Похожие';
    }
  });
})();
