

/* =========================
   Validation helpers
========================= */
function validateOptiFrom(optiFromInput) {
  const row = optiFromInput.closest("tr");
  const minVal = parseFloat(row.querySelector('input[name*="[min_val]"]').value);
  const optiFrom = parseFloat(optiFromInput.value);
  const optiToInput = row.querySelector('input[name*="[opti_to]"]');
  const optiTo = parseFloat(optiToInput.value);

  // minVal <= optiFrom
  if (isNaN(optiFrom) || isNaN(minVal) || optiFrom < minVal) {
    alert(`Optimum From must be >= Minimum Value (${minVal}).`);
    optiFromInput.value = "";
    optiFromInput.focus();
    return;
  }

  // optiFrom <= optiTo (only if optiTo already filled)
  if (!isNaN(optiTo) && optiFrom > optiTo) {
    alert(`Optimum From must be <= Optimum To (${optiTo}).`);
    optiFromInput.value = "";
    optiFromInput.focus();
    return;
  }
}

function validateOptiTo(optiToInput) {
  const row = optiToInput.closest("tr");
  const maxVal = parseFloat(row.querySelector('input[name*="[max_val]"]').value);
  const optiTo = parseFloat(optiToInput.value);
  const optiFromInput = row.querySelector('input[name*="[opti_from]"]');
  const optiFrom = parseFloat(optiFromInput.value);

  // optiTo <= maxVal
  if (isNaN(optiTo) || isNaN(maxVal) || optiTo > maxVal) {
    alert(`Optimal To must be <= Maximum Value (${maxVal}).`);
    optiToInput.value = "";
    optiToInput.focus();
    return;
  }

  // optiFrom <= optiTo (only if optiFrom already filled)
  if (!isNaN(optiFrom) && optiFrom > optiTo) {
    alert(`Optimum To must be >= Optimum From (${optiFrom}).`);
    optiToInput.value = "";
    optiToInput.focus();
    return;
  }
}

/* =========================
   AOI helpers
========================= */
function toFeatureCollection(geojson) {
  if (!geojson) throw new Error("Empty GeoJSON");

  if (typeof geojson === "string") geojson = JSON.parse(geojson);

  if (geojson.type === "FeatureCollection") return geojson;

  if (geojson.type === "Feature") {
    return { type: "FeatureCollection", features: [geojson] };
  }

  // Raw geometry
  if (geojson.type && geojson.coordinates) {
    return {
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: {}, geometry: geojson }],
    };
  }

  throw new Error("Unsupported GeoJSON structure");
}

function validateFeatureCollection(fc) {
  if (!fc || fc.type !== "FeatureCollection" || !Array.isArray(fc.features) || fc.features.length === 0) {
    throw new Error("AOI must be a GeoJSON FeatureCollection with at least one feature.");
  }

  const polygons = fc.features.filter((f) => {
    const g = f && f.geometry;
    return g && (g.type === "Polygon" || g.type === "MultiPolygon");
  });

  if (polygons.length === 0) {
    throw new Error("AOI must contain a Polygon or MultiPolygon (not just points/lines).");
  }

  return { type: "FeatureCollection", features: polygons };
}

function addGeoJSONToMap(fc, map, drawnItems) {
  drawnItems.clearLayers();

  const layer = L.geoJSON(fc, { style: { weight: 2 } });
  layer.eachLayer((l) => drawnItems.addLayer(l));

  const bounds = layer.getBounds();
  if (bounds && bounds.isValid()) {
    map.fitBounds(bounds.pad(0.1));
  }
}

function setAOI(fc, map, drawnItems, aoiInput, aoiStatusEl) {
  const cleaned = validateFeatureCollection(fc);
  addGeoJSONToMap(cleaned, map, drawnItems);
  aoiInput.value = JSON.stringify(cleaned);

  if (aoiStatusEl) {
    aoiStatusEl.textContent = `AOI loaded: ${cleaned.features.length} polygon(s).`;
  }
}

function clearAOI(drawnItems, aoiInput, aoiStatusEl) {
  drawnItems.clearLayers();
  aoiInput.value = "";
  if (aoiStatusEl) {
    aoiStatusEl.textContent = "No AOI selected. Draw on the map or upload a file.";
  }
}

async function parseKMLToGeoJSON(kmlText) {
  if (typeof toGeoJSON === "undefined") {
    throw new Error("KML support needs toGeoJSON library loaded.");
  }
  const parser = new DOMParser();
  const xml = parser.parseFromString(kmlText, "text/xml");
  return toGeoJSON.kml(xml);
}

async function parseShapefileZipToGeoJSON(arrayBuffer) {
  if (typeof shp === "undefined") {
    throw new Error("Shapefile ZIP support needs shpjs library loaded.");
  }
  return await shp(arrayBuffer);
}

/* =========================
   Main app
========================= */
$(document).ready(function () {

  /* Server-provided config (API endpoints) — read from the
     #js-config json_script block rendered by the template. */
  const CONFIG = (function () {
    const el = document.getElementById("js-config");
    try { return el ? JSON.parse(el.textContent) : {}; }
    catch (e) { console.error("Invalid #js-config JSON:", e); return {}; }
  })();
  const API = CONFIG.apiEndpoints || {};
  const mapSection = document.getElementById("mapSection") || document.getElementById("map-container")?.parentElement;
  const fileUploadSection = document.getElementById("fileUploadSection");
  const aoiOptionMap = document.getElementById("aoiOptionMap");
  const aoiOptionFile = document.getElementById("aoiOptionFile");

  const aoiInput = document.getElementById("aoiInput");
  const aoiStatusEl = document.getElementById("aoiStatus");
  const clearAoiBtn = document.getElementById("clearAoiBtn");

  const fileListElement = document.getElementById("fileList");

  // --- Leaflet state ---
  let map = null;
  let resultUrl = "";
  let currentResultKey = null;
  let popupMap = null;
  let drawnItems = new L.FeatureGroup();

  /* =========================
     Leaflet init (ROBUST)
  ========================= */
  function ensureMapReady() {
    const container = document.getElementById("map-container");
    if (!container) {
      console.error("map-container not found in DOM.");
      return;
    }

    // If map already exists, just ensure sizing
    if (map) {
      setTimeout(() => map.invalidateSize(true), 200);
      return;
    }

    // Create map
    map = L.map("map-container", { preferCanvas: true }).setView([0, 0], 2);

    // Dedicated pane for input-raster previews so they always sit above the
    // basemap (even after switching basemaps) but below drawn AOI vectors.
    map.createPane("rasterOverlays");
    map.getPane("rasterOverlays").style.zIndex = 350;

    // Basemaps (Esri layers need no API key, just attribution).
    const baseLayers = {
      Street: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap contributors",
        maxZoom: 19,
      }),
      Satellite: L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        { attribution: "Imagery &copy; Esri", maxZoom: 19 }
      ),
      Topographic: L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        { attribution: "Tiles &copy; Esri", maxZoom: 19 }
      ),
    };
    baseLayers.Street.addTo(map);
    L.control.layers(baseLayers, null, { position: "bottomleft", collapsed: true }).addTo(map);

    // Scale bar (metric).
    L.control.scale({ position: "bottomleft", imperial: false }).addTo(map);

    map.addLayer(drawnItems);

    const drawControl = new L.Control.Draw({
      position: "topleft",
      draw: {
        rectangle: true,
        polygon: true,
        polyline: false,
        circle: false,
        marker: false,
        circlemarker: false,
      },
      edit: { featureGroup: drawnItems, remove: true },
    });

    map.addControl(drawControl);

    map.on(L.Draw.Event.CREATED, function (event) {
      drawnItems.clearLayers();
      const layer = event.layer;
      drawnItems.addLayer(layer);

      try {
        const fc = toFeatureCollection(layer.toGeoJSON());
        setAOI(fc, map, drawnItems, aoiInput, aoiStatusEl);
      } catch (e) {
        console.error(e);
        alert(`Invalid AOI: ${e.message}`);
        clearAOI(drawnItems, aoiInput, aoiStatusEl);
      }
    });

    map.on("draw:deleted", function () {
      clearAOI(drawnItems, aoiInput, aoiStatusEl);
    });

    // Click any pixel to inspect raster values. Suppress while a draw / edit
    // / delete handler is active (otherwise polygon clicks would also fire).
    let inspectSuppressed = false;
    map.on('draw:drawstart draw:editstart draw:deletestart', () => { inspectSuppressed = true; });
    map.on('draw:drawstop  draw:editstop  draw:deletestop',  () => { inspectSuppressed = false; });
    map.on('click', async (e) => {
      if (inspectSuppressed) return;
      const results = await MapLayers.queryAt(e.latlng);
      if (results.length === 0) return;
      L.popup({ maxWidth: 280, className: 'inspect-popup' })
        .setLatLng(e.latlng)
        .setContent(renderInspectHtml(e.latlng, results))
        .openOn(map);
    });

    // Leaflet needs a size refresh after first paint
    setTimeout(() => map.invalidateSize(true), 250);
  }

  function showMapAndRefresh() {
    if (mapSection) mapSection.style.display = "block";
    const mapContainer = document.getElementById("map-container");
    if (mapContainer) mapContainer.style.display = "block";
    ensureMapReady();

    // Extra refresh: helps when hidden inside bootstrap cards
    setTimeout(() => map && map.invalidateSize(true), 300);
    setTimeout(() => map && map.invalidateSize(true), 700);
  }

  /* =========================
     AOI mode toggles
  ========================= */
  if (aoiOptionMap) {
    aoiOptionMap.addEventListener("change", () => {
      if (!aoiOptionMap.checked) return;
      if (fileUploadSection) fileUploadSection.style.display = "none";
      showMapAndRefresh();
    });
  }

  if (aoiOptionFile) {
    aoiOptionFile.addEventListener("change", () => {
      if (!aoiOptionFile.checked) return;
      if (fileUploadSection) fileUploadSection.style.display = "block";
      showMapAndRefresh(); // keep map visible
    });
  }

  if (clearAoiBtn) {
    clearAoiBtn.addEventListener("click", () => {
      clearAOI(drawnItems, aoiInput, aoiStatusEl);
      const f = document.getElementById("aoiFileUpload");
      if (f) f.value = "";
    });
  }

  /* =========================
     AOI file upload
  ========================= */
  const aoiFileEl = document.getElementById("aoiFileUpload");
  if (aoiFileEl) {
    aoiFileEl.addEventListener("change", async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;

      // Force UI into file mode + ensure map is visible
      if (aoiOptionFile) aoiOptionFile.checked = true;
      if (fileUploadSection) fileUploadSection.style.display = "block";
      showMapAndRefresh();

      try {
        const name = (file.name || "").toLowerCase();

        if (name.endsWith(".geojson") || name.endsWith(".json")) {
          const text = await file.text();
          const gj = JSON.parse(text);
          const fc = toFeatureCollection(gj);
          setAOI(fc, map, drawnItems, aoiInput, aoiStatusEl);
          return;
        }

        if (name.endsWith(".kml")) {
          const text = await file.text();
          const gj = await parseKMLToGeoJSON(text);
          const fc = toFeatureCollection(gj);
          setAOI(fc, map, drawnItems, aoiInput, aoiStatusEl);
          return;
        }

        if (name.endsWith(".zip")) {
          const buf = await file.arrayBuffer();
          const gj = await parseShapefileZipToGeoJSON(buf);
          const fc = toFeatureCollection(gj);
          setAOI(fc, map, drawnItems, aoiInput, aoiStatusEl);
          return;
        }

        throw new Error("Unsupported file type. Upload .geojson/.json, .kml, or a shapefile .zip.");
      } catch (err) {
        console.error(err);
        alert(`AOI upload failed: ${err.message}`);
        clearAOI(drawnItems, aoiInput, aoiStatusEl);
        event.target.value = "";
      }
    });
  }

  /* =========================
     Map layers — preview selected input rasters on the main map
     via the server-side tile endpoint (scales to large rasters).
  ========================= */
  const MapLayers = (function () {
    const active = new Map();   // filePath -> entry { fileName, leafletLayer?, bounds?, loading?, error?, visible, opacity }
    let firstAddDone = false;

    function controlEl() {
      return document.getElementById("mapLayersControl");
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g,
        c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    function fmtVal(v) {
      if (v === null || v === undefined || !isFinite(v)) return "—";
      const a = Math.abs(v);
      if (a >= 1000) return Math.round(v).toLocaleString();
      if (a >= 1) return String(Math.round(v * 100) / 100);
      if (a === 0) return "0";
      return Number(v).toPrecision(2);
    }

    async function addLayer(layerKey, fileName, opts) {
      if (!map) return;
      if (active.has(layerKey)) return;
      if (!API.tileRaster || !API.rasterMeta) return;

      opts = opts || {};
      const isResult = opts.source === "result";
      const srcParam = isResult ? "&source=result" : "";
      const opacity = typeof opts.opacity === "number" ? opts.opacity : 0.7;

      // Optimistic entry so the control shows a spinner while we set up.
      active.set(layerKey, {
        fileName, loading: true, visible: true, opacity, isResult,
      });
      render();

      try {
        // 1. Raster bounds — reuse a pre-fetched meta if the caller passed
        //    one, otherwise fetch it (small JSON; lets us fitBounds without
        //    downloading the whole file).
        let meta = opts.meta;
        if (!meta) {
          const metaResp = await fetch(`${API.rasterMeta}?path=${encodeURIComponent(layerKey)}${srcParam}`);
          if (!metaResp.ok) throw new Error(`metadata HTTP ${metaResp.status}`);
          meta = await metaResp.json();
        }
        if (!meta || !meta.bounds) throw new Error("no bounds in metadata response");

        // The user may have removed the layer while we were waiting.
        if (!active.has(layerKey)) return;

        // 2. Set up a Leaflet tile layer pointing at the tile endpoint.
        //    The browser fetches only the 256x256 tiles it needs for the
        //    current view; nothing is loaded into memory client-side.
        const tileUrl = `${API.tileRaster}?path=${encodeURIComponent(layerKey)}${srcParam}`;
        const bounds = L.latLngBounds(
          [meta.bounds[1], meta.bounds[0]],   // SW (south, west)
          [meta.bounds[3], meta.bounds[2]]    // NE (north, east)
        );
        const layerOpts = {
          opacity,
          bounds,
          tileSize: 256,
          noWrap: true,
          pane: "rasterOverlays",
        };
        // Coarse rasters (e.g. climate layers) have a low native zoom. Cap
        // native tile requests at that zoom and let Leaflet upscale for
        // deeper zooms, so they stay visible instead of requesting blank
        // over-zoom tiles.
        if (typeof meta.maxzoom === "number") {
          layerOpts.maxNativeZoom = meta.maxzoom;
        }
        const leafletLayer = L.tileLayer(tileUrl, layerOpts);
        leafletLayer.addTo(map);

        active.set(layerKey, {
          fileName, leafletLayer, bounds,
          range: Array.isArray(meta.range) ? meta.range : null,
          loading: false, visible: true, opacity, isResult,
        });

        // Zoom to the first layer added; always zoom to a freshly-run result.
        if (!firstAddDone || isResult) {
          try { map.fitBounds(bounds); } catch (_) {}
          firstAddDone = true;
        }
      } catch (err) {
        console.error("Layer preview failed for", layerKey, err);
        if (!active.has(layerKey)) return;
        active.set(layerKey, {
          fileName, loading: false, error: true, visible: false, opacity, isResult,
        });
      }
      render();
    }

    function removeLayer(filePath) {
      const entry = active.get(filePath);
      if (!entry) return;
      if (entry.leafletLayer) {
        try { map.removeLayer(entry.leafletLayer); } catch (_) {}
      }
      active.delete(filePath);
      if (active.size === 0) firstAddDone = false;
      render();
    }

    function toggleVisibility(filePath) {
      const entry = active.get(filePath);
      if (!entry || !entry.leafletLayer) return;
      if (entry.visible) {
        try { map.removeLayer(entry.leafletLayer); } catch (_) {}
        entry.visible = false;
      } else {
        entry.leafletLayer.addTo(map);
        entry.visible = true;
      }
      render();
    }

    function setOpacity(filePath, opacity) {
      const entry = active.get(filePath);
      if (!entry || !entry.leafletLayer) return;
      entry.opacity = opacity;
      entry.leafletLayer.setOpacity(opacity);
    }

    // Sample each visible non-error layer at one WGS84 point. Returns a
    // Promise<{name, value, nodata, outOfBounds, error, isResult, range}[]>.
    async function queryAt(latlng) {
      if (!API.queryPoint) return [];
      const visible = [];
      active.forEach((entry, fp) => {
        if (entry.visible && !entry.error && !entry.loading) {
          visible.push({ fp, entry });
        }
      });
      const fetches = visible.map(async ({ fp, entry }) => {
        const src = entry.isResult ? '&source=result' : '';
        try {
          const url = `${API.queryPoint}?path=${encodeURIComponent(fp)}&lat=${latlng.lat}&lng=${latlng.lng}${src}`;
          const resp = await fetch(url);
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const data = await resp.json();
          return {
            name: entry.fileName,
            value: data.value,
            nodata: !!data.nodata,
            outOfBounds: !!data.out_of_bounds,
            isResult: !!entry.isResult,
            range: entry.range,
          };
        } catch (err) {
          return { name: entry.fileName, error: true };
        }
      });
      return Promise.all(fetches);
    }

    function render() {
      const el = controlEl();
      if (!el) return;
      if (active.size === 0) {
        el.innerHTML = "";
        el.style.display = "none";
        return;
      }
      el.style.display = "";

      const parts = [
        '<div class="map-layers-head"><i class="fas fa-layer-group"></i>Map layers</div>',
      ];
      active.forEach((entry, filePath) => {
        const fpAttr = encodeURIComponent(filePath);
        const safeName = escapeHtml(entry.fileName);
        let statusHtml;
        if (entry.loading) {
          statusHtml = '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i>';
        } else if (entry.error) {
          statusHtml = '<i class="fas fa-exclamation-circle text-danger" title="Failed to load"></i>';
        } else {
          const icon = entry.visible ? "fa-eye" : "fa-eye-slash";
          statusHtml = `<button type="button" class="ml-eye" data-fp="${fpAttr}" title="Show / hide on map"><i class="fas ${icon}"></i></button>`;
        }

        let legendHtml = "";
        if (!entry.loading && !entry.error && entry.range) {
          const legCls = entry.isResult ? "map-layer-legend is-result" : "map-layer-legend";
          const loLabel = entry.isResult ? "Low" : escapeHtml(fmtVal(entry.range[0]));
          const hiLabel = entry.isResult ? "High" : escapeHtml(fmtVal(entry.range[1]));
          legendHtml = `
            <div class="${legCls}"></div>
            <div class="map-layer-legend-labels">
              <span>${loLabel}</span>
              <span>${hiLabel}</span>
            </div>`;
        }

        parts.push(`
          <div class="map-layer-item">
            <div class="map-layer-row">
              ${statusHtml}
              <span class="map-layer-name" title="${safeName}">${safeName}</span>
              <button type="button" class="ml-info" data-fp="${fpAttr}" title="Layer metadata"><i class="fas fa-info-circle"></i></button>
            </div>
            <input type="range" min="0" max="1" step="0.05" value="${entry.opacity}"
                   class="map-layer-opacity" data-fp="${fpAttr}"
                   ${entry.leafletLayer ? "" : "disabled"}
                   title="Opacity">
            ${legendHtml}
          </div>
        `);
      });
      el.innerHTML = parts.join("");

      el.querySelectorAll(".ml-eye").forEach(b => {
        b.addEventListener("click", () => toggleVisibility(decodeURIComponent(b.dataset.fp)));
      });
      el.querySelectorAll(".map-layer-opacity").forEach(s => {
        s.addEventListener("input", () => setOpacity(decodeURIComponent(s.dataset.fp), parseFloat(s.value)));
      });
      el.querySelectorAll(".ml-info").forEach(b => {
        b.addEventListener("click", () => {
          const fp = decodeURIComponent(b.dataset.fp);
          const ent = active.get(fp);
          showLayerMetadata(fp, ent ? ent.fileName : fp,
                            ent && ent.isResult ? "result" : "data");
        });
      });
    }

    return { addLayer, removeLayer, queryAt };
  })();

  /* =========================
     Trapezoid criteria slider
     Visual editor for the suitability membership function. The four named
     inputs (min_val / opti_from / opti_to / max_val) remain the source of
     truth; the slider reads and writes them and always keeps
     min_val <= opti_from <= opti_to <= max_val.
  ========================= */
  function escHtml(s) {
    return String(s).replace(/[&<>"']/g,
      c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* =========================
     Inspect popup helpers (click on the map to sample raster values)
  ========================= */
  function fmtInspectNum(v) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1000) return Math.round(v).toLocaleString();
    if (a >= 1) return String(Math.round(v * 100) / 100);
    if (a === 0) return "0";
    return Number(v).toPrecision(2);
  }

  function fmtInspectValue(r) {
    if (r.error)        return { text: 'error',         cls: 'is-empty' };
    if (r.outOfBounds)  return { text: 'out of bounds', cls: 'is-empty' };
    if (r.nodata || r.value === null || r.value === undefined) {
      return { text: 'no data', cls: 'is-empty' };
    }
    if (r.isResult && r.range && Number.isInteger(Math.round(r.value))) {
      const cls = Math.round(r.value);
      const lo = Math.round(r.range[0]);
      const hi = Math.round(r.range[1]);
      if (lo === 1 && hi === 5) {
        // Suitability never inverts, but check anyway for safety.
        let idx = cls - 1;
        if (r.invert) idx = 4 - idx;
        const levels = ['Very low', 'Low', 'Moderate', 'High', 'Very high'];
        if (idx >= 0 && idx <= 4) {
          return { text: `${cls} — ${levels[idx]} suitability`, cls: '' };
        }
      }
      return { text: String(cls), cls: '' };
    }
    return { text: fmtInspectNum(r.value), cls: '' };
  }

  function renderInspectHtml(latlng, results) {
    const coords = `${latlng.lat.toFixed(4)}, ${latlng.lng.toFixed(4)}`;
    if (results.length === 0) {
      return `<div class="inspect-coords">${coords}</div>` +
             `<div class="inspect-empty">No layers visible.</div>`;
    }
    let rows = '';
    results.forEach(r => {
      const v = fmtInspectValue(r);
      rows += `<tr>
        <td class="inspect-name">${escHtml(r.name || '')}</td>
        <td class="inspect-value ${v.cls}">${escHtml(v.text)}</td>
      </tr>`;
    });
    return `<div class="inspect-coords">${coords}</div>
      <table class="inspect-table"><tbody>${rows}</tbody></table>`;
  }

  /* =========================
     Chain-workflow link (open the result in Land Statistics)
  ========================= */
  function renderChainLink(resultPath) {
    let container = document.getElementById('resultChainLinks');
    if (!container) {
      container = document.createElement('div');
      container.id = 'resultChainLinks';
      container.className = 'mt-2';
      const resultSection = document.getElementById('resultSection');
      if (resultSection) resultSection.appendChild(container);
    }
    const statsUrl = (CONFIG.pages && CONFIG.pages.statistics) || '/statistics';
    const href = `${statsUrl}?file=${encodeURIComponent(resultPath)}`;
    container.innerHTML =
      `<a href="${href}" target="_blank" rel="noopener" class="btn btn-outline-success btn-sm btn-block">
         <i class="fas fa-arrow-circle-right"></i> Open in Land Statistics
         <i class="fas fa-external-link-alt" style="font-size:.75em;opacity:.7;margin-left:4px"></i>
       </a>`;
  }

  /* =========================
     PDF report export
  ========================= */
  function renderReportButton(resultPath, description, rasterParameters, aoiStr) {
    let container = document.getElementById('resultReportLinks');
    if (!container) {
      container = document.createElement('div');
      container.id = 'resultReportLinks';
      container.className = 'mt-2';
      const resultSection = document.getElementById('resultSection');
      if (resultSection) resultSection.appendChild(container);
    }
    container.innerHTML =
      `<button type="button" id="exportReportBtn" class="btn btn-outline-success btn-sm btn-block">
         <i class="fas fa-file-pdf"></i> Export PDF report
       </button>`;
    document.getElementById('exportReportBtn').addEventListener('click',
      () => exportReport(resultPath, description, rasterParameters, aoiStr));
  }

  function exportReport(resultPath, description, rasterParameters, aoiStr) {
    if (!API.reportSuitability) return;
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
    // Flatten rasterParameters (keyed by file path) into a list ordered by
    // the current card order so the table matches the on-screen grouping.
    const criteria = [];
    document.querySelectorAll('#rasterCards .criteria-card').forEach((card) => {
      const fp = card.getAttribute('data-original-filepath');
      const params = rasterParameters && rasterParameters[fp];
      if (!params) return;
      // Derive a short display name from the file path's last segment.
      const name = (fp || '').split(/[\\/]/).pop();
      criteria.push({ name, ...params });
    });

    const btn = document.getElementById('exportReportBtn');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating PDF…';
    }
    fetch(API.reportSuitability, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify({
        result_path: resultPath,
        description: description || 'Suitability analysis',
        criteria,
        aoi: aoiStr || '',
      }),
    })
      .then(async (r) => {
        if (!r.ok) {
          const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
          throw new Error(j.error || 'Report generation failed');
        }
        return r.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const safeName = (description || 'suitability_report').replace(/[^\w-]+/g, '_').slice(0, 60);
        a.download = safeName + '.pdf';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      })
      .catch((err) => alert('Could not generate PDF: ' + err.message))
      .finally(() => {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<i class="fas fa-file-pdf"></i> Export PDF report';
        }
      });
  }

  /* =========================
     Layer metadata viewer
  ========================= */
  function metaNum(v) {
    if (v === null || v === undefined || v === "" || isNaN(v)) return "—";
    return Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 });
  }

  function renderMetaTable(m) {
    const rows = [];
    const add = (k, v) => {
      if (v !== null && v !== undefined && v !== "") rows.push([k, v]);
    };
    add("Driver", m.driver);
    add("Dimensions", (m.width && m.height) ? `${m.width} × ${m.height} px` : null);
    add("Bands", m.bands);
    add("Data type", m.dtype);
    add("CRS", m.epsg ? `EPSG:${m.epsg}` : m.crs);
    if (m.resolution) add("Resolution", `${metaNum(m.resolution[0])} × ${metaNum(m.resolution[1])}`);
    if (m.bounds_wgs84) {
      const w = m.bounds_wgs84;
      add("Extent (WGS84)", `${metaNum(w[0])}, ${metaNum(w[1])} → ${metaNum(w[2])}, ${metaNum(w[3])}`);
    }
    add("NoData", m.nodata);
    add("Units", m.units);
    add("Band description", m.band_description);
    add("Compression", m.compression);
    if (m.overview_levels && m.overview_levels.length) add("Overviews", m.overview_levels.join(", "));
    if (m.statistics) {
      add("Min / Max", `${metaNum(m.statistics.minimum)} / ${metaNum(m.statistics.maximum)}`);
      add("Mean / Std", `${metaNum(m.statistics.mean)} / ${metaNum(m.statistics.stddev)}`);
    }

    let html = '<table class="table table-sm meta-table"><tbody>';
    rows.forEach(([k, v]) => {
      html += `<tr><th>${escHtml(k)}</th><td>${escHtml(String(v))}</td></tr>`;
    });
    html += "</tbody></table>";

    const desc = Object.assign({}, m.descriptive || {}, m.curated || {});
    const descKeys = Object.keys(desc);
    if (descKeys.length) {
      html += '<h6 class="mt-3 mb-1">Description &amp; source</h6><table class="table table-sm meta-table"><tbody>';
      descKeys.forEach((k) => {
        html += `<tr><th>${escHtml(k)}</th><td>${escHtml(String(desc[k]))}</td></tr>`;
      });
      html += "</tbody></table>";
    } else {
      html += '<p class="text-muted small mt-2 mb-0">No descriptive metadata sidecar found for this layer (showing auto-derived technical metadata only).</p>';
    }
    return html;
  }

  async function showLayerMetadata(path, name, source) {
    const titleEl = document.getElementById("layerMetaTitle");
    const bodyEl = document.getElementById("layerMetaBody");
    const dlEl = document.getElementById("layerMetaDownload");
    if (!bodyEl || !API.layerMetadata) return;
    if (titleEl) titleEl.textContent = name || "Layer metadata";
    const src = source === "result" ? "&source=result" : "";
    if (dlEl) {
      dlEl.href = `${API.layerMetadata}?path=${encodeURIComponent(path)}${src}&download=1`;
    }
    bodyEl.innerHTML = '<div class="text-muted">Loading metadata…</div>';
    try { $("#layerMetaModal").modal("show"); } catch (_) {}
    try {
      const resp = await fetch(`${API.layerMetadata}?path=${encodeURIComponent(path)}${src}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const m = await resp.json();
      if (m.error) throw new Error(m.error);
      bodyEl.innerHTML = renderMetaTable(m);
    } catch (err) {
      bodyEl.innerHTML = `<div class="text-danger">Could not load metadata: ${escHtml(err.message || String(err))}</div>`;
    }
  }

  const TrapSlider = (function () {
    const ORDER = ["min_val", "opti_from", "opti_to", "max_val"];

    function init(cardEl, axisMin, axisMax) {
      if (!(axisMax > axisMin)) axisMax = axisMin + 1;
      const range = axisMax - axisMin;
      const dec = range >= 100 ? 0 : range >= 10 ? 1 : range >= 1 ? 2 : 4;
      const round = (v) => { const f = Math.pow(10, dec); return Math.round(v * f) / f; };

      const slider = cardEl.querySelector(".trap-slider");
      const poly = cardEl.querySelector(".trap-shape");
      const handles = {};
      const inputs = {};
      ORDER.forEach((role) => {
        handles[role] = cardEl.querySelector(`.trap-handle[data-role="${role}"]`);
        inputs[role] = cardEl.querySelector(`input[name*="[${role}]"]`);
      });

      const getVal = (role) => parseFloat(inputs[role].value);
      const toPct = (v) => Math.max(0, Math.min(100, ((v - axisMin) / range) * 100));

      function clampRole(role, v) {
        v = Math.max(axisMin, Math.min(axisMax, v));
        const i = ORDER.indexOf(role);
        if (i > 0) { const lo = getVal(ORDER[i - 1]); if (isFinite(lo)) v = Math.max(v, lo); }
        if (i < 3) { const hi = getVal(ORDER[i + 1]); if (isFinite(hi)) v = Math.min(v, hi); }
        return v;
      }

      function redraw() {
        ORDER.forEach((role) => {
          const v = getVal(role);
          if (isFinite(v)) handles[role].style.left = toPct(v) + "%";
        });
        const mn = toPct(getVal("min_val"));
        const of = toPct(getVal("opti_from"));
        const ot = toPct(getVal("opti_to"));
        const mx = toPct(getVal("max_val"));
        poly.setAttribute("points", `${mn},100 ${of},0 ${ot},0 ${mx},100`);
      }

      function setRole(role, v) {
        v = clampRole(role, round(v));
        inputs[role].value = v;
        redraw();
      }

      ORDER.forEach((role) => {
        inputs[role].addEventListener("change", () => {
          const v = parseFloat(inputs[role].value);
          if (!isFinite(v)) { redraw(); return; }
          setRole(role, v);
        });

        const h = handles[role];
        h.addEventListener("pointerdown", (e) => {
          e.preventDefault();
          try { h.setPointerCapture(e.pointerId); } catch (_) {}
          h.classList.add("dragging");
        });
        h.addEventListener("pointermove", (e) => {
          if (!h.hasPointerCapture || !h.hasPointerCapture(e.pointerId)) return;
          const r = slider.getBoundingClientRect();
          if (!r.width) return;
          setRole(role, axisMin + ((e.clientX - r.left) / r.width) * range);
        });
        const release = (e) => {
          try { h.releasePointerCapture(e.pointerId); } catch (_) {}
          h.classList.remove("dragging");
        };
        h.addEventListener("pointerup", release);
        h.addEventListener("pointercancel", release);
        h.addEventListener("keydown", (e) => {
          const step = range / 100 || 1;
          if (e.key === "ArrowLeft" || e.key === "ArrowDown") { setRole(role, getVal(role) - step); e.preventDefault(); }
          else if (e.key === "ArrowRight" || e.key === "ArrowUp") { setRole(role, getVal(role) + step); e.preventDefault(); }
        });
      });

      redraw();
    }

    return { init };
  })();

  /* =========================
     Criteria cards (suitability trapezoid per layer)
  ========================= */
  let rasterCounter = 0;
  let groupRows = {};

  async function addSelectedFile(filePath, fileName, minVal, maxVal) {
    rasterCounter++;
    const rasterId = rasterCounter;
    const sanitized = DirectoryBrowser.sanitizeFilePath(filePath);
    const cards = document.getElementById("rasterCards");

    // Prefer the raster's own statistics (auto, from the file) for the
    // trapezoid axis; fall back to the values supplied by the directory
    // listing if the raster has no usable stats.
    let aMin = parseFloat(minVal);
    let aMax = parseFloat(maxVal);
    let fetchedMeta = null;
    if (API.rasterMeta) {
      try {
        const resp = await fetch(`${API.rasterMeta}?path=${encodeURIComponent(filePath)}`);
        if (resp.ok) {
          fetchedMeta = await resp.json();
          const dr = fetchedMeta && fetchedMeta.data_range;
          if (Array.isArray(dr) && isFinite(dr[0]) && isFinite(dr[1]) && dr[1] > dr[0]) {
            aMin = dr[0];
            aMax = dr[1];
          }
        }
      } catch (_) { /* keep the listing values */ }
    }
    if (!isFinite(aMin)) aMin = 0;
    if (!isFinite(aMax) || aMax <= aMin) aMax = aMin + 100;
    const span = aMax - aMin;
    const dec = span >= 100 ? 0 : span >= 10 ? 1 : span >= 1 ? 2 : 4;
    const round = (v) => { const f = Math.pow(10, dec); return Math.round(v * f) / f; };
    const optFrom = round(aMin + span / 3);
    const optTo = round(aMin + (2 * span) / 3);
    const safeName = escHtml(fileName);

    const card = document.createElement("div");
    card.className = "criteria-card";
    card.setAttribute("data-raster-id", rasterId);
    card.setAttribute("data-original-filepath", filePath);
    card.innerHTML = `
      <div class="criteria-card-head">
        <span class="criteria-group-badge"></span>
        <span class="criteria-card-name" title="${safeName}">${safeName}</span>
        <span class="criteria-card-actions">
          <button type="button" class="btn btn-link btn-sm move-up-btn" title="Move up"><i class="fas fa-arrow-up"></i></button>
          <button type="button" class="btn btn-link btn-sm move-down-btn" title="Move down"><i class="fas fa-arrow-down"></i></button>
          <button type="button" class="btn btn-link btn-sm remove-btn text-danger" title="Remove"><i class="fas fa-trash"></i></button>
        </span>
      </div>
      <div class="trap-slider" title="Drag the handles to set the suitability trapezoid">
        <svg class="trap-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
          <polygon class="trap-shape" points=""></polygon>
        </svg>
        <div class="trap-baseline"></div>
        <div class="trap-handle" data-role="min_val" tabindex="0" title="Minimum — suitability 0 below this"></div>
        <div class="trap-handle is-opt" data-role="opti_from" tabindex="0" title="Optimal from — full suitability"></div>
        <div class="trap-handle is-opt" data-role="opti_to" tabindex="0" title="Optimal to — full suitability"></div>
        <div class="trap-handle" data-role="max_val" tabindex="0" title="Maximum — suitability 0 above this"></div>
      </div>
      <div class="trap-fields">
        <label>Min<input type="text" inputmode="decimal" class="form-control form-control-sm" name="rasterParameters[${sanitized}][min_val]" value="${aMin}"></label>
        <label>Opt. from<input type="text" inputmode="decimal" class="form-control form-control-sm" name="rasterParameters[${sanitized}][opti_from]" value="${optFrom}"></label>
        <label>Opt. to<input type="text" inputmode="decimal" class="form-control form-control-sm" name="rasterParameters[${sanitized}][opti_to]" value="${optTo}"></label>
        <label>Max<input type="text" inputmode="decimal" class="form-control form-control-sm" name="rasterParameters[${sanitized}][max_val]" value="${aMax}"></label>
      </div>
      <div class="trap-combine">
        <label><input type="checkbox" class="combine-check"> Combine with previous group</label>
        <select id="combine_${sanitized}" name="rasterParameters[${sanitized}][combine]" class="combine-select" hidden>
          <option value="Yes">Yes</option>
          <option value="No" selected>No</option>
        </select>
      </div>
    `;
    cards.appendChild(card);

    card.querySelector(".remove-btn").addEventListener("click", () => removeSelectedFile(rasterId));
    card.querySelector(".move-up-btn").addEventListener("click", () => moveCard(card, "up"));
    card.querySelector(".move-down-btn").addEventListener("click", () => moveCard(card, "down"));

    const combineCheck = card.querySelector(".combine-check");
    const combineSelect = card.querySelector(".combine-select");
    combineCheck.addEventListener("change", () => {
      combineSelect.value = combineCheck.checked ? "Yes" : "No";
      updateCombineOptions();
    });

    TrapSlider.init(card, aMin, aMax);
    updateCombineOptions();

    // Preview this layer on the main map (async — non-blocking).
    // Preview this layer on the main map (async — non-blocking). Reuse the
    // metadata we already fetched so we don't request it twice.
    MapLayers.addLayer(filePath, fileName, fetchedMeta ? { meta: fetchedMeta } : undefined);
  }

  function removeSelectedFile(rasterId) {
    const card = document.querySelector(`.criteria-card[data-raster-id="${rasterId}"]`);
    if (!card) return;

    const originalFilePath = card.getAttribute("data-original-filepath");
    card.remove();

    const checkbox = document.querySelector(`input[type="checkbox"][value="${originalFilePath}"]`);
    if (checkbox) checkbox.checked = false;

    // Remove the corresponding preview from the map.
    MapLayers.removeLayer(originalFilePath);

    updateCombineOptions();
  }

  function moveCard(card, direction) {
    const container = card.parentNode;
    if (direction === "up") {
      const prev = card.previousElementSibling;
      if (prev) container.insertBefore(card, prev);
    } else if (direction === "down") {
      const next = card.nextElementSibling;
      if (next) container.insertBefore(next, card);
    }
    updateCombineOptions();
  }

  function updateCombineOptions() {
    const container = document.getElementById("rasterCards");
    if (!container) return;
    const cards = Array.from(container.querySelectorAll(".criteria-card"));
    let currentGroup = 1;
    groupRows = {};

    cards.forEach((card, index) => {
      const combineSelect = card.querySelector('select[name*="[combine]"]');
      const combineCheck = card.querySelector(".combine-check");

      if (index === 0) {
        combineSelect.value = "No";
        if (combineCheck) { combineCheck.checked = false; combineCheck.disabled = true; }
        card.setAttribute("data-group", currentGroup);
      } else {
        if (combineCheck) combineCheck.disabled = false;
        const prevGroup = parseInt(cards[index - 1].getAttribute("data-group"), 10);
        if (combineSelect.value === "Yes") card.setAttribute("data-group", prevGroup);
        else {
          currentGroup++;
          card.setAttribute("data-group", currentGroup);
        }
      }

      const groupNumber = parseInt(card.getAttribute("data-group"), 10);
      groupRows[groupNumber] = groupRows[groupNumber] || [];
      groupRows[groupNumber].push(card);

      const badge = card.querySelector(".criteria-group-badge");
      if (badge) badge.textContent = "Group " + groupNumber;
    });
  }

  function removeSelectedFileByFilePath(filePath) {
    const card = document.querySelector(`.criteria-card[data-original-filepath="${filePath}"]`);
    if (card) {
      card.remove();
      updateCombineOptions();
    }
    // Remove the corresponding preview from the map (covers the
    // user un-checking a file in the directory tree).
    MapLayers.removeLayer(filePath);
  }

  /* =========================
     Submit / validate
  ========================= */
  function validateForm(event) {
    event.preventDefault();

    const descriptionInput = document.getElementById("description");
    const descriptionValue = descriptionInput.value.trim();

    if (!descriptionValue) {
      alert("Description is required.");
      descriptionInput.focus();
      return false;
    }

    const selectedRows = document.querySelectorAll("#rasterCards .criteria-card");
    if (selectedRows.length < 1) {
      alert("Please select at least one file.");
      return false;
    }

   if (!aoiInput.value) {
  console.info("No AOI provided — processing full raster extent.");
   }

    // Build rasterParameters payload (same as your approach)
    const rasterParameters = {};
    let isValid = true;

    selectedRows.forEach((row) => {
      const originalFilePath = row.getAttribute("data-original-filepath");
      const key = DirectoryBrowser.sanitizeFilePath(originalFilePath);

      const minValInput = row.querySelector(`input[name="rasterParameters[${key}][min_val]"]`);
      const maxValInput = row.querySelector(`input[name="rasterParameters[${key}][max_val]"]`);
      const optiFromInput = row.querySelector(`input[name="rasterParameters[${key}][opti_from]"]`);
      const optiToInput = row.querySelector(`input[name="rasterParameters[${key}][opti_to]"]`);
      const combineInput = row.querySelector(`select[name="rasterParameters[${key}][combine]"]`);

      const minVal = minValInput?.value;
      const maxVal = maxValInput?.value;
      const optiFrom = optiFromInput?.value;
      const optiTo = optiToInput?.value;

      if (!minVal || !maxVal || !optiFrom || !optiTo) {
        alert(`One or more inputs are missing for: ${originalFilePath}`);
        isValid = false;
        return;
      }

        const minV = parseFloat(minVal);
        const maxV = parseFloat(maxVal);
        const of = parseFloat(optiFrom);
        const ot = parseFloat(optiTo);

        if (of < minV || of > ot || ot > maxV) {
        alert(
            `Invalid range for: ${originalFilePath}\n` +
            `Required: minVal <= optiFrom <= optiTo <= maxVal\n` +
            `Got: ${minV} <= ${of} <= ${ot} <= ${maxV}`
        );
        isValid = false;
        return;
        }

      rasterParameters[originalFilePath] = {
        opti_from: optiFrom,
        opti_to: optiTo,
        min_val: minVal,
        max_val: maxVal,
        combine: combineInput.value,
      };
    });

    if (!isValid) return false;

    const formData = {
      selectedFiles: Array.from(selectedRows).map((r) => r.getAttribute("data-original-filepath")),
      rasterParameters,
      aoi: aoiInput.value, // FeatureCollection JSON string
      description: descriptionValue,
    };

    // Submit

    $('#progressModal').modal('show');
    fetch(API.processLandSuitability, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": document.querySelector('[name=csrfmiddlewaretoken]').value,
      },
      body: JSON.stringify(formData),
    })
      .then((r) => r.json())
      .then((data) => {
        
        if (data.status === "success") {
           resultUrl = data.result_url;
              $('#downloadLink').attr('href', resultUrl);
              $('#downloadLink2').attr('href', resultUrl);
              $('#resultSection').show();

              // Show the result on the main map (tiled, like the inputs) so it
              // can be toggled against them. Replace any previous result.
              if (data.result_path) {
                if (currentResultKey) MapLayers.removeLayer(currentResultKey);
                currentResultKey = data.result_path;
                MapLayers.addLayer(data.result_path, "Suitability result",
                                   { source: "result", opacity: 0.85 });
                renderChainLink(data.result_path);
                renderReportButton(data.result_path,
                                   formData.description,
                                   formData.rasterParameters,
                                   formData.aoi);
              }

              $('html, body').animate({
                scrollTop: $('#resultSection').offset().top
              }, 500);
        } else {
          alert(`Error: ${data.message || "Unknown error"}`);
        }
      })
      .catch((err) => {
        console.error(err);
        alert("An error occurred while processing.");
      }).finally(() => {
    //ALWAYS hide modal (success or failure)
    $('#progressModal').modal('hide');
  });
    return false;
  }

  const submitBtn = document.getElementById("submitBtn");
  if (submitBtn) submitBtn.addEventListener("click", validateForm);

  // Load the raster directory tree via the shared module.
  if (fileListElement) {
    DirectoryBrowser.render({
      container: fileListElement,
      rootPath: "",
      urls: {
        directoryContents: API.directoryContents,
        folderConfigurations: API.folderConfigurations,
      },
      onFolderOpen: (folderName, folderConfig) => {
        if (map && folderConfig) map.setView(folderConfig.center, folderConfig.zoom);
      },
      onFileSelect: (filePath, item) =>
        addSelectedFile(filePath, item.name, item.min_val, item.max_val),
      onFileDeselect: (filePath) => removeSelectedFileByFilePath(filePath),
      onFileInfo: (filePath, item) => showLayerMetadata(filePath, item.name, "data"),
    });
  }

  // Initialize map immediately (since map is always present in HTML)
  showMapAndRefresh();

  // Default file upload section hidden
  if (fileUploadSection) fileUploadSection.style.display = "none";

   // -------------------------
  // View Result (faster + loading indicator)
  // -------------------------
  let popupRasterLayer = null;
  let popupLegend = null;
  let pendingResultUrl = null;
  let lastGeorasterUrl = null;
  let lastGeoraster = null;
  let loadAbortController = null;

  function ensurePopupLoadingOverlay() {
    const mapEl = document.getElementById('popup-map');
    if (!mapEl) return;
    if (document.getElementById('rasterLoadingOverlay')) return;

    // Make sure overlay can be positioned over the map container
    mapEl.style.position = mapEl.style.position || 'relative';

    const overlay = document.createElement('div');
    overlay.id = 'rasterLoadingOverlay';
    overlay.style.cssText = [
      'position:absolute',
      'inset:0',
      'display:none',
      'align-items:center',
      'justify-content:center',
      'background:rgba(255,255,255,0.85)',
      'z-index:9999',
      'pointer-events:none' // keep it simple; map won't be used while loading anyway
    ].join(';');

    overlay.innerHTML = `
      <div class="text-center">
        <div class="spinner-border" role="status" aria-label="Loading"></div>
        <div id="rasterLoadingText" class="mt-2">Loading raster…</div>
      </div>
    `;

    mapEl.appendChild(overlay);
  }

  function setPopupLoading(isLoading, message) {
    ensurePopupLoadingOverlay();
    const overlay = document.getElementById('rasterLoadingOverlay');
    const textEl = document.getElementById('rasterLoadingText');
    if (!overlay) return;

    if (textEl && message) textEl.textContent = message;
    overlay.style.display = isLoading ? 'flex' : 'none';
  }

  function ensurePopupMap() {
    if (popupMap) return popupMap;

    popupMap = L.map('popup-map', { preferCanvas: true }).setView([0, 0], 5);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(popupMap);

    // Create legend once
    popupLegend = L.control({ position: 'bottomright' });
    popupLegend.onAdd = function () {
      const div = L.DomUtil.create('div', 'legend');
      div.innerHTML += '<i style="background: #A87000"></i> Very Low<br>';
      div.innerHTML += '<i style="background: #FFD37F"></i> Low<br>';
      div.innerHTML += '<i style="background: #E9FFBE"></i> Medium<br>';
      div.innerHTML += '<i style="background: #98E600"></i> High<br>';
      div.innerHTML += '<i style="background: #267300"></i> Very High<br>';
      return div;
    };
    popupLegend.addTo(popupMap);

    return popupMap;
  }

  async function loadAndRenderResult(url) {
    if (!url) {
      alert('No result raster available yet. Run the suitability process first.');
      return;
    }

    ensurePopupMap();

    // Cancel any in-flight request from previous opens
    if (loadAbortController) {
      try { loadAbortController.abort(); } catch (_) {}
    }
    loadAbortController = new AbortController();

    // Remove previous raster layer
    if (popupRasterLayer) {
      try { popupMap.removeLayer(popupRasterLayer); } catch (_) {}
      popupRasterLayer = null;
    }

    setPopupLoading(true, 'Loading suitability raster…');

    try {
      let georaster;

      // Reuse previously parsed raster if user re-opens the same result
      if (lastGeoraster && lastGeorasterUrl === url) {
        georaster = lastGeoraster;
      } else {
        const response = await fetch(url, { signal: loadAbortController.signal });
        if (!response.ok) throw new Error(`Failed to fetch raster (${response.status})`);
        const arrayBuffer = await response.arrayBuffer();
        georaster = await parseGeoraster(arrayBuffer);

        lastGeorasterUrl = url;
        lastGeoraster = georaster;
      }

      // Fast lookup for class colors (1..5); nodata/other => transparent
      const LUT = {
        1: '#A87000',
        2: '#FFD37F',
        3: '#E9FFBE',
        4: '#98E600',
        5: '#267300'
      };
      const pixelValuesToColorFn = (values) => LUT[values[0]] || '#00000000';

      // Lower resolution = faster initial draw. Increase later if needed.
      popupRasterLayer = new GeoRasterLayer({
        georaster,
        opacity: 0.7,
        pixelValuesToColorFn,
        resolution: 128
      });

      popupRasterLayer.addTo(popupMap);
      popupMap.fitBounds(popupRasterLayer.getBounds());

    } catch (error) {
      // Abort is expected if user closes/reopens quickly
      if (error && error.name === 'AbortError') return;
      console.error(error);
      alert('Error loading raster file.');
    } finally {
      setPopupLoading(false);
    }
  }

  // Button: open modal (store URL to be rendered on shown)
  $('#viewResultBtn')
    .off('click.viewResult')
    .on('click.viewResult', function () {
      pendingResultUrl = resultUrl;
      $('#resultModal').modal('show');
    });

  // Modal shown: ensure map created, then render raster
  $('#resultModal')
    .off('shown.bs.modal.viewResult')
    .on('shown.bs.modal.viewResult', function () {
      ensurePopupMap();
      // Leaflet needs this after Bootstrap shows the modal
      setTimeout(() => popupMap && popupMap.invalidateSize(true), 150);
      loadAndRenderResult(pendingResultUrl);
    });

  // Modal hidden: abort any in-flight load (prevents wasted bandwidth/CPU)
  $('#resultModal')
    .off('hidden.bs.modal.viewResult')
    .on('hidden.bs.modal.viewResult', function () {
      if (loadAbortController) {
        try { loadAbortController.abort(); } catch (_) {}
      }
    });
});
