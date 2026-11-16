"""Functional checks for the 7 new features. Usage: python3 test_features.py http://127.0.0.1:8000"""
import datetime as dt, json, sys, urllib.parse, urllib.request
from http.cookiejar import CookieJar

BASE = sys.argv[1]
T = dt.date.today()
D = lambda n: (T + dt.timedelta(days=n)).isoformat()
ok_all = True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def session():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()), NoRedirect)


TOKEN_RE = __import__("re").compile(r'name="csrf" value="([^"]+)"')


def csrf(op):
    """Read the CSRF token the server puts into every POST form (needs the session's cookies)."""
    body = op.open(BASE + "/login" if not getattr(op, "logged", False) else BASE + "/", timeout=20).read().decode()
    return TOKEN_RE.search(body).group(1)


def req(op, path, data=None, token=True):
    if data is not None and token:
        data = dict(data, csrf=csrf(op))
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        r = op.open(BASE + path, body, timeout=20)
        return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def check(name, cond, extra=""):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))


# ---- R1 university e-mail ----
anon = session()
s, b, _ = req(anon, "/register", {"full_name": "Test Gmail", "email": "test.user@gmail.com", "password": "Passw0rd1"})
check("R1 gmail registration rejected", s == 400 and "university e-mail" in b, s)
s, b, h = req(anon, "/register", {"full_name": "Test Student", "email": f"t{T:%m%d}{dt.datetime.now():%H%M%S}@astanait.edu.kz", "password": "Passw0rd1"})
check("R1 astanait.edu.kz registration accepted", s == 303, s)
s, b, _ = req(anon, "/register")
check("R1 hint shown on register page", "@astanait.edu.kz" in b)

stu = session(); req(stu, "/login", {"email": "aruzhan.s@labbook.test", "password": "Student#2026"}); stu.logged = True
adm = session(); req(adm, "/login", {"email": "admin@labbook.test", "password": "Admin#2026"}); adm.logged = True

# ---- catalog filters markup ----
s, b, _ = req(stu, "/")
check("Filters present on catalog", 'id="f-q"' in b and 'id="f-cat"' in b and 'data-category=' in b)
check("Calendar container in booking form", 'id="avail"' in b)
check("Check dates & book buttons for students", "book-this" in b)

# ---- R6 add / edit equipment ----
s, b, _ = req(adm, "/admin/equipment", {"name": "Logic Analyzer", "category": "Electronics", "description": "16-channel USB logic analyzer", "location": "Lab C1.2"})
check("R6 add equipment", s == 200 and "Logic Analyzer added" in b, s)
import re
new_id = int(re.search(r"Logic Analyzer added to the catalog \(ID (\d+)\)", b).group(1))
s, b, _ = req(adm, f"/admin/equipment?id={new_id}")
check("R6 edit page opens", s == 200 and "Edit Logic Analyzer" in b, s)
s, b, _ = req(adm, "/admin/equipment", {"equipment_id": new_id, "name": "Logic Analyzer 16ch", "category": "Electronics", "description": "USB logic analyzer", "location": "Lab C1.3"})
check("R6 edit saves", "Logic Analyzer 16ch: changes saved" in b)
s, b, _ = req(adm, "/admin/equipment", {"name": "X", "category": "", "description": ""})
check("R6 validation rejects bad input", s == 400)
s, b, _ = req(stu, "/admin/equipment", {"name": "Hack", "category": "Hack"})
check("R6 student cannot add equipment (403)", s == 403, s)
s, b, _ = req(anon, "/")
check("R6 new item visible in catalog", "Logic Analyzer 16ch" in b)

# ---- R8 block dates ----
s, b, _ = req(adm, "/admin/block", {"equipment_id": new_id, "start_date": D(10), "end_date": D(12), "reason": "Physics lab session"})
check("R8 admin blocks dates", s == 200 and "is blocked from" in b, s)
s, b, _ = req(stu, "/book", {"equipment_id": new_id, "booking_date": D(11), "return_date": D(13)})
check("R8 booking on blocked dates rejected", s == 400 and "reserved by the lab" in b and "Physics lab session" in b, s)
s, b, _ = req(stu, "/book", {"equipment_id": new_id, "booking_date": D(14), "return_date": D(15)})
check("R8 booking outside block accepted", s == 200 and "confirmed" in b, s)
s, b, _ = req(adm, "/admin/block", {"equipment_id": new_id, "start_date": D(15), "end_date": D(16), "reason": "Calibration"})
check("R8 overlap warning for existing booking", "existing booking" in b)
s, b, _ = req(adm, "/admin/block", {"equipment_id": new_id, "start_date": D(5), "end_date": D(3)})
check("R8 end before start rejected", s == 400)
s, b, _ = req(stu, "/admin/block", {"equipment_id": new_id, "start_date": D(1), "end_date": D(2)})
check("R8 student cannot block (403)", s == 403)

# ---- R3 availability API ----
s, b, _ = req(stu, f"/api/availability?equipment_id={new_id}")
a = json.loads(b)
types = {x["type"] for x in a["busy"]}
check("R3 availability returns booked + blocked ranges", s == 200 and {"booked", "blocked"} <= types, types)
check("R3 availability has no student names", "aruzhan" not in b.lower() and "full_name" not in b)
s, b, _ = req(session(), f"/api/availability?equipment_id={new_id}")
check("R3 availability needs login", s == 401, s)
s, b, _ = req(stu, "/api/availability?equipment_id=abc")
check("R3 bad id handled", s == 400, s)

# ---- R7 reminders ----
s, b, _ = req(stu, "/book", {"equipment_id": new_id, "booking_date": D(1), "return_date": D(2)})
s, b, _ = req(stu, "/my")
check("R7 student reminder: pick up tomorrow", "Pick up <b>Logic Analyzer 16ch</b> tomorrow" in b)
s, b, _ = req(adm, "/admin")
check("R7 admin return reminders panel", "Return reminders" in b and "overdue loan" in b)

# ---- R10 CSV ----
s, b, h = req(adm, "/admin/export/bookings.csv")
lines = b.lstrip("﻿").splitlines()
check("R10 bookings CSV downloads", s == 200 and h.get("Content-Type", "").startswith("text/csv") and "attachment" in h.get("Content-Disposition", ""), s)
check("R10 bookings CSV has header + all rows", lines[0].startswith("booking_id,equipment") and len(lines) >= 152, len(lines))
check("R10 CSV starts with UTF-8 BOM (Excel)", b.startswith("﻿"))
s, b, h = req(adm, "/admin/export/stats.csv")
check("R10 statistics CSV", s == 200 and "late_return_pct" in b and "Electronics" in b, s)
s, b, h = req(stu, "/admin/export/bookings.csv")
check("R10 student cannot export (403)", s == 403, s)

# ---- unblock + regression ----
s, b, _ = req(adm, "/admin")
bid = re.search(r'name="block_id" value="(\d+)"', b).group(1)
s, b, _ = req(adm, "/admin/unblock", {"block_id": bid})
check("R8 remove block", "Blocked dates removed" in b)
s, b, _ = req(stu, "/book", {"equipment_id": 5, "booking_date": D(40), "return_date": D(60)})
check("Regression: 14-day limit still enforced", s == 400 and "at most 14 days" in b)
s, b, _ = req(stu, "/admin")
check("Regression: student /admin still 403", s == 403)

# ---- CSRF ----
s, b, _ = req(stu, "/book", {"equipment_id": 13, "booking_date": D(50), "return_date": D(51)}, token=False)
check("CSRF: booking without token rejected (403)", s == 403 and "expired" in b, s)
s, b, _ = req(stu, "/book", {"equipment_id": 13, "booking_date": D(50), "return_date": D(51), "csrf": "forged-token-from-evil-site"}, token=False)
check("CSRF: booking with forged token rejected (403)", s == 403, s)
s, b, _ = req(adm, "/admin/maintenance", {"equipment_id": 13}, token=False)
check("CSRF: admin action without token rejected (403)", s == 403, s)
other = session(); s, b, _ = req(other, "/login", {"email": "admin@labbook.test", "password": "Admin#2026"}, token=False)
check("CSRF: login without token rejected (403)", s == 403, s)
tok_stu = csrf(stu)
s, b, _ = req(adm, "/admin/maintenance", {"equipment_id": 13, "csrf": tok_stu}, token=False)
check("CSRF: another user's token rejected (403)", s == 403, s)
s, b, _ = req(stu, "/book", {"equipment_id": 13, "booking_date": D(50), "return_date": D(51)})
check("CSRF: booking with valid token works", s == 200 and "confirmed" in b, s)

print("\nALL PASSED" if ok_all else "\nSOME CHECKS FAILED")
