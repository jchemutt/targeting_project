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
from shapely.ops import transform as shapely_transform
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

def wait_for_valid_raster(path, tries=50, sleep=0.1):
    """
    Wait until a raster exists AND can be opened by rasterio.
    Prevents race conditions in threaded workflows.
    """
    for _ in range(tries):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                with rasterio.open(path):
                    return True
            except Exception:
                pass
        time.sleep(sleep)
    return False


def _is_global_dataset_path(raster_url):
    """True if raster_url sits directly under a "Global" folder (any case),
    e.g. "data/Global/x.tif" — as opposed to a country-specific path like
    "data/Africa/Kenya/x.tif". Checks directory segments only, not the
    filename, so a file literally named "global_x.tif" doesn't false-match.
    """
    return any(part.lower() == "global" for part in Path(raster_url).parts[:-1])


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
        # idx -> human-readable reason a raster produced no output, so a
        # partial failure can be explained to the user instead of just
        # silently vanishing or crashing the whole run.
        self.skip_reasons = {}
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
                raster_urls = [v.get("url") for v in in_raster.values() if v.get("url")]
                # When a Global dataset is combined with a country-specific
                # one, prefer the country's folder for the "country"
                # metadata (used later by Land Statistics' reference-layer
                # picker) — whichever raster happened to be added FIRST in
                # the UI isn't a meaningful signal, and a country's own
                # reference layers are the more useful/specific set to
                # offer than falling back to Global just because a global
                # layer was clicked first.
                first_raster_path = next(
                    (u for u in raster_urls if not _is_global_dataset_path(u)),
                    raster_urls[0] if raster_urls else None,
                )

            if first_raster_path:
                # The "country"/base-path metadata shown later in the
                # Statistics tool's reference-layer picker needs the
                # FOLDER containing this raster, not a fixed number of
                # path segments. Taking "the first 3 path parts" broke
                # for Global datasets: "data/Global/x.tif" is only 3
                # parts total, so that heuristic included the filename
                # itself as if it were a directory — get_reference_layers
                # would then try to os.listdir() a .tif file and crash.
                # The raster's own parent directory is correct for any
                # nesting depth (data/Global/x.tif -> data/Global;
                # data/Africa/Kenya/x.tif -> data/Africa/Kenya).
                raster_base_path = str(Path(first_raster_path).parent)
                logging.info("Raster base path extracted: %s", raster_base_path)
            else:
                logging.warning("No raster files found in in_raster.")
                raster_base_path = None

            # AOI Handling
            aoi_str = self.parameters.get("out_extent", None)
            logging.debug("AOI provided: %s", aoi_str)
            valid_rasters, successful_idxs = self.process_rasters(in_raster, ras_temp_path, aoi_str)

            if valid_rasters == 0:
                raise ValueError("No valid rasters intersect the AOI. Check your inputs.")

            # Only combine rasters that actually produced output — a raster
            # that had no data left after AOI masking never got its
            # normalized_<idx>.tif written, and combine_rasters would crash
            # trying to open a nonexistent file if it were still included.
            in_raster_for_combine = OrderedDict(
                (idx, params) for idx, params in in_raster.items() if idx in successful_idxs
            )

            # Build a user-facing explanation for any layer that was
            # skipped, so a partial result is transparent about what
            # happened rather than silently proceeding as if nothing was
            # dropped.
            warnings = []
            for idx, params in in_raster.items():
                if idx in successful_idxs:
                    continue
                file_name = os.path.basename(params.get("url", f"raster {idx}"))
                reason = self.skip_reasons.get(idx, "produced no output (reason unknown)")
                warnings.append(f"{file_name}: {reason}")

            # Combine grouped rasters based on combine parameter
            combined_raster = self.combine_rasters(in_raster_for_combine, ras_temp_path)

            desc = self.parameters.get("description", None)

            # Save and output final result
            final_output = self.save_output(combined_raster, ras_temp_path,raster_base_path,desc)
            logging.info("Execution completed successfully. Output: %s", final_output)
            return final_output, warnings

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

        Returns:
            tuple: (valid_count, successful_idxs) — successful_idxs is the set
            of keys from `in_raster` whose normalized_<idx>.tif was actually
            written. A raster can fail individually (e.g. no data left after
            AOI masking) without failing the whole run; combine_rasters must
            only be given the rasters that actually succeeded, or it will
            crash trying to open a file that was never created.
        """
        logging.debug("Processing rasters with AOI: %s", aoi_input)
        aoi = self.get_extent_from_aoi(aoi_input) if aoi_input else None
        idxs = list(in_raster.keys())
        successful_idxs = set()

        # Seed the shared alignment reference deterministically: process
        # datasets in the order the user added them, stopping as soon as one
        # succeeds and defines the reference grid — rather than letting
        # parallel worker threads race for it. Which raster's I/O happened
        # to finish first was an accident of timing, not a meaningful
        # choice, and made alignment behaviour non-reproducible when mixing
        # rasters of very different native resolution (e.g. a coarse global
        # layer with a fine country-specific one).
        ref_raster = os.path.join(ras_temp_path, "aligned_ref.tif")
        remaining_idxs = list(idxs)
        for idx in idxs:
            remaining_idxs.remove(idx)
            if self.process_single_raster(idx, in_raster[idx], ras_temp_path, aoi):
                successful_idxs.add(idx)
            if os.path.exists(ref_raster):
                break

        # Process whatever's left in parallel now that the reference grid
        # (if one could be built at all) is fixed.
        if remaining_idxs:
            with ThreadPoolExecutor() as executor:
                futures = {
                    executor.submit(self.process_single_raster, idx, in_raster[idx], ras_temp_path, aoi): idx
                    for idx in remaining_idxs
                }
                successful_idxs |= {futures[f] for f in futures if f.result()}

        skipped = [idx for idx in idxs if idx not in successful_idxs]
        if skipped:
            logging.warning(
                "%d of %d raster(s) produced no output and were skipped: %s",
                len(skipped), len(idxs),
                {idx: self.skip_reasons.get(idx, "unknown reason") for idx in skipped},
            )
        logging.info("Processed %d valid rasters.", len(successful_idxs))
        return len(successful_idxs), successful_idxs

    def align_to_reference(self, data, transform, src_crs,ref_raster_path):
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
                src_crs=src_crs,
                dst_crs=ref_crs,
                src_nodata=-32768,
                dst_nodata=-32768,
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
                    with rasterio.open(raster_path) as src:
                        if reference_meta is None:
                            # Set the metadata of the first raster as reference
                            reference_meta = src.meta.copy()
                            logging.debug("Reference metadata set: %s", reference_meta)

                        data = src.read(1,masked=True)  # Read as numpy array
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
                            with rasterio.open(raster_path) as src:
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

            # Open raster. When an AOI is given, read directly via a masked
            # crop (rasterio.mask.mask) so only the AOI window is ever
            # pulled into memory — reading the full band first and then
            # discarding everything outside the AOI was the performance
            # bottleneck with large/global rasters (multi-minute stalls,
            # sometimes memory pressure severe enough to fail outright).
            with rasterio.open(raster_path) as src:
                logging.debug("Raster opened: %s", raster_path)
                src_meta = src.meta.copy()
                transform = src.transform
                crs = src.crs
                no_data_value = src.nodata if src.nodata is not None else NO_DATA_VALUE

                # Transform AOI to raster CRS and validate overlap
                if aoi:
                    aoi_transformed = self.transform_aoi_to_raster_crs(aoi, crs)
                    if not self.validate_aoi_overlap(raster_path, aoi_transformed):
                        reason = "AOI bounding box does not overlap this raster's extent at all."
                        logging.warning("Skipping raster %s: %s", raster_path, reason)
                        self.skip_reasons[idx] = reason
                        return 0  # Skip processing if no overlap

                    # Apply AOI masking — reads only the cropped window.
                    try:
                        aoi_polygon = [aoi_transformed.__geo_interface__]
                        data, transform = rasterio.mask.mask(src, aoi_polygon, crop=True, filled=False)
                        data = data[0]  # Extract single-band data
                        data = data.astype(np.float32)
                        data = np.ma.masked_equal(data, no_data_value)
                        # Also mask any undeclared NaN/inf fill — a float
                        # raster with no declared NoData tag but real NaN
                        # pixels (common for climate layers) would
                        # otherwise flow through unmasked and contaminate
                        # every downstream computation NaN touches (the
                        # alignment reference raster, the combine step,
                        # threshold comparisons).
                        data = np.ma.masked_invalid(data)
                    except ValueError as e:
                        reason = f"AOI masking failed: {e}"
                        logging.error("Masking failed for raster %s: %s", raster_path, e)
                        self.skip_reasons[idx] = reason
                        return 0
                else:
                    # No AOI — full raster extent is genuinely needed.
                    data = src.read(1, masked=True)
                    data = np.ma.masked_equal(data, no_data_value)
                    data = data.astype(np.float32)
                    # Same undeclared-NaN safety net as the AOI branch above.
                    data = np.ma.masked_invalid(data)

            # How much real (non-NoData) data this raster has BEFORE
            # alignment, at its own native resolution — used below to tell
            # a genuine data-coverage gap (e.g. a land-only dataset over an
            # all-ocean AOI) apart from something going wrong during
            # resampling onto the shared analysis grid.
            pre_align_valid_count = int(np.sum(~data.mask)) if np.ma.is_masked(data) else int(data.size)

            # Handle reference raster creation and alignment
            ref_raster = os.path.join(ras_temp_path, "aligned_ref.tif")

            if not os.path.exists(ref_raster):
                with self.__class__.ref_raster_lock:  # Ensure only one thread writes the reference raster
                    if not os.path.exists(ref_raster):  # Double-check inside lock
                        logging.debug("Creating reference raster: %s", ref_raster)

                        meta = src_meta.copy()
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
                            ref_dst.write(data.astype(np.float32).filled(NO_DATA_VALUE), 1)
                        logging.debug("Reference raster created successfully.")

            # Wait until reference raster exists before continuing
            if not wait_for_valid_raster(ref_raster):
                raise RuntimeError(f"Reference raster not readable: {ref_raster}")

            # Align the current raster to the reference
            data, transform = self.align_to_reference(
                data.filled(NO_DATA_VALUE),
                transform,
                crs,      
                ref_raster
            )
            data = np.ma.masked_equal(data, NO_DATA_VALUE)  # Reapply masking

            # Validate thresholds (expected: min_val <= opti_from <= opti_to <= max_val)
            if not (user_min_val <= opt_from <= opt_to <= user_max_val):
                raise ValueError(
                    "Invalid suitability thresholds: require min_val <= opti_from <= opti_to <= max_val; "
                    f"got min={user_min_val}, opt_from={opt_from}, opt_to={opt_to}, max={user_max_val}"
                )

            # Ensure raster has valid data after masking/alignment
            valid = ~data.mask
            if not np.any(valid):
                if pre_align_valid_count == 0:
                    # Genuine data-coverage gap: this dataset simply has no
                    # real (non-NoData) values anywhere inside the AOI, even
                    # before alignment. Common and expected for land-only
                    # variables (e.g. an agronomic index) over an AOI that
                    # is mostly/entirely ocean — there is nothing to
                    # recover here, it's not a processing defect.
                    reason = (
                        "No valid (non-NoData) pixels for this dataset within "
                        "the AOI — the dataset itself has no coverage here "
                        "(e.g. an ocean or otherwise out-of-domain area)."
                    )
                    logging.warning("Skipping raster %s (idx %s): %s", raster_path, idx, reason)
                else:
                    # This one IS suspicious: the raster had real data
                    # before alignment but ended up with none after being
                    # resampled onto the shared reference grid. That points
                    # at an alignment/reprojection issue (grid/CRS mismatch,
                    # pixel-snapping at the AOI edge) rather than a genuine
                    # absence of data — flag it loudly so it isn't confused
                    # with the expected "no coverage here" case above.
                    reason = (
                        f"Had {pre_align_valid_count} valid pixel(s) before alignment "
                        "but 0 after resampling onto the shared analysis grid — "
                        "likely an alignment/reprojection issue, not missing data."
                    )
                    logging.error(
                        "Raster %s (idx %s) lost all data during alignment: %s",
                        raster_path, idx, reason,
                    )
                self.skip_reasons[idx] = reason
                return 0

            # Optional: log raster range vs user thresholds (do NOT clamp user thresholds)
            valid_vals = data.data[valid]
            actual_min = float(np.min(valid_vals))
            actual_max = float(np.max(valid_vals))
            if actual_min > user_min_val or actual_max < user_max_val:
                logging.warning(
                    "Raster value range [%f, %f] is narrower than user thresholds [%f, %f]. "
                    "Suitability will be computed using user thresholds (no clamping).",
                    actual_min, actual_max, user_min_val, user_max_val
                )

            # Compute trapezoidal suitability scores in [0, 1]
            x = data.data  # raw ndarray; use `valid` mask to ignore NoData
            suitability = np.zeros(x.shape, dtype=np.float32)

            # Optimal plateau: [opt_from, opt_to] => 1
            plateau = valid & (x >= opt_from) & (x <= opt_to)
            suitability[plateau] = 1.0

            # Rising edge: (x - min) / (opt_from - min) for (min, opt_from)
            den_up = (opt_from - user_min_val)
            if den_up > 0:
                up = valid & (x > user_min_val) & (x < opt_from)
                suitability[up] = (x[up] - user_min_val) / den_up

            # Falling edge: (max - x) / (max - opt_to) for (opt_to, max)
            den_down = (user_max_val - opt_to)
            if den_down > 0:
                down = valid & (x > opt_to) & (x < user_max_val)
                suitability[down] = (user_max_val - x[down]) / den_down

            # Safety clamp
            suitability = np.clip(suitability, 0.0, 1.0).astype(np.float32)

            # Keep NoData masked
            normalized = np.ma.masked_array(suitability, mask=data.mask)

            # Save the processed raster
            output_path = os.path.join(ras_temp_path, f"normalized_{idx}.tif")
            meta = src_meta.copy()
            meta.update({
                "driver": "GTiff",
                "dtype": "float32",
                "height": normalized.shape[0],
                "width": normalized.shape[1],
                "transform": transform,
                "nodata": NO_DATA_VALUE  # Ensure NoData is correctly set
            })

            with rasterio.open(output_path, "w", **meta) as dst:
                dst.write(normalized.astype(np.float32).filled(NO_DATA_VALUE), 1)  # Preserve NoData
            logging.info("Processed raster saved: %s", output_path)

            return 1

        except FileNotFoundError:
            self.skip_reasons[idx] = f"File not found: {params.get('url')}"
            logging.error("[ERROR] File not found: %s", params["url"], exc_info=True)
        except PermissionError:
            self.skip_reasons[idx] = f"Permission denied reading: {params.get('url')}"
            logging.error("[ERROR] Permission denied when accessing raster: %s", params["url"], exc_info=True)
        except rasterio.errors.RasterioError as e:
            self.skip_reasons[idx] = f"Rasterio error: {e}"
            logging.error("[ERROR] Rasterio processing error: %s", e, exc_info=True)
        except ValueError as e:
            self.skip_reasons[idx] = f"Invalid input: {e}"
            logging.error("[ERROR] ValueError while processing raster: %s", e, exc_info=True)
        except Exception as e:
            self.skip_reasons[idx] = f"Unexpected error: {e}"
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
        with rasterio.open(ref_raster_path) as ref_raster:
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

        aoi: shapely Polygon or MultiPolygon in EPSG:4326
        raster_crs: rasterio CRS (or object with .to_string())
        """
        if not isinstance(aoi, (Polygon, MultiPolygon)):
            raise TypeError("AOI must be a shapely Polygon or MultiPolygon.")

        transformer = Transformer.from_crs(
            "EPSG:4326",
            raster_crs.to_string() if hasattr(raster_crs, "to_string") else raster_crs,
            always_xy=True
        )

        # shapely.ops.transform will transform ALL coords (exterior + interiors, and all parts in MultiPolygon)
        return shapely_transform(lambda x, y, z=None: transformer.transform(x, y), aoi)

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

