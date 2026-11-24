"""Comando idempotente de datos base para la Plataforma NUAM.

Uso::

    venv\\Scripts\\python.exe manage.py seed_nuam

Puede ejecutarse tantas veces como sea necesario: crea o actualiza grupos
RBAC, usuarios de prueba, calificaciones de ejemplo, su certificado PDF y los
CSV de muestra, sin generar duplicados.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from calificaciones.models import Calificacion, Factores, formatear
from calificaciones.permissions import (
    GRUPO_ADMIN,
    GRUPO_AUDITOR,
    GRUPO_CORREDOR,
)

PASSWORD_PRUEBA = 'Nuam2026*'
CERO = Decimal('0.00000000')

CAMPOS_FACTORES = [f'factor_{numero:02d}' for numero in range(8, 38)]
CAMPOS_FACTORES_COMPLETOS = CAMPOS_FACTORES + ['factor_19b', 'factor_38']

CABECERA_FACTORES = (
    [
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
    + [f'F{numero}' for numero in range(8, 38)]
)

CABECERA_MONTOS = (
    [
        'EJERCICIO',
        'MERCADO',
        'NEMO',
        'FEC_PAGO',
        'SEC_EVE',
        'DESCRIPCION',
        'RUT_CLIENTE',
        'NUM_DIVIDENDO',
        'TIPO_SOCIEDAD',
        'VALOR_HISTORICO',
    ]
    + [f'M{numero}' for numero in range(8, 38)]
)


def _factores_cero() -> dict:
    return {campo: CERO for campo in CAMPOS_FACTORES_COMPLETOS}


def _fila_factores(
    ejercicio,
    nemo,
    fecha_pago,
    secuencia,
    descripcion,
    rut_cliente,
    numero_dividendo,
    valores: dict,
) -> list[str]:
    fila = [
        str(ejercicio),
        'ACCIONES',
        nemo,
        fecha_pago,
        str(secuencia),
        descripcion,
        rut_cliente,
        str(numero_dividendo),
        'A',
    ]
    for numero in range(8, 38):
        fila.append(formatear(valores.get(f'factor_{numero:02d}', CERO)))
    return fila


class Command(BaseCommand):
    help = (
        'Crea/actualiza grupos RBAC, usuarios de prueba, calificaciones base, '
        'PDF de certificado y CSV de muestra (idempotente).'
    )

    @transaction.atomic
    def handle(self, *args, **options):
        self._crear_grupos()
        usuarios = self._crear_usuarios()
        calificaciones = self._crear_calificaciones(usuarios['corredor1'])
        self._crear_pdf(calificaciones['FROWARD'])
        self._crear_csv()

        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(
                f'seed_nuam completado: {Group.objects.filter(name__in=[GRUPO_ADMIN, GRUPO_CORREDOR, GRUPO_AUDITOR]).count()} '
                f'grupos, {get_user_model().objects.filter(username__in=["admin_nuam", "corredor1", "auditor1"]).count()} '
                f'usuarios, {Calificacion.objects.count()} calificaciones, '
                f'{Factores.objects.count()} registros de factores.'
            )
        )

    # ------------------------------------------------------------------
    # Grupos y permisos
    # ------------------------------------------------------------------

    def _crear_grupos(self) -> None:
        permisos = Permission.objects.filter(
            content_type__app_label='calificaciones'
        )
        permisos_admin = permisos
        permisos_corredor = permisos.filter(
            content_type__model__in=['calificacion', 'factores'],
            codename__regex=r'^(add|change|delete|view)_',
        )
        permisos_auditor = permisos.filter(
            content_type__model__in=['calificacion', 'factores'],
            codename__startswith='view_',
        )

        definicion = {
            GRUPO_ADMIN: permisos_admin,
            GRUPO_CORREDOR: permisos_corredor,
            GRUPO_AUDITOR: permisos_auditor,
        }

        for nombre, permisos_grupo in definicion.items():
            grupo, creado = Group.objects.get_or_create(name=nombre)
            grupo.permissions.set(permisos_grupo)
            estado = 'creado' if creado else 'actualizado'
            self.stdout.write(f'  Grupo {nombre!r} {estado}.')

    # ------------------------------------------------------------------
    # Usuarios
    # ------------------------------------------------------------------

    def _crear_usuarios(self) -> dict:
        Usuario = get_user_model()
        definicion = {
            'admin_nuam': {
                'grupo': GRUPO_ADMIN,
                'is_staff': True,
                'is_superuser': True,
                'nombre': 'Administrador',
                'apellido': 'TI NUAM',
            },
            'corredor1': {
                'grupo': GRUPO_CORREDOR,
                'is_staff': False,
                'is_superuser': False,
                'nombre': 'Corredor',
                'apellido': 'de Bolsa',
            },
            'auditor1': {
                'grupo': GRUPO_AUDITOR,
                'is_staff': False,
                'is_superuser': False,
                'nombre': 'Auditor',
                'apellido': 'Cumplimiento',
            },
        }

        usuarios = {}
        for username, datos in definicion.items():
            usuario, creado = Usuario.objects.get_or_create(username=username)
            usuario.first_name = datos['nombre']
            usuario.last_name = datos['apellido']
            usuario.email = f'{username}@nuam.local'
            usuario.is_staff = datos['is_staff']
            usuario.is_superuser = datos['is_superuser']
            usuario.is_active = True
            usuario.set_password(PASSWORD_PRUEBA)
            usuario.save()

            usuario.groups.set([Group.objects.get(name=datos['grupo'])])
            usuarios[username] = usuario
            estado = 'creado' if creado else 'actualizado'
            self.stdout.write(f'  Usuario {username!r} {estado} ({datos["grupo"]}).')

        return usuarios

    # ------------------------------------------------------------------
    # Calificaciones base
    # ------------------------------------------------------------------

    def _crear_calificaciones(self, corredor) -> dict:
        base = [
            {
                'clave': 'ADP',
                'ejercicio': 2025,
                'mercado': Calificacion.MERCADO_ACCIONES,
                'instrumento': 'ADP',
                'descripcion': 'DIVIDENDO US$ 1,54',
                'fecha_pago': '2025-04-01',
                'secuencia_evento': 133947,
                'numero_dividendo': 1,
                'rut_cliente': '76111222-3',
                'factores': {},
            },
            {
                'clave': 'JEEP',
                'ejercicio': 2025,
                'mercado': Calificacion.MERCADO_ACCIONES,
                'instrumento': 'JEEP',
                'descripcion': 'JEEP ACC 1X1',
                'fecha_pago': '2025-01-02',
                'secuencia_evento': 100000809,
                'numero_dividendo': 2,
                'rut_cliente': '76333444-5',
                'factores': {},
            },
            {
                'clave': 'FROWARD',
                'ejercicio': 2023,
                'mercado': Calificacion.MERCADO_ACCIONES,
                'instrumento': 'FROWARD',
                'descripcion': 'DIVIDENDO DE PRUEBA',
                'fecha_pago': '2023-05-23',
                'secuencia_evento': 100000391,
                'numero_dividendo': 3,
                'rut_cliente': '76555666-7',
                'factores': {'factor_16': Decimal('1.00000000')},
            },
        ]

        from datetime import date

        calificaciones = {}
        for datos in base:
            anio, mes, dia = (int(parte) for parte in datos['fecha_pago'].split('-'))
            calificacion, creado = Calificacion.objects.update_or_create(
                ejercicio=datos['ejercicio'],
                mercado=datos['mercado'],
                instrumento=datos['instrumento'],
                fecha_pago=date(anio, mes, dia),
                secuencia_evento=datos['secuencia_evento'],
                defaults={
                    'descripcion': datos['descripcion'],
                    'numero_dividendo': datos['numero_dividendo'],
                    'tipo_sociedad': 'A',
                    'rut_cliente': datos['rut_cliente'],
                    'origen': Calificacion.ORIGEN_CORREDOR,
                    'valor_historico': CERO,
                    'factor_actualizacion': CERO,
                    'evento_capital': 0,
                    'acogido_isfut': False,
                    'calificacion_pendiente': False,
                    'creado_por': corredor,
                },
            )

            factores = _factores_cero()
            factores.update(datos['factores'])
            Factores.objects.update_or_create(
                calificacion=calificacion,
                defaults=factores,
            )

            estado = 'creada' if creado else 'actualizada'
            self.stdout.write(
                f'  Calificación {datos["instrumento"]} {datos["ejercicio"]} {estado}.'
            )
            calificaciones[datos['clave']] = calificacion

        return calificaciones

    # ------------------------------------------------------------------
    # Certificado PDF (reportlab)
    # ------------------------------------------------------------------

    def _crear_pdf(self, calificacion: Calificacion) -> Path:
        carpeta = Path(settings.MEDIA_ROOT) / 'certificados'
        carpeta.mkdir(parents=True, exist_ok=True)
        nombre = f'{calificacion.rut_cliente}_{calificacion.ejercicio}_{calificacion.numero_dividendo}.pdf'
        ruta = carpeta / nombre

        documento = canvas.Canvas(str(ruta), pagesize=A4)
        ancho, alto = A4
        margen = 20 * mm

        documento.setTitle(f'Certificado {calificacion.instrumento} {calificacion.ejercicio}')
        documento.setAuthor('Plataforma NUAM')

        documento.setFont('Helvetica-Bold', 16)
        documento.drawString(margen, alto - margen, 'Plataforma NUAM')
        documento.setFont('Helvetica-Bold', 12)
        documento.drawString(
            margen,
            alto - margen - 8 * mm,
            'Certificado de Calificación Tributaria',
        )
        documento.setFont('Helvetica', 9)
        documento.drawString(
            margen,
            alto - margen - 14 * mm,
            'Documento de prueba generado por manage.py seed_nuam',
        )

        filas = [
            ('NEMO', calificacion.instrumento),
            ('Ejercicio', str(calificacion.ejercicio)),
            ('Mercado', calificacion.mercado),
            ('RUT cliente', calificacion.rut_cliente),
            ('Fecha de pago', calificacion.fecha_pago.strftime('%d-%m-%Y')),
            ('Secuencia de evento', str(calificacion.secuencia_evento)),
            ('N° de dividendo', str(calificacion.numero_dividendo)),
            ('Tipo de sociedad', calificacion.get_tipo_sociedad_display()),
            ('Origen', calificacion.get_origen_display()),
            ('Descripción', calificacion.descripcion),
        ]

        y = alto - margen - 26 * mm
        for etiqueta, valor in filas:
            documento.setFont('Helvetica-Bold', 10)
            documento.drawString(margen, y, f'{etiqueta}:')
            documento.setFont('Helvetica', 10)
            documento.drawString(margen + 45 * mm, y, str(valor))
            y -= 7 * mm

        try:
            factor_16 = calificacion.factores.factor_16
        except Factores.DoesNotExist:  # pragma: no cover - defensivo
            factor_16 = CERO

        y -= 4 * mm
        documento.setFont('Helvetica-Bold', 10)
        documento.drawString(margen, y, 'Factor 16:')
        documento.setFont('Helvetica', 10)
        documento.drawString(margen + 45 * mm, y, f'{factor_16:.8f}')

        documento.setFont('Helvetica-Oblique', 8)
        documento.drawString(
            margen,
            15 * mm,
            'Certificado de ejemplo sin validez tributaria. Plataforma NUAM.',
        )

        documento.showPage()
        documento.save()

        calificacion.uri_documento = f'certificados/{nombre}'
        calificacion.save(update_fields=['uri_documento'])

        self.stdout.write(f'  PDF generado: {ruta}')
        return ruta

    # ------------------------------------------------------------------
    # CSV de muestra
    # ------------------------------------------------------------------

    def _crear_csv(self) -> None:
        carpeta = Path(settings.BASE_DIR) / 'sample_csv'
        carpeta.mkdir(parents=True, exist_ok=True)

        valida = [
            _fila_factores(
                2025,
                'ANDINA',
                '12-03-2025',
                100000901,
                'DIVIDENDO ANDINA 2025',
                '76000001-1',
                1,
                {
                    'factor_08': '0.50000000',
                    'factor_09': '0.25000000',
                    'factor_10': '0.25000000',
                },
            ),
            _fila_factores(
                2025,
                'SQM-B',
                '20-02-2025',
                100000902,
                'DIVIDENDO SQM-B 2025',
                '76000002-2',
                2,
                {'factor_16': '1.00000000'},
            ),
        ]

        montos = [
            [
                '2024',
                'ACCIONES',
                'CENCOSUD',
                '15-05-2024',
                '100000903',
                'DIVIDENDO CENCOSUD 2024',
                '76000003-3',
                '4',
                'A',
                '1500.50000000',
            ]
            + [
                '900.30000000' if numero == 8 else
                '600.20000000' if numero == 9 else
                '0.00000000'
                for numero in range(8, 38)
            ],
            [
                '2025',
                'ACCIONES',
                'NUAMTEST',
                '30-06-2025',
                '100000777',
                'PRUEBA BASE CERO',
                '77000111-2',
                '1',
                'A',
                '0.00000000',
            ]
            + [
                '100.00000000' if numero == 8 else
                '50.00000000' if numero == 9 else
                '0.00000000'
                for numero in range(8, 38)
            ],
        ]

        errores = [
            _fila_factores(
                2025,
                'ERRSUM',
                '15-03-2025',
                100000555,
                'ERROR SUMA FACTORES',
                '76999888-1',
                1,
                {
                    'factor_08': '0.70000000',
                    'factor_09': '0.50000000',
                },
            ),
            _fila_factores(
                2025,
                'ERRFEC',
                '45-13-2025',
                10000,
                'ERROR SECUENCIA Y FECHA',
                '76999888-2',
                2,
                {},
            ),
        ]

        archivos = {
            'carga_x_factor_valida.csv': (CABECERA_FACTORES, valida),
            'carga_x_monto_valida.csv': (CABECERA_MONTOS, montos),
            'carga_con_errores_validacion.csv': (CABECERA_FACTORES, errores),
        }

        for nombre, (cabecera, filas) in archivos.items():
            ruta = carpeta / nombre
            lineas = [';'.join(cabecera)]
            lineas.extend(';'.join(fila) for fila in filas)
            ruta.write_text('\n'.join(lineas) + '\n', encoding='utf-8')
            self.stdout.write(f'  CSV generado: {ruta}')
