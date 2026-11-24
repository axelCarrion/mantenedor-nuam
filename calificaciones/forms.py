"""Formularios Bootstrap 5 para el mantenedor de calificaciones."""

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from .models import (
    ETIQUETAS_FACTORES,
    Calificacion,
    Factores,
    cuantizar,
    formatear,
)

TAMANO_MAXIMO_PDF = 10 * 1024 * 1024  # 10 MB
FIRMA_PDF = b'%PDF'


def _aplicar_estilos_bootstrap(formulario) -> None:
    """Asigna clases de Bootstrap 5 a los widgets según su tipo."""
    for campo in formulario.fields.values():
        widget = campo.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault('class', 'form-check-input')
        elif isinstance(widget, forms.Select):
            widget.attrs.setdefault('class', 'form-select')
        elif isinstance(widget, forms.ClearableFileInput):
            widget.attrs.setdefault('class', 'form-control')
        else:
            widget.attrs.setdefault('class', 'form-control')


def validar_archivo_pdf(archivo) -> None:
    """Valida extensión, tamaño y cabecera binaria ``%PDF`` de un certificado."""
    if archivo is None:
        return

    if not archivo.name.lower().endswith('.pdf'):
        raise ValidationError(
            'El certificado debe ser un archivo con extensión .pdf.'
        )

    if archivo.size > TAMANO_MAXIMO_PDF:
        raise ValidationError(
            'El certificado supera el tamaño máximo permitido de 10 MB.'
        )

    encabezado = archivo.read(4)
    archivo.seek(0)
    if encabezado != FIRMA_PDF:
        raise ValidationError(
            'El archivo no es un PDF válido: falta la cabecera binaria %PDF.'
        )


class CalificacionForm(forms.ModelForm):
    """Datos de cabecera del instrumento más la carga del certificado PDF."""

    certificado_pdf = forms.FileField(
        required=False,
        label='Certificado PDF',
        help_text='Solo PDF de hasta 10 MB, con cabecera %PDF válida.',
        widget=forms.ClearableFileInput(attrs={'accept': 'application/pdf,.pdf'}),
    )

    class Meta:
        model = Calificacion
        fields = [
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
        widgets = {
            'fecha_pago': forms.DateInput(
                format='%Y-%m-%d', attrs={'type': 'date'}
            ),
            'valor_historico': forms.NumberInput(attrs={'step': '0.00000001'}),
            'factor_actualizacion': forms.NumberInput(
                attrs={'step': '0.00000001'}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _aplicar_estilos_bootstrap(self)
        self.fields['ejercicio'].widget.attrs.update(
            {'min': 1000, 'max': 9999, 'placeholder': '2025'}
        )
        self.fields['secuencia_evento'].widget.attrs.update(
            {'min': 10001, 'placeholder': '133947'}
        )
        self.fields['rut_cliente'].widget.attrs.update(
            {'placeholder': '76111222-3', 'maxlength': 12}
        )
        for campo in ('valor_historico', 'factor_actualizacion'):
            valor = self.initial.get(campo, getattr(self.instance, campo, None))
            self.initial[campo] = formatear(valor if valor is not None else 0)

    def clean_certificado_pdf(self):
        archivo = self.cleaned_data.get('certificado_pdf')
        validar_archivo_pdf(archivo)
        return archivo


class FactoresForm(forms.ModelForm):
    """Matriz de factores 08..37, 19B y 38."""

    class Meta:
        model = Factores
        fields = [campo for campo, _ in ETIQUETAS_FACTORES]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo, etiqueta in ETIQUETAS_FACTORES:
            field = self.fields[campo]
            field.label = etiqueta
            field.required = False
            field.widget = forms.NumberInput(
                attrs={
                    'class': (
                        'form-control form-control-sm text-end factor-input'
                    ),
                    'step': '0.00000001',
                    'min': '0',
                    'max': '1',
                    'inputmode': 'decimal',
                    'autocomplete': 'off',
                }
            )
            valor = self.initial.get(campo, getattr(self.instance, campo, None))
            self.initial[campo] = formatear(valor if valor is not None else 0)
        _aplicar_estilos_bootstrap(self)

    def clean(self):
        datos = super().clean()
        # Los campos vacíos se interpretan como 0.00000000.
        for campo, _ in ETIQUETAS_FACTORES:
            if datos.get(campo) in (None, ''):
                datos[campo] = Decimal('0.00000000')
            elif not isinstance(datos[campo], Decimal):
                datos[campo] = cuantizar(datos[campo])
        return datos
