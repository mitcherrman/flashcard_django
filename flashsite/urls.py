"""
URL configuration for flashsite project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
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
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    #path("admin/", admin.site.urls),
    path("api/flashcards/", include("flashcards.urls")),
]

# JSON (not HTML) for errors raised outside the API views, when DEBUG is off.
handler400 = "flashcards.errors.bad_request"
handler403 = "flashcards.errors.forbidden"
handler404 = "flashcards.errors.not_found"
handler500 = "flashcards.errors.server_error"
