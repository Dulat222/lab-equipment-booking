
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs
from pathlib import Path
import sqlite3, html, mimetypes
import os

BASE = Path(__file__).resolve().parent
DB = BASE / "lab-equipment.sqlite"


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def page(message=""):
    with db() as c:
        equipment = c.execute(
            "SELECT * FROM Equipment ORDER BY equipment_id"
        ).fetchall()
        bookings = c.execute(
            "SELECT b.booking_id, s.full_name, e.name, b.booking_date, "
            "b.return_date, b.status FROM Booking b "
            "JOIN Student s ON s.student_id=b.student_id "
            "JOIN Equipment e ON e.equipment_id=b.equipment_id "
            "ORDER BY b.booking_id DESC LIMIT 6"
        ).fetchall()
        students = c.execute(
            "SELECT student_id,full_name FROM Student"
        ).fetchall()

    cards = "".join(
        f'''<article class="item">
        <div class="item-top">
        <span class="icon" aria-hidden="true">◈</span>
        <span class="status {('booked' if x['status']=='Booked' else 'available' if x['status']=='Available' else 'maintenance')}">{html.escape(x['status'])}</span>
        </div>
        <h3>{html.escape(x['name'])}</h3>
        <p>{html.escape(x['category'])}</p>
        <small>{html.escape(x['description'] or '')}</small>
        <div class="item-foot">ID: {x['equipment_id']}</div>
        </article>'''
        for x in equipment
    )

    student_opts = "".join(
        f'<option value="{s["student_id"]}">'
        f'{html.escape(s["full_name"])} (ID {s["student_id"]})</option>'
        for s in students
    )

    equip_opts = "".join(
        f'<option value="{x["equipment_id"]}" '
        f'{"disabled" if x["status"] != "Available" else ""}>'
        f'{html.escape(x["name"])} — {html.escape(x["status"])}</option>'
        for x in equipment
    )

    rows = "".join(
        f'''<tr>
        <td>#{b["booking_id"]}</td>
        <td>{html.escape(b["full_name"])}</td>
        <td>{html.escape(b["name"])}</td>
        <td>{html.escape(b["booking_date"])}</td>
        <td>{html.escape(b["return_date"])}</td>
        <td>{html.escape(b["status"])}</td>
        </tr>'''
        for b in bookings
    ) or '<tr><td colspan="6">No bookings yet</td></tr>'

    notice = (
        f'<div class="notice" role="status">{html.escape(message)}</div>'
        if message else ''
    )

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Laboratory equipment booking service.">
<title>LabBook — Equipment Booking</title>
<link rel="icon" href="/static/images/logo.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header class="top">
<div class="brand">
<img src="/static/images/logo.svg" width="54" height="54" alt="LabBook logo">
<div><strong>LabBook</strong>
<span>Laboratory Equipment Booking Service</span></div>
</div>
<span class="tag">Student Project · Assignment 4</span>
</header>

<main>
<section class="hero">
<div>
<p class="eyebrow">LABORATORY CENTER</p>
<h1>Equipment for<br><em>your experiments.</em></h1>
<p class="intro">Browse available equipment and create test bookings stored in an SQLite database.</p>
<a class="primary-link" href="#booking">Book Equipment <span aria-hidden="true">↓</span></a>
</div>
<img class="hero-image" src="/static/images/equipment-dashboard.webp"
alt="Laboratory equipment dashboard illustration" width="1280" height="720">
</section>

{notice}

<section class="section" id="equipment">
<div class="section-heading">
<div><p class="eyebrow">CATALOG</p><h2>Equipment</h2></div>
<span class="count">{len(equipment)} items · READ</span>
</div>
<div class="grid">{cards}</div>
</section>

<section class="booking-area" id="booking">
<div class="booking-copy">
<p class="eyebrow">BOOKING · WRITE</p>
<h2>Plan Your Work</h2>
<p>Select a student, available equipment, and booking dates.
Your booking will be saved in SQLite and recorded in AuditLog.</p>
<div class="privacy">ⓘ Demo data is used for this student project.</div>
</div>

<form action="/book" method="post">
<label for="student">Student</label>
<select id="student" name="student_id" required>{student_opts}</select>

<label for="equipment">Equipment</label>
<select id="equipment" name="equipment_id" required>{equip_opts}</select>

<div class="dates">
<div><label for="booking_date">Start Date</label>
<input id="booking_date" name="booking_date" type="date" required></div>
<div><label for="return_date">Return Date</label>
<input id="return_date" name="return_date" type="date" required></div>
</div>

<button type="submit">Create Booking <span aria-hidden="true">↗</span></button>
<p class="form-hint">You can test the notification sound using the button below.</p>
</form>
</section>

<section class="section">
<div class="section-heading">
<div><p class="eyebrow">RECENT ACTIVITY</p><h2>Bookings</h2></div>
<span class="count">SQLite · AuditLog</span>
</div>
<div class="table-wrap">
<table>
<thead><tr>
<th>ID</th><th>Student</th><th>Equipment</th>
<th>Start Date</th><th>Return Date</th><th>Status</th>
</tr></thead>
<tbody>{rows}</tbody>
</table>
</div>
</section>

<section class="media-note">
<div class="sound-icon" aria-hidden="true">♫</div>
<div><h3>Sound Notification</h3>
<p>Click the button to test the notification sound. Automatic playback is disabled.</p></div>
<button class="secondary" id="soundBtn" type="button">▶ Test Sound</button>
<audio id="notify" preload="none">
<source src="/static/audio/booking-notification.mp3" type="audio/mpeg">
<source src="/static/audio/booking-notification.wav" type="audio/wav">
</audio>
</section>
</main>

<footer>
<img src="/static/images/logo.svg" alt="" width="30" height="30">
<span>LabBook · Python + SQLite · Demo Data</span>
<span>Responsive interface · 360 px+ screens</span>
</footer>
<script src="/static/app.js" defer></script>
</body>
</html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/static/'):
            rel = self.path.split('?', 1)[0].lstrip('/')
            target = (BASE / rel).resolve()
            if (not str(target).startswith(str((BASE / 'static').resolve()))
                    or not target.is_file()):
                self.send_error(404)
                return
            data = target.read_bytes()
            typ = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', typ)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(data)
            return
        self.respond(page())

    def do_POST(self):
        if self.path != '/book':
            self.send_error(404)
            return

        data = parse_qs(
            self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode()
        )

        try:
            sid = int(data['student_id'][0])
            eid = int(data['equipment_id'][0])
            start = data['booking_date'][0]
            end = data['return_date'][0]

            import datetime
            ds = datetime.date.fromisoformat(start)
            de = datetime.date.fromisoformat(end)

            if de < ds:
                raise ValueError('The return date cannot be earlier than the start date.')

            with db() as c:
                equip = c.execute(
                    'SELECT status FROM Equipment WHERE equipment_id=?', (eid,)
                ).fetchone()

                if not equip or equip['status'] != 'Available':
                    raise ValueError('This equipment is unavailable. Please choose another item.')

                cur = c.execute(
                    "INSERT INTO Booking(student_id,equipment_id,booking_date,return_date,status) "
                    "VALUES(?,?,?,?, 'Confirmed')",
                    (sid, eid, start, end)
                )

                c.execute(
                    "UPDATE Equipment SET status='Booked' WHERE equipment_id=?",
                    (eid,)
                )
                c.execute(
                    "INSERT INTO AuditLog(action,entity,entity_id,user_id,timestamp) "
                    "VALUES('CREATE','Booking',?,?,datetime('now'))",
                    (cur.lastrowid, sid)
                )
                c.execute(
                    "INSERT INTO AuditLog(action,entity,entity_id,user_id,timestamp) "
                    "VALUES('UPDATE','Equipment',?,?,datetime('now'))",
                    (eid, sid)
                )

            self.respond(page(
                'Booking created successfully. SQLite record saved and AuditLog updated.'
            ))

        except Exception as e:
            self.respond(page('Could not create booking: ' + str(e)), 400)

    def respond(self, body, code=200):
        data = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    print(f'LabBook running on port {port}')
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
