import rasterio
from rasterio.plot import show
import matplotlib.pyplot as plt
import numpy as np

def plot_raster_with_legend(raster_path):
    # Open the raster file using rasterio
    with rasterio.open(raster_path) as src:
        # Read the first band (assuming single-band raster)
        raster_data = src.read(1)
        nodata = src.nodata  # Get NoData value

        # Mask NoData values to avoid plotting them
        if nodata is not None:
            raster_data = np.ma.masked_equal(raster_data, nodata)

        # Set up the plot
        plt.figure(figsize=(10, 8))

        # Plot the raster data using a colormap (e.g., 'viridis')
        cmap = plt.get_cmap('viridis')
        img = plt.imshow(raster_data, cmap=cmap)

        # Add a color bar (legend) and label it with pixel values
        cbar = plt.colorbar(img)
        cbar.set_label('Pixel Value')

        # Add a title to the plot
        plt.title(f"Mahalanobis distance")

        # Display the plot
        plt.show()

# Path to your raster file
raster_file = "C:/Users/jchemutt/Documents/projects/Targeting/targeting_project/media/output/processing_20241118111936_e4c01b6ff8bb493684451e0f6856eff6/MahalanobisDist.tif"

# Plot the raster with legend
plot_raster_with_legend(raster_file)
