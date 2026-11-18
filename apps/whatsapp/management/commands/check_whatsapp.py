"""Revisa la configuración de los avisos por WhatsApp. SOLO LECTURA (salvo `--resume`).

    python manage.py check_whatsapp            # modo, variables, pendientes legales, cola, plantillas
    python manage.py check_whatsapp --resume   # reanuda el canal pausado si la revisión pasa

En `live` consulta a Meta el estado del número y de las plantillas. Nunca imprime el token ni
secretos: solo nombres de variables, estados y códigos. Sale con código 1 si hay un ERROR.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from apps.whatsapp import alerts, catalog, conf
from apps.whatsapp.client import WhatsAppAPIError, get_client
from apps.whatsapp.models import KIND_PAYMENT_APPROVED, WhatsAppChannelState, WhatsAppContact, WhatsAppMessage


class Command(BaseCommand):
    help = 'Revisa los avisos por WhatsApp (modo, variables, pendientes legales, cola y plantillas en Meta).'

    def add_arguments(self, parser):
        parser.add_argument('--resume', action='store_true', help='Reanuda el canal si la revisión pasa.')

    def line(self, status, text):
        self.stdout.write(f'[{status}] {text}')

    def handle(self, *args, **options):
        errors = 0
        mode = conf.mode()
        self.line('OK', f'Modo: {mode}.')
        if mode == conf.MODE_OFF:
            self.line('OK', 'Apagado: no se ofrece ni se envía nada.')
            return
        if mode == conf.MODE_LIVE:
            for name in conf.missing_live_settings():
                errors += 1
                self.line('ERROR', f'Falta o no es válida: {name}.')
        for item in conf.legal_missing():
            if mode == conf.MODE_LIVE:
                errors += 1
                self.line('ERROR', f'Pendiente legal (bloquea los envíos): {item}.')
            else:
                self.line('AVISO', f'Pendiente legal: {item}.')
        state = WhatsAppChannelState.get()
        if state.paused:
            self.line('AVISO', f'Canal pausado (código {state.paused_reason}).')
        for name in state.paused_templates:
            self.line('AVISO', f'Plantilla pausada: {name}.')
        since = timezone.now() - timedelta(hours=24)
        by_status = dict(WhatsAppMessage.objects.filter(created_at__gte=since)
                         .values_list('status').annotate(n=Count('pk')))
        self.line('OK', 'Avisos de las últimas 24 h: '
                  + (', '.join(f'{k}={v}' for k, v in sorted(by_status.items())) or 'ninguno') + '.')
        contacts = dict(WhatsAppContact.objects.values_list('status').annotate(n=Count('pk')))
        self.line('OK', 'Contactos: ' + (', '.join(f'{k}={v}' for k, v in sorted(contacts.items())) or 'ninguno') + '.')
        if mode == conf.MODE_LIVE and not conf.missing_live_settings():
            errors += self.check_meta()
        if options['resume']:
            if errors:
                raise CommandError('No se reanuda: corrige primero los errores de arriba.')
            alerts.resume()
            self.line('OK', 'Canal reanudado.')
        if errors:
            raise CommandError(f'{errors} error(es).')

    def check_meta(self):
        client = get_client()
        try:
            info = client.phone_number_info()
        except WhatsAppAPIError as exc:
            self.line('ERROR', f'No se pudo consultar el número (código {exc.code}).')
            return 1
        self.line('OK', f'Número en Meta: calidad {info.get("quality_rating", "?")}, '
                        f'estado del nombre {info.get("name_status", "?")}.')
        try:
            templates = client.templates(settings.WHATSAPP_CLOUD_WABA_ID)
        except WhatsAppAPIError as exc:
            self.line('ERROR', f'No se pudieron consultar las plantillas (código {exc.code}).')
            return 1
        errors = 0
        language = settings.WHATSAPP_CLOUD_TEMPLATE_LANG
        found = {(t.get('name'), t.get('language')): t for t in templates if isinstance(t, dict)}
        for kind, spec in catalog.TEMPLATES.items():
            if kind == KIND_PAYMENT_APPROVED and not getattr(settings, 'WHATSAPP_CLOUD_PAYMENT_NOTICE', False):
                continue
            template = found.get((spec.name, language))
            if template is None:
                errors += 1
                self.line('ERROR', f'Falta la plantilla {spec.name} en {language}.')
            elif template.get('status') != 'APPROVED' or template.get('category') != 'UTILITY':
                errors += 1
                self.line('ERROR', f'Plantilla {spec.name}: estado {template.get("status")}, categoría '
                                   f'{template.get("category")} (debe ser APPROVED y UTILITY).')
            else:
                self.line('OK', f'Plantilla {spec.name} aprobada (UTILITY, {language}).')
        return errors
