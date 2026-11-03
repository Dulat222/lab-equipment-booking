"""IoT simulation (Part D3): fake 'smart tags' send readings to LabBook over HTTP(S).

Each tag = ESP32 + current sensor (power draw in watts) + reed switch (is the item in its cabinet?).
Usage:
  python scripts/iot_simulator.py                         # local server, http://127.0.0.1:8000
  python scripts/iot_simulator.py --url https://lab-equipment-booking-vzta.onrender.com --key demo-device-key
Scenario: oscilloscopes switch on and off, and on round 3 the Thermal Camera is taken out of
its cabinet WITHOUT a booking -> the admin dashboard shows a red alert within 5 seconds.
"""
import argparse, json, random, time, urllib.request

TAGS = {2: "tag-psu-01", 6: "tag-osc-01", 7: "tag-thermo-01", 8: "tag-osc-02", 11: "tag-scan-01", 12: "tag-cad-01"}


def send(url, key, payload):
    req = urllib.request.Request(url.rstrip("/") + "/api/sensor", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "X-Device-Key": key}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--key", default="demo-device-key")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--interval", type=float, default=5)
    a = ap.parse_args()
    for rnd in range(1, a.rounds + 1):
        for eid, dev in TAGS.items():
            in_use = eid in (6, 8) and rnd % 2 == 1
            payload = {"equipment_id": eid, "device_id": dev,
                       "power_w": round(random.uniform(35, 60), 1) if in_use else round(random.uniform(0, 1.5), 1),
                       "in_cabinet": not (eid == 7 and rnd >= 3)}  # thermal camera disappears on round 3
            print(f"round {rnd} {dev:14} -> {payload}  HTTP {send(a.url, a.key, payload)}")
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
