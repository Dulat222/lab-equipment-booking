"""R4 test from Stage 1: 20 overlapping booking requests for the same item at the same moment.
Expected (R4): exactly 1 booking is created, the other 19 are rejected with a conflict message.

Usage: start the server (py app.py), then:  py r4_concurrency_test.py [base_url] [equipment_id]
Uses 20 generated demo student accounts (password Student#2026). Standard library only.
"""
import datetime as dt
import sqlite3
import sys
import threading
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
EID = int(sys.argv[2]) if len(sys.argv) > 2 else 11  # 3D Scanner
DB = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "lab_equipment.sqlite"
N = 20


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def opener():
    jar = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)


TOKEN_RE = __import__("re").compile(r'name="csrf" value="([^"]+)"')


def token(op, path):
    return TOKEN_RE.search(op.open(BASE + path, timeout=30).read().decode()).group(1)


def post(op, path, data):
    body = urllib.parse.urlencode(data).encode()
    try:
        r = op.open(BASE + path, body, timeout=30)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except OSError as e:  # connection refused/reset
        return 0, repr(e)


emails = [r[0] for r in sqlite3.connect(DB).execute(
    "SELECT email FROM User WHERE role='student' ORDER BY user_id LIMIT ?", (N,))]
sessions = []
for em in emails:
    op = opener()
    post(op, "/login", {"email": em, "password": "Student#2026", "csrf": token(op, "/login")})
    op.csrf = token(op, "/")  # session-bound CSRF token used for /book
    sessions.append(op)

start = (dt.date.today() + dt.timedelta(days=20)).isoformat()
end = (dt.date.today() + dt.timedelta(days=22)).isoformat()
before = sqlite3.connect(DB).execute(
    "SELECT COUNT(*) FROM Booking WHERE equipment_id=? AND status='Confirmed' AND booking_date<=? AND return_date>=?",
    (EID, end, start)).fetchone()[0]
open_loans = sqlite3.connect(DB).execute(
    "SELECT COUNT(*) FROM Booking WHERE equipment_id=? AND status='Confirmed' AND booking_date<=?", (EID, end)).fetchone()[0]
EXISTING = "--existing" in sys.argv  # Stage 1 wording: 20 requests overlapping an EXISTING booking -> all 20 rejected
if EXISTING and not open_loans:
    sys.exit("--existing mode: book the item for these dates first.")
if open_loans and not EXISTING:
    sys.exit(f"Item {EID} already has a confirmed/overdue loan that blocks these dates - choose another item.")

results = [None] * N
gate = threading.Barrier(N)


def worker(i):
    gate.wait()  # all 20 requests leave at the same moment
    results[i] = post(sessions[i], "/book", {"equipment_id": EID, "booking_date": start, "return_date": end, "csrf": sessions[i].csrf})


threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
for t in threads:
    t.start()
for t in threads:
    t.join()

ok = sum(1 for s, b in results if s == 200 and "confirmed" in b)
conflict = sum(1 for s, b in results if s == 400 and "already booked" in b)
errors = sum(1 for s, b in results if s == 0)
other = N - ok - conflict - errors
after = sqlite3.connect(DB).execute(
    "SELECT COUNT(*) FROM Booking WHERE equipment_id=? AND status='Confirmed' AND booking_date<=? AND return_date>=?",
    (EID, end, start)).fetchone()[0]
print(f"Item {EID}, dates {start}..{end}, {N} simultaneous requests")
print(f"confirmed: {ok}  rejected as conflict: {conflict}  connection errors: {errors}  other: {other}")
print(f"overlapping Confirmed rows in DB: before {before}, after {after}")
if EXISTING:
    print("R4 (existing booking) PASSED" if ok == 0 and conflict == N and after == before else "R4 (existing booking) FAILED")
elif after - before > 1:
    print("R4 FAILED: double booking created")
elif ok == 1 and conflict == N - 1:
    print("R4 PASSED")
else:
    print("R4 NOT MET: no double booking, but not every request got an answer")
