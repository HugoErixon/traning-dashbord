from flask import Flask, request, jsonify, send_from_directory, g as flask_g, session
from garminconnect import Garmin
from pathlib import Path
from dotenv import dotenv_values
from urllib.parse import urlparse
import base64
import hmac
import hashlib
import json
import logging
import secrets
import shutil
import time
import requests
import psycopg2
import psycopg2.extras
import subprocess
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import os
import yaml
import re
import strava_integration
from apscheduler.schedulers.background import BackgroundScheduler
try:
    from pywebpush import webpush, WebPushException
except ImportError:  # notiser ar valfria - appen ska starta anda
    webpush, WebPushException = None, Exception
try:
    import paho.mqtt.client as mqtt_client
except ImportError:  # klimatavlasningar ar valfria - appen ska starta anda
    mqtt_client = None
from werkzeug.exceptions import HTTPException
from security import LoginRateLimiter, parse_users, verify_user
from user_store import MemoryUserStore, DbUserStore, DuplicateUserError, UserStoreError
from ai_control import AiControlStore
from adaptive_plan import AdaptivePlanStore, evaluate as evaluate_adaptive_plan
from lifestyle import LifestyleStore, analyze_impacts
from activity_feedback import ActivityFeedbackStore
from strength_progression import (
    build_default_recommendations,
    build_strength_recommendations,
    recommendation_summary,
)
import session_analysis
import pace_progression
import sleep_analysis
import strain_analysis
import training_analysis
from activity_detail import normalize_activity_detail

try:
    from webauthn import (
        base64url_to_bytes,
        generate_authentication_options,
        generate_registration_options,
        options_to_json,
        verify_authentication_response,
        verify_registration_response,
    )
    from webauthn.helpers.structs import (
        AuthenticatorAttachment,
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )
    WEBAUTHN_AVAILABLE = True
except ImportError:
    WEBAUTHN_AVAILABLE = False

# Google Calendar (valfritt — kräver google_credentials.json)
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request as GRequest
    from googleapiclient.discovery import build as gbuild
    GCAL_AVAILABLE = True
except ImportError:
    GCAL_AVAILABLE = False

def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


class _JsonLogFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'message': record.getMessage(),
            'logger': record.name,
        }
        for field in ('event', 'request_id', 'method', 'path', 'status', 'duration_ms',
                      'user_id', 'activity_id', 'activities', 'delivered'):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


logger = logging.getLogger('training_dashboard')
if not logger.handlers:
    _log_handler = logging.StreamHandler()
    _log_handler.setFormatter(_JsonLogFormatter())
    logger.addHandler(_log_handler)
logger.setLevel(logging.INFO)
logger.propagate = False

config = {**dotenv_values('.env'), **os.environ}
APP_TESTING = _as_bool(config.get('APP_TESTING'))
SESSION_SECRET = str(config.get('SESSION_SECRET') or '').strip()
if not SESSION_SECRET and APP_TESTING:
    SESSION_SECRET = 'test-session-secret-not-for-production'
if len(SESSION_SECRET) < 32:
    raise RuntimeError('SESSION_SECRET must be configured with at least 32 characters')

try:
    USERS = parse_users(config.get('USERS'), config.get('SITE_PASSWORD'))
except ValueError as exc:
    raise RuntimeError(str(exc)) from exc

app = Flask(__name__, static_folder='public')
app.config.update(
    SECRET_KEY=SESSION_SECRET,
    SESSION_COOKIE_NAME='training_session',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=_as_bool(config.get('SESSION_COOKIE_SECURE')),
    SESSION_COOKIE_SAMESITE='Strict',
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_REFRESH_EACH_REQUEST=True,
    MAX_CONTENT_LENGTH=1024 * 1024,
    TESTING=APP_TESTING,
)

for _username, _user in USERS.items():
    if not _user['password_hashed']:
        logger.warning('Legacy plaintext credential configured; run the auth migration', extra={
            'event': 'auth.legacy_password',
            'user_id': _user['id'],
        })

ANTHROPIC_KEY = config.get('ANTHROPIC_API_KEY', '')

# --- AI-leverantörer ---------------------------------------------------------
# Flera leverantörer kan kedjas: LLM_PROVIDERS=gemini,cerebras,groq. Kedjan
# används i ordning och nästa tas bara vid kvot- eller nätverksfel, aldrig vid
# ett riktigt fel som en trasig prompt — det felet skulle upprepas hos alla och
# bara bränna kvot. Poängen är att kunna stapla flera gratisnivåer på varandra.
GEMINI_API_KEY  = config.get('GEMINI_API_KEY', '')
GEMINI_MODEL    = config.get('GEMINI_MODEL', 'gemini-flash-latest')
ANTHROPIC_MODEL = config.get('ANTHROPIC_MODEL', 'claude-sonnet-4-6')

# Groq, Cerebras, OpenRouter och Mistral talar alla OpenAI:s chat-completions,
# så samma adapter räcker för alla fyra — bara URL, modell och nyckel skiljer.
# Modellnamnen byts ut ofta hos leverantörerna; sätt <NAMN>_MODEL i .env om
# defaulten slutar finnas.
OPENAI_COMPATIBLE_PROVIDERS = {
    'groq': ('https://api.groq.com/openai/v1/chat/completions', 'llama-3.3-70b-versatile'),
    # gpt-oss-120b är Cerebras enda produktionsmodell; övriga är preview och
    # en av dem har redan ett avvecklingsdatum. Kontrollerad 2026-08-02.
    'cerebras': ('https://api.cerebras.ai/v1/chat/completions', 'gpt-oss-120b'),
    'openrouter': ('https://openrouter.ai/api/v1/chat/completions', 'meta-llama/llama-3.3-70b-instruct:free'),
    'mistral': ('https://api.mistral.ai/v1/chat/completions', 'mistral-small-latest'),
}


def _provider_spec(name):
    """Nyckel, modell och URL för en leverantör, eller None om namnet är okänt.

    Slås upp vid anropet i stället för vid import: konfigurationen ska kunna
    bytas ut utan att modulen laddas om, och nyckeln läses från modulens egna
    globaler så att den går att ersätta i test."""
    if name == 'gemini':
        return {'kind': 'gemini', 'key': GEMINI_API_KEY, 'model': GEMINI_MODEL,
                'label': 'Gemini'}
    if name == 'anthropic':
        return {'kind': 'anthropic', 'key': ANTHROPIC_KEY, 'model': ANTHROPIC_MODEL,
                'label': 'Anthropic'}
    if name in OPENAI_COMPATIBLE_PROVIDERS:
        url, default_model = OPENAI_COMPATIBLE_PROVIDERS[name]
        return {
            'kind': 'openai',
            'key': config.get(f'{name.upper()}_API_KEY', ''),
            'model': config.get(f'{name.upper()}_MODEL', default_model),
            'url': config.get(f'{name.upper()}_URL', url),
            'label': name.capitalize(),
        }
    return None


def _provider_configured(name):
    spec = _provider_spec(name)
    if not spec or not spec['key']:
        return False
    if name == 'anthropic' and spec['key'].startswith('sk-ant-placeholder'):
        return False
    return True


def _resolve_llm_chain():
    """Leverantörskedjan i prioritetsordning.

    LLM_PROVIDERS är den nya formen. LLM_PROVIDER (singular) stöds fortfarande
    så att befintliga .env-filer fungerar oförändrat.

    Utan någon av dem används EN leverantör, precis som förr. Kedjan byggs
    aldrig automatiskt, för flera av leverantörerna kostar pengar och en tyst
    fallback till en betald tjänst är inget man ska kunna råka ut för — den
    som vill kedja får skriva ut ordningen själv."""
    raw = config.get('LLM_PROVIDERS') or config.get('LLM_PROVIDER') or ''
    names = [n.strip().lower() for n in raw.split(',') if n.strip()]
    if not names:
        names = ['gemini'] if GEMINI_API_KEY else ['anthropic']
    seen, chain = set(), []
    for name in names:
        if _provider_spec(name) and name not in seen:
            seen.add(name)
            chain.append(name)
    return chain


LLM_CHAIN = _resolve_llm_chain()
# Behålls för bakåtkompatibilitet: en del kod och tester läser den enskilda
# leverantören.
LLM_PROVIDER = LLM_CHAIN[0] if LLM_CHAIN else 'gemini'

# När en leverantör svarat 429 är det slöseri att fortsätta fråga den under
# hela väntetiden — nästa request hoppar direkt vidare i kedjan i stället.
_llm_cooldowns = {}
_llm_cooldown_guard = threading.Lock()
LLM_TRANSIENT_COOLDOWN = float(config.get('LLM_TRANSIENT_COOLDOWN', '30'))
# En leverantor som saknar giltigt konto ar inte tillfalligt nere - det ar
# ingen ide att fraga igen forran nagon gjort nagot at saken.
LLM_DISABLED_COOLDOWN = float(config.get('LLM_DISABLED_COOLDOWN', '3600'))
AUTH_FAILURE_CODES = (401, 402, 403)


def _llm_cooldown_remaining(name):
    with _llm_cooldown_guard:
        return max(0.0, _llm_cooldowns.get(name, 0.0) - time.time())


def _set_llm_cooldown(name, seconds):
    if not seconds or seconds <= 0:
        return
    with _llm_cooldown_guard:
        _llm_cooldowns[name] = max(_llm_cooldowns.get(name, 0.0), time.time() + seconds)


def reset_llm_cooldowns():
    with _llm_cooldown_guard:
        _llm_cooldowns.clear()


def llm_available():
    return any(_provider_configured(name) for name in LLM_CHAIN)


class LLMQuotaError(RuntimeError):
    """Leverantören avvisade anropet på grund av kvot/rate limit (HTTP 429).

    Egen typ så att anropsställena kan skilja "vi har slut på kvot just nu" —
    ett väntat, övergående tillstånd som ska falla tillbaka på cache — från
    riktiga fel som en trasig prompt eller ett ogiltigt svar."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# Gemini svarar på 429 med hur länge man bör vänta. Är väntan kort beror den på
# att vi själva sköt iväg flera anrop på en gång, och då är ett omförsök rätt.
# Är den lång är dygns-/minutkvoten faktiskt slut och då ska vi fela snabbt
# i stället för att låta en request hänga — anropsställena har cache att falla
# tillbaka på.
LLM_RETRY_MAX_WAIT = float(config.get('LLM_RETRY_MAX_WAIT', '10'))


def _gemini_retry_after(error_obj):
    """Plocka ut föreslagen väntetid (sekunder) ur ett Gemini-felsvar."""
    for detail in error_obj.get('details') or []:
        delay = detail.get('retryDelay')
        if isinstance(delay, str) and delay.endswith('s'):
            try:
                return float(delay[:-1])
            except ValueError:
                pass
    match = re.search(r'retry in ([0-9.]+)s', error_obj.get('message') or '')
    return float(match.group(1)) if match else None


class LLMTransientError(RuntimeError):
    """Leverantören var tillfälligt onåbar (nätverksfel eller 5xx).

    Skiljs från kvotfel eftersom den inte har någon meningsfull väntetid, men
    är precis som kvotfel något som ska få kedjan att gå vidare till nästa."""


class LLMUnavailableError(RuntimeError):
    """Leverantören avvisade oss av konto-skäl: ogiltig nyckel, obetald faktura
    eller saknad behörighet (401/402/403).

    Kedjan ska gå vidare — en leverantör utan konto är oanvändbar, inte ett
    tecken på att prompten är trasig. Men den ska parkeras länge: det hjälper
    inte att fråga igen om trettio sekunder när det som saknas är en
    betalningsmetod."""


# Chatten ska hänga ihop över flera frågor, så tidigare turer följer med in i
# anropet. Historiken kommer från klienten och normaliseras därför hårt: både
# leverantörerna (som kräver att första turen är användarens och att rollerna
# växlar) och prompt-kostnaden sätter gränser.
CHAT_HISTORY_MAX_MESSAGES = 16
CHAT_HISTORY_MAX_CHARS = 8000
CHAT_MESSAGE_MAX_CHARS = 2000


def normalize_history(history):
    """Gör klientens samtalshistorik till en säker, strikt växlande lista.

    Resultatet börjar alltid på en användartur och slutar på en assistenttur —
    den aktuella frågan skickas separat och läggs till efteråt. Blir samtalet
    för långt faller de äldsta turerna bort först; det är sista utbytena som
    bär "det"/"den" som frågan syftar på."""
    if not isinstance(history, list):
        return []

    cleaned = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role, content = item.get('role'), item.get('content')
        if role not in ('user', 'assistant') or not isinstance(content, str):
            continue
        content = content.strip()[:CHAT_MESSAGE_MAX_CHARS]
        if not content:
            continue
        if cleaned and cleaned[-1]['role'] == role:
            # Två turer i rad från samma part avvisas av flera leverantörer.
            cleaned[-1]['content'] = f"{cleaned[-1]['content']}\n\n{content}"[:CHAT_MESSAGE_MAX_CHARS]
            continue
        cleaned.append({'role': role, 'content': content})

    # En avslutande användartur är den aktuella frågan igen — släng den hellre
    # än att låta modellen se samma fråga två gånger.
    while cleaned and cleaned[-1]['role'] == 'user':
        cleaned.pop()

    del cleaned[:-CHAT_HISTORY_MAX_MESSAGES]
    while cleaned and (cleaned[0]['role'] == 'assistant'
                       or sum(len(m['content']) for m in cleaned) > CHAT_HISTORY_MAX_CHARS):
        cleaned.pop(0)
    return cleaned


def _call_gemini(prompt, max_tokens, system, timeout, spec, allow_wait, history=None):
    turns = [{'role': 'model' if m['role'] == 'assistant' else 'user',
              'parts': [{'text': m['content']}]} for m in (history or [])]
    body = {'contents': turns + [{'role': 'user', 'parts': [{'text': prompt}]}]}
    if system:
        body['system_instruction'] = {'parts': [{'text': system}]}

    for attempt in (1, 2):
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{spec['model']}:generateContent",
            json=body,
            headers={'x-goog-api-key': spec['key'], 'Content-Type': 'application/json'},
            timeout=timeout)
        rj = resp.json()
        if 'error' not in rj:
            break
        err = rj['error']
        code = err.get('code')
        if code != 429:
            if code in AUTH_FAILURE_CODES:
                raise LLMUnavailableError(f"Gemini {code}: {err.get('message')}")
            if isinstance(code, int) and code >= 500:
                raise LLMTransientError(f"Gemini {code}: {err.get('message')}")
            raise RuntimeError(f"Gemini {code}: {err.get('message')}")
        wait = _gemini_retry_after(err)
        # Att sova är bara värt det när ingen annan leverantör kan ta över —
        # annars är det snabbare att falla vidare i kedjan direkt.
        if attempt == 2 or not allow_wait or wait is None or wait > LLM_RETRY_MAX_WAIT:
            raise LLMQuotaError(f"Gemini 429: {err.get('message')}", retry_after=wait)
        logger.warning('LLM rate limited, retrying', extra={
            'event': 'llm.rate_limited', 'retry_after_s': wait, 'model': spec['model']})
        time.sleep(wait)

    try:
        return rj['candidates'][0]['content']['parts'][0]['text']
    except (KeyError, IndexError, TypeError) as exc:
        finish = ((rj.get('candidates') or [{}])[0]).get('finishReason', 'okänt')
        raise RuntimeError(f'Gemini gav tomt svar (finishReason: {finish})') from exc


def _call_anthropic(prompt, max_tokens, system, timeout, spec, allow_wait, history=None):
    payload = {'model': spec['model'], 'max_tokens': max_tokens,
               'messages': list(history or []) + [{'role': 'user', 'content': prompt}]}
    if system:
        payload['system'] = system
    resp = requests.post('https://api.anthropic.com/v1/messages',
        json=payload,
        headers={'x-api-key': spec['key'], 'anthropic-version': '2023-06-01',
                 'content-type': 'application/json'}, timeout=timeout)
    rj = resp.json()
    if 'error' in rj:
        message = rj['error'].get('message')
        if resp.status_code == 429 or rj['error'].get('type') == 'rate_limit_error':
            try:
                wait = float(resp.headers.get('retry-after', ''))
            except (TypeError, ValueError):
                wait = None
            raise LLMQuotaError(f'Anthropic: {message}', retry_after=wait)
        if resp.status_code in AUTH_FAILURE_CODES:
            raise LLMUnavailableError(f'Anthropic {resp.status_code}: {message}')
        if resp.status_code >= 500:
            raise LLMTransientError(f'Anthropic {resp.status_code}: {message}')
        raise RuntimeError(f'Anthropic: {message}')
    try:
        return rj['content'][0]['text']
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError('Anthropic gav tomt svar') from exc


def _call_openai_compatible(prompt, max_tokens, system, timeout, spec, allow_wait, history=None):
    """Groq, Cerebras, OpenRouter, Mistral — samma chat-completions-format."""
    messages = ([{'role': 'system', 'content': system}] if system else []) + \
               list(history or []) + [{'role': 'user', 'content': prompt}]
    resp = requests.post(spec['url'],
        json={'model': spec['model'], 'max_tokens': max_tokens, 'messages': messages},
        headers={'Authorization': f"Bearer {spec['key']}",
                 'Content-Type': 'application/json'}, timeout=timeout)
    if resp.status_code == 429:
        try:
            wait = float(resp.headers.get('retry-after', ''))
        except (TypeError, ValueError):
            wait = None
        raise LLMQuotaError(f'{spec["label"]} 429: {resp.text[:200]}', retry_after=wait)
    if resp.status_code in AUTH_FAILURE_CODES:
        raise LLMUnavailableError(
            f'{spec["label"]} {resp.status_code}: {resp.text[:200]}')
    if resp.status_code >= 500:
        raise LLMTransientError(f'{spec["label"]} {resp.status_code}')
    rj = resp.json()
    if resp.status_code >= 400 or 'error' in rj:
        detail = rj.get('error') if isinstance(rj.get('error'), str) else \
                 (rj.get('error') or {}).get('message', resp.text[:200])
        raise RuntimeError(f'{spec["label"]}: {detail}')
    try:
        return rj['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f'{spec["label"]} gav tomt svar') from exc


_LLM_CALLERS = {'gemini': _call_gemini, 'anthropic': _call_anthropic,
                'openai': _call_openai_compatible}


def call_llm(prompt, max_tokens=1024, system=None, timeout=45, history=None):
    """Skicka en prompt till leverantörskedjan och returnera svarstexten.

    `history` är tidigare turer i samma samtal ({'role', 'content'}), redan
    normaliserade med normalize_history() — de skickas som riktiga chattturer
    så att uppföljningsfrågor förstår vad "det" syftar på.

    Går igenom LLM_CHAIN i ordning och hoppar över leverantörer som nyligen
    svarat 429. Faller vidare vid kvot- och nätverksfel; ett riktigt fel (trasig
    prompt, tomt svar) kastas direkt eftersom det skulle upprepas överallt.
    Kastar LLMQuotaError om alla leverantörer är slut på kvot."""
    configured = [name for name in LLM_CHAIN if _provider_configured(name)]
    if not configured:
        raise RuntimeError('Ingen AI-leverantör är konfigurerad.')

    ready = [name for name in configured if not _llm_cooldown_remaining(name)]
    # Är allt nedkylt är ett försök ändå bättre än ett säkert nej — kvoten kan
    # ha återställts tidigare än leverantören gissade.
    order = ready or configured

    last_error = None
    for position, name in enumerate(order):
        spec = _provider_spec(name)
        caller = _LLM_CALLERS[spec['kind']]
        try:
            text = caller(prompt, max_tokens, system, timeout, spec,
                          allow_wait=(position == len(order) - 1), history=history)
            if position > 0:
                logger.info('LLM served by fallback provider', extra={
                    'event': 'llm.fallback_used', 'provider': name,
                    'model': spec['model'], 'position': position})
            return text
        except LLMQuotaError as exc:
            _set_llm_cooldown(name, exc.retry_after or LLM_TRANSIENT_COOLDOWN)
            logger.warning('LLM provider out of quota', extra={
                'event': 'llm.quota', 'provider': name,
                'retry_after_s': exc.retry_after})
            last_error = exc
        except LLMUnavailableError as exc:
            _set_llm_cooldown(name, LLM_DISABLED_COOLDOWN)
            logger.warning('LLM provider rejected our account', extra={
                'event': 'llm.unavailable', 'provider': name,
                'detail': str(exc)[:200]})
            last_error = exc
        except (LLMTransientError, requests.RequestException) as exc:
            _set_llm_cooldown(name, LLM_TRANSIENT_COOLDOWN)
            logger.warning('LLM provider unreachable', extra={
                'event': 'llm.transient', 'provider': name, 'detail': str(exc)[:200]})
            last_error = LLMTransientError(str(exc))

    raise last_error


TOKEN_DIR     = str(Path.home() / '.garminconnect')
DATABASE_URL  = config.get('DATABASE_URL', '')
GCAL_ID       = config.get('GOOGLE_CALENDAR_ID', 'primary')
GCAL_CREDS    = 'google_credentials.json'
GCAL_SCOPES   = ['https://www.googleapis.com/auth/calendar.readonly']
LOCAL_TZ      = ZoneInfo('Europe/Stockholm')
ENABLE_HSTS   = _as_bool(config.get('ENABLE_HSTS'))
LOGIN_LIMITER = LoginRateLimiter(
    max_attempts=int(config.get('LOGIN_MAX_ATTEMPTS', '8')),
    window_seconds=int(config.get('LOGIN_WINDOW_SECONDS', '900')),
)
# Andra skiktet: begränsar totala inloggningsförsök per IP oavsett användarnamn,
# så att en angripare inte kan spraya olika konton från samma adress obehindrat.
LOGIN_IP_LIMITER = LoginRateLimiter(
    max_attempts=int(config.get('LOGIN_IP_MAX_ATTEMPTS', '20')),
    window_seconds=int(config.get('LOGIN_WINDOW_SECONDS', '900')),
)
REGISTER_LIMITER = LoginRateLimiter(
    max_attempts=int(config.get('REGISTER_MAX_ATTEMPTS', '3')),
    window_seconds=int(config.get('REGISTER_WINDOW_SECONDS', '3600')),
)
FORGOT_PASSWORD_LIMITER = LoginRateLimiter(
    max_attempts=int(config.get('FORGOT_PASSWORD_MAX_ATTEMPTS', '5')),
    window_seconds=int(config.get('FORGOT_PASSWORD_WINDOW_SECONDS', '3600')),
)
RESEND_API_KEY = config.get('RESEND_API_KEY', '')
MAIL_FROM = config.get('MAIL_FROM', 'Trainyze <noreply@trainyze.com>')
PUBLIC_BASE_URL = config.get('PUBLIC_BASE_URL', 'https://trainyze.com')
_public_url = urlparse(PUBLIC_BASE_URL)
AI_CONTROL_ENABLED = _as_bool(config.get('AI_CONTROL_ENABLED'))
AI_RP_ID = str(config.get('AI_RP_ID') or _public_url.hostname or 'trainyze.com').strip()
AI_ORIGIN = str(config.get('AI_ORIGIN') or
                f'{_public_url.scheme or "https"}://{_public_url.netloc or AI_RP_ID}').rstrip('/')
AI_PASSKEY_BOOTSTRAP_TOKEN = str(config.get('AI_PASSKEY_BOOTSTRAP_TOKEN') or '')
AI_AGENT_TOKEN = str(config.get('AI_AGENT_TOKEN') or '')
AI_STEP_UP_TTL_SECONDS = min(max(int(config.get('AI_STEP_UP_TTL_SECONDS', '600')), 60), 1800)
STRAVA_CLIENT_ID = str(config.get('STRAVA_CLIENT_ID') or '').strip()
STRAVA_CLIENT_SECRET = str(config.get('STRAVA_CLIENT_SECRET') or '').strip()
STRAVA_REDIRECT_URI = str(config.get('STRAVA_REDIRECT_URI') or
                          f"{PUBLIC_BASE_URL.rstrip('/')}/strava/callback").strip()
STRAVA_TOKEN_ROOT = Path(config.get('STRAVA_TOKEN_DIR') or (Path.home() / '.strava'))

def uid():
    return getattr(flask_g, 'uid', 1)

def uname():
    return getattr(flask_g, 'uname', list(USERS.keys())[0] if USERS else 'hugo')

def gcal_token():
    return f'google_token_{uname()}.json'

# Kvar efter att AC-styrningen togs bort: vattenlarmet skriver fortfarande
# lockout-flaggan bredvid AC-flaggan, och pingar keepern om den skulle vara igång.
AC_KEEPER_URL = config.get('AC_KEEPER_URL', 'http://127.0.0.1:8089')
AC_CONTROL_FLAG = config.get('AC_CONTROL_FLAG', '/home/hugoerixon/tuya-ac-keeper/data/control_enabled')
WATER_TOKEN = config.get('WATER_TOKEN', '')  # delad hemlighet för ESP32-vattensensorn
AC_BUTTON_TOKEN = config.get('AC_BUTTON_TOKEN', WATER_TOKEN)  # fysisk ESP32-knapp, fallback till vatten-token
# Lockout-flagga: ligger i samma katalog som AC-flaggan (keeperns data/-katalog).
WATER_LOCKOUT_FLAG = config.get('WATER_LOCKOUT_FLAG', os.path.join(os.path.dirname(AC_CONTROL_FLAG), 'water_lockout'))
# --- Klimatsensorer (Zigbee → zigbee2mqtt → MQTT) ---
# Dashboarden prenumererar sjalv pa MQTT. Tidigare gick avlasningarna omvagen via
# ac-keeper, vilket band ihop ren avlasning med AC-styrning; nar keepern togs bort
# forsvann aven temperaturen ur granssnittet.
MQTT_HOST = config.get('MQTT_HOST', '127.0.0.1')
MQTT_PORT = int(config.get('MQTT_PORT', '1883'))
MQTT_USERNAME = config.get('MQTT_USERNAME', '')
MQTT_PASSWORD = config.get('MQTT_PASSWORD', '')
MQTT_BASE_TOPIC = config.get('MQTT_BASE_TOPIC', 'zigbee2mqtt')
MQTT_ENABLED = config.get('MQTT_ENABLED', '1').strip().lower() not in ('0', 'false', 'off', 'no')
# En sensor som inte horts av pa sa har lange raknas som tyst i granssnittet.
# SNZB-02P rapporterar normalt var femte minut; en halvtimmes tystnad ar ett fel.
CLIMATE_STALE_SECONDS = int(config.get('CLIMATE_STALE_SECONDS', '1800'))
CLIMATE_RETENTION_DAYS = int(config.get('CLIMATE_RETENTION_DAYS', '90'))

WEATHER_LAT = float(config.get('WEATHER_LAT', '58.35593'))
WEATHER_LON = float(config.get('WEATHER_LON', '11.22411'))
WEATHER_LOCATION = config.get('WEATHER_LOCATION', 'Smögen')

if not APP_TESTING and (len(WATER_TOKEN) < 16 or len(AC_BUTTON_TOKEN) < 16):
    logger.warning('Hardware API token is missing or too short', extra={'event': 'auth.weak_hardware_token'})

def _send_verification_email(to_email, username, token):
    """Skickar verifieringslänk via Resend. Returnerar True/False (loggar fel, kastar aldrig)."""
    if not RESEND_API_KEY:
        logger.error('Cannot send verification email: RESEND_API_KEY not configured',
                      extra={'event': 'mail.no_api_key'})
        return False
    link = f"{PUBLIC_BASE_URL.rstrip('/')}/api/verify-email?token={token}"
    html = f'''<div style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2 style="color:#111;">Välkommen till Trainyze, {username}!</h2>
      <p>Klicka på länken nedan för att verifiera din e-postadress och aktivera ditt konto:</p>
      <p><a href="{link}" style="display:inline-block;background:#C8F135;color:#1a2200;
         padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:700;">
         Verifiera e-postadress</a></p>
      <p style="color:#666;font-size:13px;">Länken är giltig i 24 timmar. Om du inte skapade
      det här kontot kan du ignorera mejlet.</p>
    </div>'''
    try:
        r = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            json={'from': MAIL_FROM, 'to': [to_email], 'subject': 'Verifiera din e-postadress',
                  'html': html},
            timeout=8,
        )
        if not r.ok:
            logger.error('Verification email rejected by Resend', extra={
                'event': 'mail.send_failed', 'status': r.status_code, 'body': r.text[:300],
            })
            return False
        return True
    except Exception as e:
        logger.exception('Verification email send failed', extra={'event': 'mail.send_exception'})
        return False


def _send_password_reset_email(to_email, username, token):
    """Skickar återställningslänk via Resend. Returnerar True/False (loggar fel, kastar aldrig)."""
    if not RESEND_API_KEY:
        logger.error('Cannot send password reset email: RESEND_API_KEY not configured',
                      extra={'event': 'mail.no_api_key'})
        return False
    link = f"{PUBLIC_BASE_URL.rstrip('/')}/index.html?reset={token}"
    html = f'''<div style="font-family:sans-serif;max-width:480px;margin:0 auto;">
      <h2 style="color:#111;">Återställ ditt lösenord</h2>
      <p>Hej {username}, klicka på länken nedan för att välja ett nytt lösenord:</p>
      <p><a href="{link}" style="display:inline-block;background:#C8F135;color:#1a2200;
         padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:700;">
         Återställ lösenord</a></p>
      <p style="color:#666;font-size:13px;">Länken är giltig i 1 timme. Om du inte bad om detta
      kan du ignorera mejlet — ditt lösenord ändras inte.</p>
    </div>'''
    try:
        r = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            json={'from': MAIL_FROM, 'to': [to_email], 'subject': 'Återställ ditt lösenord',
                  'html': html},
            timeout=8,
        )
        if not r.ok:
            logger.error('Password reset email rejected by Resend', extra={
                'event': 'mail.send_failed', 'status': r.status_code, 'body': r.text[:300],
            })
            return False
        return True
    except Exception:
        logger.exception('Password reset email send failed', extra={'event': 'mail.send_exception'})
        return False

WEATHER_CODES = {
    0: 'klart',
    1: 'mest klart',
    2: 'halvklart',
    3: 'mulet',
    45: 'dimma',
    48: 'rimfrost-dimma',
    51: 'lätt duggregn',
    53: 'duggregn',
    55: 'kraftigt duggregn',
    61: 'lätt regn',
    63: 'regn',
    65: 'kraftigt regn',
    71: 'lätt snöfall',
    73: 'snöfall',
    75: 'kraftigt snöfall',
    80: 'lätta regnskurar',
    81: 'regnskurar',
    82: 'kraftiga regnskurar',
    95: 'åska',
}

def _get_outdoor_temperature_history(hours=24):
    """Hämta utetemperatur för grafen. Fel här ska inte slå ut rumstempgrafen."""
    end = datetime.now(LOCAL_TZ)
    start = end - timedelta(hours=hours)
    try:
        params = {
            'latitude': WEATHER_LAT,
            'longitude': WEATHER_LON,
            'hourly': 'temperature_2m',
            'timezone': 'auto',
            'start_date': start.date().isoformat(),
            'end_date': end.date().isoformat(),
        }
        r = requests.get('https://api.open-meteo.com/v1/forecast', params=params, timeout=6)
        r.raise_for_status()
        hourly = (r.json() or {}).get('hourly') or {}
        times = hourly.get('time') or []
        temps = hourly.get('temperature_2m') or []
        points = []
        for ts, temp in zip(times, temps):
            if temp is None:
                continue
            dt = datetime.fromisoformat(ts).replace(tzinfo=LOCAL_TZ)
            if start <= dt <= end:
                points.append({'t': dt.isoformat(), 'temp': temp})
        return points
    except Exception as e:
        print('weather history unavailable:', e)
        return []

# --- Databas & Connection Pool ---
try:
    from psycopg2.pool import ThreadedConnectionPool
except ImportError:
    ThreadedConnectionPool = None

_DB_POOL = None
_DB_POOL_LOCK = threading.Lock()

def _get_db_pool():
    global _DB_POOL
    if _DB_POOL is None and DATABASE_URL and ThreadedConnectionPool:
        with _DB_POOL_LOCK:
            if _DB_POOL is None:
                try:
                    _DB_POOL = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL, sslmode='prefer')
                except Exception as e:
                    logging.getLogger('training_dashboard').warning(f"Kunde inte initiera connection pool: {e}")
                    _DB_POOL = None
    return _DB_POOL

class _PooledConnContext:
    def __init__(self, pool, conn):
        self.pool = pool
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self.conn.rollback()
            except Exception:
                pass
        else:
            try:
                self.conn.commit()
            except Exception:
                pass
        try:
            self.pool.putconn(self.conn)
        except Exception:
            try:
                self.conn.close()
            except Exception:
                pass

class _DirectConnContext:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self.conn.rollback()
            except Exception:
                pass
        else:
            try:
                self.conn.commit()
            except Exception:
                pass
        try:
            self.conn.close()
        except Exception:
            pass

def db():
    """Returnerar en context-manager för databasanslutning.
    Använder ThreadedConnectionPool om tillgänglig, annars direkt anslutning."""
    pool = _get_db_pool()
    if pool:
        try:
            conn = pool.getconn()
            conn.autocommit = False
            return _PooledConnContext(pool, conn)
        except Exception:
            pass
    conn = psycopg2.connect(DATABASE_URL, sslmode='prefer')
    conn.autocommit = False
    return _DirectConnContext(conn)

def setup_db():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS activities (
                id BIGINT PRIMARY KEY, name TEXT, date TEXT, type TEXT,
                distance REAL, duration REAL, avg_hr INTEGER,
                raw JSONB, created_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY, value JSONB, updated_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS strength_exercises (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                exercise TEXT NOT NULL,
                sets INTEGER,
                reps TEXT,
                weight REAL,
                note TEXT,
                created_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS user_notes (
                id SERIAL PRIMARY KEY,
                text TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS journal_entries (
                id SERIAL PRIMARY KEY,
                entry_date TEXT NOT NULL,
                mood TEXT DEFAULT '',
                energy INTEGER,
                text TEXT NOT NULL,
                created_at REAL,
                updated_at REAL,
                user_id INTEGER DEFAULT 1,
                UNIQUE(entry_date, user_id))''')
            cur.execute('''CREATE TABLE IF NOT EXISTS plan_sessions (
                id SERIAL PRIMARY KEY,
                week INTEGER NOT NULL,
                dow INTEGER NOT NULL,
                type TEXT NOT NULL,
                km REAL DEFAULT 0,
                title TEXT NOT NULL,
                detail TEXT DEFAULT '',
                status TEXT DEFAULT 'planned',
                original_week INTEGER,
                original_dow INTEGER,
                ai_note TEXT,
                modified_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS health_history (
                date TEXT PRIMARY KEY,
                sleep_score INTEGER, sleep_hours REAL, deep_pct INTEGER, rem_pct INTEGER,
                hrv_avg INTEGER, resting_hr INTEGER, readiness INTEGER, body_battery INTEGER,
                stress_avg INTEGER, created_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS metric_history (
                date TEXT PRIMARY KEY,
                vo2max REAL, endurance_score INTEGER,
                lactate_hr INTEGER, lactate_pace REAL,
                hrv_status TEXT, created_at REAL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS user_goals (
                user_id INTEGER PRIMARY KEY,
                goal_title TEXT NOT NULL,
                goal_deadline TEXT,
                current_best TEXT,
                secondary_goal TEXT,
                start_date TEXT,
                updated_at REAL)''')
            # En rad per mottagen sensoravlasning. Ingen user_id: sensorerna sitter
            # i hemmet, inte hos en anvandare, och sidan ar agarlast anda.
            cur.execute('''CREATE TABLE IF NOT EXISTS sensor_readings (
                id BIGSERIAL PRIMARY KEY,
                sensor TEXT NOT NULL,
                ts TIMESTAMPTZ NOT NULL,
                temperature_c REAL,
                humidity_pct REAL,
                battery_pct REAL,
                linkquality INTEGER)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS sensor_readings_ts_idx
                ON sensor_readings (ts DESC)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS sensor_readings_sensor_ts_idx
                ON sensor_readings (sensor, ts DESC)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS strava_oauth_states (
                state_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at REAL NOT NULL)''')
        conn.commit()
    print('Databas: klar')

def migrate_db():
    with db() as conn:
        with conn.cursor() as cur:
            for tbl in ('activities', 'user_notes', 'journal_entries', 'plan_sessions', 'strength_exercises',
                        'health_history', 'metric_history'):
                try:
                    cur.execute(f'ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS user_id INTEGER DEFAULT 1')
                except Exception as e:
                    print(f'migrate_db {tbl} user_id:', e)
            try:
                cur.execute('ALTER TABLE health_history ADD COLUMN IF NOT EXISTS body_battery INTEGER')
            except Exception as e:
                print('migrate_db health_history body_battery:', e)
            try:
                # Hur passet faktiskt genomfördes (tempo, varv, puls, vikter)
                # jämfört med vad som var planerat — se session_analysis.py.
                cur.execute('ALTER TABLE plan_sessions ADD COLUMN IF NOT EXISTS execution JSONB')
            except Exception as e:
                print('migrate_db plan_sessions execution:', e)
            try:
                # Planens ursprungliga text sparas när ett godkänt tempoförslag
                # skriver om den, så anpassningen går att utvärdera i efterhand.
                cur.execute('ALTER TABLE plan_sessions ADD COLUMN IF NOT EXISTS detail_original TEXT')
            except Exception as e:
                print('migrate_db plan_sessions detail_original:', e)
            try:
                # Föreslagna måltempon väntar här tills användaren godkänt dem —
                # inget skrivs till planen automatiskt. Se pace_progression.py.
                cur.execute('''CREATE TABLE IF NOT EXISTS plan_proposals (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER DEFAULT 1,
                    session_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    kind TEXT,
                    old_detail TEXT,
                    new_detail TEXT,
                    old_pace_sec INTEGER,
                    new_pace_sec INTEGER,
                    validation TEXT,
                    reason TEXT,
                    rationale TEXT,
                    anchor JSONB,
                    created_at REAL,
                    decided_at REAL)''')
                cur.execute('''CREATE INDEX IF NOT EXISTS plan_proposals_pending_idx
                    ON plan_proposals (user_id, status)''')
            except Exception as e:
                print('migrate_db plan_proposals:', e)
            try:
                cur.execute('ALTER TABLE health_history ADD COLUMN IF NOT EXISTS stress_avg INTEGER')
            except Exception as e:
                print('migrate_db health_history stress_avg:', e)
            try:
                # Ett omdöme per genomfört pass, skrivet av synken när passet
                # först dyker upp — vad det kostade och när nästa kvalitetspass
                # tidigast bör ligga. Se strain_analysis.session_verdict.
                cur.execute('''CREATE TABLE IF NOT EXISTS session_verdicts (
                    activity_id BIGINT NOT NULL,
                    user_id INTEGER DEFAULT 1,
                    activity_date TEXT,
                    verdict JSONB,
                    created_at REAL,
                    PRIMARY KEY (activity_id, user_id))''')
                cur.execute('''CREATE INDEX IF NOT EXISTS session_verdicts_recent_idx
                    ON session_verdicts (user_id, activity_date DESC)''')
            except Exception as e:
                print('migrate_db session_verdicts:', e)
            try:
                # En rad per enhet. Endpoint är unik hos push-tjänsten, så den
                # duger som nyckel och gör om-prenumeration till en uppdatering
                # i stället för en dubblett.
                cur.execute('''CREATE TABLE IF NOT EXISTS push_subscriptions (
                    endpoint TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    created_at REAL,
                    last_ok REAL)''')
                cur.execute('''CREATE INDEX IF NOT EXISTS push_subscriptions_user_idx
                    ON push_subscriptions (user_id)''')
            except Exception as e:
                print('migrate_db push_subscriptions:', e)
            try:
                # När natten började och slutade, plus resten av stadiefördelningen.
                # Utan tiderna går läggdagsregelbundenhet inte att mäta alls.
                for column, kind in (('sleep_start', 'TEXT'), ('sleep_end', 'TEXT'),
                                     ('light_pct', 'INTEGER'), ('awake_pct', 'INTEGER')):
                    cur.execute(f'ALTER TABLE health_history ADD COLUMN IF NOT EXISTS {column} {kind}')
            except Exception as e:
                print('migrate_db health_history sleep timing:', e)
            for tbl in ('health_history', 'metric_history'):
                try:
                    cur.execute(f'ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {tbl}_pkey')
                    cur.execute(f'ALTER TABLE {tbl} ADD PRIMARY KEY (date, user_id)')
                except Exception as e:
                    print(f'migrate_db {tbl} pk:', e)
            try:
                cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS journal_entries_date_user_idx ON journal_entries (entry_date, user_id)')
            except Exception as e:
                print('migrate_db journal_entries unique:', e)
            # Engångsflytt av det tidigare hårdkodade målet till user_goals (ägaren).
            try:
                cur.execute('SELECT 1 FROM user_goals WHERE user_id=1')
                if not cur.fetchone():
                    cur.execute('''INSERT INTO user_goals
                        (user_id, goal_title, goal_deadline, current_best, secondary_goal, start_date, updated_at)
                        VALUES (1,%s,%s,%s,%s,%s,%s)''',
                        ('Halvmara under 1:20', '2026-10-10', '1:26:19 (Göteborgsvarvet)',
                         'Bygg en stark kropp — löpstyrka, överkropp, core, rörlighet',
                         '2026-05-27', time.time()))
            except Exception as e:
                print('migrate_db user_goals seed:', e)
        conn.commit()
    print('Databas: migrering klar')

if not APP_TESTING:
    try:
        setup_db()
        migrate_db()
    except Exception:
        logger.exception('Database initialization failed', extra={'event': 'database.initialize_failed'})

# --- Klimatsensorer: MQTT-prenumeration ---
# Rostern (vilka sensorer som *borde* finnas) kommer fran zigbee2mqtt sjalv via det
# retainade topicet bridge/devices. Utan den skulle en sensor som slutat skicka bara
# tystna ur listan i stallet for att flaggas som trasig - vilket ar precis vad som
# hande med Tempsensor_3, som lag nere i 44 dagar innan nagon markte det.
_sensor_roster = {}
_sensor_roster_lock = threading.Lock()
_mqtt_state = {'connected': False, 'last_error': None, 'last_message_at': None}


def _store_sensor_reading(sensor, payload):
    """Sparar en avlasning. Kastar aldrig - MQTT-traden far inte do av ett db-fel."""
    temperature = payload.get('temperature')
    humidity = payload.get('humidity')
    if temperature is None and humidity is None:
        return
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    '''INSERT INTO sensor_readings
                       (sensor, ts, temperature_c, humidity_pct, battery_pct, linkquality)
                       VALUES (%s, %s, %s, %s, %s, %s)''',
                    (sensor, datetime.now(timezone.utc), temperature, humidity,
                     payload.get('battery'), payload.get('linkquality')))
            conn.commit()
        _mqtt_state['last_message_at'] = time.time()
    except Exception:
        logger.exception('Could not store sensor reading',
                         extra={'event': 'climate.store_failed', 'sensor': sensor})


def _update_sensor_roster(payload):
    """bridge/devices -> {friendly_name: beskrivning} for alla end devices."""
    if not isinstance(payload, list):
        return
    roster = {}
    for device in payload:
        if not isinstance(device, dict) or device.get('type') == 'Coordinator':
            continue
        name = device.get('friendly_name')
        if not name:
            continue
        definition = device.get('definition') or {}
        roster[name] = {
            'model': definition.get('model'),
            'vendor': definition.get('vendor'),
            'description': definition.get('description'),
        }
    if roster:
        with _sensor_roster_lock:
            _sensor_roster.clear()
            _sensor_roster.update(roster)


def _on_mqtt_message(client, userdata, message):
    topic = message.topic
    try:
        payload = json.loads(message.payload.decode('utf-8'))
    except Exception:
        return
    prefix = f'{MQTT_BASE_TOPIC}/'
    if not topic.startswith(prefix):
        return
    name = topic[len(prefix):]
    if name == 'bridge/devices':
        _update_sensor_roster(payload)
        return
    if name.startswith('bridge/') or '/' in name:
        return
    if not isinstance(payload, dict):
        return
    # zigbee2mqtt aterpublicerar sitt cachade lage vid uppstart. De meddelandena
    # saknar linkquality (det satts forst av radiomottagningen), och att spara dem
    # skulle datera om gamla varden till nu och dolja att en sensor tystnat.
    if payload.get('linkquality') is None:
        return
    _store_sensor_reading(name, payload)


def _on_mqtt_connect(client, userdata, flags, reason_code, properties=None):
    ok = getattr(reason_code, 'is_failure', None)
    ok = (not ok) if ok is not None else (reason_code == 0)
    _mqtt_state['connected'] = bool(ok)
    if ok:
        client.subscribe([(f'{MQTT_BASE_TOPIC}/+', 0), (f'{MQTT_BASE_TOPIC}/bridge/devices', 0)])
        logger.info('MQTT connected', extra={'event': 'climate.mqtt_connected',
                                             'host': MQTT_HOST, 'port': MQTT_PORT})
    else:
        _mqtt_state['last_error'] = f'connect: {reason_code}'
        logger.warning('MQTT connect refused', extra={'event': 'climate.mqtt_refused',
                                                      'reason': str(reason_code)})


def _on_mqtt_disconnect(client, userdata, *args):
    _mqtt_state['connected'] = False
    logger.warning('MQTT disconnected', extra={'event': 'climate.mqtt_disconnected'})


def start_mqtt_listener():
    """Startar MQTT-lyssnaren i bakgrunden. Fel far aldrig stoppa dashboarden."""
    if not MQTT_ENABLED:
        return
    if mqtt_client is None:
        logger.warning('paho-mqtt not installed, climate readings disabled',
                       extra={'event': 'climate.mqtt_missing'})
        _mqtt_state['last_error'] = 'paho-mqtt saknas'
        return
    try:
        client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2,
                                    client_id=f'trainyze-{uuid.uuid4().hex[:8]}')
        if MQTT_USERNAME:
            client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        client.on_connect = _on_mqtt_connect
        client.on_disconnect = _on_mqtt_disconnect
        client.on_message = _on_mqtt_message
        # loop_start ager reconnect sjalv, sa en omstartad broker laker av sig sjalv.
        client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
        logger.info('MQTT listener started', extra={'event': 'climate.mqtt_starting',
                                                    'topic': f'{MQTT_BASE_TOPIC}/+'})
    except Exception as e:
        _mqtt_state['last_error'] = str(e)
        logger.exception('Could not start MQTT listener', extra={'event': 'climate.mqtt_failed'})


def _bedroom_temp_stats(hours):
    """(snitt, min, max) för sovrummet, eller None. Bara till AI-kontext."""
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT avg(temperature_c), min(temperature_c), max(temperature_c)
                    FROM sensor_readings
                    WHERE temperature_c IS NOT NULL
                      AND ts > now() - make_interval(hours => %s)''', (hours,))
                row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return tuple(round(float(v), 1) for v in row)
    except Exception:
        return None


def _bedroom_temp_daily(days):
    """[(datum, dygnssnitt)] för sovrummet. Bara till AI-kontext."""
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT (ts AT TIME ZONE 'Europe/Stockholm')::date AS day,
                                      avg(temperature_c)
                    FROM sensor_readings
                    WHERE temperature_c IS NOT NULL
                      AND ts > now() - make_interval(days => %s)
                    GROUP BY day ORDER BY day''', (days,))
                return [(str(day), round(float(avg), 1)) for day, avg in cur.fetchall()]
    except Exception:
        return []


def purge_old_sensor_readings():
    """Haller sensortabellen liten. Tre sensorer var femte minut blir ~250k rader/ar."""
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sensor_readings WHERE ts < now() - make_interval(days => %s)",
                            (CLIMATE_RETENTION_DAYS,))
            conn.commit()
    except Exception:
        logger.exception('Could not purge sensor readings', extra={'event': 'climate.purge_failed'})


if not APP_TESTING:
    start_mqtt_listener()

# --- Användarlager ---
# I drift bor användarna i databasen (seedas från .env första gången); .env USERS
# är därefter bara bootstrap-reserv. I tester (APP_TESTING) rörs aldrig databasen.
USER_STORE = None
if not APP_TESTING:
    try:
        USER_STORE = DbUserStore(db)
        USER_STORE.ensure_schema()
        if USER_STORE.seed_from_env(USERS):
            logger.info('Seeded users table from .env', extra={'event': 'users.seeded'})
    except Exception:
        USER_STORE = None
        logger.exception('User store unavailable, falling back to .env users',
                         extra={'event': 'users.store_failed'})
if USER_STORE is None:
    USER_STORE = MemoryUserStore(USERS)

def refresh_users():
    """Ladda om USERS-snapshotten från lagret (anropas efter varje ändring)."""
    global USERS
    USERS = USER_STORE.all()

refresh_users()

# AI-kontrollen har ett separat lager eftersom dess passkeys och jobb aldrig
# får blandas ihop med träningsassistentens vanliga chattdata.
AI_CONTROL_STORE = AiControlStore(None if APP_TESTING else db)
if not APP_TESTING:
    try:
        AI_CONTROL_STORE.ensure_schema()
    except Exception:
        logger.exception('AI control store unavailable', extra={'event': 'ai.store_failed'})

# Den adaptiva motorn sparar beslutsunderlag separat från själva planen. Under
# skuggläget kan vi därför utvärdera råden utan att något pass skrivs om.
ADAPTIVE_PLAN_STORE = AdaptivePlanStore(None if APP_TESTING else db)
if not APP_TESTING:
    try:
        ADAPTIVE_PLAN_STORE.ensure_schema()
    except Exception:
        logger.exception('Adaptive plan store unavailable', extra={'event': 'adaptive.store_failed'})

LIFESTYLE_STORE = LifestyleStore(None if APP_TESTING else db)
if not APP_TESTING:
    try:
        LIFESTYLE_STORE.ensure_schema()
    except Exception:
        logger.exception('Lifestyle store unavailable', extra={'event': 'lifestyle.store_failed'})

ACTIVITY_FEEDBACK_STORE = ActivityFeedbackStore(None if APP_TESTING else db)
if not APP_TESTING:
    try:
        ACTIVITY_FEEDBACK_STORE.ensure_schema()
    except Exception:
        logger.exception('Activity feedback store unavailable', extra={'event': 'activity_feedback.store_failed'})

# --- Garmin ---
# Token migration note for Pi: if Hugo's existing tokens are at ~/.garminconnect/,
# run: mv ~/.garminconnect ~/.garminconnect_bak && mkdir ~/.garminconnect && mv ~/.garminconnect_bak ~/.garminconnect/hugo
_garmin_clients = {}

def get_garmin(username=None):
    global _garmin_clients
    if username is None:
        username = uname()
    if username in _garmin_clients:
        return _garmin_clients[username]
    token_dir = str(Path.home() / '.garminconnect' / username)
    Path(token_dir).mkdir(parents=True, exist_ok=True)
    g = Garmin()
    try:
        g.login(tokenstore=token_dir)
    except Exception:
        # Fallback to legacy path for the first user (backward compat)
        first_user = list(USERS.keys())[0] if USERS else 'hugo'
        if username == first_user:
            g = Garmin()
            g.login(tokenstore=TOKEN_DIR)
        else:
            raise
    _garmin_clients[username] = g
    return g

def save_activities(activities, user_id=1):
    with db() as conn:
        with conn.cursor() as cur:
            for a in activities:
                try:
                    cur.execute('''INSERT INTO activities (id,name,date,type,distance,duration,avg_hr,raw,created_at,user_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (id) DO UPDATE SET raw=EXCLUDED.raw, name=EXCLUDED.name''',
                        (a.get('activityId'), a.get('activityName'), a.get('startTimeLocal'),
                         a.get('activityType', {}).get('typeKey'),
                         a.get('distance'), a.get('duration'), a.get('averageHR'),
                         json.dumps(a), time.time(), user_id))
                except Exception as e:
                    print('Spara aktivitet fel:', e)
        conn.commit()

def get_cache(key, user_id=1):
    prefixed = f'{user_id}:{key}'
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value, updated_at FROM cache WHERE key=%s", (prefixed,))
            return cur.fetchone()

def set_cache(key, value, user_id=1):
    prefixed = f'{user_id}:{key}'
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO cache (key, value, updated_at) VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at''',
                (prefixed, json.dumps(value), time.time()))
        conn.commit()

def clear_cache(*keys, user_id=1):
    prefixed = [f'{user_id}:{k}' for k in keys]
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cache WHERE key = ANY(%s)", (prefixed,))
        conn.commit()

# --- Auth ---
@app.before_request
def begin_request():
    flask_g.request_id = uuid.uuid4().hex
    flask_g.request_started = time.perf_counter()


def _request_id():
    return getattr(flask_g, 'request_id', '')


def _api_error(code, message, status, extra=None):
    payload = {'error': message, 'code': code, 'requestId': _request_id()}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def _server_error(error, event, status=500, code='internal_error',
                  message='Ett oväntat serverfel inträffade.', extra=None):
    logger.exception('Request failed', extra={
        'event': event,
        'request_id': _request_id(),
        'path': request.path,
        'method': request.method,
        'user_id': getattr(flask_g, 'uid', None),
    })
    return _api_error(code, message, status, extra=extra)


def _configured_session_user():
    username = session.get('username')
    user = USERS.get(username)
    if not user or session.get('user_id') != user['id']:
        return None, None
    return username, user


def _ensure_csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token


def _widget_token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _widget_token_from_request():
    authorization = request.headers.get('Authorization', '')
    if authorization.lower().startswith('bearer '):
        return authorization[7:].strip()
    return request.headers.get('X-Widget-Token', '').strip()


def _widget_token_user():
    token = _widget_token_from_request()
    if not token:
        return None, False
    if len(token) > 256 or not token.startswith('tdw_'):
        return None, True
    return USER_STORE.user_for_widget_token_hash(_widget_token_hash(token)), True


@app.before_request
def check_auth():
    if not request.path.startswith('/api/'):
        return
    if request.method == 'OPTIONS':
        return
    if request.path in (
        '/api/login', '/api/session', '/api/healthz', '/api/register',
        '/api/forgot-password', '/api/reset-password',
    ):
        return
    if request.method == 'GET' and request.path == '/api/verify-email':
        return
    if request.method == 'POST' and request.path in (
        '/api/water', '/api/ac/button/off', '/api/ac/button/auto-on'
    ):
        return  # Hardware endpoints authenticate with separate, scoped tokens.
    if request.path.startswith('/api/ai/agent/'):
        return  # G3-agenten verifieras med en separat, lång bearer-token i varje endpoint.

    if request.method == 'GET' and request.path in (
        '/api/ac/bedtime', '/api/weather/current', '/api/sleep-coach'
    ) and request.remote_addr in ('127.0.0.1', '::1'):
        return  # ac-keeper (same host) polls these for pre-cool scheduling.

    if request.method == 'GET' and request.path == '/api/widget/mobile':
        widget_user, token_supplied = _widget_token_user()
        if token_supplied:
            if not widget_user:
                return _api_error('invalid_widget_token', 'Widgettoken är ogiltig eller återkallad.', 401)
            flask_g.uid = widget_user['id']
            flask_g.uname = widget_user['username']
            flask_g.widget_auth = True
            return

    username, user = _configured_session_user()
    if not user:
        session.clear()
        return _api_error('authentication_required', 'Du behöver logga in igen.', 401)

    flask_g.uid = user['id']
    flask_g.uname = username
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        expected = session.get('csrf_token') or ''
        supplied = request.headers.get('X-CSRF-Token', '')
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return _api_error('invalid_csrf_token', 'Säkerhetstoken saknas eller är ogiltig.', 403)


@app.after_request
def secure_response(response):
    request_id = _request_id()
    response.headers['X-Request-ID'] = request_id
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(), payment=()'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
        "script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://tile.openstreetmap.org; "
        "connect-src 'self'; "
        "form-action 'self'"
    )
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Vary'] = 'Cookie'
    elif request.path in (
        '/', '/index.html', '/landing.html', '/app.js', '/styles.css',
        '/landing.css', '/landing.js',
    ):
        response.headers['Cache-Control'] = 'no-cache'
    if ENABLE_HSTS:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    started = getattr(flask_g, 'request_started', None)
    duration_ms = round((time.perf_counter() - started) * 1000, 1) if started else None
    if request.path != '/api/healthz':
        logger.info('request', extra={
            'event': 'http.request',
            'request_id': request_id,
            'method': request.method,
            'path': request.path,
            'status': response.status_code,
            'duration_ms': duration_ms,
            'user_id': getattr(flask_g, 'uid', None),
        })
    return response


@app.errorhandler(Exception)
def unhandled_error(error):
    if isinstance(error, HTTPException):
        return error
    logger.exception('Unhandled request error', extra={
        'event': 'http.unhandled_error',
        'request_id': _request_id(),
        'method': request.method,
        'path': request.path,
        'user_id': getattr(flask_g, 'uid', None),
    })
    if request.path.startswith('/api/'):
        return _api_error('internal_error', 'Ett oväntat serverfel inträffade.', 500)
    return 'Internal Server Error', 500


# --- Endpoints ---
@app.get('/api/healthz')
def healthz():
    return jsonify({'status': 'ok'})


@app.get('/api/session')
def auth_session():
    username, user = _configured_session_user()
    if not user:
        session.clear()
        return jsonify({'authenticated': False})
    return jsonify({
        'authenticated': True,
        'username': username,
        'userId': user['id'],
        'isAdmin': bool(user.get('is_admin')),
        'garminConnected': _garmin_connected(username),
        'stravaConfigured': _strava_configured(),
        'stravaConnected': _strava_connected(username),
        'stravaAthlete': _strava_profile(username).get('athleteName'),
        'csrfToken': _ensure_csrf_token(),
    })


@app.post('/api/login')
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    password = data.get('password')
    if not username:
        username = next(iter(USERS))
    if not isinstance(password, str) or not password or len(username) > 64 or len(password) > 1024:
        return _api_error('invalid_credentials', 'Fel användarnamn eller lösenord.', 401)

    ip_key = request.remote_addr or 'unknown'
    ip_allowed, ip_retry_after = LOGIN_IP_LIMITER.check(ip_key)
    if not ip_allowed:
        response, status = _api_error(
            'too_many_login_attempts',
            'För många inloggningsförsök från din adress. Vänta en stund och försök igen.',
            429,
        )
        response.headers['Retry-After'] = str(ip_retry_after)
        logger.warning('Login rate limited (IP-wide)', extra={
            'event': 'auth.rate_limited_ip',
            'request_id': _request_id(),
        })
        return response, status

    limiter_key = f'{request.remote_addr or "unknown"}:{username.lower()}'
    allowed, retry_after = LOGIN_LIMITER.check(limiter_key)
    if not allowed:
        response, status = _api_error(
            'too_many_login_attempts',
            'För många inloggningsförsök. Vänta en stund och försök igen.',
            429,
        )
        response.headers['Retry-After'] = str(retry_after)
        logger.warning('Login rate limited', extra={
            'event': 'auth.rate_limited',
            'request_id': _request_id(),
        })
        return response, status

    user = verify_user(USERS, username, password)
    if not user:
        LOGIN_IP_LIMITER.record_failure(ip_key)
        LOGIN_LIMITER.record_failure(limiter_key)
        logger.warning('Invalid login attempt', extra={
            'event': 'auth.login_failed',
            'request_id': _request_id(),
        })
        return _api_error('invalid_credentials', 'Fel användarnamn eller lösenord.', 401)

    if user.get('email') and not user.get('email_verified'):
        LOGIN_IP_LIMITER.record_failure(ip_key)
        LOGIN_LIMITER.record_failure(limiter_key)
        return _api_error(
            'email_not_verified',
            'Du behöver verifiera din e-postadress innan du kan logga in. Kolla din inkorg.',
            403,
        )

    LOGIN_IP_LIMITER.reset(ip_key)
    LOGIN_LIMITER.reset(limiter_key)
    session.clear()
    session.permanent = True
    session['username'] = username
    session['user_id'] = user['id']
    csrf_token = _ensure_csrf_token()
    logger.info('Login succeeded', extra={
        'event': 'auth.login_succeeded',
        'request_id': _request_id(),
        'user_id': user['id'],
    })
    return jsonify({
        'ok': True,
        'username': username,
        'userId': user['id'],
        'isAdmin': bool(user.get('is_admin')),
        'garminConnected': _garmin_connected(username),
        'stravaConfigured': _strava_configured(),
        'stravaConnected': _strava_connected(username),
        'stravaAthlete': _strava_profile(username).get('athleteName'),
        'csrfToken': csrf_token,
    })


@app.post('/api/register')
def register():
    if USER_STORE is None:
        return _api_error('registration_unavailable', 'Registrering är inte tillgänglig just nu.', 503)

    ip_key = f'register:{request.remote_addr or "unknown"}'
    allowed, retry_after = REGISTER_LIMITER.check(ip_key)
    if not allowed:
        response, status = _api_error(
            'too_many_registrations',
            'För många registreringsförsök från din adress. Vänta en stund och försök igen.',
            429,
        )
        response.headers['Retry-After'] = str(retry_after)
        return response, status

    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    email = str(data.get('email') or '').strip()
    password = data.get('password')
    if not isinstance(password, str):
        password = ''

    try:
        new_id, token = USER_STORE.create_pending(username, email, password)
    except DuplicateUserError as e:
        REGISTER_LIMITER.record_failure(ip_key)
        return _api_error('duplicate_user', str(e), 409)
    except UserStoreError as e:
        REGISTER_LIMITER.record_failure(ip_key)
        return _api_error('invalid_registration', str(e), 400)

    refresh_users()
    sent = _send_verification_email(email, username, token)
    logger.info('User registered', extra={
        'event': 'auth.registered', 'request_id': _request_id(),
        'user_id': new_id, 'mail_sent': sent,
    })
    if not sent:
        return _api_error(
            'mail_send_failed',
            'Kontot skapades men verifieringsmejlet kunde inte skickas. Kontakta ägaren.',
            502,
        )
    return jsonify({'ok': True, 'message': 'Kolla din inkorg för en verifieringslänk.'})


@app.get('/api/verify-email')
def verify_email():
    token = request.args.get('token', '')
    username = USER_STORE.verify_email_token(token) if USER_STORE else None
    if username:
        refresh_users()
    ok = bool(username)
    title = 'E-post verifierad' if ok else 'Länken är ogiltig eller har gått ut'
    body = (
        f'Ditt konto <strong>{username}</strong> är nu aktiverat. Du kan logga in.'
        if ok else
        'Länken har redan använts, gått ut, eller är felaktig. Registrera dig igen om det behövs.'
    )
    html = f'''<!doctype html><html lang="sv"><head><meta charset="utf-8">
      <title>{title}</title>
      <style>body{{font-family:sans-serif;background:#0D0F14;color:#E5E7EB;
        display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}}
        .card{{background:#161A22;border:1px solid rgba(255,255,255,0.08);border-radius:16px;
        padding:32px;max-width:420px;text-align:center;}}
        a{{color:#C8F135;}}</style></head>
      <body><div class="card"><h2>{title}</h2><p>{body}</p>
      <p><a href="{PUBLIC_BASE_URL}">Gå till Trainyze</a></p></div></body></html>'''
    return html, 200 if ok else 400


@app.post('/api/forgot-password')
def forgot_password():
    generic_response = jsonify({
        'ok': True,
        'message': 'Om kontot finns har vi skickat en länk för att återställa lösenordet.',
    })
    if USER_STORE is None:
        return generic_response

    ip_key = f'forgot-password:{request.remote_addr or "unknown"}'
    allowed, retry_after = FORGOT_PASSWORD_LIMITER.check(ip_key)
    if not allowed:
        response, status = _api_error(
            'too_many_requests',
            'För många förfrågningar från din adress. Vänta en stund och försök igen.',
            429,
        )
        response.headers['Retry-After'] = str(retry_after)
        return response, status
    FORGOT_PASSWORD_LIMITER.record_failure(ip_key)

    data = request.get_json(silent=True) or {}
    email = str(data.get('email') or '').strip()
    result = USER_STORE.create_password_reset_token(email)
    if result:
        username, token = result
        sent = _send_password_reset_email(email, username, token)
        logger.info('Password reset requested', extra={
            'event': 'auth.password_reset_requested', 'request_id': _request_id(), 'mail_sent': sent,
        })
    return generic_response


@app.post('/api/reset-password')
def reset_password():
    if USER_STORE is None:
        return _api_error('registration_unavailable', 'Kontohantering är inte tillgänglig just nu.', 503)

    data = request.get_json(silent=True) or {}
    token = str(data.get('token') or '')
    password = data.get('password')
    if not isinstance(password, str):
        password = ''
    if not token:
        return _api_error('invalid_reset_token', 'Länken är ogiltig eller har gått ut.', 400)

    try:
        username = USER_STORE.reset_password_with_token(token, password)
    except UserStoreError as e:
        return _api_error('invalid_registration', str(e), 400)

    if not username:
        return _api_error('invalid_reset_token', 'Länken är ogiltig eller har gått ut.', 400)

    refresh_users()
    logger.info('Password reset completed', extra={
        'event': 'auth.password_reset_completed', 'request_id': _request_id(),
    })
    return jsonify({'ok': True, 'message': 'Lösenordet har återställts. Du kan nu logga in.'})


@app.post('/api/logout')
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.post('/api/widget/token')
def create_widget_token():
    token = 'tdw_' + secrets.token_urlsafe(32)
    if not USER_STORE.set_widget_token_hash(uid(), _widget_token_hash(token)):
        return _api_error('user_not_found', 'Användaren kunde inte hittas.', 404)
    logger.info('Widget token rotated', extra={
        'event': 'widget.token_rotated',
        'request_id': _request_id(),
        'user_id': uid(),
    })
    return jsonify({'ok': True, 'token': token})


@app.delete('/api/widget/token')
def revoke_widget_token():
    if not USER_STORE.set_widget_token_hash(uid(), None):
        return _api_error('user_not_found', 'Användaren kunde inte hittas.', 404)
    logger.info('Widget token revoked', extra={
        'event': 'widget.token_revoked',
        'request_id': _request_id(),
        'user_id': uid(),
    })
    return jsonify({'ok': True})


# --- Användarhantering (admin) ---
def _current_is_admin():
    user = USERS.get(uname())
    return bool(user and user.get('is_admin'))


# --- Ägarens fjärrstyrda utvecklingsagent ----------------------------------
def _ai_owner_error(require_enabled=True):
    if uid() != 1 or not _current_is_admin():
        return _api_error('forbidden', 'Endast Trainyzes ägare har åtkomst.', 403)
    if require_enabled and not AI_CONTROL_ENABLED:
        return _api_error('ai_control_disabled', 'AI-kontrollen är inte aktiverad på servern.', 503)
    if not WEBAUTHN_AVAILABLE:
        return _api_error('webauthn_unavailable', 'Passkey-stödet saknas på servern.', 503)
    return None


def _ai_is_unlocked(now=None):
    now = time.time() if now is None else now
    verified_at = float(session.get('ai_verified_at') or 0)
    return verified_at > 0 and now - verified_at <= AI_STEP_UP_TTL_SECONDS


def _ai_secret_configured(value):
    value = str(value or '')
    return len(value) >= 32 and not value.lower().startswith(('replace-', 'change-', 'example-'))


def _ai_unlock_error():
    owner_error = _ai_owner_error()
    if owner_error:
        return owner_error
    if not _ai_is_unlocked():
        return _api_error('passkey_required', 'Verifiera dig med Face ID för att fortsätta.', 403)
    return None


def _b64url(value):
    return base64.urlsafe_b64encode(bytes(value)).rstrip(b'=').decode('ascii')


def _pop_passkey_challenge(name):
    encoded = session.pop(name, None)
    expires = float(session.pop(f'{name}_expires', 0) or 0)
    if not encoded or expires < time.time():
        return None
    try:
        return base64url_to_bytes(encoded)
    except Exception:
        return None


def _store_passkey_challenge(name, challenge):
    session[name] = _b64url(challenge)
    session[f'{name}_expires'] = time.time() + 300


def _agent_auth_error():
    if not AI_CONTROL_ENABLED:
        return _api_error('ai_control_disabled', 'AI-kontrollen är inte aktiverad.', 503)
    authorization = request.headers.get('Authorization', '')
    supplied = authorization[7:].strip() if authorization.lower().startswith('bearer ') else ''
    if not _ai_secret_configured(AI_AGENT_TOKEN):
        return _api_error('agent_unavailable', 'G3-agenten är inte konfigurerad.', 503)
    if not supplied or not hmac.compare_digest(AI_AGENT_TOKEN, supplied):
        return _api_error('invalid_agent_token', 'Ogiltig agenttoken.', 401)
    return None


@app.get('/api/ai/status')
def ai_control_status():
    owner_error = _ai_owner_error(require_enabled=False)
    if owner_error and owner_error[1] == 403:
        return owner_error
    credentials = AI_CONTROL_STORE.credentials_for_user(uid()) if WEBAUTHN_AVAILABLE else []
    unlocked = AI_CONTROL_ENABLED and WEBAUTHN_AVAILABLE and _ai_is_unlocked()
    verified_at = float(session.get('ai_verified_at') or 0)
    return jsonify({
        'enabled': AI_CONTROL_ENABLED,
        'webauthnAvailable': WEBAUTHN_AVAILABLE,
        'configured': bool(credentials),
        'credentialCount': len(credentials),
        'registrationAllowed': bool(
            AI_CONTROL_ENABLED and WEBAUTHN_AVAILABLE and
            ((_ai_is_unlocked() and credentials) or
             (not credentials and _ai_secret_configured(AI_PASSKEY_BOOTSTRAP_TOKEN)))
        ),
        'unlocked': unlocked,
        'unlockedUntil': verified_at + AI_STEP_UP_TTL_SECONDS if unlocked else None,
        'agentConfigured': _ai_secret_configured(AI_AGENT_TOKEN),
        'rpId': AI_RP_ID,
    })


@app.post('/api/ai/passkeys/register/options')
def ai_passkey_registration_options():
    owner_error = _ai_owner_error()
    if owner_error:
        return owner_error
    credentials = AI_CONTROL_STORE.credentials_for_user(uid())
    if credentials:
        if not _ai_is_unlocked():
            return _api_error('passkey_required', 'Verifiera befintlig passkey först.', 403)
    else:
        supplied = str((request.get_json(silent=True) or {}).get('bootstrapToken') or '')
        if not _ai_secret_configured(AI_PASSKEY_BOOTSTRAP_TOKEN):
            return _api_error('bootstrap_unavailable', 'Bootstrap-token saknas på servern.', 503)
        if not supplied or not hmac.compare_digest(AI_PASSKEY_BOOTSTRAP_TOKEN, supplied):
            return _api_error('invalid_bootstrap_token', 'Bootstrap-koden är ogiltig.', 403)

    options = generate_registration_options(
        rp_id=AI_RP_ID,
        rp_name='Trainyze AI Control',
        user_id=f'trainyze-owner:{uid()}'.encode('utf-8'),
        user_name=uname(),
        user_display_name='Trainyze ägare',
        timeout=60000,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=value['credential_id'])
                             for value in credentials],
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    _store_passkey_challenge('ai_registration_challenge', options.challenge)
    session['ai_registration_authorized'] = True
    return app.response_class(options_to_json(options), mimetype='application/json')


@app.post('/api/ai/passkeys/register/verify')
def ai_passkey_registration_verify():
    owner_error = _ai_owner_error()
    if owner_error:
        return owner_error
    challenge = _pop_passkey_challenge('ai_registration_challenge')
    authorized = bool(session.pop('ai_registration_authorized', False))
    if not challenge or not authorized:
        return _api_error('passkey_challenge_expired', 'Registreringen har gått ut. Försök igen.', 400)
    data = request.get_json(silent=True) or {}
    credential = data.get('credential')
    if not isinstance(credential, dict):
        return _api_error('invalid_passkey_response', 'Passkey-svaret saknas.', 400)
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=AI_RP_ID,
            expected_origin=AI_ORIGIN,
            require_user_verification=True,
        )
        transports = ((credential.get('response') or {}).get('transports') or [])
        AI_CONTROL_STORE.add_credential(
            uid(), verification.credential_id, verification.credential_public_key,
            verification.sign_count, transports=transports,
            label=str(data.get('label') or 'Face ID')[:80],
        )
    except Exception:
        logger.warning('Passkey registration failed', extra={
            'event': 'ai.passkey_registration_failed', 'request_id': _request_id(),
            'user_id': uid(),
        })
        return _api_error('invalid_passkey_response', 'Passkeyn kunde inte verifieras.', 400)
    session['ai_verified_at'] = time.time()
    logger.info('AI passkey registered', extra={
        'event': 'ai.passkey_registered', 'request_id': _request_id(), 'user_id': uid(),
    })
    return jsonify({'ok': True, 'unlockedForSeconds': AI_STEP_UP_TTL_SECONDS})


@app.post('/api/ai/passkeys/auth/options')
def ai_passkey_authentication_options():
    owner_error = _ai_owner_error()
    if owner_error:
        return owner_error
    credentials = AI_CONTROL_STORE.credentials_for_user(uid())
    if not credentials:
        return _api_error('passkey_not_configured', 'Ingen passkey är registrerad.', 409)
    options = generate_authentication_options(
        rp_id=AI_RP_ID,
        timeout=60000,
        allow_credentials=[PublicKeyCredentialDescriptor(id=value['credential_id'])
                           for value in credentials],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _store_passkey_challenge('ai_authentication_challenge', options.challenge)
    return app.response_class(options_to_json(options), mimetype='application/json')


@app.post('/api/ai/passkeys/auth/verify')
def ai_passkey_authentication_verify():
    owner_error = _ai_owner_error()
    if owner_error:
        return owner_error
    challenge = _pop_passkey_challenge('ai_authentication_challenge')
    if not challenge:
        return _api_error('passkey_challenge_expired', 'Verifieringen har gått ut. Försök igen.', 400)
    credential = (request.get_json(silent=True) or {}).get('credential')
    if not isinstance(credential, dict):
        return _api_error('invalid_passkey_response', 'Passkey-svaret saknas.', 400)
    try:
        credential_id = base64url_to_bytes(str(credential.get('rawId') or credential.get('id') or ''))
        stored = AI_CONTROL_STORE.credential(credential_id)
        if not stored or stored['user_id'] != uid():
            raise ValueError('Unknown credential')
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=AI_RP_ID,
            expected_origin=AI_ORIGIN,
            credential_public_key=stored['public_key'],
            credential_current_sign_count=stored['sign_count'],
            require_user_verification=True,
        )
        AI_CONTROL_STORE.update_credential_counter(credential_id, verification.new_sign_count)
    except Exception:
        logger.warning('Passkey authentication failed', extra={
            'event': 'ai.passkey_authentication_failed', 'request_id': _request_id(),
            'user_id': uid(),
        })
        return _api_error('invalid_passkey_response', 'Face ID-verifieringen misslyckades.', 403)
    session['ai_verified_at'] = time.time()
    logger.info('AI control unlocked', extra={
        'event': 'ai.unlocked', 'request_id': _request_id(), 'user_id': uid(),
    })
    return jsonify({'ok': True, 'unlockedForSeconds': AI_STEP_UP_TTL_SECONDS})


@app.post('/api/ai/lock')
def ai_control_lock():
    owner_error = _ai_owner_error()
    if owner_error:
        return owner_error
    session.pop('ai_verified_at', None)
    return jsonify({'ok': True})


@app.get('/api/ai/jobs')
def ai_jobs_list():
    unlock_error = _ai_unlock_error()
    if unlock_error:
        return unlock_error
    return jsonify({'jobs': AI_CONTROL_STORE.list_jobs(uid())})


@app.post('/api/ai/jobs')
def ai_jobs_create():
    unlock_error = _ai_unlock_error()
    if unlock_error:
        return unlock_error
    prompt = str((request.get_json(silent=True) or {}).get('prompt') or '').strip()
    if not prompt or len(prompt) > 4000:
        return _api_error('invalid_prompt', 'Instruktionen måste vara 1–4000 tecken.', 400)
    provider = 'codex'
    if prompt.startswith('/'):
        command, _, instruction = prompt.partition(' ')
        provider = command[1:].lower()
        if provider not in ('codex', 'claude'):
            return _api_error(
                'invalid_ai_provider', 'Börja med /codex eller /claude.', 400,
            )
        prompt = instruction.strip()
        if not prompt:
            return _api_error('invalid_prompt', 'Skriv en instruktion efter AI-valet.', 400)
    job = AI_CONTROL_STORE.create_job(uid(), prompt, provider=provider)
    logger.info('AI job queued', extra={
        'event': 'ai.job_queued', 'request_id': _request_id(), 'user_id': uid(),
        'job_id': job['id'], 'provider': provider,
    })
    return jsonify({'job': job}), 201


@app.get('/api/ai/jobs/<uuid:job_id>')
def ai_job_detail(job_id):
    unlock_error = _ai_unlock_error()
    if unlock_error:
        return unlock_error
    job = AI_CONTROL_STORE.get_job(str(job_id), uid())
    if not job:
        return _api_error('job_not_found', 'Uppdraget finns inte.', 404)
    return jsonify({'job': job})


@app.post('/api/ai/jobs/<uuid:job_id>/cancel')
def ai_job_cancel(job_id):
    unlock_error = _ai_unlock_error()
    if unlock_error:
        return unlock_error
    job = AI_CONTROL_STORE.get_job(str(job_id), uid())
    if not job:
        return _api_error('job_not_found', 'Uppdraget finns inte.', 404)
    if job['status'] != 'pending':
        return _api_error('job_already_started', 'Uppdraget har redan startat.', 409)
    AI_CONTROL_STORE.finish_job(str(job_id), 'cancelled')
    return jsonify({'ok': True})


@app.post('/api/ai/agent/jobs/next')
def ai_agent_next_job():
    auth_error = _agent_auth_error()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    job = AI_CONTROL_STORE.claim_next(str(data.get('agentId') or 'g3'))
    return jsonify({'job': job})


@app.post('/api/ai/agent/jobs/<uuid:job_id>/events')
def ai_agent_job_event(job_id):
    auth_error = _agent_auth_error()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    if not AI_CONTROL_STORE.append_event(str(job_id), data.get('kind'), data.get('message')):
        return _api_error('job_not_found', 'Uppdraget finns inte.', 404)
    return jsonify({'ok': True})


@app.post('/api/ai/agent/jobs/<uuid:job_id>/finish')
def ai_agent_finish_job(job_id):
    auth_error = _agent_auth_error()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    status = str(data.get('status') or '')
    if status not in ('completed', 'failed'):
        return _api_error('invalid_job_status', 'Ogiltig jobbstatus.', 400)
    if not AI_CONTROL_STORE.finish_job(
            str(job_id), status, result=data.get('result'), error=data.get('error')):
        return _api_error('job_not_found', 'Uppdraget finns inte eller är redan klart.', 404)
    logger.info('AI job finished', extra={
        'event': 'ai.job_finished', 'request_id': _request_id(),
        'job_id': str(job_id), 'status': status,
    })
    return jsonify({'ok': True})


def _garmin_token_dir(username):
    return Path.home() / '.garminconnect' / username


def _garmin_connected(username):
    token_dir = _garmin_token_dir(username)
    try:
        if token_dir.is_dir() and any(token_dir.iterdir()):
            return True
    except OSError:
        pass
    # Första användarens tokens kan ligga kvar på legacy-platsen (rotkatalogen),
    # samma fallback som get_garmin använder.
    if username == next(iter(USERS), None):
        try:
            return (Path(TOKEN_DIR) / 'garmin_tokens.json').is_file()
        except OSError:
            return False
    return False


# --- Strava OAuth (per användare) ---
STRAVA_STATE_TTL_SECONDS = 10 * 60
_TESTING_STRAVA_STATES = {}
_strava_token_lock = threading.Lock()


def _strava_configured():
    return bool(STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET and STRAVA_REDIRECT_URI)


def _strava_token_path(username):
    return STRAVA_TOKEN_ROOT / f'{username}.json'


def _read_strava_tokens(username):
    try:
        payload = json.loads(_strava_token_path(username).read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def _save_strava_tokens(username, payload):
    STRAVA_TOKEN_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = _strava_token_path(username)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload), encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)


def _strava_connected(username):
    payload = _read_strava_tokens(username)
    return bool(payload and payload.get('refresh_token'))


def _strava_profile(username):
    payload = _read_strava_tokens(username) or {}
    athlete = payload.get('athlete') or {}
    name = ' '.join(filter(None, (athlete.get('firstname'), athlete.get('lastname')))).strip()
    return {
        'connected': bool(payload.get('refresh_token')),
        'athleteId': athlete.get('id'),
        'athleteName': name or None,
        'scope': payload.get('scope') or '',
    }


def _strava_access_token(username):
    with _strava_token_lock:
        payload = _read_strava_tokens(username)
        if not payload or not payload.get('refresh_token'):
            raise strava_integration.StravaError('Strava är inte anslutet.')
        if float(payload.get('expires_at') or 0) > time.time() + 3600 \
                and payload.get('access_token'):
            return payload['access_token']
        refreshed = strava_integration.refresh_access_token(
            STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, payload['refresh_token'])
        refreshed['athlete'] = payload.get('athlete') or {}
        refreshed['scope'] = refreshed.get('scope') or payload.get('scope') or ''
        _save_strava_tokens(username, refreshed)
        return refreshed['access_token']


def _create_strava_state(user_id):
    state = secrets.token_urlsafe(32)
    digest = hashlib.sha256(state.encode('utf-8')).hexdigest()
    now = time.time()
    if APP_TESTING:
        _TESTING_STRAVA_STATES[digest] = (user_id, now)
        return state
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM strava_oauth_states WHERE created_at < %s',
                        (now - STRAVA_STATE_TTL_SECONDS,))
            cur.execute('INSERT INTO strava_oauth_states (state_hash,user_id,created_at) '
                        'VALUES (%s,%s,%s)', (digest, user_id, now))
        conn.commit()
    return state


def _consume_strava_state(state):
    if not state or len(state) > 256:
        return None
    digest = hashlib.sha256(state.encode('utf-8')).hexdigest()
    if APP_TESTING:
        entry = _TESTING_STRAVA_STATES.pop(digest, None)
        return entry[0] if entry and time.time() - entry[1] <= STRAVA_STATE_TTL_SECONDS else None
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM strava_oauth_states WHERE state_hash=%s '
                        'RETURNING user_id,created_at', (digest,))
            row = cur.fetchone()
        conn.commit()
    return row[0] if row and time.time() - row[1] <= STRAVA_STATE_TTL_SECONDS else None


def _strava_username_for_user_id(user_id):
    return next((name for name, user in USERS.items() if user.get('id') == user_id), None)


def _strava_activities(username, days=120, force=False):
    cache_key = 'strava:activities'
    if not force:
        cached = get_cache(cache_key, USERS[username]['id'])
        if cached and time.time() - cached[1] < 15 * 60:
            return cached[0]
    token = _strava_access_token(username)
    activities = [strava_integration.normalize_summary(activity) for activity in
                  strava_integration.athlete_activities(token, days=days)]
    set_cache(cache_key, activities, USERS[username]['id'])
    return activities


@app.get('/api/users')
def list_users():
    if not _current_is_admin():
        return _api_error('forbidden', 'Endast administratören kan hantera användare.', 403)
    return jsonify({'users': [
        {
            'id': rec['id'],
            'username': username,
            'isAdmin': bool(rec.get('is_admin')),
            'garminConnected': _garmin_connected(username),
            'stravaConnected': _strava_connected(username),
        }
        for username, rec in sorted(USERS.items(), key=lambda item: item[1]['id'])
    ]})


@app.post('/api/users')
def create_user():
    if not _current_is_admin():
        return _api_error('forbidden', 'Endast administratören kan hantera användare.', 403)
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    password = data.get('password')
    try:
        new_id = USER_STORE.create(username, password)
    except DuplicateUserError as exc:
        return _api_error('duplicate_username', str(exc), 409)
    except UserStoreError as exc:
        return _api_error('invalid_user', str(exc), 400)
    refresh_users()
    logger.info('User created', extra={
        'event': 'users.created',
        'request_id': _request_id(),
        'user_id': uid(),
        'created_user_id': new_id,
    })
    return jsonify({'ok': True, 'id': new_id, 'username': username}), 201


@app.delete('/api/users/<int:user_id>')
def delete_user(user_id):
    if not _current_is_admin():
        return _api_error('forbidden', 'Endast administratören kan hantera användare.', 403)
    if user_id == uid():
        return _api_error('cannot_delete_self', 'Du kan inte ta bort ditt eget konto.', 400)
    target = next((rec for rec in USERS.values() if rec['id'] == user_id), None)
    if not target:
        return _api_error('user_not_found', 'Användaren finns inte.', 404)
    if target.get('is_admin'):
        return _api_error('cannot_delete_admin', 'Administratörskontot kan inte tas bort.', 400)
    USER_STORE.delete(user_id)
    refresh_users()
    logger.info('User deleted', extra={
        'event': 'users.deleted',
        'request_id': _request_id(),
        'user_id': uid(),
        'deleted_user_id': user_id,
    })
    return jsonify({'ok': True})


# --- Garmin-koppling (per användare) ---
# Inloggningen sker med return_on_mfa=True: kräver Garmin en engångskod ligger
# MFA-tillståndet kvar på klientobjektet, som parkeras här tills koden kommer in.
GARMIN_CONNECT_LIMITER = LoginRateLimiter(max_attempts=5, window_seconds=900)
GARMIN_MFA_TTL_SECONDS = 300
_pending_garmin_mfa = {}
_pending_garmin_lock = threading.Lock()


def _prune_pending_garmin(now=None):
    now = time.time() if now is None else now
    for state_id in list(_pending_garmin_mfa):
        if now - _pending_garmin_mfa[state_id]['created'] > GARMIN_MFA_TTL_SECONDS:
            del _pending_garmin_mfa[state_id]


def _save_garmin_tokens(garmin_client, username):
    garmin_client.client.dump(str(_garmin_token_dir(username)))
    _garmin_clients.pop(username, None)
    if not APP_TESTING:
        threading.Thread(target=_initial_garmin_sync, args=(username,), daemon=True).start()


def _initial_garmin_sync(username):
    """Första hämtningen efter koppling — aktiviteter + historik i bakgrunden."""
    user_id = USERS.get(username, {}).get('id')
    if user_id is None:
        return
    try:
        run_sync(username=username, user_id=user_id)
    except Exception as e:
        print(f'Initial Garmin-synk ({username}) aktiviteter:', e)
    try:
        collect_health_history(14, username=username)
        collect_metric_history(45, username=username)
    except Exception as e:
        print(f'Initial Garmin-synk ({username}) historik:', e)


@app.post('/api/garmin/connect')
def garmin_connect():
    data = request.get_json(silent=True) or {}
    email = str(data.get('email') or '').strip()
    password = data.get('password')
    if not email or '@' not in email or len(email) > 254 \
            or not isinstance(password, str) or not password or len(password) > 1024:
        return _api_error('invalid_garmin_credentials', 'Ange e-post och lösenord för Garmin Connect.', 400)

    limiter_key = f'garmin-connect:{uid()}'
    allowed, retry_after = GARMIN_CONNECT_LIMITER.check(limiter_key)
    if not allowed:
        response, status = _api_error(
            'too_many_attempts', 'För många försök. Vänta en stund och försök igen.', 429)
        response.headers['Retry-After'] = str(retry_after)
        return response, status

    garmin_client = Garmin(email=email, password=password, return_on_mfa=True)
    try:
        status_flag, _ = garmin_client.login()
    except Exception:
        GARMIN_CONNECT_LIMITER.record_failure(limiter_key)
        logger.warning('Garmin connect failed', extra={
            'event': 'garmin.connect_failed',
            'request_id': _request_id(),
            'user_id': uid(),
        })
        # 400, inte 401 — frontendens fetch-interceptor tolkar 401 som utgången session.
        return _api_error(
            'garmin_login_failed',
            'Garmin godkände inte inloggningen. Kontrollera e-post och lösenord.', 400)

    if status_flag == 'needs_mfa':
        state_id = secrets.token_urlsafe(24)
        with _pending_garmin_lock:
            _prune_pending_garmin()
            _pending_garmin_mfa[state_id] = {
                'garmin': garmin_client,
                'username': uname(),
                'created': time.time(),
            }
        logger.info('Garmin MFA required', extra={
            'event': 'garmin.mfa_required',
            'request_id': _request_id(),
            'user_id': uid(),
        })
        return jsonify({'ok': True, 'mfaRequired': True, 'stateId': state_id})

    GARMIN_CONNECT_LIMITER.reset(limiter_key)
    _save_garmin_tokens(garmin_client, uname())
    logger.info('Garmin connected', extra={
        'event': 'garmin.connected',
        'request_id': _request_id(),
        'user_id': uid(),
    })
    return jsonify({'ok': True, 'mfaRequired': False, 'connected': True})


@app.post('/api/garmin/mfa')
def garmin_mfa():
    data = request.get_json(silent=True) or {}
    state_id = str(data.get('stateId') or '')
    code = str(data.get('code') or '').strip()
    if not state_id or not code or len(code) > 16:
        return _api_error('invalid_mfa_request', 'Ange engångskoden från Garmin.', 400)
    with _pending_garmin_lock:
        _prune_pending_garmin()
        entry = _pending_garmin_mfa.get(state_id)
        if entry and entry['username'] == uname():
            del _pending_garmin_mfa[state_id]
        else:
            entry = None
    if not entry:
        return _api_error(
            'mfa_state_expired',
            'Kopplingsförsöket har gått ut — börja om med e-post och lösenord.', 410)
    try:
        entry['garmin'].resume_login(None, code)
    except Exception:
        logger.warning('Garmin MFA failed', extra={
            'event': 'garmin.mfa_failed',
            'request_id': _request_id(),
            'user_id': uid(),
        })
        return _api_error(
            'invalid_mfa_code',
            'Garmin godkände inte koden — börja om med e-post och lösenord.', 400)
    GARMIN_CONNECT_LIMITER.reset(f'garmin-connect:{uid()}')
    _save_garmin_tokens(entry['garmin'], uname())
    logger.info('Garmin connected', extra={
        'event': 'garmin.connected',
        'request_id': _request_id(),
        'user_id': uid(),
    })
    return jsonify({'ok': True, 'connected': True})


# --- Mål per användare ---
# I tester (APP_TESTING) bor målen i minnet, i drift i user_goals-tabellen.
_TESTING_GOALS = {}


def get_user_goal(user_id):
    if APP_TESTING:
        goal = _TESTING_GOALS.get(user_id)
        return dict(goal) if goal else None
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT * FROM user_goals WHERE user_id=%s', (user_id,))
            row = cur.fetchone()
    return dict(row) if row else None


def save_user_goal(user_id, goal):
    if APP_TESTING:
        _TESTING_GOALS[user_id] = dict(goal)
        return
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO user_goals
                (user_id, goal_title, goal_deadline, current_best, secondary_goal, start_date, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id) DO UPDATE SET
                    goal_title=EXCLUDED.goal_title, goal_deadline=EXCLUDED.goal_deadline,
                    current_best=EXCLUDED.current_best, secondary_goal=EXCLUDED.secondary_goal,
                    start_date=EXCLUDED.start_date, updated_at=EXCLUDED.updated_at''',
                (user_id, goal['goal_title'], goal.get('goal_deadline'), goal.get('current_best'),
                 goal.get('secondary_goal'), goal.get('start_date'), time.time()))
        conn.commit()


def _goal_prompt_block(user_id):
    """Målrader för AI-prompterna, byggda från användarens eget mål."""
    goal = None
    try:
        goal = get_user_goal(user_id)
    except Exception as e:
        print('goal prompt fetch:', e)
    if not goal:
        return 'GOAL: No explicit goal set yet — coach for general fitness, consistency and health.'
    line = f"GOAL: {goal['goal_title']}"
    if goal.get('goal_deadline'):
        line += f" · Deadline: {goal['goal_deadline']}"
    if goal.get('current_best'):
        line += f" · Current best: {goal['current_best']}"
    lines = [line]
    if goal.get('secondary_goal'):
        lines.append(f"SECONDARY GOAL: {goal['secondary_goal']}")
    return '\n'.join(lines)


@app.get('/api/goals')
def get_goals():
    try:
        goal = get_user_goal(uid())
    except Exception as e:
        return _server_error(e, 'goals.load_failed', message='Kunde inte hämta målet.')
    return jsonify({'goal': goal})


@app.put('/api/goals')
def put_goals():
    data = request.get_json(silent=True) or {}
    title = str(data.get('goalTitle') or '').strip()
    if not title or len(title) > 200:
        return _api_error('invalid_goal', 'Skriv ett mål på max 200 tecken.', 400)
    deadline = str(data.get('goalDeadline') or '').strip()
    if deadline and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', deadline):
        return _api_error('invalid_goal_deadline', 'Deadline måste vara ett datum (ÅÅÅÅ-MM-DD).', 400)
    try:
        existing = get_user_goal(uid()) or {}
        goal = {
            'goal_title': title,
            'goal_deadline': deadline or None,
            'current_best': str(data.get('currentBest') or '').strip()[:200] or None,
            'secondary_goal': str(data.get('secondaryGoal') or '').strip()[:300] or None,
            'start_date': existing.get('start_date') or date.today().isoformat(),
        }
        save_user_goal(uid(), goal)
        saved = get_user_goal(uid())
    except Exception as e:
        return _server_error(e, 'goals.save_failed', message='Kunde inte spara målet.')
    logger.info('Goal saved', extra={
        'event': 'goals.saved',
        'request_id': _request_id(),
        'user_id': uid(),
    })
    return jsonify({'ok': True, 'goal': saved})


@app.post('/api/garmin/disconnect')
def garmin_disconnect():
    username = uname()
    token_dir = _garmin_token_dir(username)
    if token_dir.is_dir():
        shutil.rmtree(token_dir, ignore_errors=True)
    _garmin_clients.pop(username, None)
    logger.info('Garmin disconnected', extra={
        'event': 'garmin.disconnected',
        'request_id': _request_id(),
        'user_id': uid(),
    })
    return jsonify({'ok': True, 'connected': False})


def _strava_callback_page(status):
    messages = {
        'connected': ('Strava är anslutet', 'Aktiviteterna synkas nu till Trainyze.'),
        'denied': ('Anslutningen avbröts', 'Strava gav inte Trainyze åtkomst.'),
        'scope': ('Behörighet saknas', 'Tillåt åtkomst till aktiviteter och försök igen.'),
        'expired': ('Länken har gått ut', 'Stäng fönstret och starta anslutningen igen.'),
        'error': ('Strava kunde inte anslutas', 'Försök igen om en stund.'),
    }
    title, message = messages.get(status, messages['error'])
    return f'''<!doctype html><html lang="sv"><head><meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>{title}</title><style>
      body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d0f14;
      color:#e5e7eb;font-family:system-ui,sans-serif}}.card{{width:min(380px,calc(100% - 40px));
      padding:30px;border:1px solid #303641;border-radius:16px;background:#161a22;text-align:center}}
      b{{color:#fc5200}}p{{color:#9ca3af;line-height:1.5}}a{{color:#c8f135}}</style></head>
      <body data-strava-status="{status}"><div class="card"><b>STRAVA</b><h2>{title}</h2>
      <p>{message}</p><a href="/">Tillbaka till Trainyze</a></div>
      <script src="/strava-callback.js?v=1"></script></body></html>'''


@app.post('/api/strava/connect')
def strava_connect():
    if not _strava_configured():
        return _api_error(
            'strava_not_configured',
            'Strava behöver konfigureras av Trainyzes administratör först.', 503)
    state = _create_strava_state(uid())
    return jsonify({
        'authorizationUrl': strava_integration.authorization_url(
            STRAVA_CLIENT_ID, STRAVA_REDIRECT_URI, state),
    })


@app.get('/strava/callback')
def strava_callback():
    state = str(request.args.get('state') or '')
    user_id = _consume_strava_state(state)
    if user_id is None:
        return _strava_callback_page('expired'), 400
    if request.args.get('error'):
        return _strava_callback_page('denied'), 400
    code = str(request.args.get('code') or '')
    if not code or len(code) > 512:
        return _strava_callback_page('error'), 400
    username = _strava_username_for_user_id(user_id)
    if not username:
        return _strava_callback_page('error'), 400
    try:
        payload = strava_integration.exchange_code(
            STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, code)
        scope = str(request.args.get('scope') or payload.get('scope') or '')
        granted = set(filter(None, re.split(r'[ ,]+', scope)))
        if 'activity:read_all' not in granted:
            return _strava_callback_page('scope'), 403
        payload['scope'] = scope
        _save_strava_tokens(username, payload)
        if not APP_TESTING:
            threading.Thread(
                target=_strava_activities, args=(username, 120, True), daemon=True
            ).start()
        logger.info('Strava connected', extra={
            'event': 'strava.connected', 'request_id': _request_id(), 'user_id': user_id,
        })
        return _strava_callback_page('connected')
    except Exception as exc:
        logger.exception('Strava callback failed', extra={
            'event': 'strava.connect_failed', 'request_id': _request_id(), 'user_id': user_id,
        })
        return _strava_callback_page('error'), 502


@app.get('/api/strava/status')
def strava_status():
    return jsonify({
        'configured': _strava_configured(),
        **_strava_profile(uname()),
    })


@app.post('/api/strava/sync')
def strava_sync():
    if not _strava_connected(uname()):
        return _api_error('strava_not_connected', 'Strava är inte anslutet.', 409)
    try:
        activities_out = _strava_activities(uname(), 120, True)
        return jsonify({'ok': True, 'activities': len(activities_out)})
    except Exception as exc:
        return _server_error(exc, 'strava.sync_failed', status=502,
                             code='strava_provider_error',
                             message='Strava kunde inte synkas just nu.')


@app.post('/api/strava/disconnect')
def strava_disconnect():
    username = uname()
    payload = _read_strava_tokens(username) or {}
    token = payload.get('refresh_token') or payload.get('access_token')
    if token and _strava_configured():
        try:
            requests.post(
                strava_integration.REVOKE_URL,
                auth=(STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET),
                data={'token': token, 'token_type_hint': 'refresh_token'}, timeout=10,
            )
        except requests.RequestException:
            logger.warning('Strava revoke unavailable', extra={
                'event': 'strava.revoke_failed', 'request_id': _request_id(), 'user_id': uid(),
            })
    try:
        _strava_token_path(username).unlink(missing_ok=True)
    except OSError as exc:
        return _server_error(exc, 'strava.disconnect_failed')
    clear_cache('strava:activities', user_id=uid())
    return jsonify({'ok': True, 'connected': False})


@app.get('/api/status')
def status():
    return jsonify({'status': 'ok'})

def _cns_score_from_health(h):
    if not h:
        return None
    hrv = h.get('hrv') or {}
    sleep = h.get('sleep') or {}
    readiness = h.get('readiness') or {}
    stress = h.get('stress') or {}
    hrv_pct = hrv.get('component') if hrv.get('component') is not None else hrv.get('pct')
    hrv_pct = hrv_pct if hrv_pct is not None else 50
    sleep_score = sleep.get('score') if sleep.get('score') is not None else 50
    readiness_score = readiness.get('score') if readiness.get('score') is not None else 50
    stress_val = stress.get('avg') if stress.get('avg') is not None else 50
    return round(
        0.40 * min(float(hrv_pct), 100) +
        0.30 * float(sleep_score) +
        0.20 * float(readiness_score) +
        0.10 * (100 - min(float(stress_val), 100))
    )

def compute_bevel_rest_or_train(h):
    """Beräknar RestOrTrain-besked, Mål-Strain och Sömnskuld (Bevel / RestOrTrain)."""
    if not h:
        return {
            'decision': 'unknown',
            'badge': 'DATA SAKNAS',
            'badgeColor': 'gray',
            'headline': 'Väntar på hälsodata',
            'explanation': 'Synka Garmin för att beräkna dagens rekommendation.',
            'targetStrain': {'min': 0, 'max': 0, 'label': 'Okänt'},
            'sleepDebtMinutes': 0,
            'score': None,
        }

    cns = _cns_score_from_health(h)
    readiness_val = (h.get('readiness') or {}).get('score')
    score = readiness_val if readiness_val is not None else (cns if cns is not None else 50)

    sl = h.get('sleep') or {}
    total_sleep_sec = sl.get('totalSec') or 0
    sleep_debt_minutes = round((7.5 * 3600 - total_sleep_sec) / 60) if total_sleep_sec > 0 else 0

    if score >= 75:
        decision = 'train_hard'
        badge = 'KÖR HÅRT'
        badge_color = 'green'
        headline = 'Kroppen är toppåterhämtad'
        explanation = 'Hög beredskap och god återhämtning. Perfekt dag för kvalitetspass, tröskel eller långpass.'
        target_strain = {'min': 60, 'max': 85, 'label': 'Hög dos (60–85)'}
    elif score >= 50:
        decision = 'train_moderate'
        badge = 'ENLIGT PLAN'
        badge_color = 'blue'
        headline = 'Normal återhämtning'
        explanation = 'Kroppen svarar väl. Träna enligt plan och håll jämn ansträngning.'
        target_strain = {'min': 40, 'max': 65, 'label': 'Måttlig dos (40–65)'}
    elif score >= 35:
        decision = 'train_easy'
        badge = 'LUGNT PASS'
        badge_color = 'amber'
        headline = 'Nedsatt beredskap – kör lugnt'
        explanation = 'Återhämtningen är under normal nivå. Ersätt tuffa intervaller med lugn jogg (Zon 2).'
        target_strain = {'min': 20, 'max': 40, 'label': 'Aktiv vila (20–40)'}
    else:
        decision = 'rest'
        badge = 'VILA IDAG'
        badge_color = 'red'
        headline = 'Prioritera full vila och sömn'
        explanation = 'Låg beredskap och ackumulerad trötthet. Vila helt eller ta en lugn promenad.'
        target_strain = {'min': 0, 'max': 20, 'label': 'Vila (0–20)'}

    return {
        'decision': decision,
        'badge': badge,
        'badgeColor': badge_color,
        'headline': headline,
        'explanation': explanation,
        'targetStrain': target_strain,
        'sleepDebtMinutes': sleep_debt_minutes,
        'score': score,
    }

def _session_date(year, week, dow):
    return date.fromisocalendar(year, int(week), int(dow) + 1)

def _mobile_widget_payload(user_id):
    today = date.today()
    year = today.year
    iso_week = today.isocalendar()[1]
    monday = today - timedelta(days=today.weekday())
    next_monday = monday + timedelta(days=7)

    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''SELECT COALESCE(SUM(distance), 0) AS meters
                           FROM activities
                           WHERE user_id=%s AND date >= %s AND date < %s
                             AND type IN ('running','track_running','treadmill_running','trail_running')''',
                        (user_id, monday.isoformat(), next_monday.isoformat()))
            completed_km = round(float((cur.fetchone() or {}).get('meters') or 0) / 1000, 1)

            cur.execute('''SELECT COALESCE(SUM(km), 0) AS km
                           FROM plan_sessions
                           WHERE user_id=%s AND week=%s AND status IN ('planned','completed')''',
                        (user_id, iso_week))
            planned_km = round(float((cur.fetchone() or {}).get('km') or 0), 1)

            cur.execute('''SELECT id, week, dow, type, km, title, detail
                           FROM plan_sessions
                           WHERE user_id=%s AND status='planned'
                             AND type IN ('run','race')
                             AND (week > %s OR (week = %s AND dow >= %s))
                           ORDER BY week, dow
                           LIMIT 8''',
                        (user_id, iso_week, iso_week, today.weekday()))
            candidates = [dict(r) for r in cur.fetchall()]

    next_quality = None
    for session in candidates:
        try:
            session_day = _session_date(year, session['week'], session['dow'])
        except Exception:
            continue
        if session_day < today:
            continue
        next_quality = {
            'date': session_day.isoformat(),
            'weekday': session_day.strftime('%a'),
            'title': session.get('title'),
            'detail': session.get('detail'),
            'km': float(session.get('km') or 0),
            'type': session.get('type'),
        }
        break

    # Hälsocachen fylls bara av /api/health, alltså när någon öppnar
    # dashboarden — och run_sync tömmer den var tredje timme. Widgeten får
    # därför inte hänga på den ensam; utan reserv stod den tom ända tills
    # man råkade besöka sajten. health_history skrivs av den dagliga
    # rutinen och finns alltid.
    h_row = get_cache('health', user_id)
    health = h_row[0] if h_row else None
    source = 'live'
    if not has_health_payload(health or {}):
        try:
            health = latest_health_snapshot(user_id, today.isoformat()) or {}
        except Exception as e:
            print('Widget: kunde inte läsa hälsohistorik:', e)
            health = {}
        source = 'history' if health else 'none'
    sleep = health.get('sleep') or {}
    return {
        'date': today.isoformat(),
        'week': iso_week,
        'weeklyVolume': {
            'completedKm': completed_km,
            'plannedKm': planned_km,
            'remainingKm': round(max(0, planned_km - completed_km), 1) if planned_km else None,
        },
        'cns': {
            'score': _cns_score_from_health(health),
        },
        'sleep': {
            'score': sleep.get('score'),
            'sourceDate': sleep.get('sourceDate') or health.get('sourceDate') or today.isoformat(),
        },
        'nextQuality': next_quality,
        'source': source,
    }

@app.get('/api/widget/mobile')
def mobile_widget():
    return jsonify(_mobile_widget_payload(uid()))

@app.get('/api/weather/current')
def current_weather():
    """Aktuell utetemperatur från Open-Meteo."""
    try:
        params = {
            'latitude': WEATHER_LAT,
            'longitude': WEATHER_LON,
            'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m',
            'timezone': 'auto',
            'wind_speed_unit': 'ms',
        }
        r = requests.get('https://api.open-meteo.com/v1/forecast', params=params, timeout=6)
        r.raise_for_status()
        payload = r.json()
        current = payload.get('current') or {}
        units = payload.get('current_units') or {}
        code = current.get('weather_code')
        return jsonify({
            'ok': True,
            'source': 'Open-Meteo',
            'location': WEATHER_LOCATION,
            'latitude': WEATHER_LAT,
            'longitude': WEATHER_LON,
            'time': current.get('time'),
            'temperature_c': current.get('temperature_2m'),
            'apparent_temperature_c': current.get('apparent_temperature'),
            'humidity_pct': current.get('relative_humidity_2m'),
            'wind_speed_ms': current.get('wind_speed_10m'),
            'weather_code': code,
            'weather_text': WEATHER_CODES.get(code, 'okänt väderläge'),
            'units': units,
        })
    except Exception as e:
        return _server_error(
            e, 'weather.current_failed', status=502, code='weather_unavailable',
            message='Väderdata kunde inte hämtas.', extra={'ok': False, 'source': 'Open-Meteo'}
        )

# --- Klimat: sensoravläsningar direkt från MQTT ---
# Avläsningarna kommer från zigbee2mqtt och lagras av MQTT-tråden ovan. Ingen
# AC-styrning är inblandad: den här sidan läser bara av rummet.

@app.get('/api/climate')
def climate_current():
    """Senaste avläsningen per sensor, plus snitt över de som fortfarande svarar."""
    if uid() != 1:
        return jsonify({'available': False, 'error': 'Climate data is owner-only'}), 403
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT DISTINCT ON (sensor)
                        sensor, ts, temperature_c, humidity_pct, battery_pct, linkquality
                    FROM sensor_readings
                    ORDER BY sensor, ts DESC''')
                rows = cur.fetchall()
    except Exception as e:
        return _server_error(
            e, 'climate.current_failed', status=502, code='climate_unavailable',
            message='Klimatdata kunde inte hämtas.', extra={'available': False, 'sensors': []}
        )

    now = datetime.now(timezone.utc)
    sensors = {}
    for sensor, ts, temperature, humidity, battery, linkquality in rows:
        age = (now - ts).total_seconds()
        sensors[sensor] = {
            'name': sensor,
            'temperature_c': round(temperature, 1) if temperature is not None else None,
            'humidity_pct': round(humidity, 1) if humidity is not None else None,
            'battery_pct': round(battery) if battery is not None else None,
            'linkquality': linkquality,
            'ts': ts.isoformat(),
            'age_seconds': int(age),
            'stale': age > CLIMATE_STALE_SECONDS,
        }

    # En sensor som zigbee2mqtt känner till men som aldrig hört av sig ska synas som
    # tyst, inte saknas helt — annars märks ett avbrott först när någon undrar varför
    # snittet ser konstigt ut.
    with _sensor_roster_lock:
        roster = dict(_sensor_roster)
    for name, meta in roster.items():
        entry = sensors.get(name)
        if entry is None:
            entry = {
                'name': name, 'temperature_c': None, 'humidity_pct': None,
                'battery_pct': None, 'linkquality': None, 'ts': None,
                'age_seconds': None, 'stale': True,
            }
            sensors[name] = entry
        entry['model'] = meta.get('model')

    listed = sorted(sensors.values(), key=lambda s: s['name'])
    live = [s for s in listed if not s['stale']]
    temperatures = [s['temperature_c'] for s in live if s['temperature_c'] is not None]
    humidities = [s['humidity_pct'] for s in live if s['humidity_pct'] is not None]
    return jsonify({
        'available': True,
        'sensors': listed,
        'average': {
            'temperature_c': round(sum(temperatures) / len(temperatures), 1) if temperatures else None,
            'humidity_pct': round(sum(humidities) / len(humidities), 1) if humidities else None,
            'sensor_count': len(live),
        },
        'stale_after_seconds': CLIMATE_STALE_SECONDS,
        'mqtt': {'connected': _mqtt_state['connected'], 'error': _mqtt_state['last_error']},
    })


@app.get('/api/climate/history')
def climate_history():
    """Rums- och utetemperatur samt luftfuktighet för klimatgrafen."""
    if uid() != 1:
        return jsonify({'available': False, 'error': 'Climate data is owner-only'}), 403
    try:
        hours = int(request.args.get('hours', 24))
    except (TypeError, ValueError):
        hours = 24
    hours = max(1, min(168, hours))
    bucket_seconds = 300
    try:
        with db() as conn:
            with conn.cursor() as cur:
                # Snittet per femminutersfönster: sensorerna skickar i otakt, så råa
                # punkter skulle sicksacka mellan sensorer i stället för att visa rummet.
                cur.execute('''SELECT
                        to_timestamp(floor(extract(epoch FROM ts) / %s) * %s) AS bucket,
                        avg(temperature_c), avg(humidity_pct), count(DISTINCT sensor)
                    FROM sensor_readings
                    WHERE ts > now() - make_interval(hours => %s)
                    GROUP BY bucket
                    ORDER BY bucket''',
                    (bucket_seconds, bucket_seconds, hours))
                rows = cur.fetchall()
    except Exception as e:
        return _server_error(
            e, 'climate.history_failed', status=502, code='climate_unavailable',
            message='Klimathistoriken kunde inte hämtas.',
            extra={'available': False, 'points': [], 'humidity_points': []}
        )

    points, humidity_points = [], []
    for bucket, temperature, humidity, sensor_count in rows:
        stamp = bucket.isoformat()
        if temperature is not None:
            points.append({'t': stamp, 'temp': round(float(temperature), 1), 'sensors': sensor_count})
        if humidity is not None:
            humidity_points.append({'t': stamp, 'humidity': round(float(humidity), 1), 'sensors': sensor_count})
    return jsonify({
        'available': True,
        'hours': hours,
        'points': points,
        'humidity_points': humidity_points,
        'outside_points': _get_outdoor_temperature_history(hours),
        'outside_location': WEATHER_LOCATION,
    })


# --- Avvecklad AC-styrning ---
# tuya-ac-keeper är borttagen. Rutterna finns kvar och svarar 410 så att en gammal
# öppen flik, ett bokmärke eller ESP32-knappen får ett begripligt besked i stället
# för en 404 som ser ut som ett driftfel.
_AC_REMOVED_MESSAGE = 'AC-styrningen är borttagen. Klimatsidan visar bara sensoravläsningar.'


def _ac_removed():
    return jsonify({
        'ok': False, 'available': False, 'removed': True,
        'code': 'ac_removed', 'error': _AC_REMOVED_MESSAGE,
    }), 410


@app.get('/api/ac')
def ac_proxy():
    return _ac_removed()


@app.get('/api/ac/history')
def ac_history():
    return _ac_removed()


@app.get('/api/ac/loop')
def ac_loop_status():
    return _ac_removed()


@app.post('/api/ac/loop')
def ac_loop_control():
    return _ac_removed()


@app.get('/api/ac/bedtime')
def ac_bedtime_get():
    return _ac_removed()


@app.post('/api/ac/bedtime')
def ac_bedtime_set():
    return _ac_removed()


@app.post('/api/ac/manual-control')
def ac_manual_control():
    return _ac_removed()


@app.post('/api/ac/button/off')
def ac_button_off():
    return _ac_removed()


@app.post('/api/ac/button/auto-on')
def ac_button_auto_on():
    return _ac_removed()


@app.post('/api/ac/setpoint')
def ac_setpoint():
    return _ac_removed()

def _interval_work_laps_for_activity(client, activity_id):
    """Return fast 300-550 m work reps from Garmin splits for calendar labels."""
    try:
        splits = client.get_activity_splits(activity_id)
        laps = splits.get('lapDTOs') or splits.get('laps') or []
    except Exception:
        return []
    work = []
    for idx, lap in enumerate(laps):
        dist = lap.get('distance') or 0
        dur = lap.get('duration') or lap.get('elapsedDuration') or 0
        speed = lap.get('averageSpeed') or lap.get('avgSpeed') or 0
        if 300 <= dist <= 550 and dur <= 150 and speed > 0:
            work.append({'idx': idx, 'dist': dist, 'dur': dur, 'speed': speed})
    return sorted(work, key=lambda l: l['idx'])

def _add_calendar_activity_summaries(activities):
    try:
        client = get_garmin(uname())
    except Exception:
        return activities
    for activity in activities:
        type_key = ((activity.get('activityType') or {}).get('typeKey') or activity.get('type') or '').lower()
        name = (activity.get('activityName') or activity.get('name') or '').lower()
        if not any(token in type_key + ' ' + name for token in ('track', 'interval', 'fartlek', 'repeat')):
            continue
        activity_id = activity.get('activityId') or activity.get('id')
        if not activity_id:
            continue
        laps = _interval_work_laps_for_activity(client, activity_id)
        if len(laps) < 4:
            continue
        avg_dist = sum(l['dist'] for l in laps) / len(laps)
        rep_m = int(round(avg_dist / 100) * 100)
        activity['calendarSummary'] = {
            'kind': 'interval',
            'label': f"{len(laps)}×{rep_m}"
        }
    return activities

@app.get('/api/activities')
def activities():
    try:
        days = max(1, min(365, int(request.args.get('days', 50))))
    except (TypeError, ValueError):
        days = 50
    start = (date.today() - timedelta(days=days)).isoformat()
    garmin_connected = _garmin_connected(uname())
    strava_connected = _strava_connected(uname())
    if request.args.get('refresh') == '1' and garmin_connected:
        try:
            client = get_garmin(uname())
            ingest_activities(client.get_activities(0, 100), uid())
        except Exception as e:
            print('activities refresh failed', e)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT raw FROM activities
                WHERE user_id=%s AND date >= %s
                ORDER BY date DESC LIMIT 200''', (uid(), start))
            rows = cur.fetchall()
    # Garmin remains the primary source when both providers are connected. This
    # avoids showing the same workout twice, since Strava commonly receives the
    # activity from Garmin itself.
    if rows and garmin_connected:
        activities_out = [r[0] for r in rows]
        if request.args.get('calendar') == '1':
            activities_out = _add_calendar_activity_summaries(activities_out)
        return jsonify({'activities': activities_out, 'source': 'database'})

    if strava_connected:
        try:
            activities_out = _strava_activities(
                uname(), days, request.args.get('refresh') == '1')
            return jsonify({'activities': activities_out, 'source': 'strava'})
        except Exception as exc:
            logger.warning('Strava activities unavailable', extra={
                'event': 'strava.activities_failed', 'request_id': _request_id(),
                'user_id': uid(),
            })
            if rows:
                activities_out = [r[0] for r in rows]
                if request.args.get('calendar') == '1':
                    activities_out = _add_calendar_activity_summaries(activities_out)
                return jsonify({'activities': activities_out, 'source': 'database'})
            return _server_error(exc, 'strava.activities_failed', status=502,
                                 code='strava_provider_error',
                                 message='Aktiviteterna kunde inte hämtas från Strava.')

    if rows:
        activities_out = [r[0] for r in rows]
        if request.args.get('calendar') == '1':
            activities_out = _add_calendar_activity_summaries(activities_out)
        return jsonify({'activities': activities_out, 'source': 'database'})
    if not garmin_connected:
        return jsonify({'activities': [], 'source': 'not_connected', 'notConnected': True})
    try:
        client = get_garmin(uname())
        acts = client.get_activities(0, 50)
        ingest_activities(acts, uid())
        return jsonify({'activities': acts, 'source': 'garmin'})
    except Exception as e:
        return _server_error(e, 'activities.load_failed', message='Aktiviteterna kunde inte hämtas.')


@app.get('/api/strava/activities/<int:activity_id>')
def strava_activity_details(activity_id):
    """Return rich detail for an activity owned by the connected Strava athlete."""
    if activity_id <= 0:
        return _api_error('invalid_activity_id', 'Aktivitets-id är ogiltigt.', 400)
    if not _strava_connected(uname()):
        return _api_error('strava_not_connected', 'Strava är inte anslutet.', 409)
    cache_key = f'strava:activity-detail:{activity_id}'
    cached = get_cache(cache_key, uid())
    if cached and time.time() - cached[1] < 6 * 3600:
        return jsonify({'activity': cached[0], 'source': 'cache'})
    try:
        activity = strava_integration.activity_detail(
            _strava_access_token(uname()), activity_id)
        athlete_id = _strava_profile(uname()).get('athleteId')
        activity_athlete_id = activity.pop('_athleteId', None)
        if athlete_id and activity_athlete_id and int(activity_athlete_id) != int(athlete_id):
            return _api_error('activity_not_found', 'Aktiviteten hittades inte.', 404)
        set_cache(cache_key, activity, uid())
        return jsonify({'activity': activity, 'source': 'strava'})
    except strava_integration.StravaError as exc:
        return _server_error(exc, 'strava.activity_detail_failed', status=502,
                             code='strava_provider_error',
                             message='Passdetaljerna kunde inte hämtas från Strava.')


def _stored_activity_for_user(activity_id, user_id):
    """Return the stored Garmin summary and enforce per-user ownership."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT raw FROM activities WHERE id=%s AND user_id=%s',
                        (activity_id, user_id))
            row = cur.fetchone()
    return row[0] if row else None


def _garmin_activity_part(client, method_name, activity_id, default):
    """A missing optional Garmin panel must not make the activity unusable."""
    try:
        return getattr(client, method_name)(activity_id)
    except Exception as exc:
        logger.info('Optional Garmin activity data unavailable', extra={
            'event': 'activity.detail_partial',
            'request_id': _request_id(),
            'user_id': uid(),
        })
        return default


def _is_strength_activity(raw):
    type_key = str(
        ((raw.get('activityType') or {}).get('typeKey')) or raw.get('type') or ''
    ).lower()
    return any(token in type_key for token in ('strength', 'fitness', 'weight', 'gym'))


def _stored_strength_exercises(activity_id, user_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT id, exercise, sets, reps, weight, note
                FROM strength_exercises
                WHERE session_id=%s AND user_id=%s ORDER BY id''',
                        (str(activity_id), user_id))
            rows = cur.fetchall()
    return [{
        'id': row[0], 'exercise': row[1], 'sets': row[2], 'reps': row[3],
        'weight': float(row[4]) if row[4] is not None else None,
        'note': row[5] or '',
    } for row in rows]


@app.get('/api/activities/<int:activity_id>')
def activity_details(activity_id):
    """Return route, charts, zones and laps for one owned Garmin activity."""
    if activity_id <= 0:
        return _api_error('invalid_activity_id', 'Aktivitets-id är ogiltigt.', 400)
    try:
        raw = _stored_activity_for_user(activity_id, uid())
        if raw is None:
            return _api_error('activity_not_found', 'Aktiviteten hittades inte.', 404)

        strength_activity = _is_strength_activity(raw)
        strength_exercises = _stored_strength_exercises(activity_id, uid()) \
            if strength_activity else []
        cache_key = f'activity-detail:v2:{activity_id}'
        cached = get_cache(cache_key, uid())
        if cached and time.time() - cached[1] < 6 * 3600:
            cached_activity = dict(cached[0])
            if strength_activity:
                cached_activity['strengthExercises'] = strength_exercises
            return jsonify({'activity': cached_activity, 'source': 'cache'})

        if not _garmin_connected(uname()):
            return _api_error('garmin_not_connected',
                              'Garmin behöver anslutas för att visa passdetaljer.', 409)
        client = get_garmin(uname())
        exercise_sets = _garmin_activity_part(
            client, 'get_activity_exercise_sets', activity_id, {}) \
            if strength_activity else {}
        normalized = normalize_activity_detail(
            raw=raw,
            activity=_garmin_activity_part(client, 'get_activity', activity_id, {}),
            details=_garmin_activity_part(client, 'get_activity_details', activity_id, {}),
            splits=_garmin_activity_part(client, 'get_activity_splits', activity_id, {}),
            hr_zones=_garmin_activity_part(
                client, 'get_activity_hr_in_timezones', activity_id, []),
            power_zones=_garmin_activity_part(
                client, 'get_activity_power_in_timezones', activity_id, []),
            weather=_garmin_activity_part(client, 'get_activity_weather', activity_id, {}),
            gear=_garmin_activity_part(client, 'get_activity_gear', activity_id, []),
            exercise_sets=exercise_sets,
            strength_exercises=strength_exercises,
        )
        set_cache(cache_key, normalized, uid())
        return jsonify({'activity': normalized, 'source': 'garmin'})
    except Exception as exc:
        return _server_error(exc, 'activity.detail_failed',
                             message='Passdetaljerna kunde inte hämtas.')


def _feedback_source(raw):
    return 'strava' if str(raw or '').lower() == 'strava' else 'garmin'


def _feedback_activity_owned(activity_id, source, user_id, username):
    if source == 'garmin':
        return _stored_activity_for_user(activity_id, user_id) is not None
    return _strava_connected(username)


@app.get('/api/activities/<int:activity_id>/feedback')
def activity_feedback_get(activity_id):
    source = _feedback_source(request.args.get('source'))
    if not _feedback_activity_owned(activity_id, source, uid(), uname()):
        return _api_error('activity_not_found', 'Aktiviteten hittades inte.', 404)
    return jsonify({'activityId': activity_id, 'source': source,
                    'feedback': ACTIVITY_FEEDBACK_STORE.get(uid(), source, activity_id)})


@app.put('/api/activities/<int:activity_id>/feedback')
def activity_feedback_save(activity_id):
    source = _feedback_source(request.args.get('source'))
    if not _feedback_activity_owned(activity_id, source, uid(), uname()):
        return _api_error('activity_not_found', 'Aktiviteten hittades inte.', 404)
    try:
        value = ACTIVITY_FEEDBACK_STORE.save(
            uid(), source, activity_id, request.get_json(silent=True) or {})
        clear_cache(f'activity-ai:v1:{source}:{activity_id}', user_id=uid())
        return jsonify({'ok': True, 'activityId': activity_id, 'source': source,
                        'feedback': value})
    except (TypeError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return _server_error(exc, 'activity_feedback.save_failed',
                             message='Passkänslan kunde inte sparas.')


def _activity_ai_detail(activity_id, source, user_id, username):
    """Load an owned activity from the detail cache used by the open modal."""
    if source == 'strava':
        if not _strava_connected(username):
            return None
        cache_key = f'strava:activity-detail:{activity_id}'
        cached = get_cache(cache_key, user_id)
        if cached:
            return dict(cached[0])
        detail = strava_integration.activity_detail(
            _strava_access_token(username), activity_id)
        athlete_id = _strava_profile(username).get('athleteId')
        detail_athlete_id = detail.pop('_athleteId', None)
        if athlete_id and detail_athlete_id and int(detail_athlete_id) != int(athlete_id):
            return None
        set_cache(cache_key, detail, user_id)
        return detail

    raw = _stored_activity_for_user(activity_id, user_id)
    if raw is None:
        return None
    cached = get_cache(f'activity-detail:v2:{activity_id}', user_id)
    if cached:
        detail = dict(cached[0])
    else:
        strength_exercises = _stored_strength_exercises(activity_id, user_id) \
            if _is_strength_activity(raw) else []
        detail = normalize_activity_detail(
            raw=raw, strength_exercises=strength_exercises)
    detail['source'] = 'garmin'
    return detail


def _activity_ai_plan_context(activity, user_id):
    day_text = str(activity.get('date') or '')[:10]
    try:
        activity_day = date.fromisoformat(day_text)
    except ValueError:
        return None
    week, dow = _iso_week_dow(activity_day)
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''SELECT title,type,km,detail,status,execution
                FROM plan_sessions WHERE user_id=%s AND week=%s AND dow=%s
                ORDER BY CASE WHEN status='completed' THEN 0 ELSE 1 END, id LIMIT 1''',
                        (user_id, week, dow))
            row = cur.fetchone()
    return dict(row) if row else None


def _activity_ai_series_summary(series):
    points = [point for point in (series or []) if isinstance(point, dict)]
    if not points:
        return None

    def mean(key, subset):
        values = [float(point[key]) for point in subset
                  if isinstance(point.get(key), (int, float))]
        return round(sum(values) / len(values), 1) if values else None

    middle = max(1, len(points) // 2)
    first, second = points[:middle], points[middle:]
    return {
        'samples': len(points),
        'firstHalf': {key: mean(key, first) for key in ('heartRate', 'pace', 'power')},
        'secondHalf': {key: mean(key, second) for key in ('heartRate', 'pace', 'power')},
    }


def _activity_ai_prompt(activity, planned, feedback=None):
    evidence = {
        'activity': {
            'name': activity.get('name'), 'type': activity.get('type'),
            'date': activity.get('date'), 'source': activity.get('source'),
            'overview': activity.get('overview') or {},
            'laps': (activity.get('laps') or [])[:60],
            'heartRateZones': activity.get('heartRateZones') or [],
            'powerZones': activity.get('powerZones') or [],
            'seriesTrend': _activity_ai_series_summary(activity.get('series')),
            'strengthExercises': activity.get('strengthExercises') or [],
            'exerciseSets': (activity.get('exerciseSets') or [])[:80],
        },
        'plannedSession': planned,
        'athleteFeedback': feedback or None,
    }
    return f'''Analyze one completed workout retrospectively. Be an evidence-driven running and
strength coach. Use only the supplied measurements. Compare against the planned session when one
exists; otherwise assess pacing consistency, heart-rate response, power, elevation, laps, and the
workout's likely purpose without inventing a target. Pace values are seconds per kilometer. Be
direct when execution missed the plan and specific when it went well. Do not diagnose illness or
injury. Write all user-facing text in Swedish.

MEASURED WORKOUT DATA:
{json.dumps(evidence, ensure_ascii=False, default=str)}

Respond ONLY with valid JSON:
{{
  "tone": "good|mixed|warning|neutral",
  "headline": "a concrete assessment, max 9 words",
  "summary": "2-4 concise sentences using the most relevant measured numbers",
  "highlights": ["2-4 short factual observations"],
  "nextStep": "one useful takeaway for the next similar workout"
}}'''


def _normalize_activity_ai_response(payload):
    if not isinstance(payload, dict):
        raise ValueError('AI-svaret är inte ett objekt.')
    tone = str(payload.get('tone') or 'neutral').lower()
    if tone not in {'good', 'mixed', 'warning', 'neutral'}:
        tone = 'neutral'
    headline = str(payload.get('headline') or '').strip()[:160]
    summary = str(payload.get('summary') or '').strip()[:1200]
    next_step = str(payload.get('nextStep') or '').strip()[:500]
    highlights = [str(item).strip()[:300] for item in (payload.get('highlights') or [])
                  if str(item).strip()][:4]
    if not headline or not summary:
        raise ValueError('AI-svaret saknar rubrik eller sammanfattning.')
    return {
        'tone': tone, 'headline': headline, 'summary': summary,
        'highlights': highlights, 'nextStep': next_step,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
    }


# En analys per pass räcker, men två klick i rad (eller två flikar) startade
# tidigare två parallella LLM-anrop som båda drog kvot och båda kunde falla på
# 429. Låset gör att bara den första genererar; resten väntar in cachen.
_ai_overview_locks = {}
_ai_overview_locks_guard = threading.Lock()


def _ai_overview_lock(key):
    with _ai_overview_locks_guard:
        lock = _ai_overview_locks.get(key)
        if lock is None:
            lock = _ai_overview_locks[key] = threading.Lock()
        return lock


@app.post('/api/activities/<int:activity_id>/ai-overview')
def activity_ai_overview(activity_id):
    """Create once, cache permanently, and return an AI review for any owned workout."""
    if activity_id <= 0:
        return _api_error('invalid_activity_id', 'Aktivitets-id är ogiltigt.', 400)
    source = 'strava' if request.args.get('source') == 'strava' else 'garmin'
    cache_key = f'activity-ai:v1:{source}:{activity_id}'
    cached = get_cache(cache_key, uid())
    if cached:
        return jsonify({'overview': cached[0], 'cached': True})
    if not llm_available():
        return _api_error('ai_not_configured', 'AI-analysen är inte konfigurerad.', 503)
    try:
        with _ai_overview_lock(f'{uid()}:{cache_key}'):
            # Kan ha fyllts i medan vi väntade på låset.
            cached = get_cache(cache_key, uid())
            if cached:
                return jsonify({'overview': cached[0], 'cached': True})
            activity = _activity_ai_detail(activity_id, source, uid(), uname())
            if activity is None:
                return _api_error('activity_not_found', 'Aktiviteten hittades inte.', 404)
            activity['source'] = source
            planned = _activity_ai_plan_context(activity, uid())
            feedback = ACTIVITY_FEEDBACK_STORE.get(uid(), source, activity_id)
            text = call_llm(_activity_ai_prompt(activity, planned, feedback), max_tokens=800)
            cleaned = text.strip().replace('```json', '').replace('```', '').strip()
            overview = _normalize_activity_ai_response(json.loads(cleaned))
            set_cache(cache_key, overview, uid())
            return jsonify({'overview': overview, 'cached': False})
    except LLMQuotaError as exc:
        # Väntat och övergående — logga som varning utan stack trace så att
        # riktiga fel fortsätter synas i loggen.
        logger.warning('AI overview hit provider quota', extra={
            'event': 'activity.ai_quota',
            'request_id': _request_id(),
            'activity_id': activity_id,
            'retry_after_s': exc.retry_after,
        })
        return _api_error('ai_quota_exceeded',
                          'AI-kvoten är slut just nu. Försök igen om en stund.', 429,
                          extra={'retryAfter': exc.retry_after})
    except (ValueError, json.JSONDecodeError) as exc:
        return _server_error(exc, 'activity.ai_invalid_response', status=502,
                             code='ai_invalid_response',
                             message='AI-översikten fick ett ogiltigt svar.')
    except Exception as exc:
        return _server_error(exc, 'activity.ai_failed', status=502,
                             code='ai_provider_error',
                             message='AI-översikten kunde inte skapas just nu.')

# ─────────────────────────────────────────────
# WEBBNOTISER (Web Push)
# ─────────────────────────────────────────────
# På iPhone måste sajten läggas till på hemskärmen innan Safari ens tillåter
# att fråga om lov — push från en vanlig flik är blockerat av Apple. Servern
# märker inget av det; den skickar likadant oavsett plattform.
VAPID_PUBLIC_KEY  = config.get('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = config.get('VAPID_PRIVATE_KEY', '')
VAPID_SUBJECT     = config.get('VAPID_SUBJECT', 'mailto:hugo.erixon13@gmail.com')
# En morgonrapport som kommer kl 20 ar inte en morgonrapport.
MORNING_REPORT_HOURS = (
    int(config.get('MORNING_REPORT_FROM', '5')),
    int(config.get('MORNING_REPORT_TO', '11')))


def push_available():
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and webpush is not None)


def _forget_subscription(endpoint):
    """Ta bort en prenumeration som push-tjänsten sagt är död."""
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM push_subscriptions WHERE endpoint=%s', (endpoint,))
            conn.commit()
    except Exception as exc:
        print('push: kunde inte rensa prenumeration:', exc)


def send_push(user_id, title, body, url='/', tag='trainyze'):
    """Skicka en notis till alla enheter en användare registrerat.

    Returnerar antalet enheter som tog emot den. Prenumerationer som svarar
    404/410 är permanent döda — då har appen avinstallerats eller ikonen
    raderats — och rensas bort direkt, annars växer tabellen med skräp."""
    if not push_available():
        return 0
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT endpoint, p256dh, auth FROM push_subscriptions
                    WHERE user_id=%s''', (user_id,))
                rows = cur.fetchall()
    except Exception as exc:
        print('push: kunde inte läsa prenumerationer:', exc)
        return 0

    payload = json.dumps({'title': title, 'body': body, 'url': url, 'tag': tag})
    sent = 0
    for endpoint, p256dh, auth in rows:
        try:
            webpush(
                subscription_info={'endpoint': endpoint,
                                   'keys': {'p256dh': p256dh, 'auth': auth}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={'sub': VAPID_SUBJECT},
                timeout=15)
            sent += 1
            try:
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute('UPDATE push_subscriptions SET last_ok=%s WHERE endpoint=%s',
                                    (time.time(), endpoint))
                    conn.commit()
            except Exception:
                pass
        except WebPushException as exc:
            status = getattr(exc.response, 'status_code', None)
            if status in (404, 410):
                logger.info('Dropping dead push subscription', extra={
                    'event': 'push.subscription_gone', 'user_id': user_id, 'status': status})
                _forget_subscription(endpoint)
            else:
                logger.warning('Push delivery failed', extra={
                    'event': 'push.failed', 'user_id': user_id, 'status': status,
                    'detail': str(exc)[:200]})
        except Exception as exc:
            logger.warning('Push delivery error', extra={
                'event': 'push.error', 'user_id': user_id, 'detail': str(exc)[:200]})
    return sent


@app.get('/api/push/key')
def push_public_key():
    """Publika VAPID-nyckeln som webbläsaren prenumererar med."""
    return jsonify({'key': VAPID_PUBLIC_KEY, 'available': push_available()})


@app.post('/api/push/subscribe')
def push_subscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint')
    keys = data.get('keys') or {}
    p256dh, auth = keys.get('p256dh'), keys.get('auth')
    if not endpoint or not p256dh or not auth:
        return _api_error('invalid_subscription', 'Prenumerationen saknar nycklar.', 400)
    try:
        with db() as conn:
            with conn.cursor() as cur:
                # Samma enhet kan prenumerera om efter en omregistrering; då ska
                # raden bytas ut, och byter användare på enheten ska den följa med.
                cur.execute('''INSERT INTO push_subscriptions
                        (endpoint, user_id, p256dh, auth, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (endpoint) DO UPDATE SET
                        user_id=EXCLUDED.user_id, p256dh=EXCLUDED.p256dh,
                        auth=EXCLUDED.auth''',
                    (endpoint, uid(), p256dh, auth, time.time()))
            conn.commit()
    except Exception as exc:
        return _server_error(exc, 'push.subscribe_failed',
                             message='Kunde inte spara notisinställningen.')
    logger.info('Push subscription stored', extra={
        'event': 'push.subscribed', 'user_id': uid()})
    return jsonify({'ok': True})


@app.post('/api/push/unsubscribe')
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint')
    if not endpoint:
        return _api_error('invalid_subscription', 'Ingen prenumeration angiven.', 400)
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM push_subscriptions WHERE endpoint=%s AND user_id=%s',
                            (endpoint, uid()))
            conn.commit()
    except Exception as exc:
        return _server_error(exc, 'push.unsubscribe_failed',
                             message='Kunde inte ta bort notisinställningen.')
    return jsonify({'ok': True})


@app.get('/api/push/status')
def push_status():
    """Hur många enheter den inloggade användaren har registrerat."""
    count = 0
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM push_subscriptions WHERE user_id=%s', (uid(),))
                count = cur.fetchone()[0]
    except Exception as exc:
        print('push status:', exc)
    return jsonify({'devices': count, 'available': push_available()})


@app.post('/api/push/test')
def push_test():
    """Skicka en testnotis så att uppsättningen går att verifiera på riktigt."""
    if not push_available():
        return _api_error('push_not_configured', 'Notiser är inte konfigurerade på servern.', 503)
    sent = send_push(uid(), 'Trainyze', 'Testnotis — notiser fungerar.', url='/')
    if not sent:
        return _api_error('no_devices',
                          'Ingen enhet tog emot notisen. Aktivera notiser på enheten först.', 404)
    return jsonify({'ok': True, 'devices': sent})


# ─────────────────────────────────────────────
# HRV-LOGIK (Garmin HRV Status + personlig baslinje)
# ─────────────────────────────────────────────
# Garmin returnerar:
#   status: BALANCED / UNBALANCED / LOW / POOR / NONE  (trend över 7-dygns-snitt mot baslinje)
#   baseline: { lowUpper, balancedLow, balancedUpper }  (din personliga balanced-range)
# Primär signal = Garmins status (samma symbol som i Garmin Connect-appen).
# Sekundär finmätare = gårnattens HRV relativt baslinjebandet + råförhållande mot veckosnitt.

HRV_STATUS_LIGHT = {       # status → trafikljus
    'BALANCED':   'green',
    'UNBALANCED': 'amber',
    'LOW':        'red',
    'POOR':       'red',
    'NONE':       None,
}
HRV_STATUS_CAP = {         # status → taklimit för HRV-komponenten i CNS (trendstraff)
    'BALANCED':   100,
    'UNBALANCED':  80,
    'LOW':         60,
    'POOR':        45,
    'NONE':        None,
}
HRV_STATUS_VERDICT = {     # status → kort verdikt
    'BALANCED':   'HRV balanserad — autonoma nervsystemet ligger i ditt normala spann',
    'UNBALANCED': 'HRV i obalans — utanför ditt normala spann, träna med viss försiktighet',
    'LOW':        'HRV låg — under baslinjen, prioritera återhämtning',
    'POOR':       'HRV mycket låg — längre låg trend, vila rekommenderas',
    'NONE':       'Inte tillräckligt med baslinjedata ännu',
}

def hrv_component(last_night, low_upper, balanced_low, status, raw_pct):
    """
    HRV-komponent (0–100) för CNS-scoren.
    Bygger på gårnattens HRV relativt din personliga baslinje (samma som Garmins nattprick),
    med ett tak baserat på Garmins trendstatus. Faller tillbaka på råförhållande om baslinje saknas.
    """
    pos = None
    if last_night and balanced_low and low_upper:
        if last_night >= balanced_low:
            pos = 100.0
        elif last_night >= low_upper:
            span = balanced_low - low_upper
            pos = 70 + 30 * (last_night - low_upper) / span if span else 85.0
        else:
            pos = max(25.0, 70 * last_night / low_upper)
    cap = HRV_STATUS_CAP.get((status or 'NONE').upper())
    if pos is None:
        # Ingen baslinje → använd råförhållande (gammal metod) som fallback
        if raw_pct is None:
            return cap  # kan vara None
        pos = min(raw_pct, 100)
    if cap is None:
        return round(pos)
    return round(min(pos, cap))

def hrv_signal(status, last_night, weekly):
    """
    Returnerar (light, verdict) för trafikljuset.
    Primärt Garmins status; om den saknas faller vi tillbaka på Kiviniemi ±5% mot veckosnitt.
    """
    st = (status or 'NONE').upper()
    light = HRV_STATUS_LIGHT.get(st)
    if light:
        return light, HRV_STATUS_VERDICT.get(st, st.title())
    # Fallback: råförhållande
    if last_night and weekly:
        diff = (last_night - weekly) / weekly * 100
        if diff >= 5:   return 'green', f'HRV +{diff:.0f}% vs weekly avg — train hard'
        if diff <= -5:  return 'red',   f'HRV {diff:.0f}% vs weekly avg — rest or Z2'
        return 'amber', f'HRV {diff:+.0f}% vs weekly avg — normal session'
    return 'amber', 'HRV data unavailable'


def safe_health_fetch(label, default, fetcher):
    try:
        value = fetcher()
        return default if value is None else value
    except Exception as e:
        print(f'Garmin health {label} unavailable: {e}', flush=True)
        return default


def has_health_payload(result):
    return any([
        result.get('readiness', {}).get('score') is not None,
        result.get('hrv', {}).get('lastNightAvg') is not None,
        result.get('restingHR', {}).get('value') is not None,
        result.get('sleep', {}).get('totalSec') is not None,
        result.get('bodyBattery', {}).get('max') is not None,
        result.get('stress', {}).get('avg') is not None,
        result.get('respiration', {}).get('avg') is not None,
        result.get('spo2', {}).get('avg') is not None,
    ])


def has_sleep_levels(result):
    return bool(((result or {}).get('sleep') or {}).get('levels'))


def health_sleep_is_fallback(result):
    """Kommer sömnen i payloaden från en tidigare natt än den den visas som?

    Flaggan sätts på två ställen med olika form: ögonblicksbilden ur databasen
    märker hela payloaden, medan det live-hämtade svaret bara märker sömnblocket
    (`sleep.fallback`) eftersom resten av dagens siffror är färska. Ett enkelt
    `result.get('fallback')` missar därför precis det fall flaggan finns för —
    natten som ännu inte hunnit synka från klockan."""
    if not isinstance(result, dict):
        return False
    return bool(result.get('fallback') or (result.get('sleep') or {}).get('fallback'))


def health_sleep_source_date(result):
    """Vilken natt sömnen i payloaden faktiskt kommer från (ISO-datum)."""
    if not isinstance(result, dict):
        return None
    sleep = result.get('sleep') or {}
    return sleep.get('sourceDate') or result.get('sourceDate') or result.get('date')


def latest_health_snapshot(user_id, display_date):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT date, sleep_score, sleep_hours, deep_pct, rem_pct,
                                  hrv_avg, resting_hr, body_battery, stress_avg
                           FROM health_history
                           WHERE user_id=%s AND (
                               sleep_score IS NOT NULL OR sleep_hours IS NOT NULL OR
                               hrv_avg IS NOT NULL OR resting_hr IS NOT NULL OR
                               body_battery IS NOT NULL OR stress_avg IS NOT NULL
                           )
                           ORDER BY date DESC LIMIT 1''', (user_id,))
            row = cur.fetchone()
    if not row:
        return None

    source_date, sleep_score, sleep_hours, deep_pct, rem_pct, hrv_avg, resting_hr, body_battery, stress_avg = row
    total_sec = round(float(sleep_hours) * 3600) if sleep_hours is not None else None
    return {
        'date': display_date,
        'sourceDate': source_date.isoformat() if hasattr(source_date, 'isoformat') else source_date,
        'fallback': True,
        'readiness': {'score': None, 'level': None, 'feedback': None},
        'hrv': {'lastNightAvg': hrv_avg, 'weeklyAvg': None, 'status': None, 'pct': None,
                'balancedLow': None, 'balancedUpper': None, 'lowUpper': None,
                'component': None, 'light': 'amber', 'verdict': 'Senaste sparade HRV'},
        'restingHR': {'value': resting_hr, 'sevenDayAvg': None, 'min': None},
        'sleep': {'totalSec': total_sec, 'deepSec': None, 'remSec': None, 'score': sleep_score,
                  'deepPct': deep_pct or 0, 'remPct': rem_pct or 0, 'levels': [],
                  'startGMT': None, 'endGMT': None},
        'bodyBattery': {'current': body_battery, 'max': body_battery, 'charged': None, 'drained': None},
        'stress': {'avg': stress_avg, 'max': None},
        'respiration': {'avg': None, 'sleepAvg': None},
        'spo2': {'avg': None, 'min': None},
    }



@app.get('/api/health')
def health_data():
    today = date.today().isoformat()
    row = get_cache('health', uid())
    if row and (time.time() - row[1]) < 10 * 60 and has_health_payload(row[0]) and (
            not health_sleep_is_fallback(row[0]) or has_sleep_levels(row[0])):
        return jsonify(row[0])

    if not _garmin_connected(uname()):
        snapshot = latest_health_snapshot(uid(), today)
        if snapshot:
            snapshot['notConnected'] = True
            return jsonify(snapshot)
        return jsonify({
            'date': today, 'fallback': True, 'notConnected': True,
            'readiness': {'score': None, 'level': None, 'feedback': None},
            'hrv': {'lastNightAvg': None, 'weeklyAvg': None, 'status': None, 'pct': None,
                    'balancedLow': None, 'balancedUpper': None, 'lowUpper': None,
                    'component': None, 'light': 'amber', 'verdict': 'Koppla ditt Garmin-konto'},
            'restingHR': {'value': None, 'sevenDayAvg': None, 'min': None},
            'sleep': {'totalSec': None, 'deepSec': None, 'remSec': None, 'score': None,
                      'deepPct': 0, 'remPct': 0, 'levels': [], 'startGMT': None, 'endGMT': None},
            'bodyBattery': {'current': None, 'max': None, 'charged': None, 'drained': None},
            'stress': {'avg': None, 'max': None},
            'respiration': {'avg': None, 'sleepAvg': None},
            'spo2': {'avg': None, 'min': None},
        })

    try:
        client = get_garmin(uname())
        sleep     = safe_health_fetch('sleep', {}, lambda: client.get_sleep_data(today))
        hrv       = safe_health_fetch('hrv', {}, lambda: client.get_hrv_data(today))
        bb        = safe_health_fetch('body battery', [], lambda: client.get_body_battery(today, today))
        stress    = safe_health_fetch('stress', {}, lambda: client.get_stress_data(today))
        readiness = safe_health_fetch('training readiness', [], lambda: client.get_training_readiness(today))
        hr        = safe_health_fetch('heart rates', {}, lambda: client.get_heart_rates(today))
        resp      = safe_health_fetch('respiration', {}, lambda: client.get_respiration_data(today))
        spo2      = safe_health_fetch('spo2', {}, lambda: client.get_spo2_data(today))
        summary   = safe_health_fetch('daily summary', {}, lambda: client.get_user_summary(today))

        sleep = sleep if isinstance(sleep, dict) else {}
        hrv = hrv if isinstance(hrv, dict) else {}
        bb = bb if isinstance(bb, list) else []
        stress = stress if isinstance(stress, dict) else {}
        readiness = readiness if isinstance(readiness, list) else []
        hr = hr if isinstance(hr, dict) else {}
        resp = resp if isinstance(resp, dict) else {}
        spo2 = spo2 if isinstance(spo2, dict) else {}

        sleep_source_date = today
        if not (sleep.get('sleepLevels') or sleep.get('sleepMovement')):
            previous_day = (date.today() - timedelta(days=1)).isoformat()
            previous_sleep = safe_health_fetch('sleep fallback', {}, lambda: client.get_sleep_data(previous_day))
            if isinstance(previous_sleep, dict) and (previous_sleep.get('sleepLevels') or previous_sleep.get('sleepMovement')):
                sleep = previous_sleep
                sleep_source_date = previous_day

        s = sleep.get('dailySleepDTO', {})
        total_sleep_sec = s.get('sleepTimeSeconds', 0)
        deep_sec  = s.get('deepSleepSeconds', 0)
        rem_sec   = s.get('remSleepSeconds', 0)
        sleep_scores = s.get('sleepScores') or {}
        sleep_score_val = sleep_scores.get('overall', {}).get('value') if isinstance(sleep_scores, dict) else None

        hrv_sum  = hrv.get('hrvSummary', {})
        hrv_base = hrv_sum.get('baseline') or {}
        hrv_ln   = hrv_sum.get('lastNightAvg')
        hrv_wk   = hrv_sum.get('weeklyAvg')
        hrv_st   = hrv_sum.get('status')
        hrv_pct  = round((hrv_ln / hrv_wk) * 100) if hrv_wk and hrv_ln else None
        hrv_comp = hrv_component(hrv_ln, hrv_base.get('lowUpper'), hrv_base.get('balancedLow'), hrv_st, hrv_pct)
        hrv_lt, hrv_verdict = hrv_signal(hrv_st, hrv_ln, hrv_wk)

        bb_today = bb[0] if bb and isinstance(bb[0], dict) else {}
        bb_vals  = bb_today.get('bodyBatteryValuesArray') or []
        bb_points = [v[1] for v in bb_vals if v and len(v) > 1 and v[1] is not None]
        bb_now   = bb_points[-1] if bb_points else None
        bb_max   = max(bb_points, default=None)

        ready    = readiness[0] if readiness and isinstance(readiness[0], dict) else {}
        avg_resp = resp.get('avgWakingRespirationValue') or resp.get('avgRespirationValue')
        sleep_resp = resp.get('avgSleepRespirationValue')
        avg_spo2 = spo2.get('averageSpO2')
        if avg_spo2: avg_spo2 = round(avg_spo2)

        result = {
            'date': today,
            'readiness':   {'score': ready.get('score'), 'level': ready.get('level'), 'feedback': ready.get('feedbackShort')},
            'hrv':         {'lastNightAvg': hrv_ln, 'weeklyAvg': hrv_wk, 'status': hrv_st, 'pct': hrv_pct,
                            'balancedLow': hrv_base.get('balancedLow'), 'balancedUpper': hrv_base.get('balancedUpper'),
                            'lowUpper': hrv_base.get('lowUpper'), 'component': hrv_comp,
                            'light': hrv_lt, 'verdict': hrv_verdict},
            'restingHR':   {'value': hr.get('restingHeartRate'), 'sevenDayAvg': hr.get('lastSevenDaysAvgRestingHeartRate'), 'min': hr.get('minHeartRate')},
            'sleep':       {'totalSec': total_sleep_sec, 'deepSec': deep_sec, 'remSec': rem_sec, 'score': sleep_score_val,
                            'deepPct': round(deep_sec/total_sleep_sec*100) if total_sleep_sec else 0,
                            'remPct':  round(rem_sec/total_sleep_sec*100)  if total_sleep_sec else 0,
                            'lightPct': round((s.get('lightSleepSeconds') or 0)/total_sleep_sec*100) if total_sleep_sec else 0,
                            'awakePct': round((s.get('awakeSleepSeconds') or 0)/total_sleep_sec*100) if total_sleep_sec else 0,
                            'levels': (sleep.get('sleepLevels') or sleep.get('sleepMovement') or []),
                            'sourceDate': sleep_source_date,
                            'fallback': sleep_source_date != today,
                            'startGMT': s.get('sleepStartTimestampGMT'),
                            'endGMT':   s.get('sleepEndTimestampGMT')},
            'bodyBattery': {'current': bb_now, 'max': bb_max, 'charged': bb_today.get('charged'), 'drained': bb_today.get('drained')},
            'stress':      {'avg': stress.get('avgStressLevel'), 'max': stress.get('maxStressLevel')},
            'respiration': {'avg': round(avg_resp) if avg_resp else None, 'sleepAvg': round(sleep_resp) if sleep_resp else None},
            'spo2':        {'avg': avg_spo2, 'min': spo2.get('lowestSpO2')},
            'daily':       _daily_activity(summary),
        }
        result['restOrTrain'] = compute_bevel_rest_or_train(result)
        has_payload = has_health_payload(result)
        if not has_payload:
            snapshot = latest_health_snapshot(uid(), today)
            if snapshot:
                result = snapshot
                result['restOrTrain'] = compute_bevel_rest_or_train(result)
                has_payload = True
        if has_payload:
            set_cache('health', result, uid())

        # Spara även till health_history så Analysis-fliken får dagens data direkt.
        # En natt som fallit tillbaka på gårdagen får aldrig skrivas under dagens
        # datum — då står gårdagens sömn som i natt tills den riktiga natten
        # synkar, och allt som läser historiken (morgonrapport, dagens analys)
        # bygger sitt omdöme på fel natt.
        try:
            if not has_payload or health_sleep_is_fallback(result):
                return jsonify(result)
            sl = result['sleep']
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute('''INSERT INTO health_history
                        (date, sleep_score, sleep_hours, deep_pct, rem_pct, hrv_avg, resting_hr, body_battery, stress_avg, created_at, user_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (date, user_id) DO UPDATE SET
                            sleep_score=EXCLUDED.sleep_score, sleep_hours=EXCLUDED.sleep_hours,
                            deep_pct=EXCLUDED.deep_pct, rem_pct=EXCLUDED.rem_pct,
                            hrv_avg=EXCLUDED.hrv_avg, resting_hr=EXCLUDED.resting_hr,
                            body_battery=EXCLUDED.body_battery, stress_avg=EXCLUDED.stress_avg''',
                        (today, sl.get('score'),
                         round(sl.get('totalSec', 0) / 3600, 2) if sl.get('totalSec') else None,
                         sl.get('deepPct'), sl.get('remPct'),
                         result['hrv'].get('lastNightAvg'),
                         result['restingHR'].get('value'),
                         result['bodyBattery'].get('max'),
                         result['stress'].get('avg'),
                         time.time(), uid()))
                conn.commit()
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        return _server_error(e, 'health.load_failed', message='Hälsodatan kunde inte hämtas.')


@app.get('/api/health/spark')
def health_spark():
    """Senaste 7 dagarnas värden för hem-sidans mini-grafer (HRV, Strain, Sömn, RHR)."""
    user_id = uid()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT sleep_score, hrv_avg, resting_hr, date
                FROM health_history WHERE user_id=%s ORDER BY date DESC LIMIT 7''', (user_id,))
            rows = cur.fetchall()[::-1]  # äldst först

    # Hämta 7-dagars faktisk strain från aktiviteter
    today = date.today()
    activities = _recent_activities(user_id, days=14)
    chronic, _ = _load_context(user_id)
    ref = strain_analysis.reference_load(activities, today=today, chronic=chronic)
    series = strain_analysis.strain_series(activities, today=today, days=7, reference=ref)
    strain_vals = [int(round(pt.get('strain', 0))) for pt in series]

    return jsonify({
        'sleep': [r[0] for r in rows if r[0] is not None],
        'hrv':   [r[1] for r in rows if r[1] is not None],
        'rhr':   [r[2] for r in rows if r[2] is not None],
        'strain': strain_vals,
    })

@app.get('/api/health/stress-history')
def health_stress_history():
    days = max(7, min(90, int(request.args.get('days', 30))))
    start = (date.today() - timedelta(days=days)).isoformat()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT date, stress_avg
                FROM health_history
                WHERE user_id=%s AND date >= %s AND stress_avg IS NOT NULL
                ORDER BY date
            ''', (uid(), start))
            rows = cur.fetchall()
    values = [{'date': r[0], 'value': r[1]} for r in rows]
    nums = [v['value'] for v in values if v['value'] is not None]
    avg = round(sum(nums) / len(nums), 1) if nums else None
    return jsonify({'days': days, 'avg': avg, 'values': values})


def _local_sleep_stamp(dto, key):
    """Garmins lokala sömntider kommer som millisekunder — spara som 'YYYY-MM-DD HH:MM'."""
    value = dto.get(key)
    if not value:
        return None
    try:
        seconds = float(value) / 1000.0 if float(value) > 100000000000 else float(value)
        # Tidsstämpeln är redan lokal tid uttryckt som epok, så den ska läsas som UTC.
        return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError, OSError):
        return None


def _daily_activity(summary):
    """Steg, kalorier och rörelse för dagen ur Garmins dygnssammanfattning.

    Kalorierna delas upp: aktiva kalorier är det träningen kostat, medan
    totalen även innehåller basalomsättningen — det är den uppdelningen som
    säger något, inte totalsiffran.
    """
    summary = summary if isinstance(summary, dict) else {}

    def num(key):
        value = summary.get(key)
        try:
            return round(float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    steps, goal = num('totalSteps'), num('dailyStepGoal')
    moderate, vigorous = num('moderateIntensityMinutes'), num('vigorousIntensityMinutes')
    # Garmin räknar hård intensitet dubbelt mot veckomålet.
    intensity = (moderate or 0) + 2 * (vigorous or 0) if (moderate or vigorous) else None

    return {
        'steps': steps,
        'stepGoal': goal,
        'stepPct': round(steps / goal * 100) if steps and goal else None,
        'distanceM': num('totalDistanceMeters'),
        'caloriesTotal': num('totalKilocalories'),
        'caloriesActive': num('activeKilocalories'),
        'caloriesBmr': num('bmrKilocalories'),
        'floors': num('floorsAscended'),
        'floorGoal': num('userFloorsAscendedGoal'),
        'intensityMinutes': intensity,
        'intensityGoal': num('intensityMinutesGoal'),
    }


def _fetch_day_health(client, day_str):
    sleep = client.get_sleep_data(day_str) or {}
    s = sleep.get('dailySleepDTO', {}) or {}
    total = s.get('sleepTimeSeconds') or 0
    deep  = s.get('deepSleepSeconds') or 0
    rem   = s.get('remSleepSeconds') or 0
    scores = s.get('sleepScores') or {}
    sleep_score = (scores.get('overall', {}) or {}).get('value') if isinstance(scores, dict) else None
    hrv = client.get_hrv_data(day_str) or {}
    hrv_avg = (hrv.get('hrvSummary') or {}).get('lastNightAvg')
    rhr = None
    try:
        rhr = (client.get_heart_rates(day_str) or {}).get('restingHeartRate')
    except Exception:
        pass
    stress_avg = None
    try:
        stress_avg = (client.get_stress_data(day_str) or {}).get('avgStressLevel')
    except Exception:
        pass
    bb_max = None
    try:
        bb = client.get_body_battery(day_str, day_str) or []
        vals = (bb[0].get('bodyBatteryValuesArray') if bb else []) or []
        bb_max = max((v[1] for v in vals if v and v[1] is not None), default=None)
    except Exception:
        pass
    light = s.get('lightSleepSeconds') or 0
    awake = s.get('awakeSleepSeconds') or 0
    return {'date': day_str, 'sleep_score': sleep_score,
            'sleep_hours': round(total / 3600, 2) if total else None,
            'deep_pct': round(deep / total * 100) if total else None,
            'rem_pct':  round(rem / total * 100)  if total else None,
            'light_pct': round(light / total * 100) if total else None,
            'awake_pct': round(awake / total * 100) if total else None,
            'sleep_start': _local_sleep_stamp(s, 'sleepStartTimestampLocal'),
            'sleep_end': _local_sleep_stamp(s, 'sleepEndTimestampLocal'),
            'hrv_avg': hrv_avg, 'resting_hr': rhr, 'body_battery': bb_max,
            'stress_avg': stress_avg}


def collect_health_history(days=14, username=None):
    """Backfillar saknade dagar i health_history från Garmin (idempotent)."""
    if username is None:
        username = list(USERS.keys())[0] if USERS else 'hugo'
    user_id = USERS.get(username, {}).get('id', 1)
    try:
        client = get_garmin(username)
    except Exception as e:
        print('health-history: garmin-fel', e)
        return
    today = date.today()
    with db() as conn:
        with conn.cursor() as cur:
            # Treat a day as "have" only when newer history columns are filled too,
            # so older sparse rows get re-fetched once and backfilled.
            # Nätter som saknar de nya tidskolumnerna hämtas om en gång. Nätter
            # helt utan sömndata undantas — där finns inget att fylla i.
            cur.execute('''SELECT date FROM health_history
                WHERE user_id=%s AND body_battery IS NOT NULL AND stress_avg IS NOT NULL
                  AND (sleep_start IS NOT NULL OR sleep_hours IS NULL)''', (user_id,))
            have = {r[0] for r in cur.fetchall()}
    added = 0
    for i in range(1, days + 1):
        d = (today - timedelta(days=i)).isoformat()
        if d in have:
            continue
        try:
            rec = _fetch_day_health(client, d)
        except Exception as e:
            print(f'health-history {d} fel:', e)
            continue
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO health_history
                    (date, sleep_score, sleep_hours, deep_pct, rem_pct, light_pct, awake_pct,
                     sleep_start, sleep_end, hrv_avg, resting_hr, body_battery, stress_avg, created_at, user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (date, user_id) DO UPDATE SET sleep_score=EXCLUDED.sleep_score,
                        sleep_hours=EXCLUDED.sleep_hours, deep_pct=EXCLUDED.deep_pct,
                        rem_pct=EXCLUDED.rem_pct, light_pct=EXCLUDED.light_pct,
                        awake_pct=EXCLUDED.awake_pct, sleep_start=EXCLUDED.sleep_start,
                        sleep_end=EXCLUDED.sleep_end, hrv_avg=EXCLUDED.hrv_avg,
                        resting_hr=EXCLUDED.resting_hr,
                        body_battery=EXCLUDED.body_battery, stress_avg=EXCLUDED.stress_avg''',
                    (rec['date'], rec['sleep_score'], rec['sleep_hours'], rec['deep_pct'],
                     rec['rem_pct'], rec.get('light_pct'), rec.get('awake_pct'),
                     rec.get('sleep_start'), rec.get('sleep_end'),
                     rec['hrv_avg'], rec['resting_hr'], rec['body_battery'],
                     rec['stress_avg'], time.time(), user_id))
            conn.commit()
        added += 1
    print(f'health-history: {added} nya dagar tillagda')


# --- Fitness-mätare (VO2max, uthållighet, mjölksyratröskel, HRV-status) historik ---
def _find_num(obj, keys, depth=0):
    """Sök rekursivt efter första numeriska värdet under någon av nyckelnamnen (case-insensitive substr)."""
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(kk in kl for kk in keys) and isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
        for v in obj.values():
            r = _find_num(v, keys, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_num(v, keys, depth + 1)
            if r is not None:
                return r
    return None


def _fetch_day_metrics(client, day_str):
    """Hämtar fitness-mätare för en dag. Varje mätare är skyddad — saknas metoden
    (t.ex. get_lactate_threshold på garminconnect 0.3.2) hoppas den bara över."""
    vo2max = endurance = lt_hr = lt_pace = hrv_status = None
    try:
        mm = client.get_max_metrics(day_str)
        vo2max = _find_num(mm, ['vo2maxprecise', 'vo2maxvalue', 'vo2max'])
    except Exception:
        pass
    try:
        # Single-day call gives precise daily values. Passing enddate switches Garmin
        # to weekly aggregation, which can hide points in the Analysis tab.
        es = client.get_endurance_score(day_str)
        endurance = _find_num(es, ['overallscore', 'enduranceScore'.lower(), 'avg', 'gauge'])
    except Exception:
        pass
    try:
        if hasattr(client, 'get_lactate_threshold'):
            # latest=True ger den aktuella tröskeln. Daglig aggregering returnerar tomma
            # listor ({"speed": [], "heart_rate": []}) eftersom LT bara uppdateras då och då.
            lt = client.get_lactate_threshold(latest=True, start_date=day_str, end_date=day_str)
            lt_hr = _find_num(lt, ['heartrate', 'lactatethresholdheartrate'])
            speed = _find_num(lt, ['speed', 'lactatethresholdspeed'])  # m/s
            # Garmin ger löp-LT-farten 10x för liten (0.42 m/s istället för 4.22). En riktig
            # löptröskelfart ligger aldrig under ~1.5 m/s, så skala upp i så fall.
            if speed and 0 < speed < 1.5:
                speed *= 10
            if speed and speed > 0:
                lt_pace = round(1000.0 / speed, 1)  # sek/km
    except Exception:
        pass
    try:
        hrv = client.get_hrv_data(day_str) or {}
        hrv_status = (hrv.get('hrvSummary') or {}).get('status')
    except Exception:
        pass
    return {'date': day_str, 'vo2max': vo2max, 'endurance_score': int(endurance) if endurance is not None else None,
            'lactate_hr': int(lt_hr) if lt_hr is not None else None, 'lactate_pace': lt_pace,
            'hrv_status': hrv_status}


def collect_metric_history(days=45, username=None):
    """Backfillar fitness-mätare i metric_history (idempotent). Tål saknade metoder."""
    if username is None:
        username = list(USERS.keys())[0] if USERS else 'hugo'
    user_id = USERS.get(username, {}).get('id', 1)
    try:
        client = get_garmin(username)
    except Exception as e:
        print('metric-history: garmin-fel', e)
        return
    today = date.today()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT date, vo2max, endurance_score, lactate_hr, lactate_pace, hrv_status, created_at
                FROM metric_history WHERE user_id=%s''', (user_id,))
            have = {r[0]: r[1:] for r in cur.fetchall()}
    added = 0
    for i in range(0, days + 1):
        d = (today - timedelta(days=i)).isoformat()
        existing = have.get(d)
        # Revisit sparse rows created by older collectors. HRV status alone is not
        # enough for the Analysis tab's fitness trend cards.
        checked_at = existing[5] if existing else None
        recently_checked = checked_at and (time.time() - checked_at) < 20 * 3600
        if existing and any(v is not None for v in existing[:4]) and recently_checked:
            continue
        if existing and all(v is not None for v in existing[:4]):
            continue
        try:
            rec = _fetch_day_metrics(client, d)
        except Exception as e:
            print(f'metric-history {d} fel:', e)
            continue
        # Hoppa över helt tomma dagar (ingen mätare alls) så vi inte fyller tabellen med null-rader
        if not any(rec[k] is not None for k in ('vo2max', 'endurance_score', 'lactate_hr', 'lactate_pace', 'hrv_status')):
            continue
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO metric_history
                    (date, vo2max, endurance_score, lactate_hr, lactate_pace, hrv_status, created_at, user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (date, user_id) DO UPDATE SET vo2max=EXCLUDED.vo2max,
                        endurance_score=EXCLUDED.endurance_score, lactate_hr=EXCLUDED.lactate_hr,
                        lactate_pace=EXCLUDED.lactate_pace, hrv_status=EXCLUDED.hrv_status''',
                    (rec['date'], rec['vo2max'], rec['endurance_score'], rec['lactate_hr'],
                     rec['lactate_pace'], rec['hrv_status'], time.time(), user_id))
            conn.commit()
        added += 1
    print(f'metric-history: {added} nya dagar tillagda')


@app.get('/api/analysis')
def analysis():
    """A decision-ready training picture, derived from the user's own history."""
    try:
        window = max(30, min(120, int(request.args.get('days', 60) or 60)))
    except (TypeError, ValueError):
        return _api_error('invalid_window', 'Analysperioden måste vara ett antal dagar.', 400)

    today = date.today()
    start_date = today - timedelta(days=window)
    activity_start = min(start_date - timedelta(days=6), today - timedelta(weeks=8))
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute('''SELECT date, hrv_avg, resting_hr, sleep_score
                    FROM health_history WHERE date >= %s AND user_id=%s ORDER BY date''',
                            (start_date.isoformat(), uid()))
                health_rows = [dict(row) for row in cur.fetchall()]
                cur.execute('''SELECT date, vo2max, endurance_score, lactate_hr,
                        lactate_pace, hrv_status
                    FROM metric_history WHERE date >= %s AND user_id=%s ORDER BY date''',
                            (start_date.isoformat(), uid()))
                metric_rows = [dict(row) for row in cur.fetchall()]
                cur.execute('''SELECT date, type, distance, raw
                    FROM activities WHERE date >= %s AND user_id=%s ORDER BY date''',
                            (activity_start.isoformat(), uid()))
                activities = [dict(row) for row in cur.fetchall()]
                cur.execute('''SELECT week, dow, status, type, km, execution
                    FROM plan_sessions WHERE user_id=%s''', (uid(),))
                plan_rows = [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        return _server_error(exc, 'analysis.history_failed', message='Analysunderlaget kunde inte hämtas.')

    daily_load = {}
    for activity in activities:
        day = str(activity.get('date') or '')[:10]
        raw = activity.get('raw') or {}
        try:
            load = float(raw.get('activityTrainingLoad') or 0)
        except (TypeError, ValueError):
            load = 0
        if day and load > 0:
            daily_load[day] = daily_load.get(day, 0) + load
    load_series = []
    for offset in range(window + 1):
        day = start_date + timedelta(days=offset)
        rolling = sum(daily_load.get((day - timedelta(days=back)).isoformat(), 0)
                      for back in range(7))
        if rolling > 0 or load_series:
            load_series.append({'t': day.isoformat(), 'v': round(rolling, 1)})

    metrics = training_analysis.build_metrics(health_rows, metric_rows, load_series)
    volume = training_analysis.weekly_volume(activities, today=today)

    dated_plan = []
    for row in plan_rows:
        try:
            session_day = _plan_session_date(row, today)
        except (TypeError, ValueError):
            continue
        if start_date <= session_day < today:
            row['date'] = session_day.isoformat()
            dated_plan.append(row)
    execution = training_analysis.execution_summary(dated_plan)

    goal_record = get_user_goal(uid())
    pace = _pace_context(uid())
    goal = {
        'title': (goal_record or {}).get('goal_title'),
        'deadline': (goal_record or {}).get('goal_deadline'),
        'daysLeft': None,
        'anchor': pace.get('anchor'),
        'goalPace': pace.get('goalPace'),
        'feasibility': pace.get('goalFeasibility'),
        'bands': pace.get('bands'),
    }
    if goal['deadline']:
        try:
            goal['daysLeft'] = (date.fromisoformat(goal['deadline']) - today).days
        except ValueError:
            pass

    latest_status = next((row.get('hrv_status') for row in reversed(metric_rows)
                          if row.get('hrv_status')), None)
    return jsonify({
        'windowDays': window,
        'generatedAt': datetime.now(LOCAL_TZ).isoformat(),
        'dataCoverage': {'healthDays': len(health_rows), 'metricDays': len(metric_rows)},
        'hrvStatus': latest_status,
        'overview': training_analysis.overview(metrics, volume, execution, goal),
        'volume': volume,
        'execution': execution,
        'goal': goal,
        'metrics': metrics,
    })


@app.get('/api/training-load')
def training_load():
    row = get_cache('training_load', uid())
    if row and (time.time() - row[1]) < 30 * 60:
        return jsonify(row[0])
    if not _garmin_connected(uname()):
        return jsonify({
            'notConnected': True,
            'acute': None, 'chronic': None, 'ratio': None,
            'acwrStatus': None, 'statusPhrase': '',
            'monthlyAerobicLow': 0, 'monthlyAerobicHigh': 0, 'monthlyAnaerobic': 0,
            'aerobicLowMin': None, 'aerobicLowMax': None,
            'aerobicHighMin': None, 'aerobicHighMax': None,
            'anaerobicMin': None, 'anaerobicMax': None,
            'loadBalanceFeedback': None,
        })
    try:
        client = get_garmin(uname())
        today  = date.today().isoformat()
        # Garmin ger None för konton utan träningsstatus ännu (nykopplade/klockor utan load-stöd)
        status = client.get_training_status(today) or {}

        # Plocka ut data från primär enhet
        dev_map  = (status.get('mostRecentTrainingStatus') or {}).get('latestTrainingStatusData') or {}
        dev      = next(iter(dev_map.values()), {}) if dev_map else {}
        acwr_dto = dev.get('acuteTrainingLoadDTO', {})

        acute   = acwr_dto.get('dailyTrainingLoadAcute')
        chronic = acwr_dto.get('dailyTrainingLoadChronic')
        ratio   = acwr_dto.get('dailyAcuteChronicWorkloadRatio')
        status_phrase = dev.get('trainingStatusFeedbackPhrase', '')

        # Belastningsbalans per månad
        lb_map  = (status.get('mostRecentTrainingLoadBalance') or {}).get('metricsTrainingLoadBalanceDTOMap') or {}
        lb      = next(iter(lb_map.values()), {}) if lb_map else {}

        result = {
            'acute':   round(acute)   if acute   is not None else None,
            'chronic': round(chronic) if chronic is not None else None,
            'ratio':   round(ratio, 2) if ratio  is not None else None,
            'acwrStatus':   acwr_dto.get('acwrStatus'),
            'statusPhrase': status_phrase,
            'monthlyAerobicLow':  round(lb.get('monthlyLoadAerobicLow',  0)),
            'monthlyAerobicHigh': round(lb.get('monthlyLoadAerobicHigh', 0)),
            'monthlyAnaerobic':   round(lb.get('monthlyLoadAnaerobic',   0)),
            'aerobicLowMin':  lb.get('monthlyLoadAerobicLowTargetMin'),
            'aerobicLowMax':  lb.get('monthlyLoadAerobicLowTargetMax'),
            'aerobicHighMin': lb.get('monthlyLoadAerobicHighTargetMin'),
            'aerobicHighMax': lb.get('monthlyLoadAerobicHighTargetMax'),
            'anaerobicMin':   lb.get('monthlyLoadAnaerobicTargetMin'),
            'anaerobicMax':   lb.get('monthlyLoadAnaerobicTargetMax'),
            'loadBalanceFeedback': lb.get('trainingBalanceFeedbackPhrase'),
        }
        set_cache('training_load', result, uid())
        return jsonify(result)
    except Exception as e:
        return _server_error(e, 'training_load.load_failed', message='Träningsbelastningen kunde inte hämtas.')

@app.post('/api/sync')
def sync():
    if not _garmin_connected(uname()):
        return _api_error('garmin_not_connected',
                          'Koppla ditt Garmin-konto först — klicka på "Ej kopplad" längst ner i menyn.', 400)
    try:
        n = run_sync(username=uname(), user_id=uid())
        return jsonify({'ok': True, 'count': n})
    except Exception as e:
        return _server_error(e, 'sync.failed', message='Garmin-synkningen misslyckades.')

def _get_iso_week(d):
    """Returnera ISO-veckonummer för ett date-objekt."""
    return d.isocalendar()[1]

def _recent_execution_block(user_id, days=14, limit=6):
    """Rendera hur de senaste passen faktiskt genomfördes, för AI-prompterna.

    Utan det här ser modellen bara att ett pass blev av — inte om tempot,
    pulsen eller vikterna låg där planen bad om.
    """
    today = date.today()
    weeks = {_get_iso_week(today - timedelta(days=offset)) for offset in range(0, days + 1)}
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute('''SELECT id, week, dow, title, type, km, detail, execution
                    FROM plan_sessions
                    WHERE user_id = %s AND status = 'completed'
                      AND execution IS NOT NULL AND week = ANY(%s)
                    ORDER BY week DESC, dow DESC LIMIT %s''',
                    (user_id, list(weeks), limit))
                rows = cur.fetchall()
    except Exception as e:
        print('Kunde inte läsa passutvärderingar:', e)
        return ''

    blocks = []
    for row in rows:
        execution = row['execution'] or {}
        label = row['title'] or row['type'] or 'session'
        # Datumet måste med — annars kan modellen inte svara på "i förrgår".
        try:
            session_day = _plan_session_date(row).strftime('%A %Y-%m-%d')
        except Exception:
            session_day = f"week {row['week']} day {row['dow']}"
        if execution.get('discipline') == 'strength':
            body = session_analysis.describe_strength(execution)
            header = f"{session_day} — {label} (strength, planned: {row['detail'] or '—'})"
        else:
            body = session_analysis.describe_run(execution, name=label)
            header = f"{session_day} — planned: {row['detail'] or '—'}"
        if not body:
            continue
        blocks.append(f"- {header}\n{body}")

    execution_text = '' if not blocks else (
        "\n\nHOW RECENT SESSIONS WERE ACTUALLY EXECUTED "
        "(measured against what the plan asked for — use this to give specific "
        "feedback such as running easy days too fast, fading across reps, or "
        "lifting below the calculated target, instead of generic praise):\n"
        + '\n'.join(blocks)
    )
    return execution_text + _recent_activity_feedback_block(user_id, days=days, limit=limit)


def _recent_activity_feedback(user_id, days=21, limit=10):
    cutoff = time.time() - days * 86400
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute('''SELECT f.activity_id,f.source,f.data,f.updated_at,
                                      a.name,a.date,a.type
                    FROM activity_feedback f
                    LEFT JOIN activities a ON f.source='garmin'
                        AND a.id=f.activity_id AND a.user_id=f.user_id
                    WHERE f.user_id=%s AND f.updated_at >= %s
                    ORDER BY COALESCE(a.date, '') DESC, f.updated_at DESC LIMIT %s''',
                    (user_id, cutoff, limit))
                rows = [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        print('Kunde inte läsa passkänsla:', exc)
        return []
    today = date.today()
    for row in rows:
        row['data'] = dict(row.get('data') or {})
        try:
            row['age_days'] = (today - date.fromisoformat(str(row.get('date') or '')[:10])).days
        except (TypeError, ValueError):
            row['age_days'] = None
        row['activity_id'] = int(row['activity_id'])
        row['updated_at'] = float(row['updated_at'])
    return rows


def _recent_activity_feedback_block(user_id, days=21, limit=10):
    rows = _recent_activity_feedback(user_id, days=days, limit=limit)
    if not rows:
        return ''
    meal_labels = {'none': 'ingen mat', 'light': 'lätt mål', 'normal': 'normal måltid', 'heavy': 'stor måltid'}
    hydration_labels = {'low': 'för lite', 'okay': 'okej', 'good': 'bra'}
    lines = []
    for row in rows:
        feedback = row['data']
        parts = []
        if feedback.get('feeling') is not None:
            parts.append(f"känsla {feedback['feeling']}/5")
        if feedback.get('effort') is not None:
            parts.append(f"ansträngning {feedback['effort']}/10")
        if feedback.get('meal_before'):
            parts.append('mat före: ' + meal_labels.get(feedback['meal_before'], feedback['meal_before']))
        if feedback.get('hydration'):
            parts.append('vätska: ' + hydration_labels.get(feedback['hydration'], feedback['hydration']))
        if feedback.get('notes'):
            parts.append('anteckning: ' + feedback['notes'])
        if parts:
            lines.append(f"- {str(row.get('date') or '')[:10]} {row.get('name') or 'pass'}: " + ', '.join(parts))
    if not lines:
        return ''
    return ('\n\nATHLETE POST-WORKOUT FEEDBACK (self-reported; use it to explain patterns '
            'and future decisions, but do not invent causation):\n' + '\n'.join(lines))


def _build_refresh_prompt(acts):
    """Bygg en fullständig prompt för startsidans AI-rekommendation."""
    today     = date.today()
    iso_week  = _get_iso_week(today)
    weekday   = today.weekday()  # 0=mån

    # Plan- och veckovolym härleds från användarens egen plan i databasen
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COALESCE(SUM(km),0) FROM plan_sessions WHERE user_id=%s AND week=%s',
                        (uid(), iso_week))
            planned_km = float(cur.fetchone()[0] or 0)
            cur.execute('SELECT COUNT(*), MIN(week), MAX(week) FROM plan_sessions WHERE user_id=%s', (uid(),))
            plan_count, plan_first_week, plan_last_week = cur.fetchone()
    if plan_count:
        plan_line = (f"Training plan: W{plan_first_week}–W{plan_last_week} "
                     f"· This week (W{iso_week}): {planned_km:.0f} km planned")
    else:
        plan_line = ("Training plan: none set up yet — base guidance on recovery, "
                     "recent training load and the athlete's goal")

    # Senaste löppass med load-data
    recent_runs = [
        {'name': a.get('activityName'), 'date': a.get('startTimeLocal'),
         'distance': f"{a.get('distance',0)/1000:.1f} km",
         'duration': f"{int(a.get('duration',0)/60)} min",
         'avgHR': a.get('averageHR'),
         'trainingEffect': a.get('trainingEffectLabel'),
         'load': round(a.get('activityTrainingLoad', 0) or 0)}
        for a in acts if 'running' in (a.get('activityType', {}).get('typeKey') or '')
    ][:5]

    # Genomförd km + load denna vecka
    monday = today - timedelta(days=weekday)
    completed_km   = 0.0
    completed_load = 0.0
    for a in acts:
        raw_date = a.get('startTimeLocal') or ''
        try:
            act_date = datetime.fromisoformat(raw_date[:10]).date()
        except Exception:
            continue
        if act_date >= monday:
            completed_km   += (a.get('distance') or 0) / 1000
            completed_load += (a.get('activityTrainingLoad') or 0)

    remaining_km = max(0, planned_km - completed_km)

    # Training load (ACWR) från cache
    tl_row = get_cache('training_load', uid())
    tl     = tl_row[0] if tl_row else {}
    acute   = tl.get('acute')
    chronic = tl.get('chronic')
    ratio   = tl.get('ratio')
    acwr_status = tl.get('acwrStatus', '')
    load_feedback = tl.get('loadBalanceFeedback', '')

    # Hälsodata från cache
    h_row = get_cache('health', uid())
    h     = h_row[0] if h_row else {}
    readiness    = (h.get('readiness') or {}).get('score')
    hrv_obj      = h.get('hrv') or {}
    hrv_avg      = hrv_obj.get('lastNightAvg')
    hrv_weekly   = hrv_obj.get('weeklyAvg')
    hrv_status   = hrv_obj.get('status')
    hrv_bal_low  = hrv_obj.get('balancedLow')
    hrv_bal_high = hrv_obj.get('balancedUpper')
    hrv_comp     = hrv_obj.get('component')
    body_battery = (h.get('bodyBattery') or {}).get('current')
    sleep_score  = (h.get('sleep') or {}).get('score')

    # Google Calendar — kommande 7 dagar
    cal_row = get_cache('gcal_events', uid())
    gcal_lines = []
    early_days  = []
    if cal_row:
        for ev in (cal_row[0] or []):
            start_str = ev.get('start', '')
            if not start_str:
                continue
            try:
                ev_dt   = datetime.fromisoformat(start_str[:16])
                ev_date = ev_dt.date()
            except Exception:
                continue
            if today <= ev_date <= today + timedelta(days=14):
                day_name = ev_dt.strftime('%A') + ' ' + str(ev_dt.day) + ' ' + ev_dt.strftime('%b')
                time_str = ev_dt.strftime('%H:%M') if 'T' in start_str else 'all day'
                desc = _plain_calendar_text(ev.get('desc', ''))
                desc_str = f" — description: {desc}" if desc else ''
                signals = _calendar_description_signals(ev)
                signal_str = f" — training impact: {'; '.join(signals)}" if signals else ''
                gcal_lines.append(f"- {day_name}: {ev.get('title','')} ({time_str}){desc_str}{signal_str}")
                if ev_dt.hour < 7:
                    early_days.append(day_name)

    # Bygg prompten
    # Hämta dagens och nästa planerade pass från DB
    today_session = None
    next_session  = None
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM plan_sessions
                WHERE week=%s AND dow=%s AND status='planned' AND user_id=%s
                LIMIT 1""", (iso_week, weekday, uid()))
            today_session = cur.fetchone()
            cur.execute("""SELECT * FROM plan_sessions
                WHERE status='planned' AND (week > %s OR (week = %s AND dow > %s)) AND user_id=%s
                ORDER BY week, dow LIMIT 1""", (iso_week, iso_week, weekday, uid()))
            next_session = cur.fetchone()

    if today_session:
        today_km = today_session.get('km') or 0
        today_session_str = (
            f"{today_session['title']} — {today_session['detail']}"
            + (f" — {today_km:.0f} km" if today_km and str(int(today_km)) not in today_session['title'] else "")
        )
        today_km_note = f"Session distance from plan: {today_km:.0f} km — use THIS number for the session, NOT the weekly remaining km."
    else:
        today_session_str = "Rest day (no session scheduled)"
        today_km_note = ""
    next_session_str  = f"{next_session['title']} — {next_session['detail']}"   if next_session  else "No upcoming session found"

    pace_ctx = _pace_context(uid())

    prompt = f"""You are a personal training coach. Analyze ALL data below and respond ONLY with JSON. All text fields in the JSON must be written in Swedish (svenska).

{_goal_prompt_block(uid())}
{plan_line}

TODAY'S SCHEDULED SESSION (from training plan):
{today_session_str}
{today_km_note}

NEXT SCHEDULED SESSION:
{next_session_str}

RECENT RUNS:
{json.dumps(recent_runs, ensure_ascii=False, indent=2)}
{_recent_execution_block(uid())}

MEASURED PACE CAPABILITY (anchor every pace you mention to this):
{pace_progression.describe_anchor(pace_ctx['anchor'], pace_ctx['goalFeasibility'])}

WEEK STATUS W{iso_week}:
- {f'Planned: {planned_km:.0f} km · Completed: {completed_km:.1f} km · Remaining: {remaining_km:.1f} km' if plan_count else f'No plan — Completed: {completed_km:.1f} km'}
- Training load this week: {round(completed_load)}

HEALTH DATA (today):
- Training readiness: {readiness or '—'}/100
- Garmin HRV Status: {hrv_status or 'NONE'} (this is Garmin's trend assessment vs your personal baseline)
- HRV last night: {hrv_avg or '—'} ms · your balanced baseline range: {hrv_bal_low or '—'}–{hrv_bal_high or '—'} ms · weekly avg: {hrv_weekly or '—'} ms
- Body battery: {body_battery or '—'}/100
- Sleep score: {sleep_score or '—'}/100"""

    # CNS-score beräkning (Flatt & Esco 2016) — HRV-komponenten bygger nu på Garmins baslinje
    if all(v is not None for v in [readiness, hrv_avg, hrv_weekly, sleep_score, h.get('stress',{}).get('avg')]):
        # Primärt: baslinje-baserad HRV-komponent. Fallback: råförhållande mot veckosnitt.
        hrv_pct_val = round((hrv_avg / hrv_weekly) * 100) if hrv_weekly else 50
        hrv_score   = hrv_comp if hrv_comp is not None else min(hrv_pct_val, 100)
        stress_avg  = h.get('stress', {}).get('avg', 50) or 50
        cns = round(0.40 * hrv_score + 0.30 * (sleep_score or 50) + 0.20 * (readiness or 50) + 0.10 * (100 - min(stress_avg,100)))
        st = (hrv_status or 'NONE').upper()
        hrv_signal_str = {'BALANCED':'GREEN (balanced — train as planned)',
                          'UNBALANCED':'YELLOW (HRV i obalans — caution)',
                          'LOW':'RED (low — recover)',
                          'POOR':'RED (poor — rest)'}.get(st)
        if not hrv_signal_str:
            hrv_diff = ((hrv_avg - hrv_weekly) / hrv_weekly * 100) if hrv_weekly else 0
            hrv_signal_str = 'GREEN (go hard)' if hrv_diff >= 5 else 'RED (rest/Z2)' if hrv_diff <= -5 else 'YELLOW (normal session)'
        cns_rule = 'QUALITY SESSION OK' if cns >= 70 else 'NORMAL/EASY SESSION' if cns >= 45 else 'REST OR Z2 — mandatory'
        deep_pct = h.get('sleep', {}).get('deepPct', 0) or 0
        rem_pct  = h.get('sleep', {}).get('remPct', 0) or 0
        sleep_flags = []
        if deep_pct < 10: sleep_flags.append('low deep sleep (skip strength)')
        if rem_pct < 15:  sleep_flags.append('low REM (avoid intervals)')
        prompt += f"""

CNS SCORE: {cns}/100 — {cns_rule}
HRV SIGNAL (Garmin Status): {hrv_signal_str}
SLEEP QUALITY: deep sleep {deep_pct}% (goal 15–25%) · REM {rem_pct}% (goal 20–25%){(' · WARNING: ' + ', '.join(sleep_flags)) if sleep_flags else ' · OK'}
SESSION RULE: CNS ≥70 → quality session ok · CNS 45–69 → normal/easy · CNS <45 → rest/Z2 mandatory"""

    if acute is not None:
        load_feedback_en = {
            'AEROBIC_LOW_SHORTAGE':  'too little low-intensity aerobic training',
            'AEROBIC_HIGH_SHORTAGE': 'too little high-intensity aerobic training',
            'ANAEROBIC_SHORTAGE':    'too little anaerobic training',
            'OPTIMAL':               'optimal balance',
        }.get(load_feedback, load_feedback)
        acwr_en = {'LOW':'low','OPTIMAL':'optimal','HIGH':'high','VERY_HIGH':'very high'}.get(acwr_status, acwr_status)
        prompt += f"""

TRAINING LOAD (ACWR):
- Acute load (7 days): {acute} · Chronic load (28 days): {chronic}
- ACWR ratio: {ratio} ({acwr_en}) — optimal zone is 0.8–1.3
- Load balance: {load_feedback_en}
RULE: If ACWR < 0.8 you can carefully increase intensity. If > 1.3, prioritize rest or Z2."""

    if gcal_lines:
        prompt += f"""

CALENDAR (next 7 days):
{chr(10).join(gcal_lines)}
Factor this into the recommendation. Calendar descriptions are user-provided context: use the "training impact" notes to avoid hard sessions around travel, poor sleep, stress, illness, or late nights."""

    if early_days:
        prompt += f"\nEarly starts (before 07:00, likely reduced sleep): {', '.join(early_days)} — avoid quality sessions on these days and the day after."

    prompt += """

Respond ONLY with this JSON (no explanation outside JSON):
{
  "todayRecommendation": "1-2 sentence recommendation for today that references the scheduled session above — confirm it, modify it, or replace it based on the health data",
  "todayType": "easy|quality|rest",
  "nextSession": {"title": "session name", "desc": "description", "tempo": "e.g. 3:35 /km", "distance": "e.g. ~8 km"},
  "prediction3k": "e.g. 10:15",
  "insight": "one concrete insight — prefer a specific observation from HOW RECENT SESSIONS WERE ACTUALLY EXECUTED (e.g. easy runs consistently run too fast, reps fading, lifting under target weight) over a generic training-load remark"
}"""
    return prompt

@app.post('/api/refresh')
def refresh():
    row = get_cache('analysis', uid())
    if row and (time.time() - row[1]) < 60 * 60:
        return jsonify(row[0])

    try:
        client = get_garmin(uname())
        acts = client.get_activities(0, 10)
        ingest_activities(acts, uid())
    except Exception as e:
        return _server_error(e, 'analysis.garmin_failed', message='Garmin-datan kunde inte hämtas.')

    if not llm_available():
        return jsonify({'todayRecommendation': 'Lägg till en AI-nyckel (GEMINI_API_KEY) i .env.',
                        'todayType': 'easy',
                        'nextSession': {'title': 'Easy jog', 'desc': 'Z2, 30-40 min', 'tempo': '4:45-5:15 /km', 'distance': '~6 km'},
                        'prediction3k': '10:27', 'insight': 'AI-insikter kräver en API-nyckel.'})

    prompt = _build_refresh_prompt(acts)
    text = call_llm(prompt, max_tokens=600).strip().replace('```json','').replace('```','').strip()
    analysis = json.loads(text)
    set_cache('analysis', analysis, uid())
    return jsonify(analysis)

# ─────────────────────────────────────────────
# AI-ANALYS AV SENASTE PASSEN (planerat vs faktiskt)
# ─────────────────────────────────────────────
# Bumpas när svarsformatet ändras så att gamla cachade analyser inte
# renderas mot ett schema de aldrig kände till.
REVIEW_SCHEMA_VERSION = 3


def _recovery_prompt_block(user_id):
    """Sömn, CNS, belastning och strain — det en coach faktiskt väger passet mot.

    Utan det här bedömdes dagens pass blint: samma omdöme oavsett om atleten
    sovit åtta timmar eller fyra. Varje del är inslagen för sig eftersom en
    saknad datakälla aldrig får sänka hela analysen."""
    lines, cns, chronic = [], None, None
    try:
        cns, sleep_h, sleep_date = _recent_recovery(user_id)
        stale_night = bool(sleep_h) and sleep_date != date.today().isoformat()
        if cns is not None:
            lines.append(f'CNS readiness: {cns}/100'
                         + (f' (computed from the night of {sleep_date})' if stale_night else ''))
        if sleep_h and not stale_night:
            lines.append(f'Sleep last night: {sleep_h} h')
        elif sleep_h:
            # Att kalla gårdagens natt för "i natt" gav omdömen som "med bara
            # 3,9 timmars sömn i natt" på en natt atleten faktiskt sov ut.
            lines.append(
                f'Sleep on the night of {sleep_date}: {sleep_h} h — LAST NIGHT HAS NOT '
                'SYNCED FROM THE WATCH YET. Do not describe this as last night, and do '
                'not build the recommendation on it as if it were tonight\'s recovery.')
    except Exception as exc:
        print('review recovery block:', exc)

    try:
        health = latest_health_snapshot(user_id, date.today().isoformat()) or {}
        rhr = (health.get('restingHR') or {}).get('value')
        hrv = (health.get('hrv') or {}).get('lastNightAvg')
        if rhr:
            lines.append(f'Resting HR: {rhr} bpm')
        if hrv:
            lines.append(f'HRV last night: {hrv} ms')
    except Exception as exc:
        print('review health block:', exc)

    try:
        chronic, ratio = _load_context(user_id)
        if chronic:
            lines.append(f'Chronic training load: {round(chronic)}')
        if ratio:
            lines.append(f'Acute:chronic ratio: {ratio:.2f} '
                         '(above 1.3 = ramping up fast, below 0.8 = detraining)')
    except Exception as exc:
        print('review load block:', exc)

    try:
        summary = strain_analysis.strain_summary(
            _recent_activities(user_id, days=30), readiness=cns, chronic=chronic)
        lines.append(f"Strain today: {summary['strain']}/100 "
                     f"(7-day average {summary['weekAvgStrain']})")
        if summary.get('headline'):
            lines.append(f"Strain verdict: {summary['headline']} — {summary.get('detail', '')}")
    except Exception as exc:
        print('review strain block:', exc)

    if not lines:
        return 'RECOVERY & LOAD:\nNo recovery data available — do not speculate about it.'
    return 'RECOVERY & LOAD:\n' + '\n'.join(lines)


def _week_prompt_block(user_id, today):
    """Veckan så här långt: vad som gjorts, vad som missats, hur mycket.

    Ett pass går inte att bedöma isolerat — samma lugna pass är rätt beslut
    efter tre hårda dagar och fel beslut när veckans kvalitet ligger orörd."""
    week, dow = _iso_week_dow(today)
    monday = today - timedelta(days=dow)
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    'SELECT dow, type, km, title, status FROM plan_sessions '
                    'WHERE week=%s AND user_id=%s ORDER BY dow', (week, user_id))
                planned = cur.fetchall()
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT date, type, distance FROM activities '
                    'WHERE date >= %s AND user_id=%s ORDER BY date',
                    (monday.isoformat(), user_id))
                done = cur.fetchall()
    except Exception as exc:
        print('review week block:', exc)
        return ''

    names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    lines = []
    for session in planned:
        when = 'today' if session['dow'] == dow else (
            'past' if session['dow'] < dow else 'upcoming')
        km = f" {session['km']:.0f} km" if session.get('km') else ''
        lines.append(f"  {names[session['dow']]}: {session['title']}{km} "
                     f"[{session.get('status') or 'planned'}, {when}]")

    done_km = sum((row[2] or 0) for row in done) / 1000
    planned_km = sum((s.get('km') or 0) for s in planned)
    summary = [f'Logged so far this week: {len(done)} sessions, {done_km:.1f} km']
    if planned_km:
        summary.append(f'Week plan totals {planned_km:.0f} km')

    out = f'THIS WEEK (ISO week {week}):'
    if lines:
        out += '\n' + '\n'.join(lines)
    return out + '\n' + '\n'.join(summary)


def _notes_prompt_block(user_id, limit=6):
    """Atletens egna anteckningar — skador och känsla som mätdatan inte visar."""
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT text, category FROM user_notes '
                    'WHERE user_id=%s ORDER BY created_at DESC LIMIT %s', (user_id, limit))
                rows = cur.fetchall()
    except Exception as exc:
        print('review notes block:', exc)
        return ''
    if not rows:
        return ''
    listed = '\n'.join(f'  - [{row[1] or "note"}] {row[0]}' for row in rows)
    return ('ATHLETE NOTES (most recent first — injuries and how they felt; '
            'weigh these above the numbers when they conflict):\n' + listed)


def _build_review_prompt():
    """Prompt för AI-koll på DAGENS pass: planerat vs gjort, med tidsmedvetenhet."""
    now   = datetime.now()
    today = now.date()
    wk, dw = _iso_week_dow(today)

    # Refresh Garmin before judging today's workout, so this card uses the
    # latest activity/lap data rather than stale DB rows.
    try:
        client = get_garmin(uname())
        ingest_activities(client.get_activities(0, 20), uid())
    except Exception as e:
        print('training review: Garmin refresh failed', e)

    # Dagens planerade pass + dagens faktiska aktiviteter
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT * FROM plan_sessions WHERE week=%s AND dow=%s AND user_id=%s', (wk, dw, uid()))
            planned = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('''SELECT id, name, type, distance, duration, avg_hr
                FROM activities WHERE date >= %s AND user_id=%s ORDER BY date''', (today.isoformat(), uid()))
            act_rows = cur.fetchall()

    planned_str = '; '.join(f"{p['title']} — {p['detail']}" for p in planned) if planned \
                  else 'Rest day (no session scheduled)'

    INTERVAL_TYPES = {'track_running', 'interval_training', 'track'}

    def _fmt_pace(speed_ms):
        """Convert m/s to mm:ss/km string."""
        if not speed_ms or speed_ms <= 0:
            return None
        pace = 1000 / speed_ms / 60  # min/km
        return f"{int(pace)}:{int((pace % 1) * 60):02d}/km"

    def _fetch_laps(activity_id):
        """Return work-interval laps for an activity, filtering out rest laps."""
        try:
            client = get_garmin(uname())
            splits = client.get_activity_splits(activity_id)
            laps = splits.get('lapDTOs') or splits.get('laps') or []
            if not laps:
                return []
            # Compute pace for each lap
            lap_data = []
            for idx, lap in enumerate(laps):
                spd = lap.get('averageSpeed') or lap.get('avgSpeed')
                dist = lap.get('distance') or 0
                dur  = lap.get('duration') or lap.get('elapsedDuration') or 0
                hr   = lap.get('averageHR') or lap.get('avgHR')
                if dist < 50:   # skip sub-50 m auto-laps / pauses
                    continue
                lap_data.append({'idx': idx, 'dist': dist, 'dur': dur, 'speed': spd, 'hr': hr})
            if not lap_data:
                return []
            four_hundreds = [
                l for l in lap_data
                if 300 <= (l.get('dist') or 0) <= 550
                and (l.get('dur') or 0) <= 150
                and (l.get('speed') or 0) > 0
            ]
            if len(four_hundreds) >= 4:
                return sorted(four_hundreds, key=lambda l: l['idx'])

            # Identify work laps by the largest speed gap between reps and rests.
            speeds = sorted([l['speed'] for l in lap_data if l['speed']], reverse=True)
            if not speeds:
                return lap_data  # no speed data — return all
            best_gap = None
            for i in range(len(speeds) - 1):
                if speeds[i + 1] <= 0:
                    continue
                ratio = speeds[i] / speeds[i + 1]
                if ratio >= 1.15 and (best_gap is None or ratio > best_gap[0]):
                    best_gap = (ratio, i)
            if best_gap is not None:
                threshold = speeds[best_gap[1] + 1] * best_gap[0] ** 0.5
                work = [l for l in lap_data if l['speed'] and l['speed'] >= threshold]
                if len(work) >= 2:
                    return sorted(work, key=lambda l: l['idx'])

            threshold = speeds[max(0, len(speeds) // 2 - 1)]  # conservative fallback
            return sorted([l for l in lap_data if l['speed'] and l['speed'] >= threshold], key=lambda l: l['idx'])
        except Exception:
            return []

    acts = []
    lap_notes = []
    for act_id, name, typ, dist, dur, hr in act_rows:
        is_interval = (typ or '').lower() in INTERVAL_TYPES or \
                      any(w in (name or '').lower() for w in ('interval', 'track', 'fartlek', 'repeat'))
        parts = [typ or 'activity']
        if dist: parts.append(f"{dist/1000:.1f} km")
        if dur:  parts.append(f"{int(dur/60)} min")
        if dist and dur and dist > 0:
            pace = (dur / 60) / (dist / 1000)
            pace_note = ' (avg incl. rest)' if is_interval else ''
            parts.append(f"pace {int(pace)}:{int((pace % 1) * 60):02d}/km{pace_note}")
        if hr: parts.append(f"avgHR {hr}")
        acts.append(f"{name or 'Activity'} ({', '.join(parts)})")

        if is_interval and act_id:
            work_laps = _fetch_laps(act_id)
            if work_laps:
                lap_lines = []
                for i, l in enumerate(work_laps, 1):
                    p = _fmt_pace(l['speed'])
                    d = f"{l['dist']:.0f} m"
                    h_str = f", HR {l['hr']}" if l['hr'] else ''
                    lap_lines.append(f"  Rep {i}: {d} @ {p or '?'}{h_str}")
                lap_notes.append(
                    f"INTERVAL REPS for '{name or 'track activity'}' "
                    f"(verified from Garmin laps: {len(work_laps)} work reps, rest excluded):\n" + '\n'.join(lap_lines)
                )

    acts_str = '; '.join(acts) if acts else 'nothing logged yet today'
    if lap_notes:
        acts_str += '\n\n' + '\n\n'.join(lap_notes)
        acts_str += ('\n\nNOTE: Use the rep paces above (not the average pace) when evaluating '
                     'interval performance against the target pace in the plan. The rep count above '
                     'is verified from Garmin laps; do not invent or round it.')

    # Mät dagens pass mot planens måltempo så bedömningen blir konkret
    # ("4% snabbare än Z2-bandet") i stället för ett allmänt beröm.
    execution_block = ''
    main_run = None
    for row in act_rows:
        if 'running' in (row[2] or '').lower() and (row[3] or 0) > (main_run[3] if main_run else 0):
            main_run = row
    if main_run and planned:
        act_id, name, typ, dist, dur, hr = main_run
        activity = {'activityId': act_id, 'activityName': name,
                    'activityType': {'typeKey': typ}, 'distance': dist,
                    'duration': dur, 'averageHR': hr}
        target_session = next((p for p in planned if p['type'] in ('run', 'easy', 'race')), planned[0])
        try:
            kind = session_analysis.classify_session(target_session, activity)
            laps = _run_activity_laps(activity, uname()) if kind in ('interval', 'long', 'threshold') else []
            analysis = session_analysis.analyze_run(
                activity, laps, target_session, lactate_hr=_latest_lactate_hr(uid()))
            described = session_analysis.describe_run(analysis, name=name or 'today')
            if described:
                execution_block = (
                    "\n\nEXECUTION VS PLAN (measured — pace deltas are negative when faster "
                    "than target):\n" + described)
        except Exception as e:
            print('training review: execution analysis failed', e)

    # Dagens kalender (jobb/åtaganden) så "har du tid" blir smart
    cal_row = get_cache('gcal_events', uid())
    today_events = []
    if cal_row:
        for ev in (cal_row[0] or []):
            s = ev.get('start', '')
            if s[:10] != today.isoformat():
                continue
            t  = s[11:16] if 'T' in s else 'all day'
            e  = ev.get('end', '')
            te = e[11:16] if 'T' in e else ''
            today_events.append(f"{ev.get('title','')} ({t}{'–' + te if te else ''})")
    events_str = '; '.join(today_events) if today_events else 'nothing on the calendar'

    recovery_str = _recovery_prompt_block(uid())
    week_str = _week_prompt_block(uid(), today)
    notes_str = _notes_prompt_block(uid())
    optional_blocks = '\n\n'.join(b for b in (week_str, notes_str) if b)
    if optional_blocks:
        optional_blocks = '\n\n' + optional_blocks

    return f"""You are a personal running coach. Judge TODAY's session for this athlete.

{_goal_prompt_block(uid())}
Current date & time: {now.strftime('%A %d %b, %H:%M')}

TODAY'S PLANNED SESSION:
{planned_str}

ACTIVITIES LOGGED TODAY (from Garmin):
{acts_str}{execution_block}

{recovery_str}

TODAY'S CALENDAR (work / commitments):
{events_str}{optional_blocks}

How to use the context above:
- Recovery decides how hard to push, not whether the session "counts". Poor sleep, a
  suppressed HRV or a high acute:chronic ratio turns "you went too easy" into "backing off
  was the right call" — say that explicitly rather than judging pace in isolation.
- Read today against the week. A missed quality session earlier in the week changes what
  today should have been; three hard days in a row make an easy day correct.
- Never invent numbers. If a value is missing above, do not guess it or mention it.
- If the athlete has logged an injury or a complaint, say something about it — an ache that
  goes unmentioned reads as an ache that went unnoticed. Where a note contradicts the
  numbers, trust the note and say so.
- "next" must name an actual session: distance and pace or effort. "Rest well and be ready"
  is not an answer. If the plan above already says what comes next, use that.

Decide which single case applies and write accordingly:
- DONE: an activity matching the planned session was completed today. Say specifically HOW it was executed, not just that it happened — use the EXECUTION VS PLAN numbers above. If an easy day was run faster than its target band, say so plainly and explain the cost (it steals from the week's quality sessions). If reps came in under target pace, faded towards the end, or the session was cut short, name it. Only give plain praise when the numbers actually match the plan. For interval/track sessions, use the individual REP PACES (not the average pace).
- PENDING: the session has not been done yet. Use the current time AND the calendar to judge if there is still time today — if so, reassure ("you still have time, fit it in before/after work"); if it's late evening with no window left, gently note the day is nearly over.
- OTHER: the athlete did something different than planned today — acknowledge it.
- REST: it's a rest day — confirm that resting is the right call.

Respond ONLY with this JSON (all text in Swedish / svenska):
{{
  "status": "done | pending | missed | rest | other",
  "headline": "max 6 words",
  "body": "1-3 short, friendly sentences specific to today.",
  "assessment": "2-4 sentences. Explain WHY, citing the numbers you used — pace vs target, recovery, where the week stands. This is where the reasoning goes.",
  "adjust": "One sentence on what to change, or null if nothing needs changing. Do not invent a problem to fill this in.",
  "next": "One sentence on what the next session should be, given today and the week."
}}

Calibration — match this tone and this level of specificity:

Example (session run too fast on an easy day, athlete slept badly):
{{
  "status": "done",
  "headline": "För snabbt på ett lugnt pass",
  "body": "Du sprang 8 km i 4:45/km när planen sa 5:30-5:50. Passet blev bra, men det låg i fel zon.",
  "assessment": "Måltempot för dagen var 5:30-5:50/km och du låg 45 sekunder snabbare per kilometer. Med 5,5 timmars sömn och HRV under din baslinje blir ett för hårt lugnt pass dyrare än vanligt. Du har tröskelpasset på torsdag, och det är där farten hör hemma.",
  "adjust": "Håll de lugna passen under 5:30/km resten av veckan.",
  "next": "Imorgon: 6 km riktigt lugnt, 5:45/km eller långsammare."
}}

Example (planned session not done yet, still time):
{{
  "status": "pending",
  "headline": "Passet väntar fortfarande",
  "body": "Dagens 10 km är inte gjort än, men kvällen är fri enligt kalendern.",
  "assessment": "Du har inget inbokat efter 17:00 och beredskapen ligger på 78 av 100, så kroppen är redo. Veckan ligger på 24 km av planerade 45, vilket gör det här passet viktigt för att inte tappa volym.",
  "adjust": null,
  "next": "Kör dagens 10 km i kväll, sedan vilodag imorgon."
}}"""

@app.get('/api/training-review')
def training_review():
    force = request.args.get('force') == '1'
    row = get_cache('training_review', uid())
    # Två skilda frågor: är cachen värd att servera rakt av, och duger den som
    # nödutgång? Bara den första kräver aktuellt schema. En äldre analys saknar
    # de nya fälten men säger fortfarande något sant om dagens pass, och
    # gränssnittet döljer det som saknas — den slår ett felmeddelande.
    cached = row[0] if row and row[0].get('_review_version') else None
    current = cached if cached and cached['_review_version'] == REVIEW_SCHEMA_VERSION else None
    if current and not force and (time.time() - row[1]) < 30 * 60:
        return jsonify(current)
    if not llm_available():
        return jsonify({'status': 'pending', 'headline': 'AI-nyckel krävs',
                        'body': 'Lägg till en AI-nyckel (GEMINI_API_KEY) i .env för dagens passkoll.'})
    try:
        prompt = _build_review_prompt()
        # Svaret rymmer nu motivering, justering och nasta pass, sa 500 tokens
        # racker inte langre - ett avklippt svar blir ogiltig JSON.
        text = call_llm(prompt, max_tokens=1200).strip().replace('```json','').replace('```','').strip()
        review = json.loads(text)
        review['_review_version'] = REVIEW_SCHEMA_VERSION
        set_cache('training_review', review, uid())
        return jsonify(review)
    except Exception as e:
        # En utgången analys är långt bättre än ett felmeddelande: innehållet
        # gäller fortfarande dagens pass. Servera den och märk den som gammal
        # så att gränssnittet kan vara ärligt om åldern.
        if cached:
            stale = dict(cached)
            stale['_stale'] = True
            stale['_stale_age_min'] = int((time.time() - row[1]) / 60)
            logger.warning('Serving stale training review', extra={
                'event': 'training_review.stale_fallback',
                'age_min': stale['_stale_age_min'],
                'reason': type(e).__name__})
            return jsonify(stale)
        return _server_error(e, 'training_review.failed', message='Träningsanalysen kunde inte skapas.')

def _build_insights_prompt():
    today = date.today()
    start = (today - timedelta(days=21)).isoformat()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT date, sleep_score, sleep_hours, deep_pct, rem_pct, hrv_avg, resting_hr
                FROM health_history WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            hh = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('''SELECT date, type, distance FROM activities WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            acts = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('SELECT text, category FROM user_notes WHERE user_id=%s ORDER BY created_at DESC LIMIT 25', (uid(),))
            notes = cur.fetchall()

    acts_by_day = {}
    for d, typ, dist in acts:
        key = (d or '')[:10]
        label = (typ or 'activity') + (f" {dist/1000:.1f}km" if dist else '')
        acts_by_day.setdefault(key, []).append(label)

    cal_row = get_cache('gcal_events', uid())
    cal_days = {}
    if cal_row:
        for ev in (cal_row[0] or []):
            s = ev.get('start', '')
            key = s[:10]
            if not key:
                continue
            title = ev.get('title', 'event')
            early = ('T' in s and s[11:13].isdigit() and int(s[11:13]) < 7)
            prefix = 'early ' if early else ''
            cal_days.setdefault(key, []).append(f"{prefix}{title}")

    lines = []
    for d, ss, sh, dp, rp, hv, rhr in hh:
        key = d[:10]
        tr = ', '.join(acts_by_day.get(key, [])) or 'rest/none'
        cal_str = '; '.join(cal_days.get(key, [])) or '-'
        lines.append(f"{key}: sleep {ss if ss is not None else '-'} ({sh if sh is not None else '-'}h, "
                     f"deep {dp if dp is not None else '-'}%, REM {rp if rp is not None else '-'}%), "
                     f"HRV {hv if hv is not None else '-'}, RHR {rhr if rhr is not None else '-'} | "
                     f"training: {tr} | calendar: {cal_str}")
    log = '\n'.join(lines) if lines else 'No history collected yet.'
    notes_txt = '\n'.join(f"- [{c}] {t}" for t, c in notes) if notes else 'None'

    temp_note = ''
    stats = _bedroom_temp_stats(24)
    if stats:
        avg_c, min_c, max_c = stats
        temp_note = (f"\nBEDROOM TEMP (last 24h): avg {avg_c:.1f}°C, "
                     f"range {min_c:.1f}-{max_c:.1f}°C (longer history builds over time).")

    return f"""You are a brutal, data-driven performance analyst like WHOOP. 3 weeks of data below. Surface the 3-4 most important patterns — ONLY what the numbers support.

{_goal_prompt_block(uid())}

DATA (date: sleep score, hours, deep%, REM%, HRV, RHR | training | calendar):
{log}
{temp_note}

NOTES: {notes_txt}

Rules:
- title: max 4 words, punchy
- value: the key number (e.g. "−8 ms HRV", "+45 min sleep", "RHR 52→58")
- detail: exactly ONE sentence, max 12 words, cite the actual number
- action: max 5 words, starts with a verb
- icon: one emoji that fits the category (sleep=😴, HRV=💙, training=🏃, fatigue=⚠️, trend=📈, calendar=📅, temp=🌡️)
- color: "green", "amber", or "red" based on whether this is positive/neutral/negative

Write ALL text fields (headline, title, value, detail, action) in Swedish (svenska).
Respond ONLY with this JSON:
{{
  "headline": "max 5 words",
  "status": "good | watch | caution",
  "insights": [
    {{"icon": "emoji", "title": "max 4 words", "value": "short metric", "detail": "one sentence max 12 words", "action": "max 5 words", "color": "green|amber|red"}}
  ]
}}
3-4 insights, most impactful first. Only patterns the data clearly supports."""


@app.get('/api/insights')
def insights():
    force = request.args.get('force') == '1'
    try:
        row = get_cache('insights', uid())
        if row and not force and (time.time() - row[1]) < 12 * 3600:
            return jsonify(row[0])
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM health_history WHERE user_id=%s', (uid(),))
                n = cur.fetchone()[0]
    except Exception as e:
        return _server_error(e, 'insights.database_failed', message='Underlaget för insikter kunde inte hämtas.')

    if n < 3:
        return jsonify({'status': 'watch', 'headline': 'Gathering your data…',
                        'insights': [{'title': 'Building history',
                                      'detail': f'Collected {n} day(s) so far. Insights sharpen as more sleep/HRV/training history accumulates.',
                                      'action': 'Check back soon — history backfills automatically.'}]})
    if not llm_available():
        return jsonify({'status': 'watch', 'headline': 'AI-nyckel krävs',
                        'insights': [{'title': 'Ingen API-nyckel', 'detail': 'Lägg till GEMINI_API_KEY i .env för AI-insikter.', 'action': ''}]})
    try:
        prompt = _build_insights_prompt()
        text = call_llm(prompt, max_tokens=2000).strip().replace('```json', '').replace('```', '').strip()
        data = json.loads(text)
        set_cache('insights', data, uid())
        return jsonify(data)
    except Exception as e:
        return _server_error(e, 'insights.generation_failed', message='Insikterna kunde inte skapas.')

def _build_sleep_insights_prompt():
    today = date.today()
    start = (today - timedelta(days=28)).isoformat()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT date, sleep_score, sleep_hours, deep_pct, rem_pct, hrv_avg, resting_hr
                FROM health_history WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            hh = cur.fetchall()
        with conn.cursor() as cur:
            cur.execute('''SELECT date, type, distance FROM activities WHERE date >= %s AND user_id=%s ORDER BY date''', (start, uid()))
            acts = cur.fetchall()

    acts_by_day = {}
    for d, typ, dist in acts:
        key = (d or '')[:10]
        label = (typ or 'activity') + (f' {dist/1000:.1f}km' if dist else '')
        acts_by_day.setdefault(key, []).append(label)

    cal_row = get_cache('gcal_events', uid())
    cal_by_day = {}
    if cal_row:
        for ev in (cal_row[0] or []):
            s = ev.get('start', '')
            key = s[:10]
            if not key: continue
            title = ev.get('title', 'event')
            early = 'T' in s and s[11:13].isdigit() and int(s[11:13]) < 7
            cal_by_day.setdefault(key, []).append(('early ' if early else '') + title)

    lines = []
    for d, ss, sh, dp, rp, hv, rhr in hh:
        key = d[:10]
        tr  = ', '.join(acts_by_day.get(key, [])) or 'rest'
        cal = '; '.join(cal_by_day.get(key, [])) or '-'
        lines.append(f"{key}: score={ss} hours={sh} deep={dp}% REM={rp}% HRV={hv} RHR={rhr} | training: {tr} | calendar: {cal}")
    log = '\n'.join(lines) if lines else 'No history yet.'

    temp_note = ''
    daily_temps = _bedroom_temp_daily(7)
    if daily_temps:
        temp_lines = [f"{day}: avg {avg_c}°C" for day, avg_c in daily_temps]
        temp_note = '\nBEDROOM TEMPERATURE (last 7 nights):\n' + '\n'.join(temp_lines)

    return f"""You are a blunt sleep coach. Analyze 4 weeks of sleep data. Find the 3-4 most important patterns — only what numbers actually show. Write all output (headline, title, value, detail, action) in Swedish (svenska).

DATA (date: sleep score, hours, deep%, REM%, HRV, RHR | training | calendar):
{log}
{temp_note}

Rules:
- title: max 4 words, punchy (e.g. "Late REM kicks in", "Work kills deep sleep")
- value: the key number (e.g. "avg 6h 40m", "deep 12%", "wake 07:15")
- detail: ONE sentence, max 12 words, cite actual numbers or dates
- action: max 5 words, starts with a verb, specific to tonight/this week
- icon: one emoji (😴=sleep duration, 🔵=deep sleep, 🟣=REM, ⏰=wake time, 🌡️=temp, 🏃=training effect, 📅=schedule)
- color: "green" if positive pattern, "amber" if watch, "red" if problem

Write ALL text fields (headline, title, value, detail, action) in Swedish (svenska).
Respond ONLY with this JSON:
{{
  "headline": "max 5 words, describes their sleep pattern",
  "status": "good | watch | caution",
  "insights": [
    {{"icon": "emoji", "title": "max 4 words", "value": "short metric", "detail": "one sentence max 12 words", "action": "max 5 words", "color": "green|amber|red"}}
  ]
}}
3-4 insights, most impactful first."""


def _get_sleep_insights(force=False):
    try:
        row = get_cache('sleep_insights', uid())
        if row and not force and (time.time() - row[1]) < 12 * 3600:
            # Rå dict, aldrig ett färdigt svar: chatten bygger in analysen i sin
            # prompt med json.dumps och en Response går inte att serialisera.
            return row[0]
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM health_history WHERE user_id=%s', (uid(),))
                n = cur.fetchone()[0]
    except Exception as e:
        raise RuntimeError('Sömnunderlaget kunde inte hämtas.') from e

    if n < 5:
        return {'status': 'watch', 'headline': 'Samlar sömndata…',
                        'insights': [{'title': 'Need more history',
                                      'detail': f'Have {n} night(s) so far — need at least 5 to find patterns.',
                                      'action': 'Återkom om några dagar.'}]}
    if not llm_available():
        return {'status': 'watch', 'headline': 'AI-nyckel krävs',
                        'insights': [{'title': 'Ingen API-nyckel', 'detail': 'Lägg till GEMINI_API_KEY i .env.', 'action': ''}]}
    try:
        prompt = _build_sleep_insights_prompt()
        text = call_llm(prompt, max_tokens=2000).strip().replace('```json', '').replace('```', '').strip()
        data = json.loads(text)
        set_cache('sleep_insights', data, uid())
        return data
    except Exception as e:
        raise RuntimeError('Sömnanalysen kunde inte skapas.') from e


def _parse_calendar_dt(value):
    if not value:
        return None
    try:
        if 'T' not in value:
            return datetime.fromisoformat(value).replace(tzinfo=LOCAL_TZ)
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return None


def _fmt_clock(dt):
    return dt.strftime('%H:%M')


def _event_kind(title):
    t = (title or '').lower()
    work_words = ('work', 'jobb', 'jobba', 'meeting', 'möte', 'shift', 'pass', 'office')
    travel_words = ('flight', 'flyg', 'train', 'tåg', 'airport', 'resa', 'travel')
    if any(w in t for w in travel_words):
        return 'travel'
    if any(w in t for w in work_words):
        return 'work'
    return 'calendar'


@app.get('/api/sleep')
def sleep_overview():
    """Allt sömnsidan behöver: nattens siffror, historik och härledda mått.

    Kvällens läggdagsrekommendation har funnits i koden hela tiden men bara
    varit synlig för chatten — här blir den en del av sidan.
    """
    days = max(7, min(int(request.args.get('days', 21) or 21), 60))
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute('''SELECT date, sleep_score, sleep_hours, deep_pct, rem_pct,
                        light_pct, awake_pct, sleep_start, sleep_end, hrv_avg, resting_hr, body_battery
                    FROM health_history WHERE user_id=%s
                    ORDER BY date DESC LIMIT %s''', (uid(), days))
                nights = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        return _server_error(e, 'sleep.history_failed', message='Sömnhistoriken kunde inte hämtas.')

    for item in nights:
        if item.get('sleep_hours') is not None:
            item['sleep_hours'] = float(item['sleep_hours'])

    summary = sleep_analysis.summarize(nights)

    tonight = None
    try:
        tonight = _build_sleep_coach()
    except Exception as e:
        print('Sömncoach kunde inte byggas:', e)

    # Nattens hypnogram ligger i hälso-cachen och behöver inte hämtas om.
    last_night = None
    row = get_cache('health', uid())
    if row and row[0]:
        sleep = (row[0] or {}).get('sleep') or {}
        last_night = {
            'score': sleep.get('score'),
            'levels': sleep.get('levels') or [],
            'startGMT': sleep.get('startGMT'),
            'endGMT': sleep.get('endGMT'),
        }

    return jsonify({
        'nights': nights,
        'summary': summary,
        'tonight': tonight,
        'lastNight': last_night,
    })


@app.get('/api/sleep/insights')
def sleep_insights_endpoint():
    try:
        return jsonify(_get_sleep_insights(force=request.args.get('force') == '1'))
    except Exception as e:
        return _server_error(e, 'sleep.insights_failed', message='Sömnanalysen kunde inte skapas.')


@app.get('/api/sleep-coach')
def sleep_coach_endpoint():
    """Compatibility endpoint used by the local AC keeper for a dated night schedule."""
    try:
        return jsonify(_build_sleep_coach())
    except Exception as e:
        return _server_error(e, 'sleep.coach_failed', message='Sömnschemat kunde inte hämtas.')


def _build_sleep_coach():
    """Sömncoach: bygg kommande sömnschema från kalender + senaste sömn."""
    """Build one practical recommendation for tonight from sleep history + tomorrow calendar."""
    target_base_h = 7.5
    today = date.today()

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT date, sleep_score, sleep_hours, hrv_avg, resting_hr
                    FROM health_history WHERE user_id=%s ORDER BY date DESC LIMIT 7''', (uid(),))
                history = cur.fetchall()
    except Exception as e:
        raise RuntimeError('Sömnhistoriken kunde inte hämtas.') from e

    recent_hours = [float(r[2]) for r in history if r[2] is not None]
    avg_sleep = round(sum(recent_hours) / len(recent_hours), 2) if recent_hours else None
    last_sleep = recent_hours[0] if recent_hours else None
    sleep_score = history[0][1] if history and history[0][1] is not None else None

    sleep_debt = max(0, target_base_h - (last_sleep or target_base_h))
    target_h = target_base_h
    if sleep_debt >= 1.25 or (sleep_score is not None and sleep_score < 60):
        target_h = 8.5
    elif sleep_debt >= 0.5 or (sleep_score is not None and sleep_score < 75):
        target_h = 8.0

    cal_row = get_cache('gcal_events', uid())
    events = cal_row[0] if cal_row else []
    event_starts = []
    for ev in events or []:
        if ev.get('allDay'):
            continue
        start = _parse_calendar_dt(ev.get('start'))
        if not start:
            continue
        event_starts.append({
            'title': ev.get('title', 'Calendar event'),
            'start': start,
            'kind': _event_kind(ev.get('title', '')),
            'location': ev.get('location', ''),
        })

    wake_day = today + timedelta(days=1)
    day_events = [e for e in event_starts if e['start'].date() == wake_day]
    weekend = wake_day.weekday() >= 5
    default_wake = datetime.combine(wake_day, datetime.min.time(), LOCAL_TZ).replace(
        hour=8 if weekend else 7, minute=30 if weekend else 0
    )

    chosen_event = None
    wake_dt = default_wake
    anchor = None
    reason = 'Normal vakentid imorgon'
    for ev in sorted(day_events, key=lambda e: e['start']):
        buffer_min = 75
        if ev['kind'] == 'travel':
            buffer_min = 120
        elif ev['kind'] == 'work':
            buffer_min = 90
        candidate_wake = ev['start'] - timedelta(minutes=buffer_min)
        if candidate_wake < default_wake:
            chosen_event = ev
            wake_dt = max(candidate_wake, datetime.combine(wake_day, datetime.min.time(), LOCAL_TZ).replace(hour=5))
            break

    if chosen_event:
        anchor = {
            'title': chosen_event['title'],
            'time': _fmt_clock(chosen_event['start']),
            'kind': chosen_event['kind'],
        }
        reason = f"{chosen_event['title']} börjar {_fmt_clock(chosen_event['start'])}, så vakna tidigare."

    bedtime = wake_dt - timedelta(hours=target_h)
    wind_down = bedtime - timedelta(minutes=45)
    ac_precool = bedtime - timedelta(hours=2)

    night = {
        'date': wake_day.isoformat(),
        'label': wake_day.strftime('%a %d %b'),
        'bedtime': _fmt_clock(bedtime),
        'wake': _fmt_clock(wake_dt),
        'windDown': _fmt_clock(wind_down),
        'acPrecool': _fmt_clock(ac_precool),
        'targetHours': target_h,
        'reason': reason,
        'anchor': anchor,
    }

    headline = 'Lägg dig ' + night['bedtime']
    if anchor:
        headline = 'Kalenderanpassad sömn'
    elif sleep_debt >= 0.5:
        headline = 'Ta igen sömnskuld'

    reason_bits = []
    if last_sleep is not None:
        reason_bits.append(f"i natt blev {last_sleep:.1f}h")
    if sleep_score is not None:
        reason_bits.append(f"sömnpoäng {sleep_score}")
    if anchor:
        reason_bits.append(f"imorgon börjar med {anchor['title']} kl {anchor['time']}")
    basis = ', '.join(reason_bits) if reason_bits else 'din normala vakentid'

    return {
        'ok': True,
        'headline': headline,
        'targetHours': target_h,
        'avgSleepHours': avg_sleep,
        'lastSleepHours': last_sleep,
        'sleepScore': sleep_score,
        'calendarSynced': bool(cal_row),
        'summary': (
            f"Lägg dig {night['bedtime']} i natt för att få cirka {target_h:g}h sömn. "
            f"Detta baseras på {basis}."
        ),
        'night': night,
        'nights': [night],
    }


_PLAN_ACTIONS = ('justera', 'ändra', 'flytta', 'schemalägg', 'planera in', 'lägg in',
                 'ta bort', 'byt ut')
_PLAN_WORDS = ('plan', 'pass', 'träning', 'vilodag', 'löpning', 'styrka', 'intervall')
_SLEEP_WORDS = ('sömn', 'sov', 'läggdags', 'lägga mig', 'vakna', 'natt')

# Ord ett rent medhåll får bestå av. Allt utanför listan betyder att svaret
# säger något mer än "ja" — och då är det ingen bekräftelse längre.
_AFFIRMATION_VOCAB = {'ja', 'jo', 'japp', 'javisst', 'jajemen', 'yes', 'ok', 'okej',
                      'okey', 'absolut', 'visst', 'gärna', 'tack', 'kör', 'gör', 'göra',
                      'på', 'det', 'den', 'så', 'snälla', 'kan', 'du', 'vi'}
_AFFIRMATION_CORE = {'ja', 'jo', 'japp', 'javisst', 'jajemen', 'yes', 'ok', 'okej',
                     'okey', 'absolut', 'visst', 'gärna', 'kör', 'gör'}


def _last_message(history, role):
    for msg in reversed(history or []):
        if msg['role'] == role:
            return msg['content'].lower()
    return ''


def _is_affirmation(message):
    """"ja", "kör på", "ja gör det" — svar som bara bekräftar föregående tur.

    Varje ord måste rymmas i medhållsordlistan, annars är det en ny fråga:
    "ja men varför då?" ska inte tolkas som ett klartecken."""
    words = re.findall(r'[\wåäöéÅÄÖ]+', message.lower())
    return (bool(words) and len(words) <= 5
            and all(word in _AFFIRMATION_VOCAB for word in words)
            and any(word in _AFFIRMATION_CORE for word in words))


def _is_plan_change_request(message, history=None):
    """Only apply a plan change when the user clearly asks for one.

    Med samtalet i handen räcker "flytta det till torsdag" eller "ja, kör på" —
    men bara när coachens föregående svar faktiskt handlade om planen. En
    planändring skriver om schemat, så otydliga fall ska hellre bli ett vanligt
    chattsvar."""
    text = message.lower()
    has_action = any(action in text for action in _PLAN_ACTIONS)
    if has_action and any(word in text for word in _PLAN_WORDS):
        return True
    reply = _last_message(history, 'assistant')
    if not any(word in reply for word in _PLAN_WORDS):
        return False
    # Coachen frågade något om planen och löparen sa ja — då är ett klartecken
    # lika tydligt som en fullständig begäran.
    return has_action or ('?' in reply and _is_affirmation(message))


def _is_sleep_request(message, history=None):
    if any(word in message.lower() for word in _SLEEP_WORDS):
        return True
    # "varför då?" strax efter en sömnfråga handlar fortfarande om sömn, så
    # sömnunderlaget måste följa med även om ordet inte upprepas.
    previous = _last_message(history, 'user')
    return len(message) <= 80 and any(word in previous for word in _SLEEP_WORDS)


def _plan_request_text(message, history):
    """Ge planändraren samtalet som begäran lutar sig mot.

    "flytta det till torsdag" betyder ingenting utan de föregående turerna."""
    if not history:
        return message
    tail = '\n'.join(
        ('Runner: ' if msg['role'] == 'user' else 'Coach: ')
        + msg['content'][:400].replace('"', "'")
        for msg in history[-4:])
    return (f'{message}\n\nEarlier in the same conversation (this is what the request '
            f'refers to):\n{tail}')


@app.post('/api/assistant')
def assistant_chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get('message') or '').strip()
    history = normalize_history(data.get('history'))
    if not message:
        return _api_error('message_required', 'Skriv en fråga först.', 400)
    if len(message) > 500:
        return _api_error('request_too_large', 'Coachfrågan är för lång.', 400)
    if not llm_available():
        return _api_error('ai_unavailable', 'AI-tjänsten är inte konfigurerad.', 503)
    try:
        if _is_plan_change_request(message, history):
            result = _apply_plan_request(_plan_request_text(message, history))
            changes = result.get('changes', 0)
            summary = result.get('summary') or ('Planen justerad.' if changes else 'Inga ändringar behövdes.')
            notes = result.get('coaching_notes') or ''
            reply = f"{summary}\n\n{notes}".strip()
            return jsonify({'reply': reply, 'planAdjusted': True})

        custom_ctx = str(data.get('context') or '').strip()
        if custom_ctx:
            context = custom_ctx
        else:
            context = (
                "Du är Trainyzes personliga elittränare och fysiologiska coach för löpning och konditionsidrott.\n"
                "Ditt uppdrag är att hjälpa löparen att utvecklas maximalt över tid: bygga aerob bas, höja tröskelfarten, "
                "optimera löpekonomin och undvika överbelastning eller skador.\n\n"
                "COACHNINGSPRINCIPER:\n"
                "1. Tydliga, handfasta råd: Ange alltid konkreta tempon (min/km), pulszoner, repetitioner och vilolängd. Flumma aldrig.\n"
                "2. Fysiologisk precision: Förklara pedagogiskt *varför* en viss intensitet eller ett visst pass behövs (laktattröskel, "
                "mitokondrietäthet, kapillarisering i Zon 2, VO2max, RPE).\n"
                "3. Progressionsfokus: Identifiera löparens utvecklingsområden och ge råd som leder till långsiktiga PB.\n"
                "4. Lyssna och anpassa: Om löparen känner sig sliten eller har ont om tid, ge en omedelbar justerad plan.\n"
                "5. Språk och ton: Professionell, engagerad, empatisk och rak svensk löparcoach.\n"
            )

        # Lägg till dagens hälso- och belastningskontext
        try:
            today_str = date.today().isoformat()
            h_snap = latest_health_snapshot(uid(), today_str) or {}
            cns = _cns_score_from_health(h_snap)
            rot = compute_bevel_rest_or_train(h_snap)
            context += f"\nDAGENS STATUS FÖR LÖPAREN ({today_str}):\n"
            context += f"- Beredskap (CNS): {cns}/100\n"
            context += f"- Dagens rekommendation: {rot.get('headline')} ({rot.get('badge')})\n"
            context += f"- Mål-Strain idag: {rot.get('targetStrain', {}).get('label')}\n"
            context += f"- Sömnskuld: {rot.get('sleepDebtMinutes', 0)} min\n"
        except Exception:
            pass

        if history:
            context += (
                "\n\nThis is an ongoing conversation with the same athlete — the turns "
                "before this message are included. Answer the latest message as a direct "
                "continuation: resolve references like 'det', 'den' or 'samma pass' "
                "against what was already said, keep any advice you have already given "
                "unless new information changes it, and do not repeat greetings or "
                "context the athlete has just been told."
            )

        if _is_sleep_request(message, history):
            sleep = _build_sleep_coach()
            insights = _get_sleep_insights()
            context += "\n\nSÖMNSCHEMA:\n" + json.dumps(sleep, ensure_ascii=False)
            context += "\n\nSÖMNINSIKTER:\n" + json.dumps(insights, ensure_ascii=False)

        execution_context = _recent_execution_block(uid(), days=21, limit=10)
        if execution_context:
            context += execution_context + (
                "\n\nNär löparen frågar om ett specifikt genomfört pass, använd de uppmätta siffrorna ovan "
                "(splittar, puls, tempo) och analysera utförandet ärligt."
            )

        # Tempofrågor ska besvaras mot uppmätt tröskel, och ett mål som ligger
        # bortom nuvarande fysiologi ska sägas rakt ut — inte peppas bort.
        pace_context = _pace_context(uid())
        if (pace_context.get('anchor') or {}).get('ltPaceSec'):
            context += "\n\nMEASURED PACE CAPABILITY:\n" + pace_progression.describe_anchor(
                pace_context['anchor'], pace_context.get('goalFeasibility'))
            feasibility = pace_context.get('goalFeasibility')
            if feasibility and feasibility['verdict'] == 'out_of_reach':
                context += (
                    f"\n\nThe stated goal pace is {feasibility['gapSec']} s/km faster than what "
                    "this athlete's measured threshold currently supports. If the goal comes up, "
                    "say so honestly and give the realistic time the current threshold implies, "
                    "along with what would have to change. Do not encourage the goal as if it "
                    "were within reach."
                )
        return jsonify({'reply': call_llm(message, max_tokens=1024, system=context,
                                          history=history)})
    except Exception as e:
        return _server_error(
            e, 'assistant.provider_failed', status=502, code='ai_provider_error',
            message='Coachen kunde inte svara just nu.'
        )

# --- Google Calendar ---
def get_gcal_service():
    if not GCAL_AVAILABLE:
        return None
    if not os.path.exists(GCAL_CREDS):
        return None
    token_path = gcal_token()
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, GCAL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GRequest())
            except Exception as ex:
                # Refresh-token utgången/återkallad (Google "Testing"-appar: 7 dagar).
                # Kasta inte 500 — kräver ny inloggning via reauth_google.py.
                print('Google token-refresh misslyckades, ny inloggning krävs:', ex)
                return None
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GCAL_CREDS, GCAL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
    return gbuild('calendar', 'v3', credentials=creds)

def _plain_calendar_text(value):
    text = re.sub(r'<[^>]+>', ' ', value or '')
    return re.sub(r'\s+', ' ', text).strip()

def _calendar_description_signals(ev):
    text = _plain_calendar_text(' '.join([
        ev.get('title', ''),
        ev.get('location', ''),
        ev.get('desc', ''),
    ])).lower()
    signals = []
    rules = [
        (r'\b(flight|flyg|airport|flygplats|resa|travel|train|tåg|bilresa|spanien|hotell)\b',
         'resa/logistik: sänk kraven, undvik kvalitetspass samma dag om möjligt'),
        (r'\b(tidig|early|06:|05:|04:|before 7|innan 7)\b',
         'tidig start: räkna med kortare sömn och undvik hårda pass'),
        (r'\b(sen|late|middag|fest|party|konsert|after work|aw|alkohol|vin|öl)\b',
         'sen kväll/social belastning: prioritera återhämtning dagen efter'),
        (r'\b(stress|deadline|presentation|möte|meeting|workshop|kund|jobb|work)\b',
         'arbetsstress: lägg helst inte nyckelpass samma dag'),
        (r'\b(vila|rest|ledig|semester|vacation|holiday|fri)\b',
         'ledig/vila: kan passa lugnt pass om övriga signaler är gröna'),
        (r'\b(sjuk|ill|förkyld|cold|feber|injur|skad)\b',
         'sjukdom/skada nämns: prioritera vila eller mycket lugnt'),
        (r'\b(sov|sleep|dålig sömn|lite sömn|trött|tired)\b',
         'sömn/trötthet nämns: undvik intensitet'),
    ]
    for pattern, signal in rules:
        if re.search(pattern, text):
            signals.append(signal)
    return list(dict.fromkeys(signals))

def fetch_gcal_events(days=14, past_days=30):
    svc = get_gcal_service()
    if not svc:
        return []
    now = datetime.utcnow()
    time_min = (now - timedelta(days=past_days)).isoformat() + 'Z'
    time_max = (now + timedelta(days=days)).isoformat() + 'Z'
    try:
        result = svc.events().list(
            calendarId=GCAL_ID,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=200,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        raw_events = []
        for e in result.get('items', []):
            start = e['start'].get('dateTime', e['start'].get('date', ''))
            end   = e['end'].get('dateTime',   e['end'].get('date', ''))
            raw_events.append({
                'id':       e.get('id'),
                'title':    e.get('summary', 'Event'),
                'start':    start,
                'end':      end,
                'allDay':   'dateTime' not in e['start'],
                'location': e.get('location', ''),
                'desc':     _plain_calendar_text(e.get('description', '')),
            })
        # Some imported calendars encode all-day trips as repeated 06:00-20:00
        # timed events. Treat repeated same-title daytime blocks as all-day so
        # the dashboard does not imply exact clock commitments.
        title_day_counts = {}
        for ev in raw_events:
            if ev['allDay']:
                continue
            title = (ev.get('title') or '').strip().lower()
            start_day = (ev.get('start') or '')[:10]
            if title and start_day:
                title_day_counts.setdefault(title, set()).add(start_day)
        repeated_titles = {title for title, days_seen in title_day_counts.items() if len(days_seen) >= 2}
        events = []
        for ev in raw_events:
            title = (ev.get('title') or '').strip().lower()
            if not ev['allDay'] and title in repeated_titles:
                try:
                    start_dt = datetime.fromisoformat(ev['start'].replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(ev['end'].replace('Z', '+00:00'))
                    dur_h = (end_dt - start_dt).total_seconds() / 3600
                    if 6 <= start_dt.hour <= 9 and 18 <= end_dt.hour <= 22 and dur_h >= 8:
                        ev = {**ev, 'allDay': True}
                except Exception:
                    pass
            events.append(ev)
        return events
    except Exception as ex:
        print('Google Calendar fel:', ex)
        return []

@app.get('/api/calendar')
def calendar_events():
    if not os.path.exists(GCAL_CREDS):
        return jsonify({'ok': False, 'error': 'google_credentials.json is missing', 'events': []})
    if get_gcal_service() is None:
        return jsonify({'ok': False, 'error': 'Google token has expired or been revoked. Run reauth_google.py and sign in again.', 'events': []})
    events = fetch_gcal_events(days=90, past_days=30)
    # Cacha i DB i 30 min
    set_cache('gcal_events', events, uid())
    return jsonify({'ok': True, 'events': events})

@app.get('/api/calendar/status')
def calendar_status():
    has_creds = os.path.exists(GCAL_CREDS)
    has_token = os.path.exists(gcal_token())
    return jsonify({'hasCreds': has_creds, 'hasToken': has_token, 'available': GCAL_AVAILABLE})

# --- Minne / Noteringar ---
@app.get('/api/notes')
def get_notes():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, text, category, created_at FROM user_notes WHERE user_id=%s ORDER BY created_at DESC', (uid(),))
            rows = cur.fetchall()
    return jsonify({'notes': [{'id': r[0], 'text': r[1], 'category': r[2], 'created_at': r[3]} for r in rows]})

@app.post('/api/notes')
def add_note():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get('text', '').strip()
    category = data.get('category', 'general')
    if not text:
        return jsonify({'error': 'Empty note'}), 400
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO user_notes (text, category, created_at, user_id) VALUES (%s, %s, %s, %s) RETURNING id',
                        (text, category, time.time(), uid()))
            new_id = cur.fetchone()[0]
        conn.commit()
    return jsonify({'ok': True, 'id': new_id})

@app.delete('/api/notes/<int:note_id>')
def delete_note(note_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM user_notes WHERE id=%s AND user_id=%s', (note_id, uid()))
        conn.commit()
    return jsonify({'ok': True})

# --- Dagbok ---
@app.get('/api/journal')
def get_journal():
    try:
        limit = min(int(request.args.get('limit', 30)), 90)
    except ValueError:
        limit = 30
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT id, entry_date, mood, energy, text, created_at, updated_at
                FROM journal_entries
                WHERE user_id=%s
                ORDER BY entry_date DESC
                LIMIT %s
            ''', (uid(), limit))
            rows = cur.fetchall()
    return jsonify({'entries': [
        {
            'id': r[0],
            'date': r[1],
            'mood': r[2] or '',
            'energy': r[3],
            'text': r[4],
            'created_at': r[5],
            'updated_at': r[6],
        } for r in rows
    ]})

@app.post('/api/journal')
def save_journal():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get('text', '').strip()
    entry_date = (data.get('date') or datetime.now(LOCAL_TZ).date().isoformat()).strip()
    mood = (data.get('mood') or '').strip()[:32]
    energy = data.get('energy')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', entry_date):
        return jsonify({'error': 'Invalid date'}), 400
    if not text:
        return jsonify({'error': 'Empty journal entry'}), 400
    try:
        energy = int(energy) if energy not in (None, '') else None
    except (TypeError, ValueError):
        energy = None
    if energy is not None:
        energy = max(1, min(5, energy))
    now = time.time()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO journal_entries (entry_date, mood, energy, text, created_at, updated_at, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entry_date, user_id)
                DO UPDATE SET mood=EXCLUDED.mood, energy=EXCLUDED.energy, text=EXCLUDED.text, updated_at=EXCLUDED.updated_at
                RETURNING id
            ''', (entry_date, mood, energy, text, now, now, uid()))
            entry_id = cur.fetchone()[0]
        conn.commit()
    return jsonify({'ok': True, 'id': entry_id})

@app.delete('/api/journal/<int:entry_id>')
def delete_journal(entry_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM journal_entries WHERE id=%s AND user_id=%s', (entry_id, uid()))
        conn.commit()
    return jsonify({'ok': True})

# --- Styrka ---
STRENGTH_TYPES = ('strength_training', 'fitness_equipment', 'gym', 'indoor_cardio', 'cardio', 'bouldering')

@app.get('/api/strength')
def strength_sessions():
    try:
        link_manual_exercises_to_activities(uid())
    except Exception as e:
        print('Strength-länkning fel:', e)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT raw FROM activities WHERE type = ANY(%s) AND user_id=%s ORDER BY date DESC LIMIT 30",
                        (list(STRENGTH_TYPES), uid()))
            rows = cur.fetchall()
    sessions = []
    for r in rows:
        a = r[0]
        sessions.append({
            'id': str(a.get('activityId')),
            'name': a.get('activityName', 'Strength session'),
            'date': a.get('startTimeLocal'),
            'duration': a.get('duration'),
            'calories': a.get('calories'),
            'avgHR': a.get('averageHR'),
            'type': a.get('activityType', {}).get('typeKey'),
        })
    return jsonify({'sessions': sessions})

@app.get('/api/strength/<session_id>/exercises')
def get_exercises(session_id):
    try:
        link_manual_exercises_to_activity(session_id, uid())
    except Exception as e:
        print('Strength-passlänkning fel:', e)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id, exercise, sets, reps, weight, note FROM strength_exercises WHERE session_id=%s AND user_id=%s ORDER BY id',
                        (session_id, uid()))
            rows = cur.fetchall()
    return jsonify({'exercises': [{'id': r[0], 'exercise': r[1], 'sets': r[2], 'reps': r[3], 'weight': r[4], 'note': r[5]} for r in rows]})

def _first_rep_count(reps):
    if reps is None:
        return None
    m = re.search(r'\d+(?:[,.]\d+)?', str(reps))
    if not m:
        return None
    return float(m.group(0).replace(',', '.'))

def _session_day(session_id, activity_dates, created_at):
    sid = str(session_id)
    if sid in activity_dates:
        return activity_dates[sid]
    if re.match(r'^\d{4}-\d{2}-\d{2}$', sid):
        return sid
    try:
        return datetime.fromtimestamp(float(created_at), LOCAL_TZ).date().isoformat()
    except Exception:
        return date.today().isoformat()


def _strength_progression_history(user_id):
    """Return strength logs with a stable local date for progression planning."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT id, session_id, exercise, sets, reps, weight, note, created_at
                FROM strength_exercises
                WHERE user_id=%s
                ORDER BY created_at ASC, id ASC
            ''', (user_id,))
            exercise_rows = cur.fetchall()
            cur.execute('SELECT id, date, raw FROM activities WHERE user_id=%s', (user_id,))
            activity_rows = cur.fetchall()

    activity_dates = {}
    for activity_id, stored_date, raw in activity_rows:
        raw = raw or {}
        started = raw.get('startTimeLocal') or raw.get('date') or stored_date
        if started:
            activity_dates[str(activity_id)] = str(started)[:10]

    return [{
        'id': row[0],
        'sessionId': str(row[1]),
        'exercise': row[2],
        'sets': row[3],
        'reps': row[4],
        'weight': float(row[5]) if row[5] is not None else None,
        'note': row[6] or '',
        'date': _session_day(row[1], activity_dates, row[7]),
    } for row in exercise_rows]


def _plan_session_date(session, reference_day=None):
    reference_day = reference_day or date.today()
    iso_year = reference_day.isocalendar()[0]
    return date.fromisocalendar(iso_year, int(session['week']), int(session['dow']) + 1)


PLAN_SESSION_TYPES = ('run', 'easy', 'lift', 'race', 'rest')


def _valid_session_type(value):
    value = str(value or '').strip().lower()
    return value if value in PLAN_SESSION_TYPES else None


def _enrich_strength_plan(sessions, user_id, history=None):
    """Attach calculated prescriptions without mutating the saved plan text."""
    history = history if history is not None else _strength_progression_history(user_id)
    for session in sessions:
        session['strength_recommendations'] = []
        session['strength_recommendation_text'] = ''
        if session.get('type') != 'lift':
            continue
        try:
            session_day = _plan_session_date(session).isoformat()
            recommendations = build_strength_recommendations(
                session.get('detail', ''), history, before_date=session_day
            )
            if not recommendations:
                # Gympass utan igenkända övningar i detaljtexten (t.ex. ett löppass
                # som coachen gjort om till "Gympass · helkropp") får ändå vikter
                # utifrån användarens egen träningshistorik.
                recommendations = build_default_recommendations(
                    history, before_date=session_day, limit=6
                )
            session['strength_recommendations'] = recommendations
            session['strength_recommendation_text'] = recommendation_summary(recommendations)
        except (TypeError, ValueError) as exc:
            print(f"Strength progression skipped for plan session {session.get('id')}: {exc}")
    return sessions

@app.get('/api/strength/analysis')
def strength_analysis():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, raw FROM activities WHERE type = ANY(%s) AND user_id=%s",
                        (list(STRENGTH_TYPES), uid()))
            activity_rows = cur.fetchall()
            cur.execute('''
                SELECT id, session_id, exercise, sets, reps, weight, note, created_at
                FROM strength_exercises
                WHERE user_id=%s
                ORDER BY created_at ASC, id ASC
            ''', (uid(),))
            exercise_rows = cur.fetchall()

    activity_dates = {}
    for aid, raw in activity_rows:
        raw = raw or {}
        start = raw.get('startTimeLocal') or raw.get('date')
        if start:
            activity_dates[str(aid)] = str(start)[:10]

    entries = []
    sessions = set()
    weekly_volume = {}
    by_exercise = {}
    now_day = datetime.now(LOCAL_TZ).date()
    cutoff_28 = now_day - timedelta(days=28)

    for ex_id, session_id, exercise, sets, reps, weight, note, created_at in exercise_rows:
        name = (exercise or '').strip()
        if not name:
            continue
        day = _session_day(session_id, activity_dates, created_at)
        try:
            day_obj = datetime.fromisoformat(day[:10]).date()
        except Exception:
            day_obj = now_day
            day = day_obj.isoformat()

        set_count = int(sets or 1)
        rep_count = _first_rep_count(reps)
        kg = float(weight) if weight is not None else None
        volume = round(set_count * rep_count * kg, 1) if rep_count and kg else 0
        e1rm = round(kg * (1 + rep_count / 30), 1) if rep_count and kg else None
        key = name.lower()
        entry = {
            'id': ex_id,
            'sessionId': str(session_id),
            'date': day,
            'exercise': name,
            'sets': set_count,
            'reps': reps,
            'repCount': rep_count,
            'weight': kg,
            'volume': volume,
            'e1rm': e1rm,
            'note': note or '',
        }
        entries.append(entry)
        sessions.add((str(session_id), day))
        monday = (day_obj - timedelta(days=day_obj.weekday())).isoformat()
        weekly_volume[monday] = weekly_volume.get(monday, 0) + volume
        by_exercise.setdefault(key, {'name': name, 'entries': []})['entries'].append(entry)

    exercises = []
    prs = []
    for item in by_exercise.values():
        ex_entries = sorted(item['entries'], key=lambda e: (e['date'], e['id']))
        weighted = [e for e in ex_entries if e['weight']]
        e1rms = [e for e in ex_entries if e['e1rm']]
        latest = ex_entries[-1]
        best = max(e1rms, key=lambda e: e['e1rm']) if e1rms else None
        latest_e1rm = next((e for e in reversed(ex_entries) if e['e1rm']), None)
        previous_e1rm = next((e for e in reversed(ex_entries[:-1]) if e['e1rm']), None)
        delta = round(latest_e1rm['e1rm'] - previous_e1rm['e1rm'], 1) if latest_e1rm and previous_e1rm else None
        total_volume = round(sum(e['volume'] for e in ex_entries), 1)
        trend = 'flat'
        if delta is not None:
            trend = 'up' if delta > 0.2 else 'down' if delta < -0.2 else 'flat'
        if best and latest_e1rm and best['id'] == latest_e1rm['id']:
            prs.append({
                'exercise': item['name'],
                'date': best['date'],
                'e1rm': best['e1rm'],
                'weight': best['weight'],
                'reps': best['reps'],
            })
        exercises.append({
            'exercise': item['name'],
            'sessions': len({e['sessionId'] for e in ex_entries}),
            'sets': sum(e['sets'] for e in ex_entries),
            'totalVolume': total_volume,
            'lastDate': latest['date'],
            'lastWeight': latest['weight'],
            'lastReps': latest['reps'],
            'bestWeight': max((e['weight'] or 0) for e in weighted) if weighted else None,
            'bestE1rm': best['e1rm'] if best else None,
            'currentE1rm': latest_e1rm['e1rm'] if latest_e1rm else None,
            'deltaE1rm': delta,
            'trend': trend,
        })

    exercises.sort(key=lambda e: (e['lastDate'], e['totalVolume']), reverse=True)
    weeks = [{'weekStart': k, 'volume': round(v, 1)} for k, v in sorted(weekly_volume.items())[-8:]]
    recent_sessions = len({s for s in sessions if datetime.fromisoformat(s[1]).date() >= cutoff_28})
    total_volume = round(sum(e['volume'] for e in entries), 1)
    latest_date = max((e['date'] for e in entries), default=None)
    best_lifts = sorted([e for e in exercises if e['bestE1rm']], key=lambda e: e['bestE1rm'], reverse=True)[:5]
    improvements = sorted([e for e in exercises if e['deltaE1rm'] is not None], key=lambda e: e['deltaE1rm'], reverse=True)[:5]

    return jsonify({
        'summary': {
            'exerciseLogs': len(entries),
            'sessions': len(sessions),
            'recentSessions28d': recent_sessions,
            'uniqueExercises': len(exercises),
            'totalVolume': total_volume,
            'latestDate': latest_date,
        },
        'weeks': weeks,
        'exercises': exercises[:30],
        'bestLifts': best_lifts,
        'improvements': improvements,
        'recentPrs': sorted(prs, key=lambda p: p['date'], reverse=True)[:6],
    })

@app.post('/api/strength/<session_id>/exercises')
def add_exercise(session_id):
    data = request.get_json(force=True, silent=True) or {}
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO strength_exercises (session_id,exercise,sets,reps,weight,note,created_at,user_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                        (session_id, data.get('exercise',''), data.get('sets'), data.get('reps',''),
                         data.get('weight'), data.get('note',''), time.time(), uid()))
            new_id = cur.fetchone()[0]
        conn.commit()
    return jsonify({'ok': True, 'id': new_id})

@app.delete('/api/strength/exercises/<int:ex_id>')
def delete_exercise(ex_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM strength_exercises WHERE id=%s AND user_id=%s', (ex_id, uid()))
        conn.commit()
    return jsonify({'ok': True})

# --- Statiska filer ---
# ─────────────────────────────────────────────
# TRÄNINGSPLAN — seed-data (samma som JS-arrayen)
# ─────────────────────────────────────────────
PLAN_SEED = [
    # ── V23 · Återhämtning efter GöteborgsVarvet · ~35 km ─────────────────────
    {'week':23,'dow':1,'type':'run', 'km':6,  'title':'Återhämtningsjogg · 6 km',    'detail':'Z2 · 4:50–5:15/km · Lugn och lätt · Vila musklerna efter halvmaran'},
    {'week':23,'dow':2,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2 · 5:00–5:20/km · Aktiv återhämtning'},
    {'week':23,'dow':3,'type':'lift','km':0,  'title':'Helkropp – intro',             'detail':'Knäböj 3×10, marklyft 3×8, bänkpress 3×10, latsdrag 3×10, plankan 3×45 sek · 60–65% av max'},
    {'week':23,'dow':4,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',              'detail':'Z2 · 20–25 min · Spola ur benen'},
    {'week':23,'dow':6,'type':'easy','km':10, 'title':'Söndagsjogg · 10 km',         'detail':'Z2 · 5:00–5:20/km · Lugnt och långsamt'},
    # ── V24 · Bas · ~40 km ─────────────────────────────────────────────────────
    {'week':24,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',              'detail':'Z2 · Aktivering inför veckans kvalitetspass'},
    {'week':24,'dow':1,'type':'run', 'km':9,  'title':'5×1000m intervaller',          'detail':'Uppvärmning 2 km · 5×1000m @ 3:30/km · 2 min joggvila · Nedvarvning 2 km · ~9 km totalt'},
    {'week':24,'dow':2,'type':'lift','km':0,  'title':'Överkropp + core',             'detail':'Bänkpress 4×8, axelpress 3×10, latsdrag 4×8, rodd 3×10, dips 3×max, dead bug 3×12 · 70%'},
    {'week':24,'dow':3,'type':'easy','km':9,  'title':'Medium Z2 · 9 km',            'detail':'Z2 · 5:00–5:15/km · Aerob bas'},
    {'week':24,'dow':4,'type':'lift','km':0,  'title':'Underkropp + core',            'detail':'Knäböj 4×8, RDL 3×10, benpress 3×12, bulgarska utfall 3×8/ben, plankan 4×45 sek · 70–75%'},
    {'week':24,'dow':6,'type':'easy','km':12, 'title':'Långpass · 12 km',             'detail':'Z2 · 5:00–5:20/km · Bygg aerob grund'},
    # ── V25 · Bas · ~45 km ─────────────────────────────────────────────────────
    {'week':25,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',              'detail':'Z2 · Aktivering'},
    {'week':25,'dow':1,'type':'run', 'km':10, 'title':'Tröskelpass · 10 km',          'detail':'Uppvärm 2 km · 6 km @ 4:05/km (tröskel) · Nedvarv 2 km · Kontrollerat och jämnt'},
    {'week':25,'dow':2,'type':'lift','km':0,  'title':'Helkropp – progressiv',        'detail':'Knäböj 4×8, marklyft 3×6, bänkpress 4×8, axelpress 3×10, latsdrag 4×8, core-circuit 3 ronder · 72%'},
    {'week':25,'dow':3,'type':'easy','km':10, 'title':'Medium Z2 · 10 km',           'detail':'Z2 · 5:00/km · Aerob bas'},
    {'week':25,'dow':5,'type':'run', 'km':8,  'title':'6×600m intervaller',           'detail':'Uppvärm 2 km · 6×600m @ 3:25/km · 90 sek vila · Nedvarvning · Snabbt och kontrollerat'},
    {'week':25,'dow':6,'type':'easy','km':14, 'title':'Långpass · 14 km',             'detail':'Z2 · 5:00–5:15/km · Håll det lugnt, bygg uthållighet'},
    # ── V26 · Basbygge · ~50 km ────────────────────────────────────────────────
    {'week':26,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2 · Aktivering'},
    {'week':26,'dow':1,'type':'run', 'km':11, 'title':'3×2000m tröskel',              'detail':'Uppvärm 2 km · 3×2000m @ 4:00/km · 3 min joggvila · Nedvarv 2 km · ~11 km totalt'},
    {'week':26,'dow':2,'type':'lift','km':0,  'title':'Överkropp tung',               'detail':'Bänkpress 4×6, axelpress 4×6, latsdrag 4×6, smalgreppscurl 3×10, tricepspush 3×10, face pulls 3×15 · 78%'},
    {'week':26,'dow':3,'type':'easy','km':10, 'title':'Medium Z2 · 10 km',           'detail':'Z2 · 5:00/km · Aerob underhåll'},
    {'week':26,'dow':4,'type':'lift','km':0,  'title':'Underkropp tung',              'detail':'Knäböj 4×6, marklyft 4×5, bulgarska utfall 3×8, höftlyft 3×12, vadbågar 4×15, plankan 3×60 sek · 78%'},
    {'week':26,'dow':5,'type':'run', 'km':10, 'title':'Fartlekpass · 10 km',          'detail':'2 km Z2 · 5×(3 min @ 3:50/km + 2 min Z2) · 2 km nedvarvning · Varierat och roligt'},
    {'week':26,'dow':6,'type':'easy','km':15, 'title':'Långpass · 15 km',             'detail':'Z2 · 5:00–5:15/km · Sista 2 km @ 4:30/km'},
    # ── V27 · Basbygge · ~55 km ────────────────────────────────────────────────
    {'week':27,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':27,'dow':1,'type':'run', 'km':11, 'title':'Progressionsjogg · 11 km',     'detail':'3 km @ 5:10 · 3 km @ 4:45 · 3 km @ 4:20 · 2 km @ 4:00 · Kontrollerad ansträngning'},
    {'week':27,'dow':2,'type':'lift','km':0,  'title':'Helkropp – styrka',            'detail':'Knäböj 4×6, bänkpress 4×6, marklyft 3×5, axelpress 3×8, latsdrag 4×6, core-circuit · 80%'},
    {'week':27,'dow':3,'type':'easy','km':12, 'title':'Medium Z2 · 12 km',           'detail':'Z2 · 5:00/km'},
    {'week':27,'dow':5,'type':'run', 'km':10, 'title':'4×1200m tempo',                'detail':'Uppvärm 2 km · 4×1200m @ 3:50/km · 2 min vila · Nedvarvning · ~10 km'},
    {'week':27,'dow':6,'type':'easy','km':16, 'title':'Långpass · 16 km',             'detail':'Z2 · 5:00–5:10/km · Lugnt och uthålligt'},
    # ── V28 · Basbygge · ~55 km ────────────────────────────────────────────────
    {'week':28,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':28,'dow':1,'type':'run', 'km':12, 'title':'Tröskelpass · 12 km',          'detail':'Uppvärm 2 km · 8 km @ 3:58/km (halvmaratontröskel) · Nedvarv 2 km · Jämnt tempo'},
    {'week':28,'dow':2,'type':'lift','km':0,  'title':'Överkropp + rörlighet',        'detail':'Bänkpress 4×6, axelpress 4×6, latsdrag 4×6, rodd 3×8, dips 3×max, axelrörlighet, t-spine 15 min · 80%'},
    {'week':28,'dow':3,'type':'easy','km':11, 'title':'Medium Z2 · 11 km',           'detail':'Z2 · Aerob bas'},
    {'week':28,'dow':4,'type':'lift','km':0,  'title':'Underkropp + plyometri',       'detail':'Knäböj 4×5, RDL 4×6, benpress 3×10, boxjumps 4×6, höftlyft 3×12, vadhopp 4×15 · 80%'},
    {'week':28,'dow':6,'type':'easy','km':16, 'title':'Långpass · 16 km',             'detail':'Z2 · 5:00/km · Steady state · Sista 3 km lite snabbare'},
    # ── V29 · Basbygge toppar · ~58 km ────────────────────────────────────────
    {'week':29,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':29,'dow':1,'type':'run', 'km':11, 'title':'4×2000m @ halvmaraton pace',   'detail':'Uppvärm 2 km · 4×2000m @ 3:52/km · 2:30 min joggvila · Nedvarv 2 km · Race-förnimmelse'},
    {'week':29,'dow':2,'type':'lift','km':0,  'title':'Helkropp – max styrka',        'detail':'Knäböj 5×5, marklyft 4×4, bänkpress 5×5, axelpress 4×5, latsdrag 4×5 · 85%'},
    {'week':29,'dow':3,'type':'easy','km':12, 'title':'Medium Z2 · 12 km',           'detail':'Z2 · 5:00/km'},
    {'week':29,'dow':5,'type':'run', 'km':9,  'title':'10×400m bana',                 'detail':'Uppvärm 2 km · 10×400m @ 3:20/km · 90 sek vila · Nedvarv 2 km · Snabbt och skarpt'},
    {'week':29,'dow':6,'type':'easy','km':18, 'title':'Långpass · 18 km',             'detail':'Z2 · 5:00–5:10/km · Viktigaste passet hittills'},
    # ── V30 · Tröskel/Tempo · ~62 km ──────────────────────────────────────────
    {'week':30,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':30,'dow':1,'type':'run', 'km':13, 'title':'Tröskelpass · 13 km',          'detail':'Uppvärm 2 km · 9 km @ 3:55/km · Nedvarv 2 km · Stabilt och kontrollerat'},
    {'week':30,'dow':2,'type':'lift','km':0,  'title':'Överkropp + core',             'detail':'Bänkpress 4×6, axelpress 4×6, latsdrag 4×5, rodd 3×8, plankan 4×60 sek, rygghäv 3×12 · 82%'},
    {'week':30,'dow':3,'type':'easy','km':13, 'title':'Medium Z2 · 13 km',           'detail':'Z2 · Aerob volym'},
    {'week':30,'dow':4,'type':'lift','km':0,  'title':'Underkropp + plyometri',       'detail':'Knäböj 4×5, marklyft 3×4, bulgarska 3×8, boxjumps 4×6, vadbågar 4×15 · 83%'},
    {'week':30,'dow':5,'type':'run', 'km':10, 'title':'6×1000m @ 3:25/km',           'detail':'Uppvärm 2 km · 6×1000m @ 3:25/km · 2 min vila · Nedvarv 2 km · Sharpening'},
    {'week':30,'dow':6,'type':'easy','km':20, 'title':'Långpass · 20 km',             'detail':'Z2 · 5:00/km · Hjärnträning i uthållighet · Håll det lugnt'},
    # ── V31 · Tröskel/Tempo · ~65 km ──────────────────────────────────────────
    {'week':31,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':31,'dow':1,'type':'run', 'km':14, 'title':'Halvmaratonpace · 14 km',      'detail':'Uppvärm 2 km · 10 km @ 3:50/km (halvmaran pace) · Nedvarv 2 km · Känn farten'},
    {'week':31,'dow':2,'type':'lift','km':0,  'title':'Helkropp – styrka',            'detail':'Knäböj 4×5, marklyft 4×4, bänkpress 4×5, axelpress 3×6, latsdrag 4×5, core · 83–85%'},
    {'week':31,'dow':3,'type':'easy','km':13, 'title':'Medium Z2 · 13 km',           'detail':'Z2'},
    {'week':31,'dow':5,'type':'run', 'km':12, 'title':'Tröskelpass · 12 km',          'detail':'Uppvärm 2 km · 8 km @ 3:53/km · Nedvarv 2 km · Konsekvent tempo'},
    {'week':31,'dow':6,'type':'easy','km':20, 'title':'Långpass · 20 km',             'detail':'Z2 · 4:58–5:08/km · Starkt och jämnt'},
    # ── V32 · Tröskel/Tempo · ~65 km ──────────────────────────────────────────
    {'week':32,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':32,'dow':1,'type':'run', 'km':13, 'title':'5×1600m @ 3:48/km',           'detail':'Uppvärm 2 km · 5×1600m @ 3:48/km · 2:30 min vila · Nedvarv 2 km · Race-specifik'},
    {'week':32,'dow':2,'type':'lift','km':0,  'title':'Överkropp + core',             'detail':'Bänkpress 4×5, axelpress 4×5, latsdrag 4×5, rodd 3×8, core-circuit 3 ronder · 85%'},
    {'week':32,'dow':3,'type':'easy','km':14, 'title':'Medium Z2 · 14 km',           'detail':'Z2'},
    {'week':32,'dow':4,'type':'lift','km':0,  'title':'Underkropp',                   'detail':'Knäböj 4×5, RDL 4×5, bulgarska 3×8, vadhopp 4×15 · 85%'},
    {'week':32,'dow':5,'type':'run', 'km':12, 'title':'Progressionsjogg · 12 km',     'detail':'4 km Z2 · 4 km @ 4:15 · 3 km @ 3:55 · 1 km @ 3:47 · Race-förnimmelse'},
    {'week':32,'dow':6,'type':'easy','km':20, 'title':'Långpass · 20 km',             'detail':'Z2 · Peakpass för långdistans · Sista 4 km @ 4:30/km'},
    # ── V33 · Tröskel · ~60 km ────────────────────────────────────────────────
    {'week':33,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':33,'dow':1,'type':'run', 'km':13, 'title':'Halvmaratonpace · 14 km',      'detail':'Uppvärm 2 km · 10 km @ 3:47/km (målpace!) · Nedvarv 2 km · Känn målfarten'},
    {'week':33,'dow':2,'type':'lift','km':0,  'title':'Helkropp – underhåll',         'detail':'Knäböj 3×5, marklyft 3×4, bänkpress 3×5, axelpress 3×6, latsdrag 3×6 · 83% (börja minska volym)'},
    {'week':33,'dow':3,'type':'easy','km':12, 'title':'Medium Z2 · 12 km',           'detail':'Z2'},
    {'week':33,'dow':5,'type':'run', 'km':9,  'title':'8×600m @ 3:25/km',            'detail':'Uppvärm 2 km · 8×600m @ 3:25/km · 90 sek vila · Nedvarv · Sharp och snabb'},
    {'week':33,'dow':6,'type':'easy','km':18, 'title':'Långpass · 18 km',             'detail':'Z2 · 4:58/km · Sista riktiga långpasset'},
    # ── V34 · Tävlingsspecifik · ~68 km ───────────────────────────────────────
    {'week':34,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':34,'dow':1,'type':'run', 'km':14, 'title':'Race simulation · 14 km',      'detail':'Uppvärm 2 km · 10 km @ 3:47/km (exakt målpace) · Nedvarv 2 km · Bekräfta formen'},
    {'week':34,'dow':2,'type':'lift','km':0,  'title':'Överkropp – underhåll',        'detail':'Bänkpress 3×5, axelpress 3×5, latsdrag 3×5 · 80% · Håll muskelstimulus utan utmattning'},
    {'week':34,'dow':3,'type':'easy','km':14, 'title':'Medium Z2 · 14 km',           'detail':'Z2'},
    {'week':34,'dow':4,'type':'lift','km':0,  'title':'Underkropp – underhåll',       'detail':'Knäböj 3×5, RDL 3×5, bulgarska 2×8 · 80%'},
    {'week':34,'dow':5,'type':'run', 'km':11, 'title':'Tröskelpass · 11 km',          'detail':'Uppvärm 2 km · 7 km @ 3:50/km · Nedvarv 2 km'},
    {'week':34,'dow':6,'type':'easy','km':22, 'title':'Långpass · 22 km (peak!)',      'detail':'Z2 · 5:00/km · Längsta passet i hela planen · Mentalt starkt'},
    # ── V35 · Tävlingsspecifik · ~70 km ───────────────────────────────────────
    {'week':35,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':35,'dow':1,'type':'run', 'km':12, 'title':'3×3000m @ 3:47/km',           'detail':'Uppvärm 2 km · 3×3000m @ 3:47/km · 3 min vila · Nedvarv 2 km · Race-spécifikt'},
    {'week':35,'dow':2,'type':'lift','km':0,  'title':'Helkropp – underhåll',         'detail':'Knäböj 3×4, bänkpress 3×4, marklyft 3×3, axelpress 3×5, latsdrag 3×5 · 80%'},
    {'week':35,'dow':3,'type':'easy','km':14, 'title':'Medium Z2 · 14 km',           'detail':'Z2'},
    {'week':35,'dow':5,'type':'run', 'km':14, 'title':'Tröskelpass · 14 km',          'detail':'Uppvärm 2 km · 10 km @ 3:50/km · Nedvarv 2 km · Stark och kontrollerad'},
    {'week':35,'dow':6,'type':'easy','km':22, 'title':'Långpass · 22 km',             'detail':'Z2 · 5:00/km · Volymens höjdpunkt'},
    # ── V36 · Tävlingsspecifik · ~68 km ───────────────────────────────────────
    {'week':36,'dow':0,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':36,'dow':1,'type':'run', 'km':13, 'title':'Race tempo · 13 km',           'detail':'Uppvärm 2 km · 9 km @ 3:47–3:50/km · Nedvarv 2 km · Fokus på ekonomi'},
    {'week':36,'dow':2,'type':'lift','km':0,  'title':'Överkropp lätt',               'detail':'Bänkpress 3×4, axelpress 3×4, latsdrag 3×5 · 78% · Underhåll utan stress'},
    {'week':36,'dow':3,'type':'easy','km':13, 'title':'Medium Z2 · 13 km',           'detail':'Z2'},
    {'week':36,'dow':4,'type':'lift','km':0,  'title':'Underkropp lätt',              'detail':'Knäböj 3×4, RDL 3×5, bulgarska 2×6 · 78%'},
    {'week':36,'dow':5,'type':'run', 'km':10, 'title':'6×1000m @ 3:25/km',           'detail':'Uppvärm 2 km · 6×1000m @ 3:25/km · 2 min vila · Nedvarv · Sharp'},
    {'week':36,'dow':6,'type':'easy','km':20, 'title':'Långpass · 20 km',             'detail':'Z2 · 5:00/km · Sista riktiga långpasset'},
    # ── V37 · Tävlingsspecifik · ~65 km ───────────────────────────────────────
    {'week':37,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':37,'dow':1,'type':'run', 'km':14, 'title':'Halvmaratonpace · 14 km',      'detail':'Uppvärm 2 km · 10 km @ 3:47/km · Nedvarv 2 km · Bekräfta formen'},
    {'week':37,'dow':2,'type':'lift','km':0,  'title':'Helkropp – lätt',              'detail':'Knäböj 3×3, bänkpress 3×3, latsdrag 3×5 · 75% · Håll nervmönstret aktivt'},
    {'week':37,'dow':3,'type':'easy','km':12, 'title':'Medium Z2 · 12 km',           'detail':'Z2'},
    {'week':37,'dow':5,'type':'run', 'km':12, 'title':'Progressionsjogg · 12 km',     'detail':'4 km Z2 · 4 km @ 4:10 · 3 km @ 3:52 · 1 km @ 3:40 · Stark avslutning'},
    {'week':37,'dow':6,'type':'easy','km':18, 'title':'Långpass · 18 km',             'detail':'Z2 · 5:00/km · Sista längre volympass'},
    # ── V38 · Avtrappning start · ~55 km ──────────────────────────────────────
    {'week':38,'dow':0,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2'},
    {'week':38,'dow':1,'type':'run', 'km':10, 'title':'4×1000m @ 3:25/km',           'detail':'Uppvärm 2 km · 4×1000m @ 3:25/km · 2 min vila · Nedvarv · Håll spetsen'},
    {'week':38,'dow':2,'type':'lift','km':0,  'title':'Överkropp – lätt',             'detail':'Bänkpress 3×3, axelpress 3×3, latsdrag 3×4 · 73% · Underhåll'},
    {'week':38,'dow':3,'type':'easy','km':10, 'title':'Medium Z2 · 10 km',           'detail':'Z2'},
    {'week':38,'dow':5,'type':'run', 'km':9,  'title':'Tröskelpass · 9 km',           'detail':'Uppvärm 2 km · 5 km @ 3:50/km · Nedvarv 2 km · Skarp och ekonomisk'},
    {'week':38,'dow':6,'type':'easy','km':18, 'title':'Långpass · 18 km',             'detail':'Z2 · 5:00/km · Sista riktigt långa passet'},
    # ── V39 · Taper · ~50 km ──────────────────────────────────────────────────
    {'week':39,'dow':0,'type':'easy','km':6,  'title':'Lätt Z2 · 6 km',              'detail':'Z2'},
    {'week':39,'dow':1,'type':'run', 'km':9,  'title':'Race tempo · 9 km',            'detail':'Uppvärm 2 km · 5 km @ 3:47/km · Nedvarv 2 km · Bekräfta kroppens redo-känsla'},
    {'week':39,'dow':2,'type':'lift','km':0,  'title':'Styrka – underhåll lätt',      'detail':'Knäböj 2×3, bänkpress 2×3, latsdrag 2×4 · 70% · Minimal trötthet'},
    {'week':39,'dow':3,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2'},
    {'week':39,'dow':5,'type':'run', 'km':9,  'title':'4×1000m @ 3:25/km',           'detail':'Uppvärm 2 km · 4×1000m @ 3:25/km · 2 min vila · Nedvarv · Känn spetsen'},
    {'week':39,'dow':6,'type':'easy','km':16, 'title':'Långpass · 16 km',             'detail':'Z2 · 5:00/km · Sista längre pass · Lugnt och tryggt'},
    # ── V40 · Taper djup · ~35 km ─────────────────────────────────────────────
    {'week':40,'dow':0,'type':'easy','km':5,  'title':'Lätt Z2 · 5 km',              'detail':'Z2 · Håll igång benen'},
    {'week':40,'dow':1,'type':'run', 'km':7,  'title':'3×1000m @ 3:25/km + strides', 'detail':'Uppvärm 2 km · 3×1000m @ 3:25/km · 4×100m strides · Känn fräschheten'},
    {'week':40,'dow':3,'type':'easy','km':7,  'title':'Lätt Z2 · 7 km',              'detail':'Z2 · 25–30 min · Lugnt'},
    {'week':40,'dow':5,'type':'easy','km':6,  'title':'Lätt jogg + strides',         'detail':'15 min Z2 + 6×80m strides · Håll benen snabba inför loppet'},
    {'week':40,'dow':6,'type':'easy','km':8,  'title':'Lätt Z2 · 8 km',              'detail':'Z2 · Mentalt förbered dig · Visualisera loppet'},
    # ── V41 · Tävlingsvecka · ~15 km ─────────────────────────────────────────
    {'week':41,'dow':0,'type':'easy','km':4,  'title':'Lätt aktivering · 4 km',      'detail':'Z2 · 15 min · 4×80m strides i slutet · Håll igång'},
    {'week':41,'dow':1,'type':'run', 'km':4,  'title':'Kort shakeout',               'detail':'10 min Z2 + 3×100m strides @ tävlingsfart · Kort och piggt'},
    {'week':41,'dow':2,'type':'rest','km':0,  'title':'Vila',                        'detail':'Fullständig vila · Ät kolhydratrikt · Sov länge · Packa väskan'},
    {'week':41,'dow':3,'type':'rest','km':0,  'title':'Vila / rörlighet',             'detail':'Lätt stretching 20 min · Inga hårda övningar · Mental förberedelse'},
    {'week':41,'dow':4,'type':'rest','km':0,  'title':'Vila · redo!',                'detail':'Fullständig vila · Ät bra · Lägg upp trasén · Sov tidigt'},
    {'week':41,'dow':5,'type':'race','km':21, 'title':'TÄVLING — Halvmaraton sub 1:20','detail':'MÅL: 1:19:59 · Pace: 3:47/km · Km 1–5: 3:50/km (varm upp) · Km 6–18: 3:47/km · Km 19–21: ge allt · Lycka till!'},
]

def seed_plan():
    """Fyll plan_sessions från PLAN_SEED om tabellen är tom (för user_id=1)."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM plan_sessions WHERE user_id=1')
            if cur.fetchone()[0] > 0:
                return  # redan seedat
            for s in PLAN_SEED:
                cur.execute('''INSERT INTO plan_sessions
                    (week, dow, type, km, title, detail, status, original_week, original_dow, user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s,%s)''',
                    (s['week'], s['dow'], s['type'], s['km'],
                     s['title'], s['detail'], s['week'], s['dow'], 1))
        conn.commit()
    print(f'Plan seedat: {len(PLAN_SEED)} pass')

def reseed_plan():
    """Ersätt alla planerade pass med ny PLAN_SEED. Behåller completed/missed/skipped som historik."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_sessions WHERE status = 'planned' AND user_id=1")
            for s in PLAN_SEED:
                cur.execute('''INSERT INTO plan_sessions
                    (week, dow, type, km, title, detail, status, original_week, original_dow, user_id)
                    VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s,%s)''',
                    (s['week'], s['dow'], s['type'], s['km'],
                     s['title'], s['detail'], s['week'], s['dow'], 1))
        conn.commit()
    print(f'Plan omseedad: {len(PLAN_SEED)} nya pass')

if not APP_TESTING:
    try:
        seed_plan()
    except Exception:
        logger.exception('Plan seed failed', extra={'event': 'plan.seed_failed'})


# ─────────────────────────────────────────────
# PLAN API
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# MÅLTEMPON — härledda ur mätt tröskel, godkänns av användaren
# ─────────────────────────────────────────────

def _pace_context(user_id):
    """Tröskelankare, tempoband och målets rimlighet för en användare."""
    lt_pace = None
    executions = []
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT lactate_pace FROM metric_history
                    WHERE user_id=%s AND lactate_pace IS NOT NULL
                    ORDER BY date DESC LIMIT 1''', (user_id,))
                row = cur.fetchone()
                lt_pace = row[0] if row else None
                cur.execute('''SELECT execution FROM plan_sessions
                    WHERE user_id=%s AND execution IS NOT NULL
                    ORDER BY week DESC, dow DESC LIMIT 10''', (user_id,))
                executions = [r[0] for r in cur.fetchall() if r[0]]
    except Exception as e:
        print('Tempokontext kunde inte läsas:', e)

    anchor = pace_progression.derive_anchor(lt_pace_sec=lt_pace, executions=executions)

    goal_pace = None
    feasibility = None
    try:
        goal = get_user_goal(user_id)
        if goal:
            goal_pace = pace_progression.parse_goal_pace(
                goal.get('goal_title'), goal.get('secondary_goal'))
    except Exception as e:
        print('Målets tempo kunde inte tolkas:', e)
    if goal_pace and anchor.get('ltPaceSec'):
        feasibility = pace_progression.goal_feasibility(goal_pace['paceSec'], anchor['ltPaceSec'])

    bands = {}
    if anchor.get('ltPaceSec'):
        for kind in ('interval', 'threshold', 'race', 'long', 'easy'):
            band = pace_progression.target_band(kind, anchor['ltPaceSec'])
            if band:
                bands[kind] = band

    return {'anchor': anchor, 'bands': bands, 'goalPace': goal_pace, 'goalFeasibility': feasibility}


def _upcoming_run_sessions(user_id, days=14):
    """Planerade löppass framåt som kan få ett nytt måltempo."""
    today = date.today()
    wanted = set()
    for offset in range(0, days + 1):
        day = today + timedelta(days=offset)
        wanted.add(_iso_week_dow(day))
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''SELECT id, week, dow, type, km, title, detail
                FROM plan_sessions
                WHERE user_id=%s AND status='planned' AND type IN ('run','easy','race')
                ORDER BY week, dow''', (user_id,))
            rows = cur.fetchall()
    return [dict(r) for r in rows if (r['week'], r['dow']) in wanted]


def generate_pace_proposals(user_id):
    """Låt AI:n föreslå måltempo per pass och validera varje förslag.

    Inget skrivs till planen här — förslagen sparas som väntande och kräver
    ett uttryckligt godkännande.
    """
    context = _pace_context(user_id)
    anchor_sec = (context['anchor'] or {}).get('ltPaceSec')
    if not anchor_sec:
        return {'proposals': 0, 'error': 'no_anchor',
                'message': 'Ingen tröskeldata att räkna på ännu.'}

    sessions = _upcoming_run_sessions(user_id)
    if not sessions:
        return {'proposals': 0, 'message': 'Inga planerade löppass framåt.'}
    if not llm_available():
        return {'proposals': 0, 'error': 'ai_unavailable',
                'message': 'AI-tjänsten är inte konfigurerad.'}

    session_lines = []
    for item in sessions:
        current = session_analysis.parse_pace_target(item['detail'])
        kind = session_analysis.classify_session(item)
        session_lines.append({
            'id': item['id'],
            'title': item['title'],
            'detail': item['detail'],
            'km': float(item['km'] or 0),
            'kind': kind,
            'currentTargetPace': current['text'] if current else None,
        })

    prompt = f"""{pace_progression.describe_anchor(context['anchor'], context['goalFeasibility'])}

{_recent_execution_block(user_id)}

UPCOMING SESSIONS THAT NEED A TARGET PACE:
{json.dumps(session_lines, ensure_ascii=False, indent=2)}

Propose the pace each session should actually be run at, given the athlete's
measured threshold and how recent sessions were executed. Stay inside the band
for that session's kind. Where the current target is already right, propose the
same value.

Respond ONLY with JSON:
{{"proposals": [{{"id": <session id>, "paceSec": <seconds per km as an integer>, "rationale": "one short sentence in Swedish"}}]}}"""

    try:
        raw = call_llm(prompt, max_tokens=1500).strip().replace('```json', '').replace('```', '').strip()
        proposed = (json.loads(raw) or {}).get('proposals') or []
    except Exception as e:
        print('Tempoförslag misslyckades:', e)
        return {'proposals': 0, 'error': 'ai_failed',
                'message': 'AI-tjänsten kunde inte svara.'}

    by_id = {item['id']: item for item in sessions}
    kinds = {item['id']: item['kind'] for item in session_lines}
    stored = 0
    now = time.time()

    with db() as conn:
        with conn.cursor() as cur:
            # Tidigare obeslutade förslag ersätts — annars staplas de på varandra.
            cur.execute("DELETE FROM plan_proposals WHERE user_id=%s AND status='pending'", (user_id,))
            for item in proposed:
                session = by_id.get(item.get('id'))
                if not session:
                    continue
                kind = kinds.get(session['id'], 'run')
                verdict = pace_progression.validate_proposal(kind, item.get('paceSec'), anchor_sec)
                if not verdict.get('paceSec'):
                    continue
                band = context['bands'].get(kind) or {}
                old_target = session_analysis.parse_pace_target(session['detail'])
                old_pace = old_target['lowSec'] if old_target else None
                if old_pace and abs(old_pace - verdict['paceSec']) < 3:
                    continue  # redan rätt — inget att godkänna
                new_detail = session_analysis.replace_pace(
                    session['detail'], verdict['paceSec'],
                    band.get('highSec') if old_target and old_target['lowSec'] != old_target['highSec'] else None)
                cur.execute('''INSERT INTO plan_proposals
                    (user_id, session_id, status, kind, old_detail, new_detail,
                     old_pace_sec, new_pace_sec, validation, reason, rationale, anchor, created_at)
                    VALUES (%s,%s,'pending',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (user_id, session['id'], kind, session['detail'], new_detail,
                     old_pace, verdict['paceSec'], verdict['status'], verdict['reason'],
                     str(item.get('rationale') or '')[:400],
                     psycopg2.extras.Json(context['anchor']), now))
                stored += 1
        conn.commit()
    return {'proposals': stored, 'anchor': context['anchor']}


@app.get('/api/plan/pace-proposals')
def list_pace_proposals():
    context = _pace_context(uid())
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''SELECT p.*, s.title, s.week, s.dow
                FROM plan_proposals p JOIN plan_sessions s ON s.id = p.session_id
                WHERE p.user_id=%s AND p.status='pending'
                ORDER BY s.week, s.dow''', (uid(),))
            rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        row['oldPace'] = session_analysis.format_pace(row['old_pace_sec'])
        row['newPace'] = session_analysis.format_pace(row['new_pace_sec'])
    return jsonify({'proposals': rows, **context})


@app.post('/api/plan/pace-proposals/generate')
def create_pace_proposals():
    try:
        return jsonify(generate_pace_proposals(uid()))
    except Exception as e:
        return _server_error(e, 'pace.generate_failed',
                             message='Tempoförslagen kunde inte tas fram.')


@app.post('/api/plan/pace-proposals/decide')
def decide_pace_proposals():
    """Godkänn eller avfärda förslag. Först vid godkännande ändras planen."""
    data = request.json or {}
    decision = 'approved' if data.get('decision') == 'approve' else 'rejected'
    ids = data.get('ids')
    now = time.time()
    applied = 0

    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if ids:
                cur.execute('''SELECT * FROM plan_proposals
                    WHERE user_id=%s AND status='pending' AND id = ANY(%s)''',
                    (uid(), [int(i) for i in ids]))
            else:
                cur.execute("""SELECT * FROM plan_proposals
                    WHERE user_id=%s AND status='pending'""", (uid(),))
            rows = cur.fetchall()

        with conn.cursor() as cur:
            for row in rows:
                if decision == 'approved':
                    # Originaltexten bevaras första gången den skrivs om.
                    cur.execute('''UPDATE plan_sessions
                        SET detail = %s,
                            detail_original = COALESCE(detail_original, detail),
                            modified_at = %s
                        WHERE id = %s AND user_id = %s''',
                        (row['new_detail'], now, row['session_id'], uid()))
                    applied += 1
                cur.execute('''UPDATE plan_proposals SET status=%s, decided_at=%s
                    WHERE id=%s AND user_id=%s''', (decision, now, row['id'], uid()))
        conn.commit()
    return jsonify({'ok': True, 'decision': decision, 'applied': applied, 'count': len(rows)})


@app.get('/api/plan')
def get_plan():
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT * FROM plan_sessions WHERE user_id=%s ORDER BY week, dow', (uid(),))
            rows = cur.fetchall()
    sessions = [dict(r) for r in rows]
    try:
        _enrich_strength_plan(sessions, uid())
    except Exception as exc:
        print('Strength progression enrichment error:', exc)
    return jsonify({'sessions': sessions})

@app.patch('/api/plan/<int:session_id>')
def update_session(session_id):
    data = request.json or {}
    allowed = {'status','week','dow','title','detail','km','ai_note','type'}
    fields = {k: v for k, v in data.items() if k in allowed}
    if 'type' in fields:
        session_type = _valid_session_type(fields['type'])
        if not session_type:
            return jsonify({'error': f"Ogiltig passtyp — tillåtna: {', '.join(PLAN_SESSION_TYPES)}"}), 400
        fields['type'] = session_type
    if not fields:
        return jsonify({'error': 'No valid fields'}), 400
    fields['modified_at'] = time.time()
    set_clause = ', '.join(f'{k} = %s' for k in fields)
    vals = list(fields.values()) + [session_id, uid()]
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(f'UPDATE plan_sessions SET {set_clause} WHERE id = %s AND user_id = %s', vals)
        conn.commit()
    return jsonify({'ok': True})


# ─────────────────────────────────────────────
# GENERERA NYTT SCHEMA FRÅN MÅLET
# ─────────────────────────────────────────────
def _sanitize_generated_sessions(raw_sessions, start_week, start_dow, end_week):
    """Validera AI-genererade pass: bara dagar från idag t.o.m. end_week,
    max ett pass per dag, kända typer och rimliga värden."""
    out = {}
    for s in raw_sessions or []:
        if not isinstance(s, dict):
            continue
        try:
            week = int(s.get('week'))
            dow = int(s.get('dow'))
        except (TypeError, ValueError):
            continue
        if not (1 <= week <= 53 and 0 <= dow <= 6):
            continue
        if week < start_week or (week == start_week and dow < start_dow) or week > end_week:
            continue
        session_type = _valid_session_type(s.get('type'))
        title = str(s.get('title') or '').strip()[:80]
        if not session_type or not title:
            continue
        try:
            km = max(0.0, min(60.0, float(s.get('km') or 0)))
        except (TypeError, ValueError):
            km = 0.0
        out.setdefault((week, dow), {
            'week': week, 'dow': dow, 'type': session_type, 'km': km,
            'title': title, 'detail': str(s.get('detail') or '').strip()[:200],
        })
    return [out[key] for key in sorted(out)]


def _recent_training_summary(user_id):
    """Volym och frekvens senaste 4 veckorna — tål saknad data (nya användare)."""
    summary = {'weekly_km': [], 'sessions_per_week': 0, 'longest_run_km': 0, 'vo2max': None}
    try:
        start = (date.today() - timedelta(days=28)).isoformat()
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT date, type, distance FROM activities
                               WHERE user_id=%s AND date >= %s''', (user_id, start))
                rows = cur.fetchall()
                cur.execute('''SELECT vo2max FROM metric_history
                               WHERE user_id=%s AND vo2max IS NOT NULL
                               ORDER BY date DESC LIMIT 1''', (user_id,))
                vo2 = cur.fetchone()
        run_types = {'running', 'track_running', 'treadmill_running', 'trail_running'}
        weeks = {}
        count = 0
        longest = 0.0
        for raw_date, act_type, distance in rows:
            count += 1
            if (act_type or '') not in run_types:
                continue
            km = (distance or 0) / 1000
            wk = datetime.fromisoformat(str(raw_date)[:10]).date().isocalendar()[1]
            weeks[wk] = weeks.get(wk, 0) + km
            longest = max(longest, km)
        summary['weekly_km'] = [round(weeks[w], 1) for w in sorted(weeks)]
        summary['sessions_per_week'] = round(count / 4, 1)
        summary['longest_run_km'] = round(longest, 1)
        summary['vo2max'] = vo2[0] if vo2 else None
    except Exception as exc:
        print('plan generate: training summary unavailable', exc)
    return summary


@app.post('/api/plan/generate')
def generate_plan_from_goal():
    """Bygg om hela träningsschemat utifrån användarens mål. Historiken
    (completed/missed/skipped) behålls — endast planerade pass från idag
    och framåt ersätts."""
    if not llm_available():
        return _api_error('ai_unavailable', 'AI-tjänsten är inte konfigurerad.', 503)
    try:
        goal = get_user_goal(uid())
    except Exception as e:
        return _server_error(e, 'plan_generate.goal_failed', message='Målet kunde inte hämtas.')
    if not goal:
        return _api_error('goal_required', 'Sätt ditt träningsmål först — schemat byggs utifrån det.', 400)

    today = date.today()
    cur_week = today.isocalendar()[1]
    cur_dow = today.weekday()
    deadline = None
    if goal.get('goal_deadline'):
        try:
            deadline = date.fromisoformat(str(goal['goal_deadline']))
        except ValueError:
            deadline = None
    if deadline and deadline > today:
        weeks_ahead = max(4, min(16, round((deadline - today).days / 7)))
    else:
        weeks_ahead = 10
    end_week = min(52, cur_week + weeks_ahead)  # årsskifte: planen kapas vid v52

    fitness = _recent_training_summary(uid())
    try:
        library = build_default_recommendations(_strength_progression_history(uid()), limit=8)
    except Exception:
        library = []
    library_json = json.dumps([
        {'exercise': r.get('exercise'), 'prescription': r.get('prescription')}
        for r in library
    ], ensure_ascii=False)

    goal_line = goal['goal_title']
    if goal.get('goal_deadline'):
        goal_line += f" · deadline {goal['goal_deadline']}"
    if goal.get('current_best'):
        goal_line += f" · nuvarande bästa {goal['current_best']}"

    prompt = f"""You are a personal running and strength coach. Build a complete training plan as JSON. All text values must be in Swedish.

GOAL: {goal_line}
SECONDARY GOAL: {goal.get('secondary_goal') or 'none stated'}

TODAY: {today.isoformat()} · ISO week {cur_week}, weekday {cur_dow} (0=Monday)
PLAN WINDOW: from today through ISO week {end_week} ({end_week - cur_week} weeks ahead)

CURRENT FITNESS:
- Running volume per ISO week, last 4 weeks: {fitness['weekly_km'] or 'no data'} km
- Activities per week: {fitness['sessions_per_week']} · Longest recent run: {fitness['longest_run_km']} km
- VO2max: {fitness['vo2max'] or 'unknown'}
- Strength exercises with verified working prescriptions: {library_json or 'none logged'}

RULES:
- "week" is the ISO week number, "dow" is 0-6 (0=Monday). Use only weeks {cur_week}-{end_week}, and in week {cur_week} only days >= {cur_dow}.
- At most one session per day. Days without a session are rest days automatically — only add an explicit "rest" session when the rest itself matters (e.g. race week).
- Types: "run" (quality/intervals/tempo), "easy" (Z2/recovery), "lift" (strength), "race", "rest".
- Start from the athlete's CURRENT weekly volume and build gradually, max ~10% per week, with a lighter recovery week roughly every fourth week.
- If the goal is a race with a deadline, add a taper and place a "race" session on the goal date.
- Include about 2 "lift" sessions per week unless the goal clearly says otherwise. In their detail, name concrete exercises with sets×reps (e.g. "Knäböj 5×5 · Bänkpress 4×6") so the weight engine can attach loads. Do NOT write kg values.
- "detail" is concise workout instructions, max 140 characters, in Swedish.

Return ONLY this JSON, no other text:
{{"coaching_notes": "<3-5 Swedish sentences about how the plan is structured>",
  "sessions": [{{"week": <int>, "dow": <int>, "type": "<run|easy|lift|race|rest>", "km": <float>, "title": "<Swedish>", "detail": "<Swedish>"}}]}}"""

    try:
        text = call_llm(prompt, max_tokens=8000, timeout=120).strip().replace('```json', '').replace('```', '').strip()
        result = json.loads(text)
    except Exception as e:
        return _server_error(e, 'plan_generate.llm_failed', status=502, code='ai_provider_error',
                             message='Coachen kunde inte generera planen. Försök igen.')

    sessions = _sanitize_generated_sessions(result.get('sessions'), cur_week, cur_dow, end_week)
    if len(sessions) < 5:
        return _server_error(ValueError(f'only {len(sessions)} valid sessions'),
                             'plan_generate.invalid_plan', status=502, code='ai_plan_invalid',
                             message='Coachen gav en ogiltig plan. Försök igen.')

    coaching_notes = str(result.get('coaching_notes') or '')[:1000]
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''DELETE FROM plan_sessions
                               WHERE user_id=%s AND status='planned'
                                 AND (week > %s OR (week = %s AND dow >= %s))''',
                            (uid(), cur_week, cur_week, cur_dow))
                removed = cur.rowcount
                for s in sessions:
                    cur.execute('''INSERT INTO plan_sessions
                        (week, dow, type, km, title, detail, status, original_week, original_dow, ai_note, modified_at, user_id)
                        VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s,%s,%s,%s)''',
                        (s['week'], s['dow'], s['type'], s['km'], s['title'], s['detail'],
                         s['week'], s['dow'], 'Genererad från ditt mål', time.time(), uid()))
            conn.commit()
    except Exception as e:
        return _server_error(e, 'plan_generate.apply_failed', message='Planen kunde inte sparas.')

    logger.info('Plan generated from goal', extra={
        'event': 'plan.generated',
        'request_id': _request_id(),
        'user_id': uid(),
        'sessions': len(sessions),
        'replaced': removed,
    })
    return jsonify({'ok': True, 'sessions': len(sessions), 'replaced': removed,
                    'coachingNotes': coaching_notes, 'endWeek': end_week})


# ─────────────────────────────────────────────
# AKTIVITETSMATCHNING
# ─────────────────────────────────────────────
def _iso_week_dow(d):
    """Returnera (iso_week, dow_0mon) för ett date-objekt."""
    iso = d.isocalendar()
    return iso[1], iso[2] - 1  # dow: 0=mån

def _latest_lactate_hr(user_id):
    """Senast kända tröskelpuls — används för att fånga för hårda lugna pass."""
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT lactate_hr FROM metric_history
                    WHERE user_id=%s AND lactate_hr IS NOT NULL
                    ORDER BY date DESC LIMIT 1''', (user_id,))
                row = cur.fetchone()
        return int(row[0]) if row and row[0] else None
    except Exception:
        return None


def _run_activity_laps(activity, username):
    """Hämta varvdata för ett löppass. Nätverksanrop — bara när det behövs."""
    activity_id = activity.get('activityId') or activity.get('id')
    if not activity_id or not username:
        return []
    try:
        client = get_garmin(username)
        return session_analysis.normalize_laps(client.get_activity_splits(activity_id))
    except Exception as e:
        print('Varvhämtning misslyckades:', e)
        return []


def _build_session_execution(planned, acts, day, user_id, username=None,
                             strength_history=None, lactate_hr=None):
    """Analysera HUR ett genomfört pass kördes, inte bara ATT det gjordes.

    Returnerar ett dict som sparas i plan_sessions.execution och matas både
    till AI-prompterna och till gränssnittet.
    """
    run_types  = {'running', 'track_running', 'treadmill_running', 'trail_running', 'virtual_run', 'street_running', 'obstacle_course_racing'}
    bike_types = {'cycling', 'road_biking', 'gravel_cycling', 'indoor_cycling', 'mountain_biking', 'virtual_ride', 'e_biking', 'cyclocross', 'bmx'}
    lift_types = {'strength_training', 'fitness_equipment', 'weight_training'}

    if planned['type'] in ('run', 'easy', 'race', 'interval', 'threshold', 'long'):
        runs = [a for a in acts
                if (a.get('activityType') or {}).get('typeKey', '') in run_types]
        if not runs:
            return None
        # Dagens huvudpass = det längsta; uppvärmningsjoggar ska inte vinna.
        activity = max(runs, key=lambda a: a.get('distance') or 0)
        kind = session_analysis.classify_session(planned, activity)
        # Varvdata kostar ett extra API-anrop, så den hämtas bara när den
        # faktiskt tillför något (intervaller) eller för pulsdrift på långpass.
        laps = _run_activity_laps(activity, username) if kind in ('interval', 'long', 'threshold') else []
        analysis = session_analysis.analyze_run(activity, laps, planned, lactate_hr=lactate_hr)
        analysis['activityId'] = activity.get('activityId') or activity.get('id')
        analysis['activityName'] = activity.get('activityName')
        analysis['discipline'] = 'run'
        analysis['headline'] = session_analysis.headline_for(analysis)
        return analysis

    if planned['type'] in ('bike', 'cycling'):
        bikes = [a for a in acts
                 if (a.get('activityType') or {}).get('typeKey', '') in bike_types]
        if not bikes:
            return None
        activity = max(bikes, key=lambda a: a.get('distance') or 0)
        dist_km = round((activity.get('distance') or 0) / 1000, 1)
        dur_min = round((activity.get('duration') or 0) / 60)
        analysis = {
            'discipline': 'bike',
            'activityId': activity.get('activityId') or activity.get('id'),
            'activityName': activity.get('activityName') or 'Cykelpass',
            'distanceKm': dist_km,
            'durationMin': dur_min,
            'avgHr': activity.get('averageHR'),
            'headline': f"Cykelpass {dist_km} km genomfört",
        }
        return analysis

    if planned['type'] in ('lift', 'strength'):
        history = strength_history or []
        day_str = day.isoformat()
        logged = [entry for entry in history if entry.get('date') == day_str]
        if not logged:
            return None
        try:
            recommendations = build_strength_recommendations(
                planned.get('detail', ''), history, before_date=day_str)
            if not recommendations:
                recommendations = build_default_recommendations(
                    history, before_date=day_str, limit=6)
        except (TypeError, ValueError):
            recommendations = []
        analysis = session_analysis.analyze_strength(logged, recommendations)
        analysis['discipline'] = 'strength'
        analysis['headline'] = session_analysis.headline_for(analysis)
        return analysis

    return None


def match_activities_to_plan(days_back=7, user_id=1, username=None):
    """
    Jämför Garmin-aktiviteter mot planerade pass de senaste N dagarna.
    Markerar pass som completed eller missed. Re-utvärderar även 'missed'
    (om en aktivitet synkats i efterhand) men rör aldrig skipped/rescheduled.
    Idag hoppas över eftersom dagen inte är slut. Körs efter varje synk.

    Endast löppass och cykelpass (samt styrka för styrkepass) räknas som
    att ett schemalagt pass är genomfört. Promenader, simning, vardagsmotion
    räknas inte mot planerade löp-/cykelpass.
    """
    today = date.today()
    run_types  = {'running', 'track_running', 'treadmill_running', 'trail_running', 'virtual_run', 'street_running', 'obstacle_course_racing'}
    bike_types = {'cycling', 'road_biking', 'gravel_cycling', 'indoor_cycling', 'mountain_biking', 'virtual_ride', 'e_biking', 'cyclocross', 'bmx'}
    lift_types = {'strength_training', 'fitness_equipment', 'weight_training'}

    lactate_hr = _latest_lactate_hr(user_id)
    try:
        strength_history = _strength_progression_history(user_id)
    except Exception as e:
        print('Styrkehistorik kunde inte läsas:', e)
        strength_history = []

    with db() as conn:
        for i in range(0, days_back + 1):
            day = today - timedelta(days=i)
            wk, dw = _iso_week_dow(day)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Redan genomförda pass tas med när de saknar utvärdering, så
                # att analysen kan fyllas i i efterhand. Statusen rörs inte.
                cur.execute('''SELECT * FROM plan_sessions
                    WHERE week = %s AND dow = %s AND user_id = %s
                      AND (status IN ('planned','missed','skipped')
                           OR (status = 'completed' AND execution IS NULL))''',
                    (wk, dw, user_id))
                planned = cur.fetchall()
                if not planned:
                    continue
                cur.execute('''SELECT raw FROM activities
                    WHERE date >= %s AND date < %s AND user_id = %s''',
                    (day.isoformat(), (day + timedelta(days=1)).isoformat(), user_id))
                acts = [r['raw'] for r in cur.fetchall()]

            did_run  = any((a.get('activityType') or {}).get('typeKey', '') in run_types for a in acts)
            did_bike = any((a.get('activityType') or {}).get('typeKey', '') in bike_types for a in acts)
            did_lift = any((a.get('activityType') or {}).get('typeKey', '') in lift_types for a in acts)

            with conn.cursor() as cur:
                for p in planned:
                    if p['status'] == 'completed':
                        new_status = 'completed'
                    else:
                        p_type = str(p.get('type') or '').lower()
                        if p_type in ('run', 'easy', 'race', 'interval', 'threshold', 'long'):
                            completed = did_run
                        elif p_type in ('bike', 'cycling'):
                            completed = did_bike
                        elif p_type in ('lift', 'strength'):
                            completed = did_lift
                        elif p_type == 'rest':
                            completed = True  # vilodag räknas alltid som genomförd
                        else:
                            completed = False

                        if completed:
                            new_status = 'completed'
                        elif i == 0:
                            continue
                        elif p['status'] == 'skipped':
                            continue
                        else:
                            new_status = 'missed'
                    if new_status != p['status']:
                        cur.execute('''UPDATE plan_sessions SET status = %s, modified_at = %s
                            WHERE id = %s AND user_id = %s''', (new_status, time.time(), p['id'], user_id))

                    if new_status == 'completed' and not p.get('execution'):
                        try:
                            execution = _build_session_execution(
                                p, acts, day, user_id, username=username,
                                strength_history=strength_history, lactate_hr=lactate_hr)
                        except Exception as e:
                            print(f"Passutvärdering misslyckades för pass {p['id']}:", e)
                            execution = None
                        if execution:
                            cur.execute('''UPDATE plan_sessions SET execution = %s
                                WHERE id = %s AND user_id = %s''',
                                (psycopg2.extras.Json(execution), p['id'], user_id))
        conn.commit()
    print(f'Activity matching complete (last {days_back} days)')


def _parse_garmin_epoch(value, assume_utc=False):
    """Return epoch seconds for Garmin timestamps in numeric or string form."""
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        # Garmin payloads can use either seconds or milliseconds.
        return float(value) / 1000.0 if value > 100000000000 else float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.replace('.', '', 1).isdigit():
            return _parse_garmin_epoch(float(text), assume_utc=assume_utc)
        try:
            normalized = text.replace('Z', '+00:00')
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None and assume_utc:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _activity_local_date(raw):
    for key in ('startTimeLocal', 'startTimeGMT', 'calendarDate'):
        val = raw.get(key)
        if val:
            return str(val)[:10]
    return None


def _activity_start_epoch(raw):
    return (
        _parse_garmin_epoch(raw.get('startTimeLocal')) or
        _parse_garmin_epoch(raw.get('beginTimestamp'), assume_utc=True) or
        _parse_garmin_epoch(raw.get('startTimeGMT'), assume_utc=True)
    )


def link_manual_exercises_to_activity(session_id, user_id):
    """Attach date-keyed exercises to one concrete Garmin strength activity.
    Strikt per användare — både aktiviteten och övningarna måste ägas av user_id."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT raw FROM activities WHERE id=%s AND type = ANY(%s) AND user_id=%s",
                        (session_id, list(STRENGTH_TYPES), user_id))
            row = cur.fetchone()
            if not row:
                return 0
            local = _activity_local_date(row[0])
            if not local:
                return 0
            cur.execute("UPDATE strength_exercises SET session_id=%s WHERE session_id=%s AND user_id=%s",
                        (str(session_id), local, user_id))
            linked = cur.rowcount
        conn.commit()
    if linked:
        print(f'Strength: länkade {linked} manuella övningar till Garmin-pass {session_id}')
    return linked


def link_manual_exercises_to_activities(user_id):
    """Koppla manuellt loggade övningar (sparade under datum-nyckel 'YYYY-MM-DD' i
    Today's workout) till Garmin-styrkepasset som laddats upp samma dag, så de hamnar
    på rätt aktivitet i historiken. Vid flera pass samma dag väljs det som ligger
    närmast övningarnas loggtid. Idempotent — när raderna fått aktivitets-id rörs de ej.
    Strikt per användare så att en användares loggar aldrig länkas till en annans pass."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(r"""
                SELECT session_id, avg(created_at)
                FROM strength_exercises
                WHERE session_id ~ '^\d{4}-\d{2}-\d{2}$' AND user_id = %s
                GROUP BY session_id
            """, (user_id,))
            date_rows = cur.fetchall()
            date_keys = [r[0] for r in date_rows]
            if not date_keys:
                return
            cur.execute("SELECT id, raw FROM activities WHERE type = ANY(%s) AND user_id=%s",
                        (list(STRENGTH_TYPES), user_id))
            strength = cur.fetchall()
    if not strength:
        return
    by_date = {}
    for aid, raw in strength:
        local = _activity_local_date(raw)
        if not local:
            continue
        by_date.setdefault(local, []).append((str(aid), _activity_start_epoch(raw)))

    linked = 0
    with db() as conn:
        with conn.cursor() as cur:
            for dk, avg_created in date_rows:
                cands = by_date.get(dk)
                if not cands:
                    continue  # inget Garmin-pass den dagen än → vänta
                if any(c[1] is not None for c in cands) and avg_created is not None:
                    best = min(cands, key=lambda c: abs((c[1] or 0) - float(avg_created)) if c[1] else float('inf'))
                else:
                    best = cands[0]
                cur.execute("UPDATE strength_exercises SET session_id=%s WHERE session_id=%s AND user_id=%s",
                            (best[0], dk, user_id))
                linked += cur.rowcount
        conn.commit()
    if linked:
        print(f'Strength: länkade {linked} manuella övningar till Garmin-pass')


# ─────────────────────────────────────────────
# BELASTNING (STRAIN) OCH PASSOMDÖMEN
# ─────────────────────────────────────────────
# Garmins råa träningsbelastning säger ingenting i sig — 180 är en hård dag för
# en löpare och en tisdag för en annan. Allt här vägs därför mot personens egen
# kroniska belastning. Se strain_analysis.py för själva matten.

def _recent_activities(user_id, days=30):
    """Aktiviteter från de senaste dygnen, i det format strain_analysis vill ha."""
    start = (date.today() - timedelta(days=days)).isoformat()
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''SELECT id, name, date, type, distance, raw
                FROM activities WHERE date >= %s AND user_id=%s ORDER BY date''',
                        (start, user_id))
            return [dict(row) for row in cur.fetchall()]


def _load_context(user_id):
    """Kronisk belastning och ACWR från cachen — ingen extra Garmin-runda i synken."""
    row = get_cache('training_load', user_id)
    payload = (row[0] if row else None) or {}
    return payload.get('chronic'), payload.get('ratio')


def _recent_recovery(user_id):
    """CNS-beredskapen, sömnlängden och natten siffrorna kommer från.

    Samma källa som mobilwidgeten: hälsocachen först, annars den senaste
    sparade natten. Kolumnen health_history.readiness ser ut att duga men
    fylls aldrig av collect_health_history — den är NULL i varje rad.

    Datumet följer med eftersom Garmin publicerar dagens sömn först en stund
    efter uppvaknandet. Fram till dess är siffrorna gårdagens, och den som
    presenterar dem som 'i natt' talar osanning."""
    try:
        row = get_cache('health', user_id)
        health = (row[0] if row else None) or {}
        if not health:
            health = latest_health_snapshot(user_id, date.today().isoformat()) or {}
        if not health:
            return None, None, None
        sleep_sec = (health.get('sleep') or {}).get('totalSec')
        return (_cns_score_from_health(health),
                round(sleep_sec / 3600, 2) if sleep_sec else None,
                health_sleep_source_date(health))
    except Exception as e:
        print('Kunde inte läsa återhämtningsdata:', e)
        return None, None, None


def _adaptive_health_context(user_id):
    """Färska dagsvärden plus en robust personlig 28-dagarsbaslinje."""
    today = date.today()
    row = get_cache('health', user_id)
    health = (row[0] if row else None) or {}
    if not health:
        health = latest_health_snapshot(user_id, today.isoformat()) or {}

    sleep = health.get('sleep') or {}
    hrv = health.get('hrv') or {}
    resting_hr = health.get('restingHR') or {}
    sleep_sec = sleep.get('totalSec')
    baseline = {'hrv': None, 'resting_hr': None, 'sleep_hours': None}
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT AVG(hrv_avg), AVG(resting_hr), AVG(sleep_hours)
                    FROM health_history
                    WHERE user_id=%s AND date >= %s AND date < %s''',
                    (user_id, today - timedelta(days=28), today))
                values = cur.fetchone() or (None, None, None)
        baseline = {
            'hrv': float(values[0]) if values[0] is not None else None,
            'resting_hr': float(values[1]) if values[1] is not None else None,
            'sleep_hours': float(values[2]) if values[2] is not None else None,
        }
    except Exception:
        logger.exception('Adaptive health baseline failed', extra={
            'event': 'adaptive.baseline_failed', 'user_id': user_id})

    return {
        'sleep_hours': round(float(sleep_sec) / 3600, 2) if sleep_sec else None,
        'sleep_stale': health_sleep_is_fallback(health),
        'sleep_source_date': health_sleep_source_date(health),
        'readiness': (health.get('readiness') or {}).get('score'),
        'hrv': hrv.get('lastNightAvg'),
        'hrv_baseline': hrv.get('weeklyAvg') or baseline['hrv'],
        'resting_hr': resting_hr.get('value'),
        'resting_hr_baseline': resting_hr.get('sevenDayAvg') or baseline['resting_hr'],
        'baseline_days': 28,
    }


def _adaptive_today_session(user_id):
    today = date.today()
    week, dow = _iso_week_dow(today)
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''SELECT id,week,dow,type,km,title,detail,status
                FROM plan_sessions
                WHERE user_id=%s AND week=%s AND dow=%s AND status='planned'
                ORDER BY id DESC LIMIT 1''', (user_id, week, dow))
            row = cur.fetchone()
    if not row:
        return None
    result = dict(row)
    result['km'] = float(result['km']) if result.get('km') is not None else None
    result['kind'] = session_analysis.classify_session(result)
    result['is_quality'] = result['kind'] in ('threshold', 'interval', 'race')
    if result.get('km'):
        pace_minutes = 7 if result['kind'] in ('easy', 'long') else 6
        result['estimated_minutes'] = max(20, round(result['km'] * pace_minutes))
    elif result.get('type') in ('lift', 'strength'):
        result['estimated_minutes'] = 60
    else:
        result['estimated_minutes'] = 45
    return result


def build_adaptive_snapshot(user_id):
    """Bygg samma strukturerade underlag varje gång, så beslut kan spelas om."""
    today = date.today()
    activities = _recent_activities(user_id, days=30)
    chronic, ratio = _load_context(user_id)
    reference = strain_analysis.reference_load(activities, today=today, chronic=chronic)
    series = strain_analysis.strain_series(activities, today=today, days=4, reference=reference)
    past_three = series[:-1]
    hard_days = sum(1 for point in past_three if point.get('strain', 0) >= strain_analysis.HIGH_STRAIN)
    return {
        'date': today.isoformat(),
        'session': _adaptive_today_session(user_id),
        'health': _adaptive_health_context(user_id),
        'checkin': ADAPTIVE_PLAN_STORE.get_checkin(user_id, today),
        # Gårdagens val sparas som förklarande kontext. Motorn dubbelräknar dem
        # inte mot dagens HRV/sömn innan personens egna samband har validerats.
        'lifestyle': LIFESTYLE_STORE.get(user_id, today - timedelta(days=1)),
        'recent_feedback': _recent_activity_feedback(user_id, days=14, limit=5),
        'load': {
            'hard_days_last_3': hard_days,
            'chronic': chronic,
            # Kvoten visas bara som kontext; beslutsmotorn använder den inte som
            # en fristående skaderisk eftersom det saknar vetenskapligt stöd.
            'acute_chronic_ratio': ratio,
            'reference_load': reference,
        },
    }


def generate_adaptive_decision(user_id):
    snapshot = build_adaptive_snapshot(user_id)
    decision = evaluate_adaptive_plan(snapshot)
    stored = ADAPTIVE_PLAN_STORE.save_decision(
        user_id, snapshot['date'], snapshot, decision)
    return {
        'mode': 'live',
        'decisionId': stored['id'],
        'decision': decision,
        'checkin': snapshot['checkin'],
        'lastEvaluatedAt': stored['created_at'],
    }


def generate_coach_briefing(session, health=None, readiness=None, pace_anchor=None):
    """Genererar en strukturerad, handfast och motiverande coachningsbriefing inför dagens pass."""
    if not session:
        return {
            'purpose': 'Aktiv återhämtning och vila för muskeluppbyggnad och nervsystem.',
            'execution': 'Ingen schemalagd träning idag. Njut av vilodagen eller ta en lugn promenad och stretcha lätt.',
            'rpe': 'RPE 1–2/10 (vila)',
            'tips': 'Prioritera bra näring, god sömn och hydrering inför kommande träningspass.',
            'fueling': 'Normal näringsrik kost och ordentligt med vätska.',
        }

    name = session.get('name') or session.get('title') or 'Dagens pass'
    detail = (session.get('detail') or session.get('description') or '').strip()
    km = session.get('km') or session.get('distance')
    kind = str(session.get('kind') or session.get('type') or '').lower()
    full_text = f"{name} {detail}".lower()

    # Hämta måltempon från ankare om de inte redan står i passets detalj
    anchor = (pace_anchor or {}).get('anchor') or {}
    lt_sec = anchor.get('ltPaceSec')
    easy_sec = anchor.get('easyPaceSec')

    def _fmt(s):
        if not s: return None
        m, sec = divmod(int(round(s)), 60)
        return f"{m}:{sec:02d}/km"

    lt_str = _fmt(lt_sec) or "3:55–4:05/km"
    easy_str = _fmt(easy_sec) or "4:50–5:15/km"

    # Analysera passets karaktär
    is_short_intervals = any(x in full_text for x in ('200m', '300m', '400m', '500m', '600m', '800m'))
    is_long_intervals = any(x in full_text for x in ('1000m', '1500m', '2000m', '3000m', 'tusingar', 'tröskel', 'threshold', 'tempo'))
    is_interval = is_short_intervals or is_long_intervals or 'intervall' in full_text or 'fartlek' in full_text or kind in ('interval', 'threshold')
    is_long_run = 'långpass' in full_text or (km and km >= 14) or kind == 'long'
    is_strength = 'styrka' in full_text or 'gym' in full_text or 'lift' in full_text or kind in ('lift', 'strength')
    is_recovery = 'återhämtning' in full_text or 'vila' in full_text or 'lätt' in full_text or kind in ('easy', 'rest')

    # 1. Utförande & Fart: Synka 100% med passets faktiska detaljer och planerade tempon
    if detail:
        execution = detail
        if not execution.endswith('.'):
            execution += '.'
    elif is_interval:
        execution = f"15 min uppvärmning (Zon 1–2) + 3 korta stegringslopp. Huvuddel i tröskeltempo ({lt_str}). 10 min lugn nedjogg."
    elif is_long_run:
        execution = f"Löpning i lugnt och kontrollerat Zon 2-tempo ({easy_str}). Jämn ansträngning hela vägen."
    elif is_strength:
        execution = "Styrketräning med fokus på knäböj, utfall, enbens marklyft, tåhävningar och core-planka. 3–4 set med god form."
    else:
        execution = f"Genomför passet med kontrollerad ansträngning i behagligt distanstempo ({easy_str})."

    # 2. Syfte, RPE och Coachtips
    if is_short_intervals:
        purpose = "Maximal syreupptagningsförmåga (VO2max), snabbhet, anaerob kapacitet och löpekonomi i hög fart."
        rpe = "RPE 8–9/10 (mycket ansträngande – hög hastighet)"
        tips = "Gå inte ut för hårt på de första repetitionerna. Håll jämn fart genom alla repetitioner och utnyttja vilan för att återhämta pulsen."
        fueling = "Lätt kolhydratmellanmål (t.ex. banan/havregrynsgröt) 1.5–2h innan passet. Drick 4–5 dl vatten."
    elif is_long_intervals:
        purpose = "Höja din laktattröskel (LT2) och träna kroppen på att transportera bort mjölksyra i tävlingsfart."
        rpe = "RPE 7.5–8.5/10 (kontrollerat ansträngande – inte maxning)"
        tips = "Fokusera på avslappnad överkropp och hög stegfrekvens (175–182 spm). Sista repet ska gå minst lika snabbt som det första."
        fueling = "Lätt kolhydratmellanmål 1.5–2h innan passet. Drick 4–5 dl vätska med elektrolyter."
    elif is_long_run:
        purpose = "Utveckla aerob bas, mitokondrietäthet och fettförbränningseffektivitet under längre duration."
        rpe = "RPE 5–6/10 (konversationstempo – obehindrad andning)"
        tips = "Var disciplinerad i backar och motlut så att pulsen inte rusar in i tröskelzon. Sänk farten vid behov."
        fueling = "Ta med vätska och 1 gel per 40–45 min om passet överstiger 75 minuter. Drick regelbundet."
    elif is_strength:
        purpose = "Stärka höftstabilitet, sätesmuskulatur, bål och fotleder för explosivt frånskjut och skadefrihet."
        rpe = "RPE 7/10 (kvalitet och kontroll, undvik failure)"
        tips = "Styrketräning för löpare handlar om kraftöverföring och ledstabilitet. Prioritera teknik framför tunga vikter."
        fueling = "Inta protein (20–30g) och kolhydrater inom 45 min efter passet för snabb återhämtning."
    elif is_recovery:
        purpose = "Aktiv återhämtning, öka kapillärblodflödet till musklerna och rensa slaggprodukter i Zon 1–2."
        rpe = "RPE 3–4/10 (mycket lätt)"
        tips = "Släpp alla krav på fart – låt pulsen och känslan styra helt. Känns det tungt, sakta ner ytterligare."
        fueling = "Bra hydrering under dagen och näringsrik mat."
    else:
        purpose = "Upprätthålla träningskontinuitet och bygga grundläggande aerob kapacitet."
        rpe = "RPE 5–6/10 (medelansträngande)"
        tips = "Tänk på stolt hållning och att landa med foten under kroppens tyngdpunkt."
        fueling = "Vanlig måltidsordning och bra vätskebalans."

    return {
        'purpose': purpose,
        'execution': execution,
        'rpe': rpe,
        'tips': tips,
        'fueling': fueling,
    }


# Rubriker per åtgärd. Motorn skriver redan en mening själv, men den är
# formulerad som ett förslag ("Flytta kvalitetspasset"). När motorn är dagens
# enda domare ska rubriken vara ett besked, inte ett förslag bland flera.
_TODAY_TONE = {
    'keep':       ('Kör dagens pass', 'good'),
    'reduce':     ('Lätta på dagens pass', 'warn'),
    'reschedule': ('Flytta dagens kvalitetspass', 'warn'),
    'rest':       ('Vila i dag', 'bad'),
    'no_session': ('Ingen träning planerad', 'neutral'),
}


@app.get('/api/today')
def today_view():
    """Dagens enda besked.

    Hela Idag-vyn läser det här svaret. Det är själva poängen: så länge varje
    kort räknade fram sin egen bedömning kunde de säga emot varandra, och det
    gjorde de. Beredskapstalet, beslutet och skälen kommer nu ur samma
    utvärdering, så det finns inget sätt för dem att glida isär.
    """
    try:
        adaptive = generate_adaptive_decision(uid())
    except Exception as exc:
        return _server_error(exc, 'today.evaluate_failed',
                             message='Dagens besked kunde inte räknas ut.')

    decision = adaptive.get('decision') or {}
    action = decision.get('action') or 'no_session'
    fallback_headline, tone = _TODAY_TONE.get(action, (decision.get('headline'), 'neutral'))

    readiness = None
    health = None
    try:
        health = latest_health_snapshot(uid(), date.today().isoformat()) or {}
        readiness = _cns_score_from_health(health)
    except Exception:
        logger.warning('today: beredskap kunde inte läsas',
                       extra={'event': 'today.readiness_failed'})

    pace_ctx = None
    try:
        pace_ctx = _pace_context(uid())
    except Exception:
        pass

    session = decision.get('session')
    briefing = generate_coach_briefing(session, health, readiness, pace_ctx)

    return jsonify({
        'date': decision.get('date'),
        'mode': adaptive.get('mode'),
        'action': action,
        'tone': tone,
        'headline': decision.get('headline') or fallback_headline,
        'summary': fallback_headline,
        'detail': decision.get('detail'),
        'reasons': decision.get('reasons') or [],
        'signals': decision.get('signals') or [],
        'warnings': decision.get('warnings') or [],
        'confidence': decision.get('confidence'),
        'dataQuality': decision.get('dataQuality'),
        'session': decision.get('session'),
        'proposedChange': decision.get('proposedChange'),
        'readiness': readiness,
        'checkin': adaptive.get('checkin') or {},
        'decisionId': adaptive.get('decisionId'),
        'lastEvaluatedAt': adaptive.get('lastEvaluatedAt'),
        'coachBriefing': briefing,
    })


@app.get('/api/adaptive-plan/today')
def adaptive_plan_today():
    try:
        return jsonify(generate_adaptive_decision(uid()))
    except Exception as exc:
        return _server_error(exc, 'adaptive.evaluate_failed',
                             message='Dagens anpassning kunde inte räknas ut.')


@app.post('/api/adaptive-plan/checkin')
def adaptive_plan_checkin():
    try:
        ADAPTIVE_PLAN_STORE.save_checkin(uid(), date.today(), request.json or {})
        return jsonify({'ok': True, **generate_adaptive_decision(uid())})
    except (TypeError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return _server_error(exc, 'adaptive.checkin_failed',
                             message='Incheckningen kunde inte sparas.')


def _lifestyle_date(raw=None):
    if not raw:
        return date.today() - timedelta(days=1)
    try:
        value = date.fromisoformat(str(raw))
    except ValueError as exc:
        raise ValueError('Ogiltigt datum.') from exc
    if value > date.today() or value < date.today() - timedelta(days=365):
        raise ValueError('Datumet måste ligga inom de senaste 365 dagarna.')
    return value


def _lifestyle_insights(user_id):
    since = date.today() - timedelta(days=90)
    return analyze_impacts(LIFESTYLE_STORE.rows_with_outcomes(user_id, since))


@app.get('/api/lifestyle')
def lifestyle_get():
    try:
        log_date = _lifestyle_date(request.args.get('date'))
        return jsonify({'date': log_date.isoformat(),
                        'entry': LIFESTYLE_STORE.get(uid(), log_date),
                        'insights': _lifestyle_insights(uid())})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return _server_error(exc, 'lifestyle.load_failed',
                             message='Livsstilsloggen kunde inte laddas.')


@app.post('/api/lifestyle')
def lifestyle_save():
    try:
        payload = request.json or {}
        log_date = _lifestyle_date(payload.get('date'))
        entry = LIFESTYLE_STORE.save(uid(), log_date, payload)
        return jsonify({'ok': True, 'date': log_date.isoformat(), 'entry': entry,
                        'insights': _lifestyle_insights(uid())})
    except (TypeError, ValueError) as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        return _server_error(exc, 'lifestyle.save_failed',
                             message='Livsstilsloggen kunde inte sparas.')


def _unseen_activity_ids(activities, user_id):
    """Vilka av de hämtade passen vi inte har sett förut."""
    ids = [a.get('activityId') for a in activities or [] if a.get('activityId')]
    if not ids:
        return set()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM activities WHERE user_id=%s AND id = ANY(%s)',
                        (user_id, ids))
            known = {row[0] for row in cur.fetchall()}
    return {i for i in ids if i not in known}


def ingest_activities(activities, user_id=1, announce=True):
    """Spara Garmin-pass och behandla nya pass i samma atomära arbetsflöde.

    Tidigare sparade flera vyer aktiviteter direkt. Då hann nästa synk se dem
    som gamla och notifieringen försvann. All Garmin-import går nu genom denna
    enda ingång, oavsett om den startades av schemat eller av användaren.
    """
    activities = activities or []
    try:
        fresh_ids = _unseen_activity_ids(activities, user_id)
    except Exception as exc:
        logger.exception('Could not identify new activities', extra={
            'event': 'activity.ingest_detection_failed', 'user_id': user_id})
        fresh_ids = set()
    save_activities(activities, user_id)
    if not fresh_ids:
        return fresh_ids
    try:
        record_session_verdicts(fresh_ids, user_id)
    except Exception:
        logger.exception('Activity verdict failed', extra={
            'event': 'activity.verdict_failed', 'user_id': user_id})
    if announce:
        try:
            delivered = notify_new_activities(fresh_ids, user_id)
            logger.info('New activity notification handled', extra={
                'event': 'push.activity_synced', 'user_id': user_id,
                'activities': len(fresh_ids), 'delivered': delivered})
        except Exception:
            logger.exception('Activity notification failed', extra={
                'event': 'push.activity_failed', 'user_id': user_id})
    return fresh_ids


# Ett pass som dyker upp tre dagar sent behover ingen notis - da har du redan
# levt vidare. Fonstret ar snavare an for omdomena av just den anledningen.
ACTIVITY_PUSH_MAX_AGE_HOURS = float(config.get('ACTIVITY_PUSH_MAX_AGE_HOURS', '30'))
ACTIVITY_PUSH_MAX_INDIVIDUAL = 2
GARMIN_SYNC_MINUTES = min(max(int(config.get('GARMIN_SYNC_MINUTES', '15')), 5), 180)


def _activity_started_at(activity):
    """Starttiden som datetime, eller None. Kolumnen ar text ('2026-08-02 17:18:32')."""
    raw = str(activity.get('date') or '')[:19]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _activity_push_line(activity):
    """Kort beskrivning av ett pass: distans och tid, tempo nar det ar en lopning."""
    bits = []
    distance = (activity.get('distance') or 0) / 1000
    raw = activity.get('raw') or {}
    duration = activity.get('duration') or raw.get('duration') or 0
    if distance >= 0.1:
        bits.append(f'{distance:.1f} km'.replace('.', ','))
    if duration:
        minutes = int(duration / 60)
        bits.append(f'{minutes // 60} h {minutes % 60} min' if minutes >= 60
                    else f'{minutes} min')
    if distance >= 0.5 and duration:
        pace = (duration / 60) / distance
        bits.append(f'{int(pace)}:{int((pace % 1) * 60):02d}/km')
    return ' · '.join(bits)


def notify_new_activities(activity_ids, user_id=1):
    """Notis om pass som just synkats in.

    Bara riktigt farska pass, och bara nagra stycken: en backfill eller en
    forsta synk hittar hela historiken, och femtio notiser pa en gang ar ett
    battre satt att fa nagon att sla av notiser an att sla pa dem."""
    if not push_available() or not activity_ids:
        return 0
    wanted = set(activity_ids)
    # Garmin's local timestamps are stored without an offset. Compare them in
    # the same timezone instead of the server's system timezone.
    cutoff = datetime.now(LOCAL_TZ).replace(tzinfo=None) - timedelta(hours=ACTIVITY_PUSH_MAX_AGE_HOURS)
    try:
        candidates = []
        for activity in _recent_activities(user_id, days=3):
            if activity.get('id') not in wanted:
                continue
            started = _activity_started_at(activity)
            if started and started < cutoff:
                continue
            candidates.append(activity)
    except Exception as exc:
        print('passnotis: kunde inte lasa aktiviteter:', exc)
        return 0
    if not candidates:
        return 0

    if len(candidates) > ACTIVITY_PUSH_MAX_INDIVIDUAL:
        total = sum((a.get('distance') or 0) for a in candidates) / 1000
        body = f'{len(candidates)} pass'
        if total >= 0.1:
            body += f', {total:.1f} km totalt'.replace('.', ',')
        send_push(user_id, 'Nya pass synkade', body, url='/', tag='activity-synced')
        return len(candidates)

    sent = 0
    for activity in candidates:
        title = activity.get('name') or 'Nytt pass'
        body = _activity_push_line(activity)
        try:
            verdict = strain_analysis.session_verdict(activity)
            if verdict.get('headline'):
                body = f"{body} · {verdict['headline']}" if body else verdict['headline']
        except Exception as exc:
            print('passnotis: omdome saknas:', exc)
        if not body:
            body = 'Passet finns nu i Trainyze.'
        # Egen tagg per pass sa att tva pass samma dag inte ersatter varandra.
        sent += 1 if send_push(user_id, title, body,
                               url=f'/?activity={activity.get("id")}&source=garmin',
                               tag=f"activity-{activity.get('id')}") else 0
    return sent


def record_session_verdicts(activity_ids, user_id=1, max_age_days=3):
    """Skriv ett omdöme för varje nytt pass från de senaste dygnen.

    Äldre pass som dyker upp vid en första synk hoppas över — ett omdöme om ett
    tre veckor gammalt pass har ingenting att informera. Befintliga omdömen
    skrivs aldrig över; det första skrevs i den kontext som gällde då.
    """
    if not activity_ids:
        return 0
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    wanted = set(activity_ids)
    activities = [a for a in _recent_activities(user_id, days=max_age_days)
                  if a.get('id') in wanted and str(a.get('date') or '')[:10] >= cutoff
                  and strain_analysis.is_judgeable(a)]
    if not activities:
        return 0

    chronic, acwr = _load_context(user_id)
    readiness, sleep_hours, _ = _recent_recovery(user_id)
    reference = strain_analysis.reference_load(_recent_activities(user_id), chronic=chronic)

    written = 0
    with db() as conn:
        with conn.cursor() as cur:
            for activity in activities:
                try:
                    verdict = strain_analysis.session_verdict(
                        activity, reference=reference, acwr=acwr,
                        readiness=readiness, sleep_hours=sleep_hours)
                    # Hela starttiden sparas, inte bara datumet — annars avgör
                    # insättningsordningen vilket av dagens pass som visas.
                    cur.execute('''INSERT INTO session_verdicts
                        (activity_id, user_id, activity_date, verdict, created_at)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (activity_id, user_id) DO NOTHING''',
                        (activity['id'], user_id, str(activity.get('date') or verdict['date'])[:19],
                         json.dumps(verdict), time.time()))
                    written += cur.rowcount
                except Exception as e:
                    print('Passomdöme fel:', e)
        conn.commit()
    if written:
        print(f'Passomdöme: skrev {written} nya')
    return written


@app.get('/api/strain')
def strain_today():
    """Dagens belastning vägd mot vad kroppen normalt tål."""
    try:
        chronic, _ = _load_context(uid())
        readiness, _, _ = _recent_recovery(uid())
        summary = strain_analysis.strain_summary(
            _recent_activities(uid()), readiness=readiness, chronic=chronic)
        return jsonify(summary)
    except Exception as e:
        return _server_error(e, 'strain.failed', message='Belastningen kunde inte beräknas.')


@app.get('/api/session-verdict')
def session_verdicts():
    """De senast bedömda passen, nyast först."""
    try:
        limit = max(1, min(int(request.args.get('limit', 5)), 20))
    except (TypeError, ValueError):
        limit = 5
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT verdict FROM session_verdicts
                    WHERE user_id=%s ORDER BY activity_date DESC, created_at DESC
                    LIMIT %s''', (uid(), limit))
                verdicts = [row[0] for row in cur.fetchall()]
        return jsonify({'verdicts': verdicts, 'latest': verdicts[0] if verdicts else None})
    except Exception as e:
        return _server_error(e, 'session_verdict.failed',
                             message='Passomdömena kunde inte hämtas.')


def run_sync(count=50, username=None, user_id=1):
    """Hämta senaste aktiviteter, spara, rensa cache och matcha mot planen.
    Används av både /api/sync och den återkommande autosynken."""
    if username is None:
        username = list(USERS.keys())[0] if USERS else 'hugo'
    client = get_garmin(username)
    acts = client.get_activities(0, count)
    ingest_activities(acts, user_id)
    try:
        link_manual_exercises_to_activities(user_id)
    except Exception as e:
        print('Strength-länkning fel:', e)
    clear_cache('health', 'analysis', 'training_review', user_id=user_id)
    try:
        match_activities_to_plan(user_id=user_id, username=username)
    except Exception as e:
        print('Matchning efter synk fel:', e)
    try:
        maybe_run_daily_routine()
    except Exception as e:
        print('Daglig rutin fel:', e)
    return len(acts)


# ─────────────────────────────────────────────
# AI-JUSTERARE
# ─────────────────────────────────────────────
def _change_to_pin_on_today(changes):
    """Vilken enda ändring som ska tvingas till idag när användaren bett om det.

    Coachen vägrade annars lägga det efterfrågade passet på idag och sköt det
    till en lugnare dag, så begäran behöver en garanti. Men garantin gällde
    tidigare varje ändring i svaret: bad man om ett pass idag flyttades även
    lördagens och söndagens pass hit, hela veckan hamnade på en och samma dag
    och tömdes sedan på 'missed' dagen efter. Det är ett pass användaren ber
    om — resten av omplaneringen är coachens jobb och ska stå kvar som den
    planerats."""
    candidates = [
        change for change in changes or []
        if change.get('action') in ('add', 'modify', 'reschedule')
        and (not change.get('session_id') or change.get('new_title')
             or change.get('new_detail') or change.get('new_km') is not None)
    ]
    if not candidates:
        return None
    # Ett tillagt pass är per definition det som efterfrågades; annars är det
    # coachens första ändring, som prompten ber den lägga först.
    return next((c for c in candidates if c.get('action') == 'add'), candidates[0])


def ai_adjust_plan(user_request=None):
    """
    Kärnan i planjusteringen som användaren startar via träningsassistenten.
    user_request: valfri fritext från användaren (t.ex. "jag vill gymma idag
    istället för att springa") som prioriteras högt i coachens beslut.
    """
    if not llm_available():
        print('AI adjustment: API key missing')
        return

    today     = date.today()
    iso_week  = today.isocalendar()[1]
    today_dow = today.weekday()
    req_text = (user_request or '').strip()
    explicit_today_request = bool(re.search(r'\b(idag|i dag|ikväll|nu|today|tonight)\b', req_text, re.I))
    explicit_tomorrow_request = bool(re.search(r'\b(imorgon|i morgon|tomorrow)\b', req_text, re.I))
    explicit_rest_request = bool(re.search(r'\b(vilodag|vila|vilo|rest day|rest)\b', req_text, re.I))
    explicit_add_request = bool(re.search(r'\b(lägg till|lagg till|addera|skapa|extra|add|create)\b', req_text, re.I))
    tomorrow = today + timedelta(days=1)
    tomorrow_week = tomorrow.isocalendar()[1]
    tomorrow_dow = tomorrow.weekday()

    first_user = list(USERS.keys())[0] if USERS else 'hugo'
    first_uid  = USERS.get(first_user, {}).get('id', 1)

    # 1. Synka Garmin och hälsodata
    try:
        client = get_garmin(first_user)
        acts = client.get_activities(0, 20)
        ingest_activities(acts, first_uid)
        # Rensa hälso-cache så färsk sömndata hämtas
        clear_cache('health', 'training_load', user_id=first_uid)
    except Exception as e:
        print('AI adjustment: Garmin error', e)

    # 2. Hämta hälsodata
    try:
        client = get_garmin(first_user)
        today_str = today.isoformat()
        sleep     = client.get_sleep_data(today_str)
        readiness = client.get_training_readiness(today_str)
        hrv       = client.get_hrv_data(today_str)
        tl_status = client.get_training_status(today_str)

        s         = sleep.get('dailySleepDTO', {})
        sleep_score = (s.get('sleepScores') or {}).get('overall', {}).get('value')
        deep_pct  = round(s.get('deepSleepSeconds',0) / s.get('sleepTimeSeconds',1) * 100) if s.get('sleepTimeSeconds') else 0
        rem_pct   = round(s.get('remSleepSeconds',0)  / s.get('sleepTimeSeconds',1) * 100) if s.get('sleepTimeSeconds') else 0
        total_h   = round(s.get('sleepTimeSeconds',0) / 3600, 1)
        ready_score = (readiness[0] if readiness else {}).get('score')
        hrv_sum   = hrv.get('hrvSummary', {})
        hrv_avg   = hrv_sum.get('lastNightAvg')
        hrv_weekly = hrv_sum.get('weeklyAvg')
        hrv_pct   = round((hrv_avg / hrv_weekly) * 100) if hrv_weekly and hrv_avg else None

        dev_map   = tl_status.get('mostRecentTrainingStatus',{}).get('latestTrainingStatusData',{})
        dev       = next(iter(dev_map.values()), {})
        acwr_dto  = dev.get('acuteTrainingLoadDTO', {})
        acute     = acwr_dto.get('dailyTrainingLoadAcute')
        chronic   = acwr_dto.get('dailyTrainingLoadChronic')
        acwr      = acwr_dto.get('dailyAcuteChronicWorkloadRatio')
    except Exception as e:
        print('AI adjustment: health data error', e)
        sleep_score = deep_pct = rem_pct = total_h = None
        ready_score = hrv_avg = hrv_weekly = hrv_pct = None
        acute = chronic = acwr = None

    # 3. Hämta missade pass + kommande 14 dagar
    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('''SELECT * FROM plan_sessions
                WHERE status = 'missed' AND week >= %s AND user_id = %s
                ORDER BY week, dow''', (iso_week - 1, first_uid))
            missed = [dict(r) for r in cur.fetchall()]

            cur.execute('''SELECT * FROM plan_sessions
                WHERE status = 'planned' AND week >= %s AND user_id = %s
                ORDER BY week, dow LIMIT 20''', (iso_week, first_uid))
            upcoming = [dict(r) for r in cur.fetchall()]

            # Genomförd km och load denna vecka
            cur.execute('''SELECT raw FROM activities WHERE date >= %s AND user_id = %s''',
                ((today - timedelta(days=today.weekday())).isoformat(), first_uid))
            week_acts = [r['raw'] for r in cur.fetchall()]

    completed_km   = sum((a.get('distance',0) or 0)/1000 for a in week_acts
                         if any(t in (a.get('activityType',{}).get('typeKey',''))
                                for t in ('running','track_running','treadmill_running','trail_running')))
    completed_load = sum(a.get('activityTrainingLoad',0) or 0 for a in week_acts)

    weekly_km_plan = {23:35,24:40,25:45,26:50,27:55,28:55,29:58,30:62,31:65,32:65,33:60,34:68,35:70,36:68,37:65,38:55,39:50,40:35,41:15}
    planned_km = weekly_km_plan.get(iso_week, 40)
    week_cap   = round(planned_km * 1.1)

    # 4. Google Calendar — hämta från cache
    cal_row = get_cache('gcal_events', first_uid)
    gcal_str = ''
    if cal_row:
        upcoming_evs = []
        for ev in (cal_row[0] or []):
            try:
                ev_date = datetime.fromisoformat(ev.get('start','')[:10]).date()
                if today <= ev_date <= today + timedelta(days=14):
                    desc = _plain_calendar_text(ev.get('desc', ''))
                    desc_part = f" — description: {desc}" if desc else ''
                    signals = _calendar_description_signals(ev)
                    signal_part = f" — training impact: {'; '.join(signals)}" if signals else ''
                    upcoming_evs.append(f"- {ev_date}: {ev.get('title','')}{desc_part}{signal_part}")
            except Exception:
                continue
        gcal_str = '\n'.join(upcoming_evs)

    # 5. Bygg AI-prompt
    weekday_sv = ['måndag', 'tisdag', 'onsdag', 'torsdag', 'fredag', 'lördag', 'söndag']

    def _date_for_session(s):
        year = today.isocalendar()[0]
        return date.fromisocalendar(year, int(s['week']), int(s['dow']) + 1)

    def _sess(s):
        session_date = _date_for_session(s)
        return {'id': s['id'], 'date': session_date.isoformat(),
                'weekday_sv': weekday_sv[session_date.weekday()],
                'week': s['week'], 'day': s['dow'], 'type': s['type'],
                'km': s['km'], 'title': s['title'], 'detail': s['detail']}
    missed_json   = json.dumps([_sess(s) for s in missed],   ensure_ascii=False, indent=2) if missed else '(no missed sessions)'
    upcoming_json = json.dumps([_sess(s) for s in upcoming], ensure_ascii=False, indent=2)

    def _compact_strength_recommendation(item):
        previous = None
        if item.get('lastWeight') is not None:
            previous = {
                'date': item.get('lastDate'),
                'sets': item.get('lastSets'),
                'reps': item.get('lastReps'),
                'reps_max': item.get('lastRepsMax'),
                'weight_kg': item.get('lastWeight'),
            }
        return {
            'exercise': item.get('exercise'),
            'prescription': item.get('prescription'),
            'weight_kg': item.get('weight'),
            'confidence': item.get('confidence'),
            'previous': previous,
            'reason': item.get('reason'),
        }

    strength_planner_context = {'upcoming_lift_sessions': [], 'exercise_library_for_new_sessions': []}
    try:
        strength_history = _strength_progression_history(first_uid)
        for session in upcoming:
            if session.get('type') != 'lift':
                continue
            session_day = _plan_session_date(session, today).isoformat()
            recommendations = build_strength_recommendations(
                session.get('detail', ''), strength_history, before_date=session_day
            )
            strength_planner_context['upcoming_lift_sessions'].append({
                'session_id': session['id'],
                'date': session_day,
                'recommendations': [_compact_strength_recommendation(item) for item in recommendations],
            })
        default_recommendations = build_default_recommendations(
            strength_history, before_date=tomorrow.isoformat()
        )
        strength_planner_context['exercise_library_for_new_sessions'] = [
            _compact_strength_recommendation(item) for item in default_recommendations
        ]
    except Exception as exc:
        print('AI adjustment: strength progression context error', exc)
    strength_planner_json = json.dumps(strength_planner_context, ensure_ascii=False, indent=2)

    request_block = ''
    if user_request:
        request_block = f"""

=== RUNNER'S EXPLICIT REQUEST FOR TODAY (HIGH PRIORITY) ===
The runner has personally asked for this change. Honor it as far as it is sensible and safe, and adjust the surrounding plan so the training logic stays intact (e.g. if they want strength instead of a run today, move today's run to a suitable nearby day or fold it into another run, and place/keep a strength session today). Only push back if the request would clearly harm recovery or the goal — and then explain why in coaching_notes.
If the request explicitly says today/idag/tonight/ikväll/nu, the requested workout MUST be placed on TODAY (week {iso_week}, day {today_dow}). Do not move the requested workout to another day because of ACWR, weekly cap, calendar, or recovery concerns. Instead, add a concise warning in coaching_notes/reason and adjust later sessions if needed.
If the request explicitly says rest/vila/vilodag tomorrow/imorgon, ONLY affect sessions on TOMORROW ({tomorrow.isoformat()}, week {tomorrow_week}, day {tomorrow_dow}). Do not add a new workout and do not change today.
Request: "{user_request.strip()}"
"""

    prompt = f"""You are an experienced running coach with deep knowledge of physiology and training planning. You are working with a runner whose goal is a half marathon under 1:20 (3:47/km) on October 10, 2026. Current best: 1:26:19. Secondary goal: build a strong body in all areas - running strength, upper body, core, mobility. The plan runs W23-41 with phases: recovery -> base building -> threshold/tempo -> race-specific -> taper. Always respond in Swedish (svenska). All JSON text fields must be written in Swedish.

TODAY: {today} (week {iso_week}, day {today.weekday()}, where 0=Monday)
{request_block}
=== RUNNER STATUS ===

Sleep today:
- Score: {sleep_score or 'missing'}/100
- Total: {total_h or 'missing'} h · Deep sleep: {deep_pct or 'missing'}% · REM: {rem_pct or 'missing'}%

Recovery:
- Garmin readiness: {ready_score or 'missing'}/100
- Night HRV: {hrv_avg or 'missing'} ms · Weekly average: {hrv_weekly or 'missing'} ms · Difference: {(str(hrv_pct - 100) + '%') if hrv_pct else 'missing'}

Training load (ACWR):
- Acute: {acute or 'missing'} · Chronic: {chronic or 'missing'} · Ratio: {acwr or 'missing'}
- Reference: <0.8 undertrained, 0.8-1.3 optimal, >1.3 injury risk

Week status W{iso_week}:
- Completed running: {completed_km:.1f} km · Planned weekly cap: {week_cap} km
- Completed total load: {round(completed_load)}
{_recent_execution_block(first_uid)}

Execution rules:
- A session marked completed is not automatically a session done well. When easy days were run faster than their target band, the aerobic base is not being built and the next quality session will suffer — consider protecting it by making the following easy day explicitly slower.
- Reps landing under target pace, or fading across the session, mean the prescribed pace is currently too ambitious or recovery is lacking. Adjust the pace target rather than silently repeating it.
- Lifts logged below the calculated target weight mean the progression stalled; note it in coaching_notes.

=== VERIFIED STRENGTH PROGRESSION ===

The prescriptions below are calculated deterministically from completed exercise logs before each session date. Swedish and English aliases for the same exercise have already been merged. Treat these values as the source of truth; do not invent a different weight or percentage.
{strength_planner_json}

Strength rules:
- For an existing lift session, preserve the supplied sets, reps and exact weight recommendation for recognized exercises.
- For a newly added lift session, choose exercises from exercise_library_for_new_sessions when suitable and use those prescriptions.
- When you convert an existing session into a different kind of workout (e.g. a run day becomes a strength day), you MUST set "type" accordingly ("lift" for strength) — the weight recommendation engine only attaches weights to sessions with type "lift". Leave "type" null when the kind of workout is unchanged.
- A null weight means there is no comparable history, a pain warning, or no external weight is needed. Never replace null with a guessed number.
- The dashboard renders these prescriptions in a separate compact block, so do not repeat a long strength history in summary, coaching_notes or reason.

=== SESSIONS THAT NEED A DECISION ===

Missed sessions:
{missed_json}

Upcoming planned sessions, next 14 days:
{upcoming_json}

Google Calendar, next 14 days, affecting recovery and timing:
{gcal_str or '(no events)'}

=== YOUR TASK ===

Analyze the situation as a coach and make the best decisions for the runner's long-term development. You may:

- Add a new session: use this for explicit requests to add training on an empty day or to create an extra optional session
- Reschedule sessions: provide the new week and day
- Skip sessions: when they do not add value given fatigue or context
- Modify session content: change distance, pace, type, or structure
- For strength sessions, name each exercise with explicit sets and reps so the progression engine can attach the verified weight
- Combine logic: for example reschedule and modify the same session
- Keep sessions unchanged: when that is the right decision

Think like a coach, not a rule sheet. Reason about examples like:
- If three hard sessions are stacked in a row, redistribute them to avoid accumulated fatigue
- If one session was missed but the next one fits the structure well, it may be better to make the next session slightly longer than to cram in the missed one
- If the runner is in good shape, with high HRV and good sleep, use that readiness carefully
- If the runner is tired, protect quality adaptations: one good session is better than three mediocre ones
- Consider Google Calendar titles AND descriptions. Descriptions can contain the real constraint: travel, work stress, early start, late night, illness, poor sleep, vacation, or explicit training notes.
- Use calendar "training impact" notes when placing sessions. Avoid quality sessions on travel/stress/poor-sleep/illness days and usually the day after late nights or very early starts.
- Avoid stacking more than two hard sessions in a row, including run quality or high-load strength work
- Keep sessions with status completed or skipped unchanged

Grounding rules:
- Treat the "Upcoming planned sessions" JSON as the only source of truth for planned workouts. Do not assume a strength/run/rest day exists unless it appears there with its session_id.
- Use the provided date and weekday_sv fields when referring to today, tomorrow, or any moved session. If you are unsure, write the exact date instead of a relative day.
- Every change must reference a real session_id from the JSON, except action="add". Never say a session was moved, shortened, or skipped unless that exact change is present in the changes array.
- Never write a strength weight or percentage that conflicts with VERIFIED STRENGTH PROGRESSION. If no verified kg exists, omit kg.
- The summary must describe only applied changes from the changes array. Do not mention "tomorrow", "styrkepass", or "vilodag" unless those exact sessions/dates are affected by a change.

Write a concise explanation in coaching_notes before the decisions.

Return ONLY this JSON, with no comments outside it:
{{
  "coaching_notes": "<2-4 Swedish sentences explaining how you interpret the situation and why you chose this approach>",
  "changes": [
    {{
      "session_id": <int or null for add>,
      "action": "add|reschedule|skip|keep|modify",
      "new_week": <int or null>,
      "new_dow": <int 0-6 or null>,
      "type": "<run|easy|race|lift|rest, or null when the kind of workout is unchanged>",
      "new_km": <float or null>,
      "new_title": "<Swedish string or null>",
      "new_detail": "<concise workout instructions only, max 140 characters; for lift sessions include exercise + sets x reps but omit unverified kg; put reasoning in coaching_notes/reason, or null if unchanged>",
      "reason": "<one Swedish sentence explaining this decision>"
    }}
  ],
  "summary": "<one Swedish sentence summarizing today's adjustments>"
}}"""

    # 6. Anropa AI-coachen
    try:
        text = call_llm(prompt, max_tokens=3000).strip().replace('```json','').replace('```','').strip()
        result = json.loads(text)
    except Exception as e:
        print('AI adjustment: LLM error', e)
        return

    tomorrow_rest_request = explicit_tomorrow_request and explicit_rest_request
    if tomorrow_rest_request:
        tomorrow_sessions = [
            s for s in upcoming
            if s['week'] == tomorrow_week and s['dow'] == tomorrow_dow and s['type'] != 'rest'
        ]
        result['changes'] = [{
            'session_id': s['id'],
            'action': 'skip',
            'new_week': None,
            'new_dow': None,
            'type': s['type'],
            'new_km': None,
            'new_title': None,
            'new_detail': None,
            'reason': f"Användaren bad uttryckligen om vilodag imorgon ({tomorrow.isoformat()})."
        } for s in tomorrow_sessions]
        result['coaching_notes'] = (
            f"Jag tolkar önskemålet strikt: {tomorrow.isoformat()} ska vara vilodag. "
            "Därför ändras bara planerade pass på morgondagens datum."
        )

    valid_session_ids = {s['id'] for s in missed + upcoming}
    filtered_changes = []
    for change in result.get('changes', []):
        action = change.get('action')
        sid = change.get('session_id')
        if action == 'add' and not explicit_add_request and not explicit_today_request:
            print("AI adjustment: ignored add without explicit add/today request")
            continue
        if action != 'add' and sid not in valid_session_ids:
            print(f"AI adjustment: ignored ungrounded change action={action} session_id={sid}")
            continue
        filtered_changes.append(change)
    result['changes'] = filtered_changes

    # 7. Applicera ändringarna på DB
    changes_applied = 0
    applied_actions = []
    pinned_change = _change_to_pin_on_today(result.get('changes', [])) if explicit_today_request else None
    with db() as conn:
        with conn.cursor() as cur:
            for change in result.get('changes', []):
                sid    = change.get('session_id')
                action = change.get('action')
                if change is pinned_change:
                    change['new_week'] = iso_week
                    change['new_dow'] = today_dow
                    reason = change.get('reason') or ''
                    guard_note = 'Användaren bad uttryckligen om passet idag; därför läggs det på idag trots belastningsvarning.'
                    change['reason'] = (reason + ' ' + guard_note).strip()
                if action == 'keep':
                    continue
                if action == 'add':
                    new_week = change.get('new_week')
                    new_dow  = change.get('new_dow')
                    title    = change.get('new_title')
                    detail   = change.get('new_detail')
                    typ      = change.get('type') or 'easy'
                    km       = change.get('new_km') if change.get('new_km') is not None else 0
                    if new_week and new_dow is not None and title and detail:
                        cur.execute('''INSERT INTO plan_sessions
                            (week, dow, type, km, title, detail, status, original_week, original_dow, ai_note, modified_at, user_id)
                            VALUES (%s,%s,%s,%s,%s,%s,'planned',%s,%s,%s,%s,%s)''',
                            (new_week, new_dow, typ, km, title, detail, new_week, new_dow,
                             change.get('reason',''), time.time(), first_uid))
                        changes_applied += 1
                        applied_actions.append('lades till')
                    continue
                if not sid:
                    continue
                if action == 'skip':
                    cur.execute('''UPDATE plan_sessions
                        SET status='skipped', ai_note=%s, modified_at=%s WHERE id=%s AND user_id=%s''',
                        (change.get('reason',''), time.time(), sid, first_uid))
                    changes_applied += 1
                    applied_actions.append('markerades som skippat')
                elif action == 'reschedule':
                    new_week = change.get('new_week')
                    new_dow  = change.get('new_dow')
                    if new_week and new_dow is not None:
                        # Tillåt även innehållsuppdatering vid ombokning
                        extra_sets = []
                        extra_vals = []
                        if change.get('new_km') is not None:
                            extra_sets.append('km=%s'); extra_vals.append(change['new_km'])
                        if change.get('new_title'):
                            extra_sets.append('title=%s'); extra_vals.append(change['new_title'])
                        if change.get('new_detail'):
                            extra_sets.append('detail=%s'); extra_vals.append(change['new_detail'])
                        new_type = _valid_session_type(change.get('type'))
                        if new_type:
                            extra_sets.append('type=%s'); extra_vals.append(new_type)
                        extra_sql = (',' + ','.join(extra_sets)) if extra_sets else ''
                        cur.execute(f'''UPDATE plan_sessions
                            SET status='planned', week=%s, dow=%s,
                                ai_note=%s, modified_at=%s{extra_sql} WHERE id=%s AND user_id=%s''',
                            [new_week, new_dow, change.get('reason',''), time.time()] + extra_vals + [sid, first_uid])
                        changes_applied += 1
                        applied_actions.append('flyttades')
                elif action == 'modify':
                    # Ändra passinnehåll utan att flytta det
                    mod_sets = ['ai_note=%s', 'modified_at=%s']
                    mod_vals = [change.get('reason',''), time.time()]
                    if change.get('new_km') is not None:
                        mod_sets.append('km=%s'); mod_vals.append(change['new_km'])
                    if change.get('new_title'):
                        mod_sets.append('title=%s'); mod_vals.append(change['new_title'])
                    if change.get('new_detail'):
                        mod_sets.append('detail=%s'); mod_vals.append(change['new_detail'])
                    new_type = _valid_session_type(change.get('type'))
                    if new_type:
                        mod_sets.append('type=%s'); mod_vals.append(new_type)
                    if change.get('new_week') is not None and change.get('new_dow') is not None:
                        mod_sets.append('week=%s'); mod_vals.append(change['new_week'])
                        mod_sets.append('dow=%s'); mod_vals.append(change['new_dow'])
                    mod_vals.extend([sid, first_uid])
                    cur.execute(f'''UPDATE plan_sessions
                        SET {','.join(mod_sets)} WHERE id=%s AND status='planned' AND user_id=%s''',
                        mod_vals)
                    changes_applied += 1
                    applied_actions.append('justerades')
        conn.commit()

    if changes_applied:
        action_counts = ', '.join(f"{applied_actions.count(a)} {a}" for a in sorted(set(applied_actions)))
        summary = f"Planen justerad: {action_counts}."
    else:
        summary = 'Inga planändringar gjordes.'
    coaching_notes = result.get('coaching_notes', '')
    print(f'AI adjustment complete: {changes_applied} changes. {summary}')
    if coaching_notes:
        print(f'Coach: {coaching_notes}')
    set_cache('last_plan_adjustment', {
        'date': today.isoformat(),
        'changes': changes_applied,
        'summary': summary,
        'coaching_notes': coaching_notes,
        'user_request': user_request or None
    }, first_uid)


# ─────────────────────────────────────────────
# MANUELL TRIGGER (för testning)
# ─────────────────────────────────────────────
@app.post('/api/plan/reseed')
def api_reseed():
    """Ersätt alla planerade pass med ny PLAN_SEED (behåller historik)."""
    try:
        reseed_plan()
        return jsonify({'ok': True, 'sessions': len(PLAN_SEED)})
    except Exception as e:
        return _server_error(e, 'plan.reseed_failed', message='Träningsplanen kunde inte återställas.')

def manual_adjust_disabled():
    """Trigga AI-justeringen manuellt (t.ex. för testning)."""
    return jsonify({'error': 'Automatic plan coach is disabled'}), 410

def _apply_plan_request(text):
    """Apply a user-requested plan adjustment for the unified assistant."""
    try:
        match_activities_to_plan(user_id=uid(), username=uname())
        ai_adjust_plan(user_request=text)
        first_uid = USERS.get(list(USERS.keys())[0] if USERS else 'hugo', {}).get('id', 1)
        row = get_cache('last_plan_adjustment', first_uid)
        return row[0] if row else {}
    except Exception as e:
        raise RuntimeError('Planändringen kunde inte genomföras.') from e

@app.get('/api/plan/status')
def plan_status():
    """Senaste AI-justeringens status."""
    return jsonify({'date': None, 'changes': 0, 'summary': '', 'coaching_notes': ''})


# ─────────────────────────────────────────────
# SCHEDULER — synkar Garmin var tredje timme
# ─────────────────────────────────────────────
def _morning_report_text(user_id):
    """Bygg morgonrapportens rubrik och text, eller (None, None) om det saknas underlag.

    Medvetet räknad ur data i stället för genererad av AI: den här körs i
    bakgrundsjobbet, och en notis som ibland uteblir för att kvoten är slut är
    sämre än en som alltid kommer. En låsskärm rymmer heller inte mer än ett par
    rader, så det finns inget utrymme för resonemang — det står kvar i appen."""
    parts = []

    # Sömn och beredskap: hela anledningen till att rapporten väntar på synken.
    sleep_bit = None
    try:
        cns, sleep_h, sleep_date = _recent_recovery(user_id)
        # Rapporten handlar om natten som var. Har den inte synkat är gårdagens
        # siffror inget att skicka till en låsskärm som "sov 3,9 h".
        if sleep_h and sleep_date == date.today().isoformat():
            sleep_bit = f'Sov {sleep_h:.1f} h'.replace('.', ',')
        if cns is not None:
            sleep_bit = (sleep_bit + f', beredskap {cns}/100') if sleep_bit \
                        else f'Beredskap {cns}/100'
    except Exception as exc:
        print('morgonrapport: återhämtning saknas:', exc)
    if sleep_bit:
        parts.append(sleep_bit)

    # Dagens planerade pass.
    today = date.today()
    week, dow = _iso_week_dow(today)
    session_title = None
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT title, km, type FROM plan_sessions
                    WHERE week=%s AND dow=%s AND user_id=%s ORDER BY id LIMIT 1''',
                    (week, dow, user_id))
                row = cur.fetchone()
        if row:
            title, km, kind = row
            session_title = title
            # Passtitlarna innehåller ofta distansen redan ("Tröskelpass · 10 km").
            # Lägg bara till den när den saknas, annars står den dubbelt.
            if km and 'km' not in title.lower():
                session_title += f' {km:.0f} km'
        else:
            session_title = 'Vilodag'
    except Exception as exc:
        print('morgonrapport: kunde inte läsa planen:', exc)

    # En varning väger tyngre än allt annat och ska stå först.
    warning = None
    try:
        cns_for_strain, _, _ = _recent_recovery(user_id)
        chronic, _ = _load_context(user_id)
        summary = strain_analysis.strain_summary(
            _recent_activities(user_id, days=30), readiness=cns_for_strain, chronic=chronic)
        if summary.get('tone') == 'warn' and summary.get('headline'):
            warning = summary['headline']
    except Exception as exc:
        print('morgonrapport: strain saknas:', exc)

    if warning:
        parts.append(warning)
    if not parts and not session_title:
        return None, None

    headline = session_title or 'God morgon'
    return headline, ' · '.join(parts) if parts else 'Dagens data är inne.'


def maybe_send_morning_report(user_id):
    """Skicka dagens morgonrapport en gång, när sömndatan väl finns.

    Kroken sitter i den dagliga rutinen, som redan väntar på att dagens
    hälsodata synkat — därför behövs inget gissat klockslag. Tidsfönstret finns
    ändå: synkar klockan inte förrän på kvällen är en 'morgonrapport' bara
    störande, och då är det bättre att hoppa över dagen."""
    if not push_available():
        return False
    today = date.today().isoformat()
    row = get_cache('morning_report_sent', user_id)
    if row and row[0].get('date') == today:
        return False

    hour = datetime.now(LOCAL_TZ).hour
    if not (MORNING_REPORT_HOURS[0] <= hour < MORNING_REPORT_HOURS[1]):
        # Markera dagen som avklarad ändå, annars smäller den vid nästa synk
        # som råkar hamna innanför fönstret — dagen efter är en ny chans.
        set_cache('morning_report_sent', {'date': today, 'skipped': 'outside_window'}, user_id)
        return False

    # Hälsocachen kan fortfarande ligga kvar på gårdagens natt även när Garmin
    # har dagens. Då är det bättre att låta nästa synk skicka rapporten än att
    # skicka gårdagens siffror — dagen markeras medvetet inte som avklarad.
    _, _, sleep_date = _recent_recovery(user_id)
    if sleep_date and sleep_date != today:
        print('Morgonrapport: hälsodatan är från', sleep_date, '— väntar på i natt')
        return False

    headline, body = _morning_report_text(user_id)
    if not headline:
        return False

    sent = send_push(user_id, headline, body, url='/', tag='morning-report')
    set_cache('morning_report_sent', {'date': today, 'devices': sent}, user_id)
    logger.info('Morning report sent', extra={
        'event': 'push.morning_report', 'user_id': user_id, 'devices': sent})
    return bool(sent)


def maybe_run_daily_routine():
    """Den dagliga rutinen körs EN gång per dag — men först när dagens hälsodata
    faktiskt har synkat. Ingen gissad klockslag, inget 'recovery unavailable'.
    Drivs av autosynken (var 3:e timme) + varje manuell synk. Kör bara för user_id=1."""
    first_user = list(USERS.keys())[0] if USERS else 'hugo'
    first_uid  = USERS.get(first_user, {}).get('id', 1)
    today = date.today().isoformat()
    history_row = get_cache('last_daily_history', first_uid)
    history_done = bool(history_row and history_row[0].get('date') == today)
    report_row = get_cache('morning_report_sent', first_uid)
    report_done = bool(report_row and report_row[0].get('date') == today)
    if history_done and report_done:
        return  # redan klart för idag
    try:
        client = get_garmin(first_user)
        readiness = client.get_training_readiness(today)
        sleep = client.get_sleep_data(today)
    except Exception as e:
        print('Daglig rutin: kunde inte kolla hälsodata', e)
        return
    sleep_ok = bool((sleep.get('dailySleepDTO', {}) or {}).get('sleepTimeSeconds'))
    ready_ok = bool(readiness and (readiness[0] or {}).get('score'))
    if not (sleep_ok or ready_ok):
        print('Daglig rutin: dagens hälsodata inte synkad än — väntar till nästa synk')
        return
    if not history_done:
        print('Daglig rutin: dagens data finns → matchning + historik')
        collect_health_history(username=first_user)
        collect_metric_history(username=first_user)
        clear_cache('insights', user_id=first_uid)
        set_cache('last_daily_history', {'date': today}, first_uid)
    # Historiken maste vara insamlad forst - rapporten laser samma siffror.
    # Beredskapspoängen publiceras före sömnen, så en rapport som skickas på
    # enbart den rapporterar gårdagens natt som i natt. Rapporten får vänta på
    # sömnen; nästa synk inom morgonfönstret tar den istället.
    if report_done:
        return
    if not sleep_ok:
        print('Daglig rutin: dagens sömn har inte synkat än — morgonrapporten väntar')
        return
    try:
        maybe_send_morning_report(first_uid)
    except Exception as e:
        print('Daglig rutin: morgonrapport misslyckades', e)

def auto_sync_job():
    first = next(iter(USERS), None)
    for username, rec in list(USERS.items()):
        if not _garmin_connected(username):
            if _strava_connected(username):
                try:
                    activities_out = _strava_activities(username, 120, True)
                    print(f'[{datetime.now().strftime("%H:%M")}] Strava-sync klar '
                          f'({username}): {len(activities_out)} aktiviteter')
                except Exception as e:
                    print(f'Strava-sync fel ({username}):', e)
            continue
        try:
            n = run_sync(username=username, user_id=rec['id'])
            print(f'[{datetime.now().strftime("%H:%M")}] Auto-sync klar ({username}): {n} aktiviteter')
        except Exception as e:
            print(f'Auto-sync fel ({username}):', e)
        if username != first:
            # Ägarens historik sköts av den dagliga rutinen; övriga backfillas här.
            try:
                collect_health_history(3, username=username)
                collect_metric_history(3, username=username)
            except Exception as e:
                print(f'Auto-sync historik-fel ({username}):', e)

scheduler = None
if not APP_TESTING:
    scheduler = BackgroundScheduler(timezone='Europe/Stockholm')
    scheduler.add_job(auto_sync_job, 'interval', minutes=GARMIN_SYNC_MINUTES,
                      # APScheduler interprets a naive datetime in its own
                      # timezone. G3's system timezone differs from Stockholm,
                      # so an aware value is required to avoid an hours-long
                      # delay after restart.
                      next_run_time=datetime.now(LOCAL_TZ) + timedelta(seconds=30),
                      coalesce=True, max_instances=1)
    scheduler.add_job(purge_old_sensor_readings, 'interval', hours=24)
    scheduler.start()
    logger.info('Scheduler started', extra={'event': 'scheduler.started'})

# Bootstrappa hälsohistorik + fitness-mätare i bakgrunden (blockerar inte serverstarten)
def _bootstrap_history():
    first_user = list(USERS.keys())[0] if USERS else 'hugo'
    collect_health_history(14, username=first_user)
    collect_metric_history(45, username=first_user)


if not APP_TESTING:
    threading.Thread(target=_bootstrap_history, daemon=True).start()


# --- Vattensensor (ESP32) ---
# Senast rapporterade tillstånd, för dashboard/felsökning.
_water_state = {'level': None, 'ts': None, 'ac_disabled': False}

@app.post('/api/water')
def water_alert():
    """ESP32 anropar denna. När dunken är FULL aktiveras översvämningsskyddet:
    keepern tvingar AC:n AV varje cykel tills låset släpps manuellt (av/på-knappen).
    Skriver water_lockout=1 + control_enabled=0 (så dashboard-knappen visar AV) och
    ber keepern verkställa direkt så AC:n stängs av med en gång, inte vid nästa poll."""
    token = request.headers.get('x-water-token', '')
    if not token or not WATER_TOKEN or not hmac.compare_digest(token, WATER_TOKEN):
        return jsonify({'ok': False, 'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    level = data.get('level', '')
    _water_state['level'] = level
    _water_state['ts'] = datetime.now(LOCAL_TZ).isoformat()
    if level == 'full':
        try:
            os.makedirs(os.path.dirname(WATER_LOCKOUT_FLAG), exist_ok=True)
            with open(WATER_LOCKOUT_FLAG, 'w') as f:
                f.write('1')
            with open(AC_CONTROL_FLAG, 'w') as f:
                f.write('0')
            _water_state['ac_disabled'] = True
        except Exception as e:
            return _server_error(e, 'water.lockout_failed', extra={'ok': False})
        # Verkställ direkt — vänta inte på keeperns nästa pollcykel.
        try:
            requests.post(f'{AC_KEEPER_URL}/api/control/once', timeout=6)
        except Exception:
            pass  # keepern fångar låset ändå vid nästa cykel
    return jsonify({'ok': True, 'level': level, 'ac_disabled': _water_state['ac_disabled']})

@app.get('/api/water')
def water_status():
    """Visar senaste vattenrapporten (för dashboard/felsökning)."""
    if uid() != 1:
        return jsonify({'available': False, 'error': 'Endast ägaren'}), 403
    return jsonify({'available': True, **_water_state})


PUBLIC_DIR = Path(__file__).resolve().parent / 'public'


def _asset_version():
    """Innehållshash av de statiska filerna, som cache-buster i HTML-sidorna.

    Versionen stod tidigare hårdkodad (`?v=push-1`) och skulle bumpas för hand vid
    varje frontend-ändring. Den 2026-08-04 glömdes det bort: HTML:en är no-cache och
    uppdaterades, men Cloudflare cachar .js/.css på filändelse, så webbläsaren körde
    ny HTML mot gammal app.js och halva klimatsidan stod bara och laddade. En hash
    kan inte glömmas bort.
    """
    digest = hashlib.sha256()
    for name in ('app.js', 'styles.css', 'landing.css', 'landing.js'):
        try:
            digest.update((PUBLIC_DIR / name).read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:12]


ASSET_VERSION = _asset_version()


def _render_page(filename):
    """Serverar en HTML-sida med __ASSETV__ utbytt mot aktuell assetversion."""
    html = (PUBLIC_DIR / filename).read_text(encoding='utf-8')
    return app.response_class(html.replace('__ASSETV__', ASSET_VERSION), mimetype='text/html')


@app.get('/')
def index():
    _, user = _configured_session_user()
    if not user:
        return _render_page('landing.html')
    return _render_page('index.html')

@app.get('/ai')
def ai_control_page():
    _, user = _configured_session_user()
    if not user or user['id'] != 1 or not user.get('is_admin'):
        return send_from_directory('public', 'landing.html'), 403
    return send_from_directory('public', 'ai.html')

@app.get('/<path:path>')
def static_files(path):
    if path == 'ai.html':
        _, user = _configured_session_user()
        if not user or user['id'] != 1 or not user.get('is_admin'):
            return 'Not Found', 404
    return send_from_directory('public', path)

if __name__ == '__main__':
    bind_host = config.get('BIND_HOST', '0.0.0.0')
    bind_port = int(config.get('PORT', '3000'))
    logger.info('Dashboard starting', extra={'event': 'server.starting'})
    app.run(host=bind_host, port=bind_port, debug=False)
