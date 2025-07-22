import geopandas as gpd
import numpy as np
import rasterstats
import rasterio
from pathlib import Path
from shapely.geometry import box
from django.conf import settings


class LandStatistics:
    def __init__(self, boundaries_geojson, raster_file, description, zone_id_column):
        self.boundaries_geojson = boundaries_geojson
        self.raster_file = raster_file
        self.description = description
        self.zone_id_column = zone_id_column

    def _load_boundaries(self):
        try:
            boundaries = gpd.GeoDataFrame.from_features(self.boundaries_geojson['features'])
            boundaries = boundaries.set_crs("EPSG:4326")
            return boundaries
        except Exception as e:
            raise ValueError(f"Error parsing boundaries: {str(e)}")

    def _get_raster_path(self):
        raster_path = Path(settings.MEDIA_ROOT) / self.raster_file.lstrip('/media/')
        if not raster_path.exists():
            raise FileNotFoundError(f"Raster file not found at {raster_path}")
        return raster_path

    def _validate_raster_and_boundaries(self, boundaries):
        raster_path = self._get_raster_path()

        with rasterio.open(raster_path) as src:
            raster_crs = src.crs
            raster_bounds = src.bounds

        boundaries = boundaries.to_crs(raster_crs)
        raster_box = box(raster_bounds.left, raster_bounds.bottom, raster_bounds.right, raster_bounds.top)

        if not boundaries.intersects(raster_box).any():
            raise ValueError("Boundaries do not intersect with the raster extent.")

        return boundaries, raster_path

    def _compute_land_suitability(self, boundaries, raster_path):
        # Define a mapping of class numbers to meaningful names
        class_meaning_map = {
            1: "Very Low Suitability %",
            2: "Low Suitability %",
            3: "Medium Suitability %",
            4: "High Suitability %",
            5: "Very High Suitability %"
        }

        results = []
        with rasterio.open(raster_path) as src:
            for _, row in boundaries.iterrows():
                mask = row.geometry
                out_image, out_transform = rasterio.mask.mask(src, [mask], crop=True, nodata=np.nan)
                data = out_image[0]

                # Compute class counts
                unique, counts = np.unique(data[~np.isnan(data)], return_counts=True)
                class_counts = dict(zip(unique, counts))
                total_count = sum(counts)

                # Compute class percentages and apply rounding
                class_percentages = {
                    class_meaning_map[cls]: round((class_counts.get(cls, 0) / total_count * 100), 2)
                    if total_count > 0 else 0.0
                    for cls in class_meaning_map
                }

                # Convert to native Python types for JSON serialization
                native_class_percentages = {
                    key: float(value) for key, value in class_percentages.items()
                }

                results.append({
                    "zone_id": str(row[self.zone_id_column]),  # Ensure zone_id is serializable
                    **native_class_percentages
                })

        return results



    def _compute_other_raster_types(self, boundaries, raster_path):
        try:
            zonal_stats_result = rasterstats.zonal_stats(
                boundaries,
                str(raster_path),
                stats=["count", "mean", "min", "max", "std"],
                geojson_out=True,
                nodata=np.nan
            )
        except Exception as e:
            raise ValueError(f"Error performing zonal statistics: {str(e)}")

        results = []
        for zone in zonal_stats_result:
            zone_id = zone['properties'].get(self.zone_id_column, 'Unknown')
            results.append({
                "zone_id": zone_id,
                "count": int(zone['properties'].get("count", 0)),
                "mean": float(zone['properties'].get("mean", 0)),
                "min": float(zone['properties'].get("min", 0)),
                "max": float(zone['properties'].get("max", 0)),
                "std": float(zone['properties'].get("std", 0)),
            })

        return results

    def compute_statistics(self):
        boundaries = self._load_boundaries()
        boundaries, raster_path = self._validate_raster_and_boundaries(boundaries)

        if "Land suitability raster file" in self.description:
            return self._compute_land_suitability(boundaries, raster_path)
        elif "MESS raster file" in self.description or "Mahalanobis Distance raster file" in self.description:
            return self._compute_other_raster_types(boundaries, raster_path)
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
