import os
import numpy as np
import pandas as pd
from scipy.spatial.distance import mahalanobis
from scipy.stats import zscore
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.transform import xy

def read_raster(raster_path):
    with rasterio.open(raster_path) as src:
        data = src.read(1)
        transform = src.transform
        nodata = src.nodata
        crs = src.crs
        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)
        return data, transform, nodata, crs

def write_raster_to_epsg4326(data, output_path, transform, nodata, input_crs):
    rows, cols = data.shape
    west, south = xy(transform, 0, 0, offset='ul')
    east, north = xy(transform, rows - 1, cols - 1, offset='lr')
    bounds = (west, south, east, north)

    dst_transform, width, height = calculate_default_transform(
        input_crs, "EPSG:4326", cols, rows, *bounds)

    dst_data = np.empty((height, width), dtype=data.dtype)

    reproject(
        source=data,
        destination=dst_data,
        src_transform=transform,
        src_crs=input_crs,
        dst_transform=dst_transform,
        dst_crs="EPSG:4326",
        resampling=Resampling.nearest,
    )

    with rasterio.open(
        output_path, 'w',
        driver='GTiff',
        height=height,
        width=width,
        count=1,
        dtype=dst_data.dtype,
        crs="EPSG:4326",
        transform=dst_transform,
        nodata=nodata
    ) as dst:
        dst.write(dst_data, 1)

def reclassify_to_quantiles(raster_array, num_classes=5):
    """
    Reclassify a continuous raster array into quantile-based classes (1 to num_classes).
    NaN values are preserved and not included in classification.
    """
    flat_data = raster_array.flatten()
    valid_data = flat_data[~np.isnan(flat_data)]

    if len(valid_data) == 0:
        raise ValueError("No valid (non-NaN) values to compute quantiles.")

    # Compute quantile thresholds (bin edges)
    quantiles = np.quantile(valid_data, q=np.linspace(0, 1, num_classes + 1))

    # Digitize valid data only
    classified_flat = np.full(flat_data.shape, np.nan)
    classified_flat[~np.isnan(flat_data)] = np.digitize(valid_data, quantiles[1:], right=True) + 1  # 1–5

    # Reshape to original raster shape
    classified = classified_flat.reshape(raster_array.shape)

    # Cast to uint8 safely (NaNs will remain)
    classified_masked = np.where(np.isnan(classified), 0, classified).astype(np.uint8)

    return classified_masked


def calculate_mahalanobis(threshold, total_files, out_folder, df, raster_shape, transform, nodata, input_crs):
    mean_vec = threshold.iloc[:, :total_files].mean().values
    df_clean = df[~np.isnan(df).any(axis=1)]
    cov_matrix = np.cov(df_clean, rowvar=False) if df_clean.shape[0] > 1 else np.eye(df_clean.shape[1])
    inv_cov = np.linalg.pinv(cov_matrix)

    mahal_dist = np.array([
        mahalanobis(row, mean_vec, inv_cov) if not np.any(np.isnan(row)) else np.nan
        for row in df
    ])

    mahal_raster = mahal_dist.reshape(raster_shape)

    out_path = os.path.join(out_folder, "MahalanobisDist.tif")
    write_raster_to_epsg4326(mahal_raster, out_path, transform, nodata, input_crs)

    # Reclassify
    reclassified = reclassify_to_quantiles(mahal_raster)
    classified_out = os.path.join(out_folder, "MahalanobisDist_Quantiles.tif")
    write_raster_to_epsg4326(reclassified, classified_out, transform, nodata=0, input_crs=input_crs)

def calculate_mess(df, total_files, threshold, raster_shape, transform, nodata, out_folder, input_crs):
    mess_result = np.full(df.shape[0], np.nan)
    for i, row in enumerate(df):
        if not np.any(np.isnan(row)):
            z = zscore(row)
            mess_result[i] = np.min(z)
    mess_raster = mess_result.reshape(raster_shape)

    out_path = os.path.join(out_folder, "MESS.tif")
    write_raster_to_epsg4326(mess_raster, out_path, transform, nodata, input_crs)

    # Reclassify
    reclassified = reclassify_to_quantiles(mess_raster)
    classified_out = os.path.join(out_folder, "MESS_Quantiles.tif")
    write_raster_to_epsg4326(reclassified, classified_out, transform, nodata=0, input_crs=input_crs)

def similarity_analysis(total_files, work_space, file_paths):
    try:
        print("Starting similarity analysis...")
        df = pd.DataFrame()
        raster_shape, transform, nodata, input_crs = None, None, None, None

        threshold_csv = os.path.join(work_space, "temp.csv")
        if not os.path.exists(threshold_csv):
            raise FileNotFoundError("Threshold CSV not found.")

        threshold = pd.read_csv(threshold_csv)

        for i, fp in enumerate(file_paths):
            raster_data, transform, nodata, input_crs = read_raster(fp)
            if i == 0:
                raster_shape = raster_data.shape
                df = pd.DataFrame(raster_data.flatten())
            else:
                df = pd.concat([df, pd.DataFrame(raster_data.flatten())], axis=1)

        calculate_mahalanobis(threshold, total_files, work_space, df.values, raster_shape, transform, nodata, input_crs)
        calculate_mess(df.values, total_files, threshold, raster_shape, transform, nodata, work_space, input_crs)

        print("Similarity analysis and classification completed.")
    except Exception as e:
        print(f"Error in similarity_analysis: {e}")
        raise
