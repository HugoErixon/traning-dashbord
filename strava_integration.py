"""Small Strava OAuth/API adapter with Trainyze-compatible activity payloads."""

from __future__ import annotations

import time
from urllib.parse import urlencode

import requests


AUTH_URL = 'https://www.strava.com/oauth/authorize'
TOKEN_URL = 'https://www.strava.com/oauth/token'
REVOKE_URL = 'https://www.strava.com/oauth/revoke'
API_BASE_URL = 'https://www.strava.com/api/v3'


class StravaError(RuntimeError):
    pass


class StravaRateLimited(StravaError):
    """Stravas gräns gäller hela appen, inte en användare — sluta fråga en stund."""


def authorization_url(client_id, redirect_uri, state):
    return AUTH_URL + '?' + urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'approval_prompt': 'auto',
        'scope': 'read,activity:read_all',
        'state': state,
    })


def exchange_code(client_id, client_secret, code, timeout=15):
    response = requests.post(TOKEN_URL, data={
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'grant_type': 'authorization_code',
    }, timeout=timeout)
    return _token_response(response)


def refresh_access_token(client_id, client_secret, refresh_token, timeout=15):
    response = requests.post(TOKEN_URL, data={
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    }, timeout=timeout)
    return _token_response(response)


def _token_response(response):
    try:
        payload = response.json()
    except ValueError as exc:
        raise StravaError('Strava returnerade ett ogiltigt svar.') from exc
    if not response.ok or payload.get('errors') or not payload.get('access_token'):
        raise StravaError('Strava kunde inte godkänna anslutningen.')
    return payload


def api_get(access_token, path, params=None, timeout=20):
    response = requests.get(
        API_BASE_URL + path,
        params=params,
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=timeout,
    )
    if response.status_code == 401:
        raise StravaError('Strava-token är ogiltig eller återkallad.')
    if response.status_code == 429:
        raise StravaRateLimited('Stravas gräns för antal anrop är nådd just nu.')
    if not response.ok:
        raise StravaError(f'Strava API svarade med status {response.status_code}.')
    try:
        return response.json()
    except ValueError as exc:
        raise StravaError('Strava returnerade ett ogiltigt svar.') from exc


def athlete_activities(access_token, days=120, per_page=100):
    after = int(time.time() - max(1, min(365, days)) * 86400)
    return api_get(access_token, '/athlete/activities', {
        'after': after, 'page': 1, 'per_page': max(1, min(100, per_page)),
    })


def athlete_activities_since(access_token, after_epoch, per_page=100, max_pages=10):
    """Alla aktiviteter efter en tidpunkt, sida för sida (Strava ger högst 100 åt gången)."""
    out = []
    for page in range(1, max_pages + 1):
        batch = api_get(access_token, '/athlete/activities', {
            'after': int(after_epoch), 'page': page, 'per_page': per_page,
        })
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < per_page:
            break
    return out


def activity_laps(access_token, activity_id):
    return api_get(access_token, f'/activities/{int(activity_id)}/laps')


AUTO_LAP_METERS = (1000.0, 1609.34)
MIN_CHUNK_SECONDS = 360
# Två automatiska varv slås bara ihop om farten är ungefär densamma — annars
# blandas ett drag ihop med joggen efter.
MAX_CHUNK_SPEED_RATIO = 1.12


def _is_auto_lap(lap):
    meters = lap.get('distance') or 0
    return any(abs(meters - auto) <= auto * 0.03 for auto in AUTO_LAP_METERS)


def lap_rows(laps):
    """Stravas varv i race predictorns kompakta form:
    [sekunder, meter, snittpuls, fart m/s, typ, maxpuls].

    Strava har ingen terrängjusterad fart och ingen intensitetstyp. Klockans
    automatiska kilometer- eller milvarv slås ihop i följd tills en bit är minst
    sex minuter, så att ett vanligt pass ger stabila punkter. Manuella varv
    (intervalldrag) lämnas som de är och märks ACTIVE; race predictorn sorterar
    själv bort joggvilor på farten."""
    rows, chunk = [], []

    def lap_values(lap):
        seconds = lap.get('moving_time') or lap.get('elapsed_time') or 0
        return float(seconds), float(lap.get('distance') or 0), lap.get('average_heartrate'), lap.get('max_heartrate')

    def flush():
        if not chunk:
            return
        seconds = sum(c[0] for c in chunk)
        meters = sum(c[1] for c in chunk)
        hr_parts = [(c[0], c[2]) for c in chunk if c[2]]
        hr = sum(t * h for t, h in hr_parts) / sum(t for t, _ in hr_parts) if hr_parts else None
        max_hr = max((c[3] for c in chunk if c[3]), default=None)
        rows.append([round(seconds, 1), round(meters, 1), round(hr, 1) if hr else None,
                     round(meters / seconds, 3) if seconds else None, 'ACTIVE', max_hr])
        chunk.clear()

    for lap in laps or []:
        if not isinstance(lap, dict):
            continue
        values = lap_values(lap)
        if not values[0] or not values[1]:
            continue
        if not _is_auto_lap(lap):
            flush()
            chunk.append(values)
            flush()
            continue
        if chunk:
            speed = values[1] / values[0]
            mean = sum(c[1] for c in chunk) / sum(c[0] for c in chunk)
            if max(speed, mean) / min(speed, mean) > MAX_CHUNK_SPEED_RATIO:
                flush()
        chunk.append(values)
        if sum(c[0] for c in chunk) >= MIN_CHUNK_SECONDS:
            flush()
    flush()
    return rows


def activity_detail(access_token, activity_id):
    activity = api_get(access_token, f'/activities/{int(activity_id)}')
    streams = api_get(access_token, f'/activities/{int(activity_id)}/streams', {
        'keys': 'time,distance,latlng,altitude,heartrate,watts,cadence',
        'key_by_type': 'true',
    })
    try:
        zones = api_get(access_token, f'/activities/{int(activity_id)}/zones')
    except StravaError:
        # Zone distribution is optional on Strava and is unavailable for some
        # activity types/accounts. Route, streams and laps should still render.
        zones = []
    return normalize_detail(activity, streams, zones)


def _type_key(sport_type):
    value = str(sport_type or '').lower()
    if value == 'trailrun':
        return 'trail_running'
    if value in ('virtualrun', 'treadmill'):
        return 'treadmill_running'
    if value in ('run', 'race'):
        return 'running'
    if value in ('weighttraining', 'workout', 'crossfit'):
        return 'strength_training'
    if 'ride' in value or value in ('cycling', 'mountainbikeride'):
        return 'cycling'
    if value == 'swim':
        return 'swimming'
    return value or 'other'


def normalize_summary(activity):
    activity_id = int(activity['id'])
    location = ', '.join(filter(None, (
        activity.get('location_city'), activity.get('location_state'),
        activity.get('location_country'),
    )))
    return {
        'activityId': activity_id,
        'externalActivityId': activity_id,
        'source': 'strava',
        'activityName': activity.get('name') or 'Strava-aktivitet',
        'activityType': {'typeKey': _type_key(activity.get('sport_type') or activity.get('type'))},
        'startTimeLocal': activity.get('start_date_local'),
        'distance': activity.get('distance'),
        'duration': activity.get('moving_time'),
        'elapsedDuration': activity.get('elapsed_time'),
        'averageHR': activity.get('average_heartrate'),
        'maxHR': activity.get('max_heartrate'),
        'elevationGain': activity.get('total_elevation_gain'),
        'avgPower': activity.get('average_watts'),
        'maxPower': activity.get('max_watts'),
        'averageCadence': activity.get('average_cadence'),
        'calories': activity.get('calories'),
        'locationName': location or None,
        'manufacturer': activity.get('device_name') or 'Strava',
        'stravaUrl': f'https://www.strava.com/activities/{activity_id}',
        # Strava märker lopp med workout_type 1 (löpning); samma form som Garmins eventType.
        'eventType': {'typeKey': 'race' if activity.get('workout_type') == 1 else 'uncategorized'},
    }


def _stream_data(streams, key):
    stream = streams.get(key) if isinstance(streams, dict) else None
    return (stream.get('data') or []) if isinstance(stream, dict) else []


def _zones(zones, zone_type):
    source = next((item for item in zones if isinstance(item, dict)
                   and item.get('type') == zone_type), {}) if isinstance(zones, list) else {}
    return [{
        'zone': index + 1,
        'seconds': bucket.get('time') or 0,
        'low': bucket.get('min'),
    } for index, bucket in enumerate(source.get('distribution_buckets') or [])
        if isinstance(bucket, dict)]


def normalize_detail(activity, streams, zones=None):
    summary = normalize_summary(activity)
    times = _stream_data(streams, 'time')
    distances = _stream_data(streams, 'distance')
    coordinates = _stream_data(streams, 'latlng')
    elevations = _stream_data(streams, 'altitude')
    heart_rates = _stream_data(streams, 'heartrate')
    watts = _stream_data(streams, 'watts')
    cadences = _stream_data(streams, 'cadence')
    count = max(map(len, (times, distances, coordinates, elevations,
                          heart_rates, watts, cadences)), default=0)

    def at(values, index):
        return values[index] if index < len(values) else None

    route = []
    series = []
    previous_distance = previous_time = None
    for index in range(count):
        elapsed = at(times, index)
        distance = at(distances, index)
        coordinate = at(coordinates, index)
        elevation = at(elevations, index)
        pace = None
        if previous_distance is not None and previous_time is not None \
                and distance is not None and elapsed is not None:
            delta_distance = distance - previous_distance
            delta_time = elapsed - previous_time
            if delta_distance > 0.2 and delta_time > 0:
                pace = round(delta_time / (delta_distance / 1000), 1)
        if distance is not None:
            previous_distance = distance
        if elapsed is not None:
            previous_time = elapsed
        series.append({
            'elapsed': elapsed, 'distance': distance, 'heartRate': at(heart_rates, index),
            'pace': pace, 'power': at(watts, index), 'elevation': elevation,
            'cadence': at(cadences, index),
        })
        if isinstance(coordinate, list) and len(coordinate) == 2:
            route.append({'lat': coordinate[0], 'lon': coordinate[1],
                          'elevation': elevation, 'distance': distance})

    laps = []
    for index, lap in enumerate(activity.get('laps') or []):
        distance = lap.get('distance')
        moving = lap.get('moving_time') or lap.get('elapsed_time')
        laps.append({
            'index': lap.get('lap_index') or index + 1,
            'type': '', 'distance': distance, 'duration': lap.get('elapsed_time'),
            'movingDuration': moving,
            'pace': round(moving / (distance / 1000), 1) if distance and moving else None,
            'averageHR': lap.get('average_heartrate'), 'maxHR': lap.get('max_heartrate'),
            'averagePower': lap.get('average_watts'), 'maxPower': None,
            'averageCadence': lap.get('average_cadence'),
            'elevationGain': lap.get('total_elevation_gain'), 'elevationLoss': None,
        })

    moving = activity.get('moving_time')
    distance = activity.get('distance')
    return {
        'id': summary['activityId'], 'source': 'strava',
        '_athleteId': (activity.get('athlete') or {}).get('id'),
        'sourceUrl': summary['stravaUrl'], 'name': summary['activityName'],
        'type': summary['activityType']['typeKey'], 'date': summary['startTimeLocal'],
        'location': summary['locationName'], 'device': summary['manufacturer'],
        'overview': {
            'distance': distance, 'duration': moving, 'movingDuration': moving,
            'elapsedDuration': activity.get('elapsed_time'),
            'pace': round(moving / (distance / 1000), 1) if distance and moving else None,
            'calories': activity.get('calories'), 'averageHR': activity.get('average_heartrate'),
            'maxHR': activity.get('max_heartrate'),
            'elevationGain': activity.get('total_elevation_gain'), 'elevationLoss': None,
            'averagePower': activity.get('average_watts'), 'maxPower': activity.get('max_watts'),
            'normalizedPower': activity.get('weighted_average_watts'),
            'averageCadence': activity.get('average_cadence'), 'maxCadence': None,
            'trainingLoad': activity.get('suffer_score'),
            'aerobicEffect': None, 'anaerobicEffect': None, 'vo2max': None,
            'strideLength': None, 'groundContactTime': None, 'verticalOscillation': None,
            'verticalRatio': None, 'averageRespiration': None, 'averageTemperature': None,
            'bodyBatteryImpact': None, 'steps': None, 'waterEstimated': None,
        },
        'route': route, 'series': series, 'laps': laps,
        'heartRateZones': _zones(zones, 'heartrate'),
        'powerZones': _zones(zones, 'power'), 'weather': None,
        'gear': [{'name': (activity.get('gear') or {}).get('name')}]
        if (activity.get('gear') or {}).get('name') else [],
        'exerciseSets': [], 'strengthExercises': [],
    }
