"""Ejecutor explícito de pruebas; nunca cambia los hashers del servidor normal.

Uso: manage.py test --testrunner=config.test_runner.ResourceTestRunner
La suite funcional no necesita repetir el coste criptográfico de los algoritmos de Django.
Los hashers normales se mantienen disponibles para fixtures y se restauran incluso ante errores.
"""
from django.conf import settings
from django.test import override_settings
from django.test.runner import DiscoverRunner


class ResourceTestRunner(DiscoverRunner):
    def run_tests(self, test_labels, **kwargs):
        fast = 'django.contrib.auth.hashers.MD5PasswordHasher'
        hashers = [fast, *(hasher for hasher in settings.PASSWORD_HASHERS if hasher != fast)]
        with override_settings(PASSWORD_HASHERS=hashers):
            return super().run_tests(test_labels, **kwargs)
