"""Rebuild lab_equipment.sqlite with the Stage 2 schema and GENERATED demo data.

All people, emails and bookings below are synthetic (random.seed fixed so the
result is reproducible). Run:  python scripts/seed_db.py
"""
import random, sqlite3, sys, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from security import hash_password  # noqa: E402

DB = ROOT / "lab_equipment.sqlite"
TODAY = dt.date(2026, 11, 3)          # data snapshot date
SEMESTER_START = dt.date(2026, 9, 1)
random.seed(2625)

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE User(
  user_id INTEGER PRIMARY KEY AUTOINCREMENT,
  full_name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'student' CHECK(role IN ('student','admin')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE Equipment(
  equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'Available' CHECK(status IN ('Available','Maintenance')),
  description TEXT DEFAULT '',
  location TEXT DEFAULT 'Lab C1.2');
CREATE TABLE Booking(
  booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES User(user_id),
  equipment_id INTEGER NOT NULL REFERENCES Equipment(equipment_id),
  booking_date TEXT NOT NULL,
  return_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'Confirmed' CHECK(status IN ('Confirmed','Returned','Cancelled')),
  returned_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK(return_date >= booking_date));
CREATE TABLE AuditLog(
  log_id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL, entity TEXT NOT NULL, entity_id INTEGER,
  user_id INTEGER, timestamp TEXT NOT NULL);
CREATE TABLE Session(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES User(user_id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL);
CREATE TABLE SensorReading(
  reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
  equipment_id INTEGER NOT NULL REFERENCES Equipment(equipment_id),
  device_id TEXT NOT NULL,
  power_w REAL,
  in_cabinet INTEGER CHECK(in_cabinet IN (0,1)),
  recorded_at TEXT NOT NULL);
CREATE INDEX idx_booking_equipment_dates ON Booking(equipment_id, booking_date, return_date);
CREATE INDEX idx_sensor_equipment_time ON SensorReading(equipment_id, recorded_at);
"""

EQUIPMENT = [  # name, category, description, (min_days, max_days), weight, late_prob
    ("Digital Microscope", "Optics", "For observing small samples", (1, 3), 4, .05),
    ("Bench Power Supply", "Electronics", "Adjustable 0-30 V DC power supply", (1, 4), 6, .10),
    ("Signal Generator", "Electronics", "Function generator up to 25 MHz", (1, 3), 5, .10),
    ("USB Microscope", "Imaging", "Portable imaging device", (1, 3), 3, .05),
    ("VR Headset", "Computing", "Virtual lab demonstrations", (2, 5), 4, .20),
    ("Oscilloscope (2-channel)", "Electronics", "For viewing electrical signals", (1, 3), 7, .10),
    ("Thermal Camera", "Imaging", "Thermal inspection of circuits", (1, 2), 3, .05),
    ("Oscilloscope (4-channel)", "Electronics", "4-channel digital oscilloscope", (1, 3), 6, .10),
    ("Arduino Starter Kit", "Microcontrollers", "Board, sensors, breadboard, wires", (3, 9), 8, .25),
    ("Raspberry Pi 5 Kit", "Microcontrollers", "Single-board computer with camera", (3, 9), 7, .25),
    ("3D Scanner", "Imaging", "Handheld structured-light scanner", (1, 2), 2, .05),
    ("CAD Laptop", "Computing", "Workstation laptop with CAD software", (2, 6), 5, .20),
    ("Digital Multimeter Set", "Measurement", "Multimeters and probes", (1, 2), 2, .05),
    ("Sound Level Meter", "Measurement", "Class 2 sound level meter", (1, 2), 1, .05),
    ("Spectrometer", "Optics", "Visible-light USB spectrometer", (1, 3), 2, .05),
    ("ESP32 IoT Kit", "Microcontrollers", "ESP32 board with Wi-Fi/BLE sensors", (3, 9), 7, .25),
]
# demand per semester week (1..10): ramps to midterm, dips, rises again
WEEK_WEIGHT = {1: 2, 2: 4, 3: 6, 4: 8, 5: 10, 6: 12, 7: 9, 8: 6, 9: 11, 10: 5}

FIRST = ["Aruzhan", "Dias", "Madina", "Alikhan", "Aigerim", "Nursultan", "Dana", "Yerlan",
         "Kamila", "Timur", "Zhanel", "Arman", "Saule", "Bekzat", "Amina", "Daniyar",
         "Togzhan", "Askar", "Inkar", "Miras", "Aisulu", "Sanzhar", "Dilnaz", "Ruslan",
         "Assel", "Olzhas", "Malika", "Islam", "Ainur", "Ernar", "Aidana", "Adil",
         "Zarina", "Rauan", "Akbota", "Nurlan"]
LAST = ["Sadykova", "Omarov", "Bekova", "Zhumabaev", "Nurlanova", "Seitkali", "Akhmetova",
        "Kassymov", "Tulegenova", "Iskakov", "Abenova", "Serikov", "Rakhimova", "Alimov",
        "Kenzhebek", "Ospanov", "Maratova", "Yessenov", "Baimukhanova", "Utepov",
        "Dauletova", "Kairatov", "Mukhanova", "Shaikenov", "Yerzhanova", "Tokayev",
        "Amanova", "Kuanyshev", "Saparova", "Bolatov", "Zhakupova", "Nurbekov",
        "Askarova", "Temirkhanov", "Ilyasova", "Abdrakhmanov"]


def main():
    if DB.exists():
        DB.unlink()
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)

    admin_hash = hash_password("Admin#2026")
    c.execute("INSERT INTO User(full_name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
              ("Lab Administrator", "admin@labbook.test", admin_hash, "admin", "2026-08-25 09:00:00"))
    students = []
    for i, (f, l) in enumerate(zip(FIRST, LAST)):
        email = f"{f.lower()}.{l[0].lower()}@labbook.test"
        created = SEMESTER_START + dt.timedelta(days=random.randint(0, 10))
        cur = c.execute("INSERT INTO User(full_name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                        (f"{f} {l}", email, hash_password("Student#2026"),  # unique salt per user
                         "student", f"{created} 10:00:00"))
        students.append(cur.lastrowid)

    for name, cat, desc, *_ in EQUIPMENT:
        c.execute("INSERT INTO Equipment(name,category,description) VALUES(?,?,?)", (name, cat, desc))
    c.execute("UPDATE Equipment SET status='Maintenance' WHERE name='Spectrometer'")

    busy = {i + 1: [] for i in range(len(EQUIPMENT))}  # equipment_id -> [(start,end_incl_late)]
    weeks, wweights = zip(*WEEK_WEIGHT.items())
    eq_ids = list(busy)
    eq_weights = [e[4] for e in EQUIPMENT]
    rows, attempts = [], 0
    while len(rows) < 150 and attempts < 20000:
        attempts += 1
        eid = random.choices(eq_ids, eq_weights)[0]
        name, cat, desc, (dmin, dmax), _, late_p = EQUIPMENT[eid - 1]
        week = random.choices(weeks, wweights)[0]
        start = SEMESTER_START + dt.timedelta(days=(week - 1) * 7 + random.randint(0, 4))
        if start > TODAY + dt.timedelta(days=8):
            continue
        end = start + dt.timedelta(days=random.randint(dmin, dmax) - 1)
        late = random.randint(1, 4) if random.random() < late_p else 0
        real_end = end + dt.timedelta(days=late)
        if name == "Spectrometer" and real_end >= dt.date(2026, 10, 20):
            continue  # in maintenance since 20 Oct
        if any(not (real_end < s or start > e) for s, e in busy[eid]):
            continue
        uid = random.choice(students)
        if random.random() < .08:
            status, returned = "Cancelled", None
        elif real_end < TODAY:
            status, returned = "Returned", real_end.isoformat()
        else:
            status, returned = "Confirmed", None
            late = 0
        busy[eid].append((start, real_end if status != "Cancelled" else start - dt.timedelta(days=1)))
        created = start - dt.timedelta(days=random.randint(0, 5))
        rows.append((uid, eid, start.isoformat(), end.isoformat(), status, returned,
                     f"{min(created, TODAY)} {random.randint(8, 20) if created < TODAY else random.randint(5, 8):02d}:{random.randint(0, 59):02d}:00"))

    # two deliberately overdue loans (not returned, return_date already passed)
    for eid, s, e in [(9, "2026-10-26", "2026-10-31"), (12, "2026-10-28", "2026-11-01")]:
        if not any(not (dt.date.fromisoformat(e) + dt.timedelta(days=5) < bs or dt.date.fromisoformat(s) > be)
                   for bs, be in busy[eid]):
            rows.append((random.choice(students), eid, s, e, "Confirmed", None, f"{s} 09:15:00"))
            busy[eid].append((dt.date.fromisoformat(s), TODAY + dt.timedelta(days=5)))

    rows.sort(key=lambda r: r[6])
    for r in rows:
        cur = c.execute("INSERT INTO Booking(user_id,equipment_id,booking_date,return_date,status,returned_at,created_at)"
                        " VALUES(?,?,?,?,?,?,?)", r)
        c.execute("INSERT INTO AuditLog(action,entity,entity_id,user_id,timestamp) VALUES('CREATE','Booking',?,?,?)",
                  (cur.lastrowid, r[0], r[6]))
        if r[4] == "Returned":
            c.execute("INSERT INTO AuditLog(action,entity,entity_id,user_id,timestamp) VALUES('RETURN','Booking',?,1,?)",
                      (cur.lastrowid, r[5] + " 17:00:00"))
        if r[4] == "Cancelled":
            c.execute("INSERT INTO AuditLog(action,entity,entity_id,user_id,timestamp) VALUES('CANCEL','Booking',?,?,?)",
                      (cur.lastrowid, r[0], r[6]))

    # simulated IoT readings: smart tags on 6 items, every 15 min for the last 6 hours
    tagged = {2: "tag-psu-01", 6: "tag-osc-01", 7: "tag-thermo-01", 8: "tag-osc-02", 11: "tag-scan-01", 12: "tag-cad-01"}
    base = dt.datetime(2026, 11, 3, 12, 0)
    for eid, dev in tagged.items():
        for k in range(24):
            t = base - dt.timedelta(minutes=15 * (23 - k))
            active = eid in (6, 8) and 10 <= t.hour < 12
            c.execute("INSERT INTO SensorReading(equipment_id,device_id,power_w,in_cabinet,recorded_at) VALUES(?,?,?,?,?)",
                      (eid, dev, round(random.uniform(35, 60), 1) if active else round(random.uniform(0, 1.5), 1),
                       1, t.strftime("%Y-%m-%d %H:%M:%S")))
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM Booking").fetchone()[0]
    u = c.execute("SELECT COUNT(*) FROM User").fetchone()[0]
    print(f"Created {DB.name}: {u} users, {len(EQUIPMENT)} equipment, {n} bookings")


if __name__ == "__main__":
    main()
