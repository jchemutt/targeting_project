import os
import json
import uuid
import datetime
from collections import OrderedDict
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask
from dbfread import DBF
import geopandas as gpd
import csv
import re
import shutil
#from osgeo import gdal
from django.utils.timezone import now
from pathlib import Path
from shapely.geometry import shape, Polygon, MultiPolygon, box, mapping

from .similarity_analysis import similarity_analysis

# Ensure GDAL_DATA is set correctly
#os.environ['GDAL_DATA'] = os.environ['CONDA_PREFIX'] + r'\Library\share\gdal'
#print(f"GDAL_DATA is set to: {os.environ.get('GDAL_DATA')}")


def _is_global_dataset_path(raster_url):
    """True if raster_url sits directly under a "Global" folder (any case),
    e.g. "data/Global/x.tif" — as opposed to a country-specific path like
    "data/Africa/Kenya/x.tif". Checks directory segments only, not the
    filename, so a file literally named "global_x.tif" doesn't false-match.
    Mirrors the identical helper in land_suitability.py.
    """
    return any(part.lower() == "global" for part in Path(raster_url).parts[:-1])


class LandSimilarity:
    def __init__(self, parameters,session):
        self.label = "Land Similarity"
        self.parameters = parameters
        self.session = session
        self.spatial_ref = "EPSG:4326"  # Default spatial reference system (WGS84)
        self.ras_temp_path = self.create_unique_temp_path()
        print(f"Temporary processing path: {self.ras_temp_path}")

    @staticmethod
    def create_unique_temp_path():
        """
        Create a unique temporary path for processing.
        """
        base_path = os.getcwd().replace("\\", "/")
        temp_dir = os.path.join(base_path, "media/output")
        os.makedirs(temp_dir, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        unique_id = uuid.uuid4().hex
        temp_path = os.path.join(temp_dir, f"processing_{timestamp}_{unique_id}")
        os.makedirs(temp_path, exist_ok=True)
        print(f"Created unique temp path: {temp_path}")
        return temp_path

    def prepare_value_table(self):
        """
        Prepare the value table from selected raster files.
        """
        value_table = OrderedDict()
        for idx, path in enumerate(self.parameters.get('selectedFiles', [])):
            if re.search(r'\d+', str(idx)):
                raster_path = os.path.join('data', path.lstrip('/'))
                print(f"Adding raster to value table: {raster_path}")
                value_table[idx] = [raster_path]
        return value_table.values()

    @staticmethod
    def reproject_points(points_gdf, target_crs):
        """
        Reproject a GeoDataFrame to a target CRS.
        """
        print(f"Reprojecting points to CRS: {target_crs}")
        points_gdf = points_gdf.to_crs(target_crs)
        return points_gdf


    def get_extent_from_aoi(self, aoi_input):
        """
        Convert AOI input (GeoJSON dict/string, or a comma-separated
        bounding box string) into a shapely Polygon/MultiPolygon in
        EPSG:4326. Mirrors LandSuitability.get_extent_from_aoi for
        consistency between the two tools.
        """
        if isinstance(aoi_input, dict):
            if "type" in aoi_input:
                if aoi_input["type"] == "Feature":
                    geometry = aoi_input.get("geometry", None)
                    if not geometry:
                        raise ValueError("GeoJSON Feature does not contain geometry.")
                    return shape(geometry)
                elif aoi_input["type"] in {"Polygon", "MultiPolygon"}:
                    return shape(aoi_input)
                else:
                    raise ValueError(f"Unsupported GeoJSON type: {aoi_input['type']}")
            else:
                raise ValueError("Invalid GeoJSON format: Missing 'type'.")
        elif isinstance(aoi_input, str):
            coords = [float(x) for x in aoi_input.split(",")]
            if len(coords) == 4:
                return box(coords[1], coords[0], coords[3], coords[2])
            raise ValueError("Invalid AOI bounding box string.")
        raise TypeError("Unsupported AOI input type.")

    def sample_rasters(self, rasters, points_gdf, output_csv_path):
        """
        Sample raster values at point locations and write them as a CSV
        with one column per raster (column name = raster file stem,
        matching `band_names` in similarity_analysis.stack_rasters_to_template
        so the threshold statistics align correctly with the per-pixel
        raster stack).

        A point is excluded (left as NaN) from a given raster's column if
        it falls outside that raster's actual extent, or lands on a
        NoData pixel — determined by an EXPLICIT bounds/NoData check
        rather than relying on IndexError. rasterio's `.index()` does NOT
        bounds-check: it just applies the affine transform, so an
        out-of-bounds point can still produce a row/col that is a "valid"
        (if wrong) numpy index via negative-index wraparound — e.g. a
        point south of a raster's extent could compute row=-2, and
        `array[-2, col]` silently returns a pixel from the OPPOSITE edge
        of the raster rather than raising. That meant points outside a
        dataset's actual coverage could previously be silently sampled
        from the wrong location instead of being excluded, corrupting the
        similarity statistics without any indication anything was wrong.

        Parameters:
            rasters (list[str]): raster file paths.
            points_gdf (GeoDataFrame): sample points (already reprojected
                to the rasters' CRS).
            output_csv_path (str): where to write the resulting CSV.

        Returns:
            list[str]: human-readable warnings, one per raster that had
            any point excluded or could not be sampled at all.
        """
        print(f"Sampling rasters from: {rasters}")
        print(f"Sampling {len(points_gdf)} point(s)")
        print(f"Output path: {output_csv_path}")

        n_points = len(points_gdf)
        columns = {}
        warnings = []

        for raster_path in rasters:
            col_name = Path(raster_path).stem
            values = np.full(n_points, np.nan, dtype="float64")
            excluded_out_of_bounds = 0
            excluded_nodata = 0
            try:
                with rasterio.open(raster_path) as src:
                    print(f"Processing raster: {raster_path}")
                    band = src.read(1)
                    height, width = band.shape
                    nodata = src.nodata
                    band_is_float = np.issubdtype(band.dtype, np.floating)
                    for i, point in enumerate(points_gdf.geometry):
                        row, col = src.index(point.x, point.y)
                        if not (0 <= row < height and 0 <= col < width):
                            print(f"Point {point} is outside raster bounds: {raster_path}")
                            excluded_out_of_bounds += 1
                            continue
                        sample_value = band[row, col]
                        # An undeclared NaN fill (no `nodata` tag on the
                        # file, but real NaN pixels — common for climate
                        # layers) must be excluded here too, or that NaN
                        # sample value flows straight into the threshold
                        # CSV and contaminates the Mahalanobis/MESS mean
                        # and covariance for every point, not just this
                        # one.
                        is_nan = band_is_float and np.isnan(sample_value)
                        if is_nan or (nodata is not None and sample_value == nodata):
                            excluded_nodata += 1
                            continue
                        values[i] = sample_value
            except Exception as e:
                print(f"Error processing raster {raster_path}: {e}")
                warnings.append(f"{os.path.basename(raster_path)}: could not be sampled ({e}).")

            columns[col_name] = values

            total_excluded = excluded_out_of_bounds + excluded_nodata
            if total_excluded:
                parts = []
                if excluded_out_of_bounds:
                    parts.append(f"{excluded_out_of_bounds} outside its extent")
                if excluded_nodata:
                    parts.append(f"{excluded_nodata} on NoData pixels")
                warnings.append(
                    f"{os.path.basename(raster_path)}: {total_excluded} of "
                    f"{n_points} point(s) excluded ({', '.join(parts)})."
                )

        sample_df = pd.DataFrame(columns)
        if sample_df.notna().to_numpy().any():
            print("Writing sampled data to CSV...")
            sample_df.to_csv(output_csv_path, index=False)
            print(f"Sampled CSV written to: {output_csv_path}")
        else:
            print("No sampled data available. Skipping CSV creation.")

        return warnings

    def write_csv_from_dbf(self, dbf_path, csv_path):
        """
        Convert a DBF file to a CSV format.

        Not used by the main execute() pipeline anymore — sample_rasters()
        now writes temp.csv directly (DBF field names are capped at 10
        characters, which risked truncating/colliding with raster file
        names once sampling switched to one column per raster). Left here
        in case anything else needs a DBF-to-CSV utility.
        """

        try:
            with open(csv_path, 'w', newline='') as csv_file:
                db = DBF(dbf_path)
                writer = csv.writer(csv_file)
                writer.writerow(db.field_names)  # Write headers
                for record in db:
                    writer.writerow(list(record.values()))  # Write each record
        except Exception as e:
            print(f"Error converting DBF to CSV: {e}")
            raise
    def store_metadata_in_session(self, file_metadata):
            """
            Store file metadata in the session.
            """
            if "generated_files" not in self.session:
                self.session["generated_files"] = []

            self.session["generated_files"].append(file_metadata)
            # Ensure session is saved
            self.session.modified = True
            
    def execute(self):
        """
        Execute the Land Similarity analysis process.
        """
        try:
            print("Starting execution...")
            value_table = self.prepare_value_table()
            rasters = [v[0] for v in value_table]

            print(f"Raster files to be processed: {rasters}")

            # === Extract base path from the first raster ===
            raster_base_path = None
            if rasters:
                # When a Global dataset is combined with a country-specific
                # one, prefer the country's folder — whichever raster
                # happened to be added first in the UI isn't a meaningful
                # signal, and a country's own reference layers are the
                # more useful/specific set for Land Statistics to offer
                # than falling back to Global just because a global layer
                # was selected first.
                first_raster_path = next(
                    (u for u in rasters if not _is_global_dataset_path(u)),
                    rasters[0],
                )
                # See the identical fix/comment in land_suitability.py's
                # execute(): "first 3 path parts" broke for Global
                # datasets ("data/Global/x.tif" is only 3 parts total, so
                # that heuristic swallowed the filename as if it were a
                # directory) — get_reference_layers would then try to
                # os.listdir() a .tif file and crash. The raster's own
                # parent directory is correct at any nesting depth.
                raster_base_path = str(Path(first_raster_path).parent)

                print(f"Raster base path extracted: {raster_base_path}")
            else:
                print("No raster files found for similarity analysis.")
            gdf = gpd.read_file(json.dumps(self.parameters.get('in_point')))
            raster_crs = rasterio.open(rasters[0]).crs
            print(f"Raster CRS: {raster_crs}")
            gdf = self.reproject_points(gdf, raster_crs)

            print("Sampling rasters...")
            sample_csv_path = os.path.join(self.ras_temp_path, "temp.csv")
            point_warnings = self.sample_rasters(rasters, gdf, sample_csv_path)
            print(f"Sampled data saved to: {sample_csv_path}")

            temp_csv_path = os.path.join(self.ras_temp_path, "temp.csv")
            if not os.path.exists(temp_csv_path):
                raise FileNotFoundError(f"temp.csv not found: {temp_csv_path}")

            print("Calling similarity_analysis...")
            aoi_input = self.parameters.get('out_extent')
            aoi_geojson = None
            if aoi_input:
                aoi_geom = self.get_extent_from_aoi(aoi_input)
                aoi_geojson = mapping(aoi_geom)
            similarity_analysis(len(rasters), self.ras_temp_path, rasters,
                                aoi_geojson=aoi_geojson)

            mnobis_file = os.path.join(self.ras_temp_path, 'MahalanobisDist_Quantiles.tif')
            mess_file = os.path.join(self.ras_temp_path, 'MESS_Quantiles.tif')
            print(f"Checking output files: {mnobis_file}, {mess_file}")
            workspace_path = os.getcwd().replace("\\", "/")
            media_dir = os.path.join(workspace_path, "media")

            def generate_relative_path(file_path):
                if os.path.exists(file_path):
                    rel_path = os.path.relpath(file_path, media_dir).replace("\\", "/")
                    return f"/media/{rel_path}"
                print(f"File not found: {file_path}")
                return None

            result_relative_mnobis_ras_url = generate_relative_path(mnobis_file)
            result_relative_mess_ras_url = generate_relative_path(mess_file)

            if result_relative_mnobis_ras_url:
                self.store_metadata_in_session({
                    "file_path": result_relative_mnobis_ras_url,
                    "country": raster_base_path,
                    "created_at": now().isoformat(),
                    "description": "Mahalanobis Distance raster file",
                    "title":self.parameters.get('description')+" Mahalanobis",
                })

            if result_relative_mess_ras_url:
                self.store_metadata_in_session({
                    "file_path": result_relative_mess_ras_url,
                    "country": raster_base_path,
                    "created_at": now().isoformat(),
                    "description": "MESS raster file",
                    "title":self.parameters.get('description')+" MESS",
                })

            self.cleanup_intermediate_files(keep_files=[mnobis_file, mess_file])
            return {
                "Mahalanobis": result_relative_mnobis_ras_url,
                "MESS": result_relative_mess_ras_url,
                # Sample points that fell outside a dataset's coverage
                # (extent or NoData) and were excluded, per raster — so a
                # result is transparent about which points didn't
                # contribute rather than silently dropping or (worse,
                # previously) mis-sampling them.
                "point_warnings": point_warnings,
            }
        except Exception as e:
            print(f"Error during execution: {e}")
            return None
        
    def cleanup_intermediate_files(self, keep_files=None):
        """
        Delete all files in self.ras_temp_path except those explicitly listed in keep_files.

        Parameters:
            keep_files (list[str]): List of absolute file paths that should be preserved.
        """
        if keep_files is None:
            keep_files = []

        keep_set = {os.path.abspath(p) for p in keep_files if p}
        base_dir = os.path.abspath(self.ras_temp_path)

        for root, dirs, files in os.walk(base_dir):
            for fname in files:
                fpath = os.path.abspath(os.path.join(root, fname))
                if fpath in keep_set:
                    continue
                try:
                    os.remove(fpath)
                    print(f"Deleted intermediate file: {fpath}")
                except Exception as e:
                    print(f"Could not delete {fpath}: {e}")

        # Optional: remove empty subdirs (but keep main ras_temp_path directory)
        for root, dirs, _ in os.walk(base_dir, topdown=False):
            for d in dirs:
                dpath = os.path.join(root, d)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                        print(f"Removed empty directory: {dpath}")
                except Exception as e:
                    print(f"Could not remove directory {dpath}: {e}")

"""
# Sample parameters
sample_parameters = {
    'selectedFiles': [
        '/Africa/Ethiopia/ethiopia_annual_evapo_transpiration.tif',
        '/Africa/Ethiopia/ethiopia_annual_precipitation.tif'
    ],
    'in_point': {
        "features": [{
            "geometry": {"coordinates": [39.777003, 8.70774], "type": "Point"},
            "properties": {}, "type": "Feature"
        }],
        "type": "FeatureCollection"
    },
    'email': ''
}

# Instantiate the LandSimilarity class with the sample parameters
land_similarity = LandSimilarity(parameters=sample_parameters)

# Execute the process to perform Mahalanobis distance and MESS calculations
result = land_similarity.execute()

# Output the results
if result:
    print(f"Mahalanobis Result URL: {result['Mahalanobis']}")
    print(f"MESS Result URL: {result['MESS']}")
else:
    print("Execution failed.")
"""