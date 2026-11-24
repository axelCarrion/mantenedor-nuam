"""Vistas base de la plataforma NUAM.

En este hito las vistas de escritura son *placeholders* protegidos por RBAC:
existen para que las rutas y sus permisos queden operativos (incluido el
HTTP 403 del rol Auditor). El CRUD completo se implementa en el siguiente paso.
"""

from django.http import HttpResponse
from django.shortcuts import render

from .models import Calificacion, RegistroAuditoria
from .permissions import (
    DESCRIPCION_ROLES,
    lectura_calificaciones,
    puede_escribir,
    puede_ver_auditoria,
    solo_administrador,
    escritura_calificaciones,
)


@lectura_calificaciones
def dashboard(request):
    """Panel inicial: resumen y accesos según el rol del usuario."""
    grupos_usuario = list(
        request.user.groups.values_list('name', flat=True)
    )
    contexto = {
        'total_calificaciones': Calificacion.objects.count(),
        'total_pendientes': Calificacion.objects.filter(
            calificacion_pendiente=True
        ).count(),
        'grupos_usuario': grupos_usuario,
        'descripcion_roles': [
            (nombre, DESCRIPCION_ROLES[nombre])
            for nombre in grupos_usuario
            if nombre in DESCRIPCION_ROLES
        ],
        'es_administrador': puede_ver_auditoria(request.user),
        'puede_escribir': puede_escribir(request.user),
    }
    return render(request, 'calificaciones/dashboard.html', contexto)


@escritura_calificaciones
def calificacion_crear(request):
    return HttpResponse(
        'Creación manual de calificaciones (pendiente de CRUD).',
        content_type='text/plain; charset=utf-8',
    )


@escritura_calificaciones
def calificacion_editar(request, pk: int):
    return HttpResponse(
        f'Edición de la calificación {pk} (pendiente de CRUD).',
        content_type='text/plain; charset=utf-8',
    )


@escritura_calificaciones
def calificacion_eliminar(request, pk: int):
    return HttpResponse(
        f'Eliminación de la calificación {pk} (pendiente de CRUD).',
        content_type='text/plain; charset=utf-8',
    )


@escritura_calificaciones
def carga_masiva(request):
    return HttpResponse(
        'Carga masiva CSV (pendiente de implementación).',
        content_type='text/plain; charset=utf-8',
    )


@lectura_calificaciones
def historial_ddjj(request):
    return HttpResponse(
        'Buscador histórico de Declaraciones Juradas (pendiente).',
        content_type='text/plain; charset=utf-8',
    )


@solo_administrador
def auditoria(request):
    """Log inmutable de operaciones; exclusivo del Administrador TI."""
    registros = RegistroAuditoria.objects.select_related('usuario')[:100]
    return render(
        request,
        'calificaciones/auditoria.html',
        {'registros': registros},
    )
