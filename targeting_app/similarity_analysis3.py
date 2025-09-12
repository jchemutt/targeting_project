# similarity_analysis.py

import os
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.spatial.distance import mahalanobis
import rasterio
from rasterio.warp import reproject, Resampling, calculate_default_transform
from typing import List, Tuple, Optional


# -----------------------------
# Raster I/O and reprojection
# -----------------------------

def read_raster_with_nodata(raster_path: str):
    """
    Read a single-band raster as float32 with nodata -> NaN.
    Returns: (array[H,W], transform, crs, nodata, width, height)
    """
    with rasterio.open(raster_path) as src:
        arr = src.read(1).astype("float32", copy=False)
        nodata = src.nodata
        if nodata is not None:
            arr = np.where(arr == nodata, np.nan, arr)
        return arr, src.transform, src.crs, nodata, src.width, src.height


def reproject_to_match(src_arr: np.ndarray,
                       src_transform,
                       src_crs,
                       dst_transform,
                       dst_crs,
                       dst_width: int,
                       dst_height: int) -> np.ndarray:
    """
    Reproject src_arr to destination grid/CRS. Keeps data as float32 and
    uses nearest resampling (good for categorical or mixed surfaces).
    """
    dst = np.full((dst_height, dst_width), np.nan, dtype=np.float32)
    reproject(
        source=src_arr,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=Resampling.nearest,
        src_nodata=np.nan,
        dst_nodata=np.nan,
    )
    return dst


def write_raster(data: np.ndarray,
                 output_path: str,
                 transform,
                 crs,
                 nodata: Optional[float] = None,
                 dtype: Optional[str] = None,
                 compress: str = "lzw"):
    """
    Write a single-band raster on the given grid/CRS.
    - If dtype is None, infer from data.
    - If nodata is None and data is float, we omit nodata tag and keep NaNs in data.
    """
    out_dir = os.path.dirname(output_path)
    os.makedirs(out_dir, exist_ok=True)

    arr = data
    if dtype is None:
        # If there are NaNs, stick to float32
        if np.issubdtype(arr.dtype, np.floating):
            dtype = "float32"
        else:
            dtype = str(arr.dtype)
    arr = arr.astype(dtype, copy=False)

    profile = {
        "driver": "GTiff",
        "height": arr.shape[0],
        "width": arr.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "compress": compress,
    }

    # Only set a nodata tag if it's a proper scalar and not NaN for float
    if nodata is not None and not (np.issubdtype(arr.dtype, np.floating) and np.isnan(nodata)):
        profile["nodata"] = nodata

    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(arr, 1)


def write_raster_reprojected_to_epsg4326(data: np.ndarray,
                                         output_path: str,
                                         src_transform,
                                         src_crs,
                                         src_nodata: Optional[float] = None,
                                         dtype: Optional[str] = None,
                                         compress: str = "lzw"):
    """
    Convenience writer that reprojects data to EPSG:4326 before writing.
    """
    rows, cols = data.shape

    # Compute bounds of the source grid
    # Top-left (0,0) with UL offset; bottom-right (rows-1, cols-1) with LR offset
    from rasterio.transform import xy
    west, north = xy(src_transform, 0, 0, offset="ul")
    east, south = xy(src_transform, rows - 1, cols - 1, offset="lr")

    dst_transform, dst_width, dst_height = calculate_default_transform(
        src_crs, "EPSG:4326", cols, rows, west, south, east, north
    )

    # Reproject to 4326
    dst_arr = np.full((dst_height, dst_width), np.nan, dtype=np.float32)
    reproject(
        source=data.astype(np.float32, copy=False),
        destination=dst_arr,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs="EPSG:4326",
        resampling=Resampling.nearest,
        src_nodata=np.nan,
        dst_nodata=np.nan,
    )

    # Write (omit nodata tag for float with NaNs)
    write_raster(dst_arr, output_path, dst_transform, "EPSG:4326",
                 nodata=None if src_nodata is None else src_nodata,
                 dtype="float32", compress=compress)


# -----------------------------
# Stacking on a common template
# -----------------------------

def stack_rasters_to_template(file_paths: List[str]):
    """
    Reads all rasters and reprojects them to the first raster's grid.
    Returns:
      df        : DataFrame (n_pixels x n_bands)
      shape     : (height, width)
      transform : Affine of template
      crs       : CRS of template
      nodata    : original nodata of template (may be None)
      band_names: list of stem names for bands
    """
    if not file_paths:
        raise ValueError("No raster file paths provided.")

    # Template = first raster
    t_arr, t_transform, t_crs, t_nodata, t_w, t_h = read_raster_with_nodata(file_paths[0])
    raster_shape = (t_h, t_w)
    n_pixels = t_h * t_w

    df_cols = [Path(file_paths[0]).stem]
    df = pd.DataFrame(t_arr.reshape(-1))

    for fp in file_paths[1:]:
        arr, tr, crs, nd, w, h = read_raster_with_nodata(fp)
        if (crs != t_crs) or (tr != t_transform) or (w != t_w) or (h != t_h):
            arr = reproject_to_match(arr, tr, crs, t_transform, t_crs, t_w, t_h)
        df = pd.concat([df, pd.DataFrame(arr.reshape(-1))], axis=1)
        df_cols.append(Path(fp).stem)

    df.columns = df_cols

    # Final safety check
    if df.shape[0] != n_pixels:
        raise ValueError(f"Stack length {df.shape[0]} != template pixels {n_pixels} "
                         f"({raster_shape[0]} x {raster_shape[1]}).")

    return df, raster_shape, t_transform, t_crs, t_nodata, df_cols


# -----------------------------
# Classification helpers
# -----------------------------

def reclassify_to_quantiles(raster_array: np.ndarray, num_classes: int = 5) -> np.ndarray:
    """
    Reclassify continuous raster into quantile classes 1..num_classes.
    NaNs preserved → encoded as 0 in the output (uint8).
    """
    flat = raster_array.reshape(-1)
    valid = flat[~np.isnan(flat)]

    if valid.size == 0:
        # All NaNs → return zeros
        out = np.zeros_like(raster_array, dtype=np.uint8)
        return out

    # To avoid identical bin edges when distribution is very flat, add tiny jitter
    # (safe because we only use it to compute thresholds)
    if np.allclose(np.nanmin(valid), np.nanmax(valid)):
        # All values equal → put all to middle class
        cls = np.full_like(raster_array, fill_value=(num_classes + 1) // 2, dtype=np.uint8)
        cls[np.isnan(raster_array)] = 0
        return cls

    qs = np.quantile(valid, q=np.linspace(0, 1, num_classes + 1))
    # Ensure strictly increasing edges (guard numerical issues)
    for i in range(1, len(qs)):
        if qs[i] <= qs[i - 1]:
            qs[i] = np.nextafter(qs[i - 1], np.inf)

    # Digitize valid values
    out_flat = np.full(flat.shape, np.nan, dtype=np.float32)
    out_flat[~np.isnan(flat)] = np.digitize(valid, qs[1:], right=True) + 1  # 1..num_classes

    out = out_flat.reshape(raster_array.shape)
    out_u8 = np.where(np.isnan(out), 0, out).astype(np.uint8)
    return out_u8


# -----------------------------
# Mahalanobis & MESS
# -----------------------------

def _mean_std_from_threshold(threshold_df: pd.DataFrame, band_names: List[str],
                             fallback_df: Optional[pd.DataFrame] = None):
    """
    Try to compute mean/std per band from threshold_df. If columns don't align,
    fall back to fallback_df statistics.
    Returns: (mean_vec[N], std_vec[N])
    """
    # Try name alignment first
    common = [c for c in band_names if c in threshold_df.columns]
    if len(common) == len(band_names):
        mu = threshold_df[band_names].mean(axis=0).values.astype("float32")
        std = threshold_df[band_names].std(ddof=0, axis=0).values.astype("float32")
    else:
        # Fallback to "first N columns"
        if threshold_df.shape[1] >= len(band_names):
            sub = threshold_df.iloc[:, :len(band_names)]
            mu = sub.mean(axis=0).values.astype("float32")
            std = sub.std(ddof=0, axis=0).values.astype("float32")
        elif fallback_df is not None and fallback_df.shape[1] == len(band_names):
            mu = np.nanmean(fallback_df.values, axis=0).astype("float32")
            std = np.nanstd(fallback_df.values, axis=0).astype("float32")
        else:
            raise ValueError("Cannot align threshold statistics with band names and no valid fallback available.")

    # Replace zeros with tiny epsilon to avoid division by zero
    std = np.where(std == 0.0, 1e-6, std)
    return mu, std


def calculate_mahalanobis(threshold_df: pd.DataFrame,
                          df_values: np.ndarray,
                          raster_shape: Tuple[int, int],
                          out_folder: str,
                          transform,
                          crs,
                          nodata: Optional[float],
                          reproject_to_4326: bool = False):
    """
    df_values: array (n_pixels, n_vars)
    """
    H, W = raster_shape
    n_pixels, n_vars = df_values.shape

    # Mean from threshold; covariance from df_values valid rows
    mu = threshold_df.mean(axis=0).values[:n_vars].astype("float32")

    df_clean = df_values[~np.isnan(df_values).any(axis=1)]
    if df_clean.shape[0] > 1:
        cov = np.cov(df_clean, rowvar=False)
        inv_cov = np.linalg.pinv(cov)
    else:
        inv_cov = np.eye(n_vars, dtype="float32")

    # Compute distances
    out_dist = np.full(n_pixels, np.nan, dtype=np.float32)
    for i in range(n_pixels):
        row = df_values[i]
        if not np.any(np.isnan(row)):
            out_dist[i] = mahalanobis(row, mu, inv_cov)

    mahal_raster = out_dist.reshape(H, W)

    # Write continuous
    out_path = os.path.join(out_folder, "MahalanobisDist.tif")
    if reproject_to_4326:
        write_raster_reprojected_to_epsg4326(mahal_raster, out_path, transform, crs, src_nodata=nodata, dtype="float32")
    else:
        write_raster(mahal_raster, out_path, transform, crs, nodata=None, dtype="float32")

    # Reclassify to quantiles (1..5, 0=NaN)
    reclassified = reclassify_to_quantiles(mahal_raster)
    out_q = os.path.join(out_folder, "MahalanobisDist_Quantiles.tif")
    if reproject_to_4326:
        write_raster_reprojected_to_epsg4326(reclassified.astype(np.uint8), out_q, transform, crs, src_nodata=0, dtype="uint8")
    else:
        write_raster(reclassified.astype(np.uint8), out_q, transform, crs, nodata=0, dtype="uint8")


def calculate_mess(threshold_df: pd.DataFrame,
                   band_names: List[str],
                   df_values: np.ndarray,
                   raster_shape: Tuple[int, int],
                   out_folder: str,
                   transform,
                   crs,
                   nodata: Optional[float],
                   reproject_to_4326: bool = False):
    """
    MESS-like score: for each pixel vector x, compute standardized z = (x - mu)/sigma
    using mean/std from the threshold samples. The MESS is min(z) across variables
    (simple proxy; customize with min-distance-to-range if needed).
    """
    H, W = raster_shape
    n_pixels, n_vars = df_values.shape

    # Use threshold stats aligned to band_names when possible; fallback to df stats
    mu, sigma = _mean_std_from_threshold(threshold_df, band_names,
                                         fallback_df=pd.DataFrame(df_values))

    sigma = sigma.astype("float32")
    mu = mu.astype("float32")

    # Vectorized z-scores; protect from division by zero already handled in _mean_std_from_threshold
    z = (df_values - mu.reshape(1, -1)) / sigma.reshape(1, -1)
    # Any NaN row -> overall NaN
    z[np.isnan(z).any(axis=1)] = np.nan
    mess_vec = np.nanmin(z, axis=1)  # min across variables

    mess_raster = mess_vec.reshape(H, W)

    out_path = os.path.join(out_folder, "MESS.tif")
    if reproject_to_4326:
        write_raster_reprojected_to_epsg4326(mess_raster, out_path, transform, crs, src_nodata=nodata, dtype="float32")
    else:
        write_raster(mess_raster, out_path, transform, crs, nodata=None, dtype="float32")

    # Quantiles
    mess_q = reclassify_to_quantiles(mess_raster)
    out_q = os.path.join(out_folder, "MESS_Quantiles.tif")
    if reproject_to_4326:
        write_raster_reprojected_to_epsg4326(mess_q.astype(np.uint8), out_q, transform, crs, src_nodata=0, dtype="uint8")
    else:
        write_raster(mess_q.astype(np.uint8), out_q, transform, crs, nodata=0, dtype="uint8")


# -----------------------------
# Orchestrator
# -----------------------------

def similarity_analysis(total_files: int, work_space: str, file_paths: List[str],
                        reproject_outputs_to_4326: bool = False):
    """
    Main entry point.
    - total_files: expected number of raster variables (should match len(file_paths))
    - work_space: output directory where temp.csv exists and results will be written
    - file_paths: list of absolute or relative raster paths
    - reproject_outputs_to_4326: if True, output GeoTIFFs are reprojected to EPSG:4326
    """
    try:
        print("Starting similarity analysis...")

        if len(file_paths) != total_files:
            print(f"[WARN] total_files={total_files} but len(file_paths)={len(file_paths)}. Using len(file_paths).")
            total_files = len(file_paths)

        threshold_csv = os.path.join(work_space, "temp.csv")
        if not os.path.exists(threshold_csv):
            raise FileNotFoundError(f"Threshold CSV not found at: {threshold_csv}")

        threshold = pd.read_csv(threshold_csv)

        # Stack rasters to the first raster's grid
        df, raster_shape, transform, crs, nodata, band_names = stack_rasters_to_template(file_paths)

        # Safety: ensure expected variable count
        if df.shape[1] != total_files:
            print(f"[WARN] Expected {total_files} variables, but stacked {df.shape[1]}. Continuing with {df.shape[1]}.")

        # Compute and write results
        calculate_mahalanobis(threshold_df=threshold,
                              df_values=df.values,
                              raster_shape=raster_shape,
                              out_folder=work_space,
                              transform=transform,
                              crs=crs,
                              nodata=nodata,
                              reproject_to_4326=reproject_outputs_to_4326)

        calculate_mess(threshold_df=threshold,
                       band_names=band_names,
                       df_values=df.values,
                       raster_shape=raster_shape,
                       out_folder=work_space,
                       transform=transform,
                       crs=crs,
                       nodata=nodata,
                       reproject_to_4326=reproject_outputs_to_4326)

        print("Similarity analysis and classification completed.")

    except Exception as e:
        print(f"Error in similarity_analysis: {e}")
        raise
