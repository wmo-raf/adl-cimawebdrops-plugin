from .constants import SENSOR_CLASS_MAP


def get_stations(network_conn):
    client = network_conn.get_api_client()
    
    stations_dict = client.get_stations()
    
    stations_list = []
    
    for key, station in stations_dict.items():
        station_id = station.get("station_id")
        station_name = station.get("station_name")
        stations_list.append({"label": station_name, "value": station_id})
    
    return stations_list


def get_station_parameters(network_conn, station_id):
    """
    Returns a list of parameters for a given station.
    """
    client = network_conn.get_api_client()
    
    parameters = client.get_station_parameters(station_id)
    
    if not parameters:
        return []
    
    params_list = []
    for param in parameters:
        param_class = param.get("class")
        param_unit = param.get("unit")
        
        param_class_english = SENSOR_CLASS_MAP.get(param_class, param_class)
        
        label = f"{param_class_english} ({param_unit})"
        
        sensor_ids = param.get("sensor_ids", [])
        
        for sensor_id in sensor_ids:
            param_info = {
                "label": f"{label} - {sensor_id}",
                "value": f"{param_class}:{sensor_id}",
            }
            
            params_list.append(param_info)
    
    return params_list
