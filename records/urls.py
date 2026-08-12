"""URL routing for records app."""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("search/explain/", views.search_explain, name="search_explain"),
    path("help/", views.help_page, name="help"),
]
