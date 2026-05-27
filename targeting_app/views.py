"""
Views for the targeting_app.

Page views render the analysis tools; the `api/*` views are JSON endpoints
called by the frontend to browse data, run analyses and manage the session.
"""

import json
import logging
import os

from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

import geojson

from .land_similarity import LandSimilarity
from .land_statistics import LandStatistics
from .land_suitability import LandSuitability

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Page views
# ---------------------------------------------------------------------------

def _js_config():
    """Values handed to the frontend via the ``#js-config`` json_script block.

    Endpoint URLs are resolved with ``reverse()`` so the JavaScript never
    hardcodes a path — change a route in urls.py and the frontend follows.
    """
    return {
        'apiEndpoints': {
            'directoryContents': reverse('get_directory_contents'),
            'folderConfigurations': reverse('get_folder_configurations'),
            'referenceLayers': reverse('get_reference_layers'),
            'userFiles': reverse('get_user_files'),
            'processLandSuitability': reverse('process_land_suitability'),
            'processLandSimilarity': reverse('process_land_similarity'),
            'processStatistics': reverse('process_statistics'),
        },
    }


def landing_page(request):
    return render(request, 'targeting_app/pages/landing_page.html')


def suitability(request):
    return render(request, 'targeting_app/pages/suitability_page.html',
                  {'js_config': _js_config()})


def similarity(request):
    return render(request, 'targeting_app/pages/similarity_page.html',
                  {'js_config': _js_config()})


def statistics(request):
    return render(request, 'targeting_app/pages/statistics_page.html',
                  {'js_config': _js_config()})


def resources(request):
    return render(request, 'targeting_app/pages/resources_page.html')


# ---------------------------------------------------------------------------
# Data-browsing endpoints
# ---------------------------------------------------------------------------

def get_reference_layers(request):
    """Return the .tif files found under a raster path (relative to BASE_DIR)."""
    raster_path = request.GET.get('raster_path', '')
    base_dir = os.path.join(settings.BASE_DIR, raster_path)

    if not os.path.exists(base_dir):
        return JsonResponse({'error': 'Path not found'}, status=404)

    files = [
        {'file_path': os.path.join(raster_path, f), 'name': f}
        for f in os.listdir(base_dir)
        if f.endswith('.tif')
    ]
    logger.debug('Reference layers found: %d', len(files))
    return JsonResponse({'raster_files': files})


def get_directory_contents(request):
    """List directories and .tif files under ``data/<path>``.

    Each .tif is annotated with min/max values looked up from values.json.
    """
    base_dir = os.path.join(settings.BASE_DIR, 'data')
    path = request.GET.get('path', '')
    full_path = os.path.join(base_dir, path.strip('/'))

    if not os.path.exists(full_path):
        return JsonResponse({'error': 'Directory not found'}, status=404)

    # Load the min/max value config (optional).
    config_file_path = os.path.join(settings.BASE_DIR, 'values.json')
    config_data = []
    if os.path.exists(config_file_path):
        with open(config_file_path, 'r') as f:
            config_data = json.load(f)
    value_lookup = {item['name']: item for item in config_data}

    contents = []
    for item in os.listdir(full_path):
        item_path = os.path.join(full_path, item)
        if os.path.isdir(item_path):
            contents.append({'name': item, 'type': 'directory'})
        elif os.path.isfile(item_path) and item.lower().endswith('.tif'):
            cfg = value_lookup.get(item, {})
            contents.append({
                'name': item,
                'type': 'file',
                'min_val': cfg.get('min_val'),
                'max_val': cfg.get('max_val'),
            })

    return JsonResponse(contents, safe=False)


# Map view configuration per region/folder.
FOLDER_CONFIGURATIONS = {
    'Africa': {'center': [7.1881, 21.0938], 'zoom': 2},
    'Asia': {'center': [47.5162, 103.6609], 'zoom': 2},
    'Global': {'center': [0, 0], 'zoom': 1},
    'S.America': {'center': [-14.2350, -56.1167], 'zoom': 2},
    'Ethiopia': {'center': [9.145, 40.4897], 'zoom': 5},
    'Kenya': {'center': [1.2921, 36.8219], 'zoom': 5},
    'Mali': {'center': [17.5707, -3.9962], 'zoom': 5},
    'Rwanda': {'center': [-1.9403, 29.8739], 'zoom': 7},
    'Senegal': {'center': [14.4974, -14.4524], 'zoom': 6},
    'Tanzania': {'center': [-6.369028, 34.888822], 'zoom': 5},
    'Tunisia': {'center': [33.8869, 9.5375], 'zoom': 5},
    'Colombia': {'center': [4.5709, -74.2973], 'zoom': 5},
    'Ghana': {'center': [7.9465, -1.0232], 'zoom': 6},
}
DEFAULT_FOLDER_CONFIGURATION = {'center': [0, 0], 'zoom': 1}


def get_folder_configurations(request):
    """Return the map center/zoom for a given region folder."""
    folder_name = request.GET.get('folder', '')
    return JsonResponse(
        FOLDER_CONFIGURATIONS.get(folder_name, DEFAULT_FOLDER_CONFIGURATION)
    )


# ---------------------------------------------------------------------------
# AOI helpers
# ---------------------------------------------------------------------------

def convert_aoi_to_geojson(aoi_str):
    """Convert a ``lat,lon;lat,lon;...`` AOI string into a GeoJSON polygon."""
    try:
        coordinates = []
        for coord in aoi_str.split(';'):
            lat, lon = map(float, coord.split(','))
            coordinates.append([lon, lat])  # GeoJSON uses [lon, lat]

        if coordinates[0] != coordinates[-1]:
            coordinates.append(coordinates[0])  # close the ring

        return {'type': 'Polygon', 'coordinates': [coordinates]}
    except Exception as e:
        raise ValueError(f'Invalid AOI format: {e}')


def normalize_aoi_to_geometry(aoi):
    """Normalise an AOI (dict or JSON string) into a GeoJSON geometry dict.

    Accepts Feature, FeatureCollection, Polygon or MultiPolygon.
    """
    aoi_data = json.loads(aoi) if isinstance(aoi, str) else aoi

    if not isinstance(aoi_data, dict) or 'type' not in aoi_data:
        raise ValueError("Invalid AOI GeoJSON: missing 'type'.")

    aoi_type = aoi_data['type']

    if aoi_type == 'Feature':
        geom = aoi_data.get('geometry')
        if not geom:
            raise ValueError("GeoJSON Feature is missing 'geometry'.")
        return geom

    if aoi_type == 'FeatureCollection':
        features = aoi_data.get('features', [])
        if not features:
            raise ValueError('GeoJSON FeatureCollection has no features.')
        geom = features[0].get('geometry')
        if not geom:
            raise ValueError('First feature in FeatureCollection has no geometry.')
        return geom

    if aoi_type in ('Polygon', 'MultiPolygon'):
        return aoi_data

    raise ValueError(
        f'Unsupported AOI type: {aoi_type}. '
        'Expected Feature/FeatureCollection/Polygon/MultiPolygon.'
    )


def _bbox_string_to_polygon(aoi_str):
    """Convert a ``minLat,minLon,maxLat,maxLon`` bbox string into a polygon."""
    coords = [c.strip() for c in aoi_str.split(',')]
    if len(coords) != 4:
        raise ValueError('Invalid AOI bbox format. Expected 4 comma-separated values.')
    min_lat, min_lon, max_lat, max_lon = map(float, coords)
    return {
        'type': 'Polygon',
        'coordinates': [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],
        ]],
    }


def _resolve_aoi_geometry(aoi):
    """Resolve any supported AOI input into a polygonal GeoJSON geometry."""
    if isinstance(aoi, dict):
        geometry = normalize_aoi_to_geometry(aoi)
    elif isinstance(aoi, str):
        aoi_str = aoi.strip()
        try:
            geometry = normalize_aoi_to_geometry(aoi_str)
        except json.JSONDecodeError:
            if ';' in aoi_str:
                geometry = convert_aoi_to_geojson(aoi_str)
            elif ',' in aoi_str:
                geometry = _bbox_string_to_polygon(aoi_str)
            else:
                raise ValueError('Invalid AOI format.')
    else:
        raise ValueError('Unsupported AOI input type.')

    if not isinstance(geometry, dict) or geometry.get('type') not in ('Polygon', 'MultiPolygon'):
        raise ValueError(
            f"Unsupported AOI geometry: {geometry.get('type')}. "
            'Only Polygon/MultiPolygon are supported.'
        )
    return geometry


# ---------------------------------------------------------------------------
# Analysis endpoints
# ---------------------------------------------------------------------------

@require_POST
@csrf_protect
def process_land_suitability(request):
    """Run the Land Suitability analysis from posted form data."""
    try:
        form_data = json.loads(request.body)

        aoi = form_data.get('aoi')  # optional
        selected_files = form_data.get('selectedFiles', [])
        raster_parameters = form_data.get('rasterParameters', {})
        description = (form_data.get('description', '') or '').strip()

        if not description:
            return JsonResponse(
                {'status': 'error', 'message': 'Description is required.'},
                status=400,
            )

        parameters = {'description': description}

        # AOI is optional; resolve it to a polygon when supplied.
        if aoi:
            try:
                parameters['out_extent'] = _resolve_aoi_geometry(aoi)
            except ValueError as ve:
                return JsonResponse({'status': 'error', 'message': str(ve)}, status=400)
            logger.debug('AOI resolved to geometry: %s', parameters['out_extent'])
        else:
            logger.debug('No AOI provided; proceeding without spatial extent filter.')

        if not selected_files:
            return JsonResponse(
                {'status': 'error', 'message': 'No raster files selected.'},
                status=400,
            )

        for i, file_path in enumerate(selected_files):
            if file_path not in raster_parameters:
                return JsonResponse(
                    {'status': 'error',
                     'message': f'Missing parameters for raster {file_path}.'},
                    status=400,
                )
            rp = raster_parameters[file_path]
            idx = i + 1
            parameters[f'in_raster_{idx}'] = 'data' + file_path
            parameters[f'min_val_{idx}'] = rp['min_val']
            parameters[f'opti_from_{idx}'] = rp['opti_from']
            parameters[f'opti_to_{idx}'] = rp['opti_to']
            parameters[f'max_val_{idx}'] = rp['max_val']
            parameters[f'combine_{idx}'] = rp['combine']

        logger.debug('Land suitability parameters: %s', parameters)

        suitability_tool = LandSuitability(parameters, request.session)
        result_relative_url = suitability_tool.execute()
        result_absolute_url = request.build_absolute_uri(result_relative_url)

        return JsonResponse({'status': 'success', 'result_url': result_absolute_url})

    except Exception as e:
        logger.exception('Land suitability processing failed')
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@require_POST
@csrf_protect
def process_land_similarity(request):
    """Run the Land Similarity analysis from posted form data."""
    try:
        data = json.loads(request.body)

        selected_files = data.get('selectedFiles', [])
        coordinates = json.loads(data.get('points', '[]'))
        description = data.get('description', '').strip()

        if not description:
            return JsonResponse(
                {'status': 'error', 'message': 'Description is required.'},
                status=400,
            )

        logger.debug('Received %d coordinate(s)', len(coordinates))

        # Coordinates must be [longitude, latitude] numeric pairs.
        features = []
        for coord in coordinates:
            if not (isinstance(coord, list) and len(coord) == 2):
                return JsonResponse(
                    {'status': 'error', 'message': f'Invalid coordinate format: {coord}'}
                )
            lon, lat = coord
            if not (isinstance(lon, (int, float)) and isinstance(lat, (int, float))):
                return JsonResponse(
                    {'status': 'error', 'message': f'Invalid coordinate values: {coord}'}
                )
            features.append(geojson.Feature(geometry=geojson.Point((lon, lat))))

        parameters = {
            'selectedFiles': selected_files,
            'in_point': geojson.FeatureCollection(features),
            'description': description,
        }

        land_similarity = LandSimilarity(parameters, request.session)
        result = land_similarity.execute()

        return JsonResponse({
            'status': 'success',
            'result_url': {
                'mnobis': request.build_absolute_uri(result['Mahalanobis']),
                'mess': request.build_absolute_uri(result['MESS']),
            },
        })

    except Exception as e:
        logger.exception('Land similarity processing failed')
        return JsonResponse({'status': 'error', 'message': str(e)})


@require_POST
@csrf_protect
def process_statistics(request):
    """Run zonal statistics for a raster against a reference layer."""
    try:
        data = json.loads(request.body)
        raster_file = data.get('raster_path')
        description = data.get('description', '')
        reference_layer = data.get('reference_layer')
        stat_types = data.get('stat_types', [])

        if not reference_layer:
            return JsonResponse(
                {'status': 'error', 'message': 'Reference Layer is required.'},
                status=400,
            )
        if not raster_file:
            return JsonResponse(
                {'status': 'error', 'message': 'Raster file path is required.'},
                status=400,
            )

        try:
            processor = LandStatistics(reference_layer, raster_file, description, stat_types)
            results = processor.compute_statistics()
            logger.debug('Statistics computed successfully')
        except FileNotFoundError as fnfe:
            return JsonResponse({'status': 'error', 'message': str(fnfe)}, status=404)
        except ValueError as ve:
            return JsonResponse({'status': 'error', 'message': str(ve)}, status=400)
        except Exception as e:
            logger.exception('Statistics computation failed')
            return JsonResponse(
                {'status': 'error', 'message': f'Unexpected error: {e}'},
                status=500,
            )

        return JsonResponse({'status': 'success', 'results': results}, safe=False)

    except json.JSONDecodeError:
        return JsonResponse(
            {'status': 'error', 'message': 'Invalid JSON format in request body.'},
            status=400,
        )
    except Exception as e:
        logger.exception('Unhandled error in process_statistics')
        return JsonResponse(
            {'status': 'error', 'message': f'Unhandled server error: {e}'},
            status=500,
        )


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def get_user_files(request):
    """Return the list of files generated by the user during the session."""
    files = request.session.get('generated_files', [])
    return JsonResponse(files, safe=False)


@user_passes_test(lambda u: u.is_superuser)
def manage_session(request, action):
    """Superuser-only helper to inspect/clear the current session."""
    if action == 'flush':
        request.session.flush()
        return JsonResponse({'message': 'Session completely cleared!'})
    if action == 'clear':
        request.session.clear()
        return JsonResponse({'message': 'All session keys cleared, session still active.'})
    if action == 'clear_key':
        request.session.pop('generated_files', None)
        return JsonResponse({'message': "Session key 'generated_files' cleared (if it existed)."})
    return JsonResponse({'message': 'Invalid action!'})
