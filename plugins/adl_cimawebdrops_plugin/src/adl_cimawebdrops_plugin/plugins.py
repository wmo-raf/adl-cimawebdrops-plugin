from datetime import timedelta

from adl.core.registries import Plugin


class CimaWebdropsPlugin(Plugin):
    type = "adl_cimawebdrops_plugin"
    label = "ADL CIMA Webdrops Plugin"
    
    def get_urls(self):
        return []
    
    def get_start_date_from_db(self, station_link):
        start_date = super().get_start_date_from_db(station_link)
        if start_date:
            # Slight offset to avoid re-fetching the last stored sample
            start_date += timedelta(minutes=1)
        return start_date
    
    def get_station_data(self, station_link, start_date=None, end_date=None):
        dt_from = start_date.strftime("%Y%m%d%H%M") if start_date else None
        dt_to = end_date.strftime("%Y%m%d%H%M") if end_date else None
        
        client = station_link.network_connection.get_api_client()
        variable_mappings = station_link.get_variable_mappings()
        
        sensors_info = []
        for mapping in variable_mappings:
            cima_sensor_info = mapping.cima_sensor_info
            cima_sensor_info_parts = cima_sensor_info.split(":")
            
            # cima_sensor_info is in the format "sensor_class:sensor_id"
            # here we need only the sensor class
            if len(cima_sensor_info_parts) == 2:
                sensors_info.append({
                    "sensor_class": cima_sensor_info_parts[0],
                    "sensor_id": cima_sensor_info_parts[1]
                })
        
        records = client.get_data_for_sensors(sensors_info, date_from=dt_from, date_to=dt_to)
        
        return records
