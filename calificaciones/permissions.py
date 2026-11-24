"""Control de acceso basado en roles (RBAC) para la plataforma NUAM.

Los roles se materializan como grupos de Django con nombres exactos. Los
decoradores y mixins de este módulo protegen las vistas: un usuario
autenticado sin el rol requerido recibe ``HTTP 403 Forbidden``
(``PermissionDenied``), mientras que un visitante anónimo es redirigido al
login conservando ``next``.
"""

from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

# ---------------------------------------------------------------------------
# Grupos RBAC
# ---------------------------------------------------------------------------

GRUPO_ADMIN = 'Administrador TI NUAM'
GRUPO_CORREDOR = 'Corredor de Bolsa'
GRUPO_AUDITOR = 'Auditor / Cumplimiento'

GRUPOS_RBAC = (GRUPO_ADMIN, GRUPO_CORREDOR, GRUPO_AUDITOR)

#: Descripción funcional de cada rol (usada en el dashboard y en el seed).
DESCRIPCION_ROLES = {
    GRUPO_ADMIN: (
        'Acceso total, gestión de usuarios y consulta de los logs de '
        'auditoría inmutables.'
    ),
    GRUPO_CORREDOR: (
        'Lectura, creación manual, carga masiva CSV, modificación, copia y '
        'eliminación de registros, además de carga y descarga de certificados PDF.'
    ),
    GRUPO_AUDITOR: (
        'Estrictamente solo lectura: buscador histórico de Declaraciones '
        'Juradas (DDJJ) y descarga de PDFs.'
    ),
}


# ---------------------------------------------------------------------------
# Helpers de consulta
# ---------------------------------------------------------------------------


def usuario_en_grupo(user, *grupos: str) -> bool:
    """Indica si ``user`` pertenece a alguno de los grupos indicados."""
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name__in=grupos).exists()


def es_administrador(user) -> bool:
    """Superusuario o miembro de ``Administrador TI NUAM``."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or usuario_en_grupo(user, GRUPO_ADMIN)


def es_corredor(user) -> bool:
    return usuario_en_grupo(user, GRUPO_CORREDOR)


def es_auditor(user) -> bool:
    return usuario_en_grupo(user, GRUPO_AUDITOR)


def tiene_rol_rbac(user) -> bool:
    """True si el usuario pertenece a alguno de los tres roles de la plataforma."""
    return es_administrador(user) or es_corredor(user) or es_auditor(user)


def puede_escribir(user) -> bool:
    """Crear, modificar, copiar, eliminar y carga masiva."""
    return es_administrador(user) or es_corredor(user)


def puede_cargar_certificados(user) -> bool:
    return es_administrador(user) or es_corredor(user)


def puede_ver_auditoria(user) -> bool:
    """Solo el administrador TI accede a los logs inmutables."""
    return es_administrador(user)


def puede_consultar_historial(user) -> bool:
    """Buscador histórico de DDJJ: disponible para los tres roles."""
    return tiene_rol_rbac(user)


# ---------------------------------------------------------------------------
# Decoradores
# ---------------------------------------------------------------------------


def rol_requerido(*grupos: str, permitir_superusuario: bool = True):
    """Restringe una vista a los grupos indicados.

    - Anónimo -> redirección a ``LOGIN_URL`` con ``next``.
    - Autenticado sin rol -> ``PermissionDenied`` (HTTP 403).
    - Superusuario -> acceso siempre permitido (salvo que se desactive).
    """

    def decorador(vista):
        @wraps(vista)
        def _vista_protegida(request, *args, **kwargs):
            user = getattr(request, 'user', None)

            if not user or not user.is_authenticated:
                return redirect_to_login(request.get_full_path())

            if permitir_superusuario and user.is_superuser:
                return vista(request, *args, **kwargs)

            if usuario_en_grupo(user, *grupos):
                return vista(request, *args, **kwargs)

            raise PermissionDenied(
                'No tiene permisos para acceder a esta sección.'
            )

        return _vista_protegida

    return decorador


#: Acceso total del administrador TI (incluye auditoría y usuarios).
solo_administrador = rol_requerido(GRUPO_ADMIN)

#: Escritura de calificaciones: administrador o corredor.
escritura_calificaciones = rol_requerido(GRUPO_ADMIN, GRUPO_CORREDOR)

#: Lectura para cualquier rol de la plataforma.
lectura_calificaciones = rol_requerido(GRUPO_ADMIN, GRUPO_CORREDOR, GRUPO_AUDITOR)


# ---------------------------------------------------------------------------
# Mixin para vistas basadas en clases
# ---------------------------------------------------------------------------


class RolRequeridoMixin(LoginRequiredMixin):
    """Mixin equivalente a :func:`rol_requerido` para vistas basadas en clases.

    Uso::

        class CalificacionListView(RolRequeridoMixin, ListView):
            grupos_requeridos = (GRUPO_ADMIN, GRUPO_CORREDOR, GRUPO_AUDITOR)
    """

    grupos_requeridos: tuple[str, ...] = ()
    permitir_superusuario = True

    def dispatch(self, request, *args, **kwargs):
        user = request.user

        if not user.is_authenticated:
            return self.handle_no_permission()

        if self.permitir_superusuario and user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        if self.grupos_requeridos and not usuario_en_grupo(
            user, *self.grupos_requeridos
        ):
            raise PermissionDenied(
                'No tiene permisos para acceder a esta sección.'
            )

        return super().dispatch(request, *args, **kwargs)
