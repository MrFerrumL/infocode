/* Local editing and display only. Notebook code is never evaluated. */
(() => {
  'use strict';
  const source = row => row.querySelector('[name$="-text"]');
  async function copyText(text, button) {
    const original = button.textContent;
    try {await navigator.clipboard.writeText(text); button.textContent = 'Скопировано';}
    catch {button.textContent = 'Выделите код и нажмите Ctrl+C';}
    setTimeout(() => {button.textContent = original;}, 2300);
  }
  function codeInput(row) {
    const input = source(row);
    if (!input || input.dataset.notebookCodeReady) return;
    input.dataset.notebookCodeReady = '1';
    input.spellcheck = false;
    input.setAttribute('aria-label', 'Python-код ячейки');
    const wrap = document.createElement('div'); wrap.className = 'notebook-code-input';
    const gutter = document.createElement('pre'); gutter.className = 'notebook-line-numbers'; gutter.setAttribute('aria-hidden', 'true');
    input.before(wrap); wrap.append(gutter, input);
    const toolbar = document.createElement('div'); toolbar.className = 'notebook-code-tools';
    const copy = document.createElement('button'); copy.type = 'button'; copy.textContent = 'Копировать';
    copy.onclick = () => copyText(input.value, copy);
    const pick = document.createElement('button'); pick.type = 'button'; pick.textContent = 'Вставить из .py';
    const file = document.createElement('input'); file.type = 'file'; file.accept = '.py'; file.hidden = true;
    const help = document.createElement('span'); help.textContent = 'Python · Tab — отступ';
    const status = document.createElement('small'); status.className = 'notebook-code-status'; status.setAttribute('role', 'status');
    toolbar.append(help, copy, pick, file); wrap.before(toolbar); wrap.after(status);
    function count() {const lines = Math.min(5000, input.value.split('\n').length); gutter.textContent = Array.from({length:lines}, (_, i) => i+1).join('\n');}
    const change = () => {input.dispatchEvent(new Event('input', {bubbles:true}));};
    pick.onclick = () => file.click();
    file.onchange = async () => {
      const chosen = file.files?.[0]; if (!chosen) return;
      try {
        if (!chosen.name.toLowerCase().endsWith('.py') || chosen.size > 400000) throw Error('Выберите .py в UTF-8, до 100 000 символов.');
        const text = new TextDecoder('utf-8', {fatal:true}).decode(await chosen.arrayBuffer());
        if (text.length > 100000 || text.includes('\u0000')) throw Error('В коде слишком много символов или есть недопустимый нулевой символ.');
        input.value = text; change(); status.textContent = 'Код вставлен. Сохраните конспект.';
      } catch (error) {status.textContent = error instanceof TypeError ? 'Сохраните .py-файл в UTF-8 и попробуйте ещё раз.' : error.message;}
      file.value = '';
    };
    function replace(value, start=input.selectionStart, end=input.selectionEnd) {
      if (input.value.length - (end-start) + value.length > 100000) {status.textContent = 'Лимит: 100 000 символов.'; return;}
      input.setRangeText(value, start, end, 'end'); change();
    }
    input.addEventListener('keydown', event => {
      if (row.dataset.kind !== 'code' || event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === 'Tab' && !event.shiftKey) {
        event.preventDefault();
        const selected = input.value.slice(input.selectionStart, input.selectionEnd);
        if (selected.includes('\n')) {const start = input.value.lastIndexOf('\n', input.selectionStart-1)+1; replace(input.value.slice(start, input.selectionEnd).split('\n').map(line => '    '+line).join('\n'), start);}
        else replace('    ');
      } else if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        const start = input.value.lastIndexOf('\n', input.selectionStart-1)+1;
        const line = input.value.slice(start, input.selectionStart);
        replace('\n' + line.match(/^[ \t]*/)[0] + (line.trimEnd().endsWith(':') ? '    ' : ''));
      }
    });
    input.addEventListener('input', count);
    input.addEventListener('scroll', () => {gutter.scrollTop = input.scrollTop;});
    new ResizeObserver(() => {gutter.style.maxHeight = input.clientHeight + 'px';}).observe(input);
    count();
  }
  const form = document.querySelector('[data-notebook-editor]');
  if (form) {
    const root = form.querySelector('[data-content-blocks]');
    const outline = document.querySelector('[data-notebook-outline]');
    let pending = false;
    const update = () => {
      pending = false;
      const fragment = document.createDocumentFragment();
      [...root.querySelectorAll(':scope > [data-content-row]')].filter(row => !row.hidden).forEach((row, i) => {
        if (!row.id) row.id = 'editor-cell-' + row.querySelector('[name$="-kind"]').id;
        if (row.dataset.kind === 'code') codeInput(row);
        const anchor = document.createElement('a'); anchor.href = '#' + row.id;
        const kind = row.querySelector('[name$="-kind"]').selectedOptions[0]?.textContent || 'Ячейка';
        const text = row.dataset.kind === 'text' ? row.querySelector('.rich-editor')?.textContent : source(row)?.value;
        anchor.textContent = `${i+1}. ${kind}`;
        const label = document.createElement('small'); label.textContent = (text || row.querySelector('[name$="-caption"]')?.value || 'Без подписи').trim().slice(0,55);
        anchor.append(label); fragment.append(anchor);
      });
      outline.replaceChildren(fragment);
    };
    const schedule = () => {if (!pending) {pending = true; requestAnimationFrame(update);}};
    new MutationObserver(schedule).observe(root, {childList:true, attributes:true, attributeFilter:['data-kind','hidden']});
    form.addEventListener('input', schedule); form.addEventListener('change', schedule);
    form.addEventListener('draft:restored', schedule); update();
  }
  const tokens = /#[^\n]*|(?:\b[rfbuRFBU]{0,2})?(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|\b(?:False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b|\b\d+(?:\.\d+)?\b/g;
  function highlight(code) {
    const value = code.textContent, fragment = document.createDocumentFragment(); let at = 0;
    for (const match of value.matchAll(tokens)) {
      fragment.append(document.createTextNode(value.slice(at, match.index)));
      const span = document.createElement('span'); span.textContent = match[0];
      span.className = match[0].startsWith('#') ? 'nb-token-comment' : /["']/.test(match[0]) ? 'nb-token-string' : /^\d/.test(match[0]) ? 'nb-token-number' : 'nb-token-keyword';
      fragment.append(span); at = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(value.slice(at))); code.replaceChildren(fragment);
  }
  document.querySelectorAll('[data-notebook-code]').forEach(block => {
    const code = block.querySelector('code'), button = block.querySelector('[data-copy-notebook-code]');
    button.hidden = false; button.onclick = () => copyText(code.textContent, button); highlight(code);
  });
  const readOutline = document.querySelector('[data-notebook-read-outline]');
  if (readOutline) {
    document.querySelectorAll('[data-notebook-cell]').forEach((cell, i) => {
      const title = cell.querySelector('h2,h3')?.textContent || (cell.classList.contains('notebook-cell-code') ? 'Python-код' : cell.querySelector('figcaption')?.textContent || cell.querySelector('.rich-content')?.textContent || 'Материал');
      const link = document.createElement('a'); link.href = '#' + cell.id; link.textContent = `${i+1}. ${title.trim().slice(0,60)}`; readOutline.append(link);
    });
  }
  const print = document.querySelector('[data-print-notebook]');
  if (print) {print.hidden = false; print.onclick = () => window.print();}
})();
