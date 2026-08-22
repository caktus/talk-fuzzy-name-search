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

try:
    import debug_toolbar

    HAS_DEBUG_TOOLBAR = True
except ImportError:
    HAS_DEBUG_TOOLBAR = False

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(("records.urls", "records"), namespace="records")),
]

if HAS_DEBUG_TOOLBAR:
    # Registered whenever the toolbar package is installed (it is not
    # installed in the deployed --no-dev image, which is the gate that
    # matters in production). Gating on settings.DEBUG here breaks tests,
    # because the URLconf is imported lazily while DEBUG is overridden.
    urlpatterns.insert(0, path("__debug__/", include(debug_toolbar.urls)))
