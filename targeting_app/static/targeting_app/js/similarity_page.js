// Land Similarity page logic.
// Modernized to match the suitability tool: server-side tiled previews,
// basemap switcher + scale, layer metadata viewer, both results (Mahalanobis
// + MESS) rendered as tiled overlays on the main map.

document.addEventListener('DOMContentLoaded', () => {

  // ----- Config / element refs -----
  const CONFIG = (function () {
    const el = document.getElementById("js-config");
    try { return el ? JSON.parse(el.textContent) : {}; }
    catch (e) { console.error("Invalid #js-config JSON:", e); return {}; }
  })();
  const API = CONFIG.apiEndpoints || {};

  const aoiOptionMap        = document.getElementById('aoiOptionMap');
  const aoiOptionFile       = document.getElementById('aoiOptionFile');
  const mapSection          = document.getElementById('mapSection');
  const fileUploadSection   = document.getElementById('fileUploadSection');
  const aoiFileUpload       = document.getElementById('aoiFileUpload');
  const pointsInput         = document.getElementById('pointsInput');
  const uploadedPointsInput = document.getElementById('uploadedPointsInput');
  const fileError           = document.getElementById('fileError');
  const fileSuccess         = document.getElementById('fileSuccess');
  const fileListElement     = document.getElementById('fileList');
  const selectedFilesForm   = document.getElementById('selectedFilesForm');
  const selectedFilesContainer = document.getElementById('selectedFilesContainer');

  // Real spatial AOI (distinct from the "aoiOption*"/"aoiFileUpload"
  // elements above, which — despite the name — are about sample POINTS,
  // not an area of interest).
  const simAoiInput  = document.getElementById('simAoiInput');
  const simAoiStatus = document.getElementById('simAoiStatus');
  const clearSimAoiBtn = document.getElementById('clearSimAoiBtn');
  const simAoiFileUpload = document.getElementById('simAoiFileUpload');
  const simAoiFileError  = document.getElementById('simAoiFileError');

  let map = null;
  let drawnItems = new L.FeatureGroup();
  let uploadedPointsPreview = new L.FeatureGroup();
  let aoiDrawnItems = new L.FeatureGroup();
  let firstAddDone = false;
  let currentResultKeys = [];

  // ----- Tiny helpers -----
  function escHtml(s) {
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

  // ----- Inspect popup helpers (click on the map to sample raster values) -----
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
        let idx = cls - 1;
        if (r.invert) idx = 4 - idx;
        const levels = ['Very low', 'Low', 'Moderate', 'High', 'Very high'];
        if (idx >= 0 && idx <= 4) {
          return { text: `${cls} — ${levels[idx]} similarity`, cls: '' };
        }
      }
      return { text: String(cls), cls: '' };
    }
    return { text: fmtVal(r.value), cls: '' };
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

  // ----- Layer metadata modal -----
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
    rows.forEach(([k, v]) => { html += `<tr><th>${escHtml(k)}</th><td>${escHtml(String(v))}</td></tr>`; });
    html += "</tbody></table>";

    const desc = Object.assign({}, m.descriptive || {}, m.curated || {});
    const dkeys = Object.keys(desc);
    if (dkeys.length) {
      html += '<h6 class="mt-3 mb-1">Description &amp; source</h6><table class="table table-sm meta-table"><tbody>';
      dkeys.forEach((k) => { html += `<tr><th>${escHtml(k)}</th><td>${escHtml(String(desc[k]))}</td></tr>`; });
      html += "</tbody></table>";
    } else {
      html += '<p class="text-muted small mt-2 mb-0">No descriptive metadata sidecar found for this layer.</p>';
    }
    return html;
  }
  // Fixed set of curated fields an authorized user can set — mirrors the
  // allow-list the backend enforces in update_layer_metadata().
  const EDITABLE_METADATA_FIELDS = [
    { key: 'title', label: 'Title' },
    { key: 'description', label: 'Description' },
    { key: 'source', label: 'Source' },
    { key: 'units', label: 'Units' },
    { key: 'category', label: 'Category' },
    { key: 'date_created', label: 'Date created' },
    { key: 'contact', label: 'Contact' },
    { key: 'license', label: 'License' },
    { key: 'notes', label: 'Notes' },
  ];
  let currentMetaLayer = null; // { path, name, source, meta }

  // Existing values to start the edit form from — curated (already
  // user-set) takes priority, falling back to whatever was already known
  // from auto-derived/parsed sources, so editing starts from "what's
  // already there" instead of a blank form even before anyone has saved
  // curated metadata for this dataset yet.
  function prefillMetadataValues(meta) {
    const curated = (meta && meta.curated) || {};
    const descriptive = (meta && meta.descriptive) || {};
    const pick = (...candidates) => {
      for (const c of candidates) {
        if (c !== undefined && c !== null && String(c).trim() !== '') return String(c);
      }
      return '';
    };
    return {
      title: pick(curated.title, descriptive.Title),
      description: pick(curated.description, descriptive.Abstract, meta && meta.band_description),
      source: pick(curated.source, descriptive.Credit),
      units: pick(curated.units, meta && meta.units, descriptive['Units (declared)']),
      category: pick(curated.category),
      date_created: pick(curated.date_created),
      contact: pick(curated.contact),
      license: pick(curated.license),
      notes: pick(curated.notes, descriptive.Keywords),
    };
  }

  function renderMetaEditForm(meta) {
    const values = prefillMetadataValues(meta);
    const rows = EDITABLE_METADATA_FIELDS.map(({ key, label }) => {
      const val = escHtml(values[key] || '');
      const isLong = key === 'description' || key === 'notes';
      return `
        <div class="form-group row mb-2">
          <label class="col-4 col-form-label col-form-label-sm">${escHtml(label)}</label>
          <div class="col-8">
            ${isLong
              ? `<textarea class="form-control form-control-sm" data-meta-field="${key}" rows="2">${val}</textarea>`
              : `<input type="text" class="form-control form-control-sm" data-meta-field="${key}" value="${val}">`}
          </div>
        </div>`;
    }).join('');
    return `
      <form id="metaEditForm">
        ${rows}
        <div id="metaEditError" class="text-danger small mb-2"></div>
        <div class="d-flex justify-content-end">
          <button type="button" id="metaEditCancelBtn" class="btn btn-sm btn-outline-secondary mr-2">Cancel</button>
          <button type="submit" class="btn btn-sm btn-success">Save metadata</button>
        </div>
      </form>`;
  }

  function wireMetaEditForm(bodyEl, path) {
    const form = bodyEl.querySelector('#metaEditForm');
    const errorEl = bodyEl.querySelector('#metaEditError');
    bodyEl.querySelector('#metaEditCancelBtn').addEventListener('click', () => {
      bodyEl.innerHTML = renderMetaTable(currentMetaLayer.meta);
    });
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      errorEl.textContent = '';
      const metadata = {};
      form.querySelectorAll('[data-meta-field]').forEach((el) => {
        metadata[el.dataset.metaField] = el.value;
      });
      const submitBtn = form.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      submitBtn.textContent = 'Saving…';
      try {
        const resp = await fetch(API.updateLayerMetadata, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]')?.value,
          },
          body: JSON.stringify({ path, metadata }),
        });
        const data = await resp.json();
        if (!resp.ok || data.status !== 'success') {
          throw new Error(data.message || `HTTP ${resp.status}`);
        }
        currentMetaLayer.meta.curated = data.curated;
        bodyEl.innerHTML = renderMetaTable(currentMetaLayer.meta);
      } catch (err) {
        errorEl.textContent = err.message || String(err);
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save metadata';
      }
    });
  }

  async function showLayerMetadata(path, name, source) {
    const titleEl = document.getElementById("layerMetaTitle");
    const bodyEl  = document.getElementById("layerMetaBody");
    const dlEl    = document.getElementById("layerMetaDownload");
    const editBtn = document.getElementById("layerMetaEditBtn");
    if (!bodyEl || !API.layerMetadata) return;
    if (titleEl) titleEl.textContent = name || "Layer metadata";
    const src = source === "result" ? "&source=result" : "";
    if (dlEl) dlEl.href = `${API.layerMetadata}?path=${encodeURIComponent(path)}${src}&download=1`;
    // Curated metadata only applies to source datasets, not analysis
    // results — a result has no dataset sidecar to edit.
    if (editBtn) editBtn.style.display = (CONFIG.canEditMetadata && source !== "result") ? "inline-block" : "none";
    bodyEl.innerHTML = '<div class="text-muted">Loading metadata…</div>';
    try { $("#layerMetaModal").modal("show"); } catch (_) {}
    try {
      const resp = await fetch(`${API.layerMetadata}?path=${encodeURIComponent(path)}${src}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const m = await resp.json();
      if (m.error) throw new Error(m.error);
      currentMetaLayer = { path, name, source, meta: m };
      bodyEl.innerHTML = renderMetaTable(m);
      if (editBtn) {
        editBtn.onclick = () => {
          bodyEl.innerHTML = renderMetaEditForm(currentMetaLayer.meta);
          wireMetaEditForm(bodyEl, path);
        };
      }
    } catch (err) {
      bodyEl.innerHTML = `<div class="text-danger">Could not load metadata: ${escHtml(err.message || String(err))}</div>`;
    }
  }

  // ----- Map layers control (tiled previews + results) -----
  const MapLayers = (function () {
    const active = new Map();
    let controlEl = null;
    function ensure() {
      if (controlEl) return controlEl;
      controlEl = document.getElementById("mapLayersControl");
      return controlEl;
    }

    async function addLayer(layerKey, fileName, opts) {
      if (!map) return;
      if (active.has(layerKey)) return;
      if (!API.tileRaster || !API.rasterMeta) return;

      opts = opts || {};
      const isResult   = opts.source === "result";
      const srcParam   = isResult ? "&source=result" : "";
      const cmapParam  = opts.cmap ? `&cmap=${encodeURIComponent(opts.cmap)}` : "";
      const invertParam= opts.invert ? "&invert=1" : "";
      const opacity    = typeof opts.opacity === "number" ? opts.opacity : 0.7;

      active.set(layerKey, {
        fileName, loading: true, visible: true, opacity, isResult,
        invert: !!opts.invert,
      });
      render();

      try {
        const metaResp = await fetch(`${API.rasterMeta}?path=${encodeURIComponent(layerKey)}${srcParam}`);
        if (!metaResp.ok) throw new Error(`metadata HTTP ${metaResp.status}`);
        const meta = await metaResp.json();
        if (!meta || !meta.bounds) throw new Error("no bounds in metadata response");
        if (!active.has(layerKey)) return; // user removed it while we waited

        const tileUrl = `${API.tileRaster}?path=${encodeURIComponent(layerKey)}${srcParam}${cmapParam}${invertParam}`;
        const bounds = L.latLngBounds(
          [meta.bounds[1], meta.bounds[0]],
          [meta.bounds[3], meta.bounds[2]]
        );
        const layerOpts = {
          opacity, bounds, tileSize: 256, noWrap: true, pane: "rasterOverlays",
        };
        if (typeof meta.maxzoom === "number") layerOpts.maxNativeZoom = meta.maxzoom;
        const leafletLayer = L.tileLayer(tileUrl, layerOpts);
        leafletLayer.addTo(map);

        active.set(layerKey, {
          fileName, leafletLayer, bounds,
          range: Array.isArray(meta.range) ? meta.range : null,
          loading: false, visible: true, opacity,
          isResult, invert: !!opts.invert,
        });

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

    function removeLayer(layerKey) {
      const entry = active.get(layerKey);
      if (!entry) return;
      if (entry.leafletLayer && map) {
        try { map.removeLayer(entry.leafletLayer); } catch (_) {}
      }
      active.delete(layerKey);
      render();
    }

    function toggleVisibility(layerKey) {
      const entry = active.get(layerKey);
      if (!entry || !entry.leafletLayer) return;
      entry.visible = !entry.visible;
      if (entry.visible) entry.leafletLayer.addTo(map);
      else map.removeLayer(entry.leafletLayer);
      render();
    }

    function setOpacity(layerKey, value) {
      const entry = active.get(layerKey);
      if (!entry || !entry.leafletLayer) return;
      entry.opacity = value;
      entry.leafletLayer.setOpacity(value);
    }

    // Sample each visible non-error layer at one WGS84 point.
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
            invert: !!entry.invert,
            range: entry.range,
          };
        } catch (err) {
          return { name: entry.fileName, error: true };
        }
      });
      return Promise.all(fetches);
    }

    function render() {
      const el = ensure();
      if (!el) return;
      if (active.size === 0) { el.style.display = "none"; el.innerHTML = ""; return; }
      el.style.display = "block";

      const parts = [
        '<div class="map-layers-head"><i class="fas fa-layer-group"></i>Map layers</div>',
      ];
      // Newest-first ordering, matching Leaflet's draw order on the map.
      Array.from(active.entries()).reverse().forEach(([fp, entry]) => {
        const safeName = escHtml(entry.fileName || fp);
        const fpAttr = encodeURIComponent(fp);
        let statusHtml;
        if (entry.loading) {
          statusHtml = '<span class="ml-spinner"><i class="fas fa-spinner fa-spin"></i></span>';
        } else if (entry.error) {
          statusHtml = '<span class="ml-err" title="Failed to load this raster"><i class="fas fa-exclamation-triangle"></i></span>';
        } else {
          const icon = entry.visible ? "fa-eye" : "fa-eye-slash";
          statusHtml = `<button type="button" class="ml-eye" data-fp="${fpAttr}" title="Show / hide on map"><i class="fas ${icon}"></i></button>`;
        }

        let legendHtml = "";
        if (!entry.loading && !entry.error && entry.range) {
          const legCls = entry.isResult ? "map-layer-legend is-result" : "map-layer-legend";
          let loLabel, hiLabel;
          if (entry.isResult) {
            // Legend bar is always red->green visually. invert=1 makes the
            // map render match this same semantic (low similarity rendered
            // red, high similarity rendered green) regardless of which raw
            // value is which. So the labels are the same either way.
            loLabel = "Low similarity";
            hiLabel = "High similarity";
          } else {
            loLabel = escHtml(fmtVal(entry.range[0]));
            hiLabel = escHtml(fmtVal(entry.range[1]));
          }
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

  // ----- Map setup -----
  function setupMap() {
    map = L.map('map-container', { preferCanvas: true }).setView([0, 0], 2);

    // Dedicated pane so input rasters render above the basemap (200) and
    // below drawn markers (overlayPane, 400).
    map.createPane("rasterOverlays");
    map.getPane("rasterOverlays").style.zIndex = 350;

    const baseLayers = {
      Street: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap contributors", maxZoom: 19,
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
    L.control.layers(baseLayers, null, { position: "bottomright", collapsed: true }).addTo(map);
    L.control.scale({ position: "bottomright", imperial: false }).addTo(map);

    map.addLayer(drawnItems);
    map.addLayer(uploadedPointsPreview);

    map.addLayer(aoiDrawnItems);

    const drawControl = new L.Control.Draw({
      position: 'topleft',
      draw: {
        marker: true,
        polygon: true, rectangle: true,
        polyline: false, circle: false, circlemarker: false,
      },
      edit: { featureGroup: drawnItems, remove: true },
    });
    map.addControl(drawControl);

    if (clearSimAoiBtn) clearSimAoiBtn.addEventListener('click', clearSimAoi);

    map.on(L.Draw.Event.CREATED, (event) => {
      if (event.layerType === 'marker') {
        drawnItems.addLayer(event.layer);
        updatePointsInput();
      } else {
        // polygon or rectangle -> AOI. Only one AOI shape at a time —
        // drawing a new one replaces whatever was there before.
        setSimAoiFromLayer(event.layer);
      }
    });
    map.on(L.Draw.Event.DELETED, updatePointsInput);
    // Dragging/moving an existing marker also changes the point set —
    // without this, an edited point's old coordinates would silently stay
    // in pointsInput and be re-submitted on the next run.
    map.on(L.Draw.Event.EDITED, updatePointsInput);

    // Click any pixel to inspect raster values. Suppress while a draw /
    // edit / delete handler is active so we don't fight with marker drops.
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
  }

  // ----- Reset / stale-result handling -----
  // Whenever the current point selection changes (added, deleted, edited,
  // re-uploaded, or the AOI mode is switched), any previously displayed
  // analysis output no longer reflects the current selection. Clear it
  // immediately rather than leaving stale results/overlays on screen for
  // the user to mistake as belonging to the new points.
  const resultSection      = document.getElementById('resultSection');
  const staleResultNotice  = document.getElementById('staleResultNotice');

  function clearResults({ notify } = {}) {
    currentResultKeys.forEach((k) => MapLayers.removeLayer(k));
    currentResultKeys = [];
    if (resultSection) resultSection.style.display = 'none';
    const chainLinks  = document.getElementById('resultChainLinks');
    const reportLinks = document.getElementById('resultReportLinks');
    if (chainLinks)  chainLinks.innerHTML = '';
    if (reportLinks) reportLinks.innerHTML = '';
    renderPointWarnings([]);
    $('#downloadMessLink').attr('href', '#');
    $('#downloadMnobisLink').attr('href', '#');
    if (staleResultNotice) {
      staleResultNotice.style.display = (notify && resultSectionWasShown) ? 'block' : 'none';
    }
  }

  // Tracks whether a result has ever been shown in this session, so we
  // only surface the "results are stale" notice when there was something
  // to invalidate (not on the very first, empty load).
  let resultSectionWasShown = false;

  function markPointsChanged() {
    const hadResults = resultSectionWasShown && currentResultKeys.length > 0;
    clearResults({ notify: hadResults });
  }

  // ----- Real spatial AOI (draw a polygon/rectangle, or upload a file) -----
  function applySimAoiFeatureCollection(fc) {
    aoiDrawnItems.clearLayers();
    const layer = L.geoJSON(fc, { style: { weight: 2 } });
    layer.eachLayer((l) => aoiDrawnItems.addLayer(l));
    simAoiInput.value = JSON.stringify(fc);
    if (simAoiStatus) {
      simAoiStatus.textContent = 'AOI set — the analysis will be restricted to this region.';
      simAoiStatus.classList.remove('text-muted');
      simAoiStatus.classList.add('text-success');
    }
    markPointsChanged(); // a changed AOI invalidates any previous result, same as edited points
    return layer;
  }

  function setSimAoiFromLayer(layer) {
    const feature = layer.toGeoJSON();
    applySimAoiFeatureCollection({ type: 'FeatureCollection', features: [feature] });
  }

  // Normalizes a raw parsed GeoJSON value (Feature, FeatureCollection, or
  // bare geometry) into a FeatureCollection shape.
  function toSimFeatureCollection(geojson) {
    if (!geojson) throw new Error('Empty GeoJSON');
    if (typeof geojson === 'string') geojson = JSON.parse(geojson);
    if (geojson.type === 'FeatureCollection') return geojson;
    if (geojson.type === 'Feature') return { type: 'FeatureCollection', features: [geojson] };
    if (geojson.type && geojson.coordinates) {
      return { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: geojson }] };
    }
    throw new Error('Unsupported GeoJSON structure.');
  }

  // Keeps only Polygon/MultiPolygon features — an uploaded AOI file with
  // points or lines mixed in (or containing none at all) should be
  // rejected clearly rather than silently producing an empty/wrong AOI.
  function validateSimAoiFeatureCollection(fc) {
    if (!fc || fc.type !== 'FeatureCollection' || !Array.isArray(fc.features) || fc.features.length === 0) {
      throw new Error('AOI file must contain at least one feature.');
    }
    const polygons = fc.features.filter((f) => {
      const g = f && f.geometry;
      return g && (g.type === 'Polygon' || g.type === 'MultiPolygon');
    });
    if (polygons.length === 0) {
      throw new Error('AOI file must contain a Polygon or MultiPolygon (not just points/lines).');
    }
    return { type: 'FeatureCollection', features: polygons };
  }

  function setSimAoiFromUpload(fc) {
    const layer = applySimAoiFeatureCollection(fc);
    if (map) {
      const bounds = layer.getBounds();
      if (bounds && bounds.isValid()) map.fitBounds(bounds.pad(0.1));
    }
  }

  function clearSimAoi() {
    aoiDrawnItems.clearLayers();
    simAoiInput.value = '';
    if (simAoiFileUpload) simAoiFileUpload.value = '';
    if (simAoiFileError) simAoiFileError.textContent = '';
    if (simAoiStatus) {
      simAoiStatus.textContent = 'No AOI set — using full extent.';
      simAoiStatus.classList.remove('text-success');
      simAoiStatus.classList.add('text-muted');
    }
  }

  if (simAoiFileUpload) {
    simAoiFileUpload.addEventListener('change', async () => {
      const file = simAoiFileUpload.files[0];
      if (!file) return;
      if (simAoiFileError) simAoiFileError.textContent = '';

      const name = (file.name || '').toLowerCase();
      const ext = name.split('.').pop();

      try {
        let gj;
        if (ext === 'geojson' || ext === 'json') {
          gj = JSON.parse(await file.text());
        } else if (ext === 'kml') {
          gj = await parseKMLToGeoJSON(await file.text());
        } else if (ext === 'kmz') {
          gj = await parseKMZToGeoJSON(await file.arrayBuffer());
        } else if (ext === 'zip') {
          gj = await parseShapefileZipToGeoJSON(await file.arrayBuffer());
        } else {
          throw new Error('Unsupported file type. Upload GeoJSON (.geojson/.json), ' +
                           'KML/KMZ, or a zipped Shapefile (.zip).');
        }
        const fc = validateSimAoiFeatureCollection(toSimFeatureCollection(gj));
        setSimAoiFromUpload(fc);
      } catch (err) {
        console.error(err);
        if (simAoiFileError) simAoiFileError.textContent = `Could not load AOI: ${err.message || err}`;
      }
    });
  }

  // ----- Uploaded-points map preview -----
  // Renders uploaded GeoJSON/CSV points on the map so the user can verify
  // their location and distribution before running the analysis, instead
  // of submitting blind. Kept in a separate layer group from `drawnItems`
  // so it's purely a preview: not editable/deletable via the draw
  // toolbar, and not counted when `updatePointsInput` rebuilds the
  // map-drawn point list.
  function renderUploadedPointsPreview(points) {
    uploadedPointsPreview.clearLayers();
    if (!points || !points.length) {
      if (fileSuccess) fileSuccess.textContent = '';
      return;
    }
    const latLngs = [];
    points.forEach(([lng, lat]) => {
      if (!isFinite(lng) || !isFinite(lat)) return;
      L.circleMarker([lat, lng], {
        radius: 6,
        color: '#2e7d32',
        weight: 2,
        fillColor: '#66bb6a',
        fillOpacity: 0.85,
      }).addTo(uploadedPointsPreview);
      latLngs.push([lat, lng]);
    });
    if (fileSuccess) {
      fileSuccess.textContent = `Loaded ${latLngs.length} point${latLngs.length === 1 ? '' : 's'} — shown on the map below.`;
    }
    if (latLngs.length) {
      try { map.fitBounds(L.latLngBounds(latLngs), { maxZoom: 12, padding: [30, 30] }); }
      catch (_) { /* single point or degenerate bounds — ignore */ }
    }
  }

  function updatePointsInput() {
    const points = [];
    drawnItems.eachLayer((layer) => {
      if (layer instanceof L.Marker) {
        const ll = layer.getLatLng();
        points.push([ll.lng, ll.lat]);
      }
    });
    pointsInput.value = JSON.stringify(points);
    markPointsChanged();
  }

  // ----- Radio handlers (map mode vs upload). Map stays visible in both. -----
  aoiOptionMap.addEventListener('change', () => {
    if (aoiOptionMap.checked) {
      fileUploadSection.style.display = 'none';
      aoiFileUpload.value = '';
      uploadedPointsInput.value = "";
      fileError.textContent = '';
      uploadedPointsPreview.clearLayers();
      if (fileSuccess) fileSuccess.textContent = '';
      markPointsChanged();
    }
  });
  aoiOptionFile.addEventListener('change', () => {
    if (aoiOptionFile.checked) {
      fileUploadSection.style.display = 'block';
      drawnItems.clearLayers();
      pointsInput.value = "";
      markPointsChanged();
    }
  });

  // ----- Full reset: points + results, no page refresh needed -----
  function resetAnalysis() {
    drawnItems.clearLayers();
    uploadedPointsPreview.clearLayers();
    pointsInput.value = '';
    uploadedPointsInput.value = '';
    aoiFileUpload.value = '';
    fileError.textContent = '';
    if (fileSuccess) fileSuccess.textContent = '';
    clearSimAoi();
    clearResults({ notify: false });
  }
  const resetAnalysisBtn = document.getElementById('resetAnalysisBtn');
  if (resetAnalysisBtn) resetAnalysisBtn.addEventListener('click', resetAnalysis);

  // ----- Shared point-extraction across GeoJSON / KML / Shapefile -----
  // A single GeoJSON "shape" can come from json/geojson, from togeojson's
  // KML conversion, or from shpjs's shapefile conversion (which can return
  // either one FeatureCollection or an array of them, one per shapefile
  // layer inside a zip). This walks all of that uniformly.
  function extractPointsFromGeoJSON(geojsonOrArray) {
    const points = [];
    const nonPointTypes = new Set();

    function visitGeometry(geometry) {
      if (!geometry) return;
      if (geometry.type === 'Point') {
        const c = geometry.coordinates;
        if (Array.isArray(c) && c.length >= 2 && isFinite(c[0]) && isFinite(c[1])) {
          points.push([c[0], c[1]]);
        }
      } else if (geometry.type === 'MultiPoint') {
        (geometry.coordinates || []).forEach((c) => {
          if (Array.isArray(c) && c.length >= 2 && isFinite(c[0]) && isFinite(c[1])) {
            points.push([c[0], c[1]]);
          }
        });
      } else if (geometry.type === 'GeometryCollection') {
        (geometry.geometries || []).forEach(visitGeometry);
      } else {
        nonPointTypes.add(geometry.type);
      }
    }

    function visitFeatureCollection(fc) {
      if (!fc) return;
      if (fc.type === 'FeatureCollection') {
        (fc.features || []).forEach((f) => visitGeometry(f && f.geometry));
      } else if (fc.type === 'Feature') {
        visitGeometry(fc.geometry);
      } else if (fc.type) {
        // A bare geometry object.
        visitGeometry(fc);
      }
    }

    if (Array.isArray(geojsonOrArray)) geojsonOrArray.forEach(visitFeatureCollection);
    else visitFeatureCollection(geojsonOrArray);

    return { points, nonPointTypes };
  }

  // Shared "we now have a point list, finish loading it" tail — used by
  // every format (GeoJSON/CSV/KML/KMZ/Shapefile) once points are extracted.
  function finishPointsLoad(points) {
    if (!points.length) {
      fileError.textContent = 'No valid points found in this file.';
      return;
    }
    uploadedPointsInput.value = JSON.stringify(points);
    fileError.textContent = '';
    renderUploadedPointsPreview(points);
    markPointsChanged();
  }

  async function parseKMLToGeoJSON(kmlText) {
    if (typeof toGeoJSON === 'undefined') {
      throw new Error('KML support needs the toGeoJSON library, which failed to load.');
    }
    const xml = new DOMParser().parseFromString(kmlText, 'text/xml');
    const parseError = xml.querySelector('parsererror');
    if (parseError) throw new Error('Could not parse this KML file — it may be malformed.');
    return toGeoJSON.kml(xml);
  }

  async function parseKMZToGeoJSON(arrayBuffer) {
    if (typeof JSZip === 'undefined') {
      throw new Error('KMZ support needs the JSZip library, which failed to load.');
    }
    const zip = await JSZip.loadAsync(arrayBuffer);
    const kmlEntry = Object.values(zip.files).find(
      (f) => !f.dir && f.name.toLowerCase().endsWith('.kml'));
    if (!kmlEntry) throw new Error('No .kml file found inside this KMZ archive.');
    const kmlText = await kmlEntry.async('string');
    return parseKMLToGeoJSON(kmlText);
  }

  async function parseShapefileZipToGeoJSON(arrayBuffer) {
    if (typeof shp === 'undefined') {
      throw new Error('Shapefile support needs the shpjs library, which failed to load.');
    }
    return await shp(arrayBuffer);
  }

  // ----- Points file upload -----
  aoiFileUpload.addEventListener('change', async () => {
    const file = aoiFileUpload.files[0];
    if (!file) { fileError.textContent = 'Please select a file to upload.'; return; }
    if (fileSuccess) fileSuccess.textContent = '';
    fileError.textContent = '';

    const name = (file.name || '').toLowerCase();
    const ext = name.split('.').pop();

    try {
      if (ext === 'csv') {
        const text = await file.text();
        processCSV(text);
        return;
      }

      if (ext === 'geojson' || ext === 'json') {
        const text = await file.text();
        const gj = JSON.parse(text);
        const { points } = extractPointsFromGeoJSON(gj);
        finishPointsLoad(points);
        return;
      }

      if (ext === 'kml') {
        const text = await file.text();
        const gj = await parseKMLToGeoJSON(text);
        const { points } = extractPointsFromGeoJSON(gj);
        finishPointsLoad(points);
        return;
      }

      if (ext === 'kmz') {
        const buf = await file.arrayBuffer();
        const gj = await parseKMZToGeoJSON(buf);
        const { points } = extractPointsFromGeoJSON(gj);
        finishPointsLoad(points);
        return;
      }

      if (ext === 'zip') {
        const buf = await file.arrayBuffer();
        const gj = await parseShapefileZipToGeoJSON(buf);
        const { points, nonPointTypes } = extractPointsFromGeoJSON(gj);
        // Unlike GeoJSON/KML (where mixed geometry types are normal and we
        // just keep the points), a shapefile is one geometry type for the
        // whole layer — so a non-point shapefile means the user uploaded
        // the wrong kind of file entirely, not a file with some
        // irrelevant extra features. Reject it clearly rather than
        // silently returning zero points.
        if (nonPointTypes.size > 0) {
          fileError.textContent =
            `This shapefile contains ${Array.from(nonPointTypes).join(', ')} geometry, ` +
            'not points. Please upload a point shapefile for sample points.';
          return;
        }
        finishPointsLoad(points);
        return;
      }

      fileError.textContent =
        'Unsupported file type. Upload GeoJSON (.geojson/.json), CSV, KML/KMZ, ' +
        'or a zipped point Shapefile (.zip).';
    } catch (err) {
      console.error(err);
      fileError.textContent = `Could not read this file: ${err.message}`;
    }
  });

  function processCSV(content) {
    if (fileSuccess) fileSuccess.textContent = '';
    try {
      const rows = content.split('\n');
      const header = rows[0].split(',');
      const latIndex = header.findIndex((c) => c.trim().toLowerCase() === 'latitude');
      const lngIndex = header.findIndex((c) => c.trim().toLowerCase() === 'longitude');
      if (latIndex === -1 || lngIndex === -1) {
        fileError.textContent = 'CSV file must have "Latitude" and "Longitude" columns.';
        return;
      }
      const points = [];
      rows.slice(1).forEach((row) => {
        const cols = row.split(',');
        const lat = parseFloat(cols[latIndex]);
        const lng = parseFloat(cols[lngIndex]);
        if (!isNaN(lat) && !isNaN(lng)) points.push([lng, lat]);
      });
      finishPointsLoad(points);
    } catch (e) {
      fileError.textContent = 'Error processing CSV file.';
      console.error(e);
    }
  }

  // ----- Selected layers list (now also previews on the map) -----
  // Dataset paths look like "/Africa/Kenya/kenya_x.tif" (continent/country)
  // or "/Global/global_x.tif" (no country level). Returns null for a
  // global dataset (unrestricted) or the country segment for a
  // country-specific one.
  function getCountryFromPath(filePath) {
    const parts = (filePath || '').split(/[\\/]/).filter(Boolean);
    if (!parts.length) return null;
    if (parts[0].toLowerCase() === 'global') return null;
    return parts.length > 1 ? parts[1] : null;
  }

  // Combining datasets from two different countries produces no valid
  // sample-point data (their extents never overlap) — catch it at
  // selection time rather than as a confusing empty/failed analysis
  // later. Global datasets are always allowed alongside a country one.
  function findConflictingCountrySelection(newFilePath) {
    const newCountry = getCountryFromPath(newFilePath);
    if (!newCountry) return null;
    let conflict = null;
    selectedFilesContainer.querySelectorAll('.selected-file-item input[type="hidden"]').forEach((inp) => {
      if (conflict) return;
      const existingCountry = getCountryFromPath(inp.value);
      if (existingCountry && existingCountry !== newCountry) conflict = existingCountry;
    });
    return conflict;
  }

  function addSelectedFile(filePath, fileName) {
    const conflictingCountry = findConflictingCountrySelection(filePath);
    if (conflictingCountry) {
      alert(
        `This dataset is from ${getCountryFromPath(filePath)}, but you already have a ` +
        `${conflictingCountry} dataset selected. Combining datasets from two different ` +
        `countries produces no valid output (their areas never overlap). Remove the ` +
        `${conflictingCountry} dataset first, or choose another ${conflictingCountry} or ` +
        `Global dataset instead.`
      );
      const checkbox = fileListElement.querySelector(`input[type="checkbox"][value="${filePath}"]`);
      if (checkbox) checkbox.checked = false;
      return;
    }

    const item = document.createElement('div');
    item.classList.add('mb-2', 'selected-file-item', 'd-flex', 'align-items-center');
    item.innerHTML = `
      <span class="flex-grow-1">${escHtml(fileName)}</span>
      <button type="button" class="btn btn-sm btn-danger ml-2 btn-delete" title="Remove">
        <i class="fas fa-trash"></i>
      </button>
      <input type="hidden" name="selectedFiles[]" value="${escHtml(filePath)}">
    `;
    item.querySelector('.btn-delete').addEventListener('click', () => {
      item.remove();
      const cb = fileListElement.querySelector(`input[type="checkbox"][value="${filePath}"]`);
      if (cb) cb.checked = false;
      MapLayers.removeLayer(filePath);
    });
    selectedFilesContainer.appendChild(item);
    MapLayers.addLayer(filePath, fileName);
  }

  function removeSelectedFile(filePath) {
    selectedFilesContainer.querySelectorAll('.selected-file-item').forEach((it) => {
      const inp = it.querySelector(`input[type="hidden"][value="${filePath}"]`);
      if (inp) it.remove();
    });
    MapLayers.removeLayer(filePath);
  }

  // ----- Sample-point warnings (points excluded per dataset) -----
  function renderPointWarnings(warnings) {
    let container = document.getElementById('pointWarnings');
    const resultSection = document.getElementById('resultSection');
    if (!container) {
      container = document.createElement('div');
      container.id = 'pointWarnings';
      container.className = 'alert alert-warning mt-2';
      if (resultSection) resultSection.insertBefore(container, resultSection.firstChild);
    }
    if (!warnings || !warnings.length) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }
    container.style.display = 'block';
    container.innerHTML =
      '<strong><i class="fas fa-exclamation-triangle"></i> Some sample points were excluded:</strong>' +
      '<ul class="mb-0 pl-3">' +
      warnings.map((w) => `<li>${escHtml(w)}</li>`).join('') +
      '</ul>';
  }

  // ----- Chain-workflow links (open the result in Land Statistics) -----
  function renderChainLinks(resultPath) {
    let container = document.getElementById('resultChainLinks');
    if (!container) {
      container = document.createElement('div');
      container.id = 'resultChainLinks';
      container.className = 'mt-2';
      const resultSection = document.getElementById('resultSection');
      if (resultSection) resultSection.appendChild(container);
    }
    const statsUrl = (CONFIG.pages && CONFIG.pages.statistics) || '/statistics';
    const link = (path, label) => {
      if (!path) return '';
      const href = `${statsUrl}?file=${encodeURIComponent(path)}`;
      return `<a href="${href}" target="_blank" rel="noopener" class="btn btn-outline-success btn-sm btn-block">
        <i class="fas fa-arrow-circle-right"></i> ${escHtml(label)}
        <i class="fas fa-external-link-alt" style="font-size:.75em;opacity:.7;margin-left:4px"></i>
      </a>`;
    };
    container.innerHTML =
      link(resultPath.mnobis, 'Open Mahalanobis in Land Statistics') +
      link(resultPath.mess,   'Open MESS in Land Statistics');
  }

  // ----- PDF report -----
  function renderReportButton(resultPaths, description, selectedLayers, pointsStr) {
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
      () => exportReport(resultPaths, description, selectedLayers, pointsStr));
  }

  function exportReport(resultPaths, description, selectedLayers, pointsStr) {
    if (!API.reportSimilarity) return;
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
    // Selected layers: pass the display names (basenames) so the report
    // reads cleanly. Frontend caller already passes file paths; we'll trim.
    const layerNames = (selectedLayers || []).map(p =>
      String(p).split(/[\\/]/).pop());

    const btn = document.getElementById('exportReportBtn');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating PDF…';
    }
    fetch(API.reportSimilarity, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify({
        result_paths: resultPaths || {},
        description: description || 'Similarity analysis',
        selected_layers: layerNames,
        points: pointsStr || '',
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
        const safeName = (description || 'similarity_report').replace(/[^\w-]+/g, '_').slice(0, 60);
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

  // ----- Validate + submit -----
  function validateForm(event) {
    if (event && event.preventDefault) event.preventDefault();

    const description = document.getElementById('description').value.trim();
    if (!description) { alert("Please provide a description."); return false; }

    const selectedFiles = selectedFilesContainer.querySelectorAll('.selected-file-item');
    if (selectedFiles.length < 2) { alert("Please select at least two raster files."); return false; }

    const useMapPts  = document.getElementById('aoiOptionMap').checked;
    const useFilePts = document.getElementById('aoiOptionFile').checked;
    let points = [];
    if (useMapPts) {
      points = JSON.parse(pointsInput.value || "[]");
      if (points.length === 0) { alert("Please add at least one sample point on the map."); return false; }
    } else if (useFilePts) {
      points = JSON.parse(uploadedPointsInput.value || "[]");
      if (points.length === 0) { alert("Please upload points data in GeoJSON or CSV format."); return false; }
      pointsInput.value = JSON.stringify(points);
    }

    const formData = {
      selectedFiles: Array.from(selectedFiles).map(i => i.querySelector('input[type="hidden"]').value),
      points: pointsInput.value,
      description,
      aoi: simAoiInput ? simAoiInput.value : '',
    };

    try { $('#progressModal').modal('show'); } catch (_) {}
    if (staleResultNotice) staleResultNotice.style.display = 'none';
    renderPointWarnings([]);

    fetch(API.processLandSimilarity, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
      },
      body: JSON.stringify(formData),
    })
      .then((response) => response.json())
      .then((data) => {
        try { $('#progressModal').modal('hide'); } catch (_) {}
        if (data.status === 'success') {
          const messUrl   = (data.result_url && data.result_url.mess)   || "";
          const mnobisUrl = (data.result_url && data.result_url.mnobis) || "";
          $('#downloadMessLink').attr('href', messUrl);
          $('#downloadMnobisLink').attr('href', mnobisUrl);
          $('#resultSection').show();
          resultSectionWasShown = true;
          if (staleResultNotice) staleResultNotice.style.display = 'none';

          // Sample points excluded because they fell outside a dataset's
          // extent or landed on NoData — surfaced instead of silently
          // affecting the result.
          renderPointWarnings(data.point_warnings || []);

          // Chain-workflow buttons: one-click jump into Land Statistics with
          // the result pre-selected. Render once; reuse the same buttons on
          // re-runs by clearing and re-populating ``#resultChainLinks``.
          renderChainLinks(data.result_path || {});
          renderReportButton(
            data.result_path || {},
            formData.description,
            formData.selectedFiles,
            formData.points,
          );

          // Tile both results on the main map. Replace any previous.
          currentResultKeys.forEach((k) => MapLayers.removeLayer(k));
          currentResultKeys = [];
          if (data.result_path && data.result_path.mnobis) {
            // Mahalanobis: lower value = greater similarity. Invert the
            // colour ramp so low values render green / good.
            MapLayers.addLayer(data.result_path.mnobis,
                               "Mahalanobis (similarity)",
                               { source: "result", invert: true, opacity: 0.85 });
            currentResultKeys.push(data.result_path.mnobis);
          }
          if (data.result_path && data.result_path.mess) {
            // MESS: higher = more similar. Default rdylgn ramp.
            MapLayers.addLayer(data.result_path.mess,
                               "MESS (continuous similarity)",
                               { source: "result", opacity: 0.75 });
            currentResultKeys.push(data.result_path.mess);
          }

          $('html, body').animate({ scrollTop: $('#resultSection').offset().top }, 500);
        } else {
          alert(`Error: ${data.message || "Unknown error"}`);
        }
      })
      .catch((err) => {
        try { $('#progressModal').modal('hide'); } catch (_) {}
        console.error('Error:', err);
        alert('An error occurred while processing the form.');
      });

    return false;
  }

  document.getElementById('submitBtn').addEventListener('click', validateForm);
  if (selectedFilesForm) selectedFilesForm.addEventListener('submit', validateForm);

  // ----- Init -----
  setupMap();

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
    onFileSelect:   (filePath, item) => addSelectedFile(filePath, item.name),
    onFileDeselect: (filePath) => removeSelectedFile(filePath),
    onFileInfo:     (filePath, item) => showLayerMetadata(filePath, item.name, "data"),
  });

  // Live search over the loaded data-layers tree.
  const layerSearchInput = document.getElementById('layerSearchInput');
  if (layerSearchInput && fileListElement) {
    layerSearchInput.addEventListener('input', () => {
      DirectoryBrowser.filter(fileListElement, layerSearchInput.value);
    });
  }

});
