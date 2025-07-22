from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing_page, name='landing_page'),
    path('suitability', views.suitability, name='suitability'),
    path('similarity', views.similarity, name='similarity'),
    path('statistics', views.statistics, name='statistics'),
    path('api/getFolderConfigurations', views.get_folder_configurations, name='get_folder_configurations'),
    path('api/getDirectoryContents', views.get_directory_contents, name='get_directory_contents'),
    path('api/processLandSuitability', views.process_land_suitability, name='process_land_suitability'),
    path('api/processLandSimilarity', views.process_land_similarity, name='process_land_similarity'),
    path('api/getUserFiles', views.get_user_files, name='get_user_files'),
    path('api/manageSession/<str:action>', views.manage_session, name='manage_session'),
    #path('api/processLandStatistics', views.process_land_statistics, name='process_land_statistics'),
    path('api/processStatistics', views.process_statistics, name='process_statistics'),
    path('api/getReferenceLayers', views.get_reference_layers, name='get_reference_layers'),
]+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
