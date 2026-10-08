"""Ejercicios: URL de video (solo YouTube), subidas de imagen/video y filtros."""
import io
import shutil
import tempfile
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils.html import escape
from PIL import Image

from apps.accounts.form_utils import SafeImageField, SafeVideoField
from apps.accounts.tests.helpers import ScenarioTestCase
from apps.exercises import views
from apps.exercises.models import Exercise, ExerciseCategory
from apps.exercises.validators import extract_youtube_id

VIDEO_ID = 'dQw4w9WgXcQ'

GOOD_URLS = [
    f'https://www.youtube.com/watch?v={VIDEO_ID}',
    f'https://youtube.com/watch?v={VIDEO_ID}&t=30s',
    f'https://m.youtube.com/watch?v={VIDEO_ID}',
    f'https://youtu.be/{VIDEO_ID}',
    f'https://youtu.be/{VIDEO_ID}?si=abc',
    f'https://www.youtube.com/embed/{VIDEO_ID}',
    f'https://www.youtube-nocookie.com/embed/{VIDEO_ID}',
    f'https://www.youtube.com/shorts/{VIDEO_ID}',
]
BAD_URLS = [
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'https://evil.com',
    f'https://evil.com/watch?v={VIDEO_ID}',
    f'https://evil.com/youtube.com/watch?v={VIDEO_ID}',
    f'https://youtube.com.evil.com/watch?v={VIDEO_ID}',
    f'https://evilyoutube.com/watch?v={VIDEO_ID}',
    f'https://youtube.com@evil.com/watch?v={VIDEO_ID}',
    f'https://www.youtube.com:8443/watch?v={VIDEO_ID}',
    f'http://www.youtube.com/watch?v={VIDEO_ID}',            # sin HTTPS
    f'//www.youtube.com/watch?v={VIDEO_ID}',
    f'ftp://youtu.be/{VIDEO_ID}',
    f'https://youtu.be/{VIDEO_ID}"onload="alert(1)',
    f'https://youtu.be/{VIDEO_ID}\nHost: evil.com',
    f'https://youtu.be/{VIDEO_ID} <script>',
    f'https://www.youtube.com/watch?v={VIDEO_ID}x',           # ID de 12 caracteres
    'https://www.youtube.com/watch?v=corto',
    'https://www.youtube.com/watch',
    'https://www.youtube.com/',
    'https://www.youtube.com/redirect?q=https://evil.com',
    f'https://www.youtube.com/watch?v={VIDEO_ID}' + 'a' * 200,  # demasiado larga
    'youtube.com/watch?v=' + VIDEO_ID,                          # sin esquema
    '', None, 123,
]


def png_bytes(size=(8, 8), fmt='PNG'):
    buf = io.BytesIO()
    Image.new('RGB', size, 'blue').save(buf, fmt)
    return buf.getvalue()


MP4_HEAD = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 32
WEBM_HEAD = b'\x1a\x45\xdf\xa3' + b'\x00' * 32


class YouTubeUrlTests(SimpleTestCase):
    def test_good_urls_yield_the_video_id(self):
        for url in GOOD_URLS:
            with self.subTest(url=url):
                self.assertEqual(extract_youtube_id(url), VIDEO_ID)

    def test_bad_urls_yield_nothing(self):
        for url in BAD_URLS:
            with self.subTest(url=url):
                self.assertEqual(extract_youtube_id(url), '')

    def test_embed_url_is_rebuilt_never_the_original_fallback(self):
        for url in BAD_URLS:
            if isinstance(url, str):
                with self.subTest(url=url):
                    self.assertEqual(Exercise(video_url=url).youtube_embed_url, '')
                    self.assertEqual(Exercise(video_url=url).safe_video_url, '')
        ok = Exercise(video_url=f'https://youtu.be/{VIDEO_ID}?si=zzz')
        self.assertEqual(ok.youtube_embed_url, f'https://www.youtube.com/embed/{VIDEO_ID}?rel=0&modestbranding=1')
        self.assertEqual(ok.safe_video_url, f'https://www.youtube.com/watch?v={VIDEO_ID}')

    def test_model_field_validator_covers_the_admin(self):
        from django.core.exceptions import ValidationError
        exercise = Exercise(name='x', level='beginner', description='d', muscles='m',
                            video_url='https://evil.com/x')
        with self.assertRaises(ValidationError) as ctx:
            exercise.full_clean()
        self.assertIn('video_url', ctx.exception.message_dict)


class UploadFieldUnitTests(SimpleTestCase):
    def clean_image(self, name, content, max_bytes=5 * 1024 * 1024):
        return SafeImageField(required=False, max_bytes=max_bytes).clean(
            SimpleUploadedFile(name, content, content_type='image/jpeg'))

    def test_image_checks(self):
        from django.core.exceptions import ValidationError
        self.assertTrue(self.clean_image('a.png', png_bytes()))
        self.assertTrue(self.clean_image('a.JPG', png_bytes(fmt='JPEG')))
        self.assertTrue(self.clean_image('a.gif', png_bytes(fmt='GIF')))
        self.assertTrue(self.clean_image('a.webp', png_bytes(fmt='WEBP')))
        for name, content in (('fake.jpg', b'texto plano'), ('fake.png', b'<?php echo 1; ?>'),
                              ('vector.svg', b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
                              ('shell.php', png_bytes()), ('sin_ext', png_bytes()),
                              ('doble.png.exe', png_bytes()), ('vacio.png', b''),
                              ('bmp.png', png_bytes(fmt='BMP'))):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    self.clean_image(name, content)

    def test_image_size_limit(self):
        from django.core.exceptions import ValidationError
        data = png_bytes((64, 64))
        self.assertTrue(self.clean_image('ok.png', data, max_bytes=len(data)))
        with self.assertRaises(ValidationError):
            self.clean_image('ok.png', data, max_bytes=len(data) - 1)

    def clean_video(self, name, content, content_type='video/mp4', max_bytes=100 * 1024 * 1024):
        return SafeVideoField(required=False, max_bytes=max_bytes).clean(
            SimpleUploadedFile(name, content, content_type=content_type))

    def test_video_checks(self):
        from django.core.exceptions import ValidationError
        self.assertTrue(self.clean_video('a.mp4', MP4_HEAD))
        self.assertTrue(self.clean_video('a.mov', MP4_HEAD, 'video/quicktime'))
        self.assertTrue(self.clean_video('a.webm', WEBM_HEAD, 'video/webm'))
        for name, content, ctype in (
            ('virus.exe', MP4_HEAD, 'video/mp4'),                    # extensión
            ('a.mp4', MP4_HEAD, 'text/html'),                        # content-type
            ('a.mp4', MP4_HEAD, 'application/octet-stream'),
            ('a.mp4', b'esto es texto, no un video' * 3, 'video/mp4'),   # firma
            ('a.webm', MP4_HEAD, 'video/webm'),                      # firma de otro formato
            ('a.mp4', WEBM_HEAD, 'video/mp4'),
            ('a.mp4.php', MP4_HEAD, 'video/mp4'),
            ('a.avi', MP4_HEAD, 'video/x-msvideo'),
        ):
            with self.subTest(name=name, ctype=ctype):
                with self.assertRaises(ValidationError):
                    self.clean_video(name, content, ctype)

    def test_video_size_limit(self):
        from django.core.exceptions import ValidationError
        self.assertTrue(self.clean_video('a.mp4', MP4_HEAD, max_bytes=len(MP4_HEAD)))
        with self.assertRaises(ValidationError):
            self.clean_video('a.mp4', MP4_HEAD, max_bytes=len(MP4_HEAD) - 1)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='profit-tests-'))
class ExerciseFormViewTests(ScenarioTestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.login(self.trainer_a)
        self.category = ExerciseCategory.objects.create(name='Piernas')
        self.create_url = reverse('trainer_exercise_create')
        self.count = Exercise.objects.count()

    def payload(self, **over):
        data = {'name': 'Press militar', 'category': self.category.pk, 'level': 'intermediate',
                'description': 'Empuja la barra', 'muscles': 'Deltoides', 'recommendations': 'Core firme',
                'video_url': f'https://youtu.be/{VIDEO_ID}', 'is_public': 'on'}
        data.update(over)
        return data

    def test_valid_exercise_is_created(self):
        response = self.client.post(self.create_url, self.payload())
        self.assertRedirects(response, reverse('trainer_exercise_list'))
        e = Exercise.objects.get(name='Press militar')
        self.assertEqual((e.level, e.category, e.is_public, e.is_active), ('intermediate', self.category, True, True))

    def test_malicious_video_urls_are_rejected_by_the_server(self):
        for url in ('javascript:alert(1)', 'https://evil.com', 'http://youtu.be/' + VIDEO_ID,
                    f'https://youtube.com@evil.com/watch?v={VIDEO_ID}', 'data:text/html;base64,PHNjcmlwdD4='):
            with self.subTest(url=url):
                response = self.client.post(self.create_url, self.payload(video_url=url))
                self.assertEqual(response.status_code, 200)
                self.assertIn('video_url', response.context['errors'])
        self.assertEqual(Exercise.objects.count(), self.count)

    def test_malicious_url_cannot_be_set_through_edit_either(self):
        e = Exercise.objects.create(name='Sentadilla 2', level='beginner', description='d', muscles='m',
                                    video_url=f'https://youtu.be/{VIDEO_ID}')
        response = self.client.post(reverse('trainer_exercise_edit', args=[e.pk]),
                                    self.payload(video_url='javascript:alert(1)'))
        self.assertIn('video_url', response.context['errors'])
        e.refresh_from_db()
        self.assertEqual(e.video_url, f'https://youtu.be/{VIDEO_ID}')

    def test_invalid_fields_do_not_500(self):
        cases = {
            'level': ['', 'experto', 'x' * 100],
            'category': ['abc', '99999', '-1'],
            'name': ['', '   ', 'x' * 201],
            'description': ['', 'x' * 5001],
            'muscles': ['', 'x' * 1001],
            'recommendations': ['x' * 3001],
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value[:10]):
                    response = self.client.post(self.create_url, self.payload(**{field: value}))
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(field, response.context['errors'])
        response = self.client.post(self.create_url, {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue({'name', 'level', 'description', 'muscles'} <= set(response.context['errors']))
        self.assertEqual(Exercise.objects.count(), self.count)

    def test_category_is_optional(self):
        self.client.post(self.create_url, self.payload(category=''))
        self.assertIsNone(Exercise.objects.get(name='Press militar').category)

    def test_fake_image_with_jpg_extension_is_rejected(self):
        fake = SimpleUploadedFile('portada.jpg', b'no soy una imagen', content_type='image/jpeg')
        response = self.client.post(self.create_url, self.payload(image=fake))
        self.assertEqual(response.status_code, 200)
        self.assertIn('image', response.context['errors'])
        self.assertEqual(Exercise.objects.count(), self.count)

    def test_oversized_image_is_rejected(self):
        big = SimpleUploadedFile('grande.png', b'0' * (5 * 1024 * 1024 + 1), content_type='image/png')
        response = self.client.post(self.create_url, self.payload(image=big))
        self.assertIn('pesa demasiado', response.context['errors']['image'])

    def test_valid_image_and_video_are_stored(self):
        image = SimpleUploadedFile('portada.png', png_bytes(), content_type='image/png')
        video = SimpleUploadedFile('demo.mp4', MP4_HEAD, content_type='video/mp4')
        response = self.client.post(self.create_url, self.payload(image=image, video_file=video, video_url=''))
        self.assertEqual(response.status_code, 302)
        e = Exercise.objects.get(name='Press militar')
        self.assertTrue(e.image.name.startswith('exercises/images/'))
        self.assertTrue(e.video_file.name.startswith('exercises/videos/'))

    def test_fake_video_is_rejected(self):
        for name, content, ctype in (('demo.mp4', b'texto', 'video/mp4'), ('demo.exe', MP4_HEAD, 'video/mp4'),
                                     ('demo.mp4', MP4_HEAD, 'text/plain')):
            with self.subTest(name=name, ctype=ctype):
                video = SimpleUploadedFile(name, content, content_type=ctype)
                response = self.client.post(self.create_url, self.payload(video_file=video))
                self.assertEqual(response.status_code, 200)
                self.assertIn('video_file', response.context['errors'])
        self.assertEqual(Exercise.objects.count(), self.count)

    def test_edit_without_new_files_keeps_existing_ones_and_unrelated_fields(self):
        image = SimpleUploadedFile('portada.png', png_bytes(), content_type='image/png')
        self.client.post(self.create_url, self.payload(image=image))
        e = Exercise.objects.get(name='Press militar')
        old_image = e.image.name
        response = self.client.post(reverse('trainer_exercise_edit', args=[e.pk]),
                                    self.payload(name='Press militar v2'))
        self.assertEqual(response.status_code, 302)
        e.refresh_from_db()
        self.assertEqual((e.name, e.image.name, e.is_active), ('Press militar v2', old_image, True))

    def test_invalid_edit_shows_the_stored_exercise_not_half_edited_data(self):
        e = Exercise.objects.create(name='Original', level='beginner', description='d', muscles='m')
        response = self.client.post(reverse('trainer_exercise_edit', args=[e.pk]),
                                    self.payload(name='Cambiado', level='inválido'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['exercise'].name, 'Original')
        e.refresh_from_db()
        self.assertEqual(e.name, 'Original')

    def test_inline_errors_are_not_flashed_again(self):
        # exercise_form.html pinta el error de video_url junto al campo
        response = self.client.post(self.create_url, self.payload(video_url='https://evil.com'))
        self.assertIn('video_url', response.context['errors'])
        self.assertContains(response, escape(response.context['errors']['video_url']))
        self.assertFalse([str(m) for m in response.context['messages']])
        # lo mismo al editar
        e = Exercise.objects.create(name='Original', level='beginner', description='d', muscles='m')
        response = self.client.post(reverse('trainer_exercise_edit', args=[e.pk]),
                                    self.payload(video_url='https://evil.com'))
        self.assertIn('video_url', response.context['errors'])
        self.assertFalse([str(m) for m in response.context['messages']])

    def test_error_not_painted_by_the_template_is_flashed(self):
        rendered = tuple(f for f in views.EXERCISE_RENDERED if f != 'video_url')
        with mock.patch.object(views, 'EXERCISE_RENDERED', rendered):
            response = self.client.post(self.create_url, self.payload(video_url='https://evil.com'))
        self.assertTrue(any('YouTube' in str(m) for m in response.context['messages']))


class ExerciseFiltersTests(ScenarioTestCase):
    def test_garbage_filters_do_not_500_on_public_and_trainer_lists(self):
        category = ExerciseCategory.objects.create(name='Core')
        self.exercise.category = category
        self.exercise.is_public = True
        self.exercise.save()
        self.login(self.trainer_a)
        for url in (reverse('exercises'), reverse('trainer_exercise_list')):
            for query in ('?category=abc', '?category=99999999999999999999', '?category=-1',
                          '?level=inventado', '?category=&level=', '?category=%00'):
                with self.subTest(url=url, query=query):
                    self.assertEqual(self.client.get(url + query).status_code, 200)
        response = self.client.get(reverse('exercises') + f'?category={category.pk}&level=beginner')
        self.assertEqual(list(response.context['exercises']), [self.exercise])
        self.assertEqual(response.context['selected_category'], str(category.pk))
        response = self.client.get(reverse('exercises') + '?category=abc')
        self.assertIsNone(response.context['selected_category'])
