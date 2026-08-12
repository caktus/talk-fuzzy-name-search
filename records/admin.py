"""Admin registration for Person model."""

from django.contrib import admin

from .models import Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    """Admin interface for Person model."""

    list_display = ("first_name", "last_name", "middle_name", "date_of_birth", "person_id")
    list_filter = ("date_of_birth",)
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
