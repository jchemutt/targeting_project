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
  const fileListElement     = document.getElementById('fileList');
  const selectedFilesForm   = document.getElementById('selectedFilesForm');
  const selectedFilesContainer = document.getElementById('selectedFilesContainer');

  let map = null;
  let drawnItems = new L.FeatureGroup();
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
  async function showLayerMetadata(path, name, source) {
    const titleEl = document.getElementById("layerMetaTitle");
    const bodyEl  = document.getElementById("layerMetaBody");
    const dlEl    = document.getElementById("layerMetaDownload");
    if (!bodyEl || !API.layerMetadata) return;
    if (titleEl) titleEl.textContent = name || "Layer metadata";
    const src = source === "result" ? "&source=result" : "";
    if (dlEl) dlEl.href = `${API.layerMetadata}?path=${encodeURIComponent(path)}${src}&download=1`;
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
      active.forEach((entry, fp) => {
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
    L.control.layers(baseLayers, null, { position: "bottomleft", collapsed: true }).addTo(map);
    L.control.scale({ position: "bottomleft", imperial: false }).addTo(map);

    map.addLayer(drawnItems);

    const drawControl = new L.Control.Draw({
      position: 'topleft',
      draw: {
        marker: true,
        polygon: false, polyline: false, circle: false, rectangle: false, circlemarker: false,
      },
      edit: { featureGroup: drawnItems, remove: true },
    });
    map.addControl(drawControl);

    map.on(L.Draw.Event.CREATED, (event) => {
      drawnItems.addLayer(event.layer);
      updatePointsInput();
    });
    map.on(L.Draw.Event.DELETED, updatePointsInput);

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

  function updatePointsInput() {
    const points = [];
    drawnItems.eachLayer((layer) => {
      if (layer instanceof L.Marker) {
        const ll = layer.getLatLng();
        points.push([ll.lng, ll.lat]);
      }
    });
    pointsInput.value = JSON.stringify(points);
  }

  // ----- Radio handlers (map mode vs upload). Map stays visible in both. -----
  aoiOptionMap.addEventListener('change', () => {
    if (aoiOptionMap.checked) {
      fileUploadSection.style.display = 'none';
      aoiFileUpload.value = '';
      uploadedPointsInput.value = "";
      fileError.textContent = '';
    }
  });
  aoiOptionFile.addEventListener('change', () => {
    if (aoiOptionFile.checked) {
      fileUploadSection.style.display = 'block';
      drawnItems.clearLayers();
      pointsInput.value = "";
    }
  });

  // ----- Points file upload -----
  aoiFileUpload.addEventListener('change', () => {
    const file = aoiFileUpload.files[0];
    if (!file) { fileError.textContent = 'Please select a file to upload.'; return; }
    const reader = new FileReader();
    const ext = file.name.split('.').pop().toLowerCase();
    if (ext === 'geojson')      reader.onload = (e) => processGeoJSON(e.target.result);
    else if (ext === 'csv')     reader.onload = (e) => processCSV(e.target.result);
    else { fileError.textContent = 'Invalid file format. Please upload a GeoJSON or CSV file.'; return; }
    reader.readAsText(file);
  });

  function processGeoJSON(content) {
    try {
      const geoJSON = JSON.parse(content);
      const points = [];
      geoJSON.features.forEach((feature) => {
        if (feature.geometry.type === 'Point' && feature.geometry.coordinates.length === 2) {
          const [lng, lat] = feature.geometry.coordinates;
          points.push([lng, lat]);
        }
      });
      if (points.length === 0) { fileError.textContent = 'No valid points found in the GeoJSON file.'; return; }
      uploadedPointsInput.value = JSON.stringify(points);
      fileError.textContent = '';
    } catch (e) {
      fileError.textContent = 'Error processing GeoJSON file.';
      console.error(e);
    }
  }

  function processCSV(content) {
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
      if (points.length === 0) { fileError.textContent = 'No valid points found in the CSV file.'; return; }
      uploadedPointsInput.value = JSON.stringify(points);
      fileError.textContent = '';
    } catch (e) {
      fileError.textContent = 'Error processing CSV file.';
      console.error(e);
    }
  }

  // ----- Selected layers list (now also previews on the map) -----
  function addSelectedFile(filePath, fileName) {
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
    };

    try { $('#progressModal').modal('show'); } catch (_) {}

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

          // Chain-workflow buttons: one-click jump into Land Statistics with
          // the result pre-selected. Render once; reuse the same buttons on
          // re-runs by clearing and re-populating ``#resultChainLinks``.
          renderChainLinks(data.result_path || {});

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

});
