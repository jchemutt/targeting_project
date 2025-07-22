import os
import json
import numpy as np
import geopandas as gpd
import re
import time
import rasterio
import rasterio.mask
from shapely.geometry import box
from collections import OrderedDict
from pyproj import Transformer
import rasterio
from rasterio.warp import reproject, Resampling
from .reclassify import reclassify
from .main_tool import TargetingTool  

class LandSuitability(TargetingTool):
    def __init__(self, parameters):
        """Initialize the Land Suitability tool with the given parameters."""
        super().__init__()
        self.label = "Land Suitability"
        self.description = "Given a set of raster data and user optimal values, the Land Suitability tool determines the most suitable place to carry out an activity. In agriculture, it could be used to identify places with the best biophysical and socioeconomic conditions for a certain crop to do well."
        self.canRunInBackground = False
        self.value_table_cols = 6
        self.parameters = parameters

    def get_value_table_count(self, parameters):
        """Count the number of input rasters based on the parameters."""
        count = 0
        for key, value in parameters.items():
            if 'in_raster' in key:
                count += 1
        return count

    def prepare_value_table(self, parameters):
        """Organize the input parameters into an ordered dictionary for easy access during processing."""
        value_table = OrderedDict()
        for key, param in parameters.items():
            if re.search(r'\d+', key) is None:
                continue

            idx = int(re.search(r'\d+', key).group()) - 1
            if idx not in value_table.keys():
                value_table[idx] = {}
            if 'in_raster' in key:
                value_table[idx]['url'] = param['url']
            elif 'min_val' in key:
                value_table[idx]['min_val'] = param
            elif 'opti_from' in key:
                value_table[idx]['opti_from'] = param
            elif 'opti_to' in key:
                value_table[idx]['opti_to'] = param
            elif 'max_val' in key:
                value_table[idx]['max_val'] = param
            elif 'combine' in key:
                value_table[idx]['combine'] = param

        return value_table

    def output_name(self, value_table):
        """Generate a default output name for the raster."""
        return 'name1'

    def execute(self):
        """Main processing workflow for the Land Suitability tool."""
        i = 0
        ras_max_min = True
        parameters = self.parameters
        in_raster = self.prepare_value_table(parameters)
        num_rows = len(in_raster)
        out_ras = self.output_name(in_raster)

        # Define workspace and media directories
        workspace_path = os.getcwd().replace("\\", "/")
        media_dir = os.path.join(workspace_path, "media")
        if not os.path.exists(media_dir):
            os.makedirs(media_dir)
        
        output_dir = os.path.join(media_dir, "output")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        ts = str(time.time())
        ras_temp_path = os.path.join(output_dir, ts)
        
        if os.path.isdir(ras_temp_path):
            for root, dirs, files in os.walk(ras_temp_path):
                for f in files:
                    f_path = os.path.join(ras_temp_path, f)
                    if not f.endswith(('.shp', '.dbf', '.sbn', '.cpg', '.prj', '.shp.xml', '.sbx', '.shx', '.lock')):
                        os.remove(f_path)

        out_ras_path = f'{ras_temp_path}/{out_ras}'
        if not os.path.exists(ras_temp_path):
            os.makedirs(ras_temp_path)

        valid_rasters = 0

        # Check if AOI (Area of Interest) is provided and process accordingly
        if 'out_extent' in parameters.keys() and parameters['out_extent'].strip():
            in_fc = self.get_extent_from_aoi(parameters['out_extent'])
            extent = in_fc.total_bounds  # Get feature class extent
            print(f"AOI extent: {extent}")
            valid_rasters += self.raster_minus_init(in_raster, ras_max_min, ras_temp_path, in_fc, extent)
        else:
            valid_rasters += self.raster_minus_init(in_raster, ras_max_min, ras_temp_path, in_fc=None, extent=None)

        if valid_rasters == 0:
            raise ValueError("No valid rasters intersect with the AOI. Check your inputs.")

        # Initial condition checks on rasters
        self.raster_condition_init(num_rows, "ras_min1_", "ras_min2_", "ras_max1_", "ras_max2_", ras_temp_path, "<", "0")

        # Divide rasters by optimal values to normalize them
        for ras_file, min_val, max_val, opt_from_val, opt_to_val, ras_combine, row_count in self.get_row_value(in_raster, ras_max_min):
            i += 1
            self.raster_divide(opt_from_val, min_val, f"ras_min2_{i}", f"ras_min3_{i}", ras_temp_path, min_ras=True)
            self.raster_divide(opt_to_val, max_val, f"ras_max2_{i}", f"ras_max3_{i}", ras_temp_path, min_ras=False)

        # Second set of condition checks on normalized rasters
        self.raster_condition_init(num_rows, "ras_min3_", "ras_min4_", "ras_max3_", "ras_max4_", ras_temp_path, ">", "1")

        # Combine minimum and maximum values across rasters
        for j in range(num_rows):
            j += 1
            with rasterio.open(f"{ras_temp_path}ras_min4_{j}") as ras_min4, rasterio.open(f"{ras_temp_path}ras_max4_{j}") as ras_max4:
                min_data = ras_min4.read(1)
                max_data = ras_max4.read(1)
                combined = np.minimum(min_data, max_data)
                with rasterio.open(f"{ras_temp_path}ras_MnMx_{j}", 'w', **ras_min4.meta) as out_raster:
                    out_raster.write(combined, 1)

                # Prepare files for combination
        ras_temp_file, n_ras = self.set_combine_file(in_raster, ras_temp_path)

        if n_ras == 0:
            raise ValueError("n_ras is zero, cannot proceed. Check your inputs.")

        out_ras_temp = None
        meta = None
        max_raster_files = []  # List to store paths to maximum rasters

        # First, process single rasters (len(item) == 1)
        for item in ras_temp_file:
            if len(item) == 1:
                item = [f for f in item if f not in ('yes', 'no')]
                if not item:
                    continue
                f = item[0]
                with rasterio.open(f) as raster:
                    # Read only the first band (assuming a single-band raster)
                    data = raster.read(1)
                    if meta is None:
                        meta = raster.meta
                    if out_ras_temp is None:
                        out_ras_temp = data
                    else:
                        out_ras_temp *= data  # Multiply pixel values

        # Next, process groups of rasters (len(item) > 1)
        n = 0
        for item in ras_temp_file:
            if len(item) > 1:
                n += 1
                item = [f for f in item if f not in ('yes', 'no')]
                if not item:
                    continue

                # Open the first raster to use as a reference for shape
                with rasterio.open(item[0]) as ref_raster:
                    ref_shape = ref_raster.shape
                    ref_meta = ref_raster.meta

                # List to store the resampled rasters
                resampled_rasters = []

                # Resample rasters if needed
                for f in item:
                    with rasterio.open(f) as raster:
                        if raster.shape != ref_shape:
                            # Resample to match the reference raster
                            resampled_data, _ = resample_raster_to_match(f, ref_raster)
                            resampled_rasters.append(resampled_data)
                        else:
                            # Ensure you only read the first band of the raster
                            resampled_rasters.append(raster.read(1))

                # Perform the maximum operation across the resampled rasters
                max_data = np.maximum.reduce(resampled_rasters)

                # Save max_data to a temporary raster file
                max_raster_path = f"{ras_temp_path}rs_MxStat_{n}.tif"
                if meta is None:
                    meta = ref_meta
                with rasterio.open(max_raster_path, 'w', **meta) as out_raster:
                    out_raster.write(max_data, 1)
                
                # Append the file path to max_raster_files
                max_raster_files.append(max_raster_path)

        # Multiply out_ras_temp by each of the maximum rasters
        for f in max_raster_files:
            with rasterio.open(f) as raster:
                data = raster.read(1)
                if out_ras_temp is None:
                    out_ras_temp = data
                else:
                    out_ras_temp *= data

        if out_ras_temp is None:
            raise ValueError("No valid raster data found for combination.")

        # Save the final output raster
        with rasterio.open(f"{ras_temp_path}final_output.tif", 'w', **meta) as out_raster:
            out_raster.write(out_ras_temp, 1)
        # Normalize the combined raster
        out_ras_temp = out_ras_temp.astype(np.float32)
        out_ras_temp **= (1 / float(n_ras))
        with rasterio.open(out_ras_path, 'w', **meta) as out_raster:
            out_raster.write(out_ras_temp, 1)

        # Save final output
        output_path = f'{ras_temp_path}Suitability_{ts}.tif'
        output_path_un = f'{ras_temp_path}Suitability_{ts}_un.tif'
        with rasterio.open(out_ras_path) as src:
            with rasterio.open(output_path_un, 'w', **src.meta) as dst:
                dst.write(src.read(1), 1)

        # Reclassify the output raster
        reclassify(output_path_un, output_path)
        relative_output_path = os.path.relpath(output_path, media_dir)
        relative_output_path = relative_output_path.replace("\\", "/")
        result_relative_url = f"/media/{relative_output_path}"
        
        return result_relative_url
   
    def raster_minus_init(self, in_raster, ras_max_min, ras_temp_path, in_fc, extent):
        """Initialize raster subtraction for each input raster and optionally mask them to the AOI extent."""
        i = 0
        valid_rasters = 0
        for ras_file, min_val, max_val, opt_from_val, opt_to_val, ras_combine, row_count in self.get_row_value(in_raster, ras_max_min):
            i += 1
            print(f"Raster minus init for file: {ras_file}")
            if extent is not None:
                with rasterio.open(ras_file) as src:
                    # Transform the extent to the CRS of the raster
                    transformer = Transformer.from_crs("EPSG:4326", src.crs.to_string(), always_xy=True)
                    extent_transformed = transformer.transform_bounds(extent[0], extent[1], extent[2], extent[3])
                    raster_bounds = src.bounds
                    print(f"Raster bounds: {raster_bounds}")
                    if not self.bounds_intersect(raster_bounds, extent_transformed):
                        print(f"Raster {ras_file} does not intersect with AOI. Skipping masking.")
                        # Use full raster if no intersection
                        valid_rasters += 1
                        self.raster_minus(ras_file, min_val, f"ras_min1_{i}", ras_temp_path, min_ras=True)
                        self.raster_minus(ras_file, max_val, f"ras_max1_{i}", ras_temp_path, min_ras=False)
                        continue

                    try:
                        out_image, out_transform = rasterio.mask.mask(src, [box(*extent_transformed)], crop=True)
                        out_meta = src.meta
                        out_meta.update({"driver": "GTiff", "height": out_image.shape[1], "width": out_image.shape[2], "transform": out_transform, "nodata": np.nan})
                        with rasterio.open(f"{ras_temp_path}ras_mask1_{i}", "w", **out_meta) as dest:
                            dest.write(out_image)

                        self.raster_minus(f"{ras_temp_path}ras_mask1_{i}", min_val, f"ras_min1_{i}", ras_temp_path, min_ras=True)
                        self.raster_minus(f"{ras_temp_path}ras_mask1_{i}", max_val, f"ras_max1_{i}", ras_temp_path, min_ras=False)
                        os.remove(f"{ras_temp_path}ras_mask1_{i}")
                        valid_rasters += 1
                    except ValueError as e:
                        print(f"Skipping masking for raster {ras_file} due to error: {e}")
                        # Use full raster if masking fails
                        self.raster_minus(ras_file, min_val, f"ras_min1_{i}", ras_temp_path, min_ras=True)
                        self.raster_minus(ras_file, max_val, f"ras_max1_{i}", ras_temp_path, min_ras=False)
                        valid_rasters += 1
            else:
                self.raster_minus(ras_file, min_val, f"ras_min1_{i}", ras_temp_path, min_ras=True)
                self.raster_minus(ras_file, max_val, f"ras_max1_{i}", ras_temp_path, min_ras=False)
                valid_rasters += 1

        return valid_rasters

    def raster_minus(self, ras_file, val, ras_output, ras_temp_path, min_ras):
        """Subtract a value from raster data or vice versa based on the min_ras flag."""
        with rasterio.open(ras_file) as src:
            data = src.read(1)
            if min_ras:
                data = data - float(val)
            else:
                data = float(val) - data
            with rasterio.open(f"{ras_temp_path}{ras_output}", 'w', **src.meta) as out_raster:
                out_raster.write(data, 1)

    def raster_divide(self, val1, val2, ras_input, ras_output, ras_temp_path, min_ras):
        """Divide raster data by a value to normalize it."""
        with rasterio.open(f"{ras_temp_path}{ras_input}") as src:
            data = src.read(1)
            if min_ras:
                data = data / (float(val1) - float(val2))
            else:
                data = data / (float(val2) - float(val1))
            with rasterio.open(f"{ras_temp_path}{ras_output}", 'w', **src.meta) as out_raster:
                out_raster.write(data, 1)

    def raster_condition_init(self, num_rows, ras_name1, ras_name2, ras_name3, ras_name4, ras_temp_path, condition, threshold):
        """Initialize condition checks on raster data for a specified condition and threshold."""
        for i in range(num_rows):
            i += 1
            self.raster_condition(f"{ras_temp_path}{ras_name1}{i}", f"{ras_temp_path}{ras_name2}{i}", condition, threshold)
            self.raster_condition(f"{ras_temp_path}{ras_name3}{i}", f"{ras_temp_path}{ras_name4}{i}", condition, threshold)

    def raster_condition(self, ras_input, ras_output, condition, threshold):
        """Apply a condition to raster data, replacing values that meet the condition with the threshold."""
        with rasterio.open(ras_input) as src:
            data = src.read(1)
            data = np.where(eval(f"data {condition} {threshold}"), float(threshold), data)
            with rasterio.open(ras_output, 'w', **src.meta) as out_raster:
                out_raster.write(data, 1)

    def set_combine_file1(self, in_raster, ras_temp_path):
        """Build a list with paths of temporary raster files based on the combine parameter."""
        ras_temp_file = []
        n_ras = 0
        
        row_count = 0
        for _, min_val, max_val, opt_from_val, opt_to_val, ras_combine, row_count in self.get_row_value(in_raster, ras_max_min=True):
            row_count += 1
            temp_file = [f"{ras_temp_path}ras_MnMx_{row_count}"]
            
            if ras_combine.lower() == 'yes':
                temp_file.append('yes')
            
            ras_temp_file.append(temp_file)
            n_ras += 1

        return ras_temp_file, n_ras
    

    def set_combine_file(self, in_raster, ras_temp_path):
        """Build a list with lists of temporary raster files based on the combine parameter."""
        ras_temp_file = []  # List to hold groups of raster temp file paths
        current_group = []  # Current group of rasters
        n_ras = 0  # Total number of rasters

        # Retrieve the raster entries as a list
        rasters = list(self.get_row_value(in_raster, ras_max_min=True))

        for index, (ras_file, min_val, max_val, opt_from_val, opt_to_val, ras_combine, row_count) in enumerate(rasters):
            n_ras += 1  # Increment raster count

            # Create temporary file path for the raster
            temp_file_path = f"{ras_temp_path}ras_MnMx_{n_ras}"

            # Ensure the combine parameter is in lowercase
            ras_combine = ras_combine.lower()

            if index == 0:
                # First raster always starts a new group
                current_group = [temp_file_path]
            else:
                if ras_combine == 'yes':
                    # Combine with previous raster(s); add to current group
                    current_group.append(temp_file_path)
                elif ras_combine == 'no':
                    # Start a new group
                    if current_group:
                        ras_temp_file.append(current_group)
                    current_group = [temp_file_path]
                else:
                    # Handle invalid combine values
                    raise ValueError(f"Invalid combine parameter: {ras_combine}")

        # Append the last group after the loop
        if current_group:
            ras_temp_file.append(current_group)

        return ras_temp_file, n_ras


    def get_row_value(self, in_raster, ras_max_min):
        """Generator to yield values for each row in the value table."""
        for item in in_raster.values():
            row_count = 0
            ras_file = item['url']
            min_val = item['min_val']
            opt_from_val = item['opti_from']
            opt_to_val = item['opti_to']
            max_val = item['max_val']
            if 'combine' in item:
                ras_combine = item['combine']
            else:
                ras_combine = 'no'

            if ras_max_min:
                min_val = float(min_val)
                opt_from_val = float(opt_from_val)
                opt_to_val = float(opt_to_val)
                max_val = float(max_val)
            row_count += 1
            yield ras_file, min_val, max_val, opt_from_val, opt_to_val, ras_combine, row_count

    def get_extent_from_aoi(self, aoi_str):
        """Convert an AOI string to a GeoDataFrame."""
        coords = [float(x) for x in aoi_str.split(",")]
        polygon = box(coords[1], coords[0], coords[3], coords[2])
        gdf = gpd.GeoDataFrame({"geometry": [polygon]}, crs="EPSG:4326")
        return gdf
    


def resample_raster_to_match(src_raster, target_raster):
    """Resamples src_raster to match the target_raster's shape and transform."""
    with rasterio.open(src_raster) as src:
        # Get metadata from the target raster
        target_transform = target_raster.transform
        target_width = target_raster.width
        target_height = target_raster.height
        target_crs = target_raster.crs

        # Update metadata to match the target raster
        kwargs = src.meta.copy()
        kwargs.update({
            'crs': target_crs,
            'transform': target_transform,
            'width': target_width,
            'height': target_height
        })

        # Resample raster data to match the target
        data = src.read(
            out_shape=(src.count, target_height, target_width),
            resampling=Resampling.bilinear
        )

        return data[0], kwargs  # Return the first band and updated metadata


    def bounds_intersect(self, bounds1, bounds2):
        """Check if two bounding boxes intersect."""
        return not (bounds1[0] > bounds2[2] or bounds1[2] < bounds2[0] or bounds1[1] > bounds2[3] or bounds1[3] < bounds2[1])