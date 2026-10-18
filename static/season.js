/* Seasonal decorations behind the page: snow (winter), leaves and bats (Halloween), snow and a twinkling garland (New Year).
   Off during exams, for "reduce motion", and when the pupil turns them off with the button in the top bar. */
(() => {
  'use strict';
  const season = (document.body.className.match(/\bseason-(\w+)/) || [])[1];
  if (!season) return;
  const KEY = 'infocode-season-fx';
  const read = () => { try { return localStorage.getItem(KEY) ?? localStorage.getItem('infocode-snow'); } catch { return null; } };
  const write = value => { try { localStorage.setItem(KEY, value); } catch { /* private mode */ } };
  const calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const exam = document.querySelector('[data-station]');
  const ICONS = {winter: '❄', newyear: '🎄', halloween: '🎃'};
  const LABELS = {winter: 'Снегопад', newyear: 'Новогодние украшения', halloween: 'Хеллоуин'};

  // New Year: a garland of lights along the top of the page (static when motion is off).
  if (season === 'newyear' && !exam) {
    const garland = document.createElement('div');
    garland.className = 'season-garland'; garland.setAttribute('aria-hidden', 'true');
    const count = Math.max(12, Math.round(innerWidth / 46));
    for (let i = 0; i < count; i++) {
      const bulb = document.createElement('span');
      bulb.style.setProperty('--i', i % 4); bulb.style.animationDelay = `${(i % 7) * 0.35}s`;
      garland.append(bulb);
    }
    document.body.prepend(garland);
  }

  let canvas = null, frame = 0;
  const make = () => {
    const w = innerWidth, h = innerHeight, r = Math.random;
    if (season === 'halloween') {
      const leaves = Array.from({length: Math.min(22, Math.round(w / 70))}, () => ({kind: 'leaf', x: r() * w, y: r() * h, s: .35 + r() * .5,
        d: r() * Math.PI * 2, a: r() * Math.PI * 2, size: 14 + r() * 10, glyph: r() < .5 ? '🍂' : '🍁'}));
      const bats = Array.from({length: 3}, () => ({kind: 'bat', x: r() * w, y: 40 + r() * h * .4, s: .6 + r() * .6, d: r() * 6, size: 18 + r() * 8, glyph: '🦇'}));
      return leaves.concat(bats);
    }
    const flakes = Array.from({length: Math.min(season === 'newyear' ? 80 : 60, Math.round(w / (season === 'newyear' ? 20 : 28)))}, () =>
      ({kind: 'flake', x: r() * w, y: r() * h, r: 1 + r() * 2.2, s: .25 + r() * .6, d: r() * Math.PI * 2}));
    if (season === 'newyear') flakes.push(...Array.from({length: 8}, () => ({kind: 'glyph', x: r() * w, y: r() * h, s: .3 + r() * .4, d: r() * 6, a: 0, size: 12 + r() * 8, glyph: '❄'})));
    return flakes;
  };

  const start = () => {
    if (canvas || calm || exam) return;
    document.body.classList.add('season-fx-on');
    canvas = document.createElement('canvas');
    canvas.id = 'season-fx'; canvas.setAttribute('aria-hidden', 'true');
    document.body.prepend(canvas);
    const ctx = canvas.getContext('2d');
    const resize = () => {
      canvas.width = innerWidth * devicePixelRatio; canvas.height = innerHeight * devicePixelRatio;
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    };
    resize(); addEventListener('resize', resize);
    const items = make();
    const colour = getComputedStyle(document.documentElement).getPropertyValue('--flake').trim() || '#93c5fd';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const tick = () => {
      if (!canvas) return;
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
  const stop = () => { cancelAnimationFrame(frame); if (canvas) canvas.remove(); canvas = null; document.body.classList.remove('season-fx-on'); };

  const host = document.querySelector('.topbar-tools') || document.querySelector('.public-top nav');
  if (host && !calm && !exam) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'snow-toggle season-toggle'; button.textContent = ICONS[season] || '✨';
    button.title = LABELS[season] || 'Украшения'; button.setAttribute('aria-label', button.title);
    const sync = on => button.setAttribute('aria-pressed', String(on));
    button.addEventListener('click', () => {
      const on = !canvas;
      write(on ? 'on' : 'off'); sync(on);
      on ? start() : stop();
    });
    host.prepend(button);
    const on = read() !== 'off';
    sync(on);
    if (on) start();
  }
})();
