from adl.core.utils import get_object_or_none
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _

from .models import CimaWebDropsConnection
from .utils import get_stations, get_station_parameters


def get_cima_webdrops_stations_for_connection(request):
    network_connection_id = request.GET.get('connection_id')
    
    if not network_connection_id:
        response = {
            "error": _("Network connection ID is required.")
        }
        return JsonResponse(response, status=400)
    
    network_conn = get_object_or_none(CimaWebDropsConnection, pk=network_connection_id)
    if not network_conn:
        response = {
            "error": _("The selected connection is not a CIMA WebDrops API Connection")
        }
        
        return JsonResponse(response, status=400)
    
    stations_list = get_stations(network_conn)
    
    return JsonResponse(stations_list, safe=False)


def get_cima_webdrops_variables_for_connection(request):
    network_connection_id = request.GET.get('connection_id')
    
    if not network_connection_id:
        response = {
            "error": _("Network connection ID is required.")
        }
        return JsonResponse(response, status=400)
    
    network_conn = get_object_or_none(CimaWebDropsConnection, pk=network_connection_id)
    if not network_conn:
        response = {
            "error": _("The selected connection is not a CIMA WebDrops API Connection")
        }
        
        return JsonResponse(response, status=400)
    
    station_id = request.GET.get('station_id')
    if not station_id:
        response = {
            "error": _("Station ID is required.")
        }
        return JsonResponse(response, status=400)
    
    parameters_list = get_station_parameters(network_conn, station_id)
    
    return JsonResponse(parameters_list, safe=False)
