(() => {
  'use strict';
  const form = document.querySelector('[data-task-bank]');
  if (!form) return;
  const boxes = [...form.querySelectorAll('[name="selected"]')];
  const all = form.querySelector('[data-select-all]');
  const reset = form.querySelector('[data-selection-reset]');
  const status = form.querySelector('[data-selected-status]');
  const action = form.querySelector('[name="action"]');
  const difficulty = form.querySelector('[data-bulk-difficulty]');
  const submit = form.querySelector('[data-bulk-submit]');
  const update = () => {
    const count = boxes.filter(box => box.checked).length;
    status.hidden = false;
    status.textContent = `Выбрано: ${count}`;
    reset.hidden = !count;
    all.checked = !!count && count === boxes.length;
    all.indeterminate = !!count && count < boxes.length;
    all.disabled = !boxes.length;
    difficulty.hidden = action.value !== 'difficulty';
    submit.disabled = !count || !action.value;
  };
  form.querySelector('[data-select-all-label]').hidden = false;
  boxes.forEach(box => box.addEventListener('change', update));
  action.addEventListener('change', update);
  all.addEventListener('change', () => {boxes.forEach(box => {box.checked = all.checked;}); update();});
  reset.addEventListener('click', () => {boxes.forEach(box => {box.checked = false;}); update();});
  update();
})();
