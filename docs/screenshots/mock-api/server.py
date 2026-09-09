"""A stand-in CIMA WebDrops platform, for the documentation screenshot harness.

Why this exists rather than a live account: see the "Mocking the source"
section of the capture harness README in the adl repo. A guide's screenshots
have to be reproducible from the repos alone, must expose no country's data,
and must show a *healthy* instance, which means the newest observation has to
be "now" every time the capture runs.

What is recorded and what is not
--------------------------------
The sensor classes and their units below are transcribed from
``stations_sample.json`` in this plugin's source, a recorded response from a
real WebDrops deployment. They are the vendor's taxonomy — the words an
operator reads in the *Cima Sensor* select — so they are verbatim.

Station names, coordinates and sensor ids are demo values. In the recording
they name a national hydrological network's real gauges; a WebDrops station id
is *derived from its coordinates*, so keeping them would put a country's site
locations into published screenshots for no documentary gain. They are
replaced with the three stations ADL's own demo seed creates, so the guide's
walkthrough reads as one coherent instance.

Readings are synthesised at request time, so the freshness layer of the
ingestion diagnostic is honest rather than staged.

Clocks: ADL writes the request window and reads the response back in the
connection's *Stations Timezone* — the plugin formats both bounds naive and
parses the returned "YYYYMMDDHHMM" naive — so this stub works entirely in
SAMPLE_TZ, which compose.mock.yml sets to the same zone as fixture.json.
Answering in UTC instead puts the newest reading three hours in the past, and
the diagnostic reports stale data on a source that is perfectly healthy.
"""
import json
import math
import os
import random
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

CLIENT_ID = os.environ.get("MOCK_CIMA_CLIENT_ID", "adl-demo")
USERNAME = os.environ.get("MOCK_CIMA_USERNAME", "demo")
PASSWORD = os.environ.get("MOCK_CIMA_PASSWORD", "demo-password")
ACCESS_TOKEN = "demo-access-token"
SAMPLE_TZ = ZoneInfo(os.environ.get("SAMPLE_TZ", "Africa/Nairobi"))
INTERVAL_MINUTES = 15

# class -> (unit, sensor-id suffix). The units are verbatim from the recorded
# catalogue. The suffix is spelled out rather than derived from the class's
# position in this mapping: a sensor id appears in fixture.json (inside each
# mapping's "CLASS:sensor_id") and in the captured *Cima Sensor* select, so it
# has to stay readable and has to survive adding a class to this list.
CLASS_UNITS = {
    "ANEMOMETRO": ("m/s", "00"),
    "ANEMOMETRO_RAFFICA": ("m/s", "01"),
    "BAROMETRO": ("hPa", "02"),
    "BATTERIA": ("V", "03"),
    "DIREZIONEVENTO": ("Degrees", "04"),
    "DIREZIONEVENTO_RAFFICA": ("Degrees", "05"),
    "IGROMETRO": ("%", "06"),
    "PLUVIOMETRO": ("mm", "07"),
    "SIGNAL_STRENGTH": ("CSQ", "08"),
    "TERMOMETRO": ("°C", "09"),
    "TERMOMETRO_INTERNA": ("°C", "10"),
}

# The three stations ADL's demo seed creates, with their coordinates. A
# WebDrops station id is generated from the coordinates, which is why they are
# spelled out to five decimal places here: the plugin will derive
# "-1.25000_36.74000" and the fixture has to name the same string.
DEMO_STATIONS = [
    {"name": "Kabete Demo AWS", "lat": -1.25, "lng": 36.74, "sensor_prefix": "1001"},
    {"name": "Lodwar Demo AWS", "lat": 3.12, "lng": 35.60, "sensor_prefix": "1002"},
    {"name": "Mombasa Demo AWS", "lat": -4.03, "lng": 39.62, "sensor_prefix": "1003"},
]

RANGES = {
    "TERMOMETRO": (18.0, 31.0), "IGROMETRO": (35.0, 92.0),
    "BAROMETRO": (1008.0, 1018.0), "PLUVIOMETRO": (0.0, 2.4),
    "ANEMOMETRO": (0.2, 7.5), "DIREZIONEVENTO": (0.0, 359.0),
    "ANEMOMETRO_RAFFICA": (0.5, 12.0), "DIREZIONEVENTO_RAFFICA": (0.0, 359.0),
    "TERMOMETRO_INTERNA": (20.0, 34.0), "BATTERIA": (12.1, 13.8),
    "SIGNAL_STRENGTH": (12.0, 31.0),
}


def sensor_id(station, sensor_class):
    """A stable sensor id per (station, class), in the vendor's shape."""
    return f"{station['sensor_prefix']}{CLASS_UNITS[sensor_class][1]}_2"


def sensors_for_class(sensor_class):
    return [
        {
            "id": sensor_id(st, sensor_class),
            "name": st["name"],
            "lat": st["lat"],
            "lng": st["lng"],
            "mu": CLASS_UNITS[sensor_class][0],
        }
        for st in DEMO_STATIONS
    ]


def _value(sensor_class, when):
    """A deterministic-per-(class, minute) reading inside the class's range, so
    two runs of the capture over the same window agree."""
    low, high = RANGES.get(sensor_class, (0.0, 1.0))
    rnd = random.Random(f"{sensor_class}-{when:%Y%m%d%H%M}")
    phase = math.sin((when.hour * 60 + when.minute) / 1440 * 2 * math.pi)
    mid, span = (low + high) / 2, (high - low) / 2
    return round(mid + span * phase * 0.8 + rnd.uniform(-span * 0.15, span * 0.15), 2)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self):
        return self.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if not path.endswith("/token"):
            return self._send(404, {"error": "not_found"})
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode())
        given = {k: (v or [""])[0] for k, v in form.items()}
        # Checking the grant is the point of a stub over canned JSON: the
        # guide's feedback catalogue has rows for a rejected password grant,
        # and they have to be capturable on purpose.
        if (given.get("client_id") != CLIENT_ID
                or given.get("username") != USERNAME
                or given.get("password") != PASSWORD):
            return self._send(401, {"error": "invalid_grant",
                                    "error_description": "Invalid user credentials"})
        return self._send(200, {"access_token": ACCESS_TOKEN, "expires_in": 300,
                                "token_type": "Bearer"})

    def do_GET(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if not self._authorised():
            return self._send(401, {"error": "unauthorized"})

        if parts == ["sensors", "classes"]:
            return self._send(200, sorted(CLASS_UNITS))

        if len(parts) == 3 and parts[:2] == ["sensors", "list"]:
            return self._send(200, sensors_for_class(parts[2]))
        if len(parts) == 4 and parts[:2] == ["sensors", "data"]:
            return self._data(parts[2], parse_qs(parsed.query))
        return self._send(404, {"error": "not_found"})

    def _data(self, sensor_class, query):
        fmt = "%Y%m%d%H%M"
        try:
            start = datetime.strptime((query.get("from") or [""])[0], fmt)
            end = datetime.strptime((query.get("to") or [""])[0], fmt)
        except ValueError:
            return self._send(400, {"error": "from and to are required"})

        # The window arrived in station-local time, so "now" is read there too.
        now = datetime.now(SAMPLE_TZ).replace(tzinfo=None)
        end = min(end, now)

        step = timedelta(minutes=INTERVAL_MINUTES)
        when = start.replace(second=0, microsecond=0)
        when += timedelta(minutes=(-when.minute) % INTERVAL_MINUTES)

        timeline, values = [], []
        while when <= end:
            timeline.append(when.strftime("%Y%m%d%H%M"))
            values.append(_value(sensor_class, when))
            when += step

        return self._send(200, [{"timeline": timeline, "values": values}])


if __name__ == "__main__":
    ThreadingHTTPServer(("", 80), Handler).serve_forever()
