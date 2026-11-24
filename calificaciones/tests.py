"""Pruebas de RBAC, buscador, CRUD, PDF y auditoría."""

import tempfile
from datetime import date
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Calificacion, Factores, RegistroAuditoria, formatear
from .permissions import GRUPO_ADMIN, GRUPO_AUDITOR, GRUPO_CORREDOR

Usuario = get_user_model()
PDF_VALIDO = b'%PDF-1.4\n% certificado de prueba\n%%EOF\n'


def crear_calificacion(**overrides):
    """Crea una calificación con sus factores usando datos por defecto."""
    datos = {
        'ejercicio': 2025,
        'mercado': Calificacion.MERCADO_ACCIONES,
        'instrumento': 'TEST',
        'descripcion': 'REGISTRO DE PRUEBA',
        'fecha_pago': date(2025, 6, 30),
        'secuencia_evento': 100000123,
        'numero_dividendo': 1,
        'tipo_sociedad': 'A',
        'rut_cliente': '11111111-1',
        'origen': Calificacion.ORIGEN_CORREDOR,
    }
    datos.update(overrides)
    calificacion = Calificacion.objects.create(**datos)
    Factores.objects.create(calificacion=calificacion)
    return calificacion


class BaseRBACTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        grupos = {
            nombre: Group.objects.create(name=nombre)
            for nombre in (GRUPO_ADMIN, GRUPO_CORREDOR, GRUPO_AUDITOR)
        }
        cls.admin = Usuario.objects.create_superuser(
            username='admin_test', password='clave-test'
        )
        cls.corredor = Usuario.objects.create_user(
            username='corredor_test', password='clave-test'
        )
        cls.corredor.groups.add(grupos[GRUPO_CORREDOR])
        cls.auditor = Usuario.objects.create_user(
            username='auditor_test', password='clave-test'
        )
        cls.auditor.groups.add(grupos[GRUPO_AUDITOR])
        cls.calificacion = crear_calificacion()

    def rutas_escritura(self):
        pk = self.calificacion.pk
        return [
            ('calificaciones:calificacion_crear', None),
            ('calificaciones:calificacion_editar', (pk,)),
            ('calificaciones:calificacion_copiar', (pk,)),
            ('calificaciones:calificacion_eliminar', (pk,)),
            ('calificaciones:carga_masiva', None),
        ]

    def assert_403(self, usuario, nombre_ruta, args=None):
        self.client.force_login(usuario)
        respuesta = self.client.get(reverse(nombre_ruta, args=args or []))
        self.assertEqual(respuesta.status_code, 403)


class LoginTest(BaseRBACTest):
    def test_login_renderiza_formulario_y_credenciales(self):
        respuesta = self.client.get(reverse('login'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'admin_nuam')
        self.assertContains(respuesta, 'corredor1')
        self.assertContains(respuesta, 'auditor1')
        self.assertContains(respuesta, 'Nuam2026*')

    def test_login_redirige_al_panel(self):
        respuesta = self.client.post(
            reverse('login'),
            {'username': 'auditor_test', 'password': 'clave-test'},
        )
        self.assertRedirects(respuesta, reverse('calificaciones:dashboard'))

    def test_logout_via_post(self):
        self.client.force_login(self.auditor)
        respuesta = self.client.post(reverse('logout'))
        self.assertRedirects(respuesta, reverse('login'))


class RBACTest(BaseRBACTest):
    def test_auditor_solo_lectura(self):
        self.client.force_login(self.auditor)
        self.assertEqual(
            self.client.get(reverse('calificaciones:dashboard')).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse('calificaciones:historial_ddjj')).status_code,
            200,
        )
        for nombre, args in self.rutas_escritura():
            with self.subTest(ruta=nombre):
                self.assert_403(self.auditor, nombre, args)
        self.assert_403(self.auditor, 'calificaciones:auditoria')

    def test_corredor_puede_escribir_pero_no_auditar(self):
        self.client.force_login(self.corredor)
        for nombre, args in self.rutas_escritura():
            with self.subTest(ruta=nombre):
                respuesta = self.client.get(reverse(nombre, args=args or []))
                self.assertEqual(respuesta.status_code, 200)
        self.assert_403(self.corredor, 'calificaciones:auditoria')

    def test_administrador_acceso_total(self):
        self.client.force_login(self.admin)
        for nombre, args in self.rutas_escritura():
            with self.subTest(ruta=nombre):
                respuesta = self.client.get(reverse(nombre, args=args or []))
                self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            self.client.get(reverse('calificaciones:auditoria')).status_code, 200
        )

    def test_anonimo_redirige_al_login(self):
        respuesta = self.client.get(reverse('calificaciones:calificacion_crear'))
        self.assertRedirects(
            respuesta,
            f'{reverse("login")}?next={reverse("calificaciones:calificacion_crear")}',
        )


class DashboardTest(BaseRBACTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        crear_calificacion(
            instrumento='DEUDAUNO',
            mercado=Calificacion.MERCADO_DEUDA,
            origen=Calificacion.ORIGEN_SISTEMA,
            ejercicio=2024,
            secuencia_evento=100000200,
        )
        crear_calificacion(
            instrumento='PENDIENTE',
            ejercicio=2023,
            origen=Calificacion.ORIGEN_SISTEMA,
            calificacion_pendiente=True,
            secuencia_evento=100000300,
        )

    def setUp(self):
        self.client.force_login(self.auditor)

    def instrumentos(self, respuesta):
        return {
            fila.instrumento for fila in respuesta.context['page_obj']
        }

    def test_muestra_grilla_y_columnas_de_factores(self):
        respuesta = self.client.get(reverse('calificaciones:dashboard'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Factor-08')
        self.assertContains(respuesta, 'Factor-19B')
        self.assertContains(respuesta, 'Factor-38')
        self.assertContains(respuesta, 'Elementos mostrados')
        self.assertEqual(len(respuesta.context['columnas_factores']), 32)

    def test_filtro_por_mercado(self):
        respuesta = self.client.get(
            reverse('calificaciones:dashboard'), {'mercado': 'DEUDA'}
        )
        self.assertEqual(self.instrumentos(respuesta), {'DEUDAUNO'})

    def test_filtro_por_origen(self):
        respuesta = self.client.get(
            reverse('calificaciones:dashboard'), {'origen': 'SISTEMA'}
        )
        self.assertEqual(self.instrumentos(respuesta), {'DEUDAUNO', 'PENDIENTE'})

    def test_filtro_por_periodo(self):
        respuesta = self.client.get(
            reverse('calificaciones:dashboard'), {'periodo': '2025'}
        )
        self.assertEqual(self.instrumentos(respuesta), {'TEST'})

    def test_filtro_pendiente(self):
        respuesta = self.client.get(
            reverse('calificaciones:dashboard'), {'pendiente': 'on'}
        )
        self.assertEqual(self.instrumentos(respuesta), {'PENDIENTE'})

    def test_sin_resultados(self):
        respuesta = self.client.get(
            reverse('calificaciones:dashboard'),
            {'mercado': 'DEUDA', 'periodo': '2025'},
        )
        self.assertEqual(self.instrumentos(respuesta), set())
        self.assertContains(respuesta, 'No hay registros.')

    def test_auditor_no_ve_botones_de_escritura(self):
        respuesta = self.client.get(reverse('calificaciones:dashboard'))
        self.assertNotContains(respuesta, 'data-accion="editar"')
        self.assertContains(respuesta, 'solo lectura')


class CRUDBaseTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.TemporaryDirectory()
        cls._override = override_settings(MEDIA_ROOT=Path(cls._media.name))
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        cls._media.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.grupo = Group.objects.create(name=GRUPO_CORREDOR)
        self.usuario = Usuario.objects.create_user(
            username='corredor_crud', password='clave-test'
        )
        self.usuario.groups.add(self.grupo)
        self.client.force_login(self.usuario)

    def datos_formulario(self, **overrides):
        datos = {
            'ejercicio': 2025,
            'mercado': 'ACCIONES',
            'instrumento': 'NUEVO',
            'descripcion': 'REGISTRO CREADO EN PRUEBA',
            'fecha_pago': '2025-07-01',
            'secuencia_evento': 100001234,
            'numero_dividendo': 1,
            'tipo_sociedad': 'A',
            'valor_historico': '0.00000000',
            'factor_actualizacion': '1.00000000',
            'evento_capital': 0,
            'origen': 'CORREDOR',
            'rut_cliente': '11111111-1',
            'factores-factor_08': '0.50000000',
            'factores-factor_09': '0.25000000',
            'factores-factor_10': '0.25000000',
            'factores-factor_19b': '0.00000000',
            'factores-factor_38': '0.00000000',
        }
        for numero in range(11, 38):
            datos[f'factores-factor_{numero:02d}'] = '0.00000000'
        datos.update(overrides)
        return datos


class CrearCalificacionTest(CRUDBaseTest):
    def test_crear_con_factores_y_auditoria(self):
        respuesta = self.client.post(
            reverse('calificaciones:calificacion_crear'),
            self.datos_formulario(),
        )
        self.assertRedirects(respuesta, reverse('calificaciones:dashboard'))

        calificacion = Calificacion.objects.get(instrumento='NUEVO')
        self.assertEqual(calificacion.creado_por, self.usuario)
        self.assertEqual(formatear(calificacion.factores.factor_08), '0.50000000')
        self.assertEqual(
            formatear(calificacion.factores.total_suma_control), '1.00000000'
        )

        auditoria = RegistroAuditoria.objects.get(accion='CREAR')
        self.assertEqual(auditoria.calificacion_id_ref, calificacion.pk)
        self.assertEqual(auditoria.usuario, self.usuario)
        self.assertEqual(auditoria.valor_nuevo['instrumento'], 'NUEVO')
        self.assertEqual(auditoria.valor_nuevo['fecha_pago'], '2025-07-01')
        self.assertEqual(
            auditoria.valor_nuevo['factores']['factor_08'], '0.50000000'
        )

    def test_crear_con_pdf_valido_lo_renombra(self):
        archivo = SimpleUploadedFile(
            'certificado.pdf', PDF_VALIDO, content_type='application/pdf'
        )
        respuesta = self.client.post(
            reverse('calificaciones:calificacion_crear'),
            self.datos_formulario(certificado_pdf=archivo),
        )
        self.assertRedirects(respuesta, reverse('calificaciones:dashboard'))

        calificacion = Calificacion.objects.get(instrumento='NUEVO')
        self.assertEqual(
            calificacion.uri_documento.name,
            'certificados/11111111-1_2025_1.pdf',
        )
        ruta = Path(calificacion.uri_documento.path)
        self.assertTrue(ruta.exists())
        self.assertTrue(ruta.read_bytes().startswith(b'%PDF'))

    def test_rechaza_pdf_con_extension_invalida(self):
        archivo = SimpleUploadedFile(
            'certificado.txt', PDF_VALIDO, content_type='text/plain'
        )
        respuesta = self.client.post(
            reverse('calificaciones:calificacion_crear'),
            self.datos_formulario(certificado_pdf=archivo),
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'extensión .pdf')
        self.assertFalse(Calificacion.objects.filter(instrumento='NUEVO').exists())

    def test_rechaza_pdf_sin_cabecera(self):
        archivo = SimpleUploadedFile(
            'certificado.pdf', b'NOESUNPDF', content_type='application/pdf'
        )
        respuesta = self.client.post(
            reverse('calificaciones:calificacion_crear'),
            self.datos_formulario(certificado_pdf=archivo),
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'cabecera binaria %PDF')
        self.assertFalse(Calificacion.objects.filter(instrumento='NUEVO').exists())

    def test_rechaza_pdf_mayor_a_10_mb(self):
        contenido = b'%PDF' + b'0' * (10 * 1024 * 1024)
        archivo = SimpleUploadedFile(
            'certificado.pdf', contenido, content_type='application/pdf'
        )
        respuesta = self.client.post(
            reverse('calificaciones:calificacion_crear'),
            self.datos_formulario(certificado_pdf=archivo),
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '10 MB')
        self.assertFalse(Calificacion.objects.filter(instrumento='NUEVO').exists())

    def test_rechaza_suma_de_factores_mayor_a_uno(self):
        respuesta = self.client.post(
            reverse('calificaciones:calificacion_crear'),
            self.datos_formulario(**{
                'factores-factor_08': '0.70000000',
                'factores-factor_09': '0.50000000',
            }),
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'no puede superar 1.00000000')
        self.assertFalse(Calificacion.objects.filter(instrumento='NUEVO').exists())


class EditarCalificacionTest(CRUDBaseTest):
    def test_editar_y_auditar_cambios(self):
        calificacion = crear_calificacion(instrumento='EDITABLE')
        url = reverse('calificaciones:calificacion_editar', args=[calificacion.pk])

        respuesta = self.client.post(
            url,
            self.datos_formulario(
                instrumento='EDITABLE',
                descripcion='DESCRIPCION ACTUALIZADA',
                fecha_pago='2025-06-30',
                secuencia_evento=100000123,
            ),
        )
        self.assertRedirects(respuesta, reverse('calificaciones:dashboard'))

        calificacion.refresh_from_db()
        self.assertEqual(calificacion.descripcion, 'DESCRIPCION ACTUALIZADA')

        auditoria = RegistroAuditoria.objects.get(accion='MODIFICAR')
        self.assertEqual(auditoria.calificacion_id_ref, calificacion.pk)
        self.assertEqual(
            auditoria.valor_anterior['descripcion'], 'REGISTRO DE PRUEBA'
        )
        self.assertEqual(
            auditoria.valor_nuevo['descripcion'], 'DESCRIPCION ACTUALIZADA'
        )


class CopiarCalificacionTest(CRUDBaseTest):
    def test_copiar_precarga_secuencia_incrementada(self):
        original = crear_calificacion(instrumento='ORIGEN')
        respuesta = self.client.get(
            reverse('calificaciones:calificacion_copiar', args=[original.pk])
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta.context['form'].initial['secuencia_evento'],
            original.secuencia_evento + 1,
        )
        self.assertNotIn('certificado_pdf', respuesta.context['form'].initial)

    def test_copiar_crea_registro_sin_pdf(self):
        original = crear_calificacion(instrumento='ORIGEN')
        respuesta = self.client.post(
            reverse('calificaciones:calificacion_copiar', args=[original.pk]),
            self.datos_formulario(
                instrumento='COPIA',
                secuencia_evento=original.secuencia_evento + 1,
            ),
        )
        self.assertRedirects(respuesta, reverse('calificaciones:dashboard'))

        copia = Calificacion.objects.get(instrumento='COPIA')
        self.assertFalse(copia.uri_documento)
        self.assertEqual(copia.creado_por, self.usuario)
        self.assertEqual(Calificacion.objects.count(), 2)
        self.assertTrue(
            RegistroAuditoria.objects.filter(
                accion='CREAR', calificacion_id_ref=copia.pk
            ).exists()
        )


class EliminarCalificacionTest(CRUDBaseTest):
    def test_confirmacion_y_eliminacion_con_auditoria(self):
        calificacion = crear_calificacion(instrumento='BORRAR')
        url = reverse('calificaciones:calificacion_eliminar', args=[calificacion.pk])

        confirmacion = self.client.get(url)
        self.assertEqual(confirmacion.status_code, 200)
        self.assertContains(confirmacion, 'Confirmar eliminación')

        respuesta = self.client.post(url)
        self.assertRedirects(respuesta, reverse('calificaciones:dashboard'))
        self.assertFalse(Calificacion.objects.filter(pk=calificacion.pk).exists())

        auditoria = RegistroAuditoria.objects.get(accion='ELIMINAR')
        self.assertEqual(auditoria.calificacion_id_ref, calificacion.pk)
        self.assertEqual(auditoria.valor_anterior['instrumento'], 'BORRAR')


class PDFTest(CRUDBaseTest):
    def test_descarga_pdf_existente(self):
        calificacion = crear_calificacion(instrumento='CONPDF')
        calificacion.uri_documento.save(
            'prueba.pdf',
            ContentFile(PDF_VALIDO),
            save=True,
        )
        respuesta = self.client.get(
            reverse('calificaciones:calificacion_pdf', args=[calificacion.pk])
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertTrue(b''.join(respuesta.streaming_content).startswith(b'%PDF'))
        respuesta.close()

    def test_pdf_inexistente_devuelve_404(self):
        calificacion = crear_calificacion(instrumento='SINPDF')
        respuesta = self.client.get(
            reverse('calificaciones:calificacion_pdf', args=[calificacion.pk])
        )
        self.assertEqual(respuesta.status_code, 404)

    def test_auditor_puede_descargar_pdf(self):
        calificacion = crear_calificacion(instrumento='AUDITORPDF')
        calificacion.uri_documento.save(
            'auditor.pdf', ContentFile(PDF_VALIDO), save=True
        )
        auditor = Usuario.objects.create_user(
            username='auditor_pdf', password='clave-test'
        )
        auditor.groups.add(Group.objects.create(name=GRUPO_AUDITOR))
        self.client.force_login(auditor)

        respuesta = self.client.get(
            reverse('calificaciones:calificacion_pdf', args=[calificacion.pk])
        )
        self.assertEqual(respuesta.status_code, 200)
        respuesta.close()


class SeedNuamTest(TestCase):
    def test_seed_es_idempotente_y_genera_artefactos(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with override_settings(MEDIA_ROOT=base / 'media', BASE_DIR=base):
                salida = StringIO()
                call_command('seed_nuam', stdout=salida)
                call_command('seed_nuam', stdout=salida)

                self.assertEqual(Calificacion.objects.count(), 3)
                self.assertEqual(Factores.objects.count(), 3)
                self.assertEqual(
                    Group.objects.filter(
                        name__in=[GRUPO_ADMIN, GRUPO_CORREDOR, GRUPO_AUDITOR]
                    ).count(),
                    3,
                )
                self.assertEqual(
                    Usuario.objects.filter(
                        username__in=['admin_nuam', 'corredor1', 'auditor1']
                    ).count(),
                    3,
                )
                self.assertTrue(
                    Usuario.objects.get(username='admin_nuam').check_password(
                        'Nuam2026*'
                    )
                )

                froward = Calificacion.objects.get(instrumento='FROWARD')
                self.assertEqual(formatear(froward.factores.factor_16), '1.00000000')
                self.assertEqual(formatear(froward.factores.factor_08), '0.00000000')

                pdf = base / 'media' / 'certificados' / '76555666-7_2023_3.pdf'
                self.assertTrue(pdf.exists())
                self.assertTrue(pdf.read_bytes().startswith(b'%PDF'))
                self.assertEqual(
                    froward.uri_documento.name,
                    'certificados/76555666-7_2023_3.pdf',
                )

                csv_factores = base / 'sample_csv' / 'carga_x_factor_valida.csv'
                csv_montos = base / 'sample_csv' / 'carga_x_monto_valida.csv'
                csv_errores = base / 'sample_csv' / 'carga_con_errores_validacion.csv'
                for archivo in (csv_factores, csv_montos, csv_errores):
                    self.assertTrue(archivo.exists(), archivo)

                lineas = csv_factores.read_text(encoding='utf-8').splitlines()
                self.assertEqual(len(lineas), 3)
                self.assertEqual(len(lineas[0].split(';')), 39)
                self.assertEqual(lineas[0].split(';')[0], 'EJERCICIO')
                self.assertEqual(lineas[0].split(';')[9], 'F8')
                self.assertEqual(lineas[0].split(';')[-1], 'F37')
                self.assertNotIn('0E-8', lineas[1])

                lineas_montos = csv_montos.read_text(encoding='utf-8').splitlines()
                self.assertEqual(len(lineas_montos[0].split(';')), 40)
                self.assertEqual(lineas_montos[0].split(';')[9], 'VALOR_HISTORICO')
                self.assertEqual(lineas_montos[0].split(';')[10], 'M8')
