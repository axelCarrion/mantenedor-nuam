"""Rutas de la app calificaciones."""

from django.urls import path

from . import views

app_name = 'calificaciones'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('calificaciones/nueva/', views.calificacion_crear, name='calificacion_crear'),
    path(
        'calificaciones/<int:pk>/editar/',
        views.calificacion_editar,
        name='calificacion_editar',
    ),
    path(
        'calificaciones/<int:pk>/eliminar/',
        views.calificacion_eliminar,
        name='calificacion_eliminar',
    ),
    path('calificaciones/carga-masiva/', views.carga_masiva, name='carga_masiva'),
    path('historial-ddjj/', views.historial_ddjj, name='historial_ddjj'),
    path('auditoria/', views.auditoria, name='auditoria'),
]
