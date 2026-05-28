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
from django.http import (
    FileResponse, Http404, HttpResponse, HttpResponseForbidden,
    HttpResponseNotFound, JsonResponse,
)
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
    # Build a Leaflet-friendly URL template ('/api/tile/{z}/{x}/{y}.png')
    # without hardcoding the path — strip the trailing /z/x/y.png from the
    # reverse() result and append the placeholders the tile layer expects.
    _tile_sample = reverse('tile_raster', kwargs={'z': 0, 'x': 0, 'y': 0})
    _tile_template = _tile_sample.rsplit('/', 3)[0] + '/{z}/{x}/{y}.png'

    return {
        'apiEndpoints': {
            'directoryContents': reverse('get_directory_contents'),
            'folderConfigurations': reverse('get_folder_configurations'),
            'referenceLayers': reverse('get_reference_layers'),
            'serveRaster': reverse('serve_raster'),
            'tileRaster': _tile_template,
            'rasterMeta': reverse('raster_meta'),
            'layerMetadata': reverse('layer_metadata'),
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


def serve_raster(request):
    """Stream a raster file from the ``data/`` directory.

    Retained for direct file downloads if needed; the suitability map now
    consumes rasters via the tiled endpoint below.
    """
    full_path = _resolve_raster_path(request.GET.get('path', ''))
    if full_path is None:
        return HttpResponseNotFound('Raster not found or path invalid')
    response = FileResponse(open(full_path, 'rb'), content_type='image/tiff')
    response['Cache-Control'] = 'private, max-age=600'
    return response


# ---------------------------------------------------------------------------
# Server-side raster tiling (rio-tiler)
# ---------------------------------------------------------------------------

# Per-raster display info cache: tile requests are frequent and stats are
# expensive. Module-level so it survives across requests within one Django
# process; cleared on restart.
_raster_info_cache: dict = {}

# rio-tiler is the only new dependency this feature adds. Imported lazily so
# the rest of the app keeps working if it isn't installed yet.
try:
    from rio_tiler.io import Reader as _RioReader
    from rio_tiler.errors import TileOutsideBounds as _TileOutsideBounds
    from rio_tiler.colormap import cmap as _rio_cmap
    _RIO_TILER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RIO_TILER_AVAILABLE = False


# Pre-built 1x1 transparent PNG used for tiles outside the raster's footprint.
_TRANSPARENT_PNG = bytes((
    137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82,
    0, 0, 0, 1, 0, 0, 0, 1, 8, 6, 0, 0, 0, 31, 21, 196, 137,
    0, 0, 0, 13, 73, 68, 65, 84, 120, 156, 99, 0, 1, 0, 0, 5,
    0, 1, 13, 10, 45, 180, 0, 0, 0, 0, 73, 69, 78, 68, 174, 66,
    96, 130,
))


def _resolve_raster_path(path_param: str) -> str | None:
    """Validate that ``path_param`` resolves to a .tif inside ``data/``.

    Returns the absolute filesystem path, or ``None`` if the path is invalid
    (traversal attempts, wrong extension, missing file).
    """
    if not path_param:
        return None
    raster_path = path_param.lstrip('/\\').replace('\\', '/')
    if not raster_path.lower().endswith('.tif'):
        return None
    base = os.path.realpath(os.path.join(settings.BASE_DIR, 'data'))
    target = os.path.realpath(os.path.join(base, raster_path))
    if not (target == base or target.startswith(base + os.sep)):
        return None
    if not os.path.isfile(target):
        return None
    return target


def _resolve_result_path(path_param: str) -> str | None:
    """Validate that ``path_param`` resolves to a .tif inside ``MEDIA_ROOT``.

    Analysis results are written under ``MEDIA_ROOT/output/...``; this lets the
    tile endpoints serve them while blocking traversal outside media.
    """
    if not path_param:
        return None
    rel = path_param.lstrip('/\\').replace('\\', '/')
    if rel.lower().startswith('media/'):
        rel = rel[len('media/'):]
    if not rel.lower().endswith('.tif'):
        return None
    base = os.path.realpath(str(settings.MEDIA_ROOT))
    target = os.path.realpath(os.path.join(base, rel))
    if not (target == base or target.startswith(base + os.sep)):
        return None
    if not os.path.isfile(target):
        return None
    return target


def _resolve_tiled_source(request):
    """Resolve a tile/meta request's raster, honouring ``?source=``.

    Returns ``(full_path_or_None, is_result)``. ``source=result`` resolves
    under MEDIA_ROOT (analysis outputs); anything else resolves under data/.
    """
    path_param = request.GET.get('path', '')
    if request.GET.get('source') == 'result':
        return _resolve_result_path(path_param), True
    return _resolve_raster_path(path_param), False


def _get_raster_info(full_path: str):
    """Return cached display info for a raster, robust to NaN / nodata.

    Returns ``{'range': (vmin, vmax), 'nodata': value_or_None}`` or ``None``.
    The min/max is a 2-98 percentile stretch computed over *valid* pixels
    (NaN/inf and declared nodata excluded) so fill values don't flatten the
    colour ramp. Stats come from a downsampled read, so even very large
    rasters stay cheap.
    """
    if full_path in _raster_info_cache:
        return _raster_info_cache[full_path]

    info = None
    try:
        import numpy as np
        import rasterio

        with rasterio.open(full_path) as ds:
            declared_nodata = ds.nodata
            h, w = ds.height, ds.width
            # Downsample to ~1024 px on the long edge for a fast, light read.
            scale = max(1, max(h, w) // 1024)
            out_h, out_w = max(1, h // scale), max(1, w // scale)
            band = ds.read(1, masked=True, out_shape=(out_h, out_w))
            # Prefer full-resolution stats from the GDAL .aux.xml when present
            # (these match a pre-generated values file exactly).
            band_tags = ds.tags(1)

            def _tagnum(*keys):
                for k in keys:
                    if k in band_tags:
                        try:
                            return float(band_tags[k])
                        except (TypeError, ValueError):
                            pass
                return None

            stat_min = _tagnum('STATISTICS_MINIMUM')
            stat_max = _tagnum('STATISTICS_MAXIMUM')

        # Mask NaN/inf on top of any declared nodata, then keep valid values.
        arr = np.ma.masked_invalid(band)
        valid = arr.compressed()

        # Detect an undeclared NaN fill (very common for float climate layers)
        # so the tile reader can render those pixels transparent.
        nodata = declared_nodata
        if (nodata is None and np.issubdtype(band.dtype, np.floating)
                and np.ma.getmaskarray(arr).any()):
            nodata = float('nan')

        if valid.size:
            p2, p98 = np.percentile(valid, [2, 98])
            vmin, vmax = float(p2), float(p98)
            if not (np.isfinite(vmin) and np.isfinite(vmax)) or vmax <= vmin:
                vmin, vmax = float(valid.min()), float(valid.max())
            if vmax <= vmin:
                vmax = vmin + 1.0

            # True data range for the criteria slider axis: aux stats first,
            # otherwise the min/max of the (downsampled) valid pixels.
            if (stat_min is not None and stat_max is not None
                    and np.isfinite(stat_min) and np.isfinite(stat_max)
                    and stat_max > stat_min):
                data_range = (stat_min, stat_max)
            else:
                dmin, dmax = float(valid.min()), float(valid.max())
                if not (np.isfinite(dmin) and np.isfinite(dmax)) or dmax <= dmin:
                    dmin, dmax = vmin, vmax
                data_range = (dmin, dmax)

            info = {'range': (vmin, vmax), 'nodata': nodata,
                    'data_range': data_range}
    except Exception:
        logger.exception('Failed to read raster info for %s', full_path)
        info = None

    _raster_info_cache[full_path] = info
    return info


def tile_raster(request, z: int, x: int, y: int):
    """Serve a single web-mercator tile (256x256 PNG) for a raster layer.

    Reads only the relevant window from the source .tif (using GDAL via
    rio-tiler) so it scales to arbitrarily large rasters without loading
    the whole file. The first request for a raster computes its min/max
    for colormap rescaling; subsequent requests reuse the cached values.
    """
    if not _RIO_TILER_AVAILABLE:
        return JsonResponse(
            {'error': "Server-side tiling needs the 'rio-tiler' package. "
                      'Install with: pip install rio-tiler'},
            status=501,
        )

    full_path, is_result = _resolve_tiled_source(request)
    if full_path is None:
        raise Http404('Raster not found or path invalid')

    info = _get_raster_info(full_path)
    if info is None:
        return HttpResponse(_TRANSPARENT_PNG, content_type='image/png')

    vmin, vmax = info['range']
    nodata = info['nodata']

    try:
        tile_kwargs = {'tilesize': 256}
        if nodata is not None:
            tile_kwargs['nodata'] = nodata
        with _RioReader(full_path) as src:
            img = src.tile(x, y, z, **tile_kwargs)
        # rescale mutates ImageData in place in rio-tiler 6.x (returns None);
        # don't reassign, just call.
        img.rescale(in_range=((vmin, vmax),))
        # Results use a red->green suitability ramp; inputs use viridis.
        colormap = _rio_cmap.get('viridis')
        if is_result:
            try:
                colormap = _rio_cmap.get('rdylgn')
            except Exception:
                pass
        content = img.render(img_format='PNG', colormap=colormap)
    except _TileOutsideBounds:
        content = _TRANSPARENT_PNG
    except Exception:
        logger.exception('Tile render failed for %s z=%s x=%s y=%s',
                         full_path, z, x, y)
        content = _TRANSPARENT_PNG

    response = HttpResponse(content, content_type='image/png')
    response['Cache-Control'] = 'public, max-age=86400'
    return response


def raster_meta(request):
    """Return WGS84 bounds and native zoom range for a raster.

    ``bounds`` lets the frontend ``fitBounds``; ``maxzoom`` lets it set
    ``maxNativeZoom`` so coarse rasters render (upscaled) when the map is
    zoomed in past their native resolution, instead of requesting blank
    over-zoom tiles.
    """
    full_path, _is_result = _resolve_tiled_source(request)
    if full_path is None:
        return JsonResponse({'error': 'Raster not found or path invalid'},
                            status=404)
    if not _RIO_TILER_AVAILABLE:
        return JsonResponse(
            {'error': "Server-side tiling needs the 'rio-tiler' package. "
                      'Install with: pip install rio-tiler'},
            status=501,
        )
    try:
        with _RioReader(full_path) as src:
            b = src.geographic_bounds  # (west, south, east, north) in WGS84
            minz, maxz = int(src.minzoom), int(src.maxzoom)
        # Reuse the (cached) display range so the frontend legend matches the
        # colours the tiles are actually rescaled to. Also pre-warms the cache.
        info = _get_raster_info(full_path)
        value_range = list(info['range']) if info else None
        data_range = list(info['data_range']) if (info and info.get('data_range')) else None
        return JsonResponse({
            'bounds': [float(b[0]), float(b[1]), float(b[2]), float(b[3])],
            'minzoom': minz,
            'maxzoom': maxz,
            'range': value_range,
            'data_range': data_range,
        })
    except Exception as exc:
        logger.exception('raster_meta failed for %s', full_path)
        return JsonResponse({'error': str(exc)}, status=500)


def _parse_esri_metadata(full_path: str) -> dict:
    """Best-effort descriptive metadata from an ArcGIS .xml sidecar.

    The ArcGIS metadata format is ISO-19115-shaped but namespace-free, so we
    read the elements directly. Reads trusted local files only; every field is
    optional. Returns {} when no sidecar is found or parsing fails.
    """
    stem = os.path.splitext(full_path)[0]
    candidates = [full_path + '.xml', stem + '_tif.xml',
                  stem + '.tif.xml', stem + '.xml']
    xml_path = next((c for c in candidates if os.path.isfile(c)), None)
    if not xml_path:
        return {}
    try:
        import re as _re
        import xml.etree.ElementTree as ET
        root = ET.parse(xml_path).getroot()
    except Exception:
        logger.exception('Failed to parse ArcGIS metadata %s', xml_path)
        return {}

    def text(path):
        el = root.find(path)
        t = (el.text or '').strip() if el is not None else ''
        return t or None

    def fmt_date(s):
        if s and len(s) == 8 and s.isdigit():
            return '%s-%s-%s' % (s[0:4], s[4:6], s[6:8])
        return s

    out = {}
    title = (text('dataIdInfo/idCitation/resTitle')
             or text('Esri/DataProperties/itemProps/itemName'))
    if title:
        out['Title'] = title
    abstract = text('dataIdInfo/idAbs')
    if abstract:
        out['Abstract'] = _re.sub('<[^>]+>', '', abstract).strip()
    credit = text('dataIdInfo/idCredit')
    if credit:
        out['Credit'] = credit
    kws = [(k.text or '').strip() for k in root.findall('dataIdInfo/searchKeys/keyword')]
    kws = [k for k in kws if k]
    if kws:
        out['Keywords'] = ', '.join(kws)
    uom = root.find('contInfo/ImgDesc/covDim/Band/valUnit/UOM')
    if uom is not None:
        u = (uom.get('value') or uom.get('type') or (uom.text or '')).strip()
        if u:
            out['Units (declared)'] = u
    steps = []
    for p in root.findall('Esri/DataProperties/lineage/Process'):
        nm = (p.get('Name') or '').strip()
        d = fmt_date((p.get('Date') or '').strip())
        if nm:
            steps.append('%s (%s)' % (nm, d) if d else nm)
    if steps:
        out['Processing steps'] = ' \u2192 '.join(steps)
    soft = text('dataIdInfo/envirDesc')
    if soft:
        out['Created with'] = soft.strip()
    crea = fmt_date(text('Esri/CreaDate'))
    if crea:
        out['Created'] = crea
    md = fmt_date(text('mdDateSt'))
    if md:
        out['Metadata date'] = md
    return out


def layer_metadata(request):
    """Return human-facing metadata for a raster, auto-derived via GDAL.

    Technical metadata only: CRS, extent (native + WGS84), resolution,
    dimensions, dtype, nodata, band statistics, overviews, compression. If a
    ``<raster>.meta.json`` sidecar sits next to the file, its contents are
    merged under ``curated`` so descriptive fields (title, units, source) can
    be layered on later without changing this endpoint.
    """
    full_path, _is_result = _resolve_tiled_source(request)
    if full_path is None:
        return JsonResponse({'error': 'Raster not found or path invalid'},
                            status=404)
    try:
        import json as _json
        import rasterio
        from rasterio.warp import transform_bounds

        with rasterio.open(full_path) as ds:
            crs = ds.crs
            try:
                epsg = crs.to_epsg() if crs else None
            except Exception:
                epsg = None
            b = ds.bounds
            wgs = None
            if crs:
                try:
                    wgs = list(transform_bounds(crs, 'EPSG:4326',
                                                b.left, b.bottom, b.right, b.top))
                except Exception:
                    wgs = None

            band_tags = ds.tags(1)

            def _num(d, *keys):
                for k in keys:
                    if k in d:
                        try:
                            return float(d[k])
                        except (TypeError, ValueError):
                            pass
                return None

            stats = {
                'minimum': _num(band_tags, 'STATISTICS_MINIMUM'),
                'maximum': _num(band_tags, 'STATISTICS_MAXIMUM'),
                'mean': _num(band_tags, 'STATISTICS_MEAN'),
                'stddev': _num(band_tags, 'STATISTICS_STDDEV'),
            }
            if not any(v is not None for v in stats.values()):
                stats = None

            try:
                units = ds.units[0] if ds.units and ds.units[0] else None
            except Exception:
                units = None
            try:
                desc = ds.descriptions[0] if ds.descriptions and ds.descriptions[0] else None
            except Exception:
                desc = None
            try:
                overviews = list(ds.overviews(1))
            except Exception:
                overviews = []
            try:
                compression = ds.profile.get('compress')
                compression = str(compression) if compression is not None else None
            except Exception:
                compression = None

            meta = {
                'name': os.path.basename(full_path),
                'driver': ds.driver,
                'width': ds.width,
                'height': ds.height,
                'bands': ds.count,
                'dtype': ds.dtypes[0] if ds.dtypes else None,
                'crs': crs.to_string() if crs else None,
                'epsg': epsg,
                'nodata': ds.nodata,
                'resolution': [abs(ds.res[0]), abs(ds.res[1])] if ds.res else None,
                'bounds_native': [b.left, b.bottom, b.right, b.top],
                'bounds_wgs84': wgs,
                'units': units,
                'band_description': desc,
                'overview_levels': overviews,
                'compression': compression,
                'statistics': stats,
            }

        sidecar = os.path.splitext(full_path)[0] + '.meta.json'
        if os.path.isfile(sidecar):
            try:
                with open(sidecar, 'r', encoding='utf-8') as fh:
                    meta['curated'] = _json.load(fh)
            except Exception:
                logger.exception('Failed to read metadata sidecar %s', sidecar)

        descriptive = _parse_esri_metadata(full_path)
        if descriptive:
            meta['descriptive'] = descriptive

        resp = JsonResponse(meta, json_dumps_params={'indent': 2})
        if request.GET.get('download'):
            stem = os.path.splitext(os.path.basename(full_path))[0]
            resp['Content-Disposition'] = (
                'attachment; filename="%s.metadata.json"' % stem)
        return resp
    except Exception as exc:
        logger.exception('layer_metadata failed for %s', full_path)
        return JsonResponse({'error': str(exc)}, status=500)


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

        # Path relative to MEDIA_ROOT, for the tile endpoint (?source=result).
        result_path = result_relative_url
        if result_path.startswith('/media/'):
            result_path = result_path[len('/media/'):]
        elif result_path.startswith('media/'):
            result_path = result_path[len('media/'):]

        return JsonResponse({
            'status': 'success',
            'result_url': result_absolute_url,
            'result_path': result_path,
        })

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
