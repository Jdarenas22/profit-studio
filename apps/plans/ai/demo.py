"""Gemini real para perfiles sintéticos; cuota local persistente entre procesos."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.plans.academic import DEMO_MODEL, MEMBERS, demo_environment
from .base import AIPermanentError
from .gemini import GeminiClient

MAX_DAILY_CALLS = 30


def configured():
    return bool(demo_environment() and getattr(settings, 'DEMO_FREE_TIER_CONFIRMED', False)
                and settings.GEMINI_API_KEY and settings.GEMINI_MODEL == DEMO_MODEL)


def reserve_call():
    """Cada intento cuenta, también si Google falla; no reinicia al reiniciar Django."""
    if not configured():
        raise AIPermanentError('demo_not_configured')
    try:
        with closing(sqlite3.connect(settings.BASE_DIR / '.demo/api-budget.sqlite3', timeout=5)) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS calls (day TEXT PRIMARY KEY, amount INTEGER NOT NULL)')
            day = datetime.now(timezone.utc).date().isoformat()
            row = connection.execute('SELECT amount FROM calls WHERE day = ?', (day,)).fetchone()
            if row and row[0] >= MAX_DAILY_CALLS:
                raise AIPermanentError('demo_daily_limit')
            connection.execute('INSERT INTO calls VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET amount = amount + 1', (day,))
    except (sqlite3.Error, OSError):
        raise AIPermanentError('demo_budget_unavailable') from None


class DemoGeminiClient(GeminiClient):
    def generate_json(self, *, task, system, payload, schema, job_ref):
        from apps.plans.models import PlanGenerationJob
        if not configured():
            raise AIPermanentError('demo_not_configured')
        try:
            job = PlanGenerationJob.objects.select_related('client').get(
                job_ref=job_ref, client__username__in=MEMBERS)
        except (PlanGenerationJob.DoesNotExist, ValueError, ValidationError):
            raise AIPermanentError('demo_synthetic_user_required') from None
        if job.client.email != f'{job.client.username}@example.invalid':
            raise AIPermanentError('demo_synthetic_user_required')
        reserve_call()
        return super().generate_json(task=task, system=system, payload=payload, schema=schema, job_ref=job_ref)
