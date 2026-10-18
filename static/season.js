/* Seasonal decorations: Halloween (leaves, bats), winter (snow) and New Year (garland, snow).
   The calendar picks one (learning/seasons.py); each visitor can switch it in the top bar menu, the choice is kept in the browser.
   Nothing moves during exams or with "reduce motion". */
(() => {
  'use strict';
  const body = document.body;
  const exam = document.querySelector('[data-station]');
  if (exam) return;
  const KEY = 'infocode-season';
  const CHOICES = [['halloween', '🎃', 'Хеллоуин'], ['winter', '❄', 'Зима'], ['newyear', '🎄', 'Новый год'], ['none', '✕', 'Без украшений']];
  const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const write = value => { try { localStorage.setItem(KEY, value); } catch { /* private mode */ } };
  const calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const byCalendar = (body.className.match(/\bseason-(halloween|winter|newyear)\b/) || [])[1] || 'none';
  // Earlier versions had a plain on/off switch.
  const legacyOff = read('infocode-season-fx') === 'off' || read('infocode-snow') === 'off';
  let season = CHOICES.some(c => c[0] === read(KEY)) ? read(KEY) : (legacyOff ? 'none' : byCalendar);

  let canvas = null, frame = 0, garland = null;
  const particles = () => {
    const w = innerWidth, h = innerHeight, r = Math.random;
    if (season === 'halloween') {
      const leaves = Array.from({length: Math.min(22, Math.round(w / 70))}, () => ({kind: 'leaf', x: r() * w, y: r() * h, s: .35 + r() * .5,
        d: r() * Math.PI * 2, size: 14 + r() * 10, glyph: r() < .5 ? '🍂' : '🍁'}));
      const bats = Array.from({length: 3}, () => ({kind: 'bat', x: r() * w, y: 40 + r() * h * .4, s: .6 + r() * .6, d: r() * 6, size: 18 + r() * 8, glyph: '🦇'}));
      return leaves.concat(bats);
    }
    const many = season === 'newyear';
    const flakes = Array.from({length: Math.min(many ? 80 : 60, Math.round(w / (many ? 20 : 28)))}, () =>
      ({kind: 'flake', x: r() * w, y: r() * h, r: 1 + r() * 2.2, s: .25 + r() * .6, d: r() * Math.PI * 2}));
    if (many) flakes.push(...Array.from({length: 8}, () => ({kind: 'glyph', x: r() * w, y: r() * h, s: .3 + r() * .4, d: r() * 6, size: 12 + r() * 8, glyph: '❄'})));
    return flakes;
  };

  const resize = () => {
    if (!canvas) return;
    canvas.width = innerWidth * devicePixelRatio; canvas.height = innerHeight * devicePixelRatio;
    canvas.getContext('2d').setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  };
  addEventListener('resize', resize);

  const startMotion = () => {
    if (calm) return;
    body.classList.add('season-fx-on');
    canvas = document.createElement('canvas');
    canvas.id = 'season-fx'; canvas.setAttribute('aria-hidden', 'true');
    body.prepend(canvas); resize();
    const ctx = canvas.getContext('2d'), items = particles(), mine = canvas;
    const colour = getComputedStyle(document.documentElement).getPropertyValue('--flake').trim() || '#93c5fd';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const tick = () => {
      if (canvas !== mine) return;
      if (!document.hidden) {
        const w = innerWidth, h = innerHeight;
        ctx.clearRect(0, 0, w, h);
        for (const p of items) {
          p.d += .01;
          if (p.kind === 'bat') {
            p.x += p.s; p.y += Math.sin(p.d * 3) * .6;
            if (p.x > w + 30) { p.x = -30; p.y = 40 + Math.random() * h * .4; }
          } else {
            p.y += p.s; p.x += Math.sin(p.d) * (p.kind === 'leaf' ? .7 : .3);
            if (p.y > h + 20) { p.y = -20; p.x = Math.random() * w; }
          }
          if (p.kind === 'flake') {
            ctx.globalAlpha = .55; ctx.fillStyle = colour;
            ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
          } else {
            ctx.globalAlpha = p.kind === 'bat' ? .5 : .6;
            ctx.save(); ctx.translate(p.x, p.y);
            if (p.kind === 'leaf') ctx.rotate(Math.sin(p.d) * .8);
            ctx.font = `${p.size}px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif`;
            if (p.kind === 'glyph') ctx.fillStyle = colour;
            ctx.fillText(p.glyph, 0, 0); ctx.restore();
          }
        }
      }
      frame = requestAnimationFrame(tick);
    };
    tick();
  };

  const clear = () => {
    cancelAnimationFrame(frame);
    if (canvas) canvas.remove();
    if (garland) garland.remove();
    canvas = garland = null;
    body.classList.remove('season-fx-on', 'season-halloween', 'season-winter', 'season-newyear');
  };

  const apply = () => {
    clear();
    if (season === 'none') return;
    body.classList.add('season-' + season);
    if (season === 'newyear') {
      garland = document.createElement('div');
      garland.className = 'season-garland'; garland.setAttribute('aria-hidden', 'true');
      const count = Math.max(12, Math.round(innerWidth / 46));
      for (let i = 0; i < count; i++) {
        const bulb = document.createElement('span');
        bulb.style.animationDelay = `${(i % 7) * 0.35}s`;
        garland.append(bulb);
      }
      body.prepend(garland);
    }
    startMotion();
  };

  // Top bar menu: 🎃 / ❄ / 🎄 / ✕
  const host = document.querySelector('.topbar-tools') || document.querySelector('.public-top nav');
  if (host) {
    const wrap = document.createElement('div');
    wrap.className = 'season-picker';
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'snow-toggle season-toggle';
    button.setAttribute('aria-haspopup', 'menu'); button.setAttribute('aria-expanded', 'false');
    const menu = document.createElement('div');
    menu.className = 'season-menu'; menu.setAttribute('role', 'menu'); menu.hidden = true;
    const close = () => { menu.hidden = true; button.setAttribute('aria-expanded', 'false'); };
    const sync = () => {
      const current = CHOICES.find(c => c[0] === season);
      button.textContent = current[1]; button.title = 'Оформление: ' + current[2];
      button.setAttribute('aria-label', button.title); button.setAttribute('aria-pressed', String(season !== 'none'));
      menu.querySelectorAll('[role=menuitemradio]').forEach(item => item.setAttribute('aria-checked', String(item.dataset.season === season)));
    };
    for (const [value, icon, label] of CHOICES) {
      const item = document.createElement('button');
      item.type = 'button'; item.setAttribute('role', 'menuitemradio'); item.dataset.season = value;
      item.innerHTML = `<span aria-hidden="true">${icon}</span>`; item.append(label + (value === byCalendar ? ' · сейчас по календарю' : ''));
      item.addEventListener('click', () => { season = value; write(value); apply(); sync(); close(); button.focus(); });
      menu.append(item);
    }
    button.addEventListener('click', () => {
      const open = menu.hidden;
      menu.hidden = !open; button.setAttribute('aria-expanded', String(open));
      if (open) (menu.querySelector('[aria-checked=true]') || menu.firstChild).focus();
    });
    document.addEventListener('click', event => { if (!wrap.contains(event.target)) close(); });
    wrap.addEventListener('keydown', event => {
      if (event.key === 'Escape') { close(); button.focus(); }
      const items = [...menu.children], at = items.indexOf(document.activeElement);
      if (!menu.hidden && (event.key === 'ArrowDown' || event.key === 'ArrowUp') && at >= 0) {
        event.preventDefault(); items[(at + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
      }
    });
    wrap.append(button, menu);
    host.prepend(wrap);
    sync();
  }
  apply();
})();
