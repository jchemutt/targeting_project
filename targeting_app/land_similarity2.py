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

from .similarity_analysis3 import similarity_analysis

# Ensure GDAL_DATA is set correctly
#os.environ['GDAL_DATA'] = os.environ['CONDA_PREFIX'] + r'\Library\share\gdal'
#print(f"GDAL_DATA is set to: {os.environ.get('GDAL_DATA')}")

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


    def sample_rasters(self, rasters, points_path, output_path):
        """
        Sample raster values at point locations and save as a shapefile.
        """
        print(f"Sampling rasters from: {rasters}")
        print(f"Using points from: {points_path}")
        print(f"Output path: {output_path}")

        sampled_data = []
        points_gdf = gpd.read_file(points_path)
        print(f"Loaded points GeoDataFrame: {points_gdf.head()}")

        # Initialize sampled points list
        sampled_points = []

        # Sample raster values at the points
        for raster_path in rasters:
            try:
                with rasterio.open(raster_path) as src:
                    print(f"Processing raster: {raster_path}")
                    for point in points_gdf.geometry:
                        try:
                            row, col = src.index(point.x, point.y)
                            sample_value = src.read(1)[row, col]
                            sampled_data.append(sample_value)
                            sampled_points.append(point)  # geometry for this sample
                        except IndexError:
                            print(f"Point {point} is outside raster bounds.")
                            sampled_data.append(np.nan)
                            sampled_points.append(point)  # <-- add this line
            except Exception as e:
                print(f"Error processing raster {raster_path}: {e}")


        # Save sampled data to shapefile if samples exist
        if sampled_data:
            print("Writing sampled data to shapefile...")
            sample_df = pd.DataFrame({'values': sampled_data})
            sample_gdf = gpd.GeoDataFrame(sample_df, geometry=sampled_points, crs=points_gdf.crs)
            sample_gdf.to_file(output_path, driver='ESRI Shapefile')
            print(f"Sampled shapefile written to: {output_path}")
        else:
            print("No sampled data available. Skipping shapefile creation.")

    def write_csv_from_dbf(self, dbf_path, csv_path):
        """
        Convert a DBF file to a CSV format.
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
                first_raster_path = rasters[0]
                path_parts = Path(first_raster_path).parts
                if len(path_parts) >= 3:
                    raster_base_path = str(Path(*path_parts[:3]))  # Top 3 directory levels
                else:
                    raster_base_path = str(Path(first_raster_path).parent)  # Fallback

                print(f"Raster base path extracted: {raster_base_path}")
            else:
                print("No raster files found for similarity analysis.")
            gdf = gpd.read_file(json.dumps(self.parameters.get('in_point')))
            raster_crs = rasterio.open(rasters[0]).crs
            print(f"Raster CRS: {raster_crs}")
            gdf = self.reproject_points(gdf, raster_crs)

            in_fc_pt = os.path.join(self.ras_temp_path, "input_points.shp")
            print(f"Saving reprojected points to: {in_fc_pt}")
            gdf.to_file(in_fc_pt)

            print("Sampling rasters...")
            sample_shapefile_path = os.path.join(self.ras_temp_path, "temp_sample.shp")
            self.sample_rasters(rasters, in_fc_pt, sample_shapefile_path)
            print(f"Sampled data saved to: {sample_shapefile_path}")

            # Define paths for DBF and CSV
            sample_dbf_path = os.path.join(self.ras_temp_path, "temp_sample.dbf")
            sample_csv_path = os.path.join(self.ras_temp_path, "temp.csv")

            # Write the DBF file to a CSV
            self.write_csv_from_dbf(sample_dbf_path, sample_csv_path)

            temp_csv_path = os.path.join(self.ras_temp_path, "temp.csv")
            if not os.path.exists(temp_csv_path):
                raise FileNotFoundError(f"temp.csv not found: {temp_csv_path}")

            print("Calling similarity_analysis...")
            similarity_analysis(len(rasters), self.ras_temp_path, rasters)

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
                "MESS": result_relative_mess_ras_url
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