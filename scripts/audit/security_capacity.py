"""Auditoría local reproducible. Nunca lee .env ni la base o medios del proyecto.

Requiere Django en el intérprete y Waitress solo para --load. La base ficticia se
crea en un subdirectorio nuevo de --output-dir; el servidor escucha solo en loopback.
"""
import argparse
import concurrent.futures
import contextlib
import html
import io
import json
import logging
import math
import os
import platform
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--tool-site', type=Path)
    parser.add_argument('--load', action='store_true')
    parser.add_argument('--members', type=int, default=1000)
    parser.add_argument('--duration-seconds', type=int, default=0,
                        help='0: prueba corta por número de peticiones; 5 a 30: ventana temporal por nivel.')
    parser.add_argument('--workloads', default='health,mixed,trainer_list')
    parser.add_argument('--levels', default='1,5,10,25,50')
    args = parser.parse_args()
    workloads = args.workloads.split(',')
    try:
        levels = [int(level) for level in args.levels.split(',')]
    except ValueError:
        parser.error('Niveles de concurrencia inválidos.')
    if (any(workload not in ('health', 'mixed', 'trainer_list') for workload in workloads)
            or not levels or any(level < 1 or level > 100 for level in levels)
            or args.duration_seconds not in (0, *range(5, 31))):
        parser.error('Perfiles, concurrencia (1 a 100) o duración (5 a 30 s) inválidos.')
    root = args.project.resolve()
    if not (root / 'manage.py').is_file() or not 10 <= args.members <= 2000:
        parser.error('Proyecto o número de usuarios inválido (10 a 2000).')
    output = args.output_dir.resolve()
    if output == root or output.is_relative_to(root):
        parser.error('Los resultados y la base ficticia deben estar fuera del repositorio.')
    run = output / ('run-' + uuid.uuid4().hex[:12])
    run.mkdir(parents=True)
    os.chdir(root)
    sys.path.insert(0, str(root))
    if args.tool_site:
        sys.path.append(str(args.tool_site.resolve()))
    for name in tuple(os.environ):
        if name.startswith(('GEMINI_', 'FDC_', 'WOMPI_', 'WHATSAPP_CLOUD_', 'R2_', 'AWS_', 'EMAIL_',
                            'REDIS_', 'DATABASE_', 'TRAINER_')):
            os.environ.pop(name, None)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.development'
    import environ
    environ.Env.read_env = classmethod(lambda cls, *a, **kw: None)
    from django.conf import settings
    settings.DEBUG = False
    settings.SECRET_KEY = 'audit-only-fictional-secret-' + uuid.uuid4().hex
    settings.ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
    settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
                                     'NAME': str(run / 'synthetic.sqlite3')}}
    settings.MEDIA_ROOT = str(run / 'media')
    settings.STATIC_ROOT = str(run / 'staticfiles')
    settings.INSTALLED_APPS = [a for a in settings.INSTALLED_APPS if a != 'debug_toolbar']
    settings.MIDDLEWARE = [a for a in settings.MIDDLEWARE if not a.startswith('debug_toolbar.')]
    settings.STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                         'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    settings.AI_PLANS_MODE = 'off'
    settings.WHATSAPP_CLOUD_MODE = 'off'
    settings.SESSION_ENGINE = 'django.contrib.sessions.backends.db'
    # Perfil local: se reproducen estos valores visibles en production.py; no se importa producción.
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.SECURE_HSTS_SECONDS = 31536000
    settings.X_FRAME_OPTIONS = 'DENY'
    settings.AXES_ENABLED = False
    logging.disable(logging.CRITICAL)
    import django
    django.setup()
    from django.core.management import call_command
    from django.core import mail
    from django.test import Client, RequestFactory, override_settings
    from django.middleware.csrf import get_token
    from django.utils import timezone
    from django.db import connection, connections
    from django.test.utils import CaptureQueriesContext
    from apps.accounts.models import User
    from apps.memberships.models import Membership, MembershipPlan
    from apps.assessments.models import InitialAssessment

    with contextlib.redirect_stdout(io.StringIO()):
        call_command('migrate', verbosity=0, interactive=False)
    password = 'Solo-Prueba-Ficticia-8391!'
    trainer = User.objects.create_user(username='audit_trainer', password=password, role='trainer')
    other_trainer = User.objects.create_user(username='audit_other', password=password, role='trainer')
    boss = User.objects.create_user(username='audit_boss', password=password, role='trainer', is_superuser=True)
    User.objects.bulk_create([User(username=f'audit_member_{i}', password='!', role='member',
                                   first_name=f'Ficticio {i}', email=f'audit{i}@example.invalid',
                                   assigned_trainer=trainer if i % 2 == 0 else other_trainer)
                              for i in range(args.members)])
    members = list(User.objects.filter(role='member').order_by('pk'))
    members[0].set_password(password)
    members[0].save(update_fields=['password'])
    plan = MembershipPlan.objects.create(name='Plan ficticio', duration_days=30, reference_price=100000)
    today = timezone.localdate()
    Membership.objects.bulk_create([Membership(user=u, plan=plan, start_date=today,
                                               end_date=today + timezone.timedelta(days=30)) for u in members])
    assessment = InitialAssessment.objects.create(user=members[1], trainer=other_trainer,
                                                  age=30, sex='F', weight=60, height='1.65', goal='bienestar')
    checks = []

    def observe(name, response, expected):
        checks.append({'name': name, 'status': response.status_code, 'expected': expected,
                       'pass': response.status_code in expected})

    def client(user=None):
        c = Client(enforce_csrf_checks=True, raise_request_exception=False)
        if user:
            c.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        return c

    def csrf(c):
        request = RequestFactory().get('/')
        token = get_token(request)
        c.cookies[settings.CSRF_COOKIE_NAME] = request.META['CSRF_COOKIE']
        return {'HTTP_X_CSRFTOKEN': token, 'HTTP_ORIGIN': 'https://testserver'}

    anonymous, regular, coach = client(), client(members[0]), client(trainer)
    observe('anonymous_trainer', anonymous.get('/accounts/trainer/clients/', secure=True), [302])
    observe('member_trainer', regular.get('/accounts/trainer/clients/', secure=True), [403])
    observe('trainer_foreign_client', coach.get(f'/accounts/trainer/clients/{members[1].pk}/', secure=True), [404])
    observe('trainer_own_client', coach.get(f'/accounts/trainer/clients/{members[0].pk}/', secure=True), [200])
    observe('logout_get', regular.get('/accounts/logout/', secure=True), [405])
    observe('logout_missing_csrf', regular.post('/accounts/logout/', secure=True), [403])
    observe('api_anonymous', anonymous.get(f'/api/v1/assessments/{assessment.pk}/ruffier-dickson/', secure=True), [401])
    observe('api_foreign', coach.get(f'/api/v1/assessments/{assessment.pk}/ruffier-dickson/', secure=True), [404])
    response = anonymous.post('/accounts/login/', {'username': members[0].username, 'password': password,
                                                  'next': 'https://attacker.invalid/'}, secure=True, **csrf(anonymous))
    checks.append({'name': 'login_open_redirect', 'pass': response.status_code == 302 and
                   response.get('Location', '').startswith('/'), 'status': response.status_code})
    login_cookie_flags = dict(response.cookies[settings.SESSION_COOKIE_NAME].items()) if settings.SESSION_COOKIE_NAME in response.cookies else {}
    marker = '<img src=x onerror=alert(9321)>'
    members[0].first_name = marker
    members[0].save(update_fields=['first_name'])
    body = coach.get(f'/accounts/trainer/clients/{members[0].pk}/', secure=True).content.decode()
    checks.append({'name': 'stored_html_escaped', 'pass': marker not in body and html.escape(marker) in body})
    members[0].first_name = 'Ficticio 0'
    members[0].save(update_fields=['first_name'])
    observe('invalid_host', client().get('/', HTTP_HOST='attacker.invalid', secure=True), [400])
    with override_settings(WOMPI_EVENTS_SECRET='synthetic-audit-webhook-secret'):
        observe('wompi_unsigned', client().post('/payments/webhook/', '{}', content_type='application/json'), [401])
        # La entrada pequeña no contiene firma: debería rechazarse de forma controlada, nunca causar 500.
        observe('wompi_deep_unsigned_json', client().post('/payments/webhook/', '[' * 2000 + '0' + ']' * 2000,
                                                       content_type='application/json'), [400, 401, 413])
    with override_settings(WHATSAPP_CLOUD_MODE='simulated', WHATSAPP_CLOUD_APP_SECRET='synthetic-audit-meta-secret'):
        observe('whatsapp_unsigned', client().post('/whatsapp/webhook/', '{}', content_type='application/json'), [401])
    headers = dict(regular.get('/accounts/dashboard/', secure=True).headers)
    cookies = login_cookie_flags
    # Tres solicitudes controladas, solo correo en memoria; ningún destinatario real.
    reset = client()
    reset_headers = csrf(reset)
    before = len(getattr(mail, 'outbox', []))
    reset_statuses = []
    for _ in range(3):
        reset_statuses.append(reset.post('/accounts/password-reset/', {'email': members[0].email},
                                        secure=True, **reset_headers).status_code)
    resets_sent = len(getattr(mail, 'outbox', [])) - before
    query_samples = []
    for label, c, url in [('home', client(), '/'), ('member_dashboard', regular, '/accounts/dashboard/'),
                           ('trainer_list', coach, '/accounts/trainer/clients/'),
                           ('boss_list', client(boss), '/accounts/trainer/clients/')]:
        with CaptureQueriesContext(connection) as queries:
            response = c.get(url, secure=True)
        query_samples.append({'name': label, 'status': response.status_code, 'queries': len(queries),
                              'bytes': len(response.content)})
    # Experimento aislado: demuestra la causa N+1 sin editar la vista del proyecto.
    from unittest.mock import patch
    from apps.accounts import views as account_views
    original_scope = account_views.clients_for_trainer
    optimized_samples = []
    with patch.object(account_views, 'clients_for_trainer',
                      side_effect=lambda user: original_scope(user).select_related('membership')):
        for label, c in [('trainer_list', coach), ('boss_list', client(boss))]:
            with CaptureQueriesContext(connection) as queries:
                response = c.get('/accounts/trainer/clients/', secure=True)
            optimized_samples.append({'name': label, 'status': response.status_code, 'queries': len(queries),
                                      'bytes': len(response.content)})
    report = {'scope': 'isolated-development-profile-no-env', 'members': args.members,
              'python': platform.python_version(), 'os': platform.platform(), 'logical_cpus': os.cpu_count(),
              'security_checks': checks, 'response_headers': headers, 'session_cookie_flags': cookies,
              'reset_emails_for_three_posts': resets_sent, 'reset_statuses': reset_statuses, 'query_samples': query_samples,
              'membership_prefetch_experiment': optimized_samples,
              'load': [], 'load_profile': 'Waitress, 2 threads, SQLite, localhost; NO es Railway/Gunicorn'}

    if args.load:
        from waitress import create_server
        from django.core.wsgi import get_wsgi_application
        server = create_server(get_wsgi_application(), host='127.0.0.1', port=0, threads=2,
                               connection_limit=250, channel_timeout=15)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        port = server.effective_port
        roles = []
        for user in members[:48]:
            c = client(user)
            roles.append(c.cookies['sessionid'].value)
        coach_cookie = coach.cookies['sessionid'].value
        local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(url, cookie=None):
            request_headers = {'Cookie': 'sessionid=' + cookie} if cookie else {}
            req = urllib.request.Request(f'http://127.0.0.1:{port}{url}', headers=request_headers)
            begin = time.perf_counter()
            try:
                with local_opener.open(req, timeout=15) as response:
                    response.read()
                    status = response.status
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
            except (OSError, TimeoutError, urllib.error.URLError):
                status = 0
            return (time.perf_counter() - begin) * 1000, status

        for workload in workloads:
            for concurrency in levels:
                # Carga cerrada sin pausas: no equivale a usuarios humanos navegando.
                count = max(60, concurrency * 4)
                samples = []
                barrier = threading.Barrier(concurrency)

                def virtual_user(index):
                    barrier.wait(timeout=20)
                    results = []
                    seq = index
                    while ((time.perf_counter() < deadline and seq < 20000)
                           if args.duration_seconds else seq < count):
                        if workload == 'health':
                            url, cookie = '/health/', None
                        elif workload == 'trainer_list':
                            url, cookie = '/accounts/trainer/clients/', coach_cookie
                        elif seq % 10 < 4:
                            url, cookie = '/', None
                        elif seq % 10 < 9:
                            url, cookie = '/accounts/dashboard/', roles[index % len(roles)]
                        else:
                            url, cookie = '/accounts/trainer/clients/', coach_cookie
                        results.append(request(url, cookie))
                        seq += concurrency
                    return results

                request('/health/')
                deadline = time.perf_counter() + args.duration_seconds
                begin = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                    for group in executor.map(virtual_user, range(concurrency)):
                        samples.extend(group)
                elapsed = time.perf_counter() - begin
                times = sorted(item[0] for item in samples)
                row = {'workload': workload, 'concurrency': concurrency, 'requests': len(samples),
                       'duration_target_seconds': args.duration_seconds,
                       'seconds': round(elapsed, 3), 'rps': round(len(samples) / elapsed, 2),
                       'p50_ms': round(statistics.median(times), 2),
                       'p95_ms': round(times[math.ceil(len(times) * .95) - 1], 2),
                       'errors': sum(status != 200 for _, status in samples)}
                report['load'].append(row)
                print(json.dumps(row), flush=True)
                # Parada al superar el umbral de latencia, errores o duración. No busca agotar el equipo.
                if row['p95_ms'] > 3000 or row['errors'] > len(samples) * .01 or elapsed > 30:
                    break
        server.close()
        connections.close_all()
    (run / 'results.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Resultados:', run / 'results.json', flush=True)
    print('Controles:', sum(c['pass'] for c in checks), '/', len(checks), flush=True)
    print('Correos ficticios por 3 solicitudes de recuperación:', resets_sent, flush=True)


if __name__ == '__main__':
    main()
