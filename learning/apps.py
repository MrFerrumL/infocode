from django.apps import AppConfig


class LearningConfig(AppConfig):
    name = 'learning'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import signals
