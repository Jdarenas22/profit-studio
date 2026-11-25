"""La URL pública y localhost sirven la misma web con cookies adecuadas al transporte."""

from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from config.middleware import PublicOriginMiddleware


@override_settings(
    PUBLIC_BASE_URL="https://one.trycloudflare.com",
    ALLOWED_HOSTS=["localhost", "127.0.0.1", "one.trycloudflare.com"],
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
)
class PublicOriginTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

        def response(request):
            value = HttpResponse("ok")
            for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
                value.set_cookie(name, "test-only")
            return value

        self.middleware = PublicOriginMiddleware(response)

    def test_local_http_remains_usable_on_the_same_application(self):
        response = self.middleware(self.factory.get("/", HTTP_HOST="127.0.0.1:8000"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.cookies[settings.SESSION_COOKIE_NAME]["secure"])

    def test_public_https_sets_secure_session_and_csrf_cookies(self):
        response = self.middleware(
            self.factory.get(
                "/", HTTP_HOST="one.trycloudflare.com", HTTP_X_FORWARDED_PROTO="https"
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.cookies[settings.SESSION_COOKIE_NAME]["secure"])
        self.assertTrue(response.cookies[settings.CSRF_COOKIE_NAME]["secure"])

    def test_public_http_redirects_before_returning_content(self):
        response = self.middleware(
            self.factory.get(
                "/accounts/login/?next=/plans/", HTTP_HOST="one.trycloudflare.com"
            )
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"],
            "https://one.trycloudflare.com/accounts/login/?next=/plans/",
        )
        self.assertFalse(response.cookies)
