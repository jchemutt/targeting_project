import rasterio
import numpy as np

with rasterio.open('C:/Users/jchemutt/Documents/projects/Targeting/targeting_project/media/output/processing_20240919174650_fbf063a0330441b58eb3e7aa4cb4c629/MahalanobisDist.tif') as src:
    print(src.crs)
    data = src.read(1)  # Read the first band
    valid_data = data[np.isfinite(data)]  # Filter out NaN and invalid values
    print("Valid values in raster:", valid_data)