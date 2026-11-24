"""Trazabilidad inmutable de operaciones sobre calificaciones."""

from datetime import date, datetime
from decimal import Decimal

from .models import (
    ETIQUETAS_FACTORES,
    Calificacion,
    Factores,
    RegistroAuditoria,
    formatear,
)

CAMPOS_CALIFICACION = [
    'ejercicio',
    'mercado',
    'instrumento',
    'descripcion',
    'fecha_pago',
    'secuencia_evento',
    'numero_dividendo',
    'tipo_sociedad',
    'valor_historico',
    'factor_actualizacion',
    'evento_capital',
    'origen',
    'acogido_isfut',
    'calificacion_pendiente',
    'rut_cliente',
]


def _serializar(valor):
    """Convierte ``Decimal``, ``date`` y ``datetime`` a JSON serializable."""
    if isinstance(valor, Decimal):
        return formatear(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return valor


def serializar_calificacion(calificacion: Calificacion) -> dict:
    """Snapshot JSON de una calificación y sus factores."""
    datos = {'id': calificacion.pk}
    for campo in CAMPOS_CALIFICACION:
        datos[campo] = _serializar(getattr(calificacion, campo))

    datos['uri_documento'] = (
        calificacion.uri_documento.name if calificacion.uri_documento else None
    )
    datos['creado_por'] = (
        calificacion.creado_por.username
        if calificacion.creado_por_id
        else None
    )

    datos['factores'] = {}
    try:
        factores = calificacion.factores
    except Factores.DoesNotExist:
        factores = None
    if factores is not None:
        for campo, _ in ETIQUETAS_FACTORES:
            datos['factores'][campo] = formatear(getattr(factores, campo))

    return datos


def registrar_auditoria(
    usuario,
    accion: str,
    calificacion_id: int,
    detalle: str,
    valor_anterior: dict | None = None,
    valor_nuevo: dict | None = None,
) -> RegistroAuditoria:
    """Crea un registro de auditoría inmutable."""
    return RegistroAuditoria.objects.create(
        usuario=usuario,
        accion=accion,
        calificacion_id_ref=calificacion_id,
        detalle=detalle,
        valor_anterior=valor_anterior,
        valor_nuevo=valor_nuevo,
    )
