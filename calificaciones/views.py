"""Vistas de la Plataforma NUAM: buscador, CRUD y certificados."""

from pathlib import Path

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from .auditoria import registrar_auditoria, serializar_calificacion
from .forms import CalificacionForm, FactoresForm
from .models import (
    ETIQUETAS_FACTORES,
    Calificacion,
    Factores,
    RegistroAuditoria,
)
from .permissions import (
    DESCRIPCION_ROLES,
    lectura_calificaciones,
    puede_escribir,
    puede_ver_auditoria,
    solo_administrador,
    escritura_calificaciones,
)

REGISTROS_POR_PAGINA = 10
ANIOS_PERIODO_BASE = [2026, 2025, 2024, 2023]


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------


def _columnas_factores() -> list[str]:
    return [etiqueta for _, etiqueta in ETIQUETAS_FACTORES]


def _columnas_matriz(factores_form: FactoresForm) -> list[list]:
    """Reparte los 32 campos de factores en 3 columnas para la matriz."""
    campos = [factores_form[campo] for campo, _ in ETIQUETAS_FACTORES]
    return [campos[indice::3] for indice in range(3)]


def _guardar_certificado(calificacion: Calificacion, archivo) -> None:
    """Guarda el PDF con la nomenclatura RUT_EJERCICIO_DIVIDENDO.pdf."""
    if not archivo:
        return

    nombre = (
        f'{calificacion.rut_cliente}_{calificacion.ejercicio}_'
        f'{calificacion.numero_dividendo}.pdf'
    )
    ruta = f'certificados/{nombre}'
    storage = calificacion.uri_documento.storage

    if storage.exists(ruta):
        storage.delete(ruta)

    calificacion.uri_documento.save(nombre, archivo, save=False)
    calificacion.save()


def _anios_disponibles() -> list[int]:
    anios = set(ANIOS_PERIODO_BASE)
    anios.update(Calificacion.objects.values_list('ejercicio', flat=True))
    return sorted(anios, reverse=True)


def _contexto_formulario(form, factores_form, modo: str, calificacion=None):
    return {
        'form': form,
        'factores_form': factores_form,
        'modo': modo,
        'calificacion': calificacion,
        'columnas_matriz': _columnas_matriz(factores_form),
        'campos_suma': [
            f'factores-factor_{numero:02d}' for numero in range(8, 38)
        ],
        'titulo': {
            'crear': 'Ingresar calificación',
            'editar': 'Modificar calificación',
            'copiar': 'Copiar calificación',
        }[modo],
    }


# ---------------------------------------------------------------------------
# Buscador principal
# ---------------------------------------------------------------------------


@lectura_calificaciones
def dashboard(request):
    """Panel principal con filtros GET, grilla paginada y acciones."""
    filtros = {
        'mercado': request.GET.get('mercado', '').strip(),
        'origen': request.GET.get('origen', '').strip(),
        'periodo': request.GET.get('periodo', '').strip(),
        'pendiente': request.GET.get('pendiente') == 'on',
    }

    calificaciones = Calificacion.objects.select_related(
        'factores', 'creado_por'
    ).order_by('-ejercicio', 'instrumento', 'secuencia_evento')

    if filtros['mercado'] in dict(Calificacion.MERCADO_CHOICES):
        calificaciones = calificaciones.filter(mercado=filtros['mercado'])
    if filtros['origen'] in dict(Calificacion.ORIGEN_CHOICES):
        calificaciones = calificaciones.filter(origen=filtros['origen'])
    if filtros['periodo'].isdigit():
        calificaciones = calificaciones.filter(
            ejercicio=int(filtros['periodo'])
        )
    if filtros['pendiente']:
        calificaciones = calificaciones.filter(calificacion_pendiente=True)

    paginador = Paginator(calificaciones, REGISTROS_POR_PAGINA)
    pagina = paginador.get_page(request.GET.get('page'))

    parametros = request.GET.copy()
    parametros.pop('page', None)

    contexto = {
        'page_obj': pagina,
        'paginator': paginador,
        'filtros': filtros,
        'anios': _anios_disponibles(),
        'columnas_factores': _columnas_factores(),
        'querystring': parametros.urlencode(),
        'puede_escribir': puede_escribir(request.user),
        'es_administrador': puede_ver_auditoria(request.user),
        'grupos_usuario': list(
            request.user.groups.values_list('name', flat=True)
        ),
        'descripcion_roles': [
            (nombre, DESCRIPCION_ROLES[nombre])
            for nombre in request.user.groups.values_list('name', flat=True)
            if nombre in DESCRIPCION_ROLES
        ],
        'total_calificaciones': Calificacion.objects.count(),
        'total_pendientes': Calificacion.objects.filter(
            calificacion_pendiente=True
        ).count(),
    }
    return render(request, 'calificaciones/dashboard.html', contexto)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@escritura_calificaciones
def calificacion_crear(request):
    if request.method == 'POST':
        form = CalificacionForm(request.POST, request.FILES)
        factores_form = FactoresForm(request.POST, prefix='factores')

        if form.is_valid() and factores_form.is_valid():
            with transaction.atomic():
                calificacion = form.save(commit=False)
                calificacion.creado_por = request.user
                calificacion.save()

                factores = factores_form.save(commit=False)
                factores.calificacion = calificacion
                factores.save()

                _guardar_certificado(
                    calificacion, form.cleaned_data.get('certificado_pdf')
                )
                registrar_auditoria(
                    request.user,
                    'CREAR',
                    calificacion.pk,
                    (
                        f'Creación de {calificacion.instrumento} '
                        f'{calificacion.ejercicio}, evento '
                        f'{calificacion.secuencia_evento}.'
                    ),
                    valor_nuevo=serializar_calificacion(calificacion),
                )

            messages.success(
                request,
                f'Calificación {calificacion.instrumento} '
                f'{calificacion.ejercicio} creada correctamente.',
            )
            return redirect('calificaciones:dashboard')

        messages.error(request, 'Revise los errores del formulario.')
    else:
        form = CalificacionForm()
        factores_form = FactoresForm(prefix='factores')

    return render(
        request,
        'calificaciones/calificacion_form.html',
        _contexto_formulario(form, factores_form, 'crear'),
    )


@escritura_calificaciones
def calificacion_editar(request, pk: int):
    calificacion = get_object_or_404(Calificacion, pk=pk)
    factores, _ = Factores.objects.get_or_create(calificacion=calificacion)

    if request.method == 'POST':
        anterior = serializar_calificacion(calificacion)
        form = CalificacionForm(request.POST, request.FILES, instance=calificacion)
        factores_form = FactoresForm(
            request.POST, prefix='factores', instance=factores
        )

        if form.is_valid() and factores_form.is_valid():
            with transaction.atomic():
                calificacion = form.save()
                factores = factores_form.save()
                _guardar_certificado(
                    calificacion, form.cleaned_data.get('certificado_pdf')
                )
                registrar_auditoria(
                    request.user,
                    'MODIFICAR',
                    calificacion.pk,
                    (
                        f'Modificación de {calificacion.instrumento} '
                        f'{calificacion.ejercicio}, evento '
                        f'{calificacion.secuencia_evento}.'
                    ),
                    valor_anterior=anterior,
                    valor_nuevo=serializar_calificacion(calificacion),
                )

            messages.success(
                request,
                f'Calificación {calificacion.instrumento} '
                f'{calificacion.ejercicio} actualizada correctamente.',
            )
            return redirect('calificaciones:dashboard')

        messages.error(request, 'Revise los errores del formulario.')
    else:
        form = CalificacionForm(instance=calificacion)
        factores_form = FactoresForm(prefix='factores', instance=factores)

    return render(
        request,
        'calificaciones/calificacion_form.html',
        _contexto_formulario(form, factores_form, 'editar', calificacion),
    )


@escritura_calificaciones
def calificacion_copiar(request, pk: int):
    original = get_object_or_404(Calificacion, pk=pk)

    if request.method == 'POST':
        form = CalificacionForm(request.POST, request.FILES)
        factores_form = FactoresForm(request.POST, prefix='factores')

        if form.is_valid() and factores_form.is_valid():
            with transaction.atomic():
                copia = form.save(commit=False)
                copia.pk = None
                copia.creado_por = request.user
                copia.uri_documento = None
                copia.save()

                factores = factores_form.save(commit=False)
                factores.calificacion = copia
                factores.save()

                _guardar_certificado(
                    copia, form.cleaned_data.get('certificado_pdf')
                )
                registrar_auditoria(
                    request.user,
                    'CREAR',
                    copia.pk,
                    (
                        f'Copia de {original.instrumento} '
                        f'{original.ejercicio} (evento {original.pk}) como '
                        f'evento {copia.secuencia_evento}.'
                    ),
                    valor_nuevo=serializar_calificacion(copia),
                )

            messages.success(
                request,
                f'Calificación copiada como {copia.instrumento} '
                f'{copia.ejercicio}, evento {copia.secuencia_evento}.',
            )
            return redirect('calificaciones:dashboard')

        messages.error(request, 'Revise los errores del formulario.')
    else:
        factores_originales = original.factores_para_mostrar
        form = CalificacionForm(
            initial={
                'ejercicio': original.ejercicio,
                'mercado': original.mercado,
                'instrumento': original.instrumento,
                'descripcion': original.descripcion,
                'fecha_pago': original.fecha_pago,
                'secuencia_evento': original.secuencia_evento + 1,
                'numero_dividendo': original.numero_dividendo,
                'tipo_sociedad': original.tipo_sociedad,
                'valor_historico': original.valor_historico,
                'factor_actualizacion': original.factor_actualizacion,
                'evento_capital': original.evento_capital,
                'origen': original.origen,
                'acogido_isfut': original.acogido_isfut,
                'calificacion_pendiente': original.calificacion_pendiente,
                'rut_cliente': original.rut_cliente,
            }
        )
        factores_form = FactoresForm(
            prefix='factores',
            initial={
                campo: getattr(factores_originales, campo)
                for campo, _ in ETIQUETAS_FACTORES
            },
        )

    return render(
        request,
        'calificaciones/calificacion_form.html',
        _contexto_formulario(form, factores_form, 'copiar', original),
    )


@escritura_calificaciones
def calificacion_eliminar(request, pk: int):
    calificacion = get_object_or_404(Calificacion, pk=pk)

    if request.method == 'POST':
        anterior = serializar_calificacion(calificacion)
        instrumento = calificacion.instrumento
        ejercicio = calificacion.ejercicio
        secuencia = calificacion.secuencia_evento
        identificador = calificacion.pk

        with transaction.atomic():
            calificacion.delete()
            registrar_auditoria(
                request.user,
                'ELIMINAR',
                identificador,
                (
                    f'Eliminación de {instrumento} {ejercicio}, evento '
                    f'{secuencia}.'
                ),
                valor_anterior=anterior,
            )

        messages.success(
            request,
            f'Calificación {instrumento} {ejercicio} eliminada correctamente.',
        )
        return redirect('calificaciones:dashboard')

    return render(
        request,
        'calificaciones/calificacion_confirmar_eliminar.html',
        {'calificacion': calificacion},
    )


# ---------------------------------------------------------------------------
# Certificados y módulos auxiliares
# ---------------------------------------------------------------------------


@lectura_calificaciones
def calificacion_pdf(request, pk: int):
    """Descarga/visualiza el certificado PDF asociado a una calificación."""
    calificacion = get_object_or_404(Calificacion, pk=pk)

    if not calificacion.uri_documento:
        raise Http404('La calificación no tiene certificado asociado.')

    try:
        archivo = calificacion.uri_documento.open('rb')
    except FileNotFoundError as exc:
        raise Http404('El certificado no existe en el almacenamiento.') from exc

    return FileResponse(
        archivo,
        content_type='application/pdf',
        filename=Path(calificacion.uri_documento.name).name,
    )


@escritura_calificaciones
def carga_masiva(request):
    tipo = request.GET.get('tipo', 'factor')
    etiqueta = 'factor' if tipo != 'monto' else 'monto'
    return render(
        request,
        'calificaciones/carga_masiva.html',
        {'tipo': etiqueta},
    )


@lectura_calificaciones
def historial_ddjj(request):
    return render(request, 'calificaciones/historial_ddjj.html')


@solo_administrador
def auditoria(request):
    """Log inmutable de operaciones; exclusivo del Administrador TI."""
    registros = RegistroAuditoria.objects.select_related('usuario')[:100]
    return render(
        request,
        'calificaciones/auditoria.html',
        {'registros': registros},
    )
