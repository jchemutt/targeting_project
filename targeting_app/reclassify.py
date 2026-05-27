import rasterio
import numpy as np
import csv
from collections import OrderedDict
import os
from typing import List, Tuple, Optional, Union


DEFAULT_RULES: List[Tuple[float, float, int]] = [
    (0.0, 0.2, 1),
    (0.2, 0.4, 2),
    (0.4, 0.6, 3),
    (0.6, 0.8, 4),
    (0.8, 1.0, 5),
]


def _normalize_dtype(dtype: Union[str, np.dtype]) -> np.dtype:
    """Normalize dtype input to a numpy dtype."""
    try:
        return np.dtype(dtype)
    except Exception as e:
        raise ValueError(f"Invalid output_dtype '{dtype}'.") from e


def _default_nodata_for_dtype(dtype: np.dtype) -> Union[int, float]:
    """Default output nodata by dtype."""
    if np.issubdtype(dtype, np.floating):
        return np.float32(-32768)
    return int(0)


def _rules_are_valid(rules: List[Tuple[float, float, int]]) -> None:
    if not rules:
        raise ValueError("Reclassification rules cannot be empty.")
    for i, (mn, mx, v) in enumerate(rules):
        if mx < mn:
            raise ValueError(f"Rule {i} has max < min: ({mn}, {mx}, {v})")
    for i in range(1, len(rules)):
        prev_mn, prev_mx, _ = rules[i - 1]
        mn, mx, _ = rules[i]
        if mn < prev_mn:
            raise ValueError("Rules should be ordered by ascending min.")
        if mn < prev_mx:
            raise ValueError(
                f"Rules overlap between rule {i-1} ({prev_mn},{prev_mx}) and rule {i} ({mn},{mx}). "
                "Use half-open bins like [0.0,0.2), [0.2,0.4) ... and last inclusive."
            )


def reclassify(
    input_raster_path: str,
    output_raster_path: str,
    reclassification_rules: Optional[List[Tuple[float, float, int]]] = None,
    allow_overwrite: bool = False,
    output_dtype: Union[str, np.dtype] = "uint8",
    output_nodata: Optional[Union[int, float]] = None,
    compress: bool = True,
    tiled: bool = True,
    tile_size: int = 512,
) -> str:
    """
    Reclassifies an input raster (typically a 0..1 suitability raster) into classes (e.g., 1..5),
    preserving NoData pixels.

    NOTE: This function only changes the *reclassified* output file.
    The original continuous suitability raster remains unchanged for other analyses.
    """
    if os.path.exists(output_raster_path) and not allow_overwrite:
        raise FileExistsError(
            f"The output file '{output_raster_path}' already exists. "
            f"Provide a different path or set allow_overwrite=True."
        )

    rules = reclassification_rules or DEFAULT_RULES
    _rules_are_valid(rules)

    out_dtype = _normalize_dtype(output_dtype)
    nodata_out = _default_nodata_for_dtype(out_dtype) if output_nodata is None else output_nodata

    with rasterio.open(input_raster_path) as src:
        data = src.read(1, masked=True)
        masked_data = np.ma.masked_invalid(data)
        meta = src.meta.copy()

        out = np.full(masked_data.shape, nodata_out, dtype=out_dtype)
        valid = ~masked_data.mask
        vals = masked_data.data

        for i, (mn, mx, new_value) in enumerate(rules):
            is_last = (i == len(rules) - 1)
            if is_last:
                m = valid & (vals >= mn) & (vals <= mx)
            else:
                m = valid & (vals >= mn) & (vals < mx)
            out[m] = np.array(new_value, dtype=out_dtype)

        meta.update({"count": 1, "dtype": str(out_dtype), "nodata": nodata_out})

        driver = meta.get("driver", "GTiff")
        if driver.lower() in ("gtiff", "cog"):
            if compress:
                meta["compress"] = "DEFLATE"
                meta["predictor"] = 2 if np.issubdtype(out_dtype, np.floating) else 1
                meta["zlevel"] = 6
            if tiled:
                meta["tiled"] = True
                meta["blockxsize"] = int(tile_size)
                meta["blockysize"] = int(tile_size)
            meta["bigtiff"] = "IF_SAFER"

        with rasterio.open(output_raster_path, "w", **meta) as dst:
            dst.write(out, 1)

    return output_raster_path


def create_class_table_view(csv_path: str) -> str:
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


def rescale_to_0_1(input_raster_path: str, output_raster_path: str, allow_overwrite: bool = False) -> str:
    """Rescale valid pixels to [0,1] while preserving NoData."""
    if os.path.exists(output_raster_path) and not allow_overwrite:
        raise FileExistsError(
            f"The output file '{output_raster_path}' already exists. "
            f"Provide a different path or set allow_overwrite=True."
        )

    with rasterio.open(input_raster_path) as src:
        data = src.read(1, masked=True)
        data = np.ma.masked_invalid(data)
        meta = src.meta.copy()

        valid = ~data.mask
        if not np.any(valid):
            nod = src.nodata if src.nodata is not None else -32768
            out = np.full(data.shape, nod, dtype=np.float32)
            meta.update(count=1, dtype="float32", nodata=nod)
            with rasterio.open(output_raster_path, "w", **meta) as dst:
                dst.write(out, 1)
            return output_raster_path

        vals = data.data[valid].astype(np.float32)
        mn = float(vals.min())
        mx = float(vals.max())

        nod = src.nodata if src.nodata is not None else -32768
        out = np.full(data.shape, nod, dtype=np.float32)

        if np.isclose(mx, mn):
            out[valid] = 0.0
        else:
            out[valid] = (data.data[valid].astype(np.float32) - mn) / (mx - mn)

        meta.update(count=1, dtype="float32", nodata=nod)
        with rasterio.open(output_raster_path, "w", **meta) as dst:
            dst.write(out, 1)

    return output_raster_path
