// Land Statistics page logic.
// Modernized: server-side tiled previews, basemap switcher + scale, layer
// metadata viewer, reference-layer overlay, polished results table.

document.addEventListener('DOMContentLoaded', function () {

  // ----- Config / element refs -----
  const CONFIG = (function () {
    const el = document.getElementById("js-config");
    try { return el ? JSON.parse(el.textContent) : {}; }
    catch (e) { console.error("Invalid #js-config JSON:", e); return {}; }
  })();
  const API = CONFIG.apiEndpoints || {};

  const referenceSelect = document.querySelector("#zoneIdColumn");
  const processBtn      = document.getElementById("processBtn");

  let map = null;
  let currentRasterKey  = null;  // the processed-file currently shown on the map
  let currentReferenceKey = null;  // the reference layer currently shown on the map
  let selectedDescription = null;
  let selectedRasterFile  = null;
  let selectedCountry     = "";
  let selectedReferenceFile = "";

  // ----- Stat-checkbox handling (preserved from the original page) -----
  document.getElementById('allStatsCheckbox').addEventListener('change', function () {
    document.querySelectorAll('.stat-checkbox:not(#allStatsCheckbox)').forEach(cb => cb.checked = this.checked);
    updateStatDropdownLabel();
  });
  document.querySelectorAll('.stat-checkbox:not(#allStatsCheckbox)').forEach(cb => {
    cb.addEventListener('change', function () {
      const all = document.getElementById('allStatsCheckbox');
      const others = document.querySelectorAll('.stat-checkbox:not(#allStatsCheckbox)');
      all.checked = Array.from(others).every(c => c.checked);
      updateStatDropdownLabel();
    });
  });

  function updateStatDropdownLabel() {
    const selectedStats = getSelectedStatistics();
    const btn = document.getElementById('statDropdown');
    if (selectedStats.length === 0)       btn.textContent = 'Choose statistics';
    else if (selectedStats.length <= 2)   btn.textContent = selectedStats.join(', ');
    else                                  btn.textContent = `${selectedStats.length} selected`;
  }

  function getSelectedStatistics() {
    return Array.from(document.querySelectorAll('.stat-checkbox:checked:not(#allStatsCheckbox)'))
                .map(cb => cb.value);
  }

  // ----- Tiny helpers -----
  function escHtml(s) {
    return String(s).replace(/[&<>"']/g,
      c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmtVal(v) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1000) return Math.round(v).toLocaleString();
    if (a >= 1)    return String(Math.round(v * 100) / 100);
    if (a === 0)   return "0";
    return Number(v).toPrecision(2);
  }
  function metaNum(v) {
    if (v === null || v === undefined || v === "" || isNaN(v)) return "—";
    return Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 });
  }

  // ----- Layer metadata modal -----
  function renderMetaTable(m) {
    const rows = [];
    const add = (k, v) => { if (v !== null && v !== undefined && v !== "") rows.push([k, v]); };
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

  // ----- Map layers control (tiled previews) -----
  // Same shape as the suitability/similarity controls. Uses numeric legend
  // labels for everything since this page is purely "look at the raster".
  const MapLayers = (function () {
    const active = new Map();
    let controlEl = null;
    let firstAddDone = false;

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
      const isResult    = opts.source === "result";
      const srcParam    = isResult ? "&source=result" : "";
      const cmapParam   = opts.cmap ? `&cmap=${encodeURIComponent(opts.cmap)}` : "";
      const invertParam = opts.invert ? "&invert=1" : "";
      const opacity     = typeof opts.opacity === "number" ? opts.opacity : 0.75;

      active.set(layerKey, {
        fileName, loading: true, visible: true, opacity, isResult, invert: !!opts.invert,
        legendLabels: opts.legendLabels || null,
      });
      render();

      try {
        const metaResp = await fetch(`${API.rasterMeta}?path=${encodeURIComponent(layerKey)}${srcParam}`);
        if (!metaResp.ok) throw new Error(`metadata HTTP ${metaResp.status}`);
        const meta = await metaResp.json();
        if (!meta || !meta.bounds) throw new Error("no bounds in metadata response");
        if (!active.has(layerKey)) return;

        const tileUrl = `${API.tileRaster}?path=${encodeURIComponent(layerKey)}${srcParam}${cmapParam}${invertParam}`;
        const bounds = L.latLngBounds(
          [meta.bounds[1], meta.bounds[0]],
          [meta.bounds[3], meta.bounds[2]]
        );
        const layerOpts = { opacity, bounds, tileSize: 256, noWrap: true, pane: "rasterOverlays" };
        if (typeof meta.maxzoom === "number") layerOpts.maxNativeZoom = meta.maxzoom;
        const leafletLayer = L.tileLayer(tileUrl, layerOpts);
        leafletLayer.addTo(map);

        active.set(layerKey, {
          fileName, leafletLayer, bounds,
          range: Array.isArray(meta.range) ? meta.range : null,
          loading: false, visible: true, opacity, isResult, invert: !!opts.invert,
          legendLabels: opts.legendLabels || null,
        });

        // Always fit to the most recently added raster (single-raster page).
        try { map.fitBounds(bounds); } catch (_) {}
        firstAddDone = true;
      } catch (err) {
        console.error("Layer preview failed for", layerKey, err);
        if (!active.has(layerKey)) return;
        active.set(layerKey, { fileName, loading: false, error: true, visible: false, opacity, isResult });
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

    function render() {
      const el = ensure();
      if (!el) return;
      if (active.size === 0) { el.style.display = "none"; el.innerHTML = ""; return; }
      el.style.display = "block";

      const parts = ['<div class="map-layers-head"><i class="fas fa-layer-group"></i>Map layers</div>'];
      active.forEach((entry, fp) => {
        const safeName = escHtml(entry.fileName || fp);
        const fpAttr = encodeURIComponent(fp);
        let statusHtml;
        if (entry.loading)      statusHtml = '<span class="ml-spinner"><i class="fas fa-spinner fa-spin"></i></span>';
        else if (entry.error)   statusHtml = '<span class="ml-err" title="Failed to load this raster"><i class="fas fa-exclamation-triangle"></i></span>';
        else {
          const icon = entry.visible ? "fa-eye" : "fa-eye-slash";
          statusHtml = `<button type="button" class="ml-eye" data-fp="${fpAttr}" title="Show / hide on map"><i class="fas ${icon}"></i></button>`;
        }

        let legendHtml = "";
        if (!entry.loading && !entry.error && entry.range) {
          const legCls = entry.isResult ? "map-layer-legend is-result" : "map-layer-legend";
          let loLabel, hiLabel;
          if (entry.legendLabels) {
            loLabel = escHtml(entry.legendLabels[0]);
            hiLabel = escHtml(entry.legendLabels[1]);
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
          showLayerMetadata(fp, ent ? ent.fileName : fp, ent && ent.isResult ? "result" : "data");
        });
      });
    }

    return { addLayer, removeLayer };
  })();

  // ----- Map setup -----
  function setupMap() {
    map = L.map('map-container', { preferCanvas: true }).setView([0, 0], 2);

    // Dedicated pane so the input/result raster renders above the basemap
    // (zIndex 200) and below anything in the overlay pane (400).
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
  }

  // ----- Raster preview (replaces visualizeRasterFile) -----
  // Pick colormap based on the file's description (matches what suitability /
  // similarity use for their own results, so colours stay consistent).
  function previewRasterFile(filePath, fileName, description) {
    if (currentRasterKey) {
      MapLayers.removeLayer(currentRasterKey);
      currentRasterKey = null;
    }
    const opts = { source: "result", opacity: 0.8 };
    if (description === "Land suitability raster file") {
      opts.legendLabels = ["Low suitability", "High suitability"];
    } else if (description === "Mahalanobis Distance raster file") {
      // Low value = high similarity. invert=1 makes the colours match so red
      // = low similarity, green = high similarity on the map.
      opts.invert = true;
      opts.legendLabels = ["Low similarity", "High similarity"];
    } else if (description === "MESS raster file") {
      // High MESS = more similar. Default ramp already renders red->green
      // for low->high, matching "Low similarity" -> "High similarity".
      opts.legendLabels = ["Low similarity", "High similarity"];
    }
    // For other / unknown descriptions, MapLayers falls back to numeric labels.
    MapLayers.addLayer(filePath, fileName, opts);
    currentRasterKey = filePath;
  }

  // ----- User files (processed outputs) -----
  async function fetchUserFiles() {
    try {
      const response = await fetch(API.userFiles);
      if (!response.ok) throw new Error('Failed to fetch user files.');
      const files = await response.json();
      files.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      return files;
    } catch (error) {
      console.error('Error fetching user files:', error);
      alert('Could not load user files. Please try again later.');
      return [];
    }
  }

  async function displayUserFiles() {
    const files = await fetchUserFiles();
    const tableBody = document.getElementById('userFilesTableBody');
    tableBody.innerHTML = '';

    files.forEach(file => {
      const row = document.createElement('tr');
      row.innerHTML = `
        <td>${escHtml(file.title)}</td>
        <td>${new Date(file.created_at).toLocaleString()}</td>
        <td>
          <input type="radio" name="fileSelection" class="file-radio"
                 value="${escHtml(file.file_path)}"
                 data-name="${escHtml(file.title)}"
                 data-description="${escHtml(file.description)}"
                 data-country="${escHtml(file.country)}" />
        </td>
      `;
      tableBody.appendChild(row);
    });

    document.querySelectorAll('.file-radio').forEach(radio => {
      radio.addEventListener('change', async function () {
        selectedRasterFile = this.value;
        selectedDescription = this.dataset.description;
        selectedCountry = this.dataset.country;
        const fileName = this.dataset.name;

        // Drop any previous reference-layer overlay & selection — the new
        // file may cover a different country/region.
        if (currentReferenceKey) {
          MapLayers.removeLayer(currentReferenceKey);
          currentReferenceKey = null;
        }
        selectedReferenceFile = "";

        enableProcessButton();
        previewRasterFile(selectedRasterFile, fileName, selectedDescription);
        await fetchReferenceLayers(selectedCountry);

        tableBody.querySelectorAll('tr').forEach(r => r.classList.remove('is-selected'));
        const row = this.closest('tr');
        if (row) row.classList.add('is-selected');
      });
    });

    // Whole-row click selects the file.
    tableBody.querySelectorAll('tr').forEach(row => {
      row.addEventListener('click', (ev) => {
        if (ev.target.tagName === 'INPUT') return;
        const radio = row.querySelector('.file-radio');
        if (radio && !radio.checked) {
          radio.checked = true;
          radio.dispatchEvent(new Event('change'));
        }
      });
    });
  }

  // ----- Reference layers -----
  async function fetchReferenceLayers(rasterFile) {
    try {
      const referencelayer = rasterFile.replace(/\\/g, "/");
      const url = `${API.referenceLayers}?raster_path=${encodeURIComponent(referencelayer)}`;
      const response = await fetch(url);
      const data = await response.json();
      const referenceLayers = data.raster_files;

      referenceSelect.innerHTML = '<option value="">Select reference layer…</option>';

      if (!referenceLayers || referenceLayers.length === 0) {
        referenceSelect.innerHTML += '<option value="" disabled>No reference layers available</option>';
        return;
      }
      referenceLayers.forEach(layer => {
        const option = document.createElement("option");
        option.value = layer.file_path;
        option.textContent = layer.name;
        referenceSelect.appendChild(option);
      });

      referenceSelect.removeEventListener('change', handleReferenceChange);
      referenceSelect.addEventListener('change', handleReferenceChange);
    } catch (error) {
      console.error('Error fetching reference layers:', error);
      alert('Could not load reference layers. Please try again.');
      referenceSelect.innerHTML = '<option value="" disabled>Error loading layers</option>';
    }
  }

  function handleReferenceChange() {
    selectedReferenceFile = this.value;
    enableProcessButton();

    // Preview the reference layer on the map too. Reference layers come from
    // the data directory; if it isn't a tile-able raster the control just
    // shows an error icon for that entry and the page keeps working.
    if (currentReferenceKey) {
      MapLayers.removeLayer(currentReferenceKey);
      currentReferenceKey = null;
    }
    if (selectedReferenceFile) {
      const refName = this.options[this.selectedIndex].textContent || "Reference layer";
      MapLayers.addLayer(selectedReferenceFile, "Ref: " + refName, { opacity: 0.45 });
      currentReferenceKey = selectedReferenceFile;
    }
  }

  function enableProcessButton() {
    processBtn.disabled = !(selectedReferenceFile && selectedRasterFile);
  }

  // ----- Process statistics (unchanged backend contract) -----
  processBtn.addEventListener('click', async function () {
    if (!selectedReferenceFile || !selectedRasterFile) {
      alert('Please select a reference file and a raster file.');
      return;
    }
    const selectedStats = getSelectedStatistics();
    if (selectedStats.length === 0) {
      alert("Please select at least one statistic type.");
      return;
    }

    const csrfToken = document.querySelector('input[name="csrfmiddlewaretoken"]').value;
    $('#processingModal').modal('show');

    try {
      const response = await fetch(API.processStatistics, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({
          reference_layer: selectedReferenceFile,
          raster_path: selectedRasterFile,
          description: selectedDescription,
          stat_types: selectedStats,
        }),
      });

      const data = await response.json();
      if (data.status === 'success') {
        displayResults(data.results);
        // Hide the empty-state hint.
        const hint = document.getElementById('resultsEmptyHint');
        if (hint) hint.style.display = 'none';
      } else {
        alert(`Error: ${data.message}`);
      }
    } catch (error) {
      console.error('Error processing zonal statistics:', error);
    } finally {
      setTimeout(() => { $('#processingModal').modal('hide'); }, 500);
    }
  });

  // ----- Results table + chart -----
  let statChartInstance = null;

  // Format a stat value: integers stay integer, decimals get up to 3 sig figs
  // but never more than 2 decimals for big numbers.
  function fmtStat(v) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    if (Number.isInteger(v)) return v.toLocaleString();
    const a = Math.abs(v);
    if (a >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
    if (a >= 1)    return v.toFixed(2);
    if (a === 0)   return "0";
    return Number(v).toPrecision(3);
  }

  function displayResults(statistics) {
    const resultsSection = document.getElementById('resultsSection');
    const resultsTable   = document.getElementById('resultsTable');
    const downloadCsvBtn = document.getElementById('downloadCsvBtn');
    const plotChartBtn   = document.getElementById('plotChartBtn');

    if (!statistics || statistics.length === 0) {
      resultsTable.innerHTML = '<p class="text-muted">No data available.</p>';
      return;
    }

    const statLabel    = statistics[0].stat_label;
    const statsPerClass = statistics[0].statistics;
    const classes       = Object.keys(statLabel);
    const selectedStats = getSelectedStatistics();

    let headerCells = `<th>Class</th><th>Land Suitability Area (%)</th>`;
    selectedStats.forEach(stat => {
      headerCells += `<th>${escHtml(stat.charAt(0).toUpperCase() + stat.slice(1))}</th>`;
    });

    let bodyRows = '';
    classes.forEach(cls => {
      let row = `<td>${escHtml(cls)}</td><td>${fmtStat(statLabel[cls])}</td>`;
      selectedStats.forEach(stat => {
        const value = statsPerClass[cls]?.[stat];
        row += `<td>${fmtStat(value)}</td>`;
      });
      bodyRows += `<tr>${row}</tr>`;
    });

    resultsTable.innerHTML = `
      <table class="table table-bordered table-sm stats-results-table">
        <thead><tr>${headerCells}</tr></thead>
        <tbody>${bodyRows}</tbody>
      </table>
    `;

    resultsSection.style.display = 'block';
    downloadCsvBtn.style.display = 'inline-block';
    plotChartBtn.style.display   = 'inline-block';
    downloadCsvBtn.onclick = () => downloadCsv(classes, statLabel, statsPerClass, selectedStats);

    const statSelect = document.getElementById('statSelect');
    statSelect.innerHTML = selectedStats.map(stat => `<option value="${stat}">${stat}</option>`).join('');
    statSelect.onchange = () => renderChart(statSelect.value, classes, statsPerClass);

    plotChartBtn.onclick = () => {
      const defaultStat = statSelect.value || selectedStats[0];
      renderChart(defaultStat, classes, statsPerClass);
      $('#chartModal').modal('show');
    };
  }

  function renderChart(selectedStat, classes, statsPerClass) {
    const ctx = document.getElementById('statChart').getContext('2d');
    const values = classes.map(cls => statsPerClass[cls]?.[selectedStat] ?? 0);

    if (statChartInstance) statChartInstance.destroy();

    statChartInstance = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: classes,
        datasets: [{
          label: selectedStat.charAt(0).toUpperCase() + selectedStat.slice(1),
          data: values,
          backgroundColor: 'rgba(0, 153, 51, 0.75)',
          borderColor: '#007a29',
          borderWidth: 1,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { y: { beginAtZero: true } },
        plugins: { legend: { display: false } },
      },
    });
  }

  function downloadCsv(classes, statLabel, statsPerClass, selectedStats) {
    let csv = "Class,Land Suitability Area (%)";
    selectedStats.forEach(stat => csv += `,${stat}`);
    csv += "\n";
    classes.forEach(cls => {
      let row = `${cls},${statLabel[cls]}`;
      selectedStats.forEach(stat => {
        const v = statsPerClass[cls]?.[stat];
        row += `,${v !== null && v !== undefined ? v : ''}`;
      });
      csv += row + "\n";
    });
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', 'land_statistics.csv');
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  // ----- Init -----
  setupMap();
  displayUserFiles();
});
