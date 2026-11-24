"""Bucle del servicio trabajador de planes con IA: `python manage.py process_plan_jobs --loop`.

Está pensado para `AI_PLANS_WORKER=True` (la web deja el trabajo `queued` y no lanza hilos), pero también
funciona con `AI_PLANS_WORKER=False`: entonces la web sigue lanzando hilos y el trabajador solo recoge lo
que quede en cola. En ambos casos se usa el mismo reclamo atómico (`jobs.claim`), así que un trabajo nunca
corre dos veces aunque compitan dos bucles (por ejemplo, el trabajador viejo y el nuevo durante un redeploy).

Qué hace cada vuelta:
1. `close_old_connections()`: descarta la conexión a la base si venció o murió (Postgres puede haberse
   reiniciado o dormido).
2. Cada `RECOVER_EVERY` segundos, `jobs.recover_stale_jobs(start=False)`: devuelve a la cola los `running`
   sin latido de 3 minutos (trabajador caído o redeploy) o los falla si agotaron sus intentos.
3. `jobs.run_next_queued()`: reclama el `queued` más antiguo y lo corre hasta el final.
4. Si no había trabajo, duerme `interval` segundos (en tramos de 1 s para notar la señal de parada).

Parada limpia: la primera señal SIGTERM/SIGINT (Railway envía SIGTERM al redeployar) NO interrumpe el
trabajo en curso: lo termina y sale antes de reclamar otro. Una segunda señal lo corta (KeyboardInterrupt);
el trabajo queda `running` y se recupera solo cuando vence su latido. Si Railway manda SIGKILL antes de que
el trabajo termine, pasa lo mismo.

Un error inesperado (p. ej. Postgres caído) no tumba el bucle: se registra solo la clase del error, se
cierra la conexión y se reintenta con espera creciente (hasta `MAX_BACKOFF` s).

Logs: solo ids, estados, conteos y códigos. Nunca prompts, respuestas, datos de la ficha ni mensajes de
error (pueden traer datos de la conexión).
"""
import logging
import signal
import time
from contextlib import contextmanager

from django.db import close_old_connections, connection

from . import jobs

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 5       # segundos de espera cuando no hay trabajo
RECOVER_EVERY = 60         # segundos entre dos recuperaciones de huérfanos
MAX_BACKOFF = 60           # tope de la espera tras un error inesperado
WAIT_SLICE = 1.0           # la espera se parte en tramos para notar la señal de parada

_sleep = time.sleep        # las pruebas lo reemplazan


class StopRequest:
    """Bandera de parada del bucle. El manejador de señales solo escribe estos dos atributos (sin
    candados ni logs: un manejador de señal no debe tomar locks que el hilo principal pueda tener)."""

    def __init__(self):
        self.requested = False
        self.reason = ''

    def request(self, reason):
        self.reason = self.reason or reason
        self.requested = True

    def wait(self, seconds, sleep=None):
        """Duerme `seconds` en tramos de WAIT_SLICE y vuelve en cuanto se pide parar."""
        sleep = sleep or _sleep
        remaining = float(seconds)
        while remaining > 0 and not self.requested:
            chunk = min(remaining, WAIT_SLICE)
            sleep(chunk)
            remaining -= chunk


@contextmanager
def stop_on_signals(stop):
    """Instala SIGINT y SIGTERM para pedir una parada limpia (solo en el hilo principal) y los restaura
    al salir. La segunda señal, con la parada ya pedida, lanza KeyboardInterrupt."""
    def handler(signum, _frame):
        if stop.requested:
            raise KeyboardInterrupt
        stop.request(signal.Signals(signum).name)

    previous = {}
    for name in ('SIGINT', 'SIGTERM'):
        signum = getattr(signal, name, None)
        if signum is not None:
            previous[signum] = signal.signal(signum, handler)
    try:
        yield stop
    finally:
        for signum, old in previous.items():
            signal.signal(signum, old if old is not None else signal.SIG_DFL)


def _drop_connection():
    try:
        connection.close()
    except Exception:   # noqa: BLE001 — cerrar es lo mejor que se puede hacer tras un error
        pass


def run_loop(*, interval=DEFAULT_INTERVAL, max_jobs=None, stop=None, sleep=None, clock=None):
    """Procesa trabajos hasta que se pida parar (`stop`) o se alcance `max_jobs`. Devuelve cuántos corrió.

    `sleep` y `clock` se inyectan para las pruebas."""
    stop = stop or StopRequest()
    clock = clock or time.monotonic
    processed = failures = 0
    last_recovery = None
    # El motor de la base ayuda a notar una configuración equivocada (sqlite = falta DJANGO_SETTINGS_MODULE=production)
    logger.info('plan worker started interval=%s max_jobs=%s db=%s', interval, max_jobs or '-', connection.vendor)
    reason = ''
    while not stop.requested:
        try:
            close_old_connections()
            if last_recovery is None or clock() - last_recovery >= RECOVER_EVERY:
                jobs.recover_stale_jobs(start=False)   # solo su efecto: lo que quede `queued` lo toma el bucle
                last_recovery = clock()
            job_id = jobs.run_next_queued()
            failures = 0
        except Exception as exc:   # noqa: BLE001 — el bucle no muere por un error de la base o un fallo suelto
            failures += 1
            logger.error('plan worker error=%s consecutive=%s', type(exc).__name__, failures)
            _drop_connection()
            stop.wait(min(MAX_BACKOFF, interval * 2 ** min(failures, 6)), sleep)
            continue
        if job_id is None:
            stop.wait(interval, sleep)
            continue
        processed += 1
        logger.info('plan worker ran job_id=%s total=%s', job_id, processed)
        if max_jobs and processed >= max_jobs:
            reason = 'max_jobs'
            break
    reason = reason or stop.reason or 'stop'
    logger.info('plan worker stopped processed=%s reason=%s', processed, reason)
    _drop_connection()
    return processed
