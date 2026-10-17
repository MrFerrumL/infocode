/* Local, theme-aware image enlargement; original file links work without JS. */
(() => {
  'use strict';
  let dialog, image, viewport, output, baseWidth = 960, width = 960;
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };
  const resize = value => {
    width = Math.max(240, Math.min(2400, value));
    image.style.width = `${width}px`;
    output.textContent = `${Math.round(width / baseWidth * 100)}%`;
  };
  const setup = () => {
    dialog = element('dialog', 'condition-dialog');
    dialog.setAttribute('aria-label', 'Увеличенное условие');
    const toolbar = element('div', 'condition-toolbar');
    const button = (text, label, action) => {
      const node = element('button', 'button secondary', text);
      node.type = 'button'; node.setAttribute('aria-label', label);
      node.addEventListener('click', action); toolbar.append(node);
    };
    button('−', 'Уменьшить условие', () => resize(width / 1.25));
    output = element('output', 'condition-scale');
    output.setAttribute('aria-live', 'polite'); toolbar.append(output);
    button('+', 'Увеличить условие', () => resize(width * 1.25));
    button('По ширине', 'Уместить условие по ширине', () => resize(viewport.clientWidth - 24));
    button('Закрыть ×', 'Закрыть увеличенное условие', () => dialog.close());
    viewport = element('div', 'condition-viewport source-condition');
    image = element('img'); image.alt = 'Условие задания';
    viewport.append(image); dialog.append(toolbar, viewport); document.body.append(dialog);
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  };
  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-condition-zoom]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || !window.HTMLDialogElement) return;
    event.preventDefault();
    if (!dialog) setup();
    const original = link.closest('figure')?.querySelector('img');
    image.alt = original?.alt || 'Условие задания'; image.src = link.href;
    baseWidth = (original?.naturalWidth || 1920) / 2;
    resize(baseWidth); dialog.showModal(); viewport.scrollTo(0, 0);
  });
})();
