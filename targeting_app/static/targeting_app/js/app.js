document.querySelectorAll('.folder-icon').forEach((icon) => {
    icon.addEventListener('click', (event) => {
        const folder = event.target.closest('.folder');
        folder.classList.toggle('expanded');
    });
});

        // Define the validateOptiFrom function
        function validateOptiFrom(optiFromInput) {
            // Get the parent row of the `opti_from` input field
            const row = optiFromInput.closest('tr');
        
            // Find the `min_val` input field within the same row
            const minValInput = row.querySelector('input[name*="[min_val]"]');
        
            // Parse the values of `min_val` and `opti_from`
            const minVal = parseFloat(minValInput.value);
            const optiFrom = parseFloat(optiFromInput.value);
        
            // Validate that `opti_from` is greater than `min_val`
            if (isNaN(optiFrom) || optiFrom <= minVal) {
                alert(`Optimum From must be greater than Minimum Value (${minVal}).`);
                optiFromInput.value = ''; // Clear the invalid input
                optiFromInput.focus(); // Focus on the input field
            }
        }

        function validateMinVal(minValInput, initialMinVal) {
            // Parse the value entered in the min_val input field
            const minVal = parseFloat(minValInput.value);
        
            // Validate that min_val is not less than the initial minimum value
            if (isNaN(minVal) || minVal < initialMinVal) {
                alert(`Minimum Value must not be less than the initial value (${initialMinVal}).`);
                minValInput.value = initialMinVal; // Reset to the initial value
                minValInput.focus(); // Focus on the input field
            }
        }

    // Define the validateOptiTo function
    function validateOptiTo(optiToInput) {
        // Dynamically find the corresponding max_val input field
        const row = optiToInput.closest('tr'); 
        const maxValInput = row.querySelector('input[name*="[max_val]"]');
    
        // Parse the values
        const optiTo = parseFloat(optiToInput.value);
        const maxVal = parseFloat(maxValInput.value);
    
        // Validate that opti_to is less than max_val
        if (isNaN(optiTo) || optiTo >= maxVal) {
            alert(`Optimal To value must be less than Maximum Value (${maxVal}).`);
            optiToInput.value = ''; 
            optiToInput.focus(); 
        }
       }

       function validateMaxVal(maxValInput, defaultMaxVal) {
        // Parse the max_val entered by the user
        const maxVal = parseFloat(maxValInput.value);
    
        // Validate that max_val is not greater than the defaultMaxVal
        if (isNaN(maxVal) || maxVal > defaultMaxVal) {
            alert(`Maximum Value must not be greater than ${defaultMaxVal}.`);
            maxValInput.value = defaultMaxVal; 
            maxValInput.focus(); // Focus on the input field
        }
    }
    

    $(document).ready(function() {
        tippy('.tooltip-trigger', {
    content: (reference) => reference.getAttribute('data-tooltip'),
    placement: 'top',
  });
    const mapSection = document.getElementById('mapSection');
    const fileUploadSection = document.getElementById('fileUploadSection');
    const aoiOptionMap = document.getElementById('aoiOptionMap');
    const aoiOptionFile = document.getElementById('aoiOptionFile');

  

    // Event listener for "Choose from Map" radio button
    aoiOptionMap.addEventListener('change', () => {
        if (aoiOptionMap.checked) {
            mapSection.style.display = 'block'; // Show the map section
            fileUploadSection.style.display = 'none'; // Hide the file upload section
        }
    });

    // Event listener for "Upload File" radio button
    aoiOptionFile.addEventListener('change', () => {
        if (aoiOptionFile.checked) {
            mapSection.style.display = 'none'; // Hide the map section
            fileUploadSection.style.display = 'block'; // Show the file upload section
            }
           });

           document.getElementById('aoiFileUpload').addEventListener('change', (event) => {
    const file = event.target.files[0];
    const reader = new FileReader();

    reader.onload = function (e) {
        const fileContent = e.target.result; // Read file content
        aoiInput.value = fileContent; // Store in hidden input
    };

    if (file) {
        reader.readAsText(file); // Read the uploaded file
    }
});
            const fileListElement = document.getElementById('fileList');
            const selectedFilesForm = document.getElementById('selectedFilesForm');
            const selectedFilesContainer = document.getElementById('selectedFilesContainer');
            const aoiInput = document.getElementById('aoiInput');
            //const spinner = document.querySelector('.spinner-border');
            let map;
            let resultUrl = "";
            let popupMap;
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
                        rectangle: true,
                        polygon: true,
                        polyline: false,
                        circle: false,
                        marker: false,
                        circlemarker: false
                    },
                    edit: {
                        featureGroup: drawnItems,
                        remove: true // Allow removing drawn items
                    }
                };
                const drawControl = new L.Control.Draw(drawOptions);
                map.addControl(drawControl);

                // Event listener for rectangle drawn
                map.on(L.Draw.Event.CREATED, function (event) {
                    drawnItems.clearLayers(); // Clear previous drawn layers
                    const layer = event.layer;
                    drawnItems.addLayer(layer); // Add new drawn layer

                    if (event.layerType === 'rectangle') {
                        // If it's a rectangle, use bounds
                        const bounds = layer.getBounds();
                        const aoistr = `${bounds.getSouth()},${bounds.getWest()},${bounds.getNorth()},${bounds.getEast()}`;
                        aoiInput.value = aoistr; // Store AOI bounds
                    } else if (event.layerType === 'polygon') {
                        // If it's a polygon, capture the vertices
                        const latLngs = layer.getLatLngs()[0]; // Assuming a simple polygon (no holes)
                        const polygonCoords = latLngs.map(latlng => `${latlng.lat},${latlng.lng}`).join(';');
                        aoiInput.value = polygonCoords; // Store the polygon vertices as a string
                    }

                    layer.addTo(map);
                });

                 // Event listener for deleted shapes
                    map.on('draw:deleted', function () {
                        drawnItems.clearLayers(); // Clear all drawn layers
                        aoiInput.value = ''; // Clear the AOI input field
                    });
            }

            function clearDrawnLayers() {
                drawnItems.clearLayers();
                aoiInput.value = '';
            }

            // Function to fetch directory contents from Django view
            async function fetchDirectoryContents(directoryPath) {
                //showLoading();
                try {
                    const response = await fetch(`/api/getDirectoryContents?path=${encodeURIComponent(directoryPath)}`);
                    if (!response.ok) {
                        throw new Error('Failed to fetch directory contents');
                    }
                    return await response.json();
                } catch (error) {
                    console.error('Error fetching directory contents:', error);
                    alert('Failed to load directory contents. Please try again later.');
                    return [];
                } finally {
                    //hideLoading();
                }
            }

            // Function to fetch folder configurations from Django view
            async function fetchFolderConfigurations(folderName) {
                try {
                    const response = await fetch(`/api/getFolderConfigurations?folder=${encodeURIComponent(folderName)}`);
                    if (!response.ok) {
                        throw new Error('Failed to fetch folder configurations');
                    }
                    return await response.json();
                } catch (error) {
                    console.error('Error fetching folder configurations:', error);
                    alert('Failed to load folder configurations. Please try again later.');
                    return { center: [0, 0], zoom: 2 }; // Default to world view
                }
            }

            // Function to update map view based on folder configurations
            async function updateMapView(folderName) {
                const { center, zoom } = await fetchFolderConfigurations(folderName);
                map.setView(center, zoom);
            }

            // Function to sanitize file path for valid CSS selector
            function sanitizeFilePath(filePath) {
    // Replace all non-alphanumeric characters with underscores to make it a valid CSS selector
    return filePath.replace(/[^a-zA-Z0-9]/g, '_');
}

            // Function to display directory contents
            async function displayDirectoryContents(directoryContents, parentElement, directoryPath) {
                const ul = document.createElement('ul');
                ul.classList.add('list-group');

                directoryContents.forEach(item => {
                    const li = document.createElement('li');
                    li.classList.add('list-group-item');

                    if (item.type === 'directory') {
                        li.classList.add('folder');
                        li.innerHTML = `<i class="fas fa-caret-right folder-icon mr-2"></i><i class="fas fa-folder mr-2"></i>${item.name}`;
                        li.style.cursor = 'pointer';
                        li.addEventListener('click', async (event) => {
                            event.stopPropagation();
                            if (!li.dataset.loaded) {
                                const subDirectoryContents = await fetchDirectoryContents(`${directoryPath}/${item.name}`);
                                displayDirectoryContents(subDirectoryContents, li.querySelector('.folder-content'), `${directoryPath}/${item.name}`);
                                li.dataset.loaded = true;
                            }
                            li.classList.toggle('expanded');
                            const folderContent = li.querySelector('.folder-content');
                            folderContent.style.display = folderContent.style.display === 'block' ? 'none' : 'block';
                            const folderIcon = li.querySelector('.folder-icon');
                            folderIcon.classList.toggle('fa-caret-right');
                            folderIcon.classList.toggle('fa-caret-down');
                            // Update map view when folder is clicked
                            updateMapView(item.name);
                        });
                        const folderContent = document.createElement('div');
                        folderContent.classList.add('folder-content');
                        li.appendChild(folderContent);
                    } else if (item.type === 'file') {
    li.classList.add('file');
    li.style.height = '30px';  // Set a fixed height for the list item
    
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = `${directoryPath}/${item.name}`;
    checkbox.classList.add('mr-2');  // Add a custom class for styling
    
    checkbox.addEventListener('click', (event) => {
        event.stopPropagation(); // Prevent propagation to parent li
    });
    
    checkbox.addEventListener('change', () => {
    if (checkbox.checked) {
        addSelectedFile(checkbox.value, item.name, item.min_val, item.max_val);
    }  else {
        removeSelectedFileByFilePath(checkbox.value);
    }
});

    const label = document.createElement('label');
    label.classList.add('file-name');  // Add this class for styling
    label.textContent = item.name;
    label.style.lineHeight = '30px';  // Ensure label text is vertically aligned with the checkbox

    // Add the title attribute to show full file name on hover
    label.setAttribute('title', item.name);

    li.appendChild(checkbox);
    li.appendChild(label);
}

ul.appendChild(li);
                });

                parentElement.appendChild(ul);
            }



// Global raster counter for unique raster IDs
let rasterCounter = 0;

// Object to store rows grouped by group number
let groupRows = {};


// Function to add a selected file to the table
function addSelectedFile(filePath, fileName, minVal, maxVal) {
    rasterCounter++;
    const rasterId = rasterCounter; // Unique identifier for each raster

    const sanitizedFilePath = sanitizeFilePath(filePath);

    const tableBody = document.getElementById('rasterTableBody');
    const isFirstRaster = tableBody.rows.length === 0;

    const row = document.createElement('tr');
    row.setAttribute('data-raster-id', rasterId);
    row.setAttribute('data-original-filepath', filePath);
    console.log(`Adding raster: ${filePath}, rasterId: ${rasterId}`);
    row.innerHTML = `
        <td class="group-cell"></td>
        <td>${fileName}</td>
       <td><input type="text" class="form-control form-control-sm small-input" name="rasterParameters[${sanitizedFilePath}][min_val]" value="${minVal}" onchange="validateMinVal(this, ${minVal})"></td>
        <td><input type="text" class="form-control form-control-sm small-input" name="rasterParameters[${sanitizedFilePath}][opti_from]" placeholder="Optimal From" onchange="validateOptiFrom(this)"></td>
        <td><input type="text" class="form-control form-control-sm small-input" name="rasterParameters[${sanitizedFilePath}][opti_to]" placeholder="Optimal To" onchange="validateOptiTo(this)"></td>
        <td><input type="text" class="form-control form-control-sm small-input" name="rasterParameters[${sanitizedFilePath}][max_val]" value="${maxVal}" onchange="validateMaxVal(this, ${maxVal})"></td>
        <td>
            <select id="combine_${sanitizedFilePath}" name="rasterParameters[${sanitizedFilePath}][combine]" class="form-control form-control-sm">
                <option value="Yes">Yes</option>
                <option value="No">No</option>
            </select>
        </td>
        <td class="action-buttons">
            <button type="button" class="btn btn-secondary btn-sm move-up-btn"><i class="fas fa-arrow-up"></i></button>
            <button type="button" class="btn btn-secondary btn-sm move-down-btn"><i class="fas fa-arrow-down"></i></button>
            <button type="button" class="btn btn-danger btn-sm remove-btn"><i class="fas fa-trash"></i></button>
        </td>
    `;

    tableBody.appendChild(row);

    // Attach event listeners for the buttons after adding the row
    row.querySelector('.remove-btn').addEventListener('click', () => removeSelectedFile(rasterId));
    row.querySelector('.move-up-btn').addEventListener('click', () => moveRow(row, 'up'));
    row.querySelector('.move-down-btn').addEventListener('click', () => moveRow(row, 'down'));

    // Attach event listener for the combine select element
    const combineSelect = row.querySelector(`#combine_${sanitizedFilePath}`);
    combineSelect.addEventListener('change', updateCombineOptions);

    // Update combine options and visual grouping after adding a new raster
    updateCombineOptions();
}

// Function to remove a selected file from the table
function removeSelectedFile(rasterId) {
    // Find the table row using the raster ID
    const row = document.querySelector(`tr[data-raster-id="${rasterId}"]`);

    if (row) {
        // Get the original file path from the data attribute
        const originalFilePath = row.getAttribute('data-original-filepath');

        // Remove the row from the table
        row.remove();

        // Uncheck the corresponding checkbox using the original file path
        const checkbox = document.querySelector(`input[type="checkbox"][value="${originalFilePath}"]`);

        if (checkbox) {
            checkbox.checked = false;
        } else {
            console.warn(`Checkbox for file "${originalFilePath}" not found.`);
        }

        // Update combine options and visual grouping after removing a raster
        updateCombineOptions();
    } else {
        console.warn(`Row with data-raster-id="${rasterId}" not found.`);
    }
}

// Function to move a row up or down
function moveRow(row, direction) {
    const tableBody = row.parentNode;
    if (direction === 'up') {
        const prevRow = row.previousElementSibling;
        if (prevRow) {
            tableBody.insertBefore(row, prevRow);
        }
    } else if (direction === 'down') {
        const nextRow = row.nextElementSibling;
        if (nextRow) {
            tableBody.insertBefore(nextRow.nextElementSibling, row);
        }
    }
    // Update combine options and visual grouping after moving a raster
    updateCombineOptions();
}

// Function to update combine options and visual grouping
function updateCombineOptions() {
    const tableBody = document.getElementById('rasterTableBody');
    const rows = Array.from(tableBody.querySelectorAll('tr'));
    let currentGroup = 1;
    groupRows = {}; // Reset groupRows object

    rows.forEach((row, index) => {
        const rasterId = row.getAttribute('data-raster-id'); // Keep the original rasterId

        // Update the name attributes for inputs using the current index
        const inputs = row.querySelectorAll('input, select');
        inputs.forEach(input => {
            const name = input.getAttribute('name');
            if (name) {
                const newName = name.replace(/rasterParameters\[\d+\]/, `rasterParameters[${index}]`);
                input.setAttribute('name', newName);
            }
        });

        // Update combine options
        const combineSelect = row.querySelector('select[name*="[combine]"]');
        if (index === 0) {
            // First raster cannot be combined with previous
            combineSelect.value = 'No';
            combineSelect.disabled = true;
            row.setAttribute('data-group', currentGroup);
        } else {
            combineSelect.disabled = false;

            const prevRow = rows[index - 1];
            const prevGroup = parseInt(prevRow.getAttribute('data-group'));

            if (combineSelect.value === 'Yes') {
                // Same group as previous
                row.setAttribute('data-group', prevGroup);
            } else {
                // Start a new group
                currentGroup++;
                row.setAttribute('data-group', currentGroup);
            }
        }

        // Collect rows per group
        const groupNumber = parseInt(row.getAttribute('data-group'));
        if (!groupRows[groupNumber]) {
            groupRows[groupNumber] = [];
        }
        groupRows[groupNumber].push(row);

        // Update group cell
        const groupCell = row.querySelector('.group-cell');
        if (groupCell) {
            if (index === 0 || row.getAttribute('data-group') !== rows[index - 1].getAttribute('data-group')) {
                // This is the first row of a group
                groupCell.innerHTML = `
                    <button type="button" class="btn btn-link collapse-btn" data-group="${groupNumber}">[-]</button>
                    Group ${groupNumber}
                `;
                groupCell.querySelector('.collapse-btn').addEventListener('click', toggleGroup);
            } else {
                // Not the first row of the group; clear the group cell
                groupCell.innerHTML = '';
            }
        }

        // Remove existing group classes
        row.classList.remove('group-1', 'group-2', 'group-3', 'group-4', 'group-5');
        // Apply new group class (cycle through predefined styles)
        const groupClass = `group-${(groupNumber % 5) + 1}`; 
        row.classList.add(groupClass);
    });
}

// Function to toggle the collapse/expand of groups
function toggleGroup(event) {
    const groupNumber = event.target.getAttribute('data-group');
    const isCollapsed = event.target.textContent === '[+]';
    event.target.textContent = isCollapsed ? '[-]' : '[+]';

    groupRows[groupNumber].forEach(row => {
        if (row !== event.target.closest('tr')) {
            row.style.display = isCollapsed ? '' : 'none';
        }
    });
}


function removeSelectedFileByFilePath(filePath) {
    // Find the table row using the data-original-filepath
    const row = document.querySelector(`tr[data-original-filepath="${filePath}"]`);

    if (row) {
        // Remove the row from the table
        row.remove();

        // Update combine options and visual grouping after removing a raster
        updateCombineOptions();
    } else {
        console.warn(`Row with data-original-filepath="${filePath}" not found.`);
    }
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


const selectedRows = document.querySelectorAll('#rasterTableBody tr');
    if (selectedRows.length < 2) {
        alert('Please select at least two files.');
        return false;
    }

    const rasterParameters = {};
    let isValid = true;

    selectedRows.forEach(row => {
     // Get the original file path from the row's data attribute
        const originalFilePath = row.getAttribute('data-original-filepath');

        // Get the rasterId from the row's data attribute
        const rasterId = row.getAttribute('data-raster-id');

        console.log(`Validating raster: ${originalFilePath} with rasterId: ${rasterId}`);

        const minValInput = row.querySelector(`input[name="rasterParameters[${sanitizeFilePath(originalFilePath)}][min_val]"]`);
        const maxValInput = row.querySelector(`input[name="rasterParameters[${sanitizeFilePath(originalFilePath)}][max_val]"]`);
        const optiFromInput = row.querySelector(`input[name="rasterParameters[${sanitizeFilePath(originalFilePath)}][opti_from]"]`);
        const optiToInput = row.querySelector(`input[name="rasterParameters[${sanitizeFilePath(originalFilePath)}][opti_to]"]`);
        const combineInput = row.querySelector(`select[name="rasterParameters[${sanitizeFilePath(originalFilePath)}][combine]"]`);

       

        
        const minVal = minValInput.value;
        const maxVal = maxValInput.value;
        const optiFrom = optiFromInput.value;
        const optiTo = optiToInput.value;

        // Check if all inputs exist
            if (!minVal || !maxVal || !optiFrom || !optiTo ) {

            alert(`One or more inputs are missing in the row: ${rasterId}`);
            isValid = false;
            return false; 
            }

        if (!optiFrom || !optiTo || parseFloat(optiFrom) <= parseFloat(minVal) || parseFloat(optiTo) >= parseFloat(maxVal)) {
            alert('Opti From must be greater than Min Value and Opti To must be less than Max Value.');
            isValid = false;
            return false;
        }

     

        rasterParameters[originalFilePath] = {
            opti_from: optiFrom,
            opti_to: optiTo,
            min_val: minVal,
            max_val: maxVal,
            combine: combineInput.value
        };
    });

    if (!isValid) {
        return false;
    }

    

    const formData = {
        selectedFiles: Array.from(selectedRows).map(row => row.getAttribute('data-original-filepath')),
        rasterParameters: rasterParameters,
        aoi: aoiInput.value || '',
        description: descriptionValue,
    };


    $('#progressModal').modal('show').one('shown.bs.modal', function () {
        // Ensure the modal is fully displayed before proceeding
        console.log("Progress modal shown.");
        
        fetch('/api/processLandSuitability', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
            },
            body: JSON.stringify(formData)
        })
        .then(response => response.json())
        .then(data => {
            console.log("Response received:", data);
            
            setTimeout(() => {
                $('#progressModal').modal('hide'); // Ensure modal hides properly
    
                if (data.status === 'success') {
                    resultUrl = data.result_url;
                    $('#downloadLink').attr('href', resultUrl);
                    $('#downloadLink2').attr('href', resultUrl);
                    $('#resultSection').show();
    
                    // Scroll the page to the result section
                    $('html, body').animate({
                        scrollTop: $('#resultSection').offset().top
                    }, 500);
                } else {
                    alert(`Error: ${data.message}`);
                }
            }, 500); // Add small delay to ensure modal is properly hidden
        })
        .catch(error => {
            console.error("Error:", error);
            $('#progressModal').modal('hide');
            alert('An error occurred while processing the form.');
        });
    });
    
    return false;
}

            document.getElementById('submitBtn').addEventListener('click', validateForm);

            // Fetch and display the root directory contents on page load
            fetchDirectoryContents('/')
                .then(directoryContents => displayDirectoryContents(directoryContents, fileListElement, ''))
                .catch(error => console.error('Error displaying root directory contents:', error));

            // Initialize Leaflet map
            setupMap();

            // View Result Button
            $('#viewResultBtn').on('click', function () {
                $('#resultModal').modal('show');
                displayResultOnMap(resultUrl);
            });

            function displayResultOnMap(url) {

                if (popupMap) {
                    popupMap.remove();
            }

            const mapContainer = document.getElementById('popup-map');
            if (mapContainer) {
               mapContainer.innerHTML = ''; 
              }
                $('#resultModal').on('shown.bs.modal', function () {
                    popupMap = L.map('popup-map').setView([0, 0], 5);

                    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                }).addTo(popupMap);

                fetch(resultUrl)
                    .then(response => response.arrayBuffer())
                    .then(arrayBuffer => parseGeoraster(arrayBuffer))
                    .then(georaster => {
                        const pixelValuesToColorFn = values => {
                            const value = values[0];
                            switch(value) {
                                case 1: return '#A87000';
                                case 2: return '#FFD37F';
                                case 3: return '#E9FFBE';
                                case 4: return '#98E600';
                                case 5: return '#267300';
                                default: return '#00000000';
                            }
                        };

                        const layer = new GeoRasterLayer({
                            georaster,
                            opacity: 0.7,
                            pixelValuesToColorFn: pixelValuesToColorFn,
                            resolution: 256
                        });
                        layer.addTo(popupMap);
                        popupMap.fitBounds(layer.getBounds());

                        const legend = L.control({ position: 'bottomright' });

                legend.onAdd = function () {
                    const div = L.DomUtil.create('div', 'legend');
                    div.innerHTML += '<i style="background: #A87000"></i> Very Low<br>';
                    div.innerHTML += '<i style="background: #FFD37F"></i> Low<br>';
                    div.innerHTML += '<i style="background: #E9FFBE"></i> Medium<br>';
                    div.innerHTML += '<i style="background: #98E600"></i> High<br>';
                    div.innerHTML += '<i style="background: #267300"></i> Very High<br>';
                    return div;
                };

                legend.addTo(popupMap);
                    })
                    .catch(error => {
                        alert('Error loading TIFF file.');
                    });
                
                    });
            }
        });