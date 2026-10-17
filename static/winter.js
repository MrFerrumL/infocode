/* Gentle snowfall behind the page. Off during exams, for "reduce motion", and when the pupil turns it off. */
(() => {
  'use strict';
  const KEY = 'infocode-snow';
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const write = value => { try { localStorage.setItem(KEY, value); } catch { /* private mode */ } };
  const calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const exam = document.querySelector('[data-station]');
  let canvas = null, frame = 0, flakes = [];

  const start = () => {
    if (canvas || calm || exam) return;
    canvas = document.createElement('canvas');
    canvas.id = 'winter-snow';
    canvas.setAttribute('aria-hidden', 'true');
    document.body.prepend(canvas);
    const ctx = canvas.getContext('2d');
    const resize = () => {
      canvas.width = innerWidth * devicePixelRatio; canvas.height = innerHeight * devicePixelRatio;
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    };
    resize();
    addEventListener('resize', resize);
    const count = Math.min(60, Math.round(innerWidth / 28));
    flakes = Array.from({length: count}, () => ({x: Math.random() * innerWidth, y: Math.random() * innerHeight,
      r: 1 + Math.random() * 2.2, s: .25 + Math.random() * .6, d: Math.random() * Math.PI * 2}));
    const colour = getComputedStyle(document.documentElement).getPropertyValue('--flake').trim() || '#93c5fd';
    const tick = () => {
      if (!canvas) return;
      if (!document.hidden) {
        ctx.clearRect(0, 0, innerWidth, innerHeight);
        ctx.fillStyle = colour; ctx.globalAlpha = .55;
        for (const f of flakes) {
          f.y += f.s; f.d += .01; f.x += Math.sin(f.d) * .3;
          if (f.y > innerHeight + 4) { f.y = -4; f.x = Math.random() * innerWidth; }
          ctx.beginPath(); ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2); ctx.fill();
        }
      }
      frame = requestAnimationFrame(tick);
    };
    tick();
  };
  const stop = () => { cancelAnimationFrame(frame); if (canvas) canvas.remove(); canvas = null; };

  const host = document.querySelector('.topbar-tools') || document.querySelector('.public-top nav');
  if (host && !calm && !exam) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'snow-toggle'; button.textContent = '❄';
    button.title = 'Снегопад'; button.setAttribute('aria-label', 'Снегопад');
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
