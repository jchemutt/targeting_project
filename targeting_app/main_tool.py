import os
import json
import numpy as np
import geopandas as gpd
import rasterio
import rasterio.mask
from shapely.geometry import box
import smtplib
from email.message import EmailMessage

class TargetingTool:
    def __init__(self):
        # Load SMTP configuration from JSON file
        self.smtp_config = self.load_smtp_config()

    def load_smtp_config(self):
        config_path = os.path.join(os.path.dirname(__file__), '..', 'smtp_config.json')
        with open(config_path, 'r') as config_file:
            return json.load(config_file)

    def submit_message(self, output_path, emails, label):
        msg = EmailMessage()
        msg['Subject'] = f'Results for {label}'
        msg['From'] = self.smtp_config['username']
        msg['To'] = emails
        msg.set_content(f'The results for {label} have been processed and are available at the following path: {output_path}')

        try:
            with smtplib.SMTP(self.smtp_config['server'], self.smtp_config['port']) as server:
                server.ehlo()  # Can be omitted
                server.starttls()
                server.ehlo()  # Can be omitted
                server.login(self.smtp_config['username'], self.smtp_config['password'])
                server.sendmail(self.smtp_config['username'], emails, msg.as_string())
            print(f"Results emailed to {emails}")
        except Exception as e:
            print(f"Failed to send email: {str(e)}")

    def mask_raster(self, raster_path, geometry):
        with rasterio.open(raster_path) as src:
            out_image, out_transform = rasterio.mask.mask(src, [geometry], crop=True)
            out_meta = src.meta
        out_meta.update({"driver": "GTiff", "height": out_image.shape[1], "width": out_image.shape[2], "transform": out_transform})
        return out_image, out_meta

    def write_raster(self, output_path, image, meta):
        with rasterio.open(output_path, 'w', **meta) as dest:
            dest.write(image)

    def read_raster(self, raster_path):
        with rasterio.open(raster_path) as src:
            return src.read(1), src.meta

    def reclassify_raster(self, input_path, output_path, reclass_rules):
        data, meta = self.read_raster(input_path)
        reclass_data = np.copy(data)
        for old_value, new_value in reclass_rules.items():
            reclass_data[data == old_value] = new_value
        self.write_raster(output_path, reclass_data, meta)

    def crop_raster_to_extent(self, raster_path, extent, output_path):
        with rasterio.open(raster_path) as src:
            bbox = box(*extent)
            out_image, out_transform = rasterio.mask.mask(src, [bbox], crop=True)
            out_meta = src.meta
            out_meta.update({"driver": "GTiff", "height": out_image.shape[1], "width": out_image.shape[2], "transform": out_transform})
            self.write_raster(output_path, out_image, out_meta)

    def combine_rasters(self, raster_paths, method='max'):
        rasters = [rasterio.open(path) for path in raster_paths]
        arrays = [r.read(1) for r in rasters]
        meta = rasters[0].meta

        if method == 'max':
            combined_data = np.maximum.reduce(arrays)
        elif method == 'min':
            combined_data = np.minimum.reduce(arrays)
        elif method == 'mean':
            combined_data = np.mean(arrays, axis=0)
        else:
            raise ValueError("Unknown combine method: {}".format(method))

        output_path = os.path.join(os.path.dirname(raster_paths[0]), "combined.tif")
        self.write_raster(output_path, combined_data, meta)
        return output_path

    def scale_raster(self, input_path, output_path, scale_factor):
        data, meta = self.read_raster(input_path)
        scaled_data = data * scale_factor
        self.write_raster(output_path, scaled_data, meta)

   
