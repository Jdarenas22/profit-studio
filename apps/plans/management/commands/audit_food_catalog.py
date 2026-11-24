import json
from django.core.management.base import BaseCommand, CommandError
from apps.plans.sources.catalog import (
    reference_report,
    source_is_consistent,
    reviewed_reference_matches,
    FIELDS,
)
from apps.plans.sources.fdc import FDCError


class Command(BaseCommand):
    help = "Audita las 90 referencias sin red ni base; --database también revisa el catálogo de la base seleccionada."

    def add_arguments(self, parser):
        parser.add_argument("--database", action="store_true")

    def handle(self, **options):
        try:
            report = reference_report()
        except (FDCError, ValueError, KeyError) as error:
            raise CommandError(
                str(error) if isinstance(error, FDCError) else "fdc_catalog_invalid"
            ) from None
        if options["database"]:
            from apps.plans.models import Food

            report["database"] = [
                {
                    "name": food.name,
                    "active": food.is_active,
                    "source_consistent": source_is_consistent(food),
                    "reviewed_reference_matches": reviewed_reference_matches(food),
                    "missing_nutrients": [
                        field for field in FIELDS if getattr(food, field) is None
                    ],
                }
                for food in Food.objects.order_by("name")
            ]
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
