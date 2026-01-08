import numpy as np
import rasterio
from pathlib import Path
from django.conf import settings

from rasterio.windows import from_bounds
from rasterio.warp import reproject, Resampling


class LandStatistics:
    def __init__(
        self,
        reference_layer,
        raster_file,
        description,
        stat_types=None,
        reference_kind=None,  # "continuous" | "categorical" | None (auto)
    ):
        self.reference_layer = reference_layer
        self.raster_file = raster_file
        self.description = description
        self.stat_types = stat_types or ["mean"]
        self.reference_kind = reference_kind

    def _get_raster_path(self):
        raster_path = Path(settings.MEDIA_ROOT) / self.raster_file.lstrip("/media/")
        if not raster_path.exists():
            raise FileNotFoundError(f"Raster file not found at {raster_path}")
        return raster_path

    def _get_reference_path(self):
        reference_path = Path(settings.BASE_DIR) / self.reference_layer
        if not reference_path.exists():
            raise FileNotFoundError(f"Reference file not found at {reference_path}")
        return reference_path

    # -----------------------------
    # Alignment helpers
    # -----------------------------
    def _pick_resampling_for_reference(self, ref_src):
        """
        Decide resampling:
          - categorical -> nearest
          - continuous -> bilinear
          - auto heuristic if None
        """
        if self.reference_kind == "categorical":
            return Resampling.nearest
        if self.reference_kind == "continuous":
            return Resampling.bilinear

        # AUTO heuristic
        dt = np.dtype(ref_src.dtypes[0])
        if np.issubdtype(dt, np.floating):
            return Resampling.bilinear

        # integer: inspect a small sample window
        h = min(512, ref_src.height)
        w = min(512, ref_src.width)
        window = rasterio.windows.Window(0, 0, w, h)
        sample = ref_src.read(1, window=window, masked=True)
        vals = sample.compressed()

        if vals.size == 0:
            return Resampling.nearest

        uniq = np.unique(vals)
        # If few unique values, likely class codes => categorical
        return Resampling.nearest if uniq.size <= 50 else Resampling.bilinear

    def _read_class_and_aligned_ref(self, class_src, ref_src):
        """
        Read suitability/quantile raster as target grid,
        then read reference raster and align it to class grid.

        Returns: (class_data_masked, ref_aligned_masked)
        """
        class_data = class_src.read(1, masked=True)

        # If already same grid, no work
        same_grid = (
            class_src.crs == ref_src.crs
            and class_src.transform == ref_src.transform
            and class_src.width == ref_src.width
            and class_src.height == ref_src.height
        )
        if same_grid:
            ref_data = ref_src.read(1, masked=True)
            return class_data, ref_data

        resampling = self._pick_resampling_for_reference(ref_src)

        # Clip reference to class bounds when CRS matches (speed-up)
        use_window = class_src.crs == ref_src.crs

        if use_window:
            b = class_src.bounds
            win = from_bounds(b.left, b.bottom, b.right, b.top, transform=ref_src.transform)
            win = win.round_offsets().round_lengths()

            ref_subset = ref_src.read(1, window=win, masked=True)
            ref_subset_transform = ref_src.window_transform(win)

            src = ref_subset
            src_transform = ref_subset_transform
        else:
            # CRS differs; reproject from band directly
            src = rasterio.band(ref_src, 1)
            src_transform = ref_src.transform

        # Warp reference onto class grid
        dst = np.full((class_src.height, class_src.width), np.nan, dtype="float32")

        reproject(
            source=src,
            destination=dst,
            src_transform=src_transform,
            src_crs=ref_src.crs,
            dst_transform=class_src.transform,
            dst_crs=class_src.crs,
            dst_nodata=np.nan,
            resampling=resampling,
        )

        ref_aligned = np.ma.masked_invalid(dst)

        # Final safety check
        if ref_aligned.shape != class_data.shape:
            raise ValueError(
                f"Alignment failed: class={class_data.shape}, ref={ref_aligned.shape}"
            )

        return class_data, ref_aligned

    def _compute_stats_for_values(self, values):
        """Compute requested stats on a 1D numpy array of values."""
        out = {}
        if values.size == 0:
            for stat in self.stat_types:
                out[stat] = None
            return out

        for stat in self.stat_types:
            try:
                if stat == "mean":
                    out["mean"] = float(np.mean(values))
                elif stat == "sum":
                    out["sum"] = float(np.sum(values))
                elif stat == "median":
                    out["median"] = float(np.median(values))
                elif stat == "min":
                    out["min"] = float(np.min(values))
                elif stat == "max":
                    out["max"] = float(np.max(values))
                elif stat == "std":
                    out["std"] = float(np.std(values))
                else:
                    out[stat] = None
            except Exception:
                out[stat] = None
        return out

    
    def _compute_land_suitability_with_reference_layer(self, raster_path, reference_path):
        class_meaning_map = {
            1: "Very Low Suitability %",
            2: "Low Suitability %",
            3: "Medium Suitability %",
            4: "High Suitability %",
            5: "Very High Suitability %",
        }

        results = []
        with rasterio.open(raster_path) as class_src, rasterio.open(reference_path) as ref_src:
            class_data, ref_data = self._read_class_and_aligned_ref(class_src, ref_src)

            # Total pixels used for percentage calc (only valid class pixels)
            unique, counts = np.unique(class_data.compressed(), return_counts=True)
            class_counts = dict(zip(unique, counts))
            total_count = int(np.sum(counts))

            class_percentages = {
                class_meaning_map.get(cls, f"Class {int(cls)} %"): round(
                    (class_counts.get(cls, 0) / total_count * 100), 2
                )
                if total_count > 0
                else 0.0
                for cls in class_meaning_map
            }

            stats_per_class = {class_meaning_map[cls]: {} for cls in class_meaning_map}

            for cls, label in class_meaning_map.items():
                class_mask = (class_data == cls)

                # Important: combine masks so we don't include nodata from either raster
                combined = class_mask & (~class_data.mask) & (~ref_data.mask)

                values = ref_data[combined].compressed()
                stats_per_class[label] = self._compute_stats_for_values(values)

        results.append({"stat_label": class_percentages, "statistics": stats_per_class})
        return results

    def _compute_quantile_class_statistics(self, raster_path, reference_path):
        if "MESS raster file" in self.description:
            quantile_label_map = {
                1: "Most Similar (Lowest 20%)",
                2: "Low Similarity",
                3: "Moderate Similarity",
                4: "High Extrapolation",
                5: "Most Extrapolated (Top 20%)",
            }
        elif "Mahalanobis Distance raster file" in self.description:
            quantile_label_map = {
                1: "Most Similar (Lowest 20%)",
                2: "Low Distance",
                3: "Moderate Distance",
                4: "High Distance",
                5: "Least Similar (Top 20%)",
            }
        else:
            quantile_label_map = {
                1: "Lowest 20%",
                2: "Low 20–40%",
                3: "Mid 40–60%",
                4: "High 60–80%",
                5: "Top 20%",
            }

        results = []
        with rasterio.open(raster_path) as class_src, rasterio.open(reference_path) as ref_src:
            class_data, ref_data = self._read_class_and_aligned_ref(class_src, ref_src)

            unique, counts = np.unique(class_data.compressed(), return_counts=True)
            class_counts = dict(zip(unique, counts))
            total_count = int(np.sum(counts))

            class_percentages = {
                quantile_label_map.get(cls, f"Class {int(cls)}"): round(
                    (class_counts.get(cls, 0) / total_count * 100), 2
                )
                if total_count > 0
                else 0.0
                for cls in quantile_label_map
            }

            stats_per_class = {quantile_label_map[cls]: {} for cls in quantile_label_map}

            for cls, label in quantile_label_map.items():
                class_mask = (class_data == cls)
                combined = class_mask & (~class_data.mask) & (~ref_data.mask)
                values = ref_data[combined].compressed()
                stats_per_class[label] = self._compute_stats_for_values(values)

        results.append({"stat_label": class_percentages, "statistics": stats_per_class})
        return results

    def compute_statistics(self):
        raster_path = self._get_raster_path()
        reference_path = self._get_reference_path()

        if "Land suitability raster file" in self.description:
            return self._compute_land_suitability_with_reference_layer(raster_path, reference_path)
        elif "MESS raster file" in self.description or "Mahalanobis Distance raster file" in self.description:
            return self._compute_quantile_class_statistics(raster_path, reference_path)
        else:
            raise ValueError("Unsupported raster type specified.")
