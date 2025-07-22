import os
import numpy as np
import pandas as pd
from scipy.spatial.distance import mahalanobis
from scipy.stats import zscore
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.transform import xy

def read_raster(raster_path):
    """
    Read raster data and metadata using rasterio.
    Replace NoData values with NaN for computation.
    """
    try:
        with rasterio.open(raster_path) as src:
            raster_data = src.read(1)  # Read the first band
            transform = src.transform
            nodata = src.nodata

            print(f"Reading raster: {raster_path}")
            print(f"CRS: {src.crs}, Bounds: {src.bounds}, Resolution: {src.res}, NoData: {nodata}")

            # Replace NoData values with NaN
            if nodata is not None:
                raster_data = np.where(raster_data == nodata, np.nan, raster_data)

            valid_data_count = np.count_nonzero(~np.isnan(raster_data))
            print(f"Valid data count: {valid_data_count}")

            return raster_data, transform, nodata, src.crs
    except Exception as e:
        print(f"Error reading raster {raster_path}: {e}")
        raise



def write_raster_to_epsg4326(raster_data, output_path, transform, nodata, input_crs):
    """
    Transform raster data to EPSG:4326 and write it to a GeoTIFF file.
    """
    try:
        print(f"Transforming and writing raster to EPSG:4326 at {output_path}")

        # Define the target CRS
        target_crs = "EPSG:4326"

        # Calculate bounds using the transform and raster dimensions
        rows, cols = raster_data.shape
        west, south = xy(transform, 0, 0, offset='ul')  # Upper-left corner
        east, north = xy(transform, rows - 1, cols - 1, offset='lr')  # Lower-right corner
        bounds = (west, south, east, north)

        # Calculate the transformation and output dimensions
        dst_transform, width, height = calculate_default_transform(
            input_crs, target_crs, cols, rows, *bounds
        )

        # Create an empty array for the transformed raster
        dst_raster_data = np.empty((height, width), dtype=raster_data.dtype)

        # Reproject the raster
        reproject(
            source=raster_data,
            destination=dst_raster_data,
            src_transform=transform,
            src_crs=input_crs,
            dst_transform=dst_transform,
            dst_crs=target_crs,
            resampling=Resampling.nearest,  # Resampling method
        )

        # Write the transformed raster
        with rasterio.open(
            output_path,
            'w',
            driver='GTiff',
            height=height,
            width=width,
            count=1,
            dtype=dst_raster_data.dtype,
            crs=target_crs,  # Use the target CRS
            transform=dst_transform,
            nodata=nodata
        ) as dst:
            dst.write(dst_raster_data, 1)

        print(f"Raster transformed and saved to {output_path}")
    except Exception as e:
        print(f"Error transforming and writing raster to EPSG:4326: {e}")
        raise


def calculate_mahalanobis(threshold, total_files, out_folder, df, raster_shape, transform, nodata, input_crs):
    """
    Calculate Mahalanobis distance and save as a raster.
    """
    try:
        print("Calculating Mahalanobis distance...")
        mean_vec = threshold.iloc[:, :total_files].mean().values
        df_clean = df[~np.isnan(df).any(axis=1)]  # Remove rows with NaN for covariance

        # Fallback to identity matrix if covariance calculation fails
        cov_matrix = np.cov(df_clean, rowvar=False) if df_clean.shape[0] > 1 else np.eye(df_clean.shape[1])
        inv_cov_matrix = np.linalg.pinv(cov_matrix)

        # Calculate Mahalanobis distance
        mahal_dist = np.array([
            mahalanobis(row, mean_vec, inv_cov_matrix) if not np.any(np.isnan(row)) else np.nan
            for row in df
        ])

        # Reshape and save as raster
        mahal_raster = mahal_dist.reshape(raster_shape)
        write_raster_to_epsg4326(mahal_raster, os.path.join(out_folder, "MahalanobisDist.tif"), transform, nodata, input_crs)
        print("Mahalanobis Distance successfully calculated and saved.")
    except Exception as e:
        print(f"Error in Mahalanobis calculation: {e}")
        raise

def calculate_mess(df, total_files, threshold, raster_shape, transform, nodata, out_folder, input_crs):
    """
    Calculate MESS and save as a raster.
    """
    try:
        print("Calculating MESS...")
        mess_result = np.full(df.shape[0], np.nan)

        for i, row in enumerate(df):
            if not np.any(np.isnan(row)):
                z = zscore(row)
                mess_result[i] = np.min(z)

        # Reshape and save as raster
        mess_raster = mess_result.reshape(raster_shape)
        write_raster_to_epsg4326(mess_raster, os.path.join(out_folder, "MESS.tif"), transform, nodata, input_crs)
        print("MESS successfully calculated and saved.")
    except Exception as e:
        print(f"Error in MESS calculation: {e}")
        raise

def similarity_analysis(total_files, work_space, file_paths):
    """
    Perform similarity analysis (Mahalanobis and MESS) on raster data.
    """
    try:
        print("Starting similarity analysis...")
        df = pd.DataFrame()
        raster_shape, transform, nodata, input_crs = None, None, None, None

        # Read the threshold CSV
        threshold_csv_path = os.path.join(work_space, "temp.csv")
        if not os.path.exists(threshold_csv_path):
            raise FileNotFoundError(f"Threshold CSV file not found: {threshold_csv_path}")

        threshold = pd.read_csv(threshold_csv_path)
        print(f"Threshold CSV loaded from {threshold_csv_path}")

        # Read and flatten raster files
        for i, file_path in enumerate(file_paths, start=1):
            raster_data, transform, nodata, input_crs = read_raster(file_path)

            if i == 1:
                raster_shape = raster_data.shape
                df = pd.DataFrame(raster_data.flatten())
            else:
                df = pd.concat([df, pd.DataFrame(raster_data.flatten())], axis=1)

        print(f"Raster data successfully loaded. DataFrame shape: {df.shape}")

        # Mahalanobis Distance Calculation
        calculate_mahalanobis(threshold, total_files, work_space, df.values, raster_shape, transform, nodata, input_crs)

        # MESS Calculation
        calculate_mess(df.values, total_files, threshold, raster_shape, transform, nodata, work_space, input_crs)

        print("Similarity analysis completed successfully.")
    except Exception as e:
        print(f"Error in similarity_analysis: {e}")
        raise
