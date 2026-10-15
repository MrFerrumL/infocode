(() => {
  'use strict';
  const root = document.documentElement;
  root.classList.add('js-ready');
  const theme = document.querySelector('[data-theme-toggle]');
  const preferred = window.matchMedia('(prefers-color-scheme: dark)');
  const dark = () => root.dataset.theme === 'dark' || (root.dataset.theme !== 'light' && preferred.matches);
  const updateTheme = () => {
    if (!theme) return;
    theme.textContent = dark() ? '☀ Светлая' : '☾ Тёмная';
    theme.setAttribute('aria-label', dark() ? 'Включить светлую тему' : 'Включить тёмную тему');
  };
  if (theme) {
    theme.hidden = false;
    theme.addEventListener('click', () => {
      root.dataset.theme = dark() ? 'light' : 'dark';
      try { localStorage.setItem('infocode-theme', root.dataset.theme); } catch (_) { /* Theme still works without storage. */ }
      updateTheme();
    });
    preferred.addEventListener('change', updateTheme);
    updateTheme();
  }
  const menu = document.querySelector('[data-menu-toggle]');
  const sidebar = document.querySelector('#workspace-sidebar');
  const shade = document.querySelector('[data-close-menu]');
  const shell = document.querySelector('.main-shell');
  const mobile = window.matchMedia('(max-width:760px)');
  const setMenu = (open, restoreFocus = true) => {
    if (!menu || !sidebar) return;
    document.body.classList.toggle('menu-open', open);
    menu.setAttribute('aria-expanded', String(open));
    shade.hidden = !open;
    shell.inert = open;
    if (open) sidebar.querySelector('a').focus();
    else if (restoreFocus) menu.focus();
  };
  if (menu && sidebar) {
    menu.hidden = false;
    menu.addEventListener('click', () => setMenu(!document.body.classList.contains('menu-open')));
    shade.addEventListener('click', () => setMenu(false));
    mobile.addEventListener('change', () => setMenu(false, false));
    document.addEventListener('keydown', event => {
      if (!document.body.classList.contains('menu-open')) return;
      if (event.key === 'Escape') { event.preventDefault(); setMenu(false); }
      if (event.key === 'Tab') {
        const items = [...sidebar.querySelectorAll('a,button,summary')].filter(el => el.getClientRects().length);
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
  }
  // Server validation errors must never disappear inside collapsed settings.
  document.querySelectorAll('details').forEach(details => {
    if (details.querySelector('.errorlist')) details.open = true;
  });
  document.addEventListener('invalid', event => {
    for (let node = event.target.parentElement; node; node = node.parentElement) {
      if (node.tagName === 'DETAILS') node.open = true;
    }
  }, true);

  const form = document.querySelector('[data-work-wizard]');
  if (!form) return;
  const stages = [...form.querySelectorAll('[data-wizard-stage]')];
  const buttons = [...form.querySelectorAll('[data-wizard-go]')];
  const next = form.querySelector('[data-wizard-next]');
  const back = form.querySelector('[data-wizard-back]');
  const target = form.elements.target;
  const kind = form.elements.kind;
  const attemptLimit = form.elements.max_attempts;
  let attemptLimitEdited = false;
  attemptLimit.addEventListener('input', () => { attemptLimitEdited = true; });
  const mode = form.querySelector('#work-source-mode');
  let current = 0;
  form.noValidate = true;
  form.classList.add('wizard-ready');
  form.querySelectorAll('[data-wizard-progress],[data-wizard-controls],[data-source-picker],[data-recipient-search]').forEach(el => { el.hidden = false; });
  mode.value = form.elements.source.value ? 'source' : form.elements.task_ids.value.trim() ? 'ids' : 'generate';
  const sourceError = form.querySelector('[data-source-panel] .errorlist')?.closest('[data-source-panel]');
  if (sourceError) mode.value = sourceError.dataset.sourcePanel;
  const enablePanel = (panel, enabled) => {
    panel.hidden = !enabled;
    panel.querySelectorAll('input,select,textarea').forEach(el => { el.disabled = !enabled; });
  };
  const updatePanels = () => {
    form.querySelectorAll('[data-work-target]').forEach(panel => enablePanel(panel, panel.dataset.workTarget === target.value));
    form.querySelectorAll('[data-source-panel]').forEach(panel => enablePanel(panel, target.value === 'variant' && panel.dataset.sourcePanel === mode.value));
    attemptLimit.min = kind.value === 'homework' ? '0' : '1';
    const offOption = form.elements.code_policy.querySelector('option[value="off"]');
    offOption.disabled = kind.value === 'homework';
    offOption.hidden = kind.value === 'homework';
    if (offOption.disabled && form.elements.code_policy.value === 'off') form.elements.code_policy.value = 'optional';
  };
  target.addEventListener('change', updatePanels);
  mode.addEventListener('change', updatePanels);
  kind.addEventListener('change', () => {
    if (kind.value === 'required_test') target.value = 'variant';
    if (!attemptLimitEdited) attemptLimit.value = kind.value === 'homework' ? '0' : '1';
    updatePanels();
  });
  updatePanels();
  const selection = el => el.options[el.selectedIndex]?.textContent || '';
  const updateSummary = () => {
    const box = form.querySelector('[data-wizard-summary]');
    box.hidden = false;
    box.replaceChildren();
    const checked = form.querySelectorAll('[name=students]:checked').length;
    const limitLabel = kind.value === 'homework' && attemptLimit.value === '0' ? 'без ограничения' : attemptLimit.value;
    const lines = [form.elements.title.value || 'Без названия', `${selection(kind)} · ${selection(target)}`,
      `Получатели: ${checked} выбрано отдельно${form.elements.group.value ? ' + группа «' + selection(form.elements.group) + '»' : ''}.`,
      `Сдать до: ${form.elements.due_at.value.replace('T', ' ')}. Попытки: ${limitLabel}. Для зачёта: ${form.elements.pass_percent.value}%.`];
    if (target.value === 'variant') lines.push(`Python: ${selection(form.elements.code_policy)}.`);
    for (const line of lines) { const p = document.createElement('p'); p.textContent = line; box.append(p); }
  };
  const show = (index, focus = true) => {
    current = index;
    stages.forEach((stage, i) => { stage.hidden = i !== index; });
    buttons.forEach((button, i) => { if (i === index) button.setAttribute('aria-current','step'); else button.removeAttribute('aria-current'); });
    back.hidden = index === 0;
    next.hidden = index === stages.length - 1;
    form.querySelector('[data-wizard-position]').textContent = `Шаг ${index + 1} из ${stages.length}`;
    if (index === stages.length - 1) updateSummary();
    if (focus) stages[index].querySelector('h2').focus();
  };
  const validate = index => {
    for (const input of stages[index].querySelectorAll('input,select,textarea')) {
      if (!input.disabled && !input.checkValidity()) { show(index, false); input.reportValidity(); return false; }
    }
    return true;
  };
  next.addEventListener('click', () => { if (validate(current)) show(current + 1); });
  back.addEventListener('click', () => show(current - 1));
  buttons.forEach((button, i) => button.addEventListener('click', () => {
    for (let j = 0; j < i; j++) if (!validate(j)) return;
    show(i);
  }));
  form.addEventListener('submit', event => {
    if (current < stages.length - 1) { event.preventDefault(); if (validate(current)) show(current + 1); return; }
    for (let i = 0; i < stages.length; i++) if (!validate(i)) { event.preventDefault(); return; }
  });
  const search = form.querySelector('#recipient-search');
  const countRecipients = () => { form.querySelector('[data-recipient-count]').textContent = `Выбрано отдельно: ${form.querySelectorAll('[name=students]:checked').length}. Ученики группы добавятся при назначении.`; };
  search.addEventListener('input', () => {
    const query = search.value.toLocaleLowerCase('ru').trim();
    form.querySelectorAll('#id_students > div').forEach(row => { row.hidden = !row.textContent.toLocaleLowerCase('ru').includes(query); });
  });
  form.querySelectorAll('[name=students]').forEach(input => input.addEventListener('change', countRecipients));
  countRecipients();
  form.addEventListener('change', updateSummary);
  const invalid = stages.findIndex(stage => stage.querySelector('.errorlist'));
  show(invalid >= 0 ? invalid : 0, false);
})();
