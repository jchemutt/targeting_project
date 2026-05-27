// Land Statistics page logic (extracted from inline <script>)
document.addEventListener('DOMContentLoaded', function () {

    // Server-provided config (API endpoints) — read from the
    // #js-config json_script block rendered by the template.
    const CONFIG = (function () {
        const el = document.getElementById("js-config");
        try { return el ? JSON.parse(el.textContent) : {}; }
        catch (e) { console.error("Invalid #js-config JSON:", e); return {}; }
    })();
    const API = CONFIG.apiEndpoints || {};

    document.getElementById('allStatsCheckbox').addEventListener('change', function () {
        const checkboxes = document.querySelectorAll('.stat-checkbox:not(#allStatsCheckbox)');
        checkboxes.forEach(cb => cb.checked = this.checked);
        updateStatDropdownLabel();
    });

    document.querySelectorAll('.stat-checkbox:not(#allStatsCheckbox)').forEach(cb => {
        cb.addEventListener('change', function () {
            const allStatsCheckbox = document.getElementById('allStatsCheckbox');
            const otherCheckboxes = document.querySelectorAll('.stat-checkbox:not(#allStatsCheckbox)');
            const allChecked = Array.from(otherCheckboxes).every(checkbox => checkbox.checked);
            allStatsCheckbox.checked = allChecked;
            updateStatDropdownLabel();
        });
    });

    function updateStatDropdownLabel() {
        const selectedStats = getSelectedStatistics();
        const btn = document.getElementById('statDropdown');

        if (selectedStats.length === 0) {
            btn.textContent = 'Statistic Types';
        } else if (selectedStats.length <= 2) {
            btn.textContent = selectedStats.join(', ');
        } else {
            btn.textContent = `${selectedStats.length} selected`;
        }
    }

    function getSelectedStatistics() {
        const checkboxes = document.querySelectorAll('.stat-checkbox:checked:not(#allStatsCheckbox)');
        return Array.from(checkboxes).map(cb => cb.value);
    }

    let map = L.map('map-container').setView([0, 0], 5);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    let currentRasterLayer = null;
    let selectedDescription = null;
    let selectedRasterFile = null;
    let selectedCountry = "";
    let selectedReferenceFile = "";

    const referenceSelect = document.querySelector("#zoneIdColumn");
    const processBtn = document.getElementById("processBtn");

    async function fetchUserFiles() {
        try {
            const response = await fetch(API.userFiles);
            if (!response.ok) {
                throw new Error('Failed to fetch user files.');
            }
            const files = await response.json();
            files.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
            return files;
        } catch (error) {
            console.error('Error fetching user files:', error);
            alert('Could not load user files. Please try again later.');
            return [];
        }
    }

    function visualizeRasterFile(filePath, description) {
        if (currentRasterLayer) {
            map.removeLayer(currentRasterLayer);
        }

        fetch(filePath)
            .then(response => response.arrayBuffer())
            .then(arrayBuffer => parseGeoraster(arrayBuffer))
            .then(georaster => {
                const pixelValuesToColorFn = getPixelValuesToColorFn(description, georaster);

                currentRasterLayer = new GeoRasterLayer({
                    georaster,
                    opacity: 0.7,
                    pixelValuesToColorFn: pixelValuesToColorFn || undefined,
                    resolution: 256
                });

                currentRasterLayer.addTo(map);
                map.fitBounds(currentRasterLayer.getBounds());
            })
            .catch(error => {
                console.error('Error loading raster file:', error);
                alert('Could not load the selected raster file. Please try again.');
            });
    }

    function getPixelValuesToColorFn(description, georaster) {
        if (description === 'Land suitability raster file') {
            return values => {
                const value = values[0];
                switch (value) {
                    case 1: return '#A87000';
                    case 2: return '#FFD37F';
                    case 3: return '#E9FFBE';
                    case 4: return '#98E600';
                    case 5: return '#267300';
                    default: return '#00000000';
                }
            };
        } else if (description === 'Mahalanobis Distance raster file') {
            const colormapCache = {};
            const colormap = chroma
                .scale(['#440154', '#31688e', '#35b779', '#fde725'])
                .domain([georaster.mins[0], georaster.maxs[0]]);

            return values => {
                const value = values[0];
                if (value === georaster.nodataValue || isNaN(value)) return null;
                if (!colormapCache[value]) {
                    colormapCache[value] = colormap(value).hex();
                }
                return colormapCache[value];
            };
        } else {
            return null;
        }
    }

    async function displayUserFiles() {
        const files = await fetchUserFiles();
        const tableBody = document.getElementById('userFilesTableBody');
        tableBody.innerHTML = '';

        files.forEach(file => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${file.title}</td>
                <td>${new Date(file.created_at).toLocaleString()}</td>
                <td>
                    <input type="radio" name="fileSelection" class="file-radio"
                           value="${file.file_path}"
                           data-description="${file.description}"
                           data-country="${file.country}" />
                </td>
            `;
            tableBody.appendChild(row);
        });

        const radioButtons = document.querySelectorAll('.file-radio');
        radioButtons.forEach(button => {
            button.addEventListener('change', async function () {
                selectedRasterFile = this.value;
                selectedDescription = this.dataset.description;
                selectedCountry = this.dataset.country;
                enableProcessButton();
                visualizeRasterFile(selectedRasterFile, selectedDescription);
                await fetchReferenceLayers(selectedCountry);
            });
        });
    }

    async function fetchReferenceLayers(rasterFile) {
        try {
            const referencelayer = rasterFile.replace(/\\/g, "/");
            const url = `${API.referenceLayers}?raster_path=${encodeURIComponent(referencelayer)}`;
            const response = await fetch(url);
            const data = await response.json();
            const referenceLayers = data.raster_files;

            referenceSelect.innerHTML = '<option value="">Select Reference Layer</option>';

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
    }

    function enableProcessButton() {
        processBtn.disabled = !(selectedReferenceFile && selectedRasterFile);
    }

    processBtn.addEventListener('click', async function () {
        if (!selectedReferenceFile || !selectedRasterFile) {
            alert('Please select reference file and select a raster file.');
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
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({
                    reference_layer: selectedReferenceFile,
                    raster_path: selectedRasterFile,
                    description: selectedDescription,
                    stat_types: selectedStats
                }),
            });

            const data = await response.json();
            if (data.status === 'success') {
                displayResults(data.results);
                $('html, body').animate({
                    scrollTop: $('#resultsSection').offset().top
                }, 500);
            } else {
                alert(`Error: ${data.message}`);
            }
        } catch (error) {
            console.error('Error processing zonal statistics:', error);
        } finally {
            setTimeout(() => {
                $('#processingModal').modal('hide');
            }, 500);
        }
    });

    let statChartInstance = null;

    function displayResults(statistics) {
        const resultsSection = document.getElementById('resultsSection');
        const resultsTable = document.getElementById('resultsTable');
        const downloadCsvBtn = document.getElementById('downloadCsvBtn');
        const plotChartBtn = document.getElementById('plotChartBtn');

        if (!statistics || statistics.length === 0) {
            resultsTable.innerHTML = '<p>No data available.</p>';
            return;
        }

        const statLabel = statistics[0].stat_label;
        const statsPerClass = statistics[0].statistics;
        const classes = Object.keys(statLabel);
        const selectedStats = getSelectedStatistics();

        let headerRow = `
            <th>Class</th>
            <th>Land Suitability Area (%)</th>
        `;

        selectedStats.forEach(stat => {
            headerRow += `<th>${stat.charAt(0).toUpperCase() + stat.slice(1)}</th>`;
        });

        let bodyRows = '';
        classes.forEach(cls => {
            let row = `<td>${cls}</td><td>${statLabel[cls]}</td>`;
            selectedStats.forEach(stat => {
                const value = statsPerClass[cls]?.[stat];
                row += `<td>${value !== null && value !== undefined ? value.toFixed(2) : 'N/A'}</td>`;
            });
            bodyRows += `<tr>${row}</tr>`;
        });

        resultsTable.innerHTML = `
            <h3>Land Statistics</h3>
            <table class="table table-bordered">
                <thead><tr>${headerRow}</tr></thead>
                <tbody>${bodyRows}</tbody>
            </table>
        `;

        resultsSection.style.display = 'block';
        downloadCsvBtn.style.display = 'inline-block';
        plotChartBtn.style.display = 'inline-block';
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

        if (statChartInstance) {
            statChartInstance.destroy();
        }

        statChartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: classes,
                datasets: [{
                    label: selectedStat.toUpperCase(),
                    data: values
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: { beginAtZero: true }
                }
            }
        });
    }

    function downloadCsv(classes, statLabel, statsPerClass, selectedStats) {
        let csvContent = "Class,Land Suitability Area (%)";
        selectedStats.forEach(stat => {
            csvContent += `,${stat}`;
        });
        csvContent += "\n";

        classes.forEach(cls => {
            let row = `${cls},${statLabel[cls]}`;
            selectedStats.forEach(stat => {
                const value = statsPerClass[cls]?.[stat];
                row += `,${value !== null && value !== undefined ? value : ''}`;
            });
            csvContent += row + "\n";
        });

        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.setAttribute('href', url);
        link.setAttribute('download', 'land_statistics.csv');
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    displayUserFiles();
});
