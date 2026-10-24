/* «КР без регистрации»: totals in the builder, quick fill and copying pupil links. */
(() => {
  'use strict';
  const builder = document.querySelector('[data-kr-builder]');
  if (builder) {
    const inputs = [...builder.querySelectorAll('.kr-number input:not([disabled])')];
    const summary = builder.querySelector('[data-summary]');
    const update = () => {
      const total = inputs.reduce((sum, input) => sum + (+input.value || 0), 0);
      inputs.forEach(input => input.closest('.kr-number').classList.toggle('has-tasks', +input.value > 0));
      const variants = +builder.querySelector('[name=variants]').value || 1;
      summary.textContent = total ? `В каждом варианте ${total} задач, всего вариантов: ${variants}.` : 'Укажите число задач хотя бы для одного номера.';
    };
    builder.addEventListener('input', update);
    builder.querySelectorAll('[data-fill]').forEach(button => button.addEventListener('click', () => {
      inputs.forEach(input => { input.value = button.dataset.fill; });
      update();
    }));
    builder.addEventListener('submit', () => {
      const button = builder.querySelector('button:not([type=button])');
      button.disabled = true;
      button.textContent = 'Собираем варианты…';
    });
    update();
  }
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = 'Скопировано';
    } catch { window.prompt('Ссылка для учеников:', button.dataset.copy); }
  }));
})();
