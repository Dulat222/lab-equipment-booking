"""LabBook — Laboratory Equipment Booking Service (Project Stage 2 prototype).

Python standard library + SQLite. Roles: student, admin.
Run locally:  python app.py   ->  http://127.0.0.1:8000
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
import datetime as dt, html, json, mimetypes, os, re, sqlite3

from security import hash_password, verify_password, new_token, LoginLimiter

BASE = Path(__file__).resolve().parent
DB = BASE / "lab_equipment.sqlite"
SEMESTER_START = "2026-09-01"
MAX_DAYS = 14
SENSOR_KEY = os.environ.get("SENSOR_API_KEY", "demo-device-key")
limiter = LoginLimiter()
esc = html.escape

# ---- analysis queries (Part C) — the same SQL is used in the report -------------
Q1_BY_CATEGORY = """
SELECT e.category, COUNT(*) AS bookings
FROM Booking b JOIN Equipment e ON e.equipment_id = b.equipment_id
WHERE b.status <> 'Cancelled'
GROUP BY e.category ORDER BY bookings DESC"""
Q2_BY_WEEK = """
SELECT CAST((julianday(b.booking_date) - julianday('2026-09-01')) / 7 AS INTEGER) + 1 AS week,
       COUNT(*) AS bookings
FROM Booking b
WHERE b.status <> 'Cancelled'
GROUP BY week ORDER BY week"""
Q3_LATE_BY_CATEGORY = """
SELECT e.category,
       COUNT(*) AS returned_loans,
       ROUND(AVG(julianday(b.returned_at) - julianday(b.booking_date) + 1), 1) AS avg_days_kept,
       ROUND(100.0 * SUM(CASE WHEN b.returned_at > b.return_date THEN 1 ELSE 0 END) / COUNT(*), 1) AS late_pct
FROM Booking b JOIN Equipment e ON e.equipment_id = b.equipment_id
WHERE b.status = 'Returned'
GROUP BY e.category ORDER BY late_pct DESC"""


def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def today():
    return dt.date.today().isoformat()


def audit(c, action, entity, entity_id, user_id):
    c.execute("INSERT INTO AuditLog(action,entity,entity_id,user_id,timestamp) VALUES(?,?,?,?,datetime('now'))",
              (action, entity, entity_id, user_id))


def equipment_state(c):
    """Equipment list with computed current state: Maintenance / Booked / Available."""
    t = today()
    rows = c.execute("""
      SELECT e.*,
        (SELECT MIN(b.return_date) FROM Booking b WHERE b.equipment_id=e.equipment_id
           AND b.status='Confirmed' AND b.booking_date<=? ) AS busy_until,
        (SELECT power_w FROM SensorReading s WHERE s.equipment_id=e.equipment_id
           ORDER BY recorded_at DESC LIMIT 1) AS power_w
      FROM Equipment e ORDER BY e.equipment_id""", (t,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["state"] = "Maintenance" if r["status"] == "Maintenance" else ("Booked" if r["busy_until"] else "Available")
        out.append(d)
    return out


# ---- HTML layout ------------------------------------------------------------------
def layout(title, body, user=None, message="", error=False):
    nav = ['<a href="/">Catalog</a>']
    if user and user["role"] == "student":
        nav.append('<a href="/my">My bookings</a>')
    if user and user["role"] == "admin":
        nav.append('<a href="/admin">Admin dashboard</a>')
    if user:
        nav.append(f'<span class="who">{esc(user["full_name"])} · {esc(user["role"])}</span>'
                   '<form class="inline" method="post" action="/logout"><button class="link">Log out</button></form>')
    else:
        nav.append('<a href="/login">Log in</a><a class="nav-cta" href="/register">Register</a>')
    notice = (f'<div class="notice{" error" if error else ""}" role="{"alert" if error else "status"}">{esc(message)}</div>'
              if message else "")
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="LabBook: booking service for university laboratory equipment.">
<title>{esc(title)} · LabBook</title>
<link rel="icon" href="/static/images/logo.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/style.css"></head><body>
<header class="top"><a class="brand" href="/"><img src="/static/images/logo.svg" width="54" height="54" alt="LabBook logo: flask in a blue circle">
<div><strong>LabBook</strong><span>Laboratory Equipment Booking Service</span></div></a>
<nav class="nav" aria-label="Main">{"".join(nav)}</nav></header>
<main>{notice}{body}</main>
<footer><img src="/static/images/logo.svg" alt="" width="30" height="30"><span>LabBook · Python + SQLite · Project Stage 2</span>
<span>Demo data is generated · no real personal data</span></footer>
<script src="/static/app.js" defer></script></body></html>'''


def status_badge(state):
    cls = {"Available": "available", "Booked": "booked", "Maintenance": "maintenance"}.get(state, "booked")
    return f'<span class="status {cls}">{esc(state)}</span>'


def catalog_page(user, message="", error=False, form=None):
    form = form or {}
    with db() as c:
        eq = equipment_state(c)
    cards = "".join(f'''<article class="item"><div class="item-top"><span class="icon" aria-hidden="true">◈</span>{status_badge(x["state"])}</div>
<h3>{esc(x["name"])}</h3><p>{esc(x["category"])}</p><small>{esc(x["description"] or "")}</small>
<div class="item-foot">ID {x["equipment_id"]} · {esc(x["location"] or "")}{f' · busy until {esc(x["busy_until"])}' if x["state"] == "Booked" else ""}</div></article>'''
                    for x in eq)
    if user and user["role"] == "student":
        opts = "".join(f'<option value="{x["equipment_id"]}" {"selected" if str(x["equipment_id"]) == form.get("equipment_id") else ""} '
                       f'{"disabled" if x["state"] == "Maintenance" else ""}>{esc(x["name"])} — {esc(x["state"])}</option>' for x in eq)
        booking = f'''<section class="booking-area" id="booking"><div class="booking-copy"><p class="eyebrow">BOOKING</p>
<h2>Plan your lab work</h2><p>Choose equipment and dates. The system checks that nobody else has booked the item for the same days.
You can keep equipment for up to {MAX_DAYS} days.</p><div class="privacy">ⓘ Booking as <b>{esc(user["full_name"])}</b></div></div>
<form action="/book" method="post"><label for="equipment">Equipment</label><select id="equipment" name="equipment_id" required>{opts}</select>
<div class="dates"><div><label for="booking_date">Pick-up date</label><input id="booking_date" name="booking_date" type="date" min="{today()}" value="{esc(form.get("booking_date", ""))}" required></div>
<div><label for="return_date">Return date</label><input id="return_date" name="return_date" type="date" min="{today()}" value="{esc(form.get("return_date", ""))}" required aria-describedby="days-hint"></div></div>
<p class="days-hint" id="days-hint">ⓘ You can keep equipment for <b>at most {MAX_DAYS} days</b>. After you choose the pick-up date, the calendar only offers return dates within this limit.</p>
<button type="submit">Book equipment <span aria-hidden="true">↗</span></button></form></section>'''
    elif user:
        booking = ""
    else:
        booking = '''<section class="booking-area" id="booking"><div class="booking-copy"><p class="eyebrow">BOOKING</p>
<h2>Want to book something?</h2><p>Create a free student account with your university email, then pick equipment and dates.</p></div>
<div class="cta-box"><a class="primary-link" href="/register">Create account</a><a class="secondary" href="/login">I already have an account</a></div></section>'''
    body = f'''<section class="hero"><div><p class="eyebrow">LABORATORY CENTER</p><h1>Equipment for<br><em>your experiments.</em></h1>
<p class="intro">See what is free right now and book it in under a minute — no more paper sign-up sheets.</p>
<a class="primary-link" href="#booking">Book equipment <span aria-hidden="true">↓</span></a></div>
<img class="hero-image" src="/static/images/equipment-dashboard.webp" alt="Illustration of equipment cards and statuses" width="1280" height="720"></section>
{booking}
<section class="section" id="equipment"><div class="section-heading"><div><p class="eyebrow">CATALOG</p><h2>Equipment</h2></div>
<span class="count">{len(eq)} items · {sum(x["state"] == "Available" for x in eq)} available now</span></div><div class="grid">{cards}</div></section>'''
    return layout("Catalog", body, user, message, error)


def auth_page(kind, user=None, message="", error=False, email=""):
    if kind == "login":
        body = f'''<section class="auth"><h1>Log in</h1><form method="post" action="/login">
<label for="email">University email</label><input id="email" name="email" type="email" autocomplete="username" value="{esc(email)}" required>
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Log in</button><p class="form-hint">No account? <a href="/register">Register</a></p></form></section>'''
    else:
        body = f'''<section class="auth"><h1>Create a student account</h1><form method="post" action="/register">
<label for="full_name">Full name</label><input id="full_name" name="full_name" autocomplete="name" required maxlength="80">
<label for="email">University email</label><input id="email" name="email" type="email" autocomplete="email" value="{esc(email)}" required>
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required aria-describedby="pw-hint">
<p class="form-hint" id="pw-hint">At least 8 characters, including a letter and a digit.</p>
<button type="submit">Create account</button><p class="form-hint">Already registered? <a href="/login">Log in</a></p></form></section>'''
    return layout("Log in" if kind == "login" else "Register", body, user, message, error)


def my_page(user, message="", error=False):
    with db() as c:
        rows = c.execute("""SELECT b.*, e.name FROM Booking b JOIN Equipment e USING(equipment_id)
                            WHERE b.user_id=? ORDER BY b.booking_date DESC""", (user["user_id"],)).fetchall()
    t = today()
    trs = []
    for b in rows:
        st = b["status"]
        if st == "Confirmed" and b["return_date"] < t:
            st = "Overdue"
        action = (f'<form class="inline" method="post" action="/cancel"><input type="hidden" name="booking_id" value="{b["booking_id"]}">'
                  f'<button class="link danger">Cancel</button></form>') if b["status"] == "Confirmed" and b["booking_date"] > t else ""
        trs.append(f'<tr><td>#{b["booking_id"]}</td><td>{esc(b["name"])}</td><td>{b["booking_date"]}</td><td>{b["return_date"]}</td>'
                   f'<td><span class="pill {st.lower()}">{st}</span></td><td>{action}</td></tr>')
    body = f'''<section class="section"><div class="section-heading"><div><p class="eyebrow">STUDENT</p><h2>My bookings</h2></div>
<a class="primary-link small" href="/#booking">+ New booking</a></div><div class="table-wrap"><table>
<thead><tr><th>ID</th><th>Equipment</th><th>Pick-up</th><th>Return</th><th>Status</th><th></th></tr></thead>
<tbody>{"".join(trs) or '<tr><td colspan="6">You have no bookings yet. <a href="/#booking">Book equipment</a></td></tr>'}</tbody></table></div></section>'''
    return layout("My bookings", body, user, message, error)


# ---- SVG charts for the admin dashboard (C5) -------------------------------------
def svg_bar(rows, label_key, value_key, title, unit, color="#2563eb", fmt="{:g}"):
    w, row_h, left = 560, 30, 130
    vmax = max([r[value_key] for r in rows] + [1])
    h = row_h * len(rows) + 40
    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">']
    for i, r in enumerate(rows):
        y = 10 + i * row_h
        bw = (w - left - 70) * r[value_key] / vmax
        parts.append(f'<text x="{left - 8}" y="{y + 17}" text-anchor="end" class="lbl">{esc(str(r[label_key]))}</text>'
                     f'<rect x="{left}" y="{y + 4}" width="{bw:.1f}" height="18" rx="3" fill="{color}"/>'
                     f'<text x="{left + bw + 6:.1f}" y="{y + 17}" class="val">{fmt.format(r[value_key])}</text>')
    parts.append(f'<text x="{left}" y="{h - 6}" class="axis">{esc(unit)}</text></svg>')
    return "".join(parts)


def svg_line(rows, title):
    w, h, l, b, t, r = 560, 270, 48, 56, 14, 14
    vmax = max([x["bookings"] for x in rows] + [1])
    vmax = (vmax // 5 + 1) * 5
    n = max(len(rows) - 1, 1)
    X = lambda i: l + (w - l - r) * i / n
    Y = lambda v: t + (h - t - b) * (1 - v / vmax)
    pts = " ".join(f"{X(i):.1f},{Y(x['bookings']):.1f}" for i, x in enumerate(rows))
    p = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">']
    for v in range(0, vmax + 1, 5):
        p.append(f'<line x1="{l}" x2="{w - r}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/><text x="{l - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" class="axis">{v}</text>')
    p.append(f'<polyline points="{pts}" fill="none" stroke="#2563eb" stroke-width="2.5"/>')
    for i, x in enumerate(rows):
        p.append(f'<circle cx="{X(i):.1f}" cy="{Y(x["bookings"]):.1f}" r="3.5" fill="#2563eb"/>'
                 f'<text x="{X(i):.1f}" y="{h - b + 16}" text-anchor="middle" class="axis">{x["week"]}</text>')
    p.append(f'<text x="{(w + l) / 2}" y="{h - 8}" text-anchor="middle" class="axis">Semester week (week 1 starts 1 Sep 2026)</text>'
             f'<text x="12" y="{t + (h - b) / 2}" transform="rotate(-90 12 {t + (h - b) / 2})" text-anchor="middle" class="axis">Bookings</text></svg>')
    return "".join(p)


def iot_status(c):
    """Latest reading per tagged device + alert if item left the cabinet without an active booking."""
    t = today()
    rows = c.execute("""
      SELECT e.equipment_id, e.name, s.device_id, s.power_w, s.in_cabinet, s.recorded_at,
        EXISTS(SELECT 1 FROM Booking b WHERE b.equipment_id=e.equipment_id AND b.status='Confirmed'
               AND b.booking_date<=? AND b.return_date>=?) AS booked_now
      FROM SensorReading s JOIN Equipment e USING(equipment_id)
      WHERE s.reading_id IN (SELECT MAX(reading_id) FROM SensorReading GROUP BY equipment_id)
      ORDER BY e.equipment_id""", (t, t)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if not r["in_cabinet"] and not r["booked_now"]:
            d["alert"], d["level"] = "Removed from cabinet without a booking", "danger"
        elif r["power_w"] is not None and r["power_w"] > 5:
            d["alert"], d["level"] = f"In use · {r['power_w']:.0f} W", "busy"
        elif not r["in_cabinet"]:
            d["alert"], d["level"] = "Checked out (booked)", "ok"
        else:
            d["alert"], d["level"] = "Idle in cabinet", "ok"
        out.append(d)
    return out


def admin_page(user, message="", error=False):
    t = today()
    with db() as c:
        q1 = [dict(r) for r in c.execute(Q1_BY_CATEGORY)]
        q2 = [dict(r) for r in c.execute(Q2_BY_WEEK)]
        q3 = [dict(r) for r in c.execute(Q3_LATE_BY_CATEGORY)]
        k = c.execute("""SELECT
            (SELECT COUNT(*) FROM Booking) total,
            (SELECT COUNT(*) FROM Booking WHERE status='Confirmed' AND booking_date<=? AND return_date>=?) active,
            (SELECT COUNT(*) FROM Booking WHERE status='Confirmed' AND return_date<?) overdue,
            (SELECT COUNT(*) FROM User WHERE role='student') students""", (t, t, t)).fetchone()
        late_all = c.execute("SELECT ROUND(100.0*SUM(returned_at>return_date)/COUNT(*),1) FROM Booking WHERE status='Returned'").fetchone()[0]
        open_b = c.execute("""SELECT b.*, e.name, u.full_name FROM Booking b JOIN Equipment e USING(equipment_id)
             JOIN User u USING(user_id) WHERE b.status='Confirmed' ORDER BY b.return_date""").fetchall()
        eq = equipment_state(c)
        logs = c.execute("SELECT * FROM AuditLog ORDER BY log_id DESC LIMIT 8").fetchall()
        iot = iot_status(c)
    kpis = [("Bookings in database", k["total"]), ("Active today", k["active"]), ("Overdue", k["overdue"]),
            ("Late-return rate", f"{late_all}%"), ("Registered students", k["students"])]
    kpi_html = "".join(f'<div class="kpi"><span>{esc(a)}</span><strong>{b}</strong></div>' for a, b in kpis)
    ob = []
    for b in open_b:
        st = "Overdue" if b["return_date"] < t else ("Active" if b["booking_date"] <= t else "Upcoming")
        ob.append(f'<tr><td>#{b["booking_id"]}</td><td>{esc(b["full_name"])}</td><td>{esc(b["name"])}</td><td>{b["booking_date"]}</td>'
                  f'<td>{b["return_date"]}</td><td><span class="pill {st.lower()}">{st}</span></td><td>'
                  f'<form class="inline" method="post" action="/admin/return"><input type="hidden" name="booking_id" value="{b["booking_id"]}">'
                  f'<button class="link">{"Mark returned" if st != "Upcoming" else "Cancel"}</button></form></td></tr>')
    eqr = "".join(f'<tr><td>{x["equipment_id"]}</td><td>{esc(x["name"])}</td><td>{esc(x["category"])}</td><td>{status_badge(x["state"])}</td><td>'
                  f'<form class="inline" method="post" action="/admin/maintenance"><input type="hidden" name="equipment_id" value="{x["equipment_id"]}">'
                  f'<button class="link">{"End maintenance" if x["status"] == "Maintenance" else "Send to maintenance"}</button></form></td></tr>' for x in eq)
    iot_rows = "".join(f'<tr data-eq="{d["equipment_id"]}"><td>{esc(d["name"])}</td><td><code>{esc(d["device_id"])}</code></td>'
                       f'<td>{d["power_w"]} W</td><td>{"yes" if d["in_cabinet"] else "no"}</td><td>{esc(d["recorded_at"])}</td>'
                       f'<td><span class="pill {d["level"]}">{esc(d["alert"])}</span></td></tr>' for d in iot)
    log_rows = "".join(f'<tr><td>{esc(l["timestamp"])}</td><td>{esc(l["action"])}</td><td>{esc(l["entity"])} #{l["entity_id"]}</td><td>{l["user_id"]}</td></tr>' for l in logs)
    body = f'''<section class="section"><div class="section-heading"><div><p class="eyebrow">ADMIN</p><h2>Lab dashboard</h2></div>
<span class="count">Live from SQLite · {t}</span></div><div class="kpis">{kpi_html}</div></section>

<section class="section" id="stats"><div class="section-heading"><div><p class="eyebrow">DATA ANALYSIS</p><h2>Booking statistics</h2></div>
<span class="count">Source: LabBook DB, generated demo data</span></div>
<div class="charts">
<figure class="card"><figcaption>Bookings by equipment category (Sep–Nov 2026)</figcaption>{svg_bar(q1, "category", "bookings", "Bookings by category", "Number of bookings (excl. cancelled)")}</figure>
<figure class="card"><figcaption>Bookings per semester week</figcaption>{svg_line(q2, "Bookings per week")}</figure>
<figure class="card"><figcaption>Late returns by category</figcaption>{svg_bar(q3, "category", "late_pct", "Late return rate", "Late returns, % of returned loans", "#d97706", "{:g}%")}</figure>
</div></section>

<section class="section" id="iot"><div class="section-heading"><div><p class="eyebrow">SMART TAGS · IOT SIMULATION</p><h2>Equipment sensors</h2></div>
<span class="count" id="iot-updated">auto-refresh every 5 s</span></div>
<div class="table-wrap"><table><thead><tr><th>Equipment</th><th>Device</th><th>Power</th><th>In cabinet</th><th>Last reading</th><th>State</th></tr></thead>
<tbody id="iot-body">{iot_rows or '<tr><td colspan="6">No sensor data yet. Run scripts/iot_simulator.py</td></tr>'}</tbody></table></div></section>

<section class="section" id="open"><div class="section-heading"><div><p class="eyebrow">LOANS</p><h2>Open bookings</h2></div></div>
<div class="table-wrap"><table><thead><tr><th>ID</th><th>Student</th><th>Equipment</th><th>Pick-up</th><th>Return</th><th>State</th><th></th></tr></thead>
<tbody>{"".join(ob) or '<tr><td colspan="7">No open bookings</td></tr>'}</tbody></table></div></section>

<section class="section"><div class="section-heading"><div><p class="eyebrow">INVENTORY</p><h2>Equipment</h2></div></div>
<div class="table-wrap"><table><thead><tr><th>ID</th><th>Name</th><th>Category</th><th>Now</th><th></th></tr></thead><tbody>{eqr}</tbody></table></div></section>

<section class="section"><div class="section-heading"><div><p class="eyebrow">SECURITY</p><h2>Audit log (latest 8)</h2></div></div>
<div class="table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Action</th><th>Entity</th><th>User ID</th></tr></thead><tbody>{log_rows}</tbody></table></div></section>
<script src="/static/admin.js" defer></script>'''
    return layout("Admin dashboard", body, user, message, error)


def error_page(user, code, text):
    return layout(f"{code}", f'<section class="auth"><p class="eyebrow">ERROR {code}</p><h1>{esc(text)}</h1>'
                  f'<p><a class="primary-link small" href="/">Back to catalog</a></p></section>', user)


# ---- HTTP handler -----------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


class Handler(BaseHTTPRequestHandler):
    server_version = "LabBook/2.0"
    sys_version = ""

    # -- helpers
    def https(self):
        return self.headers.get("X-Forwarded-Proto", "") == "https"

    def current_user(self):
        ck = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" not in ck:
            return None
        with db() as c:
            return c.execute("""SELECT u.user_id, u.full_name, u.email, u.role FROM Session s JOIN User u USING(user_id)
                                WHERE s.token=? AND s.expires_at > datetime('now')""", (ck["sid"].value,)).fetchone()

    def form(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n > 10_000:
            return {}
        return {k: v[0].strip() for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}

    def send(self, body, code=200, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                         "font-src https://fonts.gstatic.com; img-src 'self' data:; script-src 'self'; frame-ancestors 'none'")
        if self.https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def redirect(self, to, headers=()):
        self.send_response(303)
        self.send_header("Location", to)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def start_session(self, user_id):
        token = new_token()
        with db() as c:
            c.execute("DELETE FROM Session WHERE expires_at <= datetime('now')")
            c.execute("INSERT INTO Session(token,user_id,expires_at) VALUES(?,?,datetime('now','+8 hours'))", (token, user_id))
        secure = "; Secure" if self.https() else ""
        return ("Set-Cookie", f"sid={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800{secure}")

    def forbidden(self, user):
        with db() as c:
            audit(c, "DENIED", "AdminPage", None, user["user_id"] if user else None)
        self.send(error_page(user, 403, "Forbidden: this page is only for lab administrators."), 403)

    # -- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/static/"):
            target = (BASE / path.lstrip("/")).resolve()
            if not str(target).startswith(str((BASE / "static").resolve())) or not target.is_file():
                return self.send_error(404)
            typ = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            return self.send(target.read_bytes(), ctype=typ, headers=[("Cache-Control", "public, max-age=3600")])
        if path == "/health":
            return self.send('{"ok": true}', ctype="application/json")
        user = self.current_user()
        if path == "/":
            return self.send(catalog_page(user))
        if path in ("/login", "/register"):
            return self.send(auth_page(path[1:], user))
        if path == "/my":
            if not user:
                return self.redirect("/login")
            if user["role"] != "student":
                return self.redirect("/admin")
            return self.send(my_page(user))
        if path == "/admin":
            if not user:
                return self.redirect("/login")
            if user["role"] != "admin":
                return self.forbidden(user)
            return self.send(admin_page(user))
        if path == "/admin/iot.json":
            if not user or user["role"] != "admin":
                return self.send('{"error":"forbidden"}', 403, "application/json")
            with db() as c:
                return self.send(json.dumps(iot_status(c)), ctype="application/json", headers=[("Cache-Control", "no-store")])
        self.send(error_page(user, 404, "Page not found"), 404)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/sensor":
            return self.sensor()
        user = self.current_user()
        f = self.form()
        if path == "/register":
            return self.register(user, f)
        if path == "/login":
            return self.login(user, f)
        if path == "/logout":
            ck = SimpleCookie(self.headers.get("Cookie", ""))
            if "sid" in ck:
                with db() as c:
                    c.execute("DELETE FROM Session WHERE token=?", (ck["sid"].value,))
            return self.redirect("/", [("Set-Cookie", "sid=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")])
        if not user:
            return self.redirect("/login")
        if path == "/book" and user["role"] == "student":
            return self.book(user, f)
        if path == "/cancel" and user["role"] == "student":
            return self.cancel(user, f)
        if path.startswith("/admin/"):
            if user["role"] != "admin":
                return self.forbidden(user)
            return self.admin_action(user, path, f)
        self.send(error_page(user, 404, "Not found"), 404)

    def register(self, user, f):
        name, email, pw = f.get("full_name", ""), f.get("email", "").lower(), f.get("password", "")
        problem = None
        if not (2 <= len(name) <= 80):
            problem = "Please enter your full name (2–80 characters)."
        elif not EMAIL_RE.match(email):
            problem = "Please enter a valid email address, e.g. name@university.kz."
        elif len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
            problem = "Password must be at least 8 characters and contain a letter and a digit."
        if problem:
            return self.send(auth_page("register", user, problem, True, email), 400)
        try:
            with db() as c:
                cur = c.execute("INSERT INTO User(full_name,email,password_hash,role) VALUES(?,?,?,'student')",
                                (name, email, hash_password(pw)))
                uid = cur.lastrowid
                audit(c, "REGISTER", "User", uid, uid)
        except sqlite3.IntegrityError:
            return self.send(auth_page("register", user, "An account with this email already exists. Try logging in.", True, email), 400)
        self.redirect("/#booking", [self.start_session(uid)])

    def login(self, user, f):
        email, pw = f.get("email", "").lower(), f.get("password", "")
        if limiter.locked(email):
            return self.send(auth_page("login", user, "Too many failed attempts. Try again in 5 minutes.", True, email), 429)
        with db() as c:
            row = c.execute("SELECT * FROM User WHERE email=?", (email,)).fetchone()
            ok = row is not None and verify_password(pw, row["password_hash"])
            audit(c, "LOGIN" if ok else "LOGIN_FAILED", "User", row["user_id"] if row else None, row["user_id"] if row else None)
        if not ok:
            limiter.fail(email)
            return self.send(auth_page("login", user, "Wrong email or password.", True, email), 401)
        limiter.reset(email)
        self.redirect("/admin" if row["role"] == "admin" else "/#booking", [self.start_session(row["user_id"])])

    def book(self, user, f):
        try:
            eid = int(f.get("equipment_id", "0"))
            ds = dt.date.fromisoformat(f.get("booking_date", ""))
            de = dt.date.fromisoformat(f.get("return_date", ""))
        except ValueError:
            return self.send(catalog_page(user, "Please choose equipment and both dates.", True, f), 400)
        problem = None
        if ds < dt.date.today():
            problem = "Pick-up date cannot be in the past."
        elif de < ds:
            problem = "Return date cannot be earlier than the pick-up date."
        elif (de - ds).days + 1 > MAX_DAYS:
            problem = f"You can book equipment for at most {MAX_DAYS} days."
        with db() as c:
            eq = c.execute("SELECT * FROM Equipment WHERE equipment_id=?", (eid,)).fetchone()
            if not problem and (not eq or eq["status"] == "Maintenance"):
                problem = "This equipment is under maintenance. Please choose another item."
            if not problem:
                clash = c.execute("""SELECT booking_date, return_date FROM Booking WHERE equipment_id=? AND status='Confirmed'
                                     AND booking_date<=? AND (return_date>=? OR return_date<?) ORDER BY booking_date LIMIT 1""",
                                  (eid, de.isoformat(), ds.isoformat(), today())).fetchone()  # overdue loans also block
                if clash:
                    problem = (f"{eq['name']} is already booked from {clash['booking_date']} to {clash['return_date']}. "
                               "Choose other dates or another item.")
            if not problem:
                cur = c.execute("INSERT INTO Booking(user_id,equipment_id,booking_date,return_date,status) VALUES(?,?,?,?,'Confirmed')",
                                (user["user_id"], eid, ds.isoformat(), de.isoformat()))
                audit(c, "CREATE", "Booking", cur.lastrowid, user["user_id"])
                bid = cur.lastrowid
        if problem:
            return self.send(catalog_page(user, problem, True, f), 400)
        self.send(my_page(user, f"Booking #{bid} confirmed: {eq['name']}, {ds} – {de}. Pick it up at {eq['location']}."))

    def cancel(self, user, f):
        with db() as c:
            n = c.execute("""UPDATE Booking SET status='Cancelled' WHERE booking_id=? AND user_id=? AND status='Confirmed'
                             AND booking_date>?""", (f.get("booking_id"), user["user_id"], today())).rowcount
            if n:
                audit(c, "CANCEL", "Booking", int(f.get("booking_id")), user["user_id"])
        self.send(my_page(user, "Booking cancelled." if n else "This booking cannot be cancelled.", not n))

    def admin_action(self, user, path, f):
        with db() as c:
            if path == "/admin/return":
                b = c.execute("SELECT * FROM Booking WHERE booking_id=? AND status='Confirmed'", (f.get("booking_id"),)).fetchone()
                if not b:
                    msg, err = "Booking not found or already closed.", True
                elif b["booking_date"] > today():
                    c.execute("UPDATE Booking SET status='Cancelled' WHERE booking_id=?", (b["booking_id"],))
                    audit(c, "CANCEL", "Booking", b["booking_id"], user["user_id"])
                    msg, err = f"Booking #{b['booking_id']} cancelled.", False
                else:
                    c.execute("UPDATE Booking SET status='Returned', returned_at=? WHERE booking_id=?", (today(), b["booking_id"]))
                    audit(c, "RETURN", "Booking", b["booking_id"], user["user_id"])
                    msg, err = f"Booking #{b['booking_id']} marked as returned.", False
            elif path == "/admin/maintenance":
                e = c.execute("SELECT * FROM Equipment WHERE equipment_id=?", (f.get("equipment_id"),)).fetchone()
                if not e:
                    msg, err = "Equipment not found.", True
                else:
                    new = "Available" if e["status"] == "Maintenance" else "Maintenance"
                    c.execute("UPDATE Equipment SET status=? WHERE equipment_id=?", (new, e["equipment_id"]))
                    audit(c, "UPDATE", "Equipment", e["equipment_id"], user["user_id"])
                    msg, err = f"{e['name']}: status set to {new}.", False
            else:
                return self.send(error_page(user, 404, "Not found"), 404)
        self.send(admin_page(user, msg, err), 400 if err else 200)

    def sensor(self):
        """IoT endpoint: smart tag sends JSON {equipment_id, device_id, power_w, in_cabinet} with X-Device-Key."""
        if self.headers.get("X-Device-Key") != SENSOR_KEY:
            return self.send('{"error":"invalid device key"}', 401, "application/json")
        try:
            n = int(self.headers.get("Content-Length", "0"))
            d = json.loads(self.rfile.read(min(n, 4096)))
            vals = (int(d["equipment_id"]), str(d["device_id"])[:40], float(d["power_w"]), 1 if d["in_cabinet"] else 0)
        except (ValueError, KeyError, TypeError):
            return self.send('{"error":"bad payload"}', 400, "application/json")
        with db() as c:
            if not c.execute("SELECT 1 FROM Equipment WHERE equipment_id=?", (vals[0],)).fetchone():
                return self.send('{"error":"unknown equipment"}', 404, "application/json")
            c.execute("INSERT INTO SensorReading(equipment_id,device_id,power_w,in_cabinet,recorded_at) VALUES(?,?,?,?,datetime('now'))", vals)
        self.send('{"ok":true}', 201, "application/json")

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))


def ensure_db():
    if not DB.exists():
        import subprocess, sys
        subprocess.run([sys.executable, str(BASE / "scripts" / "seed_db.py")], check=True)


if __name__ == "__main__":
    ensure_db()
    port = int(os.environ.get("PORT", "8000"))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    print(f"LabBook running at http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
