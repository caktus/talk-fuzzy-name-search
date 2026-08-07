"""Admin registration for Person model."""

from django.contrib import admin
from django.utils.html import format_html

from .models import Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    """Admin interface for Person model."""

    list_display = ("first_name", "last_name", "middle_name", "date_of_birth", "phonetic_preview")
    list_filter = ("date_of_birth",)
    search_fields = ("first_name", "last_name", "middle_name")
    date_hierarchy = "date_of_birth"
    readonly_fields = ("first_name_phonetic", "last_name_phonetic", "phonetic_tokens_display")
    fieldsets = (
        (
            "Personal Information",
            {
                "fields": ("first_name", "last_name", "middle_name", "date_of_birth", "nicknames"),
            },
        ),
        (
            "Phonetic Tokens",
            {
                "fields": ("first_name_phonetic", "last_name_phonetic", "phonetic_tokens_display"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Phonetic")
    def phonetic_preview(self, obj):
        """Show first 2 tokens from each phonetic array."""
        first_tokens = ", ".join(obj.first_name_phonetic[:2]) if obj.first_name_phonetic else "--"
        last_tokens = ", ".join(obj.last_name_phonetic[:2]) if obj.last_name_phonetic else "--"
        return format_html(
            "<span>F: {}</span> <span>L: {}</span>",
            first_tokens,
            last_tokens,
        )

    @admin.display(description="All Tokens")
    def phonetic_tokens_display(self, obj):
        """Display all phonetic tokens in a formatted way."""
        first_tokens = obj.first_name_phonetic or []
        last_tokens = obj.last_name_phonetic or []

        html = "<div style='max-height: 200px; overflow-y: auto;'>"
        html += "<strong>First Name Tokens:</strong><br>"
        html += "<code>" + ", ".join(first_tokens) + "</code><br><br>"
        html += "<strong>Last Name Tokens:</strong><br>"
        html += "<code>" + ", ".join(last_tokens) + "</code>"
        html += "</div>"
        return format_html(html)
