"""
Views for the targeting_app.

Page views render the analysis tools; the `api/*` views are JSON endpoints
called by the frontend to browse data, run analyses and manage the session.
"""

import io
import json
import logging
import math
import os
import re

from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.http import (
    FileResponse, Http404, HttpResponse, HttpResponseForbidden,
    HttpResponseNotFound, JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

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
            'queryPoint': reverse('query_point'),
            'reportSuitability': reverse('report_suitability'),
            'reportSimilarity': reverse('report_similarity'),
            'reportStatistics': reverse('report_statistics'),
            'aoiHistogram': reverse('aoi_histogram'),
        },
        'pages': {
            'statistics': reverse('statistics'),
            'suitability': reverse('suitability'),
            'similarity': reverse('similarity'),
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
    # Tolerate paths that already include a leading "data/" segment — the
    # reference-layers API returns paths in that shape, but ``data/`` is
    # always the base directory we resolve under anyway.
    if raster_path.lower().startswith('data/'):
        raster_path = raster_path[len('data/'):]
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

            # Global value-distribution histogram for the criteria-card
            # slider — computed from the same downsampled read so it
            # comes essentially free, no extra reader pass. Binned over
            # ``data_range`` so the bars align with the slider's axis.
            hist_bins, hist_edges = None, None
            try:
                drange_min, drange_max = float(data_range[0]), float(data_range[1])
                if drange_max > drange_min:
                    # Clip the downsampled valid pixels to the data-range
                    # window before histogramming so an outlier doesn't
                    # squish every meaningful bar into one bin.
                    in_range = valid[(valid >= drange_min) & (valid <= drange_max)]
                    if in_range.size:
                        counts, edges = np.histogram(in_range, bins=30,
                                                     range=(drange_min, drange_max))
                        hist_bins = counts.astype(int).tolist()
                        hist_edges = [float(e) for e in edges]
            except Exception:
                logger.exception('Histogram computation failed for %s', full_path)

            info = {'range': (vmin, vmax), 'nodata': nodata,
                    'data_range': data_range,
                    'hist_bins': hist_bins, 'hist_edges': hist_edges}
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
        # ``invert=1`` flips the colour ramp. Reversing ``in_range`` as
        # ``((vmax, vmin),)`` does NOT work: rio-tiler's rescale calls
        # ``np.clip(image, imin, imax)`` internally, and numpy's clip with
        # ``imin > imax`` collapses every value to a single constant
        # (everything ends up at one end of the colormap — the all-green
        # Mahalanobis bug). Mirror the data values instead so the rescale
        # can run with a normal ``(vmin, vmax)`` ordering.
        if request.GET.get('invert') == '1':
            img.array = (vmin + vmax) - img.array
        img.rescale(in_range=((vmin, vmax),))
        # Default colormaps: viridis for inputs, rdylgn for results. A
        # ``?cmap=<name>`` query param overrides either; if rio-tiler doesn't
        # know the requested name, we fall back to the default.
        default_cmap = 'rdylgn' if is_result else 'viridis'
        cmap_name = request.GET.get('cmap') or default_cmap
        try:
            colormap = _rio_cmap.get(cmap_name)
        except Exception:
            colormap = _rio_cmap.get(default_cmap if default_cmap != cmap_name else 'viridis')
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
            'hist_bins':  info.get('hist_bins')  if info else None,
            'hist_edges': info.get('hist_edges') if info else None,
        })
    except Exception as exc:
        logger.exception('raster_meta failed for %s', full_path)
        return JsonResponse({'error': str(exc)}, status=500)


@require_GET
def query_point(request):
    """Sample a raster at a single WGS84 coordinate.

    Returns ``{'value': <float>}`` for a valid pixel, ``{'value': None,
    'nodata': True}`` for a masked / nodata pixel, or ``{'value': None,
    'out_of_bounds': True}`` if the coordinate is outside the raster.
    """
    full_path, _is_result = _resolve_tiled_source(request)
    if full_path is None:
        return JsonResponse({'error': 'Raster not found or path invalid'},
                            status=404)
    try:
        lat = float(request.GET.get('lat'))
        lng = float(request.GET.get('lng'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'lat and lng query params required'},
                            status=400)

    import rasterio
    from rasterio.crs import CRS as _CRS
    from rasterio.warp import transform as _rio_transform
    from rasterio.windows import Window as _Window
    wgs84 = _CRS.from_epsg(4326)

    try:
        with rasterio.open(full_path) as src:
            x, y = lng, lat

            # Reproject the click point into the raster's CRS if they differ.
            # Compare CRS objects directly to avoid quirks with ``to_epsg()``.
            if src.crs is not None and src.crs != wgs84:
                try:
                    xs, ys = _rio_transform(wgs84, src.crs, [lng], [lat])
                    x, y = xs[0], ys[0]
                except Exception:
                    logger.exception('CRS reprojection failed for %s', full_path)
                    # Continue with raw lat/lng; sample() may still work if the
                    # CRS difference is cosmetic (e.g. an unparsed WKT).

            # Map the (x, y) coordinate to a pixel (row, col). ``src.index``
            # works in the raster's CRS and always returns ints (or arrays of
            # ints in newer rasterio); we coerce to ints for safety.
            try:
                row, col = src.index(x, y)
                row, col = int(row), int(col)
            except Exception:
                return JsonResponse({'value': None, 'out_of_bounds': True})

            if row < 0 or col < 0 or row >= src.height or col >= src.width:
                return JsonResponse({'value': None, 'out_of_bounds': True})

            # Read just that one pixel from band 1.
            arr = src.read(1, window=_Window(col, row, 1, 1))
            if arr.size == 0:
                return JsonResponse({'value': None, 'out_of_bounds': True})
            val = float(arr.flat[0])

            # Treat declared nodata or NaN/inf as 'no data' (NaN-safe).
            nd = src.nodata
            if nd is not None:
                try:
                    if isinstance(nd, float) and math.isnan(nd):
                        if math.isnan(val):
                            return JsonResponse({'value': None, 'nodata': True})
                    elif val == nd:
                        return JsonResponse({'value': None, 'nodata': True})
                except (TypeError, ValueError):
                    pass
            if not math.isfinite(val):
                return JsonResponse({'value': None, 'nodata': True})
            return JsonResponse({'value': val})
    except Exception:
        logger.exception('query_point failed for %s', full_path)
        return JsonResponse({'value': None, 'out_of_bounds': True})


# ============================== PDF reports ==============================

def _render_result_png(raster_path: str, vmin: float, vmax: float,
                       invert: bool = False, cmap: str = 'rdylgn',
                       max_size: int = 1000) -> bytes:
    """Render a downsampled PNG snapshot of a raster with the given colormap
    (same set the tile endpoint uses). Returns PNG bytes.
    """
    from rio_tiler.io import Reader as _RioReader
    from rio_tiler.colormap import cmap as _rio_cmap
    with _RioReader(raster_path) as src:
        img = src.preview(max_size=max_size)
    # Reversing in_range as ``((vmax, vmin),)`` does NOT work for rio-tiler's
    # rescale: it calls ``np.clip(image, imin, imax)`` internally, and clip
    # with ``imin > imax`` collapses every value to a constant (everything
    # ends up rendered at one end of the colormap). Mirror the values
    # instead so we can rescale with a normal ``(vmin, vmax)`` ordering.
    if invert:
        img.array = (vmin + vmax) - img.array
    img.rescale(in_range=((vmin, vmax),))
    try:
        colormap = _rio_cmap.get(cmap)
    except Exception:
        colormap = _rio_cmap.get('viridis')
    return img.render(img_format='PNG', colormap=colormap)


def _cmap_opts_for_description(description: str, is_data_layer: bool = False):
    """Pick a (cmap, invert) pair for a processed-file or data raster, so the
    PDF map matches what the user saw in the browser (click-to-inspect logic).
    """
    if is_data_layer or not description:
        return ('viridis', False)
    if description == 'Mahalanobis Distance raster file':
        return ('rdylgn', True)
    # Suitability, MESS, anything else with a result-style ramp.
    return ('rdylgn', False)


def _report_styles():
    """Brand colours + Paragraph styles shared by every report builder.
    Returns ``(colors_dict, styles_dict)``.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    C = {
        'green':       colors.HexColor('#009933'),
        'green_dark':  colors.HexColor('#007a29'),
        'green_tint':  colors.HexColor('#eef6ef'),
        'ink':         colors.HexColor('#1f2d24'),
        'muted':       colors.HexColor('#5f6f64'),
        'border':      colors.HexColor('#e3e8e4'),
    }
    base = getSampleStyleSheet()
    S = {
        'h1': ParagraphStyle('TTH1', parent=base['Heading1'],
                             textColor=C['green'], fontSize=16, leading=20,
                             spaceAfter=2),
        'subtitle': ParagraphStyle('TTSub', parent=base['Normal'],
                                   textColor=C['muted'], fontSize=9,
                                   leading=12, spaceAfter=10),
        'h2': ParagraphStyle('TTH2', parent=base['Heading2'],
                             textColor=C['ink'], fontSize=11, leading=14,
                             spaceBefore=10, spaceAfter=4),
        'body': ParagraphStyle('TTBody', parent=base['Normal'],
                               textColor=C['ink'], fontSize=9, leading=12),
        'caption': ParagraphStyle('TTCap', parent=base['Normal'],
                                  fontSize=8, textColor=C['muted'],
                                  alignment=1, spaceAfter=8, leading=10),
        'footer': ParagraphStyle('TTFoot', parent=base['Normal'],
                                 fontSize=8, textColor=C['muted'],
                                 alignment=1, leading=10),
    }
    return C, S


def _result_image_flowable(raster_path, cmap='rdylgn', invert=False,
                            max_w_cm=17.0, max_h_cm=11.0,
                            aoi_str=None, points=None):
    """Render a result raster via rio-tiler and return a ReportLab Image
    flowable, sized to fit (max_w_cm x max_h_cm) preserving aspect ratio.

    Optional overlays:
    - ``aoi_str``: GeoJSON string. Draws the polygon outline on the image
      so the AOI's footprint is visible against the raster.
    - ``points``: iterable of ``[lng, lat]`` pairs. Draws white-haloed red
      dots at each sample-point location (used by the Similarity report).
    Overlay failures degrade gracefully — the un-overlayed PNG is used.
    """
    from reportlab.lib.units import cm
    from reportlab.platypus import Image as RLImage
    from PIL import Image as _PIL
    info = _get_raster_info(raster_path) or {}
    rng = info.get('range') or [0, 1]
    vmin, vmax = float(rng[0]), float(rng[1])
    if vmax <= vmin:
        vmax = vmin + 1.0
    png = _render_result_png(raster_path, vmin, vmax,
                             invert=invert, cmap=cmap)
    if aoi_str:
        png = _draw_aoi_overlay(png, raster_path, aoi_str)
    if points:
        png = _draw_points_overlay(png, raster_path, points)
    buf = io.BytesIO(png)
    with _PIL.open(buf) as test:
        iw, ih = test.size
    buf.seek(0)
    aspect = iw / max(ih, 1)
    if max_w_cm / aspect <= max_h_cm:
        w_cm, h_cm = max_w_cm, max_w_cm / aspect
    else:
        w_cm, h_cm = max_h_cm * aspect, max_h_cm
    return RLImage(buf, width=w_cm * cm, height=h_cm * cm)


def _aoi_geometries(aoi_value):
    """Flatten an AOI of any shape (FeatureCollection / Feature / Geometry)
    to a list of bare GeoJSON geometry dicts. Returns ``[]`` for invalid
    input.
    """
    if isinstance(aoi_value, str):
        try:
            aoi = json.loads(aoi_value)
        except Exception:
            return []
    else:
        aoi = aoi_value
    geometries = []

    def _walk(node):
        if isinstance(node, dict):
            t = node.get('type')
            if t == 'FeatureCollection':
                for f in node.get('features') or []:
                    _walk(f)
            elif t == 'Feature':
                _walk(node.get('geometry'))
            elif node.get('coordinates') is not None and node.get('type'):
                geometries.append(node)
        elif isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(aoi)
    return geometries


def _draw_aoi_overlay(png_bytes: bytes, raster_path: str, aoi_str: str) -> bytes:
    """Draw the AOI polygon outline over a rendered raster PNG. Returns
    modified PNG bytes (original bytes on any failure)."""
    geometries = _aoi_geometries(aoi_str)
    if not geometries:
        return png_bytes
    try:
        from PIL import Image, ImageDraw
        import rasterio
        from rasterio.crs import CRS
        from rasterio.warp import transform_geom

        with rasterio.open(raster_path) as src:
            bounds = src.bounds
            wgs84 = CRS.from_epsg(4326)
            if src.crs is not None and src.crs != wgs84:
                geometries = [transform_geom(wgs84, src.crs, g)
                              for g in geometries]

        img = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
        iw, ih = img.size
        bw = bounds.right - bounds.left
        bh = bounds.top - bounds.bottom
        if bw <= 0 or bh <= 0:
            return png_bytes

        def geo_to_px(x, y):
            return ((x - bounds.left) / bw * iw,
                    (bounds.top - y) / bh * ih)

        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        # Warm orange outline reads well over rdylgn / viridis.
        line_color = (240, 100, 30, 255)
        line_w = max(2, int(min(iw, ih) / 280))

        def draw_ring(coords):
            if not coords or len(coords) < 2:
                return
            pixels = [geo_to_px(c[0], c[1]) for c in coords]
            if pixels[0] != pixels[-1]:
                pixels.append(pixels[0])
            draw.line(pixels, fill=line_color, width=line_w, joint='curve')

        for g in geometries:
            t = g.get('type')
            coords = g.get('coordinates', [])
            if t == 'Polygon':
                for ring in coords:
                    draw_ring(ring)
            elif t == 'MultiPolygon':
                for poly in coords:
                    for ring in poly:
                        draw_ring(ring)
            elif t in ('LineString',):
                draw_ring(coords)
            elif t == 'MultiLineString':
                for line in coords:
                    draw_ring(line)

        out = Image.alpha_composite(img, overlay)
        buf = io.BytesIO()
        out.save(buf, 'PNG')
        return buf.getvalue()
    except Exception:
        logger.exception('AOI overlay failed for %s', raster_path)
        return png_bytes


def _draw_points_overlay(png_bytes: bytes, raster_path: str, points) -> bytes:
    """Draw small white-haloed dots at sample-point locations over a
    rendered PNG. Used by the Similarity report.
    """
    if not points:
        return png_bytes
    try:
        from PIL import Image, ImageDraw
        import rasterio
        from rasterio.crs import CRS
        from rasterio.warp import transform as _rio_transform

        lngs, lats = [], []
        for p in points:
            try:
                lngs.append(float(p[0])); lats.append(float(p[1]))
            except (TypeError, ValueError, IndexError):
                continue
        if not lngs:
            return png_bytes

        with rasterio.open(raster_path) as src:
            bounds = src.bounds
            wgs84 = CRS.from_epsg(4326)
            if src.crs is not None and src.crs != wgs84:
                xs, ys = _rio_transform(wgs84, src.crs, lngs, lats)
            else:
                xs, ys = lngs, lats

        img = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
        iw, ih = img.size
        bw = bounds.right - bounds.left
        bh = bounds.top - bounds.bottom
        if bw <= 0 or bh <= 0:
            return png_bytes

        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        outer_r = max(5, int(min(iw, ih) / 90))
        inner_r = max(2, outer_r - 3)
        halo_color = (255, 255, 255, 230)
        core_color = (220, 30, 60, 255)

        for x, y in zip(xs, ys):
            if x < bounds.left or x > bounds.right \
                    or y < bounds.bottom or y > bounds.top:
                continue
            px = (x - bounds.left) / bw * iw
            py = (bounds.top - y) / bh * ih
            draw.ellipse([(px - outer_r, py - outer_r),
                          (px + outer_r, py + outer_r)], fill=halo_color)
            draw.ellipse([(px - inner_r, py - inner_r),
                          (px + inner_r, py + inner_r)], fill=core_color)

        out = Image.alpha_composite(img, overlay)
        buf = io.BytesIO()
        out.save(buf, 'PNG')
        return buf.getvalue()
    except Exception:
        logger.exception('Points overlay failed for %s', raster_path)
        return png_bytes


def _compute_class_breakdown(raster_path: str, aoi_str: str = ''):
    """Count pixels per integer class (1..5) within the AOI, or across the
    full raster (downsampled) if no AOI is set. Returns ``{1: n, 2: n, ...}``
    or ``None`` if computation fails.
    """
    try:
        import numpy as np
        import rasterio

        geometries = _aoi_geometries(aoi_str) if aoi_str else []
        with rasterio.open(raster_path) as src:
            if geometries:
                from rasterio.mask import mask as rio_mask
                from rasterio.crs import CRS
                from rasterio.warp import transform_geom
                wgs84 = CRS.from_epsg(4326)
                if src.crs is not None and src.crs != wgs84:
                    try:
                        geometries = [transform_geom(wgs84, src.crs, g)
                                      for g in geometries]
                    except Exception:
                        geometries = None
                try:
                    out_image, _ = rio_mask(src, geometries, crop=True,
                                            filled=False, indexes=1)
                    arr = np.asarray(out_image).ravel()
                    if hasattr(out_image, 'mask') and out_image.mask is not False:
                        arr = arr[~np.asarray(out_image.mask).ravel()]
                except ValueError:
                    return None
            else:
                h, w = src.height, src.width
                scale = max(1, max(h, w) // 1024)
                out_h, out_w = max(1, h // scale), max(1, w // scale)
                band = src.read(1, masked=True, out_shape=(out_h, out_w))
                arr = band.compressed()

            arr = arr[np.isfinite(arr)]
            if arr.size == 0:
                return None
            arr_int = np.rint(arr).astype(int)
            return {c: int(np.sum(arr_int == c)) for c in range(1, 6)}
    except Exception:
        logger.exception('class breakdown failed for %s', raster_path)
        return None


def _class_breakdown_drawing(class_counts):
    """Build a small horizontal-bar chart of the 5-class breakdown. Each
    row: label, bar (coloured by class), percentage. Returns a ReportLab
    Drawing, or ``None`` if the counts are empty.
    """
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    total = sum(class_counts.values()) if class_counts else 0
    if not total:
        return None

    labels = ['Very low', 'Low', 'Moderate', 'High', 'Very high']
    bar_cols = ['#d73027', '#fc8d59', '#fee08b', '#91cf60', '#1a9850']

    width  = 17.0 * cm
    height = 4.6 * cm
    label_w = 2.2 * cm
    pct_w   = 1.4 * cm
    bar_max = width - label_w - pct_w - 0.4 * cm
    row_h   = height / 5
    bar_h   = row_h - 4

    d = Drawing(width, height)
    for i, c in enumerate(range(1, 6)):
        n = class_counts.get(c, 0)
        pct = n / total * 100
        bar_len = (pct / 100) * bar_max
        # Rows from top to bottom (class 1 at top).
        row_top = height - (i * row_h)
        y_bar = row_top - row_h + 2
        y_text = y_bar + bar_h / 2 - 3
        d.add(String(0, y_text, f'{c} — {labels[i]}',
                     fontSize=8.5, fillColor=colors.HexColor('#1f2d24')))
        d.add(Rect(label_w, y_bar, bar_max, bar_h,
                   fillColor=colors.HexColor('#f1f3f1'), strokeColor=None))
        d.add(Rect(label_w, y_bar, bar_len, bar_h,
                   fillColor=colors.HexColor(bar_cols[i]), strokeColor=None))
        d.add(String(label_w + bar_max + 4, y_text,
                     f'{pct:5.1f}%' if pct >= 0.1 else '<0.1%',
                     fontSize=8.5, fillColor=colors.HexColor('#1f2d24')))
    return d


def _fmt_num_for_pdf(v):
    """Format a numeric value for the PDF table; '-' for empty/None."""
    if v is None or v == '':
        return '—'
    try:
        f = float(v)
        if f.is_integer():
            return f'{int(f):,}'
        if abs(f) >= 100:
            return f'{f:,.1f}'
        return f'{f:.2f}'
    except (TypeError, ValueError):
        return str(v)


def _xml_escape_for_pdf(s) -> str:
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
                  .replace('>', '&gt;').replace('"', '&quot;'))


def _aoi_summary_text(aoi_str: str):
    """Walk a GeoJSON Feature / FeatureCollection / Geometry and return a
    short HTML-ish summary of its bounding box and geodesic area, or
    ``None`` if empty.
    """
    try:
        aoi = json.loads(aoi_str)
    except Exception:
        return None
    coords_flat = []
    rings = []  # list of [(lng, lat), ...] closed rings, for area calc

    def _push_ring(ring):
        pts = [(float(c[0]), float(c[1])) for c in ring if len(c) >= 2]
        if len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            rings.append(pts)

    def _walk(node):
        if isinstance(node, dict):
            geom = node.get('geometry', node)
            t = geom.get('type') if isinstance(geom, dict) else None
            coords = geom.get('coordinates') if isinstance(geom, dict) else None
            if t == 'Polygon' and coords:
                for ring in coords:
                    _push_ring(ring)
            elif t == 'MultiPolygon' and coords:
                for poly in coords:
                    for ring in poly:
                        _push_ring(ring)
            if isinstance(geom, dict) and 'coordinates' in geom:
                _walk(geom['coordinates'])
            for f in node.get('features') or []:
                _walk(f)
        elif isinstance(node, list):
            if (node and isinstance(node[0], (int, float))
                    and len(node) >= 2):
                coords_flat.append((float(node[0]), float(node[1])))
            else:
                for child in node:
                    _walk(child)

    _walk(aoi)
    if not coords_flat:
        return None
    lngs = [c[0] for c in coords_flat]
    lats = [c[1] for c in coords_flat]

    # Geodesic area in km² using the same spherical-excess approximation
    # the frontend uses, so the value in the side panel and the value in
    # the PDF match.
    area_km2 = 0.0
    for ring in rings:
        s = 0.0
        for i in range(len(ring) - 1):
            lng1, lat1 = ring[i]
            lng2, lat2 = ring[i + 1]
            s += math.radians(lng2 - lng1) * (
                2 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2)))
        area_km2 += abs(s * (6378137.0 ** 2) / 2) / 1e6

    if area_km2 >= 1:
        area_txt = f'{int(round(area_km2)):,} km²'
    elif area_km2 > 0:
        area_txt = f'{area_km2:.2f} km²'
    else:
        area_txt = ''

    return (
        f'Bounding box &nbsp;<b>{min(lngs):.3f}° to {max(lngs):.3f}° E</b>'
        f' &nbsp;·&nbsp; <b>{min(lats):.3f}° to {max(lats):.3f}° N</b>'
        f'<br/>Vertices: {len(coords_flat)}'
        + (f' &nbsp;·&nbsp; Area: <b>{area_txt}</b>' if area_txt else '')
    )


def _build_suitability_pdf(out_buf, title, raster_path, criteria, aoi_str):
    """Lay out a one-page Land Suitability report into ``out_buf``."""
    import datetime
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    C, S = _report_styles()
    doc = SimpleDocTemplate(
        out_buf, pagesize=A4,
        topMargin=1.4 * cm, bottomMargin=1.4 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        title='Land Suitability Report', author='Targeting Tools',
    )

    story = []
    story.append(Paragraph('Targeting Tools &mdash; Land Suitability Report', S['h1']))
    story.append(Paragraph(
        f'<b>{_xml_escape_for_pdf(title)}</b><br/>'
        f'Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}',
        S['subtitle']))

    # Map snapshot
    try:
        img_flow = _result_image_flowable(raster_path, cmap='rdylgn',
                                          invert=False,
                                          aoi_str=aoi_str)
        story.append(img_flow)
        story.append(Paragraph(
            '<font color="#d73027">■</font>'
            '<font color="#f46d43">■</font>'
            '<font color="#fee08b">■</font>'
            '<font color="#d9ef8b">■</font>'
            '<font color="#66bd63">■</font>'
            '<font color="#1a9850">■</font>'
            '&nbsp;&nbsp;<i>Low</i> &rarr; <i>High</i> suitability'
            + ('&nbsp;&nbsp;·&nbsp;&nbsp;<font color="#f0641e">▬</font> AOI outline'
               if aoi_str else ''),
            S['caption']))
    except Exception:
        logger.exception('report_suitability map render failed')
        story.append(Paragraph(
            '<i>Map snapshot could not be rendered.</i>', S['caption']))

    # Class breakdown (Distribution by suitability class)
    breakdown = _compute_class_breakdown(raster_path, aoi_str)
    if breakdown:
        story.append(Paragraph(
            'Distribution by suitability class'
            + (' (within AOI)' if aoi_str else ' (full extent)'),
            S['h2']))
        drawing = _class_breakdown_drawing(breakdown)
        if drawing is not None:
            story.append(drawing)

    # Criteria table
    if criteria:
        story.append(Paragraph('Criteria', S['h2']))
        rows = [['Layer', 'Min', 'Opt from', 'Opt to', 'Max', 'Combine']]
        for c in criteria:
            rows.append([
                Paragraph(_xml_escape_for_pdf(c.get('name', '')), S['body']),
                _fmt_num_for_pdf(c.get('min_val')),
                _fmt_num_for_pdf(c.get('opti_from')),
                _fmt_num_for_pdf(c.get('opti_to')),
                _fmt_num_for_pdf(c.get('max_val')),
                c.get('combine') or '—',
            ])
        t = Table(rows, colWidths=[5.6 * cm, 2.1 * cm, 2.1 * cm,
                                   2.1 * cm, 2.1 * cm, 2.1 * cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), C['green_tint']),
            ('TEXTCOLOR',  (0, 0), (-1, 0), C['ink']),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 9),
            ('LINEBELOW',  (0, 0), (-1, 0), 1.2, C['green']),
            ('GRID',       (0, 1), (-1, -1), 0.25, C['border']),
            ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
            ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(t)

    if aoi_str:
        info_text = _aoi_summary_text(aoi_str)
        if info_text:
            story.append(Paragraph('Area of interest', S['h2']))
            story.append(Paragraph(info_text, S['body']))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        '<i>Generated by Targeting Tools &mdash; '
        'Alliance Bioversity / CIAT</i>', S['footer']))
    doc.build(story)


def _build_similarity_pdf(out_buf, title, mnobis_path, mess_path,
                           selected_layers, points):
    """Lay out a one-page Land Similarity report.

    Two result thumbnails side-by-side (Mahalanobis on the left, MESS on the
    right), with a colour key under each, then the inputs used and the
    sample-points info.
    """
    import datetime
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    C, S = _report_styles()
    doc = SimpleDocTemplate(
        out_buf, pagesize=A4,
        topMargin=1.4 * cm, bottomMargin=1.4 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        title='Land Similarity Report', author='Targeting Tools',
    )

    story = []
    story.append(Paragraph('Targeting Tools &mdash; Land Similarity Report', S['h1']))
    story.append(Paragraph(
        f'<b>{_xml_escape_for_pdf(title)}</b><br/>'
        f'Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}',
        S['subtitle']))

    # Two thumbnails side-by-side (each fits ~8.2 cm wide).
    def _result_cell(path, label, cmap='rdylgn', invert=False):
        try:
            img = _result_image_flowable(path, cmap=cmap, invert=invert,
                                         max_w_cm=8.2, max_h_cm=8.0,
                                         points=points)
            cap = Paragraph(label, S['caption'])
            return [img, cap]
        except Exception:
            logger.exception('similarity map render failed for %s', path)
            return [Paragraph('<i>Map snapshot unavailable.</i>',
                              S['caption']),
                    Paragraph(label, S['caption'])]

    legend_html = (
        '<font color="#d73027">■</font>'
        '<font color="#f46d43">■</font>'
        '<font color="#fee08b">■</font>'
        '<font color="#d9ef8b">■</font>'
        '<font color="#66bd63">■</font>'
        '<font color="#1a9850">■</font>'
        '&nbsp;&nbsp;<i>Low</i> &rarr; <i>High</i> similarity'
        + ('<br/><font color="#dc1e3c">●</font> Sample points' if points else ''))

    cells = []
    if mnobis_path:
        cells.append(_result_cell(mnobis_path,
                                   '<b>Mahalanobis</b><br/>' + legend_html,
                                   invert=True))
    if mess_path:
        cells.append(_result_cell(mess_path,
                                   '<b>MESS</b><br/>' + legend_html,
                                   invert=False))

    if len(cells) == 2:
        # Two columns
        t = Table([[cells[0], cells[1]]],
                  colWidths=[8.5 * cm, 8.5 * cm])
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(t)
    elif len(cells) == 1:
        for el in cells[0]:
            story.append(el)

    # Selected layers
    if selected_layers:
        story.append(Paragraph('Selected layers', S['h2']))
        rows = [[Paragraph(_xml_escape_for_pdf(n), S['body'])]
                for n in selected_layers]
        t = Table(rows, colWidths=[17 * cm])
        t.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.25, C['border']),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(t)

    # Sample points
    if points:
        story.append(Paragraph('Sample points', S['h2']))
        if len(points) > 0:
            lngs = [float(p[0]) for p in points]
            lats = [float(p[1]) for p in points]
            sumtxt = (
                f'<b>{len(points)} point{"s" if len(points) != 1 else ""}</b>'
                f' &middot; bounding box '
                f'<b>{min(lngs):.3f}° to {max(lngs):.3f}° E</b>'
                f', <b>{min(lats):.3f}° to {max(lats):.3f}° N</b>')
            story.append(Paragraph(sumtxt, S['body']))
            preview = points[:8]
            coord_lines = ' &middot; '.join(
                f'({float(p[0]):.3f}, {float(p[1]):.3f})' for p in preview)
            if len(points) > 8:
                coord_lines += f' &middot; …+{len(points) - 8} more'
            story.append(Spacer(1, 4))
            story.append(Paragraph(coord_lines, S['body']))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        '<i>Generated by Targeting Tools &mdash; '
        'Alliance Bioversity / CIAT</i>', S['footer']))
    doc.build(story)


def _build_statistics_pdf(out_buf, title, raster_path, raster_description,
                           reference_layer_name, stat_types, results):
    """Lay out a one-page Land Statistics report.

    Map snapshot of the processed file (colormap auto-picked from the file
    type), then parameters, an embedded bar chart of the first selected
    statistic, and the full results table.
    """
    import datetime
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.charts.barcharts import VerticalBarChart

    C, S = _report_styles()
    doc = SimpleDocTemplate(
        out_buf, pagesize=A4,
        topMargin=1.4 * cm, bottomMargin=1.4 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        title='Land Statistics Report', author='Targeting Tools',
    )

    story = []
    story.append(Paragraph('Targeting Tools &mdash; Land Statistics Report', S['h1']))
    story.append(Paragraph(
        f'<b>{_xml_escape_for_pdf(title)}</b><br/>'
        f'Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}',
        S['subtitle']))

    # ---- Map snapshot (auto-picked colormap based on file type) ----
    cmap_name, invert = _cmap_opts_for_description(raster_description)
    try:
        img_flow = _result_image_flowable(raster_path, cmap=cmap_name,
                                          invert=invert,
                                          max_w_cm=17.0, max_h_cm=9.0)
        story.append(img_flow)
    except Exception:
        logger.exception('report_statistics map render failed')
        story.append(Paragraph(
            '<i>Map snapshot could not be rendered.</i>', S['caption']))

    # ---- Parameters block ----
    story.append(Paragraph('Parameters', S['h2']))
    param_rows = [['Field', 'Value']]
    if raster_description:
        param_rows.append(['Source type', raster_description])
    if reference_layer_name:
        param_rows.append(['Reference layer', reference_layer_name])
    if stat_types:
        param_rows.append(['Statistics', ', '.join(stat_types)])
    if len(param_rows) > 1:
        t = Table(param_rows, colWidths=[4.5 * cm, 12.5 * cm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), C['green_tint']),
            ('TEXTCOLOR',  (0, 0), (-1, 0), C['ink']),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 9),
            ('LINEBELOW',  (0, 0), (-1, 0), 1.2, C['green']),
            ('GRID',       (0, 1), (-1, -1), 0.25, C['border']),
            ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(t)

    # ---- Results table + chart ----
    if results and isinstance(results, list) and results:
        item = results[0] or {}
        stat_label = item.get('stat_label') or {}
        per_class = item.get('statistics') or {}
        class_names = list(stat_label.keys())
        stats_used = stat_types or (
            list(next(iter(per_class.values()), {}).keys())
            if per_class else []
        )

        # Bar chart of the first selected stat across the classes.
        if class_names and stats_used:
            chart_stat = stats_used[0]
            values = []
            for cn in class_names:
                v = (per_class.get(cn) or {}).get(chart_stat)
                try:
                    values.append(float(v))
                except (TypeError, ValueError):
                    values.append(0.0)
            story.append(Paragraph(
                f'{chart_stat.capitalize()} by class', S['h2']))
            drawing = Drawing(17 * cm, 6.5 * cm)
            bc = VerticalBarChart()
            bc.x = 50
            bc.y = 25
            bc.height = 6.0 * cm - 25
            bc.width = 17 * cm - 60
            bc.data = [values]
            bc.categoryAxis.categoryNames = [str(n) for n in class_names]
            bc.categoryAxis.labels.fontSize = 7
            bc.categoryAxis.labels.angle = 20
            bc.categoryAxis.labels.dx = -8
            bc.categoryAxis.labels.dy = -2
            bc.valueAxis.labels.fontSize = 7
            bc.valueAxis.valueMin = 0
            bc.bars[0].fillColor = C['green']
            bc.bars[0].strokeColor = C['green_dark']
            bc.bars[0].strokeWidth = 0.5
            drawing.add(bc)
            story.append(drawing)

        # Full results table.
        story.append(Paragraph('Results', S['h2']))
        header = ['Class', 'Land Suitability Area (%)'] + [
            s.capitalize() for s in stats_used
        ]
        rows = [header]
        for cn in class_names:
            row = [Paragraph(_xml_escape_for_pdf(cn), S['body']),
                   _fmt_num_for_pdf(stat_label.get(cn))]
            for s in stats_used:
                row.append(_fmt_num_for_pdf((per_class.get(cn) or {}).get(s)))
            rows.append(row)
        # Compute column widths so wide-valued stats like ``sum`` don't get
        # squished into a column that fits only "1,2…". Reserve fixed space
        # for ``sum`` (large totals) and ``mean`` (fractional), share the
        # remainder between the others.
        total_w = 17.0
        class_w  = 4.2
        area_w   = 2.6
        wide_stats = {'sum'}
        med_stats  = {'mean', 'median', 'max', 'min'}
        wide_per   = 2.2  # cm each
        med_per    = 1.55
        small_per  = 1.25
        n_wide = sum(1 for s in stats_used if s.lower() in wide_stats)
        n_med  = sum(1 for s in stats_used if s.lower() in med_stats)
        n_small = max(0, len(stats_used) - n_wide - n_med)
        used = class_w + area_w + n_wide * wide_per + n_med * med_per + n_small * small_per
        # Scale down proportionally if we'd overflow the page width.
        if used > total_w:
            scale = total_w / used
            class_w *= scale; area_w *= scale
            wide_per *= scale; med_per *= scale; small_per *= scale
        col_widths = [class_w * cm, area_w * cm]
        for s in stats_used:
            sl = s.lower()
            if sl in wide_stats:   col_widths.append(wide_per * cm)
            elif sl in med_stats:  col_widths.append(med_per * cm)
            else:                  col_widths.append(small_per * cm)
        t = Table(rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), C['green_tint']),
            ('TEXTCOLOR',  (0, 0), (-1, 0), C['ink']),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 8.5),
            ('LINEBELOW',  (0, 0), (-1, 0), 1.2, C['green']),
            ('GRID',       (0, 1), (-1, -1), 0.25, C['border']),
            ('ALIGN',      (1, 0), (-1, -1), 'RIGHT'),
            ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(t)

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        '<i>Generated by Targeting Tools &mdash; '
        'Alliance Bioversity / CIAT</i>', S['footer']))
    doc.build(story)


@require_POST
@csrf_protect
def report_suitability(request):
    """Generate and return a PDF report for a Land Suitability analysis."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    result_path = data.get('result_path')
    if not result_path:
        return JsonResponse({'error': 'result_path is required'},
                            status=400)
    full_path = _resolve_result_path(result_path)
    if not full_path:
        return JsonResponse({'error': 'Result file not found'},
                            status=404)

    description = (data.get('description') or 'Untitled analysis').strip()
    criteria = data.get('criteria') or []
    aoi = data.get('aoi') or ''

    try:
        import reportlab  # noqa: F401
    except ImportError:
        return JsonResponse({
            'error': 'ReportLab is not installed. Run '
                     '`pip install reportlab` in your environment.'
        }, status=500)

    pdf_buf = io.BytesIO()
    try:
        _build_suitability_pdf(pdf_buf, description, full_path, criteria, aoi)
    except Exception:
        logger.exception('report_suitability PDF build failed')
        return JsonResponse({'error': 'Could not generate PDF.'},
                            status=500)
    pdf_buf.seek(0)

    response = HttpResponse(pdf_buf.read(), content_type='application/pdf')
    safe_name = re.sub(r'[^\w\-]+', '_', description)[:60] or 'suitability_report'
    response['Content-Disposition'] = f'attachment; filename="{safe_name}.pdf"'
    return response


@require_POST
@csrf_protect
def report_similarity(request):
    """Generate and return a PDF report for a Land Similarity analysis."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    rp = data.get('result_paths') or {}
    mnobis_rel = rp.get('mnobis')
    mess_rel = rp.get('mess')
    if not (mnobis_rel or mess_rel):
        return JsonResponse(
            {'error': 'result_paths.{mnobis,mess} required'}, status=400)

    mnobis_full = _resolve_result_path(mnobis_rel) if mnobis_rel else None
    mess_full = _resolve_result_path(mess_rel) if mess_rel else None
    if not (mnobis_full or mess_full):
        return JsonResponse({'error': 'No result file found'}, status=404)

    description = (data.get('description') or 'Untitled analysis').strip()
    selected_layers = data.get('selected_layers') or []
    points = data.get('points') or []
    # Frontend may pass points as a JSON string (the pointsInput value).
    if isinstance(points, str):
        try:
            points = json.loads(points)
        except Exception:
            points = []

    try:
        import reportlab  # noqa: F401
    except ImportError:
        return JsonResponse({
            'error': 'ReportLab is not installed. Run '
                     '`pip install reportlab` in your environment.'
        }, status=500)

    pdf_buf = io.BytesIO()
    try:
        _build_similarity_pdf(
            pdf_buf, description, mnobis_full, mess_full,
            selected_layers, points,
        )
    except Exception:
        logger.exception('report_similarity PDF build failed')
        return JsonResponse({'error': 'Could not generate PDF.'}, status=500)
    pdf_buf.seek(0)

    response = HttpResponse(pdf_buf.read(), content_type='application/pdf')
    safe_name = re.sub(r'[^\w\-]+', '_', description)[:60] or 'similarity_report'
    response['Content-Disposition'] = f'attachment; filename="{safe_name}.pdf"'
    return response


@require_POST
@csrf_protect
def report_statistics(request):
    """Generate and return a PDF report for a Land Statistics analysis."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    raster_path = data.get('raster_path')
    if not raster_path:
        return JsonResponse({'error': 'raster_path is required'}, status=400)
    full_path = _resolve_result_path(raster_path)
    if not full_path:
        return JsonResponse({'error': 'Result file not found'}, status=404)

    description = (data.get('description') or 'Untitled analysis').strip()
    raster_description = data.get('raster_description') or ''
    reference_layer_name = data.get('reference_layer_name') or ''
    stat_types = data.get('stat_types') or []
    results = data.get('results') or []

    try:
        import reportlab  # noqa: F401
    except ImportError:
        return JsonResponse({
            'error': 'ReportLab is not installed. Run '
                     '`pip install reportlab` in your environment.'
        }, status=500)

    pdf_buf = io.BytesIO()
    try:
        _build_statistics_pdf(
            pdf_buf, description, full_path, raster_description,
            reference_layer_name, stat_types, results,
        )
    except Exception:
        logger.exception('report_statistics PDF build failed')
        return JsonResponse({'error': 'Could not generate PDF.'}, status=500)
    pdf_buf.seek(0)

    response = HttpResponse(pdf_buf.read(), content_type='application/pdf')
    safe_name = re.sub(r'[^\w\-]+', '_', description)[:60] or 'statistics_report'
    response['Content-Disposition'] = f'attachment; filename="{safe_name}.pdf"'
    return response


# ============================== AOI histogram ==============================

@require_POST
@csrf_protect
def aoi_histogram(request):
    """Compute a histogram of a raster's values WITHIN the user's AOI polygon.

    POST JSON body: ``{path, aoi, bins}`` where ``aoi`` is a GeoJSON
    FeatureCollection / Feature / Geometry. Returns
    ``{bins: [counts...], edges: [...], min, max, count_total}``.

    This is used by the Suitability criteria cards to overlay a value-
    distribution histogram behind the trapezoid slider, so the user can
    pick min / opt_from / opt_to / max with awareness of what data
    actually exists in their area of interest.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    path_param = data.get('path')
    aoi_value = data.get('aoi')
    try:
        n_bins = int(data.get('bins') or 20)
    except (TypeError, ValueError):
        n_bins = 20
    n_bins = max(5, min(60, n_bins))

    if not path_param or not aoi_value:
        return JsonResponse({'error': 'path and aoi are required'}, status=400)

    full_path = _resolve_raster_path(path_param)
    if not full_path:
        return JsonResponse({'error': 'Raster not found'}, status=404)

    if isinstance(aoi_value, str):
        try:
            aoi = json.loads(aoi_value)
        except Exception:
            return JsonResponse({'error': 'Invalid AOI JSON'}, status=400)
    else:
        aoi = aoi_value

    # Flatten an AOI of any shape (FeatureCollection / Feature / Geometry)
    # to a list of bare geometry dicts that rasterio.mask understands.
    geometries = []

    def _walk(node):
        if isinstance(node, dict):
            t = node.get('type')
            if t == 'FeatureCollection':
                for f in node.get('features') or []:
                    _walk(f)
            elif t == 'Feature':
                _walk(node.get('geometry'))
            elif node.get('coordinates') is not None and node.get('type'):
                geometries.append(node)
        elif isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(aoi)
    if not geometries:
        return JsonResponse({'error': 'No polygon geometry in AOI'},
                            status=400)

    import numpy as np
    import rasterio
    from rasterio.mask import mask as rio_mask
    from rasterio.crs import CRS as _CRS
    from rasterio.warp import transform_geom

    try:
        with rasterio.open(full_path) as src:
            # Reproject AOI from WGS84 to the raster's native CRS if needed.
            wgs84 = _CRS.from_epsg(4326)
            if src.crs is not None and src.crs != wgs84:
                try:
                    geometries = [transform_geom(wgs84, src.crs, g)
                                  for g in geometries]
                except Exception:
                    logger.exception('aoi_histogram: AOI reprojection failed')

            try:
                out_image, _ = rio_mask(src, geometries, crop=True,
                                        filled=False, indexes=1)
            except ValueError:
                # AOI doesn't overlap the raster's footprint.
                return JsonResponse({
                    'bins': [], 'edges': [], 'min': None, 'max': None,
                    'count_total': 0,
                })

            # ``out_image`` is a MaskedArray when ``filled=False`` and a
            # plain ndarray otherwise. Normalise to 1-D of valid floats.
            arr = np.asarray(out_image).ravel()
            if hasattr(out_image, 'mask') and out_image.mask is not False:
                mask_arr = np.asarray(out_image.mask).ravel()
                arr = arr[~mask_arr]
            if src.nodata is not None:
                try:
                    if math.isnan(src.nodata):
                        arr = arr[~np.isnan(arr)]
                    else:
                        arr = arr[arr != src.nodata]
                except (TypeError, ValueError):
                    pass
            # Drop any residual NaN/inf even if nodata wasn't declared.
            arr = arr[np.isfinite(arr)]

            if arr.size == 0:
                return JsonResponse({
                    'bins': [], 'edges': [], 'min': None, 'max': None,
                    'count_total': 0,
                })

            # Cap the sample for speed; with 200k random pixels the histogram
            # shape is stable to within rounding for any reasonable AOI.
            if arr.size > 200_000:
                rng = np.random.default_rng(0)
                idx = rng.choice(arr.size, 200_000, replace=False)
                arr = arr[idx]

            counts, edges = np.histogram(arr, bins=n_bins)
            return JsonResponse({
                'bins':        counts.astype(int).tolist(),
                'edges':       [float(e) for e in edges],
                'min':         float(arr.min()),
                'max':         float(arr.max()),
                'count_total': int(arr.size),
            })
    except Exception:
        logger.exception('aoi_histogram failed for %s', full_path)
        return JsonResponse({'error': 'Failed to compute histogram'},
                            status=500)


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

    # Order alphabetically with directories listed before files, case-
    # insensitive — so continents/countries/files are predictable regardless
    # of what order the filesystem happens to return them in.
    contents.sort(key=lambda c: (c['type'] != 'directory', c['name'].lower()))

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

        def _strip_media(u):
            if not u:
                return None
            if u.startswith('/media/'):
                return u[len('/media/'):]
            if u.startswith('media/'):
                return u[len('media/'):]
            return u

        return JsonResponse({
            'status': 'success',
            'result_url': {
                'mnobis': request.build_absolute_uri(result['Mahalanobis']),
                'mess': request.build_absolute_uri(result['MESS']),
            },
            'result_path': {
                'mnobis': _strip_media(result.get('Mahalanobis')),
                'mess': _strip_media(result.get('MESS')),
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
