from land_suitability2 import LandSuitability

parameters= {
    "emails": "",
    "out_extent": "5.2828729778253525,36.202156982009875,12.084534381500971,42.18170274595235",
    "in_raster_1": "data/Africa/Ethiopia/ethiopia_annual_evapo_transpiration.tif",
    "min_val_1": "1239",
    "opti_from_1": "1500",
    "opti_to_1": "2000",
    "max_val_1": "2901",
    "combine_1": "No",
    "in_raster_2": "data/Africa/Ethiopia/ethiopia_annual_precipitation.tif",
    "min_val_2": "102",
    "opti_from_2": "150",
    "opti_to_2": "1000",
    "max_val_2": "2002",
    "combine_2": "Yes"
}


# Initialize the tool with test parameters
land_suitability_tool = LandSuitability(parameters)

# Execute the tool
try:
    result = land_suitability_tool.execute()
    print(f"Execution successful! Result saved at: {result}")
except Exception as e:
    print(f"Execution failed: {e}")
