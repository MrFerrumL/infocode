document.querySelectorAll('[data-copy-target]').forEach(button => {
  button.addEventListener('click', async () => {
    const field = document.getElementById(button.dataset.copyTarget);
    const status = document.querySelector('[data-copy-status]');
    if (!field) return;
    try {
      await navigator.clipboard.writeText(field.value);
      if (status) status.textContent = 'Скопировано. Передайте карточку лично ученику.';
    } catch (_) {
      field.focus(); field.select();
      if (status) status.textContent = 'Текст выделен. Нажмите Ctrl+C или выберите «Копировать».';
    }
  });
});
