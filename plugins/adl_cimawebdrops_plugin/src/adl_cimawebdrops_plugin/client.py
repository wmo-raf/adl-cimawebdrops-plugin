import time
from datetime import datetime

import requests
from django.core.cache import cache

# Today's ingestion timeout, unchanged. The diagnostic's on-demand checks pass
# their own bound instead — see models.SOURCE_CHECK_TIMEOUT_SECONDS.
DEFAULT_TIMEOUT = 60

SENSOR_CLASSES_PATH = "/sensors/classes/"

# The ingestion diagnostic's shared HTTP status table. The category strings are
# written out rather than imported from core: importing core's vocabulary would
# break this plugin at import time on an older core, and core drops any value it
# does not recognise anyway.
#
# 400 and 422 decline because a malformed request is our bug, 429 because rate
# limiting is our polling schedule, and 3xx because a redirect says nothing
# about the source. Nothing here ever stamps UNKNOWN: declining leaves core's
# read-time classification free to do better later, and a stamp does not.
STATUS_CATEGORIES = {
    401: "AUTH_FAILED",
    403: "PERMISSION_DENIED",
    404: "PATH_NOT_FOUND",
}


def category_for_status(status_code):
    """The diagnostic failure category for an HTTP status, or None where the
    status carries no honest one."""
    if status_code in STATUS_CATEGORIES:
        return STATUS_CATEGORIES[status_code]
    if status_code is not None and 500 <= status_code < 600:
        return "PROTOCOL_ERROR"
    return None


def _raise_for_status(response):
    """``raise_for_status()``, tagging the raised error for the diagnostic.

    The exception is stamped in place rather than wrapped, so the original type
    still matches core's own exception table and the traceback survives. A code
    from the server is proof the server answered, which is what makes every
    category derived from one layer 5.
    """
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        category = category_for_status(e.response.status_code)
        if category:
            e.adl_category = category
            e.adl_layer = 5
        raise


def generate_station_id(lat: float, lng: float, precision: int = 5) -> str:
    """
    Generate a reproducible, human-readable station ID from coordinates.
    Example: 17.66206_33.97296
    """
    return f"{round(float(lat), precision):.{precision}f}_{round(float(lng), precision):.{precision}f}"


class CimaWebDropsClient(object):
    def __init__(self, token_endpoint, client_id, username, password, api_base_url,
                 timeout=DEFAULT_TIMEOUT, retries=None, use_cache=True):
        self.token_endpoint = token_endpoint
        self.client_id = client_id
        self.username = username
        self.password = password
        self.api_base_url = api_base_url
        self.timeout = timeout
        self.use_cache = use_cache

        self._access_token = None
        self._token_expiry_epoch = 0  # epoch seconds

        self.session = requests.Session()
        if retries is not None:
            # Mounted only when asked for. requests' default adapter already
            # retries nothing, so the ingestion path keeps its behaviour and
            # the on-demand diagnostic checks can say so explicitly.
            adapter = requests.adapters.HTTPAdapter(max_retries=retries)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

    def _ensure_token(self):
        now = int(time.time())

        if self._access_token and now < self._token_expiry_epoch - 30:
            return  # still valid (30s safety margin)

        # Obtain fresh token via password grant
        r = self.session.post(
            self.token_endpoint,
            data={
                "grant_type": "password",
                "client_id": self.client_id,
                "username": self.username,
                "password": self.password,
            },
            timeout=self.timeout,
        )
        _raise_for_status(r)
        data = r.json()

        # A 2xx is not proof of a token: requests follows redirects, so an
        # expired session landing on an HTML login page arrives here as a clean
        # 200, and a JSON body without the key is the same non-answer. Both
        # raise ValueError, which is what the source check catches.
        if not isinstance(data, dict) or not data.get("access_token"):
            raise ValueError("The token response carried no access token.")

        self._access_token = data["access_token"]
        # Many IdPs return 'expires_in' seconds; fall back to 300s if missing
        self._token_expiry_epoch = int(time.time()) + int(data.get("expires_in", 300))

    def _auth_headers(self):
        self._ensure_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def get_sensor_classes(self):
        cache_key = f"{self.client_id}-cima-sensor-classes"

        if self.use_cache and cache.get(cache_key):
            return cache.get(cache_key)

        url = f"{self.api_base_url}{SENSOR_CLASSES_PATH}"
        r = self.session.get(url, headers=self._auth_headers(), timeout=self.timeout)
        _raise_for_status(r)

        sensor_classes = r.json()

        if not isinstance(sensor_classes, list):
            raise ValueError("The response carried no sensor class list.")

        if self.use_cache:
            # Cache for 24 hours
            cache.set(cache_key, sensor_classes, 86400)

        return sensor_classes

    def get_sensors_list_for_class(self, sensor_class: str):
        cache_key = f"{self.client_id}-cima-sensor-list-class-{sensor_class}"

        if self.use_cache:
            value = cache.get(cache_key)
            if value is not None:
                return cache.get(cache_key)

        url = f"{self.api_base_url}/sensors/list/{sensor_class}/"
        r = self.session.get(url, headers=self._auth_headers(), timeout=self.timeout)
        _raise_for_status(r)

        sensors = r.json()

        if self.use_cache:
            # Cache for 24 hours
            cache.set(cache_key, sensors, 86400)

        return sensors

    def get_unique_stations_with_parameters(self, sensor_classes: list, coord_precision: int = 5):
        """
        Returns a list of unique stations and the parameters they monitor.

        Output shape:
        [
          {
            "station_id": str,         # e.g. "17.66206_33.97296"
            "station_name": str,
            "lat": float,
            "lng": float,
            "parameters": [
              {
                "class": str,            # e.g. "TERMOMETRO"
                "unit": str | None,      # e.g. "°C"
                "sensor_ids": [str, ...] # sensor IDs for that class here
              },
              ...
            ]
          },
          ...
        ]
        """
        stations_index = {}

        for sensor_class in sensor_classes:
            sensors_list_for_class = self.get_sensors_list_for_class(sensor_class) or []

            for s in sensors_list_for_class:
                name = (s.get("name") or "").strip()
                lat = s.get("lat")
                lng = s.get("lng")
                mu = s.get("mu")  # unit
                sid = s.get("id")

                if lat is None or lng is None:
                    continue  # skip malformed entries

                # Round coords for stability
                lat_r = round(float(lat), coord_precision)
                lng_r = round(float(lng), coord_precision)

                # Unique station key & ID
                key = (lat_r, lng_r)
                station_id = generate_station_id(lat_r, lng_r, coord_precision)

                if key not in stations_index:
                    stations_index[key] = {
                        "station_id": station_id,
                        "station_name": name,
                        "lat": lat_r,
                        "lng": lng_r,
                        "parameters": {}
                    }

                station_entry = stations_index[key]

                # Add or merge parameter info
                if sensor_class not in station_entry["parameters"]:
                    station_entry["parameters"][sensor_class] = {
                        "unit": mu,
                        "sensor_ids": set()
                    }

                if mu and not station_entry["parameters"][sensor_class]["unit"]:
                    station_entry["parameters"][sensor_class]["unit"] = mu

                if sid:
                    station_entry["parameters"][sensor_class]["sensor_ids"].add(str(sid))

        # Convert sensor_ids to list and sort
        result = []
        for st in stations_index.values():
            params_list = []
            for cls_name, meta in sorted(st["parameters"].items(), key=lambda kv: kv[0]):
                params_list.append({
                    "class": cls_name,
                    "unit": meta["unit"],
                    "sensor_ids": sorted(meta["sensor_ids"])
                })
            st["parameters"] = params_list
            result.append(st)

        # Sort stations for consistent output
        result.sort(key=lambda x: (x["station_name"], x["lat"], x["lng"]))
        return result

    def get_stations(self):
        """
        Returns a dictionary of stations with their details.
        The keys are station IDs generated from coordinates.
        """

        cache_key = f"{self.client_id}-cima-stations"

        if self.use_cache and cache.get(cache_key):
            return cache.get(cache_key)

        sensor_classes = self.get_sensor_classes()

        if not sensor_classes:
            return {}

        stations = self.get_unique_stations_with_parameters(sensor_classes)

        # Convert to dict keyed by station_id
        stations_dict = {st["station_id"]: st for st in stations}

        if self.use_cache:
            # Cache for 24 hours
            cache.set(cache_key, stations_dict, 86400)

        return stations_dict

    def get_station_parameters(self, station_id: str):
        """
        Returns a list of parameters for a given station.
        Each parameter includes its class, unit, and sensor IDs.
        """

        cache_key = f"{self.client_id}-cima-station-params-{station_id}"

        if self.use_cache and cache.get(cache_key):
            return cache.get(cache_key)

        stations = self.get_stations()

        if station_id not in stations:
            return []

        station_info = stations[station_id]

        if "parameters" not in station_info:
            return []

        parameters = station_info["parameters"]

        # Convert to list format
        params_list = [
            {
                "class": param["class"],
                "unit": param["unit"],
                "sensor_ids": param["sensor_ids"]
            } for param in parameters
        ]

        if self.use_cache:
            # Cache for 24 hours
            cache.set(cache_key, params_list, 86400)

        return params_list

    def get_data_for_sensor(self, sensor_class, sensor_id, date_from=None, date_to=None, date_as_string=True):
        """
        Fetches data for a specific sensor within the given date range.

        :param sensor_class: Class of the sensor (e.g., "TERMOMETRO")
        :param sensor_id: ID of the sensor
        :param date_from: Start date in "YYYYMMDDHHMM" format
        :param date_to: End date in "YYYYMMDDHHMM" format
        :return: List of records with sensor data
        """

        url = f"{self.api_base_url}/sensors/data/{sensor_class}/{sensor_id}/"

        params = {}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to

        if date_as_string:
            params["date_as_string"] = "true"

        r = self.session.get(url, headers=self._auth_headers(), params=params, timeout=self.timeout)
        _raise_for_status(r)

        data = r.json()
        timeline = data[0]["timeline"]
        values = data[0]["values"]

        # Create dictionary with date as key and value as reading
        date_value_dict = dict(zip(timeline, values))

        return date_value_dict

    def get_data_for_sensors(self, sensors_info, date_from=None, date_to=None, date_as_string=True):
        """
        Fetches data for the specified sensors within the given date range.

        :param sensors_info: List of dicts with sensor class and ID
        :param date_from: Start date in "YYYYMMDDHHMM" format
        :param date_to: End date in "YYYYMMDDHHMM" format
        :return: List of records with sensor data
        """

        if not sensors_info:
            return []

        station_data = {}

        for sensor in sensors_info:
            if "sensor_class" not in sensor or "sensor_id" not in sensor:
                raise ValueError("Each sensor must have 'sensor_class' and 'sensor_id' keys")

            sensor_class = sensor["sensor_class"]
            sensor_id = sensor["sensor_id"]

            sensor_data = self.get_data_for_sensor(sensor_class, sensor_id, date_from, date_to, date_as_string=True)
            if not sensor_data:
                continue

            for obs_date_str, value in sensor_data.items():
                if obs_date_str not in station_data:
                    if date_as_string:
                        # Convert string date to datetime object
                        obs_date_obj = datetime.strptime(obs_date_str, "%Y%m%d%H%M")
                    else:
                        obs_date_obj = datetime.strptime(obs_date_str, "%Y-%m-%dT%H:%M:%SZ")
                    station_data[obs_date_str] = {
                        "observation_time": obs_date_obj
                    }

                station_data[obs_date_str][f"{sensor_class}:{sensor_id}"] = value

        return list(station_data.values())
