from django.urls import path
from wagtail import hooks

from .views import (
    get_cima_webdrops_stations_for_connection,
    get_cima_webdrops_variables_for_connection,
)


@hooks.register('register_admin_urls')
def urlconf_cimawebdrops_plugin():
    return [
        path("adl-cimawebdrops-plugin/cimawebdrops-conn-stations/",
             get_cima_webdrops_stations_for_connection,
             name="cimawebdrops_stations_for_connection"),
        path("adl-cimawebdrops-plugin/cimawebdrops-conn-variables/",
             get_cima_webdrops_variables_for_connection,
             name="cimawebdrops_variables_for_connection"),
    ]
