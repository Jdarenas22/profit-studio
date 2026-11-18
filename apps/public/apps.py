from django.apps import AppConfig


class PublicConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.public'
    verbose_name = 'Páginas Públicas'

    def ready(self):
        # Comprobaciones de despliegue propias (profit.W001, profit.W002): check --deploy
        from config import checks  # noqa: F401
