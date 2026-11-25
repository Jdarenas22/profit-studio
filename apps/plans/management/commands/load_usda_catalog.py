"""Actualización offline; vista previa por defecto, activación explícita de referencias verificadas."""

import json
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.plans.models import Food
from apps.plans.sources.catalog import load_reference, source_is_consistent
from apps.plans.sources.fdc import FDCError
from apps.plans.sources.fdc_import import import_food


class Command(BaseCommand):
    help = "Previsualiza referencias USDA locales. --apply importa INACTIVAS; --activate habilita referencias comprobadas."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--activate", action="store_true")

    def handle(self, **options):
        if options["activate"] and not options["apply"]:
            raise CommandError("fdc_catalog_activation_requires_apply")
        try:
            _data, entries, pending = load_reference()
            if not options["apply"]:
                self.stdout.write(
                    json.dumps(
                        {
                            "operation": "preview_only",
                            "references": len(entries),
                            "pending": [
                                {"name": entry["name"], "reason": entry["reason"]}
                                for entry in pending
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            changed = activated = 0
            with transaction.atomic():
                for entry in entries:
                    food = (
                        Food.objects.select_for_update()
                        .filter(name=entry["name"])
                        .first()
                    )
                    if (
                        food
                        and food.fdc_id
                        and food.source_hash != entry["source_hash"]
                    ):
                        raise FDCError("fdc_catalog_existing_revision_requires_review")
                    if food is None:
                        food = Food.objects.create(**entry["seed"])
                    food, _snapshot, updated = import_food(
                        entry["record"],
                        food_id=food.pk,
                        state=entry["state"],
                        confirm_description=entry["expected_description"],
                        apply=True,
                    )
                    changed += int(updated)
                    if options["activate"] and not food.validated_by:
                        if not source_is_consistent(food):
                            raise FDCError("fdc_catalog_source_inconsistent")
                        if not food.is_active:
                            food.is_active = True
                            food.save(update_fields=["is_active"])
                            activated += 1
            self.stdout.write(
                json.dumps(
                    {
                        "operation": "applied",
                        "references": len(entries),
                        "changed": changed,
                        "activated": activated,
                        "needs_specification": len(pending),
                        "pending_unchanged": True,
                        "historical_plans_unchanged": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        except FDCError as error:
            raise CommandError(str(error)) from None
        except ValidationError:
            raise CommandError("fdc_local_validation_failed") from None
