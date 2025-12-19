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
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.decorators import user_passes_test

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

def normalize_aoi_to_geometry(aoi):
    """
    Normalize AOI input (dict or JSON string) into a GeoJSON geometry dict.
    Supports: Feature, FeatureCollection, Polygon, MultiPolygon.
    """
    aoi_data = json.loads(aoi) if isinstance(aoi, str) else aoi

    if not isinstance(aoi_data, dict) or "type" not in aoi_data:
        raise ValueError("Invalid AOI GeoJSON: missing 'type'.")

    t = aoi_data["type"]

    # Feature -> geometry
    if t == "Feature":
        geom = aoi_data.get("geometry")
        if not geom:
            raise ValueError("GeoJSON Feature is missing 'geometry'.")
        return geom

    # FeatureCollection -> use first feature geometry
    if t == "FeatureCollection":
        feats = aoi_data.get("features", [])
        if not feats:
            raise ValueError("GeoJSON FeatureCollection has no features.")
        geom = feats[0].get("geometry")
        if not geom:
            raise ValueError("First feature in FeatureCollection has no geometry.")
        return geom

    # Already a geometry
    if t in ("Polygon", "MultiPolygon"):
        return aoi_data

    raise ValueError(f"Unsupported AOI type: {t}. Expected Feature/FeatureCollection/Polygon/MultiPolygon.")


@require_POST
@csrf_protect
def process_land_suitability(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    try:
        form_data = json.loads(request.body)

        aoi = form_data.get("aoi", None)  # optional
        selected_files = form_data.get("selectedFiles", [])
        raster_parameters = form_data.get("rasterParameters", {})
        description = (form_data.get("description", "") or "").strip()

        if not description:
            return JsonResponse(
                {"status": "error", "message": "Description is required."},
                status=400
            )

        # Initialize parameters
        parameters = {"description": description}

        # -------------------------
        # AOI handling
        # -------------------------
        if aoi:
            # Case 1: AOI is dict already (GeoJSON)
            if isinstance(aoi, dict):
                geometry = normalize_aoi_to_geometry(aoi)

            # Case 2: AOI is string
            elif isinstance(aoi, str):
                aoi_str = aoi.strip()

                # Try GeoJSON JSON string first
                try:
                    geometry = normalize_aoi_to_geometry(aoi_str)
                except json.JSONDecodeError:
                    # Not JSON -> try your custom formats
                    if ";" in aoi_str:
                        # semicolon polygon string -> convert
                        geometry = convert_aoi_to_geojson(aoi_str)
                    elif "," in aoi_str:
                        # bbox string "minLat,minLon,maxLat,maxLon" (your current interpretation)
                        coords = [c.strip() for c in aoi_str.split(",")]
                        if len(coords) != 4:
                            return JsonResponse(
                                {"status": "error", "message": "Invalid AOI bbox format. Expected 4 comma-separated values."},
                                status=400
                            )
                        min_lat, min_lon, max_lat, max_lon = map(float, coords)
                        geometry = {
                            "type": "Polygon",
                            "coordinates": [[
                                [min_lon, min_lat],
                                [max_lon, min_lat],
                                [max_lon, max_lat],
                                [min_lon, max_lat],
                                [min_lon, min_lat],
                            ]]
                        }
                    else:
                        return JsonResponse(
                            {"status": "error", "message": "Invalid AOI format."},
                            status=400
                        )
            else:
                return JsonResponse(
                    {"status": "error", "message": "Unsupported AOI input type."},
                    status=400
                )

            # Ensure AOI geometry is polygonal
            if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
                return JsonResponse(
                    {"status": "error", "message": f"Unsupported AOI geometry: {geometry.get('type')}. Only Polygon/MultiPolygon are supported."},
                    status=400
                )

            parameters["out_extent"] = geometry
            print("AOI processed as GeoJSON geometry:", parameters["out_extent"])

        else:
            print("AOI not provided. Proceeding without spatial extent filter.")

        # -------------------------
        # Validate selected rasters
        # -------------------------
        if not selected_files:
            return JsonResponse(
                {"status": "error", "message": "No raster files selected."},
                status=400
            )

        # -------------------------
        # Collect raster params
        # -------------------------
        for i, file_path in enumerate(selected_files):
            if file_path not in raster_parameters:
                return JsonResponse(
                    {"status": "error", "message": f"Missing parameters for raster {file_path}."},
                    status=400
                )

            rp = raster_parameters[file_path]

            parameters[f"in_raster_{i + 1}"] = "data" + file_path
            parameters[f"min_val_{i + 1}"] = rp["min_val"]
            parameters[f"opti_from_{i + 1}"] = rp["opti_from"]
            parameters[f"opti_to_{i + 1}"] = rp["opti_to"]
            parameters[f"max_val_{i + 1}"] = rp["max_val"]
            parameters[f"combine_{i + 1}"] = rp["combine"]

        print("Processed Parameters:", json.dumps(parameters, indent=4))

        # Run tool
        suitability_tool = LandSuitability(parameters, request.session)
        result_relative_url = suitability_tool.execute()
        result_absolute_url = request.build_absolute_uri(result_relative_url)

        return JsonResponse({"status": "success", "result_url": result_absolute_url})

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

def get_user_files(request):
    """
    Retrieve a list of files generated by the user during the session.
    """
    files = request.session.get("generated_files", [])
    return JsonResponse(files, safe=False)



@user_passes_test(lambda u: u.is_superuser)
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

@require_POST
@csrf_protect
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
@require_POST
@csrf_protect
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