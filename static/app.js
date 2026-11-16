
(() => {
  const button = document.getElementById('soundBtn');
  const audio = document.getElementById('notify');

  if (!button) return;

  button.addEventListener('click', async () => {
    try {
      if (audio) {
        audio.currentTime = 0;
        await audio.play();
        return;
      }
    } catch (error) {
      // Если аудиофайлов нет, используем сигнал браузера.
    }

    try {
      const AudioContextClass =
        window.AudioContext || window.webkitAudioContext;

      if (!AudioContextClass) {
        alert('Браузер не поддерживает воспроизведение звука.');
        return;
      }

      const context = new AudioContextClass();
      if (context.state === 'suspended') {
        await context.resume();
      }

      const oscillator = context.createOscillator();
      const volume = context.createGain();

      oscillator.type = 'sine';
      oscillator.frequency.value = 880;

      volume.gain.setValueAtTime(0.001, context.currentTime);
      volume.gain.exponentialRampToValueAtTime(
        0.15, context.currentTime + 0.03
      );
      volume.gain.exponentialRampToValueAtTime(
        0.001, context.currentTime + 0.35
      );

      oscillator.connect(volume);
      volume.connect(context.destination);

      oscillator.start();
      oscillator.stop(context.currentTime + 0.36);
      oscillator.addEventListener('ended', () => context.close());
    } catch (error) {
      alert('Не удалось воспроизвести звук. Проверь громкость браузера.');
    }
  });
})();

// Usability fix after user testing (B2): limit the return-date picker to 14 days after pick-up.
(() => {
  const start = document.getElementById('booking_date');
  const end = document.getElementById('return_date');
  if (!start || !end) return;
  const MAX_DAYS = 14;
  const iso = (d) => d.toISOString().slice(0, 10);
  start.addEventListener('change', () => {
    if (!start.value) return;
    const s = new Date(start.value + 'T00:00:00Z');
    const max = new Date(s.getTime() + (MAX_DAYS - 1) * 86400000);
    end.min = start.value;
    end.max = iso(max);
    if (end.value && (end.value < end.min || end.value > end.max)) end.value = '';
  });
})();

// Catalog search and filters (name/description, category, status) — runs in the browser, no reload.
(() => {
  const q = document.getElementById('f-q');
  const cat = document.getElementById('f-cat');
  const st = document.getElementById('f-state');
  const count = document.getElementById('f-count');
  const empty = document.getElementById('f-empty');
  const cards = [...document.querySelectorAll('#catalog-grid .item')];
  if (!q || !cards.length) return;
  function apply() {
    const words = q.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    cards.forEach((c) => {
      const tokens = c.dataset.name.split(/[^a-z0-9]+/);
      const ok = words.every((w) => tokens.some((t) => t.startsWith(w))) && // word-start match: "osc" finds oscilloscopes, not microscopes
        (!cat.value || c.dataset.category === cat.value) &&
        (!st.value || c.dataset.state === st.value);
      c.hidden = !ok;
      if (ok) shown += 1;
    });
    count.textContent = `${shown} of ${cards.length} shown`;
    empty.hidden = shown > 0;
  }
  [q, cat, st].forEach((el) => el.addEventListener('input', apply));
  apply();
})();

// "Check dates & book" on a card: choose the item in the booking form and scroll to it.
(() => {
  const select = document.getElementById('equipment');
  if (!select) return;
  document.querySelectorAll('.book-this').forEach((b) => b.addEventListener('click', () => {
    select.value = b.dataset.id;
    select.dispatchEvent(new Event('change'));
    document.getElementById('booking').scrollIntoView({ behavior: 'smooth' });
  }));
})();

// R3: availability calendar for the chosen item. Click a free day = pick-up, click again = return.
(() => {
  const box = document.getElementById('avail');
  const select = document.getElementById('equipment');
  const start = document.getElementById('booking_date');
  const end = document.getElementById('return_date');
  if (!box || !select || !start || !end) return;
  const pad = (n) => String(n).padStart(2, '0');
  const iso = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  const addDays = (s, n) => iso(new Date(Date.parse(s + 'T00:00:00Z') + n * 86400000));
  let data = null;
  let month = null; // first day of the shown month, ISO

  function busyOn(day) {
    if (!data) return null;
    if (data.maintenance) return { type: 'blocked', label: 'Under maintenance' };
    return data.busy.find((b) => b.start <= day && day <= b.end) || null;
  }
  function rangeFree(a, b) {
    for (let d = a; d <= b; d = addDays(d, 1)) if (busyOn(d)) return false;
    return true;
  }
  function render() {
    if (!data) { box.innerHTML = ''; return; }
    const m0 = new Date(month + 'T00:00:00Z');
    const title = m0.toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    const first = (m0.getUTCDay() + 6) % 7; // Monday first
    const days = new Date(Date.UTC(m0.getUTCFullYear(), m0.getUTCMonth() + 1, 0)).getUTCDate();
    let cells = '';
    for (let i = 0; i < first; i += 1) cells += '<span></span>';
    for (let d = 1; d <= days; d += 1) {
      const day = `${month.slice(0, 8)}${pad(d)}`;
      const b = busyOn(day);
      const past = day < data.today;
      const sel = start.value && day >= start.value && day <= (end.value || start.value);
      const cls = past ? 'past' : b ? b.type : 'free';
      const tip = past ? 'Past' : b ? b.label : 'Free';
      cells += `<button type="button" class="day ${cls}${sel ? ' sel' : ''}${day === data.today ? ' today' : ''}" data-day="${day}" ` +
        `${past || b ? 'disabled' : ''} title="${day}: ${tip}" aria-label="${day}: ${tip}">${d}</button>`;
    }
    const thisMonth = data.today.slice(0, 8) + '01';
    box.innerHTML = `<div class="cal"><div class="cal-head">
      <button type="button" class="cal-nav" data-step="-1" ${month <= thisMonth ? 'disabled' : ''} aria-label="Previous month">‹</button>
      <strong>${title}</strong>
      <button type="button" class="cal-nav" data-step="1" aria-label="Next month">›</button></div>
      <div class="cal-grid"><i>Mo</i><i>Tu</i><i>We</i><i>Th</i><i>Fr</i><i>Sa</i><i>Su</i>${cells}</div>
      <div class="cal-legend"><span class="k free"></span>Free <span class="k booked"></span>Booked
      <span class="k blocked"></span>Reserved by lab <span class="k overdue"></span>Not returned yet <span class="k sel"></span>Your dates</div>
      <p class="cal-tip">Click a free day for pick-up, then another for return (max ${data.max_days} days).</p></div>`;
  }
  async function load() {
    box.innerHTML = '<p class="cal-tip">Loading availability…</p>';
    try {
      const res = await fetch('/api/availability?equipment_id=' + encodeURIComponent(select.value), { credentials: 'same-origin', cache: 'no-store' });
      data = res.ok ? await res.json() : null;
    } catch (e) { data = null; }
    if (data && !month) month = data.today.slice(0, 8) + '01';
    if (!data) box.innerHTML = '<p class="cal-tip">Availability could not be loaded. You can still pick dates below.</p>';
    else render();
  }
  box.addEventListener('click', (ev) => {
    const nav = ev.target.closest('.cal-nav');
    if (nav) {
      const m = new Date(month + 'T00:00:00Z');
      m.setUTCMonth(m.getUTCMonth() + Number(nav.dataset.step));
      month = iso(m);
      render();
      return;
    }
    const cell = ev.target.closest('.day.free');
    if (!cell) return;
    const day = cell.dataset.day;
    const limit = start.value ? addDays(start.value, data.max_days - 1) : null;
    if (start.value && !end.value && day >= start.value && day <= limit && rangeFree(start.value, day)) {
      end.value = day;
    } else {
      start.value = day;
      end.value = '';
      start.dispatchEvent(new Event('change')); // applies the 14-day limit to the return picker
    }
    render();
  });
  select.addEventListener('change', () => { load(); });
  [start, end].forEach((el) => el.addEventListener('change', render));
  load();
})();
