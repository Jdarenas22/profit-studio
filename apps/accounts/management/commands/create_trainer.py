import getpass
from django.core.management.base import BaseCommand, CommandError
from apps.accounts.models import User


class Command(BaseCommand):
    help = 'Crea la cuenta de entrenadora (rol trainer). Úsalo una sola vez en producción.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='adri')
        parser.add_argument('--email', default='')
        parser.add_argument('--first-name', dest='first_name', default='Adriana')
        parser.add_argument('--last-name', dest='last_name', default='')
        parser.add_argument('--password', default='')

    def handle(self, *args, **options):
        username = options['username']
        email = options['email']
        first_name = options['first_name']
        last_name = options['last_name']
        password = options['password']

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING(
                f'Ya existe un usuario con username "{username}". '
                'Usa --username para especificar otro.'
            ))
            return

        if not password:
            password = getpass.getpass('Contraseña para la entrenadora: ')
            confirm = getpass.getpass('Confirmar contraseña: ')
            if password != confirm:
                raise CommandError('Las contraseñas no coinciden.')
        if len(password) < 8:
            raise CommandError('La contraseña debe tener al menos 8 caracteres.')

        trainer = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=User.ROLE_TRAINER,
            is_staff=True,
        )

        self.stdout.write(self.style.SUCCESS(
            f'Entrenadora creada: {trainer.get_full_name() or trainer.username} '
            f'(username: {trainer.username}, staff: {trainer.is_staff})'
        ))
        self.stdout.write('Ahora puedes iniciar sesión en /accounts/login/')
