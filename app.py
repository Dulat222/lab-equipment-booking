"""LabBook — Laboratory Equipment Booking Service (ICT Final Project, final version).

Python standard library + SQLite. Roles: student, admin.
Run locally:  python app.py   ->  http://127.0.0.1:8000
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
import csv, datetime as dt, html, io, json, mimetypes, os, re, sqlite3

from security import hash_password, verify_password, new_token, LoginLimiter, csrf_for_session, csrf_equal

BASE = Path(__file__).resolve().parent
DB = BASE / "lab_equipment.sqlite"
SEMESTER_START = "2026-09-01"
MAX_DAYS = 14
SENSOR_KEY = os.environ.get("SENSOR_API_KEY", "demo-device-key")
# R1: only university addresses may register (comma-separated list, can be changed without code edits)
UNI_DOMAINS = tuple(d.strip().lower() for d in os.environ.get("UNI_EMAIL_DOMAINS", "astanait.edu.kz").split(",") if d.strip())

# R8: dates an administrator closes for an item (lab session, calibration...). Created at start-up if missing.
BLOCK_SCHEMA = """
CREATE TABLE IF NOT EXISTS Block(
  block_id INTEGER PRIMARY KEY AUTOINCREMENT,
  equipment_id INTEGER NOT NULL REFERENCES Equipment(equipment_id),
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  created_by INTEGER REFERENCES User(user_id),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK(end_date >= start_date));
CREATE INDEX IF NOT EXISTS idx_block_equipment_dates ON Block(equipment_id, start_date, end_date);
"""
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
           ORDER BY recorded_at DESC LIMIT 1) AS power_w,
        (SELECT k.end_date FROM Block k WHERE k.equipment_id=e.equipment_id
           AND k.start_date<=? AND k.end_date>=? ORDER BY k.end_date DESC LIMIT 1) AS reserved_until
      FROM Equipment e ORDER BY e.equipment_id""", (t, t, t)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if r["status"] == "Maintenance":
            d["state"] = "Maintenance"
        elif r["busy_until"]:
            d["state"] = "Booked"
        elif r["reserved_until"]:
            d["state"] = "Reserved"
        else:
            d["state"] = "Available"
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
<footer><img src="/static/images/logo.svg" alt="" width="30" height="30"><span>LabBook · Python + SQLite · ICT Final Project 2026</span>
<span>Demo data is generated · no real personal data</span></footer>
<script src="/static/app.js" defer></script></body></html>'''


def status_badge(state):
    cls = {"Available": "available", "Booked": "booked", "Maintenance": "maintenance", "Reserved": "reserved"}.get(state, "booked")
    return f'<span class="status {cls}">{esc(state)}</span>'


def student_reminders(c, user_id):
    """R7: on-site reminders about the student's own loans (no e-mail service needed)."""
    t = dt.date.today()
    tm = t + dt.timedelta(days=1)
    rows = c.execute("""SELECT b.booking_date, b.return_date, e.name, e.location FROM Booking b JOIN Equipment e USING(equipment_id)
                        WHERE b.user_id=? AND b.status='Confirmed' AND b.booking_date<=? ORDER BY b.return_date""",
                     (user_id, tm.isoformat())).fetchall()
    out = []
    for r in rows:
        ret, pick = dt.date.fromisoformat(r["return_date"]), dt.date.fromisoformat(r["booking_date"])
        name = esc(r["name"])
        if ret < t:
            n = (t - ret).days
            out.append(("overdue", f"<b>{name}</b> is overdue: it was due on {ret} ({n} day{'s' if n != 1 else ''} ago). Please return it to the lab."))
        elif pick > t:
            out.append(("info", f"Pick up <b>{name}</b> tomorrow ({pick}) at {esc(r['location'] or 'the lab')}."))
        elif pick == t and ret >= t and r["booking_date"] == t.isoformat():
            out.append(("info", f"Your booking of <b>{name}</b> starts today. Pick it up at {esc(r['location'] or 'the lab')}."))
        if ret == t:
            out.append(("due", f"Return <b>{name}</b> today."))
        elif ret == tm:
            out.append(("due", f"Return <b>{name}</b> tomorrow ({ret})."))
    return out


def reminders_html(items, title="Reminders"):
    if not items:
        return ""
    lis = "".join(f'<li class="rem {lvl}">{txt}</li>' for lvl, txt in items)
    return f'<section class="reminders" aria-label="{esc(title)}"><p class="eyebrow">{esc(title.upper())}</p><ul>{lis}</ul></section>'


def catalog_page(user, message="", error=False, form=None):
    form = form or {}
    with db() as c:
        eq = equipment_state(c)
        rem = reminders_html(student_reminders(c, user["user_id"])) if user and user["role"] == "student" else ""
    is_student = bool(user and user["role"] == "student")
    cards = "".join(f'''<article class="item" data-name="{esc(x["name"].lower())} {esc((x["description"] or "").lower())}" data-category="{esc(x["category"])}" data-state="{x["state"]}">
<div class="item-top"><span class="icon" aria-hidden="true">◈</span>{status_badge(x["state"])}</div>
<h3>{esc(x["name"])}</h3><p>{esc(x["category"])}</p><small>{esc(x["description"] or "")}</small>
<div class="item-foot">ID {x["equipment_id"]} · {esc(x["location"] or "")}{f' · busy until {esc(x["busy_until"])}' if x["state"] == "Booked" else ""}{f' · reserved by the lab until {esc(x["reserved_until"])}' if x["state"] == "Reserved" else ""}</div>
{f'<button type="button" class="link book-this" data-id="{x["equipment_id"]}">Check dates &amp; book</button>' if is_student and x["state"] != "Maintenance" else ""}</article>'''
                    for x in eq)
    cats = sorted({x["category"] for x in eq})
    filters = ('<div class="filters" role="search"><input id="f-q" type="search" placeholder="Search by name or description" aria-label="Search equipment">'
               '<select id="f-cat" aria-label="Filter by category"><option value="">All categories</option>'
               + "".join(f'<option>{esc(cx)}</option>' for cx in cats) +
               '</select><select id="f-state" aria-label="Filter by status"><option value="">Any status</option>'
               '<option>Available</option><option>Booked</option><option>Reserved</option><option>Maintenance</option></select>'
               '<span class="count" id="f-count" aria-live="polite"></span></div>'
               '<p class="empty" id="f-empty" hidden>No equipment matches your search. Clear the filters to see all items.</p>')
    if user and user["role"] == "student":
        opts = "".join(f'<option value="{x["equipment_id"]}" {"selected" if str(x["equipment_id"]) == form.get("equipment_id") else ""} '
                       f'{"disabled" if x["state"] == "Maintenance" else ""}>{esc(x["name"])} — {esc(x["state"])}</option>' for x in eq)
        booking = f'''<section class="booking-area" id="booking"><div class="booking-copy"><p class="eyebrow">BOOKING</p>
<h2>Plan your lab work</h2><p>Choose equipment and dates. The system checks that nobody else has booked the item for the same days.
You can keep equipment for up to {MAX_DAYS} days.</p><div class="privacy">ⓘ Booking as <b>{esc(user["full_name"])}</b></div></div>
<form action="/book" method="post"><label for="equipment">Equipment</label><select id="equipment" name="equipment_id" required>{opts}</select>
<div id="avail" class="avail" aria-live="polite"></div>
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
{rem}{booking}
<section class="section" id="catalog"><div class="section-heading"><div><p class="eyebrow">CATALOG</p><h2>Equipment</h2></div>
<span class="count">{len(eq)} items · {sum(x["state"] == "Available" for x in eq)} available now</span></div>{filters}<div class="grid" id="catalog-grid">{cards}</div></section>'''
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
<label for="email">University email</label><input id="email" name="email" type="email" autocomplete="email" value="{esc(email)}" placeholder="name@{UNI_DOMAINS[0]}" required aria-describedby="email-hint">
<p class="form-hint field-hint" id="email-hint">Only university addresses (@{", @".join(UNI_DOMAINS)}) can register.</p>
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required aria-describedby="pw-hint">
<p class="form-hint" id="pw-hint">At least 8 characters, including a letter and a digit.</p>
<button type="submit">Create account</button><p class="form-hint">Already registered? <a href="/login">Log in</a></p></form></section>'''
    return layout("Log in" if kind == "login" else "Register", body, user, message, error)


def my_page(user, message="", error=False):
    with db() as c:
        rows = c.execute("""SELECT b.*, e.name FROM Booking b JOIN Equipment e USING(equipment_id)
                            WHERE b.user_id=? ORDER BY b.booking_date DESC""", (user["user_id"],)).fetchall()
        rem = reminders_html(student_reminders(c, user["user_id"]))
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
    body = rem + f'''<section class="section"><div class="section-heading"><div><p class="eyebrow">STUDENT</p><h2>My bookings</h2></div>
<a class="primary-link small" href="/#booking">+ New booking</a></div><div class="table-wrap"><table>
<thead><tr><th>ID</th><th>Equipment</th><th>Pick-up</th><th>Return</th><th>Status</th><th></th></tr></thead>
<tbody>{"".join(trs) or '<tr><td colspan="6">You have no bookings yet. <a href="/#booking">Book equipment</a></td></tr>'}</tbody></table></div></section>'''
    return layout("My bookings", body, user, message, error)


# ---- SVG charts for the admin dashboard (C5) -------------------------------------
def svg_bar(rows, label_key, value_key, title, unit, color="#1385e5", fmt="{:g}"):
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
    p.append(f'<polyline points="{pts}" fill="none" stroke="#1a3a5c" stroke-width="2.5"/>')
    for i, x in enumerate(rows):
        p.append(f'<circle cx="{X(i):.1f}" cy="{Y(x["bookings"]):.1f}" r="4" fill="#1385e5"/>'
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
        tm = (dt.date.fromisoformat(t) + dt.timedelta(days=1)).isoformat()
        due = c.execute("""SELECT b.booking_id, b.return_date, e.name, u.full_name FROM Booking b JOIN Equipment e USING(equipment_id)
             JOIN User u USING(user_id) WHERE b.status='Confirmed' AND b.return_date IN (?,?) ORDER BY b.return_date""", (t, tm)).fetchall()
        blocks = c.execute("""SELECT k.*, e.name FROM Block k JOIN Equipment e USING(equipment_id)
             WHERE k.end_date>=? ORDER BY k.start_date""", (t,)).fetchall()
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
                  f'<button class="link">{"End maintenance" if x["status"] == "Maintenance" else "Send to maintenance"}</button></form> '
                  f'<a class="link-btn" href="/admin/equipment?id={x["equipment_id"]}">Edit</a></td></tr>' for x in eq)
    iot_rows = "".join(f'<tr data-eq="{d["equipment_id"]}"><td>{esc(d["name"])}</td><td><code>{esc(d["device_id"])}</code></td>'
                       f'<td>{d["power_w"]} W</td><td>{"yes" if d["in_cabinet"] else "no"}</td><td>{esc(d["recorded_at"])}</td>'
                       f'<td><span class="pill {d["level"]}">{esc(d["alert"])}</span></td></tr>' for d in iot)
    rem_items = [("overdue", f"<b>{k['overdue']}</b> overdue loan{'s' if k['overdue'] != 1 else ''} — see Open bookings below.")] if k["overdue"] else []
    rem_items += [("due", f"#{d['booking_id']} <b>{esc(d['name'])}</b> — {esc(d['full_name'])} returns it "
                          f"{'today' if d['return_date'] == t else 'tomorrow'} ({d['return_date']}).") for d in due]
    cats = sorted({x["category"] for x in eq})
    eq_opts = "".join(f'<option value="{x["equipment_id"]}">{esc(x["name"])}</option>' for x in eq)
    block_rows = "".join(f'<tr><td>{esc(b["name"])}</td><td>{b["start_date"]}</td><td>{b["end_date"]}</td><td>{esc(b["reason"])}</td><td>'
                         f'<form class="inline" method="post" action="/admin/unblock"><input type="hidden" name="block_id" value="{b["block_id"]}">'
                         f'<button class="link danger">Remove</button></form></td></tr>' for b in blocks)
    log_rows = "".join(f'<tr><td>{esc(l["timestamp"])}</td><td>{esc(l["action"])}</td><td>{esc(l["entity"])} #{l["entity_id"]}</td><td>{l["user_id"]}</td></tr>' for l in logs)
    body = f'''<section class="section"><div class="section-heading"><div><p class="eyebrow">ADMIN</p><h2>Lab dashboard</h2></div>
<span class="count">Live from SQLite · {t}</span></div><div class="kpis">{kpi_html}</div></section>
{reminders_html(rem_items, "Return reminders")}

<section class="section" id="stats"><div class="section-heading"><div><p class="eyebrow">DATA ANALYSIS</p><h2>Booking statistics</h2></div>
<div class="btn-row"><a class="secondary" href="/admin/export/stats.csv" download>⬇ Statistics CSV</a>
<a class="secondary" href="/admin/export/bookings.csv" download>⬇ All bookings CSV</a></div></div>
<div class="charts">
<figure class="card"><figcaption>Bookings by equipment category (Sep–Nov 2026)</figcaption>{svg_bar(q1, "category", "bookings", "Bookings by category", "Number of bookings (excl. cancelled)")}</figure>
<figure class="card"><figcaption>Bookings per semester week</figcaption>{svg_line(q2, "Bookings per week")}</figure>
<figure class="card"><figcaption>Late returns by category</figcaption>{svg_bar(q3, "category", "late_pct", "Late return rate", "Late returns, % of returned loans", "#1a3a5c", "{:g}%")}</figure>
</div></section>

<section class="section" id="iot"><div class="section-heading"><div><p class="eyebrow">SMART TAGS · IOT SIMULATION</p><h2>Equipment sensors</h2></div>
<span class="count" id="iot-updated">auto-refresh every 5 s</span></div>
<div class="table-wrap"><table><thead><tr><th>Equipment</th><th>Device</th><th>Power</th><th>In cabinet</th><th>Last reading</th><th>State</th></tr></thead>
<tbody id="iot-body">{iot_rows or '<tr><td colspan="6">No sensor data yet. Run scripts/iot_simulator.py</td></tr>'}</tbody></table></div></section>

<section class="section" id="open"><div class="section-heading"><div><p class="eyebrow">LOANS</p><h2>Open bookings</h2></div></div>
<div class="table-wrap"><table><thead><tr><th>ID</th><th>Student</th><th>Equipment</th><th>Pick-up</th><th>Return</th><th>State</th><th></th></tr></thead>
<tbody>{"".join(ob) or '<tr><td colspan="7">No open bookings</td></tr>'}</tbody></table></div></section>

<section class="section" id="inventory"><div class="section-heading"><div><p class="eyebrow">INVENTORY</p><h2>Equipment</h2></div></div>
<div class="table-wrap"><table><thead><tr><th>ID</th><th>Name</th><th>Category</th><th>Now</th><th></th></tr></thead><tbody>{eqr}</tbody></table></div>
<details class="panel"><summary>+ Add new equipment</summary>{equipment_form(None, cats)}</details></section>

<section class="section" id="blocks"><div class="section-heading"><div><p class="eyebrow">LAB RESERVATIONS</p><h2>Blocked dates</h2></div>
<span class="count">Students cannot book an item on these dates</span></div>
<div class="table-wrap"><table><thead><tr><th>Equipment</th><th>From</th><th>To</th><th>Reason</th><th></th></tr></thead>
<tbody>{block_rows or '<tr><td colspan="5">No blocked dates.</td></tr>'}</tbody></table></div>
<details class="panel"><summary>+ Block dates for an item</summary>
<form method="post" action="/admin/block" class="form-grid"><div><label for="blk-eq">Equipment</label><select id="blk-eq" name="equipment_id" required>{eq_opts}</select></div>
<div><label for="blk-reason">Reason (shown to students)</label><input id="blk-reason" name="reason" maxlength="80" placeholder="e.g. Physics lab session"></div>
<div><label for="blk-from">From</label><input id="blk-from" name="start_date" type="date" min="{t}" required></div>
<div><label for="blk-to">To</label><input id="blk-to" name="end_date" type="date" min="{t}" required></div>
<div class="span2"><button type="submit">Block these dates</button></div></form></details></section>

<section class="section"><div class="section-heading"><div><p class="eyebrow">SECURITY</p><h2>Audit log (latest 8)</h2></div></div>
<div class="table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Action</th><th>Entity</th><th>User ID</th></tr></thead><tbody>{log_rows}</tbody></table></div></section>
<script src="/static/admin.js" defer></script>'''
    return layout("Admin dashboard", body, user, message, error)


def equipment_form(e, cats):
    """R6: create (e is None) or edit equipment."""
    v = (lambda k: esc(str(e[k] or ""))) if e else (lambda k: "")
    hid = f'<input type="hidden" name="equipment_id" value="{e["equipment_id"]}">' if e else ""
    cat_list = "".join(f'<option value="{esc(cx)}">' for cx in cats)
    return f'''<form method="post" action="/admin/equipment" class="form-grid">{hid}
<div><label for="eq-name{v("equipment_id")}">Name</label><input id="eq-name{v("equipment_id")}" name="name" value="{v("name")}" minlength="2" maxlength="80" required></div>
<div><label for="eq-cat{v("equipment_id")}">Category</label><input id="eq-cat{v("equipment_id")}" name="category" value="{v("category")}" list="cat-list" minlength="2" maxlength="40" required>
<datalist id="cat-list">{cat_list}</datalist></div>
<div class="span2"><label for="eq-desc{v("equipment_id")}">Description</label><input id="eq-desc{v("equipment_id")}" name="description" value="{v("description")}" maxlength="200"></div>
<div><label for="eq-loc{v("equipment_id")}">Location</label><input id="eq-loc{v("equipment_id")}" name="location" value="{v("location") or "Lab C1.2"}" maxlength="40"></div>
<div class="align-end"><button type="submit">{"Save changes" if e else "Add equipment"}</button></div></form>'''


def equipment_edit_page(user, eid):
    with db() as c:
        e = c.execute("SELECT * FROM Equipment WHERE equipment_id=?", (eid,)).fetchone()
        cats = sorted({r[0] for r in c.execute("SELECT DISTINCT category FROM Equipment")})
    if not e:
        return None
    body = f'''<section class="section"><div class="section-heading"><div><p class="eyebrow">INVENTORY · ID {e["equipment_id"]}</p>
<h2>Edit {esc(e["name"])}</h2></div><a class="secondary" href="/admin#inventory">← Back to dashboard</a></div>
{equipment_form(e, cats)}</section>'''
    return layout("Edit equipment", body, user)


def error_page(user, code, text):
    return layout(f"{code}", f'<section class="auth"><p class="eyebrow">ERROR {code}</p><h1>{esc(text)}</h1>'
                  f'<p><a class="primary-link small" href="/">Back to catalog</a></p></section>', user)


def availability(eid):
    """R3: busy date ranges for one item (no student names) for the booking calendar."""
    t = today()
    with db() as c:
        e = c.execute("SELECT equipment_id, name, status FROM Equipment WHERE equipment_id=?", (eid,)).fetchone()
        if not e:
            return {"error": "unknown equipment"}
        busy = []
        for b in c.execute("""SELECT booking_date, return_date FROM Booking WHERE equipment_id=? AND status='Confirmed'
                              ORDER BY booking_date""", (eid,)):
            if b["return_date"] < t:   # overdue: blocks every date from pick-up until it is returned
                busy.append({"start": b["booking_date"], "end": "9999-12-31", "type": "overdue", "label": "Not returned yet"})
            else:
                busy.append({"start": b["booking_date"], "end": b["return_date"], "type": "booked", "label": "Booked"})
        for k in c.execute("SELECT start_date, end_date, reason FROM Block WHERE equipment_id=? AND end_date>=?", (eid, t)):
            busy.append({"start": k["start_date"], "end": k["end_date"], "type": "blocked", "label": k["reason"] or "Reserved by the lab"})
    return {"equipment_id": e["equipment_id"], "name": e["name"], "maintenance": e["status"] == "Maintenance",
            "today": t, "max_days": MAX_DAYS, "busy": busy}


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

    # ---- CSRF ----
    def session_token(self):
        """sid cookie value if it belongs to a valid, unexpired session."""
        ck = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" not in ck:
            return None
        with db() as c:
            ok = c.execute("SELECT 1 FROM Session WHERE token=? AND expires_at > datetime('now')", (ck["sid"].value,)).fetchone()
        return ck["sid"].value if ok else None

    def expected_csrf(self):
        sid = self.session_token()
        if sid:
            return csrf_for_session(sid), None
        ck = SimpleCookie(self.headers.get("Cookie", ""))
        if "csrf" in ck and re.fullmatch(r"[A-Za-z0-9_-]{20,100}", ck["csrf"].value):
            return ck["csrf"].value, None
        tok = getattr(self, "_anon_csrf", None) or new_token()
        self._anon_csrf = tok
        secure = "; Secure" if self.https() else ""
        return tok, ("Set-Cookie", f"csrf={tok}; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400{secure}")

    def csrf_ok(self, f):
        expected, _ = self.expected_csrf()
        return csrf_equal(f.get("csrf", ""), expected)

    def send(self, body, code=200, ctype="text/html; charset=utf-8", headers=()):
        if isinstance(body, str) and ctype.startswith("text/html") and 'method="post"' in body:
            # add the CSRF token as a hidden field to every POST form on the page
            tok, cookie = self.expected_csrf()
            body = re.sub(r'(<form\b[^>]*method="post"[^>]*>)', lambda m: m.group(1) + f'<input type="hidden" name="csrf" value="{tok}">', body)
            if cookie:
                headers = list(headers) + [cookie]
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
        if path in ("/admin/equipment", "/admin/export/bookings.csv", "/admin/export/stats.csv"):
            if not user:
                return self.redirect("/login")
            if user["role"] != "admin":
                return self.forbidden(user)
            if path == "/admin/equipment":
                q = parse_qs(urlsplit(self.path).query)
                page = equipment_edit_page(user, (q.get("id") or ["0"])[0])
                return self.send(page) if page else self.send(error_page(user, 404, "Equipment not found"), 404)
            return self.export_csv(user, path)
        if path == "/api/availability":
            if not user:
                return self.send('{"error":"login required"}', 401, "application/json")
            q = parse_qs(urlsplit(self.path).query)
            try:
                eid = int((q.get("equipment_id") or ["0"])[0])
            except ValueError:
                return self.send('{"error":"bad equipment_id"}', 400, "application/json")
            return self.send(json.dumps(availability(eid)), ctype="application/json", headers=[("Cache-Control", "no-store")])
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
        if not self.csrf_ok(f):
            with db() as c:
                audit(c, "CSRF_FAIL", "Form", None, user["user_id"] if user else None)
            return self.send(error_page(user, 403, "This form has expired or did not come from LabBook. Reload the page and try again."), 403)
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
            problem = f"Please enter a valid email address, e.g. name@{UNI_DOMAINS[0]}."
        elif not email.endswith(tuple("@" + d for d in UNI_DOMAINS)):
            problem = f"Please use your university e-mail (…@{UNI_DOMAINS[0]}). Other addresses cannot register."
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
            # R4: take the write lock BEFORE the conflict check, so two simultaneous requests
            # cannot both pass the check and both insert (check-then-insert race).
            c.execute("BEGIN IMMEDIATE")
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
                blk = c.execute("""SELECT start_date, end_date, reason FROM Block WHERE equipment_id=?
                                     AND start_date<=? AND end_date>=? ORDER BY start_date LIMIT 1""",
                                (eid, de.isoformat(), ds.isoformat())).fetchone()
                if blk:
                    why = f" ({blk['reason']})" if blk["reason"] else ""
                    problem = (f"{eq['name']} is reserved by the lab from {blk['start_date']} to {blk['end_date']}{why}. "
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
            elif path == "/admin/equipment":
                msg, err = self.save_equipment(c, user, f)
            elif path == "/admin/block":
                msg, err = self.add_block(c, user, f)
            elif path == "/admin/unblock":
                n = c.execute("DELETE FROM Block WHERE block_id=?", (f.get("block_id"),)).rowcount
                if n:
                    audit(c, "DELETE", "Block", int(f.get("block_id")), user["user_id"])
                msg, err = ("Blocked dates removed.", False) if n else ("Block not found.", True)
            else:
                return self.send(error_page(user, 404, "Not found"), 404)
        self.send(admin_page(user, msg, err), 400 if err else 200)

    def save_equipment(self, c, user, f):
        name, cat = f.get("name", ""), f.get("category", "")
        desc, loc = f.get("description", ""), f.get("location", "") or "Lab C1.2"
        if not (2 <= len(name) <= 80) or not (2 <= len(cat) <= 40) or len(desc) > 200 or len(loc) > 40:
            return "Check the fields: name 2–80, category 2–40, description up to 200, location up to 40 characters.", True
        eid = f.get("equipment_id")
        if eid:
            n = c.execute("UPDATE Equipment SET name=?, category=?, description=?, location=? WHERE equipment_id=?",
                          (name, cat, desc, loc, eid)).rowcount
            if not n:
                return "Equipment not found.", True
            audit(c, "UPDATE", "Equipment", int(eid), user["user_id"])
            return f"{name}: changes saved.", False
        cur = c.execute("INSERT INTO Equipment(name,category,description,location) VALUES(?,?,?,?)", (name, cat, desc, loc))
        audit(c, "CREATE", "Equipment", cur.lastrowid, user["user_id"])
        return f"{name} added to the catalog (ID {cur.lastrowid}).", False

    def add_block(self, c, user, f):
        try:
            eid = int(f.get("equipment_id", "0"))
            ds = dt.date.fromisoformat(f.get("start_date", ""))
            de = dt.date.fromisoformat(f.get("end_date", ""))
        except ValueError:
            return "Choose the item and both dates.", True
        reason = f.get("reason", "")[:80]
        eq = c.execute("SELECT name FROM Equipment WHERE equipment_id=?", (eid,)).fetchone()
        if not eq:
            return "Equipment not found.", True
        if de < ds:
            return "The end date cannot be earlier than the start date.", True
        if de < dt.date.today():
            return "These dates are already in the past.", True
        if (de - ds).days > 90:
            return "You can block at most 90 days at once.", True
        cur = c.execute("INSERT INTO Block(equipment_id,start_date,end_date,reason,created_by) VALUES(?,?,?,?,?)",
                        (eid, ds.isoformat(), de.isoformat(), reason, user["user_id"]))
        audit(c, "CREATE", "Block", cur.lastrowid, user["user_id"])
        n = c.execute("""SELECT COUNT(*) FROM Booking WHERE equipment_id=? AND status='Confirmed'
                         AND booking_date<=? AND return_date>=?""", (eid, de.isoformat(), ds.isoformat())).fetchone()[0]
        warn = f" Note: {n} existing booking{'s' if n != 1 else ''} overlap these dates — contact the student{'s' if n != 1 else ''}." if n else ""
        return f"{eq['name']} is blocked from {ds} to {de}.{warn}", False

    def export_csv(self, user, path):
        """R10: CSV export for Excel (UTF-8 with BOM; cells starting with = + - @ are neutralised)."""
        safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v
        buf = io.StringIO()
        w = csv.writer(buf)
        with db() as c:
            if path.endswith("bookings.csv"):
                w.writerow(["booking_id", "equipment", "category", "student", "pick_up", "return_due", "status", "returned_at", "days", "returned_late"])
                for r in c.execute("""SELECT b.booking_id, e.name, e.category, u.full_name, b.booking_date, b.return_date, b.status, b.returned_at,
                        CAST(julianday(b.return_date) - julianday(b.booking_date) + 1 AS INTEGER) AS days,
                        CASE WHEN b.status='Returned' AND b.returned_at > b.return_date THEN 'yes'
                             WHEN b.status='Returned' THEN 'no' ELSE '' END
                        FROM Booking b JOIN Equipment e USING(equipment_id) JOIN User u USING(user_id) ORDER BY b.booking_id"""):
                    w.writerow([safe(x) if x is not None else "" for x in r])
                name = "bookings"
            else:
                q1 = {r["category"]: r["bookings"] for r in c.execute(Q1_BY_CATEGORY)}
                q3 = {r["category"]: r for r in c.execute(Q3_LATE_BY_CATEGORY)}
                w.writerow(["category", "bookings_excl_cancelled", "returned_loans", "avg_days_kept", "late_return_pct"])
                for cat in sorted(set(q1) | set(q3), key=lambda k: -q1.get(k, 0)):
                    r3 = q3.get(cat)
                    w.writerow([safe(cat), q1.get(cat, 0), r3["returned_loans"] if r3 else 0,
                                r3["avg_days_kept"] if r3 else "", r3["late_pct"] if r3 else ""])
                name = "statistics"
            audit(c, "EXPORT", "CSV", None, user["user_id"])
        data = ("\ufeff" + buf.getvalue()).encode("utf-8")
        self.send(data, ctype="text/csv; charset=utf-8",
                  headers=[("Content-Disposition", f'attachment; filename="labbook_{name}_{today()}.csv"'), ("Cache-Control", "no-store")])

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
    with db() as c:          # add tables introduced after Stage 2 to an existing database
        c.executescript(BLOCK_SCHEMA)


if __name__ == "__main__":
    ensure_db()
    port = int(os.environ.get("PORT", "8000"))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    print(f"LabBook running at http://{host}:{port}")
    class Server(ThreadingHTTPServer):
        request_queue_size = 64  # default 5 drops connections when many users click at once
        daemon_threads = True
    Server((host, port), Handler).serve_forever()
