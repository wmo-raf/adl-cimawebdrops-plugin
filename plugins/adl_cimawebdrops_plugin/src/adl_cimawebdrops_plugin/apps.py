from django.apps import AppConfig

from adl.core.registries import plugin_registry


class CimaWebdropsPluginConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = "adl_cimawebdrops_plugin"
    
    def ready(self):
        from .plugins import CimaWebdropsPlugin
        
        plugin_registry.register(CimaWebdropsPlugin())
