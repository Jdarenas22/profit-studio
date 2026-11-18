"""Procesa la cola de avisos por WhatsApp. Lo corre el servicio CRON de Railway (contrato, sección 7.1).

    python manage.py process_whatsapp_queue
    Railway > servicio cron > Settings > Cron Schedule: */15 12-23 * * 1-6   (UTC = 7:00-18:45 en Bogotá)

Pasos: recupera envíos colgados, encola los avisos de membresía del día (idempotente), envía lo
vencido (máximo 50 por corrida, 1 s entre mensajes, dentro de la ventana horaria) y borra lo antiguo.
Termina en menos de 4 minutos. Sale con código 1 (CommandError) si el canal está pausado o, en
`live`, si falta una variable o un pendiente legal: así Railway muestra la corrida como fallida.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.whatsapp import conf, queue, services
from apps.whatsapp.models import WhatsAppChannelState


class Command(BaseCommand):
    help = 'Envía los avisos por WhatsApp pendientes (cron de Railway).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=queue.RUN_LIMIT, help='Máximo de envíos por corrida.')

    def handle(self, *args, **options):
        try:
            self._run(options)
        finally:
            connection.close()

    def _run(self, options):
        if not conf.enabled():
            self.stdout.write('Avisos por WhatsApp apagados (WHATSAPP_CLOUD_MODE=off): nada que hacer.')
            return
        if conf.is_live():
            missing = conf.missing_live_settings() + conf.legal_missing()
            if missing:
                raise CommandError('No se envía nada: falta ' + '; '.join(missing) + '.')
        recovered = queue.recover_stale()
        state = WhatsAppChannelState.get()
        if state.paused:
            raise CommandError(f'Canal pausado (código {state.paused_reason}). Revisa y ejecuta: '
                               'python manage.py check_whatsapp --resume')
        created = services.enqueue_membership_notices()
        summary = queue.process_due(limit=max(1, options['limit']))
        purged_messages, purged_inbound = queue.purge()
        detail = ', '.join(f'{key}={value}' for key, value in sorted(summary.items())) or 'ninguno'
        self.stdout.write(f'Recuperados: {recovered}. Avisos de membresía encolados: {created}. '
                          f'Resultados: {detail}. Borrados por antigüedad: {purged_messages} avisos, '
                          f'{purged_inbound} respuestas.')
        if WhatsAppChannelState.get().paused:
            raise CommandError('El canal quedó pausado durante la corrida (revisa el correo de alerta).')
