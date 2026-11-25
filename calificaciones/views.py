"""Vistas de la Plataforma NUAM: buscador, CRUD y certificados."""

import json
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

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
from .services.csv_loader import (
    CABECERA_FACTOR,
    CABECERA_MONTO,
    TIPO_FACTOR,
    TIPO_MONTO,
    CargaMasivaError,
    importar_filas,
    procesar_csv,
)

REGISTROS_POR_PAGINA = 10
HISTORIAL_POR_PAGINA = 15
AUDITORIA_POR_PAGINA = 20
ANIOS_PERIODO_BASE = [2026, 2025, 2024, 2023]

CARPETA_CARGAS_TMP = 'cargas_tmp'
TAMANO_MAXIMO_CSV = 10 * 1024 * 1024
PLANTILLAS_CSV = (
    'carga_x_factor_valida.csv',
    'carga_x_monto_valida.csv',
    'carga_con_errores_validacion.csv',
)


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


# ---------------------------------------------------------------------------
# Soporte de carga masiva CSV
# ---------------------------------------------------------------------------


def _carpeta_cargas() -> Path:
    carpeta = Path(settings.MEDIA_ROOT) / CARPETA_CARGAS_TMP
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _ruta_temporal(token: str) -> Path:
    return _carpeta_cargas() / Path(token).name


def _guardar_temporal(archivo) -> str:
    token = f'{uuid.uuid4().hex}.csv'
    archivo.seek(0)
    with open(_ruta_temporal(token), 'wb') as destino:
        for bloque in archivo.chunks():
            destino.write(bloque)
    return token


def _limpiar_carga(request) -> None:
    info = request.session.pop('carga_masiva', None)
    if not info:
        return
    ruta = _ruta_temporal(info.get('archivo', ''))
    if ruta.is_file():
        ruta.unlink(missing_ok=True)


def _especificacion_columnas(tipo: str) -> list[dict]:
    """Especificación de columnas para el modal VER FORMATO."""
    columnas = [
        (0, 'EJERCICIO', 'Entero de 4 dígitos (1000–9999).'),
        (1, 'MERCADO', 'AC, ACN, ACCIONES, DE o DEUDA.'),
        (2, 'NEMO', 'Texto no vacío, máximo 50 caracteres.'),
        (3, 'FEC_PAGO', 'Fecha estricta DD-MM-AAAA.'),
        (4, 'SEC_EVE', 'Entero mayor que 10000 (>= 10001).'),
        (5, 'DESCRIPCION', 'Texto hasta 100 caracteres.'),
        (6, 'RUT_CLIENTE', 'Texto no vacío hasta 12 caracteres.'),
        (7, 'NUM_DIVIDENDO', 'Entero mayor o igual a 0.'),
        (8, 'TIPO_SOCIEDAD', 'A (abierta) o C (cerrada).'),
    ]

    if tipo == TIPO_FACTOR:
        for numero in range(8, 38):
            posicion = 9 + (numero - 8)
            obligatorio = numero <= 32
            columnas.append(
                (
                    posicion,
                    f'F{numero}',
                    'Obligatorio; factor entre 0 y 1.'
                    if obligatorio
                    else 'Opcional (vacío = 0.00000000).',
                )
            )
    else:
        columnas.append(
            (9, 'VALOR_HISTORICO', 'Monto mayor o igual a 0 (base B).')
        )
        for numero in range(8, 38):
            posicion = 10 + (numero - 8)
            obligatorio = numero <= 32
            columnas.append(
                (
                    posicion,
                    f'M{numero}',
                    'Obligatorio; monto mayor o igual a 0.'
                    if obligatorio
                    else 'Opcional (vacío = 0).',
                )
            )

    return [
        {'posicion': posicion, 'columna': columna, 'regla': regla}
        for posicion, columna, regla in columnas
    ]


def _contexto_carga(tipo: str, **extra) -> dict:
    contexto = {
        'tipo': tipo,
        'es_monto': tipo == TIPO_MONTO,
        'especificacion': _especificacion_columnas(tipo),
        'cabecera': CABECERA_MONTO if tipo == TIPO_MONTO else CABECERA_FACTOR,
        'etiquetas_factores': (
            [f'M{numero}' for numero in range(8, 38)]
            if tipo == TIPO_MONTO
            else [f'F{numero}' for numero in range(8, 38)]
        ),
        'plantillas': PLANTILLAS_CSV,
    }
    contexto.update(extra)
    return contexto


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
    """Carga masiva CSV con previsualización, validación y confirmación."""
    tipo_solicitado = request.POST.get('tipo') or request.GET.get('tipo')
    tipo = TIPO_MONTO if tipo_solicitado == TIPO_MONTO else TIPO_FACTOR

    if request.method == 'GET':
        _limpiar_carga(request)
        return render(
            request,
            'calificaciones/carga_masiva.html',
            _contexto_carga(tipo),
        )

    accion = request.POST.get('accion', 'previsualizar')

    if accion != 'grabar':
        # Cualquier acción distinta de GRABAR invalida la previsualización
        # anterior para no importar un archivo que ya no está en pantalla.
        _limpiar_carga(request)

    if accion == 'cancelar':
        messages.info(request, 'Carga masiva cancelada.')
        return redirect('calificaciones:dashboard')

    if accion == 'grabar':
        info = request.session.get('carga_masiva')
        if not info:
            messages.error(
                request,
                'No hay una previsualización vigente. Suba el archivo nuevamente.',
            )
            return redirect('calificaciones:carga_masiva')

        ruta = _ruta_temporal(info['archivo'])
        if not ruta.is_file():
            _limpiar_carga(request)
            messages.error(
                request, 'El archivo temporal expiró. Vuelva a subirlo.'
            )
            return redirect('calificaciones:carga_masiva')

        try:
            with open(ruta, 'rb') as manejador:
                resultado = procesar_csv(
                    manejador, info['tipo'], info['nombre']
                )
        except CargaMasivaError as error:
            _limpiar_carga(request)
            messages.error(request, f'No se pudo procesar el CSV: {error}')
            return redirect('calificaciones:carga_masiva')

        creadas = []
        try:
            creadas = importar_filas(
                resultado, request.user, info['nombre']
            )
        except IntegrityError:
            _limpiar_carga(request)
            messages.error(
                request,
                'No se pudo completar la carga: uno de los registros ya '
                'existe o viola una restricción de la base de datos. '
                'Revise el archivo y vuelva a intentarlo.',
            )
            return redirect('calificaciones:carga_masiva')

        _limpiar_carga(request)

        if creadas:
            messages.success(
                request,
                f'Carga masiva completada: {len(creadas)} calificaciones '
                f'importadas correctamente.',
            )
        else:
            messages.warning(
                request,
                'No se importaron filas: todas tenían errores de validación.',
            )
        return redirect('calificaciones:dashboard')

    # Acción PREVISUALIZAR / VALIDAR
    archivo = request.FILES.get('archivo_csv')
    if archivo is None:
        messages.error(request, 'Debe seleccionar un archivo CSV.')
        return render(
            request,
            'calificaciones/carga_masiva.html',
            _contexto_carga(tipo),
        )

    if not archivo.name.lower().endswith('.csv'):
        messages.error(request, 'El archivo debe tener extensión .csv.')
        return render(
            request,
            'calificaciones/carga_masiva.html',
            _contexto_carga(tipo),
        )

    if archivo.size > TAMANO_MAXIMO_CSV:
        messages.error(request, 'El archivo CSV no puede superar 10 MB.')
        return render(
            request,
            'calificaciones/carga_masiva.html',
            _contexto_carga(tipo),
        )

    try:
        resultado = procesar_csv(archivo, tipo, archivo.name)
    except CargaMasivaError as error:
        messages.error(request, f'No se pudo procesar el CSV: {error}')
        return render(
            request,
            'calificaciones/carga_masiva.html',
            _contexto_carga(tipo),
        )

    _limpiar_carga(request)
    token = _guardar_temporal(archivo)
    request.session['carga_masiva'] = {
        'archivo': token,
        'tipo': tipo,
        'nombre': archivo.name,
    }

    return render(
        request,
        'calificaciones/carga_masiva.html',
        _contexto_carga(
            tipo,
            resultado=resultado,
            nombre_archivo=archivo.name,
            puede_grabar=resultado.total_validas > 0,
        ),
    )


@lectura_calificaciones
def carga_masiva_plantilla(request, nombre: str):
    """Descarga protegida de los CSV de ejemplo."""
    if nombre not in PLANTILLAS_CSV:
        raise Http404('Plantilla no disponible.')

    ruta = Path(settings.BASE_DIR) / 'sample_csv' / nombre
    if not ruta.is_file():
        raise Http404('La plantilla no existe en el servidor.')

    return FileResponse(
        ruta.open('rb'),
        as_attachment=True,
        filename=nombre,
        content_type='text/csv',
    )


def _json_legible(valor) -> str | None:
    """Serializa un snapshot JSON con indentación legible para la vista."""
    if valor is None:
        return None
    return json.dumps(valor, indent=2, ensure_ascii=False, sort_keys=True)


@lectura_calificaciones
def historial_ddjj(request):
    """Buscador histórico de Declaraciones Juradas con auditoría de consultas."""
    filtros = {
        'rut_cliente': request.GET.get('rut_cliente', '').strip(),
        'ejercicio': request.GET.get('ejercicio', '').strip(),
        'instrumento': request.GET.get('instrumento', '').strip(),
    }
    busqueda = any(filtros.values())

    calificaciones = Calificacion.objects.none()
    total = 0

    if busqueda:
        calificaciones = Calificacion.objects.select_related(
            'factores'
        ).order_by(
            '-ejercicio', 'instrumento', 'fecha_pago', 'secuencia_evento'
        )

        if filtros['rut_cliente']:
            calificaciones = calificaciones.filter(
                rut_cliente__icontains=filtros['rut_cliente']
            )
        if filtros['ejercicio'].isdigit():
            calificaciones = calificaciones.filter(
                ejercicio=int(filtros['ejercicio'])
            )
        if filtros['instrumento']:
            calificaciones = calificaciones.filter(
                instrumento__icontains=filtros['instrumento']
            )

        total = calificaciones.count()
        detalle = f'Consulta histórica DDJJ: {total} resultado(s)'
        criterios = []
        if filtros['rut_cliente']:
            criterios.append(f'RUT {filtros["rut_cliente"]}')
        if filtros['ejercicio']:
            criterios.append(f'ejercicio {filtros["ejercicio"]}')
        if filtros['instrumento']:
            criterios.append(f'instrumento {filtros["instrumento"]}')
        if criterios:
            detalle += ' — ' + ', '.join(criterios)
        detalle += '.'

        registrar_auditoria(
            request.user,
            RegistroAuditoria.ACCION_CONSULTA_HISTORICA,
            None,
            detalle,
            valor_nuevo={'filtros': filtros, 'resultados': total},
        )

    paginador = Paginator(calificaciones, HISTORIAL_POR_PAGINA)
    pagina = paginador.get_page(request.GET.get('page'))

    parametros = request.GET.copy()
    parametros.pop('page', None)

    return render(
        request,
        'calificaciones/historial_ddjj.html',
        {
            'filtros': filtros,
            'busqueda': busqueda,
            'page_obj': pagina,
            'paginator': paginador,
            'total': total,
            'anios': _anios_disponibles(),
            'columnas_factores': _columnas_factores(),
            'querystring': parametros.urlencode(),
        },
    )


@solo_administrador
def auditoria(request):
    """Log inmutable de operaciones; exclusivo del Administrador TI."""
    filtros = {
        'usuario': request.GET.get('usuario', '').strip(),
        'accion': request.GET.get('accion', '').strip(),
        'desde': request.GET.get('desde', '').strip(),
        'hasta': request.GET.get('hasta', '').strip(),
    }

    registros = RegistroAuditoria.objects.select_related('usuario').order_by(
        '-timestamp', '-pk'
    )

    if filtros['usuario']:
        registros = registros.filter(
            usuario__username=filtros['usuario']
        )
    if filtros['accion'] in dict(RegistroAuditoria.ACCION_CHOICES):
        registros = registros.filter(accion=filtros['accion'])

    desde = parse_date(filtros['desde']) if filtros['desde'] else None
    hasta = parse_date(filtros['hasta']) if filtros['hasta'] else None
    if desde:
        registros = registros.filter(timestamp__date__gte=desde)
    if hasta:
        registros = registros.filter(timestamp__date__lte=hasta)

    paginador = Paginator(registros, AUDITORIA_POR_PAGINA)
    pagina = paginador.get_page(request.GET.get('page'))

    registros_pagina = list(pagina.object_list)
    for registro in registros_pagina:
        registro.valor_anterior_json = _json_legible(
            registro.valor_anterior
        )
        registro.valor_nuevo_json = _json_legible(registro.valor_nuevo)

    usuarios = (
        RegistroAuditoria.objects.exclude(usuario__isnull=True)
        .values_list('usuario__username', flat=True)
        .distinct()
        .order_by('usuario__username')
    )

    parametros = request.GET.copy()
    parametros.pop('page', None)

    return render(
        request,
        'calificaciones/auditoria.html',
        {
            'filtros': filtros,
            'page_obj': pagina,
            'paginator': paginador,
            'registros': registros_pagina,
            'usuarios': usuarios,
            'acciones': RegistroAuditoria.ACCION_CHOICES,
            'total': paginador.count,
            'querystring': parametros.urlencode(),
        },
    )
