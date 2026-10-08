import getpass
import os
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from apps.accounts.models import User


class Command(BaseCommand):
    help = 'Crea la cuenta de entrenadora (rol trainer). Úsalo una sola vez en producción.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='adri')
        parser.add_argument('--email', default='')
        parser.add_argument('--first-name', dest='first_name', default='Adriana')
        parser.add_argument('--last-name', dest='last_name', default='')
        parser.add_argument(
            '--password', default='',
            help='NO recomendado: queda visible en la lista de procesos. Prefiere la variable '
                 'de entorno TRAINER_INITIAL_PASSWORD (o TRAINER_PASSWORD) o el aviso interactivo.',
        )
        parser.add_argument('--superuser', action='store_true', default=False,
                            help='Otorgar permisos de superusuario (solo para la entrenadora principal)')

    def handle(self, *args, **options):
        username = options['username']
        email = options['email']
        first_name = options['first_name']
        last_name = options['last_name']
        password = options['password']
        make_superuser = options['superuser']

        if User.objects.filter(username=username).exists():
            if make_superuser:
                # Si ya existe pero le faltan permisos de superusuario, los aplicamos
                updated = User.objects.filter(username=username).update(
                    is_staff=True, is_superuser=True, role=User.ROLE_TRAINER
                )
                self.stdout.write(self.style.SUCCESS(
                    f'Cuenta "{username}" ya existía — '
                    f'actualizada con permisos de superusuaria (is_superuser=True).'
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f'Ya existe un usuario con username "{username}". '
                    'Usa --username para especificar otro.'
                ))
            return

        if not password:
            # Preferir el entorno: no queda en los argumentos del proceso ni en los logs.
            # TRAINER_PASSWORD se acepta por compatibilidad con despliegues anteriores.
            password = (
                os.environ.get('TRAINER_INITIAL_PASSWORD')
                or os.environ.get('TRAINER_PASSWORD')
                or ''
            )
        if not password:
            password = getpass.getpass('Contraseña para la entrenadora: ')
            confirm = getpass.getpass('Confirmar contraseña: ')
            if password != confirm:
                raise CommandError('Las contraseñas no coinciden.')
        if len(password) < 8:
            raise CommandError('La contraseña debe tener al menos 8 caracteres.')
        try:
            validate_password(password, user=User(
                username=username, email=email,
                first_name=first_name, last_name=last_name,
            ))
        except ValidationError as exc:
            raise CommandError('Contraseña no válida: ' + ' '.join(exc.messages))

        trainer = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=User.ROLE_TRAINER,
            is_staff=True,
            is_superuser=make_superuser,
        )

        role_label = 'Entrenador/a principal (superusuario)' if make_superuser else 'Entrenador/a'
        self.stdout.write(self.style.SUCCESS(
            f'{role_label} creado/a: {trainer.get_full_name() or trainer.username} '
            f'(username: {trainer.username}, staff: {trainer.is_staff}, superuser: {trainer.is_superuser})'
        ))
        self.stdout.write('Ahora puedes iniciar sesión en /accounts/login/')
