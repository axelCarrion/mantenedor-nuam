"""Pruebas de RBAC, login y del comando idempotente ``seed_nuam``."""

import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Calificacion, Factores, formatear
from .permissions import GRUPO_ADMIN, GRUPO_AUDITOR, GRUPO_CORREDOR

Usuario = get_user_model()

RUTAS_ESCRITURA = [
    ('calificaciones:calificacion_crear', None),
    ('calificaciones:calificacion_editar', (1,)),
    ('calificaciones:calificacion_eliminar', (1,)),
    ('calificaciones:carga_masiva', None),
]


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
        self.assertContains(respuesta, 'bootstrap')

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


class AuditorSoloLecturaTest(BaseRBACTest):
    def test_puede_ver_panel_e_historial(self):
        self.client.force_login(self.auditor)
        self.assertEqual(
            self.client.get(reverse('calificaciones:dashboard')).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse('calificaciones:historial_ddjj')).status_code,
            200,
        )

    def test_recibe_403_en_rutas_de_escritura(self):
        for nombre, args in RUTAS_ESCRITURA:
            with self.subTest(ruta=nombre):
                self.assert_403(self.auditor, nombre, args)

    def test_recibe_403_en_auditoria(self):
        self.assert_403(self.auditor, 'calificaciones:auditoria')


class CorredorTest(BaseRBACTest):
    def test_puede_acceder_a_escritura(self):
        self.client.force_login(self.corredor)
        for nombre, args in RUTAS_ESCRITURA:
            with self.subTest(ruta=nombre):
                respuesta = self.client.get(reverse(nombre, args=args or []))
                self.assertEqual(respuesta.status_code, 200)

    def test_no_puede_ver_auditoria(self):
        self.assert_403(self.corredor, 'calificaciones:auditoria')


class AdministradorTest(BaseRBACTest):
    def test_acceso_total(self):
        self.client.force_login(self.admin)
        for nombre, args in RUTAS_ESCRITURA:
            with self.subTest(ruta=nombre):
                respuesta = self.client.get(reverse(nombre, args=args or []))
                self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            self.client.get(reverse('calificaciones:auditoria')).status_code, 200
        )


class AnonimoTest(BaseRBACTest):
    def test_redirige_al_login_con_next(self):
        respuesta = self.client.get(reverse('calificaciones:calificacion_crear'))
        self.assertRedirects(
            respuesta,
            f'{reverse("login")}?next={reverse("calificaciones:calificacion_crear")}',
        )


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
                self.assertEqual(
                    formatear(froward.factores.factor_08), '0.00000000'
                )

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
