import os
import json
import rasterio

def get_raster_min_max(raster_path):
    with rasterio.open(raster_path) as src:
        data = src.read(1, masked=True)
        min_val = data.min()
        max_val = data.max()
    return min_val, max_val

def process_raster_files(input_folder, output_json):
    raster_info_list = []
    
    for root, _, files in os.walk(input_folder):
        for file in files:
            if file.endswith('.tif'):
                file_path = os.path.join(root, file)
                min_val, max_val = get_raster_min_max(file_path)
                raster_info = {
                        'name': file,
                        'min_val': int(min_val) if min_val.is_integer() else float(min_val),
                        'max_val': int(max_val) if max_val.is_integer() else float(max_val)
                    }
                raster_info_list.append(raster_info)
                print(raster_info)
    
 
    
    with open(output_json, 'w') as json_file:
        json.dump(raster_info_list, json_file, indent=4)

if __name__ == "__main__":
    input_folder = 'C:/Users/jchemutt/Documents/projects/Targeting/targeting_project/data'
    output_json = 'C:/Users/jchemutt/Documents/projects/Targeting/targeting_project/values4.json'
    process_raster_files(input_folder, output_json)
