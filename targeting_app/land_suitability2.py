import os
import logging
import numpy as np
import time
import re
import rasterio
import rasterio.mask
import threading
from shapely.geometry import  shape, Polygon,MultiPolygon, box
from collections import OrderedDict
import json
from concurrent.futures import ThreadPoolExecutor
from pyproj import Transformer
from rasterio.enums import Resampling
from django.utils.timezone import now
from .reclassify import reclassify
from .main_tool import TargetingTool
from pathlib import Path
import shutil

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Change to logging.INFO or logging.ERROR for less verbosity
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("land_suitability.log"),
        logging.StreamHandler()
    ]
)


class LandSuitability(TargetingTool):
    """Tool for determining land suitability based on raster data and user-defined criteria."""

    ref_raster_lock = threading.Lock()

    def __init__(self, parameters,session):
        """Initialize the tool with user-defined parameters."""
        super().__init__()
        self.label = "Land Suitability"
        self.description = (
            "Identifies suitable areas based on user-provided raster data and optimal values."
        )
        self.canRunInBackground = False
        self.parameters = parameters
        self.session = session
        logging.debug("LandSuitability initialized with parameters: %s", parameters)

    def prepare_value_table(self, parameters):
        """
        Prepare input parameters into an ordered dictionary.
        """
        logging.debug("Preparing value table from parameters.")
        value_table = OrderedDict()
        for key, param in parameters.items():
            if key in ['description', 'out_extent']:
                continue

            match = re.search(r'\d+', key)
            if not match:
                logging.warning("Key '%s' does not match the expected pattern and will be skipped.", key)
                continue
            idx = int(match.group()) - 1
            value_table.setdefault(idx, {})
            value_table[idx][self.get_param_key(key)] = param
        logging.debug("Prepared value table: %s", value_table)
        return value_table

    @staticmethod
    def get_param_key(key):
        """Map parameter key to a consistent format."""
        mappings = {
            "in_raster": "url",
            "min_val": "min_val",
            "opti_from": "opti_from",
            "opti_to": "opti_to",
            "max_val": "max_val",
            "combine": "combine",
        }
        for prefix, mapped_key in mappings.items():
            if prefix in key:
                return mapped_key
        raise ValueError(f"Unexpected parameter key: {key}")

    def execute(self):
        """Main workflow to process rasters and determine suitability."""
        try:
            logging.info("Execution started.")
            in_raster = self.prepare_value_table(self.parameters)
            
            ras_temp_path = self.prepare_temp_directory()

                    # Extract the first raster's path from OrderedDict
            first_raster_path = None
            if isinstance(in_raster, OrderedDict) and in_raster:
                first_raster_path = list(in_raster.values())[0].get("url")  # Get first raster file URL

            if first_raster_path:
                # Extract "data/Africa/Rwanda" (first three parts of the path)
                path_parts = Path(first_raster_path).parts
                if len(path_parts) >= 3:
                    raster_base_path = str(Path(*path_parts[:3]))  # Take first three levels
                else:
                    raster_base_path = str(Path(first_raster_path).parent)  # Fallback
                logging.info("Raster base path extracted: %s", raster_base_path)
            else:
                logging.warning("No raster files found in in_raster.")
                raster_base_path = None

            # AOI Handling
            aoi_str = self.parameters.get("out_extent", None)
            logging.debug("AOI provided: %s", aoi_str)
            valid_rasters = self.process_rasters(in_raster, ras_temp_path, aoi_str)

            if valid_rasters == 0:
                raise ValueError("No valid rasters intersect the AOI. Check your inputs.")

            # Combine grouped rasters based on combine parameter
            combined_raster = self.combine_rasters(in_raster, ras_temp_path)

            desc = self.parameters.get("description", None)

            # Save and output final result
            final_output = self.save_output(combined_raster, ras_temp_path,raster_base_path,desc)
            logging.info("Execution completed successfully. Output: %s", final_output)
            return final_output

        except Exception as e:
            logging.error("Error during execution: %s", e, exc_info=True)
            raise RuntimeError(f"Error during execution: {e}")

    def prepare_temp_directory(self):
        """Set up temporary directories for processing."""
        logging.debug("Preparing temporary directory.")
        workspace_path = os.getcwd().replace("\\", "/")
        media_dir = os.path.join(workspace_path, "media")
        os.makedirs(media_dir, exist_ok=True)

        output_dir = os.path.join(media_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        ts = str(time.time())
        ras_temp_path = os.path.join(output_dir, ts)
        os.makedirs(ras_temp_path, exist_ok=True)

        logging.debug("Temporary directory prepared: %s", ras_temp_path)
        return ras_temp_path

    def process_rasters(self, in_raster, ras_temp_path, aoi_input=None):
        """
        Process input rasters: mask with AOI (if provided), normalize, and prepare for combination.
        """
        logging.debug("Processing rasters with AOI: %s", aoi_input)
        aoi = self.get_extent_from_aoi(aoi_input) if aoi_input else None
        valid_rasters = 0
        with ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(self.process_single_raster, idx, params, ras_temp_path, aoi)
                for idx, params in in_raster.items()
            ]
            results = [f.result() for f in futures]
            valid_rasters = sum(results)
        logging.info("Processed %d valid rasters.", valid_rasters)
        return valid_rasters

    def align_to_reference(self, data, transform, ref_raster_path):
        """
        Align the masked raster to the reference raster.

        Parameters:
            data (np.ndarray): The raster data to align.
            transform (Affine): The affine transform of the input raster.
            ref_raster_path (str): Path to the reference raster for alignment.

        Returns:
            tuple: (aligned_data, aligned_transform) where aligned_data is the raster
                   aligned to the reference grid and aligned_transform is the affine transform.
        """
        with rasterio.open(ref_raster_path) as ref:
            ref_transform = ref.transform
            ref_crs = ref.crs
            ref_width = ref.width
            ref_height = ref.height

            aligned_data = np.empty((ref_height, ref_width), dtype=data.dtype)
            rasterio.warp.reproject(
                source=data,
                destination=aligned_data,
                src_transform=transform,
                dst_transform=ref_transform,
                src_crs=ref.crs,
                dst_crs=ref_crs,
                resampling=rasterio.warp.Resampling.nearest,
            )
            return aligned_data, ref_transform
        
   

    def combine_rasters(self, in_raster, ras_temp_path):
        """
        Combine grouped normalized rasters based on the combine parameter.

        Parameters:
            in_raster (OrderedDict): Table of raster inputs.
            ras_temp_path (str): Path for temporary storage.

        Returns:
            np.array: Combined raster data with NoData values properly masked.
        """
        NO_DATA_VALUE = -32768  # NoData value (should be ignored in computations)

        ras_temp_file, n_ras = self.set_combine_file(in_raster, ras_temp_path)

        if n_ras == 0:
            logging.error("No rasters available for combination.")
            raise ValueError("No rasters available for combination.")

        combined_data = None
        reference_meta = None  # Initialize reference metadata
        logging.debug("Combining rasters: %s", ras_temp_file)

        try:
            for group in ras_temp_file:
                if len(group) == 1:
                    raster_path = group[0]
                    with rasterio.open(raster_path,masked=True) as src:
                        if reference_meta is None:
                            # Set the metadata of the first raster as reference
                            reference_meta = src.meta.copy()
                            logging.debug("Reference metadata set: %s", reference_meta)

                        data = src.read(1)  # Read as numpy array
                        masked_data = np.ma.masked_equal(data, NO_DATA_VALUE)  # Mask out NoData values

                        if combined_data is None:
                            combined_data = masked_data
                        else:
                            combined_data = combined_data * masked_data  # Perform element-wise multiplication

                else:
                    group_data = None
                    for raster_path in group:
                        # Ensure `reference_meta` is set before resampling
                        if reference_meta is None:
                            with rasterio.open(raster_path,masked=True) as src:
                                reference_meta = src.meta.copy()
                                logging.debug("Reference metadata set: %s", reference_meta)

                        aligned_path = os.path.join(ras_temp_path, f"aligned_{os.path.basename(raster_path)}")
                        self.resample_raster(raster_path, reference_meta, aligned_path)

                        with rasterio.open(aligned_path) as aligned_src:
                            data = aligned_src.read(1)  # Read as numpy array
                            masked_data = np.ma.masked_equal(data, NO_DATA_VALUE)  # Mask out NoData values

                            if group_data is None:
                                group_data = masked_data
                            else:
                                group_data = np.ma.maximum(group_data, masked_data)  # Element-wise max

                    if combined_data is None:
                        combined_data = group_data
                    else:
                        combined_data = combined_data * group_data  # Perform element-wise multiplication

            # Fill masked (NoData) values with NO_DATA_VALUE before returning
            combined_data = np.ma.filled(combined_data, NO_DATA_VALUE)

        except Exception as e:
            logging.error("Error while combining rasters: %s", e, exc_info=True)
            raise

        logging.debug("Combined raster data shape: %s", combined_data.shape if combined_data is not None else None)
        return combined_data

    
    




    def process_single_raster(self, idx, params, ras_temp_path, aoi=None):
        """
        Process a single raster, including AOI masking, thresholding, normalization, and alignment.

        Ensures only one thread creates the reference raster to avoid race conditions.

        Parameters:
            idx (int): The index of the raster.
            params (dict): Parameters for the raster.
            ras_temp_path (str): Path to the temporary directory for saving outputs.
            aoi (Polygon, optional): AOI polygon for masking.

        Returns:
            int: 1 if processing succeeds, 0 otherwise.
        """
        try:
            logging.debug("Processing raster: %s", params)
            raster_path = params["url"]
            user_min_val = float(params["min_val"])
            user_max_val = float(params["max_val"])
            opt_from = float(params["opti_from"])
            opt_to = float(params["opti_to"])

            NO_DATA_VALUE = -32768  # NoData value
            FILTERED_OUT_VALUE = -9999  # Filtered-out value for out-of-threshold pixels

            # Open raster with masked values
            with rasterio.open(raster_path, masked=True) as src:
                logging.debug("Raster opened: %s", raster_path)
                transform = src.transform
                crs = src.crs
                data = src.read(1, masked=True)  # Read as a masked array (preserves NoData)

                # Extract NoData value from raster
                no_data_value = src.nodata if src.nodata is not None else NO_DATA_VALUE
                data = np.ma.masked_equal(data, no_data_value)  # Mask NoData values
                logging.debug("NoData value replaced with masked array")

                # Convert to float32 for consistency
                data = data.astype(np.float32)

                # Transform AOI to raster CRS and validate overlap
                if aoi:
                    aoi_transformed = self.transform_aoi_to_raster_crs(aoi, crs)
                    if not self.validate_aoi_overlap(raster_path, aoi_transformed):
                        logging.warning("Skipping raster as AOI does not overlap: %s", raster_path)
                        return 0  # Skip processing if no overlap

                    # Apply AOI masking
                    try:
                        aoi_polygon = [aoi_transformed.__geo_interface__]
                        data, transform = rasterio.mask.mask(src, aoi_polygon, crop=True)
                        data = data[0]  # Extract single-band data
                    except ValueError as e:
                        logging.error("Masking failed for raster %s: %s", raster_path, e)
                        return 0

            # Handle reference raster creation and alignment
            ref_raster = os.path.join(ras_temp_path, "aligned_ref.tif")

            if not os.path.exists(ref_raster):
                with self.__class__.ref_raster_lock:  # Ensure only one thread writes the reference raster
                    if not os.path.exists(ref_raster):  # Double-check inside lock
                        logging.debug("Creating reference raster: %s", ref_raster)

                        meta = src.meta.copy()
                        meta.update({
                            "driver": "GTiff",
                            "dtype": "float32",
                            "count": 1,
                            "height": data.shape[0],
                            "width": data.shape[1],
                            "transform": transform,
                            "nodata": NO_DATA_VALUE  # Ensure NoData is correctly set
                        })

                        with rasterio.open(ref_raster, "w", **meta) as ref_dst:
                            ref_dst.write(data.filled(NO_DATA_VALUE), 1)
                        logging.debug("Reference raster created successfully.")

            # Wait until reference raster exists before continuing
            while not os.path.exists(ref_raster):
                logging.debug("Waiting for reference raster to be created...")
                time.sleep(0.1)  # Small delay to avoid busy-waiting

            # Align the current raster to the reference
            data, transform = self.align_to_reference(data.filled(NO_DATA_VALUE), transform, ref_raster)
            data = np.ma.masked_equal(data, NO_DATA_VALUE)  # Reapply masking

            # Validate and adjust user-provided min_val and max_val
            valid_data = data[~data.mask]
            if valid_data.size > 0:
                actual_min = np.min(valid_data)
                actual_max = np.max(valid_data)
                if actual_min > user_min_val or actual_max < user_max_val:
                    logging.warning("Adjusting min_val and max_val to fit raster range: [%f, %f]", actual_min, actual_max)
                user_min_val = max(user_min_val, actual_min)
                user_max_val = min(user_max_val, actual_max)
            else:
                logging.warning("No valid data in raster. Skipping normalization.")
                return 0

            # **Apply threshold-based filtering**
            threshold_mask = (data < opt_from) | (data > opt_to)  # Track out-of-range pixels
            data[threshold_mask] = FILTERED_OUT_VALUE  # Assign -9999 for filtered pixels

            # **Normalize raster data** (ignoring NoData and -9999)
            valid_mask = ~data.mask & (data != FILTERED_OUT_VALUE)  # Ensure NoData remains masked
            normalized = np.ma.masked_array(np.full(data.shape, NO_DATA_VALUE, dtype=np.float32), mask=data.mask)

            if user_max_val > user_min_val:
                normalized[valid_mask] = (data[valid_mask] - user_min_val) / (user_max_val - user_min_val)
                normalized = np.clip(normalized, 0, 1)
            else:
                logging.warning("Invalid normalization range: [%f, %f]. Skipping normalization.", user_min_val, user_max_val)
                normalized = data  # Keep original values if range is invalid

            # **Replace thresholded values with 0, but keep NoData values masked**
            normalized[threshold_mask & ~data.mask] = 0

            # Save the processed raster
            output_path = os.path.join(ras_temp_path, f"normalized_{idx}.tif")
            meta = src.meta.copy()
            meta.update({
                "driver": "GTiff",
                "dtype": "float32",
                "height": normalized.shape[0],
                "width": normalized.shape[1],
                "transform": transform,
                "nodata": NO_DATA_VALUE  # Ensure NoData is correctly set
            })

            with rasterio.open(output_path, "w", **meta) as dst:
                dst.write(normalized.filled(NO_DATA_VALUE), 1)  # Preserve NoData
            logging.info("Processed raster saved: %s", output_path)

            return 1

        except FileNotFoundError:
            logging.error("[ERROR] File not found: %s", params["url"], exc_info=True)
        except PermissionError:
            logging.error("[ERROR] Permission denied when accessing raster: %s", params["url"], exc_info=True)
        except rasterio.errors.RasterioError as e:
            logging.error("[ERROR] Rasterio processing error: %s", e, exc_info=True)
        except ValueError as e:
            logging.error("[ERROR] ValueError while processing raster: %s", e, exc_info=True)
        except Exception as e:
            logging.error("[ERROR] Unexpected error while processing raster: %s", e, exc_info=True)

        return 0


        
    


    def set_combine_file(self, in_raster, ras_temp_path):
        """
        Build a list with groups of temporary raster files based on the 'combine' parameter.

        Parameters:
            in_raster (OrderedDict): Table of raster inputs and their parameters.
            ras_temp_path (str): Path for temporary storage.

        Returns:
            tuple: (ras_temp_file, n_ras)
                - ras_temp_file: List of grouped raster file paths.
                - n_ras: Total number of rasters.
        """
        ras_temp_file = []  # List to hold groups of raster temp file paths
        current_group = []  # Current group of rasters
        n_ras = 0           # Total number of rasters

        # Iterate over the raster parameters
        for idx, params in in_raster.items():
            n_ras += 1  # Increment raster count

            # Create temporary file path for the raster
            temp_file_path = os.path.join(ras_temp_path, f"normalized_{idx}.tif")

            # Check the 'combine' parameter
            combine = params.get("combine", "no").lower()

            if combine == "yes":
                # Add to the current group
                current_group.append(temp_file_path)
            else:
                # Start a new group if 'combine' is "no"
                if current_group:
                    ras_temp_file.append(current_group)
                current_group = [temp_file_path]

        # Append the last group after the loop
        if current_group:
            ras_temp_file.append(current_group)

        return ras_temp_file, n_ras
    
   


    def save_output(self, combined_raster, ras_temp_path,raster_base_path,desc):
        """
        Save the combined raster as the final output.
        """
        output_path = os.path.join(ras_temp_path, "final_output.tif")

        if combined_raster is None:
            raise ValueError("Combined raster is None. Cannot save output.")

        ref_raster_path = os.path.join(ras_temp_path, "normalized_0.tif")
        with rasterio.open(ref_raster_path,masked=True) as ref_raster:
            meta = ref_raster.meta
            meta.update({
                "driver": "GTiff",
                "dtype": "float32",
                "width": combined_raster.shape[1],
                "height": combined_raster.shape[0],
                "count": 1,
            })
            with rasterio.open(output_path, "w", **meta) as dst:
                dst.write(combined_raster, 1)

        # Ensure no open references to the file before reclassifying
        logging.debug("Ensuring file is closed before reclassification: %s", output_path)
        output_reclassified_path = os.path.splitext(output_path)[0] + "_reclassified.tif"
        try:
            reclassify(output_path, output_reclassified_path, allow_overwrite=True)  # Apply reclassification
        except Exception as e:
            logging.error("Reclassification failed: %s", e, exc_info=True)
            raise

        self.cleanup_intermediate_files(ras_temp_path, output_reclassified_path)
        # Compute relative path for the output
        media_dir = os.path.join(os.getcwd(), "media")
        relative_output_path = os.path.relpath(output_reclassified_path, media_dir)
        relative_output_path = relative_output_path.replace("\\", "/")  # Standardize path format for URLs
        result_relative_url = f"/media/{relative_output_path}"

         # Store metadata in the session
        self.store_metadata_in_session({
            "file_path": result_relative_url,
            "country": raster_base_path,
            "created_at": now().isoformat(),
            "description": "Land suitability raster file",
            "title": desc,
            
        })

        
        return result_relative_url



    def transform_aoi_to_raster_crs2(self, aoi, raster_crs):
        """
        Transform AOI coordinates to match the raster's CRS.
        Parameters:
            aoi (shapely.geometry.Polygon): AOI in lat/lon (EPSG:4326).
            raster_crs (CRS): Target CRS of the raster.
        Returns:
            shapely.geometry.Polygon: AOI transformed to raster CRS.
        """
        transformer = Transformer.from_crs("EPSG:4326", raster_crs.to_string(), always_xy=True)
        transformed_coords = [transformer.transform(x, y) for x, y in aoi.exterior.coords]
        return box(*transformed_coords)
    
    def transform_aoi_to_raster_crs(self, aoi, raster_crs):
        """
        Transform AOI coordinates to match the raster's CRS.

        Parameters:
            aoi (shapely.geometry.Polygon or MultiPolygon): AOI geometry in lat/lon (EPSG:4326).
            raster_crs (CRS): Target CRS of the raster.

        Returns:
            shapely.geometry.Polygon: AOI transformed to the raster's CRS.
        """
        if not isinstance(aoi, (Polygon, MultiPolygon)):
            raise TypeError("AOI must be a shapely Polygon or MultiPolygon.")

        transformer = Transformer.from_crs("EPSG:4326", raster_crs.to_string(), always_xy=True)

        # Transform coordinates
        transformed_coords = [
            transformer.transform(x, y) for x, y in aoi.exterior.coords
        ]
        return Polygon(transformed_coords)

    def get_extent_from_aoi(self, aoi_input):
        """
        Convert AOI input to a shapely.geometry.Polygon.

        Parameters:
            aoi_input (str or dict): AOI in JSON string (GeoJSON) or bounding box format.

        Returns:
            shapely.geometry.Polygon: AOI as a shapely polygon.
        """
        if isinstance(aoi_input, dict):
            # Handle AOI as GeoJSON dictionary
            if "type" in aoi_input:
                if aoi_input["type"] == "Feature":
                    # Extract geometry from a Feature
                    geometry = aoi_input.get("geometry", None)
                    if not geometry:
                        raise ValueError("GeoJSON Feature does not contain geometry.")
                    return shape(geometry)  # Convert to shapely polygon
                elif aoi_input["type"] in {"Polygon", "MultiPolygon"}:
                    # Raw geometry without being wrapped in a Feature
                    return shape(aoi_input)
                else:
                    raise ValueError(f"Unsupported GeoJSON type: {aoi_input['type']}")
            else:
                raise ValueError("Invalid GeoJSON format: Missing 'type'.")
        elif isinstance(aoi_input, str):
            # Handle AOI as bounding box string
            coords = [float(x) for x in aoi_input.split(",")]
            if len(coords) == 4:
                return box(coords[1], coords[0], coords[3], coords[2])  # Bounding box
            else:
                raise ValueError("Invalid bounding box format.")
        else:
            raise TypeError("Unsupported AOI input type. Must be a string or dictionary.")

    
    def validate_aoi_overlap(self, raster_path, aoi):
        with rasterio.open(raster_path) as src:
            raster_bounds = box(*src.bounds)
            if not aoi.intersects(raster_bounds):
                logging.warning("AOI does not overlap with raster: %s", raster_path)
                return False
        return True
    
    def cleanup_intermediate_files(self, ras_temp_path, final_file_path):
        """
        Delete all files in ras_temp_path except the final product.

        Parameters:
            ras_temp_path (str): The temporary directory path.
            final_file_path (str): Absolute path of the file to keep.
        """
        final_file_path = os.path.abspath(final_file_path)

        for name in os.listdir(ras_temp_path):
            path = os.path.join(ras_temp_path, name)
            try:
                if os.path.abspath(path) == final_file_path:
                    # Skip the final product
                    continue

                if os.path.isfile(path) or os.path.islink(path):
                    os.remove(path)
                    logging.debug("Deleted intermediate file: %s", path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
                    logging.debug("Deleted intermediate directory: %s", path)
            except Exception as e:
                logging.warning("Could not delete %s: %s", path, e)
    
    
    def store_metadata_in_session(self, file_metadata):
        """
        Store file metadata in the session.
        """
        if "generated_files" not in self.session:
            self.session["generated_files"] = []

        self.session["generated_files"].append(file_metadata)
        # Ensure session is saved
        self.session.modified = True
        logging.debug("Stored file metadata in session: %s", file_metadata)

    def resample_raster(self, input_path, reference_meta, output_path):
        """
        Resample the input raster to match the reference raster's shape and resolution.
        Parameters:
            input_path (str): Path to the input raster.
            reference_meta (dict): Metadata of the reference raster.
            output_path (str): Path to save the resampled raster.
        """
        with rasterio.open(input_path) as src:
            transform = reference_meta['transform']
            width = reference_meta['width']
            height = reference_meta['height']

            # Resample the input raster
            data = src.read(
                out_shape=(src.count, height, width),
                resampling=Resampling.bilinear,  # Use bilinear resampling for continuous data
            )

            # Update metadata to match the reference
            resampled_meta = src.meta.copy()
            resampled_meta.update({
                'transform': transform,
                'width': width,
                'height': height,
            })

            # Save the resampled raster
            with rasterio.open(output_path, 'w', **resampled_meta) as dst:
                dst.write(data)

