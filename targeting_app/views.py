import os
import json
import geojson
from django.http import JsonResponse
from django.shortcuts import render
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponseBadRequest, HttpResponse
import numpy as np
import geopandas as gpd
from shapely.geometry import box
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from .land_suitability2 import LandSuitability
from .land_similarity2 import LandSimilarity
from .land_statistics2 import LandStatistics

def landing_page(request):
    return render(request, 'targeting_app/landing_page.html')

def suitability(request):
    return render(request, 'targeting_app/suitability.html')

def similarity(request):
    return render(request, 'targeting_app/land_similarity3.html')

def statistics(request):
    return render(request, 'targeting_app/land_statistics2.html')

import os
from django.conf import settings
from django.http import JsonResponse

def get_reference_layers(request):
    raster_path = request.GET.get('raster_path', '')
    base_dir = os.path.join(settings.BASE_DIR, raster_path)

    if not os.path.exists(base_dir):
        return JsonResponse({'error': 'Path not found'}, status=404)

    files = [
        {
            'file_path': os.path.join(raster_path, f),  # Relative path
            'name': f
        }
        for f in os.listdir(base_dir) if f.endswith('.tif')
    ]

    print("Processed Parameters:", json.dumps(files, indent=4))

    return JsonResponse({'raster_files': files})


def get_directory_contents(request):
    # Root directory for raster files
    base_dir = os.path.join(settings.BASE_DIR, 'data')
    # Get the relative path from request or default to an empty string
    path = request.GET.get('path', '')

    # Full path to the directory
    full_path = os.path.join(base_dir, path.strip('/'))

    if not os.path.exists(full_path):
        return JsonResponse({'error': 'Directory not found'}, status=404)

    contents = []
    # Load JSON file with min_val and max_val values
    config_file_path = os.path.join(settings.BASE_DIR, 'values.json')
    if os.path.exists(config_file_path):
        with open(config_file_path, 'r') as f:
            config_data = json.load(f)
    else:
        config_data = {}

    for item in os.listdir(full_path):
        item_path = os.path.join(full_path, item)
        if os.path.isdir(item_path):
            contents.append({'name': item, 'type': 'directory'})
        elif os.path.isfile(item_path) and item.lower().endswith('.tif'):
            # Search for min_val and max_val in config_data based on file name
            min_val = None
            max_val = None
            for config_item in config_data:
                if config_item['name'] == item:
                    min_val = config_item.get('min_val')
                    max_val = config_item.get('max_val')
                    break
            
            contents.append({'name': item, 'type': 'file', 'min_val': min_val, 'max_val': max_val})

    return JsonResponse(contents, safe=False)

def get_folder_configurations(request):
    folder_name = request.GET.get('folder', '')
    # Fetch folder configurations from database or static JSON file
    # Replace with your actual data fetching logic
    configurations = {
        'Africa': {'center': [7.1881, 21.0938], 'zoom': 2},
        'Asia': {'center': [47.5162, 103.6609], 'zoom': 2},
        'Global': {'center': [0, 0], 'zoom': 1},
        'S.America': {'center': [-14.2350, -56.1167], 'zoom': 2},
        'Ethiopia': {'center': [9.145, 40.4897], 'zoom': 5},
        'Kenya': {'center': [1.2921, 36.8219], 'zoom': 5},
        'Mali': {'center': [17.5707, -3.9962], 'zoom': 5},
        'Rwanda': {'center': [-1.9403, 29.8739], 'zoom': 7},
        'Senegal': {'center': [14.4974, -14.4524], 'zoom': 6},
        'Tanzania': {'center': [-6.369028, 34.888822], 'zoom': 5},
        'Tunisia': {'center': [33.8869, 9.5375], 'zoom': 5},
        'Colombia': {'center': [4.5709, -74.2973], 'zoom': 5},
        'Ghana': {'center': [7.9465, -1.0232], 'zoom': 6},

        # Add configurations for other folders as needed
    }
    return JsonResponse(configurations.get(folder_name, {'center': [0, 0], 'zoom': 1}))

def convert_aoi_to_geojson(aoi_str):
    """
    Converts an AOI string into GeoJSON polygon format.

    Parameters:
        aoi_str (str): AOI as a string of lat,lon pairs separated by semicolons.

    Returns:
        dict: GeoJSON polygon object.
    """
    try:
        # Parse the input string into a list of coordinates
        coordinates = []
        for coord in aoi_str.split(";"):
            lat, lon = map(float, coord.split(","))
            coordinates.append([lon, lat])  # GeoJSON uses [longitude, latitude]

        # Ensure the polygon is closed
        if coordinates[0] != coordinates[-1]:
            coordinates.append(coordinates[0])

        # Create the GeoJSON polygon
        geojson_polygon = {
            "type": "Polygon",
            "coordinates": [coordinates]
        }
        return geojson_polygon
    except Exception as e:
        raise ValueError(f"Invalid AOI format: {e}")


@csrf_exempt
def process_land_suitability(request):
    if request.method == 'POST':
        try:
            # Parse the form data
            form_data = json.loads(request.body)

            # Get AOI and other inputs
            aoi = form_data.get('aoi', None)  # AOI is optional
            selected_files = form_data.get('selectedFiles', [])
            raster_parameters = form_data.get('rasterParameters', {})
            description = form_data.get('description', '').strip()
            if not description:
                return JsonResponse({'status': 'error', 'message': 'Description is required.'}, status=400)

            # Initialize parameters
            parameters = {'description': description}
          

            # Handle AOI (Polygon, Rectangle, or GeoJSON)
            if aoi:
                try:
                    # Attempt to parse AOI as GeoJSON
                    aoi_data = json.loads(aoi) if isinstance(aoi, str) else aoi
                    if "type" in aoi_data:
                        if aoi_data["type"] == "Feature" and "geometry" in aoi_data:
                            # Extract geometry from Feature
                            geometry = aoi_data["geometry"]
                        elif aoi_data["type"] in ["Polygon", "MultiPolygon"]:
                            # Directly use the geometry
                            geometry = aoi_data
                        else:
                            raise ValueError("Unsupported AOI type.")

                        parameters['out_extent'] = geometry  # Add GeoJSON geometry
                        print(f"AOI processed as GeoJSON: {parameters['out_extent']}")
                    else:
                        raise ValueError("Invalid GeoJSON format.")
                except json.JSONDecodeError:
                    if ";" in aoi:
                        # Handle semicolon-separated polygon AOI
                        geojson_aoi = convert_aoi_to_geojson(aoi)
                        parameters['out_extent'] = geojson_aoi
                        print(f"AOI provided as polygon string converted to GeoJSON: {geojson_aoi}")
                    elif "," in aoi:
                        # Handle rectangle AOI as bounding box
                        coords = aoi.split(",")
                        if len(coords) == 4:
                            parameters['out_extent'] = {
                                "type": "Polygon",
                                "coordinates": [[
                                    [float(coords[1]), float(coords[0])],
                                    [float(coords[3]), float(coords[0])],
                                    [float(coords[3]), float(coords[2])],
                                    [float(coords[1]), float(coords[2])],
                                    [float(coords[1]), float(coords[0])]
                                ]]
                            }
                            print(f"AOI provided as rectangle converted to GeoJSON: {parameters['out_extent']}")
                        else:
                            return JsonResponse({'status': 'error', 'message': 'Invalid AOI format.'}, status=400)
                    else:
                        return JsonResponse({'status': 'error', 'message': 'Invalid AOI format.'}, status=400)
            else:
                print("AOI not provided. Proceeding without spatial extent filter.")

            # Validate selected raster files
            if not selected_files:
                return JsonResponse({'status': 'error', 'message': 'No raster files selected.'}, status=400)

            # Process raster parameters for each file
            for i, file_path in enumerate(selected_files):
                if file_path not in raster_parameters:
                    return JsonResponse({'status': 'error', 'message': f'Missing parameters for raster {file_path}.'}, status=400)

                # Populate parameters dictionary for each raster
                parameters[f"in_raster_{i + 1}"] = 'data' + file_path
                parameters[f"min_val_{i + 1}"] = raster_parameters[file_path]['min_val']
                parameters[f"opti_from_{i + 1}"] = raster_parameters[file_path]['opti_from']
                parameters[f"opti_to_{i + 1}"] = raster_parameters[file_path]['opti_to']
                parameters[f"max_val_{i + 1}"] = raster_parameters[file_path]['max_val']
                parameters[f"combine_{i + 1}"] = raster_parameters[file_path]['combine']

            print("Processed Parameters:", json.dumps(parameters, indent=4))
            #return JsonResponse({'status': 'success', 'result_url': parameters})
            suitability_tool = LandSuitability(parameters,request.session)
            result_relative_url = suitability_tool.execute()

            # Construct the complete URL for the output file
            result_absolute_url = request.build_absolute_uri(result_relative_url)

            # Send the email with the absolute URL
            #suitability_tool.submit_message(result_absolute_url, parameters['emails'], suitability_tool.label)

            return JsonResponse({'status': 'success', 'result_url': result_absolute_url})

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})

    return HttpResponse(status=405)

def get_user_files(request):
    """
    Retrieve a list of files generated by the user during the session.
    """
    files = request.session.get("generated_files", [])
    return JsonResponse(files, safe=False)

def manage_session(request, action):
    if action == "flush":
        request.session.flush()
        return JsonResponse({"message": "Session completely cleared!"})
    elif action == "clear":
        request.session.clear()
        return JsonResponse({"message": "All session keys cleared, session still active."})
    elif action == "clear_key":
        key = "generated_files"
        request.session.pop(key, None)
        return JsonResponse({"message": f"Session key '{key}' cleared (if it existed)."})
    return JsonResponse({"message": "Invalid action!"})

@csrf_exempt
def process_land_similarity(request):
    if request.method == 'POST':
        try:
            # Parse the form data
            data = json.loads(request.body)

            # Extract the parameters
            # Extract the parameters
            selected_files = data.get('selectedFiles', [])
            coordinates = json.loads(data.get('points', '[]'))
            description = data.get('description', '').strip()
            if not description:
                return JsonResponse({'status': 'error', 'message': 'Description is required.'}, status=400)


          
           
            # Debugging logs
            print("Received coordinates:", coordinates)

            # Ensure coordinates are in [longitude, latitude] format
            features = []
            for coord in coordinates:
                if isinstance(coord, list) and len(coord) == 2:
                    lon, lat = coord
                    print(f"Processing coordinate: lon={lon}, lat={lat}")  # Debugging log
                    if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
                        features.append(geojson.Feature(geometry=geojson.Point((lon, lat))))
                    else:
                        print(f"Invalid coordinate values: {coord}")  # Debugging log
                        return JsonResponse({'status': 'error', 'message': f'Invalid coordinate values: {coord}'})
                else:
                    print(f"Invalid coordinate format: {coord}")  # Debugging log
                    return JsonResponse({'status': 'error', 'message': f'Invalid coordinate format: {coord}'})
            
            feature_collection = geojson.FeatureCollection(features)
            


            # Prepare parameters for the LandSimilarity class
            parameters = {
                'selectedFiles': selected_files,
                'in_point': feature_collection,
                'description': description
            }

            #return JsonResponse({'status': 'error', 'message': str(parameters)})

            # Instantiate and execute the LandSimilarity class
            land_similarity = LandSimilarity(parameters,request.session)
            result = land_similarity.execute()

            result_mnobis_url = request.build_absolute_uri(result['Mahalanobis'])
            result_mess_url = request.build_absolute_uri(result['MESS'])
            return JsonResponse({'status': 'success', 'result_url': {'mnobis': result_mnobis_url,'mess': result_mess_url}})
            #return JsonResponse(response_data)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})

    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'})

'''@csrf_exempt
def process_land_statistics(request):
    """
    Handle the processing of land statistics by accepting boundary GeoJSON, raster file path,
    and additional parameters like zone_id_column.
    """
    if request.method == 'POST':
        try:
            # Parse request data
            data = json.loads(request.body)
            boundaries_geojson = data.get('boundary')
            raster_file = data.get('raster_path')
            description = data.get('description', '')
            zone_id_column = data.get('zone_id_column')

            # Validate required inputs
            if not boundaries_geojson:
                return JsonResponse({'status': 'error', 'message': 'Boundary GeoJSON is required.'}, status=400)
            if not raster_file:
                return JsonResponse({'status': 'error', 'message': 'Raster file path is required.'}, status=400)
            if not zone_id_column:
                return JsonResponse({'status': 'error', 'message': 'Zone ID column is required.'}, status=400)

            # Initialize and process zonal statistics
            try:
                processor = LandStatistics(boundaries_geojson, raster_file, description, zone_id_column)
                print("Processor initialized. Computing statistics...")
                results = processor.compute_statistics()
                print("Statistics computed successfully:", results)
            except FileNotFoundError as fnfe:
                print("File not found error:", str(fnfe))
                return JsonResponse({'status': 'error', 'message': str(fnfe)}, status=404)
            except ValueError as ve:
                print("Value error during statistics computation:", str(ve))
                return JsonResponse({'status': 'error', 'message': str(ve)}, status=400)
            except Exception as e:
                print("Unexpected error during statistics computation:", str(e))
                return JsonResponse({'status': 'error', 'message': f'Unexpected error: {str(e)}'}, status=500)

            # Return successful results
            return JsonResponse({'status': 'success', 'results': results}, safe=False)

        except json.JSONDecodeError:
            print("Invalid JSON format in request body.")
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON format in request body.'}, status=400)
        except Exception as e:
            print("Unhandled exception:", str(e))
            return JsonResponse({'status': 'error', 'message': f'Unhandled server error: {str(e)}'}, status=500)

    print(f"Invalid request method: {request.method}")
    return JsonResponse({'status': 'error', 'message': f'Invalid request method: {request.method}'}, status=405)
'''
@csrf_exempt
def process_statistics(request):
    """
    Handle the processing of land statistics by accepting boundary GeoJSON, raster file path,
    and additional parameters like zone_id_column.
    """
    if request.method == 'POST':
        try:
            # Parse request data
            data = json.loads(request.body)
            raster_file = data.get('raster_path')
            description = data.get('description', '')
            reference_layer = data.get('reference_layer')
            stat_types = data.get('stat_types', [])

            # Validate required inputs
            if not reference_layer:
                return JsonResponse({'status': 'error', 'message': 'Reference Layer is required.'}, status=400)
            if not raster_file:
                return JsonResponse({'status': 'error', 'message': 'Raster file path is required.'}, status=400)
            

            # Initialize and process zonal statistics
            try:
                processor = LandStatistics(reference_layer, raster_file, description,stat_types)
                print("Processor initialized. Computing statistics...")
                results = processor.compute_statistics()
                print("Statistics computed successfully:", results)
            except FileNotFoundError as fnfe:
                print("File not found error:", str(fnfe))
                return JsonResponse({'status': 'error', 'message': str(fnfe)}, status=404)
            except ValueError as ve:
                print("Value error during statistics computation:", str(ve))
                return JsonResponse({'status': 'error', 'message': str(ve)}, status=400)
            except Exception as e:
                print("Unexpected error during statistics computation:", str(e))
                return JsonResponse({'status': 'error', 'message': f'Unexpected error: {str(e)}'}, status=500)

            # Return successful results
            return JsonResponse({'status': 'success', 'results': results}, safe=False)

        except json.JSONDecodeError:
            print("Invalid JSON format in request body.")
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON format in request body.'}, status=400)
        except Exception as e:
            print("Unhandled exception:", str(e))
            return JsonResponse({'status': 'error', 'message': f'Unhandled server error: {str(e)}'}, status=500)

    print(f"Invalid request method: {request.method}")
    return JsonResponse({'status': 'error', 'message': f'Invalid request method: {request.method}'}, status=405)