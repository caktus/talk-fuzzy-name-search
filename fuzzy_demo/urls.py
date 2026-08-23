"""
URL configuration for fuzzy_demo project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(("records.urls", "records"), namespace="records")),
]

if "debug_toolbar" in settings.INSTALLED_APPS:
    # settings.py adds debug_toolbar to INSTALLED_APPS only when DEBUG is
    # on, and the deployed --no-dev image does not ship the package. Gate
    # on INSTALLED_APPS (rather than DEBUG directly) because the URLconf
    # is imported lazily, after tests have overridden DEBUG.
    try:
        import debug_toolbar

        urlpatterns.insert(0, path("__debug__/", include(debug_toolbar.urls)))
    except ImportError:  # pragma: no cover - depends on install state
        pass
