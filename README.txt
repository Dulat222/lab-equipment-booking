LABBOOK — LABORATORY EQUIPMENT BOOKING SERVICE (ICT Project Stage 2)
Author: Dulat Nurlybek · SE-2625 (individual project)
Live: https://lab-equipment-booking-vzta.onrender.com/   (Render free tier: first open after idle takes ~50 s)
Code: https://github.com/Dulat222/lab-equipment-booking

RUN LOCALLY (Windows / macOS / Linux)
1. Install Python 3.10+ (no extra packages needed — standard library only).
2. Open the project folder in a terminal (VS Code: Terminal > New Terminal).
3. Run:  py app.py        (macOS/Linux: python3 app.py)
4. Open http://127.0.0.1:8000
5. Stop with Ctrl+C.
To reset the database to the original generated data:  py scripts/seed_db.py

DEMO ACCOUNTS (generated data — not real people)
  Admin:    admin@labbook.test      / Admin#2026
  Student:  aruzhan.s@labbook.test  / Student#2026   (all 36 demo students use Student#2026)
  Or register a new student account on /register.

MAIN SCENARIOS
  Student: Register/Log in -> choose equipment and dates -> Book -> "My bookings" -> cancel an upcoming booking.
  Admin:   Log in -> dashboard (KPIs, 3 charts, IoT sensors) -> "Mark returned" / "Send to maintenance".

SECURITY (from Assignment 3)
  - Passwords stored as PBKDF2-HMAC-SHA256 hashes (600 000 iterations as recommended by OWASP, random 16-byte salt) — security.py
  - Role-based access: students get HTTP 403 on /admin (logged as DENIED in AuditLog)
  - Session cookie: random 256-bit token, HttpOnly, SameSite=Lax, Secure on HTTPS, expires after 8 h
  - Brute-force protection: 5 wrong passwords -> account email locked for 5 min (HTTP 429)
  - HTTPS on Render + HSTS header; CSP, X-Frame-Options, nosniff headers
  - All SQL uses parameters (no string concatenation) -> no SQL injection; all output HTML-escaped
  - CSRF tokens on every form: logged-in users get a token bound to their session (HMAC-SHA256),
    visitors (login/register) a random token in an HttpOnly cookie; a missing or wrong token -> HTTP 403,
    logged as CSRF_FAIL in AuditLog. Set env CSRF_SECRET to keep tokens valid across restarts.
  - Colours meet WCAG AA contrast (buttons 5.4:1); PageSpeed/Lighthouse accessibility checked after the change

IOT SIMULATION (Part D3)
  Open the admin dashboard, then in a second terminal run:
     py scripts/iot_simulator.py
  For the deployed site:
     py scripts/iot_simulator.py --url https://lab-equipment-booking-vzta.onrender.com --key demo-device-key
  The "Equipment sensors" table refreshes every 5 s; on round 3 the Thermal Camera leaves its
  cabinet without a booking and a red alert appears. (Device key can be changed with env SENSOR_API_KEY.)

R4 TEST (Stage 1 top requirement: no double booking)
  Start the server (py app.py), then in a second terminal:
     py tests/r4_concurrency_test.py http://127.0.0.1:8000 5              (20 requests for a free item -> 1 confirmed, 19 rejected)
  Item ids without open loans in the generated data: 5, 7, 11, 13, 14 (reset with py scripts/seed_db.py).

NEW FEATURES (after Stage 2 defence prep, 16 Nov 2026)
  R1  Registration accepts only university e-mail (@astanait.edu.kz). Change with env UNI_EMAIL_DOMAINS.
      Demo accounts (@labbook.test) can still log in.
  R3  Booking form shows an availability calendar for the chosen item (free / booked / reserved / not returned).
      Click a free day for pick-up, then another for return. Data: GET /api/availability?equipment_id=N (login required).
  R6  Admin can add new equipment and edit existing items (Admin dashboard -> Inventory -> "+ Add new equipment" / "Edit").
  R7  On-site reminders: students see "return today/tomorrow", "overdue" and "pick up tomorrow" on Catalog and My bookings;
      the admin sees "Return reminders" on the dashboard.
  R8  Admin can block dates for an item (lab session, calibration). Students cannot book those dates. Table: Block.
  R10 Admin can download CSV files (Excel-ready): all bookings and statistics by category.
  +   Catalog search (word start) and filters by category and status.
  The Block table is created automatically on start-up if the database is older.
  Feature tests: start the server, then  py tests/test_features.py http://127.0.0.1:8000

FILES
  app.py                  web server, pages, routes, analysis SQL (Q1–Q3)
  security.py             password hashing, tokens, login rate limit
  lab_equipment.sqlite    database (User, Equipment, Booking, AuditLog, Session, SensorReading)
  scripts/seed_db.py      rebuilds the DB with 150 GENERATED bookings (random seed 2625)
  scripts/iot_simulator.py fake smart-tag readings
  static/                 CSS, JS, logo, images

NOTE: Render's free tier has an ephemeral filesystem and spins the service down after 15 minutes
without traffic. On every spin-down, restart or redeploy the SQLite file returns to the generated
state stored in the repository (150 bookings). Accounts and bookings created during a demo persist
only while the instance stays awake. Planned fix: move to a managed PostgreSQL database (week 10 plan).
