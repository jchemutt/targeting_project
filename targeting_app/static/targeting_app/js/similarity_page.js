// Land Similarity page logic (extracted from inline <script>)
document.addEventListener('DOMContentLoaded', () => {

            // Server-provided config (API endpoints) — read from the
            // #js-config json_script block rendered by the template.
            const CONFIG = (function () {
                const el = document.getElementById("js-config");
                try { return el ? JSON.parse(el.textContent) : {}; }
                catch (e) { console.error("Invalid #js-config JSON:", e); return {}; }
            })();
            const API = CONFIG.apiEndpoints || {};

            const aoiOptionMap = document.getElementById('aoiOptionMap');
            const aoiOptionFile = document.getElementById('aoiOptionFile');
            const mapSection = document.getElementById('mapSection');
            const fileUploadSection = document.getElementById('fileUploadSection');
            const aoiFileUpload = document.getElementById('aoiFileUpload');
            const pointsInput = document.getElementById('pointsInput');
            const uploadedPointsInput = document.getElementById('uploadedPointsInput');
            const fileError = document.getElementById('fileError');

               
              
            const fileListElement = document.getElementById('fileList');
            const selectedFilesForm = document.getElementById('selectedFilesForm');
            const selectedFilesContainer = document.getElementById('selectedFilesContainer');
            let map;
            let popupMap; 
            let resultUrl = "";
            let messResultUrl = "";
            let mnobisResultUrl = "";
            let drawnItems = new L.FeatureGroup();

            // Leaflet map setup
            function setupMap() {
                map = L.map('map-container').setView([0, 0], 1); // Default view, will be updated dynamically

                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                }).addTo(map);

                map.addLayer(drawnItems); // Add drawn items layer to the map

                // Initialize Leaflet Draw
                const drawOptions = {
                    position: 'topleft',
                    draw: {
                        marker: true,
                        polygon: false,
                        polyline: false,
                        circle: false,
                        rectangle: false,
                        circlemarker: false
                    },
                    edit: {
                        featureGroup: drawnItems,
                        remove: true // Allow removing drawn items
                    }
                };
                const drawControl = new L.Control.Draw(drawOptions);
                map.addControl(drawControl);

                // Event listener for marker drawn
                map.on(L.Draw.Event.CREATED, function (event) {
                    const layer = event.layer;
                    drawnItems.addLayer(layer); // Add new drawn layer
                    updatePointsInput();
                });

                // Event listener for marker deleted
                map.on(L.Draw.Event.DELETED, function () {
                    updatePointsInput();
                });
            }

            function updatePointsInput() {
                const points = [];
                drawnItems.eachLayer(function (layer) {
                    if (layer instanceof L.Marker) {
                        const latLng = layer.getLatLng();
                        points.push([latLng.lng, latLng.lat]); // Capture coordinates in [longitude, latitude] format
                    }
                });
                pointsInput.value = JSON.stringify(points);
            }

            // Event listeners for radio buttons
    aoiOptionMap.addEventListener('change', () => {
        if (aoiOptionMap.checked) {
            mapSection.style.display = 'block'; // Show the map section
            fileUploadSection.style.display = 'none'; // Hide the file upload section
            aoiFileUpload.value = ''; // Clear file input
            uploadedPointsInput.value = ""; // Clear uploaded points
            fileError.textContent = ''; // Clear any error message
        }
    });

    aoiOptionFile.addEventListener('change', () => {
        if (aoiOptionFile.checked) {
            mapSection.style.display = 'none'; // Hide the map section
            fileUploadSection.style.display = 'block'; // Show the file upload section
            drawnItems.clearLayers(); // Clear map markers
            pointsInput.value = ""; // Clear map points
        }
    });

    // File processing
    aoiFileUpload.addEventListener('change', () => {
        const file = aoiFileUpload.files[0];
        if (!file) {
            fileError.textContent = 'Please select a file to upload.';
            return;
        }

        const reader = new FileReader();
        const fileExtension = file.name.split('.').pop().toLowerCase();

        if (fileExtension === 'geojson') {
            reader.onload = (e) => processGeoJSON(e.target.result);
        } else if (fileExtension === 'csv') {
            reader.onload = (e) => processCSV(e.target.result);
        } else {
            fileError.textContent = 'Invalid file format. Please upload a GeoJSON or CSV file.';
            return;
        }

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

            if (points.length === 0) {
                fileError.textContent = 'No valid points found in the GeoJSON file.';
                return;
            }

            uploadedPointsInput.value = JSON.stringify(points);
            fileError.textContent = '';
           
        } catch (error) {
            fileError.textContent = 'Error processing GeoJSON file.';
            console.error(error);
        }
    }

    function processCSV(content) {
        try {
            const rows = content.split('\n');
            const header = rows[0].split(',');
            const latIndex = header.findIndex((col) => col.trim().toLowerCase() === 'latitude');
            const lngIndex = header.findIndex((col) => col.trim().toLowerCase() === 'longitude');

            if (latIndex === -1 || lngIndex === -1) {
                fileError.textContent = 'CSV file must have "Latitude" and "Longitude" columns.';
                return;
            }

            const points = [];
            rows.slice(1).forEach((row) => {
                const columns = row.split(',');
                const lat = parseFloat(columns[latIndex]);
                const lng = parseFloat(columns[lngIndex]);

                if (!isNaN(lat) && !isNaN(lng)) {
                    points.push([lng, lat]);
                }
            });

            if (points.length === 0) {
                fileError.textContent = 'No valid points found in the CSV file.';
                return;
            }

            uploadedPointsInput.value = JSON.stringify(points);
            fileError.textContent = '';
           
        } catch (error) {
            fileError.textContent = 'Error processing CSV file.';
            console.error(error);
        }
    }



            // Function to add selected file to the selected files container
            function addSelectedFile(filePath, fileName) {
                const selectedFileItem = document.createElement('div');
                selectedFileItem.classList.add('mb-2', 'selected-file-item', 'd-flex', 'align-items-center');
                selectedFileItem.innerHTML = `
                    <span class="flex-grow-1">${fileName}</span>
                    <button type="button" class="btn btn-sm btn-danger ml-2 btn-delete">
                        <i class="fas fa-trash"></i>
                    </button>
                    <input type="hidden" name="selectedFiles[]" value="${filePath}">
                `;
                const deleteButton = selectedFileItem.querySelector('.btn-delete');
                deleteButton.addEventListener('click', () => {
                    selectedFileItem.remove();
                    const correspondingCheckbox = fileListElement.querySelector(`input[type="checkbox"][value="${filePath}"]`);
                    if (correspondingCheckbox) {
                        correspondingCheckbox.checked = false;
                    }
                });
                selectedFilesContainer.appendChild(selectedFileItem);
            }

            // Function to remove selected file from the selected files container
            function removeSelectedFile(filePath) {
                const selectedFileItems = selectedFilesContainer.querySelectorAll('.selected-file-item');
                selectedFileItems.forEach(item => {
                    const input = item.querySelector(`input[type="hidden"][value="${filePath}"]`);
                    if (input) {
                        item.remove();
                    }
                });
            }

            // Validate form before submission
            function validateForm(event) {
                event.preventDefault(); // Prevent the form from submitting
                const descriptionInput = document.getElementById('description');
                const descriptionValue = descriptionInput.value.trim();

                    if (!descriptionValue) {
                        alert('Description is required.');
                        descriptionInput.focus();
                        return false;
                    }

                const selectedFiles = selectedFilesContainer.querySelectorAll('.selected-file-item');
                if (selectedFiles.length < 2) {
                    alert('Please select at least two files.');
                    return false;
                }

                const aoiOptionMap = document.getElementById('aoiOptionMap').checked; // Check if "Select on the Map" is selected
                const aoiOptionFile = document.getElementById('aoiOptionFile').checked; // Check if "Upload File" is selected

                let points = [];

                if (aoiOptionMap) {
                points = JSON.parse(pointsInput.value || "[]");
                if (points.length === 0) {
                    alert("Please select at least one point on the map.");
                    return false;
                }
                // ensure uploaded is not accidentally used
                uploadedPointsInput.value = "";
                } else {
                points = JSON.parse(uploadedPointsInput.value || "[]");
                if (points.length === 0) {
                    alert("Please upload points data either in Geojson or csv format.");
                    return false;
                }
               
                pointsInput.value = JSON.stringify(points);
                }

                const formData = {
                    selectedFiles: Array.from(selectedFiles).map(item => item.querySelector(`input[type="hidden"]`).value),
                    points: pointsInput.value, 
                    description: descriptionValue,
                };

                //console.error('FormData:', formData);

                // Show progress modal
                $('#progressModal').modal('show');

                

                fetch(API.processLandSimilarity, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
                    },
                    body: JSON.stringify(formData)
                })
                .then(response => response.json())
                .then(data => {
                    $('#progressModal').modal('hide');
                    if (data.status === 'success') {
                        resultUrl = data.result_url;
                        console.error('resultUrl:', resultUrl);
                        messResultUrl = resultUrl.mess;
                         mnobisResultUrl = resultUrl.mnobis;
                        $('#downloadMessLink').attr('href', resultUrl.mess);
                        $('#downloadMnobisLink').attr('href', resultUrl.mnobis);
                        $('#downloadLink2').attr('href', resultUrl.mnobis);
                        $('#resultSection').show();
                        // Scroll the page to the result section
                      $('html, body').animate({
                    scrollTop: $('#resultSection').offset().top
                }, 500);
                
                    } else {


                        alert(`Error: ${data.message}`);
                    }
                })
                .catch(error => {
                    $('#progressModal').modal('hide');
                    console.error('Error:', error);
                    alert('An error occurred while processing the form.');
                });

                return false;
            }

            document.getElementById('submitBtn').addEventListener('click', validateForm);

            // Load the raster directory tree via the shared module.
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
                onFileSelect: (filePath, item) => addSelectedFile(filePath, item.name),
                onFileDeselect: (filePath) => removeSelectedFile(filePath),
            });

            // Initialize Leaflet map
            setupMap();
          

          


            document.getElementById('viewMnobisMapBtn').addEventListener('click', function () {
                $('#resultModal').modal('show');
                displayResultOnMap(mnobisResultUrl);
            });

            

function displayResultOnMap(url) {
    if (popupMap) {
        popupMap.remove();
    }

    $('#resultModal').on('shown.bs.modal', async function () {
        popupMap = L.map('popup-map').setView([0, 0], 5);

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
        }).addTo(popupMap);

        try {
            const response = await fetch(url);
            const arrayBuffer = await response.arrayBuffer();
            const georaster = await parseGeoraster(arrayBuffer);

            const nodataValue = georaster.nodataValue || null;
            const minValue = georaster.mins[0];
            const maxValue = georaster.maxs[0];

            if (minValue === undefined || maxValue === undefined || minValue === maxValue) {
                alert('Error: Raster contains no valid data or uniform values.');
                return;
            }

            // Detect if classified into quantiles
            const isQuantileRaster = minValue >= 1 && maxValue <= 5 && Number.isInteger(minValue) && Number.isInteger(maxValue);

            let pixelValuesToColorFn;
            let legend;

            if (isQuantileRaster) {
                // Discrete classified legend (e.g., Mahalanobis Dist quantiles)
                const quantileLabels = {
                    1: 'Very High Similarity',
                    2: 'High Similarity',
                    3: 'Moderate Similarity',
                    4: 'Low Similarity',
                    5: 'Very Low Similarity'
                };

                const classColors = {
                    1: '#267300',
                    2: '#98E600',
                    3: '#E9FFBE',
                    4: '#FFD37F',
                    5: '#A87000'
                };

                pixelValuesToColorFn = values => {
                    const val = values[0];
                    return classColors[val] || null;
                };

                legend = L.control({ position: 'bottomright' });
                legend.onAdd = function () {
                    const div = L.DomUtil.create('div', 'info legend');
                    div.innerHTML += '<strong>Mahalanobis Similarity</strong><br>';
                    Object.keys(quantileLabels).forEach(key => {
                        div.innerHTML += `<i style="background:${classColors[key]};"></i> ${quantileLabels[key]}<br>`;
                    });
                    return div;
                };

            } else {
                // Continuous legend for unclassified raster
                const colormap = chroma
                    .scale(['#440154', '#31688e', '#35b779', '#fde725'])
                    .domain([minValue, maxValue]);

                pixelValuesToColorFn = values => {
                    const value = values[0];
                    if (value === nodataValue || isNaN(value)) return null;
                    return colormap(value).hex();
                };

                legend = L.control({ position: 'bottomright' });
                legend.onAdd = function () {
                    const div = L.DomUtil.create('div', 'info legend');
                    const gradient = colormap.colors(100).join(',');
                    div.innerHTML = `
                        <div><strong>Pixel Value</strong></div>
                        <div style="width: 200px; height: 15px; background: linear-gradient(to right, ${gradient});"></div>
                        <div style="display: flex; justify-content: space-between;">
                            <span>${minValue.toFixed(2)}</span>
                            <span>${maxValue.toFixed(2)}</span>
                        </div>
                    `;
                    return div;
                };
            }

            const layer = new GeoRasterLayer({
                georaster,
                opacity: 0.7,
                pixelValuesToColorFn,
                resolution: 256
            });

            layer.addTo(popupMap);
            popupMap.fitBounds(layer.getBounds());

            if (legend) {
                legend.addTo(popupMap);
            }

        } catch (error) {
            console.error('Error loading raster:', error);
            alert('An error occurred while loading the raster.');
        }
    });



}








        });
