import rasterio
import numpy as np
import csv
from collections import OrderedDict
import os

def reclassify(input_raster_path, output_raster_path, reclassification_rules=None, allow_overwrite=False):
    """
    Reclassifies the input raster based on the provided rules while preserving NoData values.

    Parameters:
        input_raster_path (str): Path to the input raster.
        output_raster_path (str): Path to save the reclassified raster.
        reclassification_rules (list of tuples): List of (min, max, new_value) for reclassification.
        allow_overwrite (bool): Whether to overwrite an existing file.

    Returns:
        str: Path to the reclassified raster.
    """
    NO_DATA_VALUE = -32768  # Consistent NoData value

    if os.path.exists(output_raster_path) and not allow_overwrite:
        raise FileExistsError(f"The output file '{output_raster_path}' already exists. "
                              f"Please provide a different path or enable overwriting.")

    with rasterio.open(input_raster_path,masked=True) as src:
        data = src.read(1).astype(np.float32)  # Ensure float32 for processing
        meta = src.meta.copy()

        # Preserve NoData values
        nodata = src.nodata if src.nodata is not None else NO_DATA_VALUE
        masked_data = np.ma.masked_equal(data, nodata)  # Mask NoData values

        if not reclassification_rules:
            reclassification_rules = [
                (0, 0.2, 1),
                (0.2, 0.4, 2),
                (0.4, 0.6, 3),
                (0.6, 0.8, 4),
                (0.8, 1.0, 5)
            ]

        # Initialize reclassified array with NoData values
        reclassified_data = np.ma.masked_array(
            np.full(masked_data.shape, NO_DATA_VALUE, dtype=np.float32),
            mask=masked_data.mask  # Inherit NoData mask
        )

        # Apply reclassification rules
        for (min_val, max_val, new_value) in reclassification_rules:
            mask = (masked_data >= min_val) & (masked_data <= max_val) & ~masked_data.mask  # Ignore NoData
            reclassified_data[mask] = new_value

        # Convert masked array back to standard numpy array while preserving NoData
        reclassified_data = np.ma.filled(reclassified_data, NO_DATA_VALUE)

        # Update metadata (ensure correct NoData handling)
        meta.update({
            "count": 1,
            "dtype": "float32",
            "nodata": NO_DATA_VALUE  # Explicit NoData assignment
        })

        # Save reclassified raster
        with rasterio.open(output_raster_path, "w", **meta) as dst:
            dst.write(reclassified_data, 1)

    return output_raster_path





def create_class_table_view(csv_path):
    fields = ["value", "Level"]
    symbology_fields = [
        OrderedDict(value=1, Level='Very Low'),
        OrderedDict(value=2, Level='Low'),
        OrderedDict(value=3, Level='Medium'),
        OrderedDict(value=4, Level='High'),
        OrderedDict(value=5, Level='Very High')
    ]

    with open(csv_path, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fields)
        writer.writeheader()

        for row in symbology_fields:
            writer.writerow(row)

    return csv_path

def rescale_to_0_1(input_raster_path, output_raster_path):
    with rasterio.open(input_raster_path) as src:
        data = src.read(1)
        meta = src.meta

        data_min = data.min()
        data_max = data.max()

        # Rescale data to 0 - 1
        scaled_data = (data - data_min) / (data_max - data_min)

        # Update meta to reflect the number of layers
        meta.update(count=1, dtype=rasterio.float32)

        with rasterio.open(output_raster_path, 'w', **meta) as dst:
            dst.write(scaled_data, 1)

    return output_raster_path
