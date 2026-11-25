"""Modelos del Mantenedor de Calificaciones Tributarias - Plataforma NUAM.

Toda la aritmética financiera se realiza con ``decimal.Decimal`` y redondeo
``ROUND_HALF_UP`` (redondeo comercial), evitando por completo ``float``.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

# ---------------------------------------------------------------------------
# Utilidades numéricas
# ---------------------------------------------------------------------------

#: Precisión de los factores y valores monetarios (8 decimales, estilo FECU/NUAM).
OCHO_DECIMALES = Decimal('0.00000001')

#: Precisión de la suma de control de factores (8 decimales).
UNIDAD_FACTOR = Decimal('1.00000000')

#: Etiquetas de presentación de la matriz de factores, en orden.
ETIQUETAS_FACTORES = (
    [(f'factor_{numero:02d}', f'Factor-{numero:02d}') for numero in range(8, 20)]
    + [('factor_19b', 'Factor-19B')]
    + [(f'factor_{numero:02d}', f'Factor-{numero:02d}') for numero in range(20, 39)]
)


def cuantizar(valor) -> Decimal:
    """Normaliza un valor a 8 decimales usando redondeo ``ROUND_HALF_UP``.

    Acepta ``Decimal``, ``str`` o ``int``. Se rechaza ``float`` de forma
    explícita para no arrastrar errores de representación binaria.
    """

    if valor is None:
        return Decimal('0.00000000')
    if isinstance(valor, float):
        raise TypeError(
            'No se permite float en cálculos financieros; use Decimal o str.'
        )
    return Decimal(valor).quantize(OCHO_DECIMALES, rounding=ROUND_HALF_UP)


def formatear(valor) -> str:
    """Devuelve el valor con exactamente 8 decimales, apto para pantallas y PDF.

    ``str(Decimal('0E-8'))`` imprime ``0E-8``; este helper garantiza
    ``0.00000000`` en la presentación sin alterar el valor numérico.
    """

    return f'{cuantizar(valor):.8f}'


# ---------------------------------------------------------------------------
# Calificacion
# ---------------------------------------------------------------------------


class Calificacion(models.Model):
    """Calificación tributaria de un instrumento para un ejercicio dado."""

    MERCADO_ACCIONES = 'ACCIONES'
    MERCADO_DEUDA = 'DEUDA'

    MERCADO_CHOICES = [
        (MERCADO_ACCIONES, 'Acciones'),
        (MERCADO_DEUDA, 'Deuda'),
    ]

    #: Códigos abreviados aceptados y su equivalencia canónica.
    MERCADO_ALIAS = {
        'AC': MERCADO_ACCIONES,
        'ACN': MERCADO_ACCIONES,
        'DE': MERCADO_DEUDA,
    }

    TIPO_SOCIEDAD_CHOICES = [
        ('A', 'Abierta'),
        ('C', 'Cerrada'),
    ]

    ORIGEN_CORREDOR = 'CORREDOR'
    ORIGEN_SISTEMA = 'SISTEMA'

    ORIGEN_CHOICES = [
        (ORIGEN_CORREDOR, 'Corredor'),
        (ORIGEN_SISTEMA, 'Sistema'),
    ]

    ejercicio = models.IntegerField(
        validators=[MinValueValidator(1000), MaxValueValidator(9999)],
        help_text='Ejercicio tributario de 4 dígitos (ej. 2025).',
    )
    mercado = models.CharField(
        max_length=15,
        choices=MERCADO_CHOICES,
        help_text='Mercado del instrumento. Acepta ACCIONES/DEUDA o AC/ACN/DE.',
    )
    instrumento = models.CharField(
        max_length=50,
        help_text='NEMO del instrumento.',
    )
    descripcion = models.CharField(max_length=100, blank=True)
    fecha_pago = models.DateField()
    secuencia_evento = models.BigIntegerField(
        validators=[MinValueValidator(10001)],
        help_text='Secuencia del evento; debe ser mayor que 10000.',
    )
    numero_dividendo = models.BigIntegerField(default=0)
    tipo_sociedad = models.CharField(
        max_length=1,
        choices=TIPO_SOCIEDAD_CHOICES,
    )
    valor_historico = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        default=Decimal('0.00000000'),
    )
    factor_actualizacion = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        default=Decimal('0.00000000'),
    )
    evento_capital = models.IntegerField(default=0)
    origen = models.CharField(
        max_length=15,
        choices=ORIGEN_CHOICES,
        default=ORIGEN_CORREDOR,
    )
    acogido_isfut = models.BooleanField(default=False)
    calificacion_pendiente = models.BooleanField(default=False)
    rut_cliente = models.CharField(max_length=12)
    uri_documento = models.FileField(
        upload_to='certificados/',
        max_length=255,
        blank=True,
        null=True,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='calificaciones_creadas',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Calificación'
        verbose_name_plural = 'Calificaciones'
        ordering = ['-ejercicio', 'instrumento', 'secuencia_evento']
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'ejercicio',
                    'mercado',
                    'instrumento',
                    'fecha_pago',
                    'secuencia_evento',
                ],
                name='uniq_calificacion_evento',
            ),
        ]
        indexes = [
            models.Index(fields=['ejercicio', 'instrumento']),
            models.Index(fields=['rut_cliente']),
        ]

    def __str__(self) -> str:
        return (
            f'{self.instrumento} {self.ejercicio} '
            f'evento {self.secuencia_evento}'
        )

    def normalizar_mercado(self) -> None:
        """Convierte alias (AC/ACN/DE) al valor canónico (ACCIONES/DEUDA)."""
        if self.mercado:
            codigo = str(self.mercado).strip().upper()
            self.mercado = self.MERCADO_ALIAS.get(codigo, codigo)

    def clean_fields(self, exclude=None):
        # Debe ejecutarse antes de la validación de choices del campo.
        self.normalizar_mercado()
        if self.valor_historico is not None:
            self.valor_historico = cuantizar(self.valor_historico)
        if self.factor_actualizacion is not None:
            self.factor_actualizacion = cuantizar(self.factor_actualizacion)
        super().clean_fields(exclude=exclude)

    def clean(self) -> None:
        super().clean()

        self.normalizar_mercado()
        if self.mercado and self.mercado not in dict(self.MERCADO_CHOICES):
            raise ValidationError(
                {'mercado': 'Mercado inválido. Use ACCIONES, DEUDA, AC, ACN o DE.'}
            )

        if self.ejercicio is not None and not (1000 <= self.ejercicio <= 9999):
            raise ValidationError(
                {'ejercicio': 'El ejercicio debe tener exactamente 4 dígitos.'}
            )

        if self.secuencia_evento is not None and self.secuencia_evento <= 10000:
            raise ValidationError(
                {'secuencia_evento': 'La secuencia del evento debe ser mayor que 10000.'}
            )

        self.valor_historico = cuantizar(self.valor_historico)
        self.factor_actualizacion = cuantizar(self.factor_actualizacion)

    def save(self, *args, **kwargs):
        self.normalizar_mercado()
        self.valor_historico = cuantizar(self.valor_historico)
        self.factor_actualizacion = cuantizar(self.factor_actualizacion)
        super().save(*args, **kwargs)

    @property
    def valor_actualizado(self) -> Decimal:
        """Valor histórico actualizado por factor, con redondeo comercial."""
        return cuantizar(
            cuantizar(self.valor_historico) * cuantizar(self.factor_actualizacion)
        )

    @property
    def factores_para_mostrar(self) -> 'Factores':
        """Factores asociados o una instancia vacía para grillas y reportes."""
        try:
            return self.factores
        except Factores.DoesNotExist:
            return Factores(calificacion=self)

    @property
    def factor_actualizacion_formateado(self) -> str:
        return formatear(self.factor_actualizacion)

    @property
    def valor_historico_formateado(self) -> str:
        return formatear(self.valor_historico)


# ---------------------------------------------------------------------------
# Factores
# ---------------------------------------------------------------------------


class Factores(models.Model):
    """Factores de distribución (08 a 38) asociados a una calificación."""

    #: Factores que participan en la suma de control (08..37).
    CAMPOS_SUMA_CONTROL = [
        f'factor_{numero:02d}' for numero in range(8, 38)
    ]

    calificacion = models.OneToOneField(
        Calificacion,
        on_delete=models.CASCADE,
        related_name='factores',
    )

    factor_08 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_09 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_10 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_11 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_12 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_13 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_14 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_15 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_16 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_17 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_18 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_19 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_19b = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_20 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_21 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_22 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_23 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_24 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_25 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_26 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_27 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_28 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_29 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_30 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_31 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_32 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_33 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_34 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_35 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_36 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_37 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )
    factor_38 = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal('0.00000000')
    )

    class Meta:
        verbose_name = 'Factores'
        verbose_name_plural = 'Factores'

    def __str__(self) -> str:
        return f'Factores de {self.calificacion_id}'

    @property
    def total_suma_control(self) -> Decimal:
        """Suma de los factores 08..37 (sin 19b ni 38)."""
        total = sum(
            (cuantizar(getattr(self, campo)) for campo in self.CAMPOS_SUMA_CONTROL),
            Decimal('0.00000000'),
        )
        return cuantizar(total)

    @property
    def valores_formateados(self) -> list[tuple[str, str]]:
        """Pares (etiqueta, valor) con 8 decimales para grillas y PDFs."""
        return [
            (etiqueta, formatear(getattr(self, campo)))
            for campo, etiqueta in ETIQUETAS_FACTORES
        ]

    def clean_fields(self, exclude=None):
        # Cuantiza antes de las validaciones de dígitos decimales del campo.
        for campo in self.CAMPOS_SUMA_CONTROL + ['factor_19b', 'factor_38']:
            valor = getattr(self, campo)
            if valor is not None:
                setattr(self, campo, cuantizar(valor))
        super().clean_fields(exclude=exclude)

    def clean(self) -> None:
        super().clean()
        errores = {}

        for campo in self.CAMPOS_SUMA_CONTROL + ['factor_19b', 'factor_38']:
            valor = cuantizar(getattr(self, campo))

            if valor < Decimal('0.00000000') or valor > UNIDAD_FACTOR:
                errores[campo] = (
                    'El factor debe estar entre 0.00000000 y 1.00000000.'
                )
            setattr(self, campo, valor)

        total = sum(
            (cuantizar(getattr(self, campo)) for campo in self.CAMPOS_SUMA_CONTROL),
            Decimal('0.00000000'),
        )
        if cuantizar(total) > UNIDAD_FACTOR:
            errores['__all__'] = [
                'La suma de los factores 08 a 37 no puede superar 1.00000000 '
                f'(suma actual: {cuantizar(total)}).'
            ]

        if errores:
            raise ValidationError(errores)

    def save(self, *args, **kwargs):
        for campo in self.CAMPOS_SUMA_CONTROL + ['factor_19b', 'factor_38']:
            setattr(self, campo, cuantizar(getattr(self, campo)))
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# RegistroAuditoria
# ---------------------------------------------------------------------------


class RegistroAuditoria(models.Model):
    """Bitácora inmutable de operaciones sobre calificaciones."""

    ACCION_CREAR = 'CREAR'
    ACCION_MODIFICAR = 'MODIFICAR'
    ACCION_ELIMINAR = 'ELIMINAR'
    ACCION_CARGA_MASIVA = 'CARGA_MASIVA'
    ACCION_CONSULTA_HISTORICA = 'CONSULTA_HISTORICA'

    ACCION_CHOICES = [
        (ACCION_CREAR, 'Crear'),
        (ACCION_MODIFICAR, 'Modificar'),
        (ACCION_ELIMINAR, 'Eliminar'),
        (ACCION_CARGA_MASIVA, 'Carga masiva'),
        (ACCION_CONSULTA_HISTORICA, 'Consulta histórica'),
    ]

    #: Clase Bootstrap del badge asociado a cada acción.
    ACCION_BADGES = {
        ACCION_CREAR: 'success',
        ACCION_MODIFICAR: 'warning',
        ACCION_ELIMINAR: 'danger',
        ACCION_CARGA_MASIVA: 'primary',
        ACCION_CONSULTA_HISTORICA: 'info',
    }

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='registros_auditoria',
    )
    accion = models.CharField(max_length=30, choices=ACCION_CHOICES)
    calificacion_id_ref = models.IntegerField(null=True, blank=True)
    detalle = models.TextField()
    valor_anterior = models.JSONField(null=True, blank=True)
    valor_nuevo = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Registro de auditoría'
        verbose_name_plural = 'Registros de auditoría'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['accion', 'timestamp']),
            models.Index(fields=['calificacion_id_ref']),
        ]

    def __str__(self) -> str:
        return f'{self.accion} #{self.calificacion_id_ref} ({self.timestamp:%Y-%m-%d %H:%M})'

    @property
    def accion_badge(self) -> str:
        """Clase Bootstrap del badge según el tipo de acción."""
        return self.ACCION_BADGES.get(self.accion, 'secondary')

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(
                'Los registros de auditoría son inmutables: no pueden modificarse.'
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            'Los registros de auditoría son inmutables: no pueden eliminarse.'
        )
