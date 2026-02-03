

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

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);

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
     Directory loading
  ========================= */
  async function fetchDirectoryContents(directoryPath) {
    try {
      const response = await fetch(`/api/getDirectoryContents?path=${encodeURIComponent(directoryPath)}`);
      if (!response.ok) throw new Error("Failed to fetch directory contents");
      return await response.json();
    } catch (error) {
      console.error("Error fetching directory contents:", error);
      alert("Failed to load directory contents. Please try again later.");
      return [];
    }
  }

  async function fetchFolderConfigurations(folderName) {
  const response = await fetch(`/api/getFolderConfigurations?folder=${encodeURIComponent(folderName)}`);
  if (!response.ok) throw new Error("Failed to fetch folder configurations");
  return await response.json(); // expects { center: [lat,lng], zoom: number }
}

async function updateMapView(folderName) {
  if (!map) return;
  const { center, zoom } = await fetchFolderConfigurations(folderName);
  map.setView(center, zoom);
}


  function sanitizeFilePath(filePath) {
    return filePath.replace(/[^a-zA-Z0-9]/g, "_");
  }

  async function displayDirectoryContents(directoryContents, parentElement, directoryPath) {
    const ul = document.createElement("ul");
    ul.classList.add("list-group");

    directoryContents.forEach((item) => {
      const li = document.createElement("li");
      li.classList.add("list-group-item");

      if (item.type === "directory") {
        li.classList.add("folder");
        li.innerHTML = `<i class="fas fa-caret-right folder-icon mr-2"></i><i class="fas fa-folder mr-2"></i>${item.name}`;
        li.style.cursor = "pointer";

        li.addEventListener("click", async (event) => {
          event.stopPropagation();
          if (!li.dataset.loaded) {
            const sub = await fetchDirectoryContents(`${directoryPath}/${item.name}`);
            displayDirectoryContents(sub, li.querySelector(".folder-content"), `${directoryPath}/${item.name}`);
            li.dataset.loaded = true;
          }
          li.classList.toggle("expanded");
          const folderContent = li.querySelector(".folder-content");
          folderContent.style.display = folderContent.style.display === "block" ? "none" : "block";
          const folderIcon = li.querySelector(".folder-icon");
          folderIcon.classList.toggle("fa-caret-right");
          folderIcon.classList.toggle("fa-caret-down");
           try {
                await updateMapView(item.name);
            } catch (e) {
                console.error("updateMapView failed:", e);
            }
        });

        const folderContent = document.createElement("div");
        folderContent.classList.add("folder-content");
        li.appendChild(folderContent);
      } else if (item.type === "file") {
        li.classList.add("file");
        li.style.height = "30px";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = `${directoryPath}/${item.name}`;
        checkbox.classList.add("mr-2");

        checkbox.addEventListener("click", (event) => event.stopPropagation());
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) addSelectedFile(checkbox.value, item.name, item.min_val, item.max_val);
          else removeSelectedFileByFilePath(checkbox.value);
        });

        const label = document.createElement("label");
        label.classList.add("file-name");
        label.textContent = item.name;
        label.style.lineHeight = "30px";
        label.setAttribute("title", item.name);

        li.appendChild(checkbox);
        li.appendChild(label);
      }

      ul.appendChild(li);
    });

    parentElement.appendChild(ul);
  }

  /* =========================
     Raster table logic (yours)
  ========================= */
  let rasterCounter = 0;
  let groupRows = {};

  function addSelectedFile(filePath, fileName, minVal, maxVal) {
    rasterCounter++;
    const rasterId = rasterCounter;
    const sanitized = sanitizeFilePath(filePath);

    const tableBody = document.getElementById("rasterTableBody");
    const row = document.createElement("tr");
    row.setAttribute("data-raster-id", rasterId);
    row.setAttribute("data-original-filepath", filePath);

    row.innerHTML = `
      <td class="group-cell"></td>
      <td>${fileName}</td>
      <td><input type="text" class="form-control form-control-sm" name="rasterParameters[${sanitized}][min_val]" value="${minVal}" onchange="validateMinVal(this, ${minVal})"></td>
      <td><input type="text" class="form-control form-control-sm" name="rasterParameters[${sanitized}][opti_from]" placeholder="Optimal From" onchange="validateOptiFrom(this)"></td>
      <td><input type="text" class="form-control form-control-sm" name="rasterParameters[${sanitized}][opti_to]" placeholder="Optimal To" onchange="validateOptiTo(this)"></td>
      <td><input type="text" class="form-control form-control-sm" name="rasterParameters[${sanitized}][max_val]" value="${maxVal}" onchange="validateMaxVal(this, ${maxVal})"></td>
      <td>
        <select id="combine_${sanitized}" name="rasterParameters[${sanitized}][combine]" class="form-control form-control-sm">
          <option value="Yes">Yes</option>
          <option value="No">No</option>
        </select>
      </td>
      <td>
        <button type="button" class="btn btn-secondary btn-sm move-up-btn"><i class="fas fa-arrow-up"></i></button>
        <button type="button" class="btn btn-secondary btn-sm move-down-btn"><i class="fas fa-arrow-down"></i></button>
        <button type="button" class="btn btn-danger btn-sm remove-btn"><i class="fas fa-trash"></i></button>
      </td>
    `;

    tableBody.appendChild(row);

    row.querySelector(".remove-btn").addEventListener("click", () => removeSelectedFile(rasterId));
    row.querySelector(".move-up-btn").addEventListener("click", () => moveRow(row, "up"));
    row.querySelector(".move-down-btn").addEventListener("click", () => moveRow(row, "down"));

    const combineSelect = row.querySelector(`#combine_${sanitized}`);
    combineSelect.addEventListener("change", updateCombineOptions);

    updateCombineOptions();
  }

  function removeSelectedFile(rasterId) {
    const row = document.querySelector(`tr[data-raster-id="${rasterId}"]`);
    if (!row) return;

    const originalFilePath = row.getAttribute("data-original-filepath");
    row.remove();

    const checkbox = document.querySelector(`input[type="checkbox"][value="${originalFilePath}"]`);
    if (checkbox) checkbox.checked = false;

    updateCombineOptions();
  }

  function moveRow(row, direction) {
    const tableBody = row.parentNode;
    if (direction === "up") {
      const prevRow = row.previousElementSibling;
      if (prevRow) tableBody.insertBefore(row, prevRow);
    } else if (direction === "down") {
      const nextRow = row.nextElementSibling;
      if (nextRow) tableBody.insertBefore(nextRow.nextElementSibling, row);
    }
    updateCombineOptions();
  }

  function updateCombineOptions() {
    const tableBody = document.getElementById("rasterTableBody");
    const rows = Array.from(tableBody.querySelectorAll("tr"));
    let currentGroup = 1;
    groupRows = {};

    rows.forEach((row, index) => {
      const combineSelect = row.querySelector('select[name*="[combine]"]');

      if (index === 0) {
        combineSelect.value = "No";
        combineSelect.disabled = true;
        row.setAttribute("data-group", currentGroup);
      } else {
        combineSelect.disabled = false;
        const prevRow = rows[index - 1];
        const prevGroup = parseInt(prevRow.getAttribute("data-group"), 10);

        if (combineSelect.value === "Yes") row.setAttribute("data-group", prevGroup);
        else {
          currentGroup++;
          row.setAttribute("data-group", currentGroup);
        }
      }

      const groupNumber = parseInt(row.getAttribute("data-group"), 10);
      groupRows[groupNumber] = groupRows[groupNumber] || [];
      groupRows[groupNumber].push(row);

      const groupCell = row.querySelector(".group-cell");
      if (groupCell) {
        if (index === 0 || row.getAttribute("data-group") !== rows[index - 1].getAttribute("data-group")) {
          groupCell.innerHTML = `<button type="button" class="btn btn-link collapse-btn" data-group="${groupNumber}">[-]</button> Group ${groupNumber}`;
          groupCell.querySelector(".collapse-btn").addEventListener("click", toggleGroup);
        } else {
          groupCell.innerHTML = "";
        }
      }
    });
  }

  function toggleGroup(event) {
    event.stopPropagation();
    const groupNumber = event.target.getAttribute("data-group");
    const isCollapsed = event.target.textContent === "[+]";
    event.target.textContent = isCollapsed ? "[-]" : "[+]";

    groupRows[groupNumber].forEach((row) => {
      if (row !== event.target.closest("tr")) {
        row.style.display = isCollapsed ? "" : "none";
      }
    });
  }

  function removeSelectedFileByFilePath(filePath) {
    const row = document.querySelector(`tr[data-original-filepath="${filePath}"]`);
    if (row) {
      row.remove();
      updateCombineOptions();
    }
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

    const selectedRows = document.querySelectorAll("#rasterTableBody tr");
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
      const key = sanitizeFilePath(originalFilePath);

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
    fetch("/api/processLandSuitability", {
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

  // Load tree
  if (fileListElement) {
    fetchDirectoryContents("/")
      .then((contents) => displayDirectoryContents(contents, fileListElement, ""))
      .catch((e) => console.error("Error displaying root directory contents:", e));
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
