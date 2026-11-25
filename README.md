# Mantenedor de Calificaciones Tributarias — Plataforma NUAM

Aplicación web desarrollada en Django para la administración de calificaciones
tributarias de instrumentos financieros, sus factores tributarios, certificados
PDF y declaraciones juradas. Incluye control de acceso basado en roles (RBAC),
carga masiva de datos desde archivos CSV y una bitácora de auditoría inmutable.

**Proyecto académico — Equipo G2 · INACAP**

---

## Descripción general

La Plataforma NUAM centraliza el mantenimiento de calificaciones tributarias por
ejercicio comercial. Permite registrar y consultar instrumentos (NEMO), fechas de
pago, secuencias de evento, números de dividendo y los 32 factores tributarios
`Factor-08` a `Factor-37` (incluyendo `Factor-19B` y `Factor-38`), todos
almacenados y desplegados con **8 decimales exactos**.

El sistema está pensado para tres perfiles: administración TI, corredores de
bolsa y auditoría/cumplimiento. Cada operación de escritura y cada consulta
histórica queda registrada en una bitácora que no puede modificarse ni
eliminarse.

### Funcionalidades principales

- **Mantenedor principal** con filtros por mercado, origen, periodo comercial y
  calificación pendiente.
- **CRUD completo** de calificaciones: ingresar, modificar, copiar y eliminar.
- **Certificados PDF** con validación de extensión, tamaño y cabecera binaria.
- **Carga masiva CSV** en dos modalidades: por factor y por monto, con
  previsualización, validación fila a fila y tolerancia de redondeo controlada.
- **Buscador histórico de Declaraciones Juradas (DDJJ)** por RUT, ejercicio e
  instrumento, con descarga del certificado PDF.
- **Vista de auditoría inmutable** con filtros, badges por acción y comparación
  lado a lado de los valores anterior y nuevo en formato JSON.
- **Seed idempotente** con usuarios, grupos, calificaciones, PDF y CSV de
  ejemplo.

---

## Stack tecnológico

| Componente | Versión / uso |
| --- | --- |
| Python | 3.11 o superior |
| Django | 5.2 |
| Base de datos | SQLite local (`db.sqlite3`) |
| Frontend | Bootstrap 5 vía CDN + JavaScript vanilla |
| Aritmética decimal | `decimal.Decimal` con `ROUND_HALF_UP` a 8 decimales |
| PDF | `reportlab` |
| Pruebas | `django.test` (`manage.py test`) |

---

## Requisitos previos

- [Git](https://git-scm.com/)
- [Python 3.11+](https://www.python.org/) con `pip`
- No se requiere Node.js ni un servidor de base de datos externo.

---

## Instalación y ejecución local

### 1. Clonar el repositorio

```bash
git clone https://github.com/axelCarrion/mantenedor-nuam.git
cd mantenedor-nuam
```

### 2. Crear y activar el entorno virtual

**Windows (PowerShell):**

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Instalar dependencias

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Aplicar migraciones

```bash
python manage.py migrate
```

### 5. Cargar datos de ejemplo

```bash
python manage.py seed_nuam
```

El comando es **idempotente**: puede ejecutarse varias veces sin duplicar
información. Crea los tres grupos RBAC, los usuarios de prueba, tres
calificaciones de ejemplo, su certificado PDF en `media/certificados/` y los
archivos CSV de muestra en `sample_csv/`.

### 6. Iniciar el servidor

```bash
python manage.py runserver
```

Abrir [http://127.0.0.1:8000/](http://127.0.0.1:8000/) e iniciar sesión con
alguno de los usuarios de prueba.

---

## Usuarios de prueba (sandbox)

| Usuario | Contraseña | Rol RBAC | Permisos |
| --- | --- | --- | --- |
| `admin_nuam` | `Nuam2026*` | Administrador TI NUAM | Acceso total, gestión de usuarios y consulta de la auditoría inmutable. |
| `corredor1` | `Nuam2026*` | Corredor de Bolsa | Lectura, creación, modificación, copia, eliminación, carga masiva CSV y carga/descarga de certificados PDF. |
| `auditor1` | `Nuam2026*` | Auditor / Cumplimiento | Solo lectura: mantenedor, historial DDJJ y descarga de PDF. Recibe HTTP 403 en rutas de escritura y en la vista de auditoría. |

> Los usuarios y contraseñas son exclusivamente para el entorno académico de
> demostración.

### Matriz de permisos

| Módulo / acción | Administrador TI | Corredor de Bolsa | Auditor / Cumplimiento |
| --- | :---: | :---: | :---: |
| Mantenedor y filtros | ✅ | ✅ | ✅ |
| Crear / modificar / copiar / eliminar | ✅ | ✅ | ❌ (403) |
| Carga masiva CSV | ✅ | ✅ | ❌ (403) |
| Descargar certificados PDF | ✅ | ✅ | ✅ |
| Historial DDJJ | ✅ | ✅ | ✅ |
| Auditoría inmutable | ✅ | ❌ (403) | ❌ (403) |

---

## Guía rápida de uso

### Mantenedor de calificaciones (`/`)

1. Iniciar sesión con `corredor1` o `admin_nuam`.
2. Usar los filtros **Mercado**, **Origen**, **Periodo Comercial** y
   **Calificación Pendiente** y presionar **BUSCAR**.
3. Seleccionar una fila con el botón de radio para habilitar **MODIFICAR**,
   **ELIMINAR** o **COPIAR**, o usar **INGRESAR** para crear un registro nuevo.
4. En el formulario se ingresan los datos de cabecera y los factores en una
   matriz de tres columnas. La suma de `Factor-08` a `Factor-37` no puede
   superar `1.00000000`.
5. **COPIAR** precarga todos los datos y sugiere `secuencia_evento + 1`, sin
   copiar el certificado PDF.

### Certificados PDF

- El archivo debe ser `.pdf`, pesar como máximo **10 MB** y comenzar con la
  cabecera binaria `%PDF`.
- Se guarda como `[RUT]_[Ejercicio]_[N°Dividendo].pdf` dentro de
  `media/certificados/`, sobrescribiendo el archivo previo del mismo nombre.
- Se puede visualizar o descargar desde el enlace **Ver PDF** del mantenedor o
  desde el botón **Descargar Certificado PDF** del historial DDJJ.

### Carga masiva CSV

Acceder desde la barra inferior del mantenedor (**CARGA X FACTOR** /
**CARGA X MONTO**) o directamente a:

- `/calificaciones/carga-masiva/?tipo=factor`
- `/calificaciones/carga-masiva/?tipo=monto`

Pasos:

1. Arrastrar el archivo `.csv` a la zona de carga o usar **Seleccionar
   Archivo**.
2. Presionar **PREVISUALIZAR / VALIDAR**.
3. Revisar el resumen y la grilla: cada fila muestra un badge **Válida** o
   **Error** con el motivo exacto.
4. Presionar **GRABAR** para importar únicamente las filas válidas. La operación
   se ejecuta en una transacción y registra un evento `CARGA_MASIVA` en la
   auditoría.

Archivos de ejemplo incluidos en `sample_csv/`:

| Archivo | Modalidad | Contenido |
| --- | --- | --- |
| `carga_x_factor_valida.csv` | `tipo=factor` | Dos filas válidas con factores `F8` a `F37`. |
| `carga_x_monto_valida.csv` | `tipo=monto` | Dos filas válidas: una con `VALOR_HISTORICO > 0` y otra con base calculada desde la suma de montos. |
| `carga_con_errores_validacion.csv` | `tipo=factor` | Dos filas inválidas para probar los mensajes de error. |

Los archivos también se pueden descargar desde el botón **VER FORMATO** de la
pantalla de carga masiva, que muestra la especificación completa de columnas.

#### Reglas de la carga masiva

- Delimitador `;`, codificación `utf-8-sig` o `latin-1`.
- Decimales con punto o coma; se cuantizan a 8 decimales con `ROUND_HALF_UP`.
- Cabecera obligatoria en las posiciones 0 a 8: `EJERCICIO`, `MERCADO`, `NEMO`,
  `FEC_PAGO` (`DD-MM-AAAA`), `SEC_EVE` (> 10000), `DESCRIPCION`,
  `RUT_CLIENTE`, `NUM_DIVIDENDO` y `TIPO_SOCIEDAD` (`A`/`C`).
- **Factor:** `F8` a `F32` obligatorios, `F33` a `F37` opcionales; cada factor
  en `[0, 1]` y suma `F8..F37 <= 1.00000000`.
- **Monto:** `B = VALOR_HISTORICO` si es positivo; si es `0`, `B` es la suma de
  los montos. Cada factor es `Monto / B` a 8 decimales.
- Si `B` proviene de la suma de montos y el exceso de redondeo es de hasta
  `0.00000005`, se ajusta automáticamente el factor mayor; sobre ese límite la
  fila se rechaza.
- Se rechazan duplicados dentro del archivo y contra la base de datos por
  `(ejercicio, mercado, instrumento, fecha_pago, secuencia_evento)`.

### Historial de Declaraciones Juradas (`/historial-ddjj/`)

1. Ingresar el **RUT Cliente** (por ejemplo `76555666-7`), el **Ejercicio**
   (por ejemplo `2023`) y, opcionalmente, el **Instrumento** (por ejemplo
   `FROWARD`).
2. Presionar **BUSCAR**.
3. La grilla muestra ejercicio, RUT, instrumento, fecha de pago, número de
   dividendo, secuencia, los 32 factores con 8 decimales y el botón
   **Descargar Certificado PDF** (o el indicador **Sin PDF adjunto**).
4. Cada búsqueda con parámetros genera automáticamente un evento
   `CONSULTA_HISTORICA` en la auditoría, con los filtros usados y la cantidad de
   resultados.

### Auditoría inmutable (`/auditoria/`)

Disponible únicamente para el rol **Administrador TI NUAM** (`admin_nuam`).

- Tabla paginada de la más reciente a la más antigua.
- Filtros por **Usuario**, **Acción** y rango de fechas **Desde** / **Hasta**.
- Acciones con badge: `CREAR`, `MODIFICAR`, `ELIMINAR`, `CARGA_MASIVA` y
  `CONSULTA_HISTORICA`.
- Botón **Ver Cambios (JSON)** que abre un modal Bootstrap con la comparación
  lado a lado de `valor_anterior` y `valor_nuevo`, indentada para su lectura.

Los registros de auditoría son inmutables a nivel de modelo: no pueden
modificarse ni eliminarse.

---

## Estructura del proyecto

```text
mantenedor-nuam/
├── manage.py
├── requirements.txt
├── README.md
├── nuam_project/
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── calificaciones/
│   ├── models.py                 # Calificacion, Factores y RegistroAuditoria
│   ├── views.py                  # Dashboard, CRUD, PDF, carga masiva, DDJJ, auditoría
│   ├── forms.py                  # Formularios Bootstrap y validación de PDF
│   ├── permissions.py            # Grupos RBAC y decoradores de acceso
│   ├── auditoria.py              # Serialización y registro de auditoría
│   ├── context_processors.py     # Rol activo disponible en templates
│   ├── services/
│   │   └── csv_loader.py         # Motor de carga masiva y conversión de montos
│   ├── management/commands/
│   │   └── seed_nuam.py          # Datos de ejemplo idempotentes
│   ├── templates/calificaciones/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── calificacion_form.html
│   │   ├── calificacion_confirmar_eliminar.html
│   │   ├── carga_masiva.html
│   │   ├── historial_ddjj.html
│   │   ├── auditoria.html
│   │   └── login.html
│   ├── migrations/
│   └── tests.py
├── sample_csv/
│   ├── carga_x_factor_valida.csv
│   ├── carga_x_monto_valida.csv
│   └── carga_con_errores_validacion.csv
└── media/
    └── certificados/             # PDFs generados/subidos (no versionados)
```

---

## Pruebas automatizadas

Ejecutar la suite completa desde la raíz del proyecto:

```bash
python manage.py test
```

En Windows con el entorno virtual:

```powershell
venv\Scripts\python.exe manage.py test
```

La suite cubre, entre otros:

- Login/logout y control de acceso por rol (incluidos los HTTP 403).
- Filtros del mantenedor y renderizado de la grilla.
- CRUD completo con validación de certificados PDF.
- Registro de auditoría para crear, modificar, eliminar y carga masiva.
- Procesamiento CSV por factor y por monto, casos límite de tolerancia de
  redondeo, duplicados y errores de cabecera.
- Búsqueda histórica DDJJ con generación de `CONSULTA_HISTORICA`.
- Vista de auditoría con filtros y comparación JSON.

---

## Notas técnicas

- La base de datos local es `db.sqlite3` y está excluida del repositorio.
- Los certificados PDF y los archivos temporales de carga masiva están
  excluidos mediante `.gitignore`; los CSV de `sample_csv/` sí se versionan.
- Todos los factores y montos se normalizan a 8 decimales con
  `ROUND_HALF_UP` para evitar diferencias de punto flotante.
- `Factor-19B` y `Factor-38` se muestran y almacenan, pero no participan de la
  suma de control `F8..F37`.
- Este proyecto es una demostración académica; para un entorno productivo se
  deben configurar `DEBUG=False`, una `SECRET_KEY` externa, `ALLOWED_HOSTS` y
  una base de datos robusta.
