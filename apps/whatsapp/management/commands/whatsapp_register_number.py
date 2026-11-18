"""Registra el número de envío en la Cloud API (paso único, contrato sección 13.1, punto 8).

    python manage.py whatsapp_register_number

Pide el PIN de verificación en dos pasos por consola (no se muestra, no se guarda, no va en logs).
Solo en modo `live` con las variables configuradas.
"""
import getpass
import re

from django.core.management.base import BaseCommand, CommandError

from apps.whatsapp import conf
from apps.whatsapp.client import WhatsAppAPIError, get_client


class Command(BaseCommand):
    help = 'Registra el número de WhatsApp en la Cloud API (pide el PIN de dos pasos por consola).'

    def handle(self, *args, **options):
        if not conf.is_live() or conf.missing_live_settings():
            raise CommandError('Solo en WHATSAPP_CLOUD_MODE=live con todas las variables configuradas.')
        pin = getpass.getpass('PIN de verificación en dos pasos (6 dígitos): ').strip()
        if not re.fullmatch(r'\d{6}', pin):
            raise CommandError('El PIN debe tener 6 dígitos.')
        try:
            get_client().register_number(pin)
        except WhatsAppAPIError as exc:
            raise CommandError(f'Meta rechazó el registro (código {exc.code}).') from None
        self.stdout.write('Número registrado en la Cloud API.')
