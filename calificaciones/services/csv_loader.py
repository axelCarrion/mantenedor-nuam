"""Motor de carga masiva CSV para calificaciones tributarias.

Soporta dos modalidades:

* ``factor``: la fila entrega directamente los factores F8..F37.
* ``monto``: la fila entrega montos y se convierten a factores usando la
  base de cálculo ``B`` (valor histórico o suma de montos), con tolerancia
  de redondeo acotada a ``0.00000005`` cuando ``B`` proviene de la suma.

Toda la aritmética usa ``Decimal`` con ``ROUND_HALF_UP`` a 8 decimales.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from ..auditoria import registrar_auditoria
from ..models import Calificacion, Factores, cuantizar, formatear

CERO = Decimal('0.00000000')
UNO = Decimal('1.00000000')
TOLERANCIA_REDONDEO = Decimal('0.00000005')

TIPO_FACTOR = 'factor'
TIPO_MONTO = 'monto'
TIPOS = (TIPO_FACTOR, TIPO_MONTO)

CAMPOS_FACTOR = [f'factor_{numero:02d}' for numero in range(8, 38)]
ETIQUETAS_FACTOR = [f'Factor-{numero:02d}' for numero in range(8, 38)]

CABECERA_BASE = [
    'EJERCICIO',
    'MERCADO',
    'NEMO',
    'FEC_PAGO',
    'SEC_EVE',
    'DESCRIPCION',
    'RUT_CLIENTE',
    'NUM_DIVIDENDO',
    'TIPO_SOCIEDAD',
]
CABECERA_FACTOR = CABECERA_BASE + [f'F{numero}' for numero in range(8, 38)]
CABECERA_MONTO = (
    CABECERA_BASE
    + ['VALOR_HISTORICO']
    + [f'M{numero}' for numero in range(8, 38)]
)

REGEX_FECHA = re.compile(r'^\d{2}-\d{2}-\d{4}$')
REGEX_ENTERO = re.compile(r'^\d+$')

ALIAS_MERCADO = {
    'AC': Calificacion.MERCADO_ACCIONES,
    'ACN': Calificacion.MERCADO_ACCIONES,
    'ACCIONES': Calificacion.MERCADO_ACCIONES,
    'DE': Calificacion.MERCADO_DEUDA,
    'DEUDA': Calificacion.MERCADO_DEUDA,
}


class CargaMasivaError(Exception):
    """Error de formato que impide procesar el archivo completo."""


@dataclass
class FilaCarga:
    """Resultado del análisis de una fila del CSV."""

    numero: int
    datos: dict
    factores: dict
    errores: list[str] = field(default_factory=list)

    @property
    def valida(self) -> bool:
        return not self.errores

    @property
    def factores_formateados(self) -> list[tuple[str, str]]:
        return [
            (etiqueta, formatear(self.factores.get(campo, CERO)))
            for campo, etiqueta in zip(CAMPOS_FACTOR, ETIQUETAS_FACTOR)
        ]

    @property
    def valor_historico_formateado(self) -> str:
        return formatear(self.datos.get('valor_historico') or CERO)


@dataclass
class ResultadoCarga:
    """Conjunto de filas analizadas de un archivo CSV."""

    tipo: str
    filas: list[FilaCarga]
    nombre_archivo: str = ''

    @property
    def total_validas(self) -> int:
        return sum(1 for fila in self.filas if fila.valida)

    @property
    def total_errores(self) -> int:
        return len(self.filas) - self.total_validas


# ---------------------------------------------------------------------------
# Lectura y parseo
# ---------------------------------------------------------------------------


def leer_texto(archivo) -> str:
    """Decodifica el archivo probando ``utf-8-sig`` y luego ``latin-1``."""
    contenido = archivo.read()
    if isinstance(contenido, str):
        return contenido
    try:
        return contenido.decode('utf-8-sig')
    except UnicodeDecodeError:
        return contenido.decode('latin-1')


def _decimal(texto: str) -> Decimal | None:
    """Convierte texto numérico aceptando punto o coma decimal."""
    limpio = texto.strip().replace(' ', '').replace(',', '.')
    if not limpio:
        return None
    try:
        valor = Decimal(limpio)
    except InvalidOperation:
        return None
    if not valor.is_finite():
        return None
    return valor


def _entero(
    texto: str,
    campo: str,
    errores: list[str],
    minimo: int | None = None,
    maximo: int | None = None,
) -> int | None:
    limpio = texto.strip()
    if not limpio:
        errores.append(f'{campo} es obligatorio.')
        return None
    if not REGEX_ENTERO.match(limpio):
        errores.append(f'{campo} debe ser un número entero.')
        return None

    valor = int(limpio)
    if minimo is not None and valor < minimo:
        errores.append(f'{campo} debe ser mayor o igual a {minimo}.')
        return None
    if maximo is not None and valor > maximo:
        errores.append(f'{campo} debe ser menor o igual a {maximo}.')
        return None
    return valor


def _fecha(texto: str, errores: list[str]):
    limpio = texto.strip()
    if not limpio:
        errores.append('FEC_PAGO es obligatoria.')
        return None
    if not REGEX_FECHA.match(limpio):
        errores.append('FEC_PAGO debe tener el formato DD-MM-AAAA.')
        return None
    try:
        return datetime.strptime(limpio, '%d-%m-%Y').date()
    except ValueError:
        errores.append('FEC_PAGO no es una fecha válida.')
        return None


def _mercado(texto: str, errores: list[str]) -> str | None:
    codigo = texto.strip().upper()
    if not codigo:
        errores.append('MERCADO es obligatorio.')
        return None
    mercado = ALIAS_MERCADO.get(codigo)
    if mercado is None:
        errores.append(
            'MERCADO inválido: use AC, ACN, ACCIONES, DE o DEUDA.'
        )
    return mercado


def _validar_cabecera(cabecera: list[str], tipo: str) -> None:
    """Valida la cabecera permitiendo omitir las columnas opcionales finales.

    Para ``factor`` son obligatorias las posiciones 0 a 33 (F8..F32) y para
    ``monto`` las posiciones 0 a 34 (VALOR_HISTORICO + M8..M32). Las columnas
    posteriores pueden faltar o venir con encabezado vacío.
    """
    esperada = CABECERA_FACTOR if tipo == TIPO_FACTOR else CABECERA_MONTO
    minimo = 34 if tipo == TIPO_FACTOR else 35

    if len(cabecera) < minimo:
        raise CargaMasivaError(
            f'La cabecera debe tener al menos {minimo} columnas; '
            f'se encontraron {len(cabecera)}.'
        )

    for posicion, nombre in enumerate(cabecera[: len(esperada)]):
        encontrado = cabecera[posicion].strip().upper()
        if encontrado == nombre:
            continue
        if posicion >= minimo and not encontrado:
            continue
        raise CargaMasivaError(
            f'Cabecera inválida en la posición {posicion}: '
            f'se esperaba {nombre} y se encontró {encontrado or "(vacío)"}.'
        )


# ---------------------------------------------------------------------------
# Conversión matemática
# ---------------------------------------------------------------------------


def factores_desde_montos(
    montos: list[Decimal], valor_historico: Decimal
) -> tuple[list[Decimal], list[str]]:
    """Convierte montos a factores de 8 decimales con regla de tolerancia.

    Retorna ``(factores, errores)``. Si hay errores, los factores devueltos
    son los calculados hasta donde fue posible.
    """
    errores: list[str] = []

    if any(monto < CERO for monto in montos):
        return [CERO for _ in montos], [
            'Todos los montos deben ser mayores o iguales a 0.'
        ]

    base_desde_suma = valor_historico == CERO
    base = valor_historico if valor_historico > CERO else sum(montos, CERO)

    if base <= CERO:
        return [CERO for _ in montos], [
            'La base de cálculo B debe ser mayor que 0: '
            'indique VALOR_HISTORICO o montos con suma positiva.'
        ]

    factores = [cuantizar(monto / base) for monto in montos]

    for etiqueta, factor in zip(ETIQUETAS_FACTOR, factores):
        if factor > UNO:
            errores.append(
                f'{etiqueta} resultante ({formatear(factor)}) supera 1.00000000.'
            )

    suma = sum(factores, CERO)
    if suma > UNO:
        exceso = suma - UNO
        if base_desde_suma and exceso <= TOLERANCIA_REDONDEO:
            indice_mayor = max(
                range(len(factores)), key=lambda indice: factores[indice]
            )
            factores[indice_mayor] = cuantizar(
                factores[indice_mayor] - exceso
            )
            suma = sum(factores, CERO)
        else:
            errores.append(
                f'La suma de los factores ({formatear(suma)}) supera '
                '1.00000000 y no es corregible por tolerancia de redondeo.'
            )

    if sum(factores, CERO) > UNO:
        errores.append(
            'La suma de los factores resultantes supera 1.00000000.'
        )

    return factores, errores


# ---------------------------------------------------------------------------
# Procesamiento por fila
# ---------------------------------------------------------------------------


def _procesar_fila(
    celdas: list[str],
    tipo: str,
    numero: int,
    vistos: dict[tuple, int],
) -> FilaCarga:
    errores: list[str] = []
    celdas = [celda.strip() for celda in celdas]

    def valor(indice: int) -> str:
        return celdas[indice] if indice < len(celdas) else ''

    ejercicio = _entero(valor(0), 'EJERCICIO', errores, 1000, 9999)
    mercado = _mercado(valor(1), errores)

    instrumento = valor(2)
    if not instrumento:
        errores.append('NEMO es obligatorio.')
    elif len(instrumento) > 50:
        errores.append('NEMO no puede superar 50 caracteres.')

    fecha_pago = _fecha(valor(3), errores)
    secuencia = _entero(valor(4), 'SEC_EVE', errores, 10001)

    descripcion = valor(5)
    if len(descripcion) > 100:
        errores.append('DESCRIPCION no puede superar 100 caracteres.')

    rut_cliente = valor(6)
    if not rut_cliente:
        errores.append('RUT_CLIENTE es obligatorio.')
    elif len(rut_cliente) > 12:
        errores.append('RUT_CLIENTE no puede superar 12 caracteres.')

    numero_dividendo = _entero(
        valor(7), 'NUM_DIVIDENDO', errores, minimo=0
    )

    tipo_sociedad = valor(8).upper()
    if tipo_sociedad not in ('A', 'C'):
        errores.append('TIPO_SOCIEDAD debe ser A o C.')

    datos = {
        'ejercicio': ejercicio,
        'mercado': mercado,
        'instrumento': instrumento,
        'fecha_pago': fecha_pago,
        'secuencia_evento': secuencia,
        'descripcion': descripcion,
        'rut_cliente': rut_cliente,
        'numero_dividendo': numero_dividendo,
        'tipo_sociedad': tipo_sociedad,
        'valor_historico': CERO,
    }

    factores = {campo: CERO for campo in CAMPOS_FACTOR}

    if tipo == TIPO_FACTOR:
        factores = _factores_de_columnas(valor, errores)
    else:
        errores_montos: list[str] = []
        valor_historico = _monto(
            valor(9), 'VALOR_HISTORICO', errores_montos
        )
        datos['valor_historico'] = (
            valor_historico if valor_historico is not None else CERO
        )
        montos = _montos_de_columnas(valor, errores_montos)
        if not errores_montos:
            lista_factores, errores_conversion = factores_desde_montos(
                montos, valor_historico
            )
            errores_montos.extend(errores_conversion)
            factores = dict(zip(CAMPOS_FACTOR, lista_factores))
        errores.extend(errores_montos)

    base_valida = (
        ejercicio is not None
        and mercado is not None
        and bool(instrumento)
        and fecha_pago is not None
        and secuencia is not None
    )
    if base_valida:
        clave = (ejercicio, mercado, instrumento, fecha_pago, secuencia)
        if clave in vistos:
            errores.append(
                f'Duplicada en el archivo: ya fue cargada en la fila {vistos[clave]}.'
            )
        else:
            vistos[clave] = numero
            if Calificacion.objects.filter(
                ejercicio=ejercicio,
                mercado=mercado,
                instrumento=instrumento,
                fecha_pago=fecha_pago,
                secuencia_evento=secuencia,
            ).exists():
                errores.append(
                    'Ya existe una calificación en la base de datos para '
                    'este ejercicio, mercado, instrumento, fecha de pago y '
                    'secuencia de evento.'
                )

    return FilaCarga(numero=numero, datos=datos, factores=factores, errores=errores)


def _monto(texto: str, campo: str, errores: list[str]) -> Decimal | None:
    if not texto.strip():
        errores.append(f'{campo} es obligatorio.')
        return None
    valor = _decimal(texto)
    if valor is None:
        errores.append(f'{campo} no es un número válido.')
        return None
    valor = cuantizar(valor)
    if valor < CERO:
        errores.append(f'{campo} debe ser mayor o igual a 0.')
        return None
    return valor


def _factores_de_columnas(valor, errores: list[str]) -> dict:
    """Lee F8..F37; F8..F32 obligatorios y F33..F37 opcionales."""
    factores = {}
    for indice, (campo, etiqueta) in enumerate(
        zip(CAMPOS_FACTOR, ETIQUETAS_FACTOR)
    ):
        posicion = 9 + indice
        obligatorio = posicion <= 33
        texto = valor(posicion)

        if not texto:
            if obligatorio:
                errores.append(f'{etiqueta} es obligatorio.')
            factores[campo] = CERO
            continue

        numero = _decimal(texto)
        if numero is None:
            errores.append(f'{etiqueta} no es un número válido.')
            factores[campo] = CERO
            continue

        factor = cuantizar(numero)
        if factor < CERO or factor > UNO:
            errores.append(
                f'{etiqueta} debe estar entre 0.00000000 y 1.00000000.'
            )
        factores[campo] = factor

    suma = sum(factores.values(), CERO)
    if suma > UNO:
        errores.append(
            f'La suma de los factores 08 a 37 ({formatear(suma)}) '
            'supera 1.00000000.'
        )
    return factores


def _montos_de_columnas(valor, errores: list[str]) -> list[Decimal]:
    """Lee M8..M37; M8..M32 obligatorios y M33..M37 opcionales."""
    montos = []
    for indice, etiqueta in enumerate(ETIQUETAS_FACTOR):
        posicion = 10 + indice
        obligatorio = posicion <= 34
        texto = valor(posicion)
        campo = f'M{8 + indice}'

        if not texto:
            if obligatorio:
                errores.append(f'{campo} es obligatorio.')
            montos.append(CERO)
            continue

        numero = _decimal(texto)
        if numero is None:
            errores.append(f'{campo} no es un número válido.')
            montos.append(CERO)
            continue

        monto = cuantizar(numero)
        if monto < CERO:
            errores.append(f'{campo} debe ser mayor o igual a 0.')
        montos.append(monto)
    return montos


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def procesar_csv(archivo, tipo: str, nombre_archivo: str = '') -> ResultadoCarga:
    """Analiza un CSV y devuelve el resultado por fila (sin persistir)."""
    if tipo not in TIPOS:
        raise CargaMasivaError('Tipo de carga inválido: use factor o monto.')

    texto = leer_texto(archivo)
    if not texto.strip():
        raise CargaMasivaError('El archivo CSV está vacío.')

    lector = csv.reader(io.StringIO(texto), delimiter=';')
    filas = [fila for fila in lector if any(celda.strip() for celda in fila)]
    if not filas:
        raise CargaMasivaError('El archivo CSV no contiene datos.')

    if filas[0][0].strip().upper() == 'EJERCICIO':
        _validar_cabecera(filas[0], tipo)
        cuerpo = filas[1:]
        numero_inicial = 2
    else:
        cuerpo = filas
        numero_inicial = 1

    if not cuerpo:
        raise CargaMasivaError('El archivo CSV no contiene filas de datos.')

    resultado = ResultadoCarga(
        tipo=tipo, filas=[], nombre_archivo=nombre_archivo
    )
    vistos: dict[tuple, int] = {}

    for desplazamiento, celdas in enumerate(cuerpo):
        numero = numero_inicial + desplazamiento
        resultado.filas.append(
            _procesar_fila(celdas, tipo, numero, vistos)
        )

    return resultado


def importar_filas(
    resultado: ResultadoCarga, usuario, nombre_archivo: str = ''
) -> list[Calificacion]:
    """Persiste las filas válidas y registra el evento CARGA_MASIVA."""
    validas = [fila for fila in resultado.filas if fila.valida]
    creadas: list[Calificacion] = []

    if not validas:
        return creadas

    with transaction.atomic():
        for fila in validas:
            calificacion = Calificacion(
                **fila.datos,
                origen=Calificacion.ORIGEN_CORREDOR,
                creado_por=usuario,
            )
            calificacion.save()
            Factores.objects.create(
                calificacion=calificacion, **fila.factores
            )
            creadas.append(calificacion)

        detalle = (
            f'Carga masiva {resultado.tipo.upper()}: '
            f'{len(creadas)} registros creados'
        )
        if nombre_archivo:
            detalle += f' desde {nombre_archivo}'
        detalle += '.'

        registrar_auditoria(
            usuario,
            'CARGA_MASIVA',
            None,
            detalle,
            valor_nuevo={
                'tipo': resultado.tipo,
                'archivo': nombre_archivo,
                'creados': [
                    {
                        'id': calificacion.pk,
                        'instrumento': calificacion.instrumento,
                        'ejercicio': calificacion.ejercicio,
                        'secuencia_evento': calificacion.secuencia_evento,
                    }
                    for calificacion in creadas
                ],
            },
        )

    return creadas
