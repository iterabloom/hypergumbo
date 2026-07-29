# SPDX-License-Identifier: AGPL-3.0-or-later
from django.urls import path
from . import views

urlpatterns = [
    path("users/", views.list_users),
    path("admin/", views.AdminView.as_view()),
]
