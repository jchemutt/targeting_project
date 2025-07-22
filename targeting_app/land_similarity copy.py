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
from osgeo import gdal
import csv
import re

from .similarity_analysis import similarity_analysis

# Ensure GDAL_DATA environment variable is set correctly
os.environ['GDAL_DATA'] = os.environ['CONDA_PREFIX'] + r'\Library\share\gdal'
print(f"GDAL_DATA is set to: {os.environ.get('GDAL_DATA')}")

class LandSimilarity:
    def __init__(self, parameters):
        self.label = "Land Similarity"
        self.description = ""
        self.canRunInBackground = False
        self.value_table_cols = 6
        self.spatial_ref = "EPSG:4326"  # Assuming WGS84
        self.parameters = parameters
        self.ras_temp_path = self.create_unique_temp_path()

    def create_unique_temp_path(self):
        workspace_path = os.getcwd().replace("\\", "/")
        media_dir = os.path.join(workspace_path, "media")
        if not os.path.exists(media_dir):
            os.makedirs(media_dir)

        output_dir = os.path.join(media_dir, "output")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        unique_id = uuid.uuid4().hex
        temp_path = os.path.join(output_dir, f"processing_{timestamp}_{unique_id}")
        if not os.path.exists(temp_path):
            os.makedirs(temp_path)
        return temp_path

    def get_value_table_count(self, parameters):
        return len(parameters['selectedFiles'])

    def prepare_value_table(self, parameters):
        value_table = OrderedDict()
        for idx, path in enumerate(parameters['selectedFiles']):
            if re.search(r'\d+', str(idx)) is None:
                continue

            if idx not in value_table.keys():
                value_table[idx] = []
            value_table[idx].append(os.path.join('data', path.lstrip('/')))
        return value_table.values()

    def updateParameters(self, parameters):
        return

    def get_min_cell_size(self, images):
        ras_cell_size = {}
        extent_array = []

        for img in images:
            if isinstance(img, list):
                img = img[0]
            img = img.replace("'", "").strip()
            with rasterio.open(img) as src:
                cellsize = src.res[0]
                ras_cell_size[img] = cellsize
                extent = src.bounds
                extent_ls = [extent.left, extent.bottom, extent.right, extent.top]
                extent_array.append(extent_ls)

        min_extent = list(np.amin(np.array(extent_array), axis=0))
        min_size = min(ras_cell_size.values())
        source_cell_ras = None
        source_ext_ras = None
        diff_cell_raster = []
        diff_ext_raster = []

        output = {
            'diff_cell_raster': [],
            'diff_ext_raster': [],
            'source_ext_ras': None,
            'source_cell_ras': None,
            'min_extent': min_extent,
            'min_size': min_size
        }

        for img in images:
            if isinstance(img, list):
                img = img[0]
            img = img.replace("'", "").strip()
            with rasterio.open(img) as src:
                cellsize = src.res[0]
                extent = src.bounds
                extent_ls = [extent.left, extent.bottom, extent.right, extent.top]
                if extent_ls != min_extent:
                    output['diff_ext_raster'].append(img)
                else:
                    output['source_ext_ras'] = img

                if min_size != cellsize:
                    output['diff_cell_raster'].append(img)
                else:
                    output['source_cell_ras'] = img

        return output

    def equalize_raster(self, infos, all_rasters, processing_path):
        processed_raster = []
        for raster in all_rasters:
            if isinstance(raster, list):
                raster = raster[0]
            raster = raster.replace("'", "").strip()

            clip_name = f'clip_{os.path.basename(raster)}'
            res_name = f'res_{os.path.basename(raster)}'

            clip_raster = os.path.join(processing_path, clip_name)
            res_raster = os.path.join(processing_path, res_name)

            with rasterio.open(raster) as src:
                out_image, out_transform = mask(src, [infos['min_extent']], crop=True)
                out_meta = src.meta.copy()
                out_meta.update({"driver": "GTiff",
                                 "height": out_image.shape[1],
                                 "width": out_image.shape[2],
                                 "transform": out_transform})
                with rasterio.open(clip_raster, "w", **out_meta) as dest:
                    dest.write(out_image)

            gdal.Warp(res_raster, clip_raster, xRes=infos['min_size'], yRes=infos['min_size'], resampleAlg='nearest')

            processed_raster.append(res_raster)
        return processed_raster

    def reproject_points(self, points_gdf, target_crs):
        points_gdf = points_gdf.to_crs(epsg=4326)  # Assuming input points are in WGS84
        points_gdf = points_gdf.to_crs(target_crs)
        return points_gdf

    def execute(self):
        out_mnobis_ras = 'Mahalanobis_Raster.tif'
        out_mess_ras = 'MESS_Raster.tif'

        out_mnobis_ras_path = os.path.join(self.ras_temp_path, out_mnobis_ras)
        out_mess_ras_path = os.path.join(self.ras_temp_path, out_mess_ras)

        if not os.path.exists(self.ras_temp_path):
            os.makedirs(self.ras_temp_path)

        json_file = os.path.join(self.ras_temp_path, 'in_point.json')
        with open(json_file, 'w') as the_file:
            the_file.write(json.dumps(self.parameters['in_point'], indent=4))
        in_fc_pt = os.path.join(self.ras_temp_path, "in_point.shp")

        gdf = gpd.read_file(json_file)

        print("Input GeoDataFrame:")
        print(gdf.head())

        value_table = self.prepare_value_table(self.parameters)
        print("Prepared Value Table:")
        print(value_table)

        if not value_table:
            raise ValueError("No raster files provided in the parameters.")

        first_raster_path = list(value_table)[0][0]
        with rasterio.open(first_raster_path) as src:
            raster_crs = src.crs
            print(f"Raster CRS: {raster_crs}")
            gdf = self.reproject_points(gdf, raster_crs)

        gdf.to_file(in_fc_pt)

        if 'out_extent' in self.parameters.keys():
            if not isinstance(self.parameters['out_extent'], OrderedDict):
                in_fc = self.parameters['out_extent']
            else:
                json_file = os.path.join(self.ras_temp_path, 'extent.json')
                with open(json_file, 'w') as the_file:
                    the_file.write(json.dumps(self.parameters['out_extent'], indent=4))

                in_fc = os.path.join(self.ras_temp_path, "extent.shp")
                extent_gdf = gpd.read_file(json_file)
                extent_gdf.to_file(in_fc)
        else:
            in_fc = None

        try:
            self.createValueSample(self.parameters, in_fc_pt, self.ras_temp_path, in_fc, extent=None)
        except Exception as e:
            print(f"Error creating value sample: {e}")
            return None, None

        try:
            similarity_analysis(self.get_value_table_count(self.parameters), self.ras_temp_path)
        except Exception as e:
            print(f"Error in similarity_analysis: {e}")
            return None, None

        mnobis_file = os.path.join(self.ras_temp_path, 'MahalanobisDist.asc')
        mess_file = os.path.join(self.ras_temp_path, 'MESS.asc')
        workspace_path = os.getcwd().replace("\\", "/")
        media_dir = os.path.join(workspace_path, "media")

        if os.path.exists(mnobis_file):
            mnobis_ras = mnobis_file
            relative_mnobis_ras_path = os.path.relpath(mnobis_ras, media_dir)
            relative_mnobis_ras_path = relative_mnobis_ras_path.replace("\\", "/")
            result_relative_mnobis_ras_url = f"/media/{relative_mnobis_ras_path}"
        else:
            result_relative_mnobis_ras_url = None

        if os.path.exists(mess_file):
            mess_ras = mess_file
            relative_mess_ras_path = os.path.relpath(mess_ras, media_dir)
            relative_mess_ras_path = relative_mess_ras_path.replace("\\", "/")
            result_relative_mess_ras_url = f"/media/{relative_mess_ras_path}"
        else:
            result_relative_mess_ras_url = None

        return result_relative_mnobis_ras_url, result_relative_mess_ras_url

    def write_csv_from_dbf(self, in_dbf, out_csv):
        with open(out_csv, 'w', newline='') as csvf:
            db = DBF(in_dbf)
            writer = csv.writer(csvf)
            writer.writerow(db.field_names)
            for record in db:
                writer.writerow(list(record.values()))
        print("Finished writing to CSV")
        print("Checking if temp.csv exists and its content:")
        if os.path.exists(out_csv):
            print(f"temp.csv exists: {out_csv}")
            with open(out_csv, 'r') as f:
                print(f.read())

    def createValueSample(self, parameters, in_fc_pt, ras_temp_path, in_fc, extent):
        in_val_raster = list(self.prepare_value_table(parameters))
        num_rows = self.get_value_table_count(parameters)
        first_in_raster = in_val_raster[0][0] if num_rows > 0 else None
        sample_in_ras = []
        equalize = False

        for row_count, in_ras_file in enumerate(in_val_raster):
            rast = in_ras_file[0]
            if 'user' in rast.lower():
                equalize = True
                break

        if equalize:
            raster_infos = self.get_min_cell_size(in_val_raster)
            in_val_raster = self.equalize_raster(raster_infos, in_val_raster, ras_temp_path)

        for row_count, in_ras_file in enumerate(in_val_raster):
            if isinstance(in_ras_file, list):
                in_ras_file = in_ras_file[0]

            i = row_count + 1
            if extent is not None:
                try:
                    clip_raster = os.path.join(ras_temp_path, f"mask_{i}.tif")
                    with rasterio.open(in_ras_file) as src:
                        out_image, out_transform = mask(src, [extent], crop=True)
                        out_meta = src.meta.copy()
                        out_meta.update({"driver": "GTiff",
                                         "height": out_image.shape[1],
                                         "width": out_image.shape[2],
                                         "transform": out_transform})
                        with rasterio.open(clip_raster, "w", **out_meta) as dest:
                            dest.write(out_image)
                    sample_ras = self.convertRasterToASCII(num_rows, ras_temp_path, i, clip_raster, clip_raster)
                    sample_in_ras.append(sample_ras)
                except Exception as ex:
                    print(f"Error clipping raster: {ex}")
                    sample_ras = self.convertRasterToASCII(num_rows, ras_temp_path, i, in_ras_file, in_ras_file)
                    sample_in_ras.append(sample_ras)
            else:
                sample_ras = self.convertRasterToASCII(num_rows, ras_temp_path, i, in_ras_file, in_ras_file)
                sample_in_ras.append(sample_ras)

        sample_output = os.path.join(ras_temp_path, "temp.dbf")
        self.sample_rasters(sample_in_ras, in_fc_pt, sample_output)

        # Write the DBF to CSV
        sample_output_csv = os.path.join(ras_temp_path, "temp.csv")
        self.write_csv_from_dbf(sample_output, sample_output_csv)

    def convertRasterToASCII(self, num_rows, ras_temp_path, i, first_in_raster, in_raster):
        output_ascii_path = os.path.join(ras_temp_path, f"tempAscii_{i}.asc")
        
        translate_options = gdal.TranslateOptions(format="AAIGrid", creationOptions=["FORCE_CELLSIZE=TRUE"])
        
        try:
            if num_rows > 1:
                if i == 1:
                    sample_ras = first_in_raster
                    gdal.Translate(output_ascii_path, first_in_raster, options=translate_options)
                else:
                    sample_ras = in_raster
                    gdal.Translate(output_ascii_path, sample_ras, options=translate_options)
            else:
                sample_ras = in_raster
                gdal.Translate(output_ascii_path, in_raster, options=translate_options)
            
            print(f"Successfully converted raster to ASCII: {output_ascii_path}")
            return sample_ras
        except Exception as e:
            print(f"Error converting raster to ASCII: {e}")
            raise

    def sample_rasters(self, rasters, points, output):
        sampled_data = []
        sampled_points = []
        points_gdf = gpd.read_file(points)
        # Get the CRS from the first raster
        with rasterio.open(rasters[0]) as src:
            raster_crs = src.crs
        for raster_path in rasters:
            print(f"Processing raster: {raster_path}")
            with rasterio.open(raster_path) as src:
                for point in points_gdf.geometry:
                    print(f"Processing geometry: {point}")
                    try:
                        row, col = src.index(point.x, point.y)
                        sample_value = src.read(1)[row, col]
                        print(f"Sample value: {sample_value}")
                        sampled_data.append(sample_value)
                        sampled_points.append(point)
                    except IndexError:
                        print("Error sampling geometry: Input shapes do not overlap raster.")
                        sampled_data.append(np.nan)

        if sampled_data:
            sample_df = pd.DataFrame(sampled_data, columns=['values'])
            sample_gdf = gpd.GeoDataFrame(sample_df, geometry=sampled_points, crs=raster_crs)
            sample_output_dbf = output.replace('.dbf', '.shp')
            sample_gdf.to_file(sample_output_dbf, driver='ESRI Shapefile')
        else:
            print("No samples obtained from the rasters. Skipping GeoDataFrame creation.")


