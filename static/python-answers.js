/* Editing conveniences only; validation and permissions remain on the server. */
(() => {
  'use strict';
  document.querySelectorAll('textarea[data-python-answer]').forEach(input => {
    const toolbar = document.createElement('div'); toolbar.className = 'python-input-tools';
    const pick = document.createElement('button'); pick.type = 'button'; pick.textContent = 'Вставить из .py';
    const file = document.createElement('input'); file.type = 'file'; file.accept = '.py'; file.hidden = true; file.tabIndex = -1;
    const counter = document.createElement('span'); counter.className = 'muted';
    const info = document.createElement('small'); info.className = 'python-input-status'; info.setAttribute('role', 'status');
    const limit = input.maxLength > 0 ? input.maxLength : 20000;
    const count = () => {counter.textContent = `${input.value.length.toLocaleString('ru-RU')} / ${limit.toLocaleString('ru-RU')}`;};
    toolbar.append(pick, file, counter); input.before(toolbar); input.after(info);
    pick.onclick = () => file.click();
    file.onchange = async () => {
      const chosen = file.files?.[0]; if (!chosen) return;
      try {
        if (!chosen.name.toLowerCase().endsWith('.py') || chosen.size > limit * 4) throw Error('Выберите файл .py до 20 000 символов в UTF-8.');
        const source = new TextDecoder('utf-8', {fatal:true}).decode(await chosen.arrayBuffer());
        if (source.length > limit || source.includes('\u0000')) throw Error('В файле слишком много символов или недопустимый нулевой символ.');
        input.value = source; input.dispatchEvent(new Event('input', {bubbles:true})); input.focus();
        info.textContent = 'Код вставлен. Отправьте ответ, чтобы сохранить его.';
      } catch (error) {info.textContent = error instanceof TypeError ? 'Сохраните .py-файл в UTF-8 и попробуйте ещё раз.' : error.message;}
      file.value = '';
    };
    function replace(text, start=input.selectionStart, end=input.selectionEnd) {
      if (input.value.length - (end-start) + text.length > limit) {info.textContent='Достигнут лимит длины кода.'; return;}
      input.setRangeText(text, start, end, 'end'); input.dispatchEvent(new Event('input', {bubbles:true}));
    }
    input.addEventListener('keydown', event => {
      if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === 'Tab' && !event.shiftKey) {
        event.preventDefault();
        const selected = input.value.slice(input.selectionStart, input.selectionEnd);
        if (selected.includes('\n')) {
          const start = input.value.lastIndexOf('\n', input.selectionStart-1) + 1;
          replace(input.value.slice(start, input.selectionEnd).split('\n').map(line => '    '+line).join('\n'), start);
        } else replace('    ');
      } else if (event.key === 'Enter' && !event.shiftKey) {
        const start = input.value.lastIndexOf('\n', input.selectionStart-1) + 1;
        const line = input.value.slice(start, input.selectionStart);
        const indent = line.match(/^[ \t]*/)[0] + (line.trimEnd().endsWith(':') ? '    ' : '');
        event.preventDefault(); replace('\n' + indent);
      }
    });
    input.addEventListener('input', count); count();
    info.textContent = 'Tab — 4 пробела · Enter — новая строка с отступом · Shift+Tab — выйти из поля';
  });
})();
