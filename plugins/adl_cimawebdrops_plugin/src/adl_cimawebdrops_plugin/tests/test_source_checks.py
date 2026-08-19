"""
Tests for the ingestion-diagnostic contracts: ``get_source_endpoint()``,
``check_source()``, ``check_station_source()``, the ``adl_sources_count``
duck-typed handover and the exception stamping in ``client.py``. See the
"Ingestion Diagnostic Contracts" page in the ADL developer guide.

All tests run without touching the database: model instances are built unsaved
and the HTTP layer is stubbed, so the seam under test is exactly the contract
core consumes. That is what ``SimpleTestCase`` buys here — Django still calls
``setup_databases()`` whatever the class, so the suite is run on this plugin's
own compose stack with ``make test`` from the repo root.
"""

import ast
import os
from datetime import datetime, timezone
from unittest import mock

import requests
from adl.core.source_checks import SourceCheckResult, SourceCheckStatus
from django.test import SimpleTestCase

from adl_cimawebdrops_plugin.client import CimaWebDropsClient, category_for_status
from adl_cimawebdrops_plugin.models import CimaWebDropsConnection, CimaWebDropsStationLink
from adl_cimawebdrops_plugin.plugins import CimaWebdropsPlugin

TOKEN_ENDPOINT = "https://identity.example.org/auth/token"
API_BASE_URL = "https://webdrops.example.org/api"
API_HOST = "webdrops.example.org"

NOT_JSON = object()


class FakeResponse:
    """A stubbed ``requests`` response: status code, and a body that either
    parses or does not."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if self.payload is NOT_JSON:
            # What an HTML login page reached through a redirect looks like
            # from here. requests' own JSONDecodeError is a ValueError too.
            raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class FakeAPIClient:
    """A stubbed CIMA client that answers the one call a check makes."""

    def __init__(self, sensor_classes=None, stations=None, error=None, sensor_data=None):
        self.sensor_classes = sensor_classes if sensor_classes is not None else []
        self.stations = stations if stations is not None else {}
        self.error = error
        self.sensor_data = sensor_data or []
        self.sensor_calls = []

    def get_sensor_classes(self):
        if self.error is not None:
            raise self.error
        return self.sensor_classes

    def get_stations(self):
        if self.error is not None:
            raise self.error
        return self.stations

    def get_data_for_sensor(self, sensor_class, sensor_id, **kwargs):
        self.sensor_calls.append((sensor_class, sensor_id))
        answer = self.sensor_data[len(self.sensor_calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeVariableMapping:
    """The one attribute ``get_station_data()`` reads off a mapping."""

    def __init__(self, cima_sensor_info):
        self.cima_sensor_info = cima_sensor_info


def station_record(station_id="17.66206_33.97296", name="Wad Medani",
                   parameters=("TERMOMETRO", "PLUVIOMETRO")):
    return {
        "station_id": station_id,
        "station_name": name,
        "parameters": [{"class": c, "unit": None, "sensor_ids": ["1"]} for c in parameters],
    }


def make_connection(**kwargs):
    kwargs.setdefault("token_endpoint", TOKEN_ENDPOINT)
    kwargs.setdefault("client_id", "client")
    kwargs.setdefault("username", "user")
    kwargs.setdefault("password", "secret")
    kwargs.setdefault("api_base_url", API_BASE_URL)
    return CimaWebDropsConnection(**kwargs)


def make_station_link(connection=None, mappings=(), **kwargs):
    kwargs.setdefault("cima_station_id", "17.66206_33.97296")
    link = CimaWebDropsStationLink(**kwargs)
    link.network_connection = connection or make_connection()
    link.get_variable_mappings = lambda: list(mappings)
    return link


def make_client(**kwargs):
    """A real client with a token already in hand, so a test that stubs the
    data call does not also have to stub the token exchange."""
    kwargs.setdefault("token_endpoint", TOKEN_ENDPOINT)
    kwargs.setdefault("client_id", "client")
    kwargs.setdefault("username", "user")
    kwargs.setdefault("password", "secret")
    kwargs.setdefault("api_base_url", API_BASE_URL)
    kwargs.setdefault("use_cache", False)
    client = CimaWebDropsClient(**kwargs)
    client._access_token = "token"
    client._token_expiry_epoch = 2 ** 31
    return client


def stub_api_client(client):
    """Patch the client factory, capturing the arguments the check passed."""
    calls = []

    def factory(self, **kwargs):
        calls.append(kwargs)
        return client

    patcher = mock.patch.object(CimaWebDropsConnection, "get_api_client", autospec=True,
                                side_effect=factory)
    return patcher, calls


class GetApiClientTests(SimpleTestCase):
    """The factory's defaults are the ingestion path's behaviour, unchanged;
    only the on-demand checks ask for anything else."""

    def test_defaults_are_todays_ingestion_behaviour(self):
        client = make_connection().get_api_client()
        self.assertTrue(client.use_cache)
        self.assertEqual(client.timeout, 60)

    def test_checks_can_bound_and_bypass(self):
        client = make_connection().get_api_client(use_cache=False, timeout=5, retries=0)
        self.assertFalse(client.use_cache)
        self.assertEqual(client.timeout, 5)


class GetSourceEndpointTests(SimpleTestCase):

    def test_names_the_data_host_not_the_token_host(self):
        # Layer 4 is the path to the source, and the source is where the
        # observations come from.
        self.assertEqual(make_connection().get_source_endpoint(), (API_HOST, 443))

    def test_an_explicit_port_is_carried_through(self):
        connection = make_connection(api_base_url="http://webdrops.example.org:8080/api")
        self.assertEqual(connection.get_source_endpoint(), (API_HOST, 8080))

    def test_a_plain_http_base_url_defaults_to_80(self):
        connection = make_connection(api_base_url="http://webdrops.example.org/api")
        self.assertEqual(connection.get_source_endpoint(), (API_HOST, 80))


class CheckSourceTests(SimpleTestCase):

    def run_check(self, client, connection=None):
        connection = connection or make_connection()
        patcher, calls = stub_api_client(client)
        with patcher:
            result = connection.check_source()
        self.assertIsInstance(result, SourceCheckResult)
        self.assertIn(result.status, SourceCheckStatus.ALL)
        return result, calls

    def test_a_parsed_sensor_class_list_is_ok(self):
        result, _calls = self.run_check(FakeAPIClient(sensor_classes=["TERMOMETRO"]))
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIsNone(result.category)
        self.assertIn(API_HOST, result.message)
        self.assertIn("1", result.message)

    def test_bypasses_the_cache_and_bounds_the_call(self):
        _result, calls = self.run_check(FakeAPIClient(sensor_classes=[]))
        self.assertEqual(calls, [{"use_cache": False, "timeout": 5, "retries": 0}])

    def test_classifies_from_the_status_the_server_sent(self):
        for status, category in ((401, "AUTH_FAILED"), (403, "PERMISSION_DENIED"),
                                 (404, "PATH_NOT_FOUND"), (500, "PROTOCOL_ERROR"),
                                 (503, "PROTOCOL_ERROR")):
            with self.subTest(status=status):
                error = requests.HTTPError(response=FakeResponse(status))
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertEqual(result.category, category)
                self.assertIn(str(status), result.message)
                self.assertIn("/sensors/classes/", result.message)

    def test_declines_a_status_that_is_not_the_sources_fault(self):
        for status in (400, 422, 429):
            with self.subTest(status=status):
                error = requests.HTTPError(response=FakeResponse(status))
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertIsNone(result.category)

    def test_a_login_page_200_is_not_ok(self):
        # The body parsed but was not a taxonomy, or did not parse at all —
        # either way the source said nothing we can trust. A token response
        # without a token lands here too.
        for error in (ValueError("The response carried no sensor class list."),
                      ValueError("The token response carried no access token."),
                      requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)):
            with self.subTest(error=str(error)):
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertIsNone(result.category)
                self.assertIn("not a sensor class list", result.message)

    def test_a_codeless_failure_declines_the_category(self):
        # Core stamps every return layer 5, so a layer-4 category here would
        # have the diagnostic contradict itself about which layer failed.
        for error in (requests.ConnectionError("connection refused"),
                      requests.exceptions.SSLError("bad handshake"),
                      requests.exceptions.ReadTimeout("timed out")):
            with self.subTest(error=type(error).__name__):
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertIsNone(result.category)
                self.assertIn("could not be reached", result.message)

    def test_survives_the_core_normaliser(self):
        from adl.core.source_checks import normalise_source_check_result
        result, _calls = self.run_check(FakeAPIClient(sensor_classes=["TERMOMETRO"]))
        self.assertEqual(normalise_source_check_result(result).status, SourceCheckStatus.OK)

    def test_core_detects_the_override(self):
        from adl.core.source_checks import connection_implements_check_source
        self.assertTrue(connection_implements_check_source(make_connection()))


class CheckStationSourceTests(SimpleTestCase):

    def run_check(self, client, link=None):
        link = link or make_station_link()
        patcher, calls = stub_api_client(client)
        with patcher:
            result = link.check_station_source()
        self.assertIsInstance(result, SourceCheckResult)
        self.assertIn(result.status, SourceCheckStatus.ALL)
        return result, calls

    def test_a_present_id_is_ok_with_the_upstream_label_and_count(self):
        client = FakeAPIClient(stations={"17.66206_33.97296": station_record()})
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("17.66206_33.97296", result.message)
        self.assertIn("Wad Medani", result.message)
        self.assertIn("2", result.message)

    def test_a_present_id_without_a_label_still_reads_cleanly(self):
        client = FakeAPIClient(stations={"17.66206_33.97296": {"parameters": []}})
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("17.66206_33.97296", result.message)

    def test_zero_parameters_is_still_ok_with_the_zero_stated(self):
        client = FakeAPIClient(stations={
            "17.66206_33.97296": station_record(parameters=()),
        })
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("0", result.message)

    def test_an_unknown_id_is_proven_not_found(self):
        # The branch this check exists for: get_station_parameters() answers []
        # for a typo, which would have reported OK.
        client = FakeAPIClient(stations={"0.00000_0.00000": station_record("0.00000_0.00000")})
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "PATH_NOT_FOUND")
        self.assertIn("17.66206_33.97296", result.message)

    def test_an_empty_station_list_is_not_proof_of_absence(self):
        result, _calls = self.run_check(FakeAPIClient(stations={}))
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertIsNone(result.category)

    def test_bypasses_the_cache(self):
        # Harder here than at connection scope: a day-old list would report a
        # station added upstream yesterday as proven missing.
        client = FakeAPIClient(stations={"17.66206_33.97296": station_record()})
        _result, calls = self.run_check(client)
        self.assertEqual(calls, [{"use_cache": False, "timeout": 5, "retries": 0}])

    def test_a_failed_read_is_never_converted_into_ok(self):
        for error in (requests.ConnectionError("connection refused"),
                      requests.HTTPError(response=FakeResponse(500)),
                      ValueError("The response carried no sensor class list.")):
            with self.subTest(error=type(error).__name__):
                result, _calls = self.run_check(FakeAPIClient(error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertNotEqual(result.category, "PATH_NOT_FOUND")

    def test_core_detects_the_override(self):
        from adl.core.source_checks import station_link_implements_check_station_source
        self.assertTrue(station_link_implements_check_station_source(make_station_link()))


class SourcesCountTests(SimpleTestCase):
    """The count is committed only from something the source told us, and only
    once it has told us."""

    START = datetime(2026, 8, 1, tzinfo=timezone.utc)
    END = datetime(2026, 8, 2, tzinfo=timezone.utc)

    MAPPINGS = (FakeVariableMapping("TERMOMETRO:1"), FakeVariableMapping("PLUVIOMETRO:2"))

    def collect(self, link, client):
        patcher, _calls = stub_api_client(client)
        with patcher:
            return CimaWebdropsPlugin().get_station_data(link, self.START, self.END)

    def test_counts_the_entries_every_response_carried(self):
        link = make_station_link(mappings=self.MAPPINGS)
        client = FakeAPIClient(sensor_data=[
            ({"202608010000": 21.5, "202608010100": 22.0}, 2),
            ({"202608010000": 0.0}, 1),
        ])
        records = self.collect(link, client)
        self.assertEqual(link.adl_sources_count, 3)
        self.assertEqual(len(records), 2)

    def test_an_empty_answer_is_zero_not_silence(self):
        link = make_station_link(mappings=self.MAPPINGS)
        client = FakeAPIClient(sensor_data=[({}, 0), ({}, 0)])
        self.collect(link, client)
        self.assertEqual(link.adl_sources_count, 0)

    def test_a_first_call_that_fails_makes_no_claim_at_all(self):
        # None, never 0: a run that never got an answer must not accuse the
        # source of having offered nothing.
        link = make_station_link(mappings=self.MAPPINGS)
        link.adl_sources_count = None
        client = FakeAPIClient(sensor_data=[requests.ConnectionError("refused")])
        with self.assertRaises(requests.ConnectionError):
            self.collect(link, client)
        self.assertIsNone(link.adl_sources_count)

    def test_a_later_failure_keeps_what_the_source_already_offered(self):
        link = make_station_link(mappings=self.MAPPINGS)
        link.adl_sources_count = None
        client = FakeAPIClient(sensor_data=[
            ({"202608010000": 21.5}, 1),
            requests.ConnectionError("refused"),
        ])
        with self.assertRaises(requests.ConnectionError):
            self.collect(link, client)
        self.assertEqual(link.adl_sources_count, 1)

    def test_never_counts_our_own_variable_mappings(self):
        # Two mappings, one entry offered. A count of 2 would be our config
        # read back to us as though the source had said it.
        link = make_station_link(mappings=self.MAPPINGS)
        client = FakeAPIClient(sensor_data=[({"202608010000": 21.5}, 1), ({}, 0)])
        self.collect(link, client)
        self.assertEqual(link.adl_sources_count, 1)

    def test_the_count_is_taken_before_the_collapse_into_timestamps(self):
        # Three raw entries, two of which share a timestamp and collapse into
        # one reading. The source offered three.
        payload = [{
            "timeline": ["202608010000", "202608010000", "202608010100"],
            "values": [21.5, 21.6, 22.0],
        }]
        client = make_client()
        with mock.patch.object(client.session, "get", return_value=FakeResponse(200, payload)):
            readings, count = client.get_data_for_sensor("TERMOMETRO", "1")
        self.assertEqual(count, 3)
        self.assertEqual(len(readings), 2)


class ExceptionStampingTests(SimpleTestCase):
    """A failed ingestion run carries the source's own verdict into the
    activity log, stamped in place so core's type table still applies."""

    def get_sensor_classes(self, response):
        client = make_client()
        with mock.patch.object(client.session, "get", return_value=response):
            return client.get_sensor_classes()

    def exchange_token(self, response):
        client = make_client()
        client._access_token = None
        client._token_expiry_epoch = 0
        with mock.patch.object(client.session, "post", return_value=response):
            return client._ensure_token()

    def test_stamps_a_classified_status_at_layer_5(self):
        for status, category in ((401, "AUTH_FAILED"), (403, "PERMISSION_DENIED"),
                                 (404, "PATH_NOT_FOUND"), (502, "PROTOCOL_ERROR")):
            with self.subTest(status=status):
                with self.assertRaises(requests.HTTPError) as caught:
                    self.get_sensor_classes(FakeResponse(status))
                self.assertEqual(caught.exception.adl_category, category)
                self.assertEqual(caught.exception.adl_layer, 5)

    def test_stamps_the_token_exchange_too(self):
        # A code from the server is proof the server answered, so a 401 here is
        # AUTH_FAILED at layer 5 like any other.
        with self.assertRaises(requests.HTTPError) as caught:
            self.exchange_token(FakeResponse(401))
        self.assertEqual(caught.exception.adl_category, "AUTH_FAILED")
        self.assertEqual(caught.exception.adl_layer, 5)

    def test_leaves_a_declined_status_unstamped(self):
        # Declining keeps core's read-time tier free to classify the row later;
        # a stamp — UNKNOWN above all — would block it permanently.
        for status in (400, 422, 429):
            with self.subTest(status=status):
                with self.assertRaises(requests.HTTPError) as caught:
                    self.get_sensor_classes(FakeResponse(status))
                self.assertFalse(hasattr(caught.exception, "adl_category"))

    def test_core_reads_the_stamp(self):
        from adl.core.classification import classify_failure
        with self.assertRaises(requests.HTTPError) as caught:
            self.get_sensor_classes(FakeResponse(401))
        self.assertEqual(classify_failure(caught.exception), ("AUTH_FAILED", 5))

    def test_a_body_that_is_not_a_sensor_class_list_raises(self):
        for payload in (NOT_JSON, {"error": "unauthorized"}, None):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.get_sensor_classes(FakeResponse(200, payload))

    def test_a_token_response_without_a_token_raises(self):
        for payload in (NOT_JSON, {"error": "invalid_grant"}, {"access_token": ""}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.exchange_token(FakeResponse(200, payload))

    def test_the_status_table_declines_what_is_not_the_sources_fault(self):
        self.assertIsNone(category_for_status(302))
        self.assertIsNone(category_for_status(429))
        self.assertEqual(category_for_status(404), "PATH_NOT_FOUND")


class OlderCoreImportSafetyTests(SimpleTestCase):
    """The plugin must import cleanly on a core release that predates the
    source-check contracts, so nothing may import ``adl.core.source_checks``
    at module level.

    The contracts import it lazily instead, inside the method that needs it.
    Never wrap that import in ``try/except ImportError``: on an older core the
    method is never called, so the handler is unreachable, and it would turn a
    genuine import failure into a silent "this plugin does not support the
    check".
    """

    # Every module this plugin ships. Extend it as the plugin grows more.
    MODULES = ["models.py", "plugins.py", "client.py", "apps.py", "views.py",
               "utils.py", "validators.py", "widgets.py", "wagtail_hooks.py",
               "constants.py"]

    DENIED = "adl.core.source_checks"

    def test_no_module_level_import_of_source_checks(self):
        package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in self.MODULES:
            path = os.path.join(package_dir, name)
            if not os.path.exists(path):
                continue  # a module this plugin does not (yet) ship
            with open(path) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if node.col_offset != 0:
                    continue  # indented imports are lazy, inside a function
                names = [a.name for a in node.names]
                module = getattr(node, "module", "") or ""
                self.assertNotIn(
                    self.DENIED, [module] + names,
                    f"{name} imports {self.DENIED} at module level")
