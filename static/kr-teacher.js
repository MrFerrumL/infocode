/* «КР без регистрации»: totals in the builder, quick fill, the catalogue picker and copying pupil links. */
(() => {
  'use strict';
  const builder = document.querySelector('[data-kr-builder]');
  if (builder) {
    const rows = [...builder.querySelectorAll('[data-row]')];
    const row = number => builder.querySelector(`[data-row="${number}"]`);
    const summary = builder.querySelector('[data-summary]');
    const bankData = document.getElementById('kr-bank');
    const bank = bankData ? JSON.parse(bankData.textContent) : null;
    // Tasks of a number in the bank for the chosen level, exam source and year («2025» or «2025+» — that year and newer).
    const inBank = number => {
      const level = builder.querySelector('[name=level]').value;
      const source = builder.querySelector('[name=source]').value;
      const year = builder.querySelector('[name=year]').value;
      const newer = year.endsWith('+');
      const fromYear = parseInt(year, 10);
      return bank.rows.reduce((sum, [n, l, s, y, k]) => sum + (n === number && (!level || l === level)
        && (!source || s === source || (source === 'official' && bank.official.includes(s)))
        && (!year || (newer ? y >= fromYear : y === fromYear)) ? k : 0), 0);
    };
    const update = () => {
      let total = 0;
      const short = [];
      const variants = +builder.querySelector('[name=variants]').value || 1;
      rows.forEach(r => {
        const count = +r.querySelector('input').value || 0;
        total += count;
        r.classList.toggle('has-tasks', count > 0);
        if (!bank) return;
        const number = +r.dataset.row;
        const have = inBank(number);
        r.querySelector('[data-bank-count]').textContent = `в банке ${have}`;
        const fromBank = r.querySelector('select').value === 'bank';
        const lacking = fromBank && count * variants > have;
        r.classList.toggle('is-short', lacking);
        if (lacking) short.push(`№ ${number} (есть ${have}, нужно ${count * variants})`);
      });
      summary.textContent = !total ? 'Укажите число задач хотя бы для одного номера.'
        : short.length ? `Не хватает задач с этим уровнем, источником и годом: ${short.join(', ')}. Уменьшите число, выберите «Любой» или «Генератор».`
        : `В каждом варианте ${total} задач, всего вариантов: ${variants}.`;
      summary.classList.toggle('is-short', short.length > 0);
    };
    builder.addEventListener('input', update);
    builder.addEventListener('change', update);
    builder.querySelectorAll('[data-fill]').forEach(button => button.addEventListener('click', () => {
      rows.forEach(r => { const input = r.querySelector('input'); if (!input.disabled) input.value = button.dataset.fill; });
      update();
    }));
    builder.addEventListener('submit', () => {
      const button = builder.querySelector('.kr-submit button');
      button.disabled = true;
      button.textContent = 'Собираем варианты…';
    });

    // Catalogue picker: chosen tasks are kept as "id:number" while the page is open and sent as ids.
    const picker = builder.querySelector('[data-picker]');
    const field = picker.querySelector('[data-picked]');
    const list = picker.querySelector('[data-pick-list]');
    const chips = picker.querySelector('[data-chips]');
    const picked = new Map();  // id → {number, label}
    field.value.split(',').filter(Boolean).forEach(id => picked.set(id, {number: '', label: `ID ${id}`}));
    const render = () => {
      field.value = [...picked.keys()].join(',');
      chips.innerHTML = '';
      picked.forEach((task, id) => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'kr-chip';
        chip.textContent = `${task.label} ✕`;
        chip.title = 'Убрать из КР';
        chip.addEventListener('click', () => { picked.delete(id); render(); });
        chips.append(chip);
      });
      picker.querySelector('[data-picked-total]').textContent = picked.size ? `Выбрано задач: ${picked.size}` : '';
      rows.forEach(r => {
        const n = [...picked.values()].filter(t => String(t.number) === r.dataset.row).length;
        r.querySelector('[data-picked-count]').textContent = n ? `выбрано ${n}` : '';
      });
      list.querySelectorAll('[data-pick]').forEach(b => {
        const on = picked.has(b.dataset.pick);
        b.textContent = on ? '✓ В КР' : '+ В КР';
        b.classList.toggle('is-picked', on);
      });
    };
    const load = async (page = 1) => {
      const params = new URLSearchParams({number: picker.querySelector('[data-filter-number]').value,
                                          q: picker.querySelector('[data-filter-q]').value, page,
                                          mode: (picker.querySelector('[data-photo-mode]') || {}).value || '',
                                          level: picker.querySelector('[data-filter-level]').value,
                                          source: picker.querySelector('[data-filter-source]').value,
                                          year: picker.querySelector('[data-filter-year]').value});
      list.innerHTML = '<p class="muted">Загружаем задачи…</p>';
      try {
        const response = await fetch(`${picker.dataset.url}&${params}`, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
        list.innerHTML = response.ok ? await response.text() : '<p class="muted">Не удалось загрузить задачи.</p>';
      } catch { list.innerHTML = '<p class="muted">Нет связи с сервером.</p>'; }
      render();
    };
    picker.querySelectorAll('[data-filter-number], [data-filter-level], [data-filter-source], [data-filter-year]').forEach(el => el.addEventListener('change', () => load()));
    picker.querySelector('[data-filter-go]').addEventListener('click', () => load());
    picker.querySelector('[data-filter-q]').addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); load(); }
    });
    list.addEventListener('click', event => {
      const page = event.target.closest('[data-page]');
      if (page) { load(page.dataset.page); picker.scrollIntoView({behavior: 'smooth'}); return; }
      const more = event.target.closest('[data-more]');
      if (more) { more.closest('.kr-pick-card').classList.toggle('is-open'); return; }
      const button = event.target.closest('[data-pick]');
      if (!button) return;
      const id = button.dataset.pick;
      if (picked.has(id)) picked.delete(id);
      else {
        picked.set(id, {number: button.dataset.number, label: button.dataset.label});
        // A number with chosen tasks takes them, at least one per variant.
        const target = row(button.dataset.number);
        if (target) {
          target.querySelector('select').value = 'pick';
          const input = target.querySelector('input');
          if (!+input.value) input.value = 1;
        }
      }
      render();
      update();
    });
    render();
    update();
    load();  // all numbers at once; filters narrow the list
  }
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = 'Скопировано';
    } catch { window.prompt('Ссылка для учеников:', button.dataset.copy); }
  }));
})();
