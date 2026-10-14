/* Calendar wall-clock values are always Moscow time, independent of browser zone. */
(() => {
  'use strict';
  const DAY = 86400000;
  const node = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  };
  const dayKey = value => value.toISOString().slice(0, 10);
  const dayDate = key => new Date(key + 'T00:00:00Z');
  const wallDate = value => new Date(value.slice(0, 19) + 'Z');
  const addDays = (date, count) => new Date(date.getTime() + count * DAY);
  const clock = date => date.toISOString().slice(11, 16);
  const inputTime = date => date.toISOString().slice(0, 16);
  const fmt = (date, options) => new Intl.DateTimeFormat('ru-RU', {timeZone: 'UTC', ...options}).format(date);
  const moscowNow = () => {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(new Date()).map(p => [p.type, p.value]));
    return new Date(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:00Z`);
  };
  const startWeek = date => addDays(date, -((date.getUTCDay() + 6) % 7));

  document.querySelectorAll('[data-dialog-close]').forEach(button => {
    button.addEventListener('click', () => button.closest('dialog').close());
  });
  document.querySelectorAll('dialog.calendar-dialog').forEach(dialog => {
    dialog.addEventListener('click', event => {
      if (event.target === dialog) {
        const bounds = dialog.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
      }
    });
  });
  document.querySelectorAll('[data-calendar-form]').forEach(form => {
    const get = name => form.querySelector(`[name="${name}"]`);
    const update = () => {
      const repeat = get('recurrence')?.value || 'none';
      form.querySelectorAll('[data-repeat-options]').forEach(el => el.hidden = repeat === 'none');
      form.querySelectorAll('[data-weekday-options]').forEach(el => el.hidden = !['weekly', 'biweekly'].includes(repeat));
      form.querySelectorAll('[data-audience-options]').forEach(el => el.hidden = get('audience')?.value !== 'selected');
    };
    form.addEventListener('change', update);
    const start = get('starts_at'), end = get('ends_at');
    start?.addEventListener('change', () => {
      if (start.value && (!end.value || end.value <= start.value)) {
        end.value = inputTime(new Date(wallDate(start.value + ':00').getTime() + (get('all_day')?.checked ? DAY : 3600000)));
      }
    });
    get('all_day')?.addEventListener('change', () => {
      if (get('all_day').checked && start.value) {
        start.value = start.value.slice(0, 10) + 'T00:00';
        end.value = inputTime(addDays(dayDate(start.value.slice(0, 10)), 1));
      }
    });
    form.querySelector('[data-calendar-student-search]')?.addEventListener('input', event => {
      const search = event.target.value.toLocaleLowerCase('ru').trim();
      form.querySelectorAll('[id="id_students"] > div').forEach(row => row.hidden = !row.textContent.toLocaleLowerCase('ru').includes(search));
    });
    update();
  });

  const root = document.querySelector('[data-calendar]');
  if (!root) return;
  const teacher = root.dataset.teacher === 'true';
  const body = root.querySelector('[data-calendar-body]');
  const status = root.querySelector('[data-calendar-status]');
  const title = root.querySelector('[data-calendar-title]');
  const viewSelect = root.querySelector('[data-calendar-view]');
  const jump = root.querySelector('[data-calendar-jump]');
  const newDialog = document.querySelector('[data-calendar-new]');
  const detailDialog = document.querySelector('[data-calendar-detail]');
  let current = dayDate(dayKey(moscowNow())), mode = 'month', rows = [], fetchVersion = 0, dragging = null;
  try { const saved = localStorage.getItem('infocode-calendar-view'); if (['month', 'week', 'day', 'agenda'].includes(saved)) mode = saved; } catch (_) {}
  viewSelect.value = mode;

  const range = () => {
    if (mode === 'month') {
      const first = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), 1));
      return {begin: startWeek(first), end: addDays(startWeek(first), 42)};
    }
    if (mode === 'week') return {begin: startWeek(current), end: addDays(startWeek(current), 7)};
    return {begin: current, end: addDays(current, mode === 'day' ? 1 : 30)};
  };
  const filtered = () => rows.filter(row => root.querySelector(`[data-calendar-kind="${row.kind}"]`)?.checked);
  const inDay = (row, date) => wallDate(row.start) < addDays(date, 1) && wallDate(row.end) > date;
  const createAt = (start, end, allDay = false) => {
    if (!teacher) return;
    if (!newDialog || typeof newDialog.showModal !== 'function') {
      location.href = `${root.dataset.create}?start=${encodeURIComponent(inputTime(start))}&end=${encodeURIComponent(inputTime(end))}&all_day=${allDay ? 1 : 0}`;
      return;
    }
    const form = newDialog.querySelector('form');
    form.reset();
    form.elements.starts_at.value = inputTime(start);
    form.elements.ends_at.value = inputTime(end);
    form.elements.all_day.checked = allDay;
    form.elements.repeat_until.value = dayKey(addDays(start, 90));
    form.dispatchEvent(new Event('change'));
    newDialog.showModal();
    form.elements.title.focus();
  };
  const showEvent = row => {
    if (!detailDialog || typeof detailDialog.showModal !== 'function') { location.href = row.url; return; }
    detailDialog.querySelector('[data-event-title]').textContent = row.title;
    const start = wallDate(row.start), end = wallDate(row.end);
    detailDialog.querySelector('[data-event-time]').textContent = `${fmt(start, {weekday: 'long', day: 'numeric', month: 'long'})} · ${row.all_day ? 'весь день' : clock(start) + ' — ' + (dayKey(start) === dayKey(end) ? '' : fmt(end, {day: 'numeric', month: 'short'}) + ' ') + clock(end)} · МСК`;
    detailDialog.querySelector('[data-event-teacher]').textContent = `${row.teacher} · ${row.audience}`;
    detailDialog.querySelector('[data-event-location]').textContent = row.location || '';
    detailDialog.querySelector('[data-event-description]').textContent = row.description || '';
    detailDialog.querySelector('[data-event-detail]').href = row.url;
    const meeting = detailDialog.querySelector('[data-event-meeting]');
    meeting.hidden = !/^https?:\/\//i.test(row.meeting_url || '');
    if (!meeting.hidden) meeting.href = row.meeting_url;
    const edit = detailDialog.querySelector('[data-event-edit]');
    edit.hidden = !teacher || !row.editable;
    if (!edit.hidden) edit.href = row.edit_url;
    detailDialog.showModal();
  };
  const eventNode = (row, agenda = false) => {
    const el = node('button', 'calendar-event');
    el.type = 'button'; el.dataset.color = row.color;
    el.title = `${row.title} · ${row.all_day ? 'весь день' : clock(wallDate(row.start)) + '–' + clock(wallDate(row.end))} МСК`;
    const time = node('time', '', row.all_day ? 'Весь день' : clock(wallDate(row.start)) + (agenda ? ' — ' + clock(wallDate(row.end)) : ''));
    const name = node('strong', '', row.title);
    el.append(time, name);
    if (agenda) el.append(node('small', '', [row.teacher, row.location].filter(Boolean).join(' · ')));
    el.addEventListener('click', event => { event.stopPropagation(); showEvent(row); });
    if (teacher && row.editable) {
      el.draggable = true;
      el.addEventListener('dragstart', event => {
        dragging = row;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', row.id);
      });
      el.addEventListener('dragend', () => { dragging = null; root.querySelectorAll('.drag-over').forEach(n => n.classList.remove('drag-over')); });
    }
    return el;
  };
  const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const moveTo = async (row, start) => {
    const duration = wallDate(row.end) - wallDate(row.start);
    status.textContent = 'Сохраняем перенос…';
    try {
      const response = await fetch(row.move_url, {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
        body: JSON.stringify({original_date: row.original_date, revision: row.revision,
          start: inputTime(start), end: inputTime(new Date(start.getTime() + duration))})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось сохранить перенос.');
      await load(); status.textContent = 'Занятие перенесено. Участники получили уведомление.';
    } catch (error) { await load(); status.textContent = error.message || 'Не удалось сохранить перенос.'; }
  };
  const dropTarget = (el, date, timed = false) => {
    el.addEventListener('dragover', event => {
      if (!dragging || !teacher) return;
      event.preventDefault(); el.classList.add('drag-over');
    });
    el.addEventListener('dragleave', event => { if (!el.contains(event.relatedTarget)) el.classList.remove('drag-over'); });
    el.addEventListener('drop', event => {
      event.preventDefault(); el.classList.remove('drag-over');
      const row = dragging; dragging = null;
      if (!row) return;
      const old = wallDate(row.start);
      let minutes = old.getUTCHours() * 60 + old.getUTCMinutes();
      if (timed && !row.all_day) minutes = Math.max(0, Math.min(1410, Math.floor((event.clientY - el.getBoundingClientRect().top) / 24) * 30));
      if (row.all_day) minutes = 0;
      moveTo(row, new Date(date.getTime() + minutes * 60000));
    });
  };

  const renderMonth = visible => {
    const days = node('div', 'calendar-weekdays');
    ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].forEach(label => days.append(node('div', '', label)));
    const grid = node('div', 'calendar-month');
    const {begin} = range(); const today = dayKey(moscowNow());
    for (let i = 0; i < 42; i++) {
      const date = addDays(begin, i), key = dayKey(date);
      const cell = node('div', 'calendar-cell'); cell.dataset.date = key;
      if (date.getUTCMonth() !== current.getUTCMonth()) cell.classList.add('is-outside');
      if (key === today) cell.classList.add('is-today');
      const number = node('button', 'calendar-day-number', String(date.getUTCDate())); number.type = 'button';
      number.setAttribute('aria-label', fmt(date, {day: 'numeric', month: 'long'}) + ': открыть день');
      number.addEventListener('click', event => { event.stopPropagation(); current = date; mode = 'day'; viewSelect.value = mode; load(); });
      cell.append(number);
      const events = visible.filter(row => inDay(row, date));
      events.slice(0, 3).forEach(row => cell.append(eventNode(row)));
      if (events.length > 3) {
        const more = node('button', 'calendar-more', `Ещё ${events.length - 3}`); more.type = 'button';
        more.addEventListener('click', event => { event.stopPropagation(); current = date; mode = 'day'; viewSelect.value = mode; load(); });
        cell.append(more);
      }
      if (teacher) cell.addEventListener('click', () => createAt(new Date(date.getTime() + 9 * 3600000), new Date(date.getTime() + 10 * 3600000)));
      dropTarget(cell, date); grid.append(cell);
    }
    body.append(days, grid);
  };
  const timedNodes = (column, events, date) => {
    const intervals = events.map(row => ({row, start: Math.max(0, (wallDate(row.start) - date) / 60000),
      end: Math.min(1440, (wallDate(row.end) - date) / 60000)})).sort((a, b) => a.start - b.start || b.end - a.end);
    let cluster = [], clusterEnd = 0;
    const flush = () => {
      if (!cluster.length) return;
      const lanes = [];
      cluster.forEach(item => {
        let lane = lanes.findIndex(end => end <= item.start);
        if (lane < 0) lane = lanes.length;
        lanes[lane] = item.end; item.lane = lane;
      });
      cluster.forEach(item => {
        const el = eventNode(item.row);
        el.style.top = `${item.start * .8}px`;
        el.style.height = `${Math.max(18, (item.end - item.start) * .8 - 2)}px`;
        el.style.left = `calc(${item.lane * 100 / lanes.length}% + 2px)`;
        el.style.width = `calc(${100 / lanes.length}% - 4px)`;
        column.append(el);
      });
      cluster = [];
    };
    intervals.forEach(item => {
      if (cluster.length && item.start >= clusterEnd) flush();
      if (!cluster.length) clusterEnd = item.end;
      else clusterEnd = Math.max(clusterEnd, item.end);
      cluster.push(item);
    });
    flush();
  };
  const selectable = (column, date) => {
    if (!teacher) return;
    let selection = null, anchor = 0, last = 0;
    const minutes = event => Math.max(0, Math.min(1410, Math.floor((event.clientY - column.getBoundingClientRect().top) / 24) * 30));
    const paint = () => {
      const min = Math.min(anchor, last), max = Math.max(anchor, last) + 30;
      selection.style.top = `${min * .8}px`; selection.style.height = `${(max - min) * .8}px`;
      selection.firstChild.textContent = `${clock(new Date(date.getTime() + min * 60000))}–${clock(new Date(date.getTime() + max * 60000))}`;
    };
    column.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.target.closest('.calendar-event') || event.pointerType === 'touch') return;
      anchor = last = minutes(event); selection = node('div', 'calendar-selection'); selection.append(node('span'));
      column.append(selection); column.setPointerCapture(event.pointerId); paint(); event.preventDefault();
    });
    column.addEventListener('pointermove', event => { if (selection) { last = minutes(event); paint(); } });
    column.addEventListener('pointerup', () => {
      if (!selection) return;
      selection.remove(); selection = null;
      createAt(new Date(date.getTime() + Math.min(anchor, last) * 60000), new Date(date.getTime() + (Math.max(anchor, last) + 30) * 60000));
    });
    column.addEventListener('pointercancel', () => { selection?.remove(); selection = null; });
    column.addEventListener('click', event => {
      if (!event.target.closest('.calendar-event') && event.pointerType === 'touch') {
        const min = minutes(event); createAt(new Date(date.getTime() + min * 60000), new Date(date.getTime() + (min + 30) * 60000));
      }
    });
  };
  const renderTime = (visible, scrollTop) => {
    const {begin, end} = range(), count = (end - begin) / DAY, now = moscowNow();
    const scroll = node('div', 'calendar-time-scroll');
    const grid = node('div', 'calendar-time-grid' + (count === 1 ? ' is-day' : '')); grid.style.setProperty('--days', count);
    const head = node('div', 'calendar-time-head'); head.append(node('div', '', 'МСК'));
    const allDay = node('div', 'calendar-all-day'); allDay.append(node('div', 'calendar-all-day-label', 'Весь день'));
    const timeline = node('div', 'calendar-timeline'); const hours = node('div', 'calendar-hours');
    for (let hour = 1; hour < 24; hour++) { const mark = node('span', '', String(hour).padStart(2, '0') + ':00'); mark.style.top = `${hour * 48}px`; hours.append(mark); }
    timeline.append(hours);
    for (let i = 0; i < count; i++) {
      const date = addDays(begin, i), today = dayKey(date) === dayKey(now);
      const label = node('div', today ? 'is-today' : '', fmt(date, {weekday: 'short'})); label.append(node('strong', '', date.getUTCDate())); head.append(label);
      const dayBar = node('div'); visible.filter(row => row.all_day && inDay(row, date)).forEach(row => dayBar.append(eventNode(row)));
      if (teacher) dayBar.addEventListener('click', event => { if (!event.target.closest('.calendar-event')) createAt(date, addDays(date, 1), true); });
      dropTarget(dayBar, date); allDay.append(dayBar);
      const column = node('div', 'calendar-time-column' + (today ? ' is-today' : '')); column.dataset.date = dayKey(date);
      timedNodes(column, visible.filter(row => !row.all_day && inDay(row, date)), date);
      if (today) { const line = node('div', 'calendar-now-line'); line.style.top = `${(now.getUTCHours() * 60 + now.getUTCMinutes()) * .8}px`; column.append(line); }
      selectable(column, date); dropTarget(column, date, true); timeline.append(column);
    }
    grid.append(head, allDay, timeline); scroll.append(grid); body.append(scroll);
    requestAnimationFrame(() => scroll.scrollTop = scrollTop == null ? 400 : scrollTop);
  };
  const renderAgenda = visible => {
    const agenda = node('div', 'calendar-agenda'), {begin, end} = range(); let found = false;
    for (let date = begin; date < end; date = addDays(date, 1)) {
      const events = visible.filter(row => inDay(row, date)); if (!events.length) continue;
      found = true;
      const section = node('section', 'calendar-agenda-day');
      const label = node('div', 'calendar-agenda-date', fmt(date, {weekday: 'short', month: 'short'})); label.append(node('strong', '', date.getUTCDate()));
      const list = node('div'); events.forEach(row => list.append(eventNode(row, true))); section.append(label, list); agenda.append(section);
    }
    if (!found) { const empty = node('div', 'calendar-empty'); empty.append(node('span', 'empty-calendar-icon', '▦'), node('h2', '', 'На этот период нет событий'), node('p', '', teacher ? 'Добавьте занятие или назначьте домашнее задание.' : 'Здесь появятся назначенные вам занятия и сроки работ.')); agenda.append(empty); }
    body.append(agenda);
  };
  const render = () => {
    const scrollTop = body.querySelector('.calendar-time-scroll')?.scrollTop;
    body.replaceChildren(); const visible = filtered();
    if (mode === 'month') renderMonth(visible);
    else if (mode === 'agenda') renderAgenda(visible);
    else renderTime(visible, scrollTop);
  };
  const load = async () => {
    const version = ++fetchVersion, {begin, end} = range();
    jump.value = dayKey(current);
    title.textContent = mode === 'month' ? fmt(current, {month: 'long', year: 'numeric'}) : mode === 'day' ? fmt(current, {day: 'numeric', month: 'long', year: 'numeric'}) : `${fmt(begin, {day: 'numeric', month: 'short'})} — ${fmt(addDays(end, -1), {day: 'numeric', month: 'short', year: 'numeric'})}`;
    root.querySelector('[data-calendar-export]').href = `${root.dataset.export}?start=${dayKey(begin)}&end=${dayKey(end)}`;
    status.textContent = 'Загружаем расписание…';
    try {
      const response = await fetch(`${root.dataset.feed}?start=${dayKey(begin)}&end=${dayKey(end)}`, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('Не удалось загрузить расписание. Обновите страницу или войдите снова.');
      const data = await response.json();
      if (version !== fetchVersion) return;
      rows = data.events; render(); status.textContent = '';
    } catch (error) { if (version === fetchVersion) { rows = []; body.replaceChildren(); status.textContent = error.message; } }
  };
  root.querySelector('[data-calendar-today]').addEventListener('click', () => { current = dayDate(dayKey(moscowNow())); load(); });
  const shift = sign => {
    if (mode === 'month') current = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + sign, 1));
    else current = addDays(current, sign * (mode === 'week' ? 7 : mode === 'agenda' ? 30 : 1));
    load();
  };
  root.querySelector('[data-calendar-prev]').addEventListener('click', () => shift(-1));
  root.querySelector('[data-calendar-next]').addEventListener('click', () => shift(1));
  viewSelect.addEventListener('change', () => {
    mode = viewSelect.value;
    try { localStorage.setItem('infocode-calendar-view', mode); } catch (_) {}
    load();
  });
  jump.addEventListener('change', () => { if (/^\d{4}-\d{2}-\d{2}$/.test(jump.value)) { current = dayDate(jump.value); load(); } });
  root.querySelectorAll('[data-calendar-kind]').forEach(input => input.addEventListener('change', render));
  document.querySelector('[data-calendar-create]')?.addEventListener('click', event => {
    if (!newDialog?.showModal) return; event.preventDefault();
    createAt(new Date(current.getTime() + 9 * 3600000), new Date(current.getTime() + 10 * 3600000));
  });
  window.addEventListener('focus', () => { if (!document.querySelector('dialog[open]')) load(); });
  setInterval(() => { if (!document.hidden && !document.querySelector('dialog[open]')) load(); }, 60000);
  load();
})();
