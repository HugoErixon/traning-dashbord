"""Stable, frontend-friendly representation of a Garmin activity.

Garmin's private API uses different field names in the activity list, activity
summary, chart details and split responses.  Keeping that translation here
means the calendar UI does not need to know which Garmin response a value came
from, and makes partial responses useful instead of failing the whole view.
"""

from __future__ import annotations

import math


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _rounded(value, digits=1):
    value = _number(value)
    return round(value, digits) if value is not None else None


def _pick(*values):
    return next((value for value in values if value is not None), None)


def _downsample(items, limit):
    if len(items) <= limit:
        return items
    step = math.ceil((len(items) - 1) / (limit - 1))
    sampled = items[::step]
    if sampled[-1] is not items[-1]:
        sampled.append(items[-1])
    return sampled


def _pace(distance_m, duration_s):
    distance_m = _number(distance_m)
    duration_s = _number(duration_s)
    if not distance_m or distance_m < 1 or not duration_s or duration_s <= 0:
        return None
    return round(duration_s / (distance_m / 1000), 1)


def _metric_series(details):
    descriptors = details.get('metricDescriptors') or []
    indexes = {
        item.get('key'): item.get('metricsIndex')
        for item in descriptors
        if isinstance(item, dict) and isinstance(item.get('metricsIndex'), int)
    }
    rows = details.get('activityDetailMetrics') or []
    if not indexes or not rows:
        return []

    def value(metrics, key):
        index = indexes.get(key)
        if index is None or index >= len(metrics):
            return None
        return _number(metrics[index])

    first_timestamp = None
    series = []
    for row in rows:
        metrics = row.get('metrics') if isinstance(row, dict) else None
        if not isinstance(metrics, list):
            continue
        timestamp = value(metrics, 'directTimestamp')
        if timestamp is not None and first_timestamp is None:
            first_timestamp = timestamp
        elapsed = value(metrics, 'sumElapsedDuration')
        if elapsed is None and timestamp is not None and first_timestamp is not None:
            elapsed = (timestamp - first_timestamp) / 1000
        speed = value(metrics, 'directSpeed')
        grade_speed = value(metrics, 'directGradeAdjustedSpeed')
        point = {
            'elapsed': _rounded(elapsed, 1),
            'distance': _rounded(value(metrics, 'sumDistance'), 1),
            'heartRate': _rounded(value(metrics, 'directHeartRate'), 0),
            'pace': round(1000 / speed, 1) if speed and speed > 0.2 else None,
            'gradeAdjustedPace': round(1000 / grade_speed, 1)
            if grade_speed and grade_speed > 0.2 else None,
            'power': _rounded(value(metrics, 'directPower'), 0),
            'elevation': _rounded(value(metrics, 'directElevation'), 1),
            'cadence': _rounded(_pick(
                value(metrics, 'directRunCadence'),
                value(metrics, 'directDoubleCadence'),
            ), 0),
            'temperature': _rounded(value(metrics, 'directAirTemperature'), 1),
            'stamina': _rounded(value(metrics, 'directAvailableStamina'), 0),
            'performanceCondition': _rounded(
                value(metrics, 'directPerformanceCondition'), 0),
        }
        if point['elapsed'] is not None:
            series.append(point)
    return _downsample(series, 1200)


def _route(details):
    polyline = (details.get('geoPolylineDTO') or {}).get('polyline') or []
    points = []
    for point in polyline:
        if not isinstance(point, dict):
            continue
        lat, lon = _number(point.get('lat')), _number(point.get('lon'))
        if lat is None or lon is None:
            continue
        points.append({
            'lat': round(lat, 6),
            'lon': round(lon, 6),
            'elevation': _rounded(point.get('altitude'), 1),
            'distance': _rounded(point.get('distanceInMeters'), 1),
        })
    return _downsample(points, 1400)


def _laps(splits):
    result = []
    for position, lap in enumerate(splits.get('lapDTOs') or splits.get('laps') or []):
        if not isinstance(lap, dict):
            continue
        distance = _number(lap.get('distance'))
        duration = _number(_pick(lap.get('movingDuration'), lap.get('duration')))
        result.append({
            'index': int(_pick(lap.get('lapIndex'), position + 1)),
            'type': lap.get('intensityType') or '',
            'distance': _rounded(distance, 1),
            'duration': _rounded(lap.get('duration'), 1),
            'movingDuration': _rounded(lap.get('movingDuration'), 1),
            'pace': _pace(distance, duration),
            'averageHR': _rounded(lap.get('averageHR'), 0),
            'maxHR': _rounded(lap.get('maxHR'), 0),
            'averagePower': _rounded(lap.get('averagePower'), 0),
            'maxPower': _rounded(lap.get('maxPower'), 0),
            'averageCadence': _rounded(lap.get('averageRunCadence'), 0),
            'elevationGain': _rounded(lap.get('elevationGain'), 1),
            'elevationLoss': _rounded(lap.get('elevationLoss'), 1),
        })
    return result


def _zones(zones):
    result = []
    for zone in zones if isinstance(zones, list) else []:
        if not isinstance(zone, dict):
            continue
        result.append({
            'zone': int(_pick(zone.get('zoneNumber'), len(result) + 1)),
            'seconds': _rounded(zone.get('secsInZone'), 0) or 0,
            'low': _rounded(zone.get('zoneLowBoundary'), 0),
        })
    return result


def _gear(items):
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        result.append({
            'name': _pick(item.get('displayName'), item.get('customMakeModel'),
                          item.get('gearMakeName'), item.get('gearTypeName')),
            'model': _pick(item.get('customMakeModel'), item.get('gearModelName')),
            'totalDistance': _rounded(item.get('totalDistance'), 0),
        })
    return result


def normalize_activity_detail(raw, activity=None, details=None, splits=None,
                              hr_zones=None, power_zones=None, weather=None,
                              gear=None):
    """Combine Garmin responses into one serializable activity payload."""
    raw = raw or {}
    activity = activity or {}
    details = details or {}
    splits = splits or {}
    summary = activity.get('summaryDTO') or {}
    activity_type = _pick(
        (raw.get('activityType') or {}).get('typeKey'),
        (activity.get('activityTypeDTO') or {}).get('typeKey'),
        raw.get('type'),
    )
    distance = _number(_pick(summary.get('distance'), raw.get('distance')))
    duration = _number(_pick(summary.get('duration'), raw.get('duration')))
    moving_duration = _number(_pick(
        summary.get('movingDuration'), raw.get('movingDuration'), duration))

    overview = {
        'distance': _rounded(distance, 1),
        'duration': _rounded(duration, 1),
        'movingDuration': _rounded(moving_duration, 1),
        'elapsedDuration': _rounded(_pick(
            summary.get('elapsedDuration'), raw.get('elapsedDuration')), 1),
        'pace': _pace(distance, moving_duration),
        'calories': _rounded(_pick(summary.get('calories'), raw.get('calories')), 0),
        'averageHR': _rounded(_pick(summary.get('averageHR'), raw.get('averageHR')), 0),
        'maxHR': _rounded(_pick(summary.get('maxHR'), raw.get('maxHR')), 0),
        'elevationGain': _rounded(_pick(
            summary.get('elevationGain'), raw.get('elevationGain')), 1),
        'elevationLoss': _rounded(_pick(
            summary.get('elevationLoss'), raw.get('elevationLoss')), 1),
        'averagePower': _rounded(_pick(
            summary.get('averagePower'), raw.get('avgPower')), 0),
        'maxPower': _rounded(_pick(summary.get('maxPower'), raw.get('maxPower')), 0),
        'normalizedPower': _rounded(_pick(
            summary.get('normalizedPower'), raw.get('normPower')), 0),
        'averageCadence': _rounded(_pick(
            summary.get('averageRunCadence'), raw.get('averageRunningCadenceInStepsPerMinute')), 0),
        'maxCadence': _rounded(_pick(
            summary.get('maxRunCadence'), raw.get('maxRunningCadenceInStepsPerMinute')), 0),
        'trainingLoad': _rounded(_pick(
            summary.get('activityTrainingLoad'), raw.get('activityTrainingLoad')), 0),
        'aerobicEffect': _rounded(_pick(
            summary.get('trainingEffect'), raw.get('aerobicTrainingEffect')), 1),
        'anaerobicEffect': _rounded(_pick(
            summary.get('anaerobicTrainingEffect'), raw.get('anaerobicTrainingEffect')), 1),
        'vo2max': _rounded(raw.get('vO2MaxValue'), 1),
        'strideLength': _rounded(_pick(summary.get('strideLength'), raw.get('avgStrideLength')), 1),
        'groundContactTime': _rounded(_pick(
            summary.get('groundContactTime'), raw.get('avgGroundContactTime')), 0),
        'verticalOscillation': _rounded(_pick(
            summary.get('verticalOscillation'), raw.get('avgVerticalOscillation')), 1),
        'verticalRatio': _rounded(_pick(
            summary.get('verticalRatio'), raw.get('avgVerticalRatio')), 1),
        'averageRespiration': _rounded(_pick(
            summary.get('avgRespirationRate'), raw.get('avgRespirationRate')), 1),
        'averageTemperature': _rounded(_pick(
            summary.get('averageTemperature'), raw.get('avgTemperature')), 1),
        'bodyBatteryImpact': _rounded(_pick(
            summary.get('differenceBodyBattery'), raw.get('differenceBodyBattery')), 0),
        'steps': _rounded(_pick(summary.get('steps'), raw.get('steps')), 0),
        'waterEstimated': _rounded(_pick(
            summary.get('waterEstimated'), raw.get('waterEstimated')), 0),
    }

    weather = weather if isinstance(weather, dict) else {}
    return {
        'id': _pick(raw.get('activityId'), activity.get('activityId')),
        'name': _pick(raw.get('activityName'), activity.get('activityName'), 'Aktivitet'),
        'type': activity_type or '',
        'date': _pick(raw.get('startTimeLocal'), summary.get('startTimeLocal')),
        'location': _pick(raw.get('locationName'), activity.get('locationName')),
        'device': _pick(raw.get('manufacturer'),
                        (activity.get('metadataDTO') or {}).get('manufacturer')),
        'overview': overview,
        'route': _route(details),
        'series': _metric_series(details),
        'laps': _laps(splits),
        'heartRateZones': _zones(hr_zones),
        'powerZones': _zones(power_zones),
        'weather': {
            'temperature': _rounded(weather.get('temp'), 1),
            'feelsLike': _rounded(weather.get('apparentTemp'), 1),
            'humidity': _rounded(weather.get('relativeHumidity'), 0),
            'windSpeed': _rounded(weather.get('windSpeed'), 1),
            'windDirection': weather.get('windDirectionCompassPoint'),
            'description': (weather.get('weatherTypeDTO') or {}).get('desc'),
        } if weather else None,
        'gear': _gear(gear),
    }
