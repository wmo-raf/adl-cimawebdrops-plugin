from django.forms import Widget
from django.urls import reverse


class CimaWebDropsStationSelectWidget(Widget):
    template_name = 'adl_cimawebdrops_plugin/widgets/cimawebdrops_station_select_widget.html'
    
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        
        context.update({
            'cimawebdrops_stations_url': reverse("cimawebdrops_stations_for_connection"),
        })
        
        return context


class CimaWebDropsVariableSelectWidget(Widget):
    template_name = 'adl_cimawebdrops_plugin/widgets/cimawebdrops_variable_select_widget.html'
    
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        
        context.update({
            'cimawebdrops_variables_url': reverse("cimawebdrops_variables_for_connection"),
        })
        
        return context
