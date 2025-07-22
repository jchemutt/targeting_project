import os
import numpy as np
import pandas as pd
from scipy.spatial.distance import mahalanobis
from scipy.stats import zscore
import rasterio

def read_raster(raster_path):
    """Read raster data and metadata using rasterio, and handle NoData values."""
    with rasterio.open(raster_path) as src:
        raster_data = src.read(1)  # Read the first band
        transform = src.transform
        nodata = src.nodata

        # If NoData is a specific value, replace it with np.nan
        if nodata is not None:
            raster_data = np.where(raster_data == nodata, np.nan, raster_data)

        return raster_data, transform, nodata

def write_raster(raster_data, output_path, transform, nodata):
    """Write raster data to a GeoTIFF file."""
    with rasterio.open(
        output_path,
        'w',
        driver='GTiff',
        height=raster_data.shape[0],
        width=raster_data.shape[1],
        count=1,
        dtype=raster_data.dtype,
        crs='EPSG:4326',  # Adjust CRS if necessary
        transform=transform,
        nodata=nodata
    ) as dst:
        dst.write(raster_data, 1)
    print(f"Raster saved to {output_path}")

def calculate_mahalanobis(threshold, idx, total_files, out_folder, df, raster_shape, transform, nodata):
    """Calculate Mahalanobis distance and save as a raster."""
    mean_vec = threshold.iloc[:, idx:idx + total_files].mean().values
    df_clean = df[~np.isnan(df).any(axis=1)]  # Remove rows with NaN values for covariance calculation

    if df_clean.shape[0] > 1:
        cov_matrix = np.cov(df_clean, rowvar=False)
    else:
        cov_matrix = np.eye(df_clean.shape[1])  # Fallback to identity matrix

    inv_cov_matrix = np.linalg.pinv(cov_matrix)
    mahal_dist = np.array([mahalanobis(row, mean_vec, inv_cov_matrix) for row in df])

    # Reshape the result back to the raster shape
    mahal_raster = mahal_dist.reshape(raster_shape)
    
    # Write the Mahalanobis result as a raster
    write_raster(mahal_raster, os.path.join(out_folder, "MahalanobisDist.tif"), transform, nodata)
    print("Mahalanobis Distance calculated")

def calculate_mess(df, total_files, threshold, idx, raster_shape, transform, nodata, out_folder):
    """Calculate MESS and save as a raster."""
    bool_na = ~np.isnan(df).any(axis=1)
    mess_result = np.full(df.shape[0], np.nan)

    for i in range(df.shape[0]):
        if bool_na[i]:
            z = zscore(df[i, :])
            mess_result[i] = min(z)

    # Reshape the result back to the raster shape
    mess_raster = mess_result.reshape(raster_shape)

    # Write the MESS result as a raster
    write_raster(mess_raster, os.path.join(out_folder, "MESS.tif"), transform, nodata)
    print("MESS calculated")

def similarity_analysis(total_files, work_space, file_paths):
    """Main function to perform similarity analysis (Mahalanobis and MESS) on raster data."""
    df = pd.DataFrame()
    raster_shape = None
    transform = None
    nodata = None

    # Read the threshold CSV (assuming this contains the required data for calculations)
    threshold = pd.read_csv(os.path.join(work_space, "temp.csv"))

    # Iterate over the actual raster file paths
    for i, file_path in enumerate(file_paths, start=1):
        raster_data, transform, nodata = read_raster(file_path)
        
        if i == 1:
            raster_shape = raster_data.shape
            df = pd.DataFrame(raster_data.flatten())  # Flatten the raster into 1D array
        else:
            df = pd.concat([df, pd.DataFrame(raster_data.flatten())], axis=1)

    # Calculate Mahalanobis distance and save it as a GeoTIFF raster
    try:
        calculate_mahalanobis(threshold, 0, total_files, work_space, df.values, raster_shape, transform, nodata)
    except Exception as e:
        print(f"Error in calculate_mahalanobis: {e}")

    # Calculate MESS and save it as a GeoTIFF raster
    try:
        calculate_mess(df.values, total_files, threshold, 0, raster_shape, transform, nodata, work_space)
    except Exception as e:
        print(f"Error in calculate_mess: {e}")
