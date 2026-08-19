from datetime import datetime, timedelta

from adl.core.registries import Plugin


class CimaWebdropsPlugin(Plugin):
    type = "adl_cimawebdrops_plugin"
    label = "ADL CIMA Webdrops Plugin"

    # Webdrops constraint: MAX_ALLOWED_SENSOR_QUERY_HOURS = 240 (10 days)
    MAX_QUERY_HOURS = 240

    def get_urls(self):
        return []

    def get_start_date_from_db(self, station_link):
        start_date = super().get_start_date_from_db(station_link)
        if start_date:
            # Slight offset to avoid re-fetching the last stored sample
            start_date += timedelta(minutes=1)
        return start_date

    def get_dates_for_station(self, station_link, latest=False):
        start_date, end_date = super().get_dates_for_station(station_link, latest=latest)

        # If either bound is missing, don't try to clamp (super() decides behavior)
        if not start_date or not end_date:
            return start_date, end_date

        max_window = timedelta(hours=self.MAX_QUERY_HOURS)

        # If requested period exceeds Webdrops max, cap end_date
        if end_date - start_date > max_window:
            end_date = start_date + max_window

        return start_date, end_date

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
            if len(cima_sensor_info_parts) == 2:
                sensors_info.append({
                    "sensor_class": cima_sensor_info_parts[0],
                    "sensor_id": cima_sensor_info_parts[1]
                })

        station_data = {}

        for sensor in sensors_info:
            sensor_class = sensor["sensor_class"]
            sensor_id = sensor["sensor_id"]

            sensor_data, sources_count = client.get_data_for_sensor(
                sensor_class, sensor_id, date_from=dt_from, date_to=dt_to, date_as_string=True
            )

            # Duck-typed sources-count handover: core stores this on the run's
            # activity log so "looked, found nothing" (0) stays distinguishable
            # from "never looked" (None). Committed per sensor, each time a
            # response has been received and parsed — a run whose first call
            # raises leaves the attribute None and makes no claim, while one
            # that fails after three sensors answered keeps what those three
            # offered and so acquits the source.
            #
            # Never len(sensors_info): that is a count of our own configured
            # variable mappings, knowable without touching the network, so it
            # would say nothing about the source while looking like evidence.
            if getattr(station_link, "adl_sources_count", None) is None:
                station_link.adl_sources_count = 0
            station_link.adl_sources_count += sources_count

            if not sensor_data:
                continue

            for obs_date_str, value in sensor_data.items():
                if obs_date_str not in station_data:
                    obs_date_obj = datetime.strptime(obs_date_str, "%Y%m%d%H%M")
                    station_data[obs_date_str] = {
                        "observation_time": obs_date_obj
                    }

                station_data[obs_date_str][f"{sensor_class}:{sensor_id}"] = value

        return list(station_data.values())
