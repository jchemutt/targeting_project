import geopandas as gpd
import numpy as np
import rasterstats
import rasterio
from pathlib import Path
from shapely.geometry import box
from django.conf import settings


class LandStatistics:
    def __init__(self, reference_layer, raster_file, description,stat_types=None):
        self.reference_layer = reference_layer
        self.raster_file = raster_file
        self.description = description
        self.stat_types = stat_types or ["mean"] 


    def _get_raster_path(self):
        raster_path = Path(settings.MEDIA_ROOT) / self.raster_file.lstrip('/media/')
        if not raster_path.exists():
            raise FileNotFoundError(f"Raster file not found at {raster_path}")
        return raster_path
    
    def _get_reference_path(self):
        reference_path = Path(settings.BASE_DIR) / self.reference_layer
        if not reference_path.exists():
            raise FileNotFoundError(f"Reference file not found at {reference_path}")
        return reference_path

    

        
    def _compute_land_suitability_with_reference_layer(self, raster_path, reference_path):
        """Computes dynamic land suitability statistics per class with reference layer."""

        class_meaning_map = {
            1: "Very Low Suitability %",
            2: "Low Suitability %",
            3: "Medium Suitability %",
            4: "High Suitability %",
            5: "Very High Suitability %",
        }

        results = []
        with rasterio.open(raster_path) as class_src, rasterio.open(reference_path) as ref_src:
            class_data = class_src.read(1, masked=True)
            ref_data = ref_src.read(1, masked=True)

            # Total pixels used for percentage calc
            unique, counts = np.unique(class_data.compressed(), return_counts=True)
            class_counts = dict(zip(unique, counts))
            total_count = sum(counts)

            class_percentages = {
                class_meaning_map.get(cls, f"Class {int(cls)} %"): round((class_counts.get(cls, 0) / total_count * 100), 2)
                if total_count > 0 else 0.0
                for cls in class_meaning_map
            }

            # Initialize dictionary for dynamic stats per class
            stats_per_class = {class_meaning_map.get(cls, f"Class {int(cls)} %"): {} for cls in class_meaning_map}

            for cls in class_meaning_map:
                class_mask = class_data == cls
                values = ref_data[class_mask].compressed()  # Masked array -> array

                if values.size == 0:
                    for stat in self.stat_types:
                        stats_per_class[class_meaning_map[cls]][stat] = None
                else:
                    for stat in self.stat_types:
                        try:
                            if stat == "mean":
                                stats_per_class[class_meaning_map[cls]]["mean"] = float(np.mean(values))
                            elif stat == "sum":
                                stats_per_class[class_meaning_map[cls]]["sum"] = float(np.sum(values))
                            elif stat == "median":
                                stats_per_class[class_meaning_map[cls]]["median"] = float(np.median(values))
                            elif stat == "min":
                                stats_per_class[class_meaning_map[cls]]["min"] = float(np.min(values))
                            elif stat == "max":
                                stats_per_class[class_meaning_map[cls]]["max"] = float(np.max(values))
                            elif stat == "std":
                                stats_per_class[class_meaning_map[cls]]["std"] = float(np.std(values))
                            else:
                                stats_per_class[class_meaning_map[cls]][stat] = None
                        except Exception:
                            stats_per_class[class_meaning_map[cls]][stat] = None

        results.append({
            "stat_label": class_percentages,
            "statistics": stats_per_class
        })

        return results
            

    def _compute_quantile_class_statistics(self, raster_path, reference_path):
        """
        Computes summary statistics for already quantile-classified raster using reference zones.
        """
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
            class_data = class_src.read(1, masked=True)
            ref_data = ref_src.read(1, masked=True)

            unique, counts = np.unique(class_data.compressed(), return_counts=True)
            class_counts = dict(zip(unique, counts))
            total_count = sum(counts)

            class_percentages = {
                quantile_label_map.get(cls, f"Class {int(cls)}"): round((class_counts.get(cls, 0) / total_count * 100), 2)
                if total_count > 0 else 0.0
                for cls in quantile_label_map
            }

            stats_per_class = {quantile_label_map.get(cls, f"Class {int(cls)}"): {} for cls in quantile_label_map}

            for cls in quantile_label_map:
                class_mask = class_data == cls
                values = ref_data[class_mask].compressed()

                for stat in self.stat_types:
                    try:
                        if values.size == 0:
                            stats_per_class[quantile_label_map[cls]][stat] = None
                        elif stat == "mean":
                            stats_per_class[quantile_label_map[cls]]["mean"] = float(np.mean(values))
                        elif stat == "sum":
                            stats_per_class[quantile_label_map[cls]]["sum"] = float(np.sum(values))
                        elif stat == "median":
                            stats_per_class[quantile_label_map[cls]]["median"] = float(np.median(values))
                        elif stat == "min":
                            stats_per_class[quantile_label_map[cls]]["min"] = float(np.min(values))
                        elif stat == "max":
                            stats_per_class[quantile_label_map[cls]]["max"] = float(np.max(values))
                        elif stat == "std":
                            stats_per_class[quantile_label_map[cls]]["std"] = float(np.std(values))
                        else:
                            stats_per_class[quantile_label_map[cls]][stat] = None
                    except Exception:
                        stats_per_class[quantile_label_map[cls]][stat] = None

        results.append({
            "stat_label": class_percentages,
            "statistics": stats_per_class
        })

        return results


    def _compute_quantile_statistics(self, raster_path):
        """
        Computes 5-bin quantile breakdown for Mahalanobis or MESS raster
        """
        with rasterio.open(raster_path) as src:
            data = src.read(1, masked=True).compressed()
            if data.size == 0:
                raise ValueError("No valid data found for quantile analysis")

            quantiles = np.percentile(data, [20, 40, 60, 80])
            labels = ["Lowest 20%", "Low 20%-40%", "Mid 40%-60%", "High 60%-80%", "Top 20%"]
            counts = [0, 0, 0, 0, 0]

            all_data = src.read(1, masked=True).compressed()
            for val in all_data:
                if val <= quantiles[0]:
                    counts[0] += 1
                elif val <= quantiles[1]:
                    counts[1] += 1
                elif val <= quantiles[2]:
                    counts[2] += 1
                elif val <= quantiles[3]:
                    counts[3] += 1
                else:
                    counts[4] += 1

            total = sum(counts)
            result = {label: round(counts[i] / total * 100, 2) for i, label in enumerate(labels)}

            return [{"quantiles": result}]
        
    def _compute_other_raster_types(self, raster_path):
            try:
                zonal_stats_result = rasterstats.zonal_stats(
                    str(raster_path),
                    stats=["count", "mean", "min", "max", "std"],
                    nodata=np.nan
                )
            except Exception as e:
                raise ValueError(f"Error performing zonal statistics: {str(e)}")

            results = []
            for i, zone in enumerate(zonal_stats_result):
                results.append({
                    "zone_id": f"Zone {i + 1}",
                    "count": int(zone.get("count", 0)),
                    "mean": float(zone.get("mean", 0)),
                    "min": float(zone.get("min", 0)),
                    "max": float(zone.get("max", 0)),
                    "std": float(zone.get("std", 0)),
                })

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
            
    def debug(self):
        try:
            boundaries = self._load_boundaries()
            raster_path = self._get_raster_path()
            print(f"Boundaries CRS: {boundaries.crs}")
            print(f"Raster path: {raster_path}")
            with rasterio.open(raster_path) as src:
                print(f"Raster CRS: {src.crs}")
                print(f"Raster Bounds: {src.bounds}")
        except Exception as e:
            print(f"Debugging error: {str(e)}")
