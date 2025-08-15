from adl.core.models import NetworkConnection, StationLink, DataParameter, Unit
from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import MultiFieldPanel, FieldPanel, InlinePanel
from wagtail.models import Orderable

from .client import CimaWebDropsClient
from .validators import validate_start_date
from .widgets import CimaWebDropsStationSelectWidget, CimaWebDropsVariableSelectWidget


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
    
    def get_api_client(self):
        """
        Returns the CIMA Webdrops API client instance.
        """
        return CimaWebDropsClient(
            token_endpoint=self.token_endpoint,
            client_id=self.client_id,
            username=self.username,
            password=self.password,
            api_base_url=self.api_base_url
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
