/* The server independently validates ownership, uploads and every rich-text fragment. */
(() => {
  'use strict';
  const form = document.querySelector('[data-course-editor]');
  if (!form) return;
  const root = form.querySelector('[data-content-blocks]');
  if (!root) return;
  const template = document.querySelector('#empty-content-block');
  const total = form.querySelector('[name="blocks-TOTAL_FORMS"]');
  const palette = form.querySelector('[data-block-palette]');
  const status = form.querySelector('[data-composer-status]');
  let insertAfter = null, dragged = null, dirty = false;
  const urls = new Set();
  const rows = () => [...root.querySelectorAll(':scope > [data-content-row]')];
  const field = (row, name) => row.querySelector(`[name$="-${name}"]`);
  const deleted = row => field(row, 'DELETE').checked;
  const active = () => rows().filter(row => !deleted(row));
  const tell = text => {status.textContent = text;};
  const changed = () => {dirty = true; form.dispatchEvent(new Event('input', {bubbles: true}));};
  function safeURL(value) {
    const text = (value || '').trim();
    if (/[\u0000-\u001f\u007f\\]/.test(text)) return '';
    try {const url = new URL(text); return ['https:', 'http:', 'mailto:'].includes(url.protocol) && !url.username ? text : '';}
    catch {return '';}
  }
  function cleanHTML(html) {
    const doc = new DOMParser().parseFromString(html || '', 'text/html');
    const result = document.createElement('div');
    const aliases = {B:'STRONG', I:'EM', STRIKE:'S', DIV:'P'};
    const allowed = new Set('P BR H2 H3 STRONG EM U S SUP SUB PRE CODE BLOCKQUOTE OL UL LI HR A MARK'.split(' '));
    function walk(source, target) {
      for (const node of source.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) {target.append(document.createTextNode(node.textContent)); continue;}
        if (node.nodeType !== Node.ELEMENT_NODE) continue;
        const tag = aliases[node.tagName] || node.tagName;
        if (!allowed.has(tag)) {walk(node, target); continue;}
        const el = document.createElement(tag.toLowerCase());
        if (tag === 'A' && safeURL(node.getAttribute('href'))) {el.href = safeURL(node.getAttribute('href')); el.rel = 'noopener noreferrer';}
        walk(node, el); target.append(el);
      }
    }
    walk(doc.body, result); return result.innerHTML;
  }
  function refresh() {
    let n = 0;
    rows().forEach(row => {
      row.hidden = deleted(row);
      if (row.hidden) {row.querySelectorAll('[required]').forEach(el => el.required = false); return;}
      field(row, 'position').value = ++n;
      row.querySelector('.block-index').textContent = String(n).padStart(2, '0');
      row.querySelector('[data-block-up]').disabled = n === 1;
      const kind = field(row, 'kind').value;
      row.dataset.kind = kind;
      row.querySelectorAll('[data-block-field]').forEach(el => el.hidden = !el.dataset.blockField.split(' ').includes(kind));
      row.querySelectorAll('[data-block-upload]').forEach(el => el.hidden = el.dataset.blockUpload !== kind);
      const source = field(row, 'text'), host = row.querySelector('.rich-host');
      source.hidden = kind === 'text'; host.hidden = kind !== 'text';
      row.querySelector('.source-label').hidden = kind === 'text';
      if (kind === 'text' && !host.firstChild) buildRich(row);
    });
    active().forEach((row, i, list) => row.querySelector('[data-block-down]').disabled = i === list.length - 1);
    form.querySelector('[data-block-count]').textContent = `${n} / 40 блоков`;
  }
  function newRow(kind='text', after=null, notify=true) {
    if (+total.value >= 40) {tell('До 40 блоков на шаг. Сохраните шаг после удаления блоков или разделите материал на несколько шагов.'); return null;}
    const index = +total.value;
    const holder = document.createElement('template');
    holder.innerHTML = template.innerHTML.replaceAll('__prefix__', String(index));
    const row = holder.content.firstElementChild;
    if (after?.parentElement === root) after.after(row); else root.append(row);
    total.value = index + 1;
    field(row, 'kind').value = kind;
    initialize(row); refresh();
    if (notify) changed();
    return row;
  }
  function buildRich(row) {
    const source = field(row, 'text'), host = row.querySelector('.rich-host');
    const toolbar = document.createElement('div'); toolbar.className = 'rich-toolbar'; toolbar.setAttribute('role', 'toolbar'); toolbar.setAttribute('aria-label', 'Форматирование текста');
    const editor = document.createElement('div'); editor.className = 'rich-editor rich-content'; editor.contentEditable = 'true'; editor.setAttribute('role', 'textbox'); editor.setAttribute('aria-multiline', 'true'); editor.setAttribute('aria-label', 'Текст блока'); editor.dataset.placeholder = 'Напишите объяснение. Выделите текст, чтобы изменить оформление…';
    editor.innerHTML = cleanHTML(source.value);
    const sync = () => {source.value = editor.innerHTML;};
    editor.addEventListener('input', sync);
    editor.addEventListener('blur', sync);
    const commands = [ ['B', 'Жирный (Ctrl+B)', 'bold'], ['I', 'Курсив (Ctrl+I)', 'italic'], ['U', 'Подчёркнутый (Ctrl+U)', 'underline'], ['S', 'Зачёркнутый', 'strikeThrough'], ['H2', 'Заголовок', 'formatBlock', 'h2'], ['H3', 'Подзаголовок', 'formatBlock', 'h3'], ['Тт', 'Обычный абзац', 'formatBlock', 'p'], ['•', 'Список', 'insertUnorderedList'], ['1.', 'Нумерованный список', 'insertOrderedList'], ['❝', 'Цитата', 'formatBlock', 'blockquote'], ['x²', 'Верхний индекс', 'superscript'], ['x₂', 'Нижний индекс', 'subscript'], ['↶', 'Отменить', 'undo'], ['↷', 'Повторить', 'redo'], ['Tx', 'Убрать форматирование', 'removeFormat'] ];
    function tool(label, title, action) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.title = title; button.setAttribute('aria-label', title);
      button.addEventListener('mousedown', e => e.preventDefault());
      button.onclick = action; toolbar.append(button); return button;
    }
    commands.forEach(([label, title, command, value]) => tool(label, title, () => {editor.focus(); document.execCommand('styleWithCSS', false, false); document.execCommand(command, false, value || null); sync(); changed();}));
    const link = document.createElement('div'); link.className = 'rich-link-editor'; link.hidden = true;
    const input = document.createElement('input'); input.type = 'text'; input.inputMode = 'url'; input.placeholder = 'https://…'; input.setAttribute('aria-label', 'Адрес ссылки');
    const apply = document.createElement('button'); apply.type = 'button'; apply.textContent = 'Вставить';
    const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Отмена';
    let linkRange = null;
    tool('↗', 'Добавить ссылку', () => {const selection = getSelection(); linkRange = selection.rangeCount && editor.contains(selection.anchorNode) ? selection.getRangeAt(0).cloneRange() : null; link.hidden = false; input.focus();});
    tool('⊘', 'Убрать ссылку', () => {editor.focus(); document.execCommand('unlink'); sync(); changed();});
    apply.onclick = () => {
      const url = safeURL(input.value);
      if (!url) {input.setCustomValidity('Используйте http://, https:// или mailto:'); input.reportValidity(); return;}
      input.setCustomValidity(''); editor.focus(); const selection = getSelection();
      if (linkRange) {selection.removeAllRanges(); selection.addRange(linkRange);}
      if (!linkRange || linkRange.collapsed) {const a = document.createElement('a'); a.href = url; a.textContent = url; document.execCommand('insertHTML', false, a.outerHTML);}
      else document.execCommand('createLink', false, url);
      link.hidden = true; sync(); changed();
    };
    input.oninput = () => input.setCustomValidity(''); cancel.onclick = () => {link.hidden = true; input.setCustomValidity(''); editor.focus();};
    link.append(input, apply, cancel);
    function insertMedia(kind, file=null) {
      if (+total.value > 37) {tell('Для вставки внутрь абзаца нужны свободные места для блоков. Сохраните шаг после удаления ненужных блоков.'); return;}
      let tailHTML = '';
      const selection = getSelection();
      if (selection.rangeCount && editor.contains(selection.anchorNode) && editor.contains(selection.focusNode)) {
        const range = selection.getRangeAt(0).cloneRange(); range.collapse(true); range.setEnd(editor, editor.childNodes.length);
        const tail = document.createElement('div'); tail.append(range.extractContents()); tailHTML = tail.innerHTML; sync();
      }
      const media = newRow(kind, row); if (!media) return;
      if (tailHTML && tailHTML.replace(/<[^>]+>/g, '').trim()) {
        const next = newRow('text', media);
        if (next) {field(next, 'text').value = tailHTML; next.querySelector('.rich-editor').innerHTML = cleanHTML(tailHTML);}
      }
      // Avoid empty text blocks when inserting at the start of a paragraph.
      if (!editor.textContent.trim()) {field(row, 'DELETE').checked = true; refresh();}
      if (file) {const transfer = new DataTransfer(); transfer.items.add(file); field(media, kind).files = transfer.files; showUpload(media, kind);}
      else field(media, kind).click();
      changed();
    }
    tool('▧', 'Вставить фото в месте курсора', () => insertMedia('image'));
    tool('▷', 'Вставить видео в месте курсора', () => insertMedia('video'));
    editor.addEventListener('paste', event => {
      event.preventDefault();
      const file = [...(event.clipboardData?.files || [])].find(f => /^image\/(png|jpeg|webp)$/.test(f.type));
      if (file) {insertMedia('image', file); return;}
      const html = event.clipboardData.getData('text/html');
      if (html) document.execCommand('insertHTML', false, cleanHTML(html));
      else document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
      sync(); changed();
    });
    // Browser HTML drops must not bypass the same paste restrictions.
    editor.addEventListener('drop', e => {e.preventDefault();});
    host.append(toolbar, link, editor);
  }
  function showUpload(row, kind) {
    const file = field(row, kind).files?.[0]; if (!file) return;
    const slot = row.querySelector('.block-media-preview'); slot.replaceChildren();
    if (kind === 'image' || kind === 'video') {
      const el = document.createElement(kind === 'image' ? 'img' : 'video');
      const url = URL.createObjectURL(file); urls.add(url); el.src = url;
      if (kind === 'video') {el.controls = true; el.preload = 'metadata';} else el.alt = file.name;
      slot.append(el);
    }
    const caption = document.createElement('small'); caption.textContent = file.name + ' · ' + (file.size/1024/1024).toFixed(1) + ' МБ · загрузится при сохранении'; slot.append(caption);
    changed();
  }
  function initialize(row) {
    field(row, 'kind').addEventListener('change', () => {const host = row.querySelector('.rich-host'); if (field(row, 'kind').value === 'text') host.replaceChildren(); refresh(); changed();});
    row.querySelector('[data-block-up]').onclick = () => {const list = active(), at = list.indexOf(row); if (at > 0) list[at-1].before(row); refresh(); changed();};
    row.querySelector('[data-block-down]').onclick = () => {const list = active(), at = list.indexOf(row); if (at < list.length-1) list[at+1].after(row); refresh(); changed();};
    row.querySelector('[data-block-delete]').onclick = () => {
      field(row, 'DELETE').checked = true; refresh(); changed(); tell('Блок удалится после сохранения. ');
      const undo = document.createElement('button'); undo.type = 'button'; undo.textContent = 'Вернуть блок'; undo.onclick = () => {field(row, 'DELETE').checked = false; refresh(); changed(); tell('Блок восстановлен.');}; status.append(undo);
    };
    row.querySelector('[data-block-copy]').onclick = () => {
      const copy = newRow(field(row, 'kind').value, row); if (!copy) return;
      ['text', 'caption', 'asset'].forEach(name => field(copy, name).value = field(row, name).value);
      field(copy, 'copy_from').value = field(row, 'id').value || field(row, 'copy_from').value;
      ['image', 'video', 'file'].forEach(name => {if (field(row, name).files.length) {field(copy, name).files = field(row, name).files; showUpload(copy, name);}});
      copy.querySelector('.rich-host').replaceChildren(); refresh(); changed();
      if (!copy.querySelector('.block-media-preview').children.length) copy.querySelector('.block-media-preview').replaceChildren(...[...row.querySelector('.block-media-preview').childNodes].map(node => node.cloneNode(true)));
    };
    row.querySelector('[data-insert-here]').onclick = () => {insertAfter = row; row.after(palette); palette.querySelector('button').focus(); tell('Выберите тип нового блока.');};
    ['image', 'video', 'file'].forEach(kind => field(row, kind).addEventListener('change', () => showUpload(row, kind)));
    const grip = row.querySelector('.block-grip');
    grip.addEventListener('dragstart', e => {dragged = row; row.classList.add('is-dragging'); e.dataTransfer.setData('text/plain', 'course-block'); e.dataTransfer.effectAllowed = 'move';});
    grip.addEventListener('dragend', () => {dragged = null; row.classList.remove('is-dragging');});
    row.addEventListener('dragover', e => {if (dragged && row !== dragged) e.preventDefault();});
    row.addEventListener('drop', e => {if (!dragged || dragged === row) return; e.preventDefault(); const before = e.clientY < row.getBoundingClientRect().top + row.offsetHeight/2; if (before) row.before(dragged); else row.after(dragged); refresh(); changed();});
  }
  palette.addEventListener('click', e => {const button = e.target.closest('[data-add-block]'); if (!button) return; const row = newRow(button.dataset.addBlock, insertAfter); insertAfter = null; root.after(palette); if (row) {row.scrollIntoView({behavior:'smooth', block:'center'}); row.querySelector('.rich-editor')?.focus();}});
  rows().forEach(initialize); refresh(); form.classList.add('composer-ready');
  form.addEventListener('input', () => {dirty = true;});
  form.addEventListener('formdata', () => {rows().forEach(row => {const editor = row.querySelector('.rich-editor'); if (editor && field(row, 'kind').value === 'text') field(row, 'text').value = editor.innerHTML;});});
  form.addEventListener('submit', () => {dirty = false;}, true);
  window.addEventListener('beforeunload', event => {if (dirty) {event.preventDefault(); event.returnValue = '';}});
  form.addEventListener('draft:prepare', event => {
    const wanted = Math.min(40, Number(event.detail['blocks-TOTAL_FORMS']?.[0] || 0));
    while (+total.value < wanted) newRow('text', null, false);
  });
  form.addEventListener('draft:restored', () => {
    rows().sort((a,b) => +field(a,'position').value - +field(b,'position').value).forEach(row => {root.append(row); row.querySelector('.rich-host').replaceChildren();});
    refresh(); dirty = true;
  });
  const dialog = document.querySelector('.content-preview-dialog');
  document.querySelector('[data-local-preview]').onclick = () => {
    const body = dialog.querySelector('[data-preview-body]'); body.replaceChildren(); body.className = 'course-content';
    const title = document.createElement('h2'); title.textContent = form.querySelector('[name=title]').value; body.append(title);
    active().forEach(row => {
      const kind = field(row, 'kind').value;
      if (kind === 'text') {const div = document.createElement('div'); div.className = 'rich-content'; div.innerHTML = cleanHTML(field(row, 'text').value); body.append(div);}
      else if (kind === 'code') {const pre = document.createElement('pre'); pre.textContent = field(row, 'text').value; body.append(pre);}
      else if (kind === 'divider') body.append(document.createElement('hr'));
      else {const original = row.querySelector('.block-media-preview'); body.append(original.cloneNode(true)); if (!original.children.length) {const note = document.createElement('p'); note.className='notice'; note.textContent = 'Материал из медиатеки будет доступен после сохранения.'; body.append(note);}}
      if (field(row, 'caption').value && kind !== 'text') {const p = document.createElement('figcaption'); p.textContent = field(row, 'caption').value; body.append(p);}
    });
    dialog.showModal();
  };
  const closePreview = () => {dialog.querySelectorAll('video').forEach(v => v.pause()); dialog.close();};
  dialog.querySelector('[data-close-preview]').onclick = closePreview;
  dialog.addEventListener('cancel', closePreview);
})();
