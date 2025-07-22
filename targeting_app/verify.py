import rasterio
import numpy as np

raster_path = "C:/Users/jchemutt/Documents/projects/Targeting/targeting_project/media/output/processing_20241118111936_e4c01b6ff8bb493684451e0f6856eff6/MahalanobisDist.tif"
with rasterio.open(raster_path) as src:
    print("Raster CRS:", src.crs)
    print("Raster bounds:", src.bounds)
    print("NoData value:", src.nodata)
    print("Resolution:", src.res)
