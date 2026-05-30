from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing_page, name='landing_page'),
    path('suitability', views.suitability, name='suitability'),
    path('similarity', views.similarity, name='similarity'),
    path('statistics', views.statistics, name='statistics'),
    path('resources', views.resources, name='resources'),
    path('api/getFolderConfigurations', views.get_folder_configurations, name='get_folder_configurations'),
    path('api/getDirectoryContents', views.get_directory_contents, name='get_directory_contents'),
    path('api/processLandSuitability', views.process_land_suitability, name='process_land_suitability'),
    path('api/processLandSimilarity', views.process_land_similarity, name='process_land_similarity'),
    path('api/getUserFiles', views.get_user_files, name='get_user_files'),
    path('api/manageSession/<str:action>', views.manage_session, name='manage_session'),
    path('api/processStatistics', views.process_statistics, name='process_statistics'),
    path('api/queryPoint', views.query_point, name='query_point'),
    path('api/reportSuitability', views.report_suitability, name='report_suitability'),
    path('api/reportSimilarity', views.report_similarity, name='report_similarity'),
    path('api/reportStatistics', views.report_statistics, name='report_statistics'),
    path('api/getReferenceLayers', views.get_reference_layers, name='get_reference_layers'),
    path('api/serveRaster', views.serve_raster, name='serve_raster'),
    path('api/tile/<int:z>/<int:x>/<int:y>.png', views.tile_raster, name='tile_raster'),
    path('api/rasterMeta', views.raster_meta, name='raster_meta'),
    path('api/layerMetadata', views.layer_metadata, name='layer_metadata'),
]+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
