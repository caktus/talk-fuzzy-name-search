"""Admin registration for CourtRecord model."""

from django.contrib import admin

from .models import CourtRecord


@admin.register(CourtRecord)
class CourtRecordAdmin(admin.ModelAdmin):
    """Admin interface for CourtRecord model."""

    list_display = ("pk", "first_name", "last_name", "middle_name", "date_of_birth", "person_id")
    date_hierarchy = "date_of_birth"
    search_fields = ("first_name", "last_name", "middle_name")
    readonly_fields = ("person_id",)
    fieldsets = (
        (
            "Personal Information",
            {
                "fields": ("first_name", "last_name", "middle_name", "date_of_birth", "nicknames"),
            },
        ),
        (
            "Entity Resolution",
            {
                "fields": ("person_id",),
                "description": "Links records representing the same real person.",
            },
        ),
    )
