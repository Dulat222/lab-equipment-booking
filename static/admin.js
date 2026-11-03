// Admin dashboard: refresh the IoT sensor table every 5 seconds without reloading the page.
(() => {
  const body = document.getElementById('iot-body');
  const stamp = document.getElementById('iot-updated');
  if (!body) return;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let last = {};

  async function refresh() {
    try {
      const res = await fetch('/admin/iot.json', { credentials: 'same-origin', cache: 'no-store' });
      if (!res.ok) return;
      const rows = await res.json();
      body.innerHTML = rows.map((d) => {
        const changed = last[d.equipment_id] && last[d.equipment_id] !== d.recorded_at;
        return `<tr data-eq="${d.equipment_id}" class="${changed ? 'flash' : ''}"><td>${esc(d.name)}</td>` +
          `<td><code>${esc(d.device_id)}</code></td><td>${d.power_w} W</td><td>${d.in_cabinet ? 'yes' : 'no'}</td>` +
          `<td>${esc(d.recorded_at)}</td><td><span class="pill ${d.level}">${esc(d.alert)}</span></td></tr>`;
      }).join('') || '<tr><td colspan="6">No sensor data yet. Run scripts/iot_simulator.py</td></tr>';
      last = Object.fromEntries(rows.map((d) => [d.equipment_id, d.recorded_at]));
      if (stamp) stamp.textContent = 'updated ' + new Date().toLocaleTimeString();
    } catch (e) { /* network hiccup: try again next tick */ }
  }
  refresh();
  setInterval(refresh, 5000);
})();
