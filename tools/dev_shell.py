"""Lokal utvecklingsserver för Trainyzes frontend – webbsidan och mobilappen.

Ingen databas, ingen Garmin, ingen Python-miljö behövs; bara Python 3.

    python3 tools/dev_shell.py              # påhittad data (mock), http://localhost:3100
    python3 tools/dev_shell.py --prod       # din riktiga data från trainyze.com, skrivskyddat
    python3 tools/dev_shell.py --prod --skriv   # riktig data, även ändringar tillåts
    python3 tools/dev_shell.py --lan        # nås även från telefonen (samma nät/Tailscale)

Sidor:
    /            webbsidan (public/index.html)
    /app/        mobilappen (public/app/)
    /__iphone/   iPhone 15 Pro-simulatorn som visar /app/ (tools/iphone/)

--prod skickar alla /api/-anrop vidare till trainyze.com. Du loggar in med ditt
vanliga konto; sessionskakan sparas bara för localhost. Skrivskyddat läge
släpper bara igenom inloggning och utloggning – allt annat som skulle ändra
något (plan, anteckningar, coachfrågor som kan flytta pass) stoppas med ett
tydligt besked. Lägg till --skriv när du verkligen vill testa sådant.

Servern skickar samma Content-Security-Policy som produktionen, utom att
inbäddning från samma origin tillåts (simulatorn visar appen i en iframe).
Endast för utveckling – aldrig i produktion.
"""
import argparse
import http.client
import json
import mimetypes
import random
import ssl
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / 'public'
IPHONE = Path(__file__).resolve().parent / 'iphone'
PROD_HOST = 'trainyze.com'
WRITE_ALLOWED_READONLY = {'/api/login', '/api/logout'}
mimetypes.add_type('application/manifest+json', '.webmanifest')
CSP = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'self'; "
    "script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://tile.openstreetmap.org; "
    "connect-src 'self'; "
    "form-action 'self'"
)

ARGS = None


def asset_version():
    files = [p for p in list(PUBLIC.rglob('*')) + list(IPHONE.rglob('*')) if p.is_file()]
    return str(int(max(p.stat().st_mtime for p in files)))


# ── Påhittad data ──────────────────────────────────────────────────────────
PLAN_EPOCH = date(2025, 12, 29)


def plan_week_dow(d):
    monday = d - timedelta(days=d.weekday())
    return (monday - PLAN_EPOCH).days // 7 + 1, d.weekday()


def mock_plan():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    pattern = [
        ('easy', 'Lugnt Z2', 8, 'Lugn puls, du ska kunna prata hela vägen.'),
        ('run', 'Intervaller 6×1000 m', 10, '2 km uppvärmning\n6×1000 m i 4:05/km, 2 min joggvila\n2 km nedjogg'),
        ('lift', 'Styrka – underkropp', None, 'Knäböj 4×6 · Rumänsk marklyft 3×8 · Utfall 3×10/ben'),
        ('easy', 'Återhämtning', 6, 'Mycket lugnt, gärna mjukt underlag.'),
        ('rest', 'Vila', None, 'Planerad vila.'),
        ('run', 'Tempopass', 11, '3 km uppv. + 5 km tempo i 4:20/km + 3 km nedv.'),
        ('easy', 'Långpass', 16, 'Jämnt och lugnt, sista 2 km lite snabbare.'),
    ]
    sessions = []
    for offset in range(21):
        d = monday + timedelta(days=offset)
        typ, title, km, detail = pattern[offset % 7]
        week, dow = plan_week_dow(d)
        status = 'planned'
        if d < today:
            status = 'missed' if offset == 3 else 'completed'
        sessions.append({
            'id': 1000 + offset, 'week': week, 'dow': dow, 'type': typ, 'title': title,
            'km': km, 'detail': detail, 'status': status,
            'ai_note': 'Håll igen första intervallen – HRV var lite låg i går.' if typ == 'run' and d >= today else None,
            'strength_recommendations': [], 'strength_recommendation_text': '',
        })
    return {'sessions': sessions}


def mock_today():
    today = date.today()
    plan = mock_plan()['sessions']
    week, dow = plan_week_dow(today)
    session = next((s for s in plan if s['week'] == week and s['dow'] == dow and s['type'] != 'rest'), None)
    return {
        'date': today.isoformat(), 'mode': 'active', 'action': 'keep' if session else 'no_session',
        'tone': 'good', 'headline': 'Kör passet som planerat' if session else 'Vilodag – njut av den',
        'summary': 'Kör passet som planerat',
        'detail': 'Sömnen och HRV ligger inom din normala nivå och belastningen är balanserad (ACWR 1,05).',
        'reasons': ['HRV inom baslinjen', 'Sömn 7 h 42 min', 'ACWR 1,05'],
        'signals': [], 'warnings': [], 'confidence': 'high', 'dataQuality': 'good',
        'session': session, 'proposedChange': None, 'readiness': 74,
        'checkin': {}, 'decisionId': 'mock', 'lastEvaluatedAt': time.time(),
        'coachBriefing': {
            'purpose': 'Höja tröskeln och vänja benen vid 10 km-fart.',
            'execution': 'Jämna intervaller – sista ska kännas som den första. Stanna hellre en rep tidigare än att tappa form.',
            'rpe': '7–8 av 10 på intervallerna',
            'tips': 'Ta joggvilan på riktigt; gå inte.',
            'fueling': 'Lätt måltid 2–3 h innan, vatten under passet räcker.',
        } if session else {},
    }


def mock_health():
    return {
        'date': date.today().isoformat(),
        'readiness': {'score': 71, 'level': 'HIGH', 'feedback': 'Redo'},
        'hrv': {'lastNightAvg': 62, 'weeklyAvg': 59, 'status': 'BALANCED', 'pct': 105,
                'balancedLow': 52, 'balancedUpper': 68, 'component': 80},
        'restingHR': {'value': 46, 'sevenDayAvg': 47, 'min': 44},
        'sleep': {'totalSec': 27720, 'deepSec': 5300, 'remSec': 6100, 'score': 82,
                  'deepPct': 19, 'remPct': 22, 'lightPct': 54, 'awakePct': 5,
                  'sourceDate': date.today().isoformat(), 'fallback': False},
        'bodyBattery': {'current': 78, 'max': 92, 'charged': 61, 'drained': 34},
        'stress': {'avg': 24, 'max': 71},
        'respiration': {'avg': 14, 'sleepAvg': 13},
        'spo2': {'avg': 96, 'min': 91},
    }


def mock_activities():
    out = []
    today = date.today()
    kinds = [('running', 'Intervaller', 10400, 2820, 158), ('running', 'Lugn löpning', 8100, 2950, 136),
             ('strength_training', 'Styrka', 0, 2700, 112), ('running', 'Långpass', 16200, 5600, 141)]
    for i, (typ, name, dist, dur, hr) in enumerate(kinds):
        d = today - timedelta(days=1 + i * 2)
        out.append({'activityId': 9000 + i, 'activityName': name, 'startTimeLocal': f'{d.isoformat()} 17:30:00',
                    'distance': dist, 'duration': dur, 'averageHR': hr, 'activityType': {'typeKey': typ}})
    return {'activities': out, 'source': 'mock'}


MOCK_GET = {
    '/api/session': lambda: {'authenticated': True, 'username': 'demo', 'userId': 2, 'isAdmin': False,
                             'garminConnected': True, 'stravaConfigured': False, 'stravaConnected': False,
                             'stravaAthlete': None, 'csrfToken': 'dev'},
    '/api/goals': lambda: {'goal': {'goal': 'Milen under 45 min', 'deadline': None, 'current': None, 'secondary': None}},
    '/api/today': mock_today,
    '/api/health': mock_health,
    '/api/health/spark': lambda: {'sleep': [74, 80, 68, 85, 79, 77, 82], 'hrv': [55, 58, 61, 57, 60, 59, 62],
                                  'rhr': [48, 47, 47, 49, 46, 47, 46], 'strain': [35, 62, 20, 48, 70, 15, 41]},
    '/api/plan': mock_plan,
    '/api/activities': mock_activities,
}

COACH_REPLIES = [
    'Du ligger bra till. Senaste fyra veckorna har du ökat volymen med **8 %** utan att vilopulsen stigit.\n\n'
    '- Fortsätt med en kvalitetsdag och ett långpass i veckan\n- Håll lugna pass verkligen lugna',
    'Fokusera på jämnhet i dag. Börja försiktigt – första intervallen ska kännas lite för lätt.',
    'Sömnen har varit stabil, i snitt **7 h 31 min** senaste veckan. Djupsömnen ligger på 19 %, vilket är bra.',
]


# ── Server ─────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = 'TrainyzeDev/2'

    def log_message(self, fmt, *args):  # tystare: bara fel
        if ARGS.verbose or (len(args) > 1 and str(args[1])[:1] in '45'):
            super().log_message(fmt, *args)

    # -- svar
    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        # Samma CSP som produktionen (garmin_server.secure_response), så att det
        # som blockeras på trainyze.com också blockeras här. Enda skillnaden:
        # inbäddning från samma origin tillåts, annars kan simulatorn inte visa appen.
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        for key, value in (extra or []):
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, data, status=200):
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def _redirect(self, to):
        self.send_response(302)
        self.send_header('Location', to)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _file(self, base, rel):
        file = (base / rel).resolve()
        if base.resolve() not in file.parents or not file.is_file():
            return self._send(404, b'Not Found', 'text/plain')
        body = file.read_bytes()
        if file.suffix == '.html':
            body = body.replace(b'__ASSETV__', asset_version().encode())
        ctype = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
            ctype += '; charset=utf-8'
        self._send(200, body, ctype)

    # -- routing
    def _route(self):
        path = urlparse(self.path).path
        if path.startswith('/api/'):
            return self._proxy() if ARGS.prod else self._mock(path)
        if path == '/__dev/info':
            return self._json({'mode': 'prod' if ARGS.prod else 'mock',
                               'readonly': bool(ARGS.prod and not ARGS.skriv), 'version': asset_version()})
        if path == '/__dev/version':
            return self._json({'version': asset_version()})
        if path in ('/__iphone', '/app'):
            return self._redirect(path + '/')
        if path.startswith('/__iphone/'):
            return self._file(IPHONE, path[len('/__iphone/'):] or 'index.html')
        if path == '/app/':
            return self._file(PUBLIC, 'app/index.html')
        if path in ('/', ''):
            return self._file(PUBLIC, 'index.html')
        return self._file(PUBLIC, path.lstrip('/'))

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith('/api/'):
            return self._send(405, b'Method Not Allowed', 'text/plain')
        if ARGS.prod:
            return self._proxy()
        length = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(length) or b'{}') if length else {}
        except ValueError:
            body = {}
        if path == '/api/assistant':
            time.sleep(1.2)
            return self._json({'reply': random.choice(COACH_REPLIES)})
        if path == '/api/login':
            return self._json({**MOCK_GET['/api/session'](), 'ok': True, 'username': body.get('username') or 'demo'})
        return self._json({'ok': True})

    do_PUT = do_PATCH = do_DELETE = do_POST

    def _mock(self, path):
        maker = MOCK_GET.get(path)
        return self._json(maker() if maker else {})

    def _proxy(self):
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else None
        if self.command not in ('GET', 'HEAD') and not ARGS.skriv and path not in WRITE_ALLOWED_READONLY:
            return self._json({'error': 'Skrivskyddat utvecklingsläge: ändringar mot den riktiga datan är avstängda. '
                                        'Starta med --prod --skriv för att tillåta dem.',
                               'code': 'dev_readonly'}, status=403)
        headers = {'Host': PROD_HOST, 'User-Agent': 'trainyze-dev-proxy',
                   'Accept': self.headers.get('Accept', 'application/json')}
        for name in ('Cookie', 'Content-Type', 'X-CSRF-Token'):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        try:
            conn = http.client.HTTPSConnection(PROD_HOST, timeout=90, context=ssl.create_default_context())
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except OSError as exc:
            return self._json({'error': f'trainyze.com kunde inte nås: {exc}', 'code': 'network'}, status=502)
        extra = []
        for key, value in resp.getheaders():
            if key.lower() == 'set-cookie':
                # Kakan ska gälla localhost, inte trainyze.com.
                parts = [p for p in value.split(';') if not p.strip().lower().startswith('domain=')]
                extra.append(('Set-Cookie', ';'.join(parts)))
        self._send(resp.status, data, resp.getheader('Content-Type', 'application/json'), extra)


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=3100)
    ap.add_argument('--lan', action='store_true', help='lyssna på alla nätverk (för telefonen)')
    ap.add_argument('--prod', action='store_true', help='använd riktig data från trainyze.com')
    ap.add_argument('--skriv', action='store_true', help='tillåt ändringar i --prod-läge')
    ap.add_argument('--verbose', action='store_true')
    ARGS = ap.parse_args()
    host = '0.0.0.0' if ARGS.lan else '127.0.0.1'
    mode = ('riktig data från trainyze.com' + ('' if ARGS.skriv else ' (skrivskyddat)')) if ARGS.prod else 'påhittad data'
    print(f'Trainyze dev – {mode}', flush=True)
    print(f'  Webbsidan:  http://localhost:{ARGS.port}/', flush=True)
    print(f'  Mobilappen: http://localhost:{ARGS.port}/app/', flush=True)
    print(f'  iPhone:     http://localhost:{ARGS.port}/__iphone/', flush=True)
    ThreadingHTTPServer((host, ARGS.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
