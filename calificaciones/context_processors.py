"""Contexto de roles disponible en todas las plantillas."""

from .permissions import (
    GRUPO_ADMIN,
    GRUPO_AUDITOR,
    GRUPO_CORREDOR,
    es_administrador,
    es_auditor,
    es_corredor,
    puede_escribir,
)


def roles(request):
    user = getattr(request, 'user', None)

    if not user or not user.is_authenticated:
        return {
            'es_admin_ti': False,
            'puede_escribir_ui': False,
            'rol_principal': '',
        }

    if es_administrador(user):
        rol = GRUPO_ADMIN
    elif es_corredor(user):
        rol = GRUPO_CORREDOR
    elif es_auditor(user):
        rol = GRUPO_AUDITOR
    else:
        rol = ''

    return {
        'es_admin_ti': es_administrador(user),
        'puede_escribir_ui': puede_escribir(user),
        'rol_principal': rol,
    }
