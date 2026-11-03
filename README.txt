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

IOT SIMULATION (Part D3)
  Open the admin dashboard, then in a second terminal run:
     py scripts/iot_simulator.py
  For the deployed site:
     py scripts/iot_simulator.py --url https://lab-equipment-booking-vzta.onrender.com --key demo-device-key
  The "Equipment sensors" table refreshes every 5 s; on round 3 the Thermal Camera leaves its
  cabinet without a booking and a red alert appears. (Device key can be changed with env SENSOR_API_KEY.)

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
