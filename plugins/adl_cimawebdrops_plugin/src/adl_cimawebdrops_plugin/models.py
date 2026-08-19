from urllib.parse import urlparse

import requests
from adl.core.models import NetworkConnection, StationLink, DataParameter, Unit
from django.db import models
from django.utils.translation import gettext, gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import MultiFieldPanel, FieldPanel, InlinePanel
from wagtail.models import Orderable

from .client import (
    DEFAULT_TIMEOUT,
    SENSOR_CLASSES_PATH,
    CimaWebDropsClient,
    category_for_status,
)
from .validators import validate_start_date
from .widgets import CimaWebDropsStationSelectWidget, CimaWebDropsVariableSelectWidget

# What the diagnostic's on-demand checks pass instead of the ingestion
# defaults. Core bounds its whole probe — DNS, TCP and the source check
# together — by a 15-second wall clock and abandons rather than kills a worker
# that overruns it, so the check has to come back first with a real verdict.
# This client's own 60-second timeout alone exceeds that whole budget.
# Deliberately not a model field: an operator who raised it to 300 for a slow
# partner would silently re-break the probe.
SOURCE_CHECK_TIMEOUT_SECONDS = 5


class CimaWebDropsConnection(NetworkConnection):
    """
    Model representing a connection to the CIMA Web Drops API.
    """
    station_link_model_string_label = "adl_cimawebdrops_plugin.CimaWebDropsStationLink"

    token_endpoint = models.URLField(max_length=255, verbose_name=_("Token Endpoint URL"))
    client_id = models.CharField(max_length=255, verbose_name=_("Client ID"))
    username = models.CharField(max_length=255, verbose_name=_("Username"))
    password = models.CharField(max_length=255, verbose_name=_("Password"))
    api_base_url = models.URLField(max_length=255, verbose_name=_("API Base URL"))

    panels = NetworkConnection.panels + [
        MultiFieldPanel([
            FieldPanel("token_endpoint"),
            FieldPanel("client_id"),
            FieldPanel("username"),
            FieldPanel("password"),
            FieldPanel("api_base_url"),
        ], heading=_("CIMA Web Drops API Credentials")),
    ]

    class Meta:
        verbose_name = _("CIMA Web Drops API Connection")
        verbose_name_plural = _("CIMA Web Drops API Connections")

    @property
    def source_host(self):
        """The data host this connection dials, for operator-facing messages."""
        return urlparse(self.api_base_url).hostname

    def get_api_client(self, use_cache=True, timeout=DEFAULT_TIMEOUT, retries=None):
        """
        Returns the CIMA Webdrops API client instance.

        The defaults are the ingestion path's behaviour, unchanged. The
        diagnostic's on-demand checks pass a bounded, cache-bypassed client
        instead.
        """
        return CimaWebDropsClient(
            token_endpoint=self.token_endpoint,
            client_id=self.client_id,
            username=self.username,
            password=self.password,
            api_base_url=self.api_base_url,
            timeout=timeout,
            retries=retries,
            use_cache=use_cache,
        )

    def get_source_endpoint(self):
        """
        The (host, port) core's generic DNS -> TCP probe dials (layer 4 of the
        ingestion diagnostic).

        Two hosts are configured here and this names the data one. Layer 4 is
        the path to the source, and the source is where observations come from,
        so an outage of the token endpoint surfaces at layer 5 instead — with
        the host it failed to reach named in the message, so the operator is
        never misled about which box is down.
        """
        parsed = urlparse(self.api_base_url)
        return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

    def check_source(self):
        """
        Ask whether the source accepts our credentials and offers data (layer 5
        of the ingestion diagnostic). Read-only, on demand only.

        The sensor-class taxonomy is the cheapest read that proves both halves
        at once: it forces the token exchange as a side effect, so a valid token
        against a dead data API cannot read OK here. A discrete call to the
        token endpoint would be cheaper and would prove only half — leaving a
        real outage to surface as stale data two layers away from its cause.

        The cache is bypassed: this taxonomy is otherwise held for 24 hours, and
        a cached copy would report OK while the source is down, which is the
        precise failure this check exists to catch.
        """
        # Imported lazily: this module does not exist on a core release
        # predating the source-check contracts, where this method is never
        # called and a module-level import would kill the whole plugin.
        from adl.core.source_checks import SourceCheckResult, SourceCheckStatus

        host = self.source_host

        try:
            # Client construction belongs inside the guarded region: the token
            # is fetched on demand, so a credential fault has to read as a check
            # failure rather than an unhandled error.
            client = self.get_api_client(use_cache=False, timeout=SOURCE_CHECK_TIMEOUT_SECONDS,
                                         retries=0)
            sensor_classes = client.get_sensor_classes()
        except requests.HTTPError as e:
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                category=category_for_status(e.response.status_code),
                message=gettext("%(host)s returned HTTP %(code)s for %(path)s.") % {
                    "host": host,
                    "code": e.response.status_code,
                    "path": SENSOR_CLASSES_PATH,
                },
            )
        except ValueError:
            # Ordered ahead of RequestException on purpose: requests' own
            # JSONDecodeError is both, and it belongs here. The source sent no
            # code to classify from, so the category is declined.
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                message=gettext("%(host)s answered, but the response was not a sensor "
                                "class list.") % {
                    "host": host,
                },
            )
        except requests.RequestException as e:
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                message=gettext("%(host)s could not be reached: %(error)s") % {
                    "host": host,
                    "error": e,
                },
            )

        return SourceCheckResult(
            status=SourceCheckStatus.OK,
            message=gettext("%(host)s accepted our credentials and returned "
                            "%(count)s sensor class(es).") % {
                "host": host,
                "count": len(sensor_classes),
            },
        )


class CimaWebDropsStationLink(StationLink):
    """
    Model representing a link to a CIMA Web Drops station.
    """
    cima_station_id = models.CharField(max_length=255, verbose_name=_("CIMA Station"))
    start_date = models.DateTimeField(blank=True, null=True, validators=[validate_start_date],
                                      verbose_name=_("Initial Collection start date"),
                                      help_text=_(
                                          "The date to start collection data for the first collection. "
                                          "Ignored if any data has been collected already for this station"), )

    panels = StationLink.panels + [
        FieldPanel("cima_station_id", widget=CimaWebDropsStationSelectWidget),
        FieldPanel("start_date"),
        InlinePanel("variable_mappings", label=_("Station Variable Mapping"), heading=_("Station Variable Mappings")),
    ]

    class Meta:
        verbose_name = _("CIMA Web Drops Station Link")
        verbose_name_plural = _("CIMA Web Drops Station Links")

    def __str__(self):
        return f"{self.station} - {self.cima_station_id}"

    def get_variable_mappings(self):
        """
        Returns the variable mappings for this station link.
        """
        return self.variable_mappings.all()

    def get_first_collection_date(self):
        """
        Returns the first collection date for this station link.
        Returns None if no start date is set.
        """
        return self.start_date


class CimaWebDropsStationLinkVariableMapping(Orderable):
    station_link = ParentalKey(CimaWebDropsStationLink, on_delete=models.CASCADE, related_name="variable_mappings")
    adl_parameter = models.ForeignKey(DataParameter, on_delete=models.CASCADE, verbose_name=_("ADL Parameter"))
    cima_sensor_info = models.CharField(max_length=255, verbose_name=_("Cima Sensor"))
    cima_parameter_unit = models.ForeignKey(Unit, on_delete=models.CASCADE, verbose_name=_("Cima Parameter Unit"))

    panels = [
        FieldPanel("adl_parameter"),
        FieldPanel("cima_sensor_info", widget=CimaWebDropsVariableSelectWidget),
        FieldPanel("cima_parameter_unit"),
    ]

    @property
    def source_parameter_name(self):
        """
        Returns the sensor_class of the CIMA variable.
        """
        return self.cima_sensor_info

    @property
    def source_parameter_unit(self):
        """
        Returns the unit of the CIMA variable.
        """
        return self.cima_parameter_unit
