# Documentación Completa — ProFit Studio

> Guía técnica detallada de cómo está construida la plataforma, qué hace cada parte del código, y cómo operar el sistema.

---

## Tabla de contenidos

1. [Resumen del proyecto](#1-resumen-del-proyecto)
2. [Tecnologías utilizadas](#2-tecnologías-utilizadas)
3. [Estructura de carpetas](#3-estructura-de-carpetas)
4. [Cómo funciona Django (base)](#4-cómo-funciona-django-base)
5. [App: accounts — Usuarios y autenticación](#5-app-accounts--usuarios-y-autenticación)
6. [App: assessments — Valoraciones físicas](#6-app-assessments--valoraciones-físicas)
7. [App: exercises — Biblioteca de ejercicios](#7-app-exercises--biblioteca-de-ejercicios)
8. [App: routines — Rutinas de entrenamiento](#8-app-routines--rutinas-de-entrenamiento)
9. [App: memberships — Membresías](#9-app-memberships--membresías)
10. [App: payments — Pagos con Wompi](#10-app-payments--pagos-con-wompi)
11. [App: public — Páginas públicas](#11-app-public--páginas-públicas)
12. [Plantillas (Templates) y diseño visual](#12-plantillas-templates-y-diseño-visual)
13. [Configuración del sistema](#13-configuración-del-sistema)
14. [Cómo agregar videos de entrenamiento](#14-cómo-agregar-videos-de-entrenamiento)
15. [Cómo agregar la foto de la entrenadora](#15-cómo-agregar-la-foto-de-la-entrenadora)
16. [Cómo pasar a producción en Railway](#16-cómo-pasar-a-producción-en-railway)
17. [Variables de entorno — referencia completa](#17-variables-de-entorno--referencia-completa)
18. [Comandos útiles del día a día](#18-comandos-útiles-del-día-a-día)

---

## 1. Resumen del proyecto

**ProFit Studio** es una plataforma web completa para un negocio de entrenamiento personal. Permite que una entrenadora gestione sus clientes desde una sola aplicación:

- **Registro y login** de clientes con roles separados (entrenadora vs miembro)
- **Valoraciones físicas** iniciales con cálculo automático de IMC y Test Dickson
- **Rutinas de entrenamiento** personalizadas por cliente, con días y ejercicios
- **Biblioteca de ejercicios** con videos, imágenes y descripción de músculos
- **Membresías** mensuales, trimestrales o semestrales con control de vencimiento
- **Pagos en línea** integrados con Wompi (pasarela colombiana)
- **Panel de administración** web para gestionar todo sin tocar código

El sistema tiene dos tipos de usuarios:
- **Entrenadora**: puede agregar clientes, crear valoraciones, construir rutinas, gestionar membresías
- **Miembro (cliente)**: puede ver su rutina asignada, su dashboard personal y sus datos

---

## 2. Tecnologías utilizadas

### Backend (servidor)

| Tecnología | Versión | Para qué sirve |
|------------|---------|----------------|
| **Python** | 3.11+ | Lenguaje de programación principal |
| **Django** | 5.0.6 | Framework web — maneja URLs, base de datos, plantillas, autenticación |
| **Gunicorn** | 22.0.0 | Servidor WSGI para producción (el que "sirve" la web) |
| **PostgreSQL** | latest | Base de datos en producción (más robusta que SQLite) |
| **SQLite** | built-in | Base de datos en desarrollo local (un solo archivo) |
| **Celery** | 5.3.6 | Tareas en segundo plano (envío de correos, procesos largos) |
| **Redis** | 5.0.7 | Cache y mensajería para Celery (solo en producción) |

### Frontend (navegador del usuario)

| Tecnología | Para qué sirve |
|------------|----------------|
| **Tailwind CSS** (CDN) | Estilos visuales — colores, espaciado, tipografía, responsive |
| **Alpine.js** (CDN) | Interactividad sin recargar página — cálculos en tiempo real, validaciones |
| **Font Awesome** (CDN) | Íconos (lupa, check, usuario, etc.) |
| **HTMX** | Solicitudes parciales sin recargar la página completa |

> **Nota:** Tailwind, Alpine y Font Awesome se cargan desde CDN (internet). No hay archivos CSS/JS locales que compilar.

### Servicios externos (producción)

| Servicio | Para qué sirve |
|----------|----------------|
| **Railway.app** | Hosting del servidor Django y PostgreSQL |
| **Cloudflare R2** | Almacenamiento de archivos: videos, fotos, imágenes |
| **Wompi** | Pasarela de pagos colombiana (tarjeta crédito/débito, PSE, etc.) |

---

## 3. Estructura de carpetas

```
kit-web-scrolling/               ← Carpeta raíz del proyecto
│
├── manage.py                    ← Comando principal de Django (nunca modificar)
├── .env                         ← Variables secretas (NO subir a git, NO compartir)
├── .env.example                 ← Plantilla de variables (sí subir a git)
├── .gitignore                   ← Lista de archivos que git ignora
├── Procfile                     ← Define cómo iniciar el servidor en Railway/Heroku
├── railway.toml                 ← Configuración de despliegue en Railway
├── requirements/                ← Dependencias de Python
│   ├── base.txt                 ← Dependencias compartidas (todos los entornos)
│   ├── development.txt          ← Desarrollo: agrega debug toolbar
│   ├── local.txt                ← Local sin PostgreSQL ni Redis
│   └── production.txt           ← Producción (igual a base.txt)
│
├── config/                      ← Configuración central de Django
│   ├── urls.py                  ← Enrutador principal — conecta las apps
│   ├── settings/
│   │   ├── base.py              ← Configuración compartida (apps, middleware, etc.)
│   │   ├── development.py       ← Sobreescribe para desarrollo (SQLite, debug=True)
│   │   └── production.py        ← Sobreescribe para producción (PostgreSQL, R2, SSL)
│   ├── wsgi.py                  ← Punto de entrada para el servidor web
│   └── celery.py                ← Configuración de tareas en segundo plano
│
├── apps/                        ← Todas las aplicaciones del proyecto
│   ├── accounts/                ← Usuarios, login, registro, perfiles
│   ├── assessments/             ← Valoraciones físicas (IMC + Test Dickson)
│   ├── exercises/               ← Biblioteca de ejercicios con videos
│   ├── routines/                ← Rutinas de entrenamiento personalizadas
│   ├── memberships/             ← Planes y membresías activas
│   ├── payments/                ← Integración con Wompi
│   └── public/                  ← Páginas públicas (inicio, servicios, contacto)
│
├── templates/                   ← Archivos HTML de todas las páginas
│   ├── base.html                ← Layout base del que heredan las demás páginas
│   ├── accounts/                ← Login, registro, dashboard del miembro
│   ├── trainer/                 ← Panel completo de la entrenadora
│   ├── public/                  ← Páginas públicas (inicio, planes, contacto)
│   ├── exercises/               ← Detalle de ejercicio
│   ├── routines/                ← Vista de rutina para el miembro
│   ├── payments/                ← Checkout y confirmación de pago
│   └── partials/                ← Fragmentos reutilizables (navbar, footer)
│
├── static/                      ← Archivos estáticos propios (CSS/JS/imágenes fijas)
│   ├── css/
│   ├── js/
│   └── img/
│
└── .venv/                       ← Entorno virtual Python (no subir a git)
```

---

## 4. Cómo funciona Django (base)

Django sigue el patrón **MTV: Model – Template – View**.

```
Usuario hace clic / abre URL
        ↓
   config/urls.py             ← "¿Qué app maneja esta URL?"
        ↓
   apps/XXX/urls.py           ← "¿Qué vista concreta responde?"
        ↓
   apps/XXX/views.py          ← Lógica: lee la base de datos, procesa datos
        ↓
   apps/XXX/models.py         ← Estructura de la base de datos
        ↓
   templates/XXX/pagina.html  ← El HTML que ve el usuario
```

### ¿Qué es un Model?

Un **Model** es una clase Python que define una tabla en la base de datos. Cada atributo de la clase es una columna.

```python
class DixonTest(models.Model):
    p0 = models.PositiveIntegerField()   # columna "p0" — entero positivo
    p1 = models.PositiveIntegerField()   # columna "p1"
    p2 = models.PositiveIntegerField()   # columna "p2"
    index_value = models.DecimalField()  # columna "index_value" — decimal
```

### ¿Qué es una View?

Una **View** es una función Python que recibe la solicitud del usuario y devuelve una respuesta (normalmente HTML). Aquí va toda la lógica: leer de la base de datos, validar formularios, guardar datos.

### ¿Qué es una Template?

Una **Template** es un archivo HTML con etiquetas especiales de Django (`{{ variable }}`, `{% if %}`, `{% for %}`). Django rellena los huecos con datos reales antes de enviárselo al navegador.

### ¿Qué son las Migraciones?

Cada vez que cambias un modelo (agregas un campo, lo renombras), debes crear una **migración** — un archivo Python que le dice a Django cómo actualizar la base de datos.

```bash
python manage.py makemigrations   # crea el archivo de migración
python manage.py migrate          # aplica los cambios a la base de datos
```

---

## 5. App: accounts — Usuarios y autenticación

**Carpeta:** `apps/accounts/`

Esta app gestiona todo lo relacionado con usuarios: registro, login, perfiles, roles.

### Modelo: User (`apps/accounts/models.py`)

El modelo `User` extiende el usuario estándar de Django (`AbstractUser`) y agrega campos propios:

```python
class User(AbstractUser):
    # Roles del sistema
    ROLE_TRAINER = 'trainer'  # La entrenadora
    ROLE_MEMBER  = 'member'   # Los clientes

    role   = CharField(choices=ROLE_CHOICES)     # 'trainer' o 'member'
    gender = CharField(choices=GENDER_CHOICES)   # 'F', 'M', u 'O'
    phone  = CharField()                          # Teléfono de contacto
    profile_photo = ImageField(upload_to='profiles/')  # Foto de perfil
    interested_plan = ForeignKey(MembershipPlan) # Plan que le interesa al registrarse
    created_at = DateTimeField(auto_now_add=True) # Fecha de registro
```

**`AbstractUser`** ya incluye: `username`, `first_name`, `last_name`, `email`, `password`, `is_active`, `is_staff`, `last_login`, `date_joined`.

**Propiedades calculadas:**
- `user.is_trainer` → `True` si el rol es 'trainer'
- `user.has_active_membership` → `True` si tiene membresía vigente

### Vistas principales (`apps/accounts/views.py`)

| Función | URL | Qué hace |
|---------|-----|----------|
| `login_view` | `/accounts/login/` | Procesa el formulario de login con django-axes |
| `register_view` | `/accounts/register/` | Crea un nuevo usuario con rol 'member' |
| `dashboard_redirect` | `/accounts/dashboard/` | Redirige según el rol del usuario |
| `trainer_dashboard` | `/accounts/trainer/` | Panel principal de la entrenadora |
| `trainer_clients` | `/accounts/trainer/clients/` | Lista todos los clientes |
| `trainer_add_client` | `/accounts/trainer/clients/add/` | Formulario para crear un cliente |
| `trainer_client_detail` | `/accounts/trainer/clients/<id>/` | Detalle de un cliente específico |
| `trainer_client_edit` | `/accounts/trainer/clients/<id>/edit/` | Editar datos del cliente |
| `member_profile_edit` | `/accounts/profile/edit/` | El miembro edita su propio perfil |

### Decoradores de seguridad (`apps/accounts/decorators.py`)

```python
@trainer_required    # Solo la entrenadora puede acceder — redirige si es miembro
@membership_required # Solo miembros con membresía activa — redirige si venció
```

### Protección contra ataques de fuerza bruta (django-axes)

Si alguien intenta adivinar contraseñas, el sistema bloquea el intento después de **5 intentos fallidos** durante **1 hora**. Configurado en `config/settings/base.py`:

```python
AXES_FAILURE_LIMIT = 5        # máximo intentos
AXES_COOLOFF_TIME  = 1        # horas de bloqueo
```

### Mensaje de bienvenida con género

En `templates/accounts/register_success.html`:

```html
{% if user.gender == 'M' %}¡Bienvenido
{% elif user.gender == 'F' %}¡Bienvenida
{% else %}¡Bienvenido/a{% endif %}, {{ user.first_name }}!
```

---

## 6. App: assessments — Valoraciones físicas

**Carpeta:** `apps/assessments/`

Permite que la entrenadora registre una valoración inicial de cada cliente: datos físicos, objetivos, y el Test Dickson de capacidad cardiovascular.

### Modelo: InitialAssessment

Almacena los datos físicos del cliente en un momento dado:

```python
class InitialAssessment(models.Model):
    user     = ForeignKey(User)   # Cliente evaluado
    trainer  = ForeignKey(User)   # Entrenadora que realizó la evaluación
    date     = DateField(auto_now_add=True)  # Se rellena automáticamente
    age      = PositiveIntegerField()        # Edad
    sex      = CharField(choices=['M','F','O'])  # Sexo biológico
    weight   = DecimalField()    # Peso en kg (ej: 72.5)
    height   = DecimalField()    # Estatura en metros (ej: 1.68)
    goal     = TextField()       # Objetivo del cliente
    physical_restrictions = TextField()  # Lesiones, limitaciones
    observations = TextField()   # Notas adicionales

    # Campos calculados automáticamente al guardar:
    imc = DecimalField()                # Índice de Masa Corporal
    imc_classification = CharField()   # "Bajo peso", "Peso normal", etc.
```

**Cálculo automático del IMC:** En el método `save()`, antes de guardar en la base de datos:

```python
def save(self, *args, **kwargs):
    if self.weight and self.height:
        imc_val = peso_kg / (estatura_m ** 2)
        self.imc = round(imc_val, 2)
        self.imc_classification = classify_by_ranges(imc_val, IMC_RANGES)
    super().save(*args, **kwargs)
```

**Clasificaciones IMC:**

| Rango | Clasificación |
|-------|---------------|
| < 18.5 | Bajo peso |
| 18.5 – 24.9 | Peso normal |
| 25.0 – 29.9 | Sobrepeso |
| ≥ 30.0 | Obesidad |

### Modelo: DixonTest

El Test Dickson mide la capacidad cardiovascular midiendo el pulso en tres momentos:

```python
class DixonTest(models.Model):
    assessment = OneToOneField(InitialAssessment)  # Una valoración, un test
    p0 = PositiveIntegerField()   # Pulso inicial en reposo (ppm)
    p1 = PositiveIntegerField()   # Pulso justo después de actividad física
    p2 = PositiveIntegerField()   # Pulso 1 minuto después

    # Calculados automáticamente:
    index_value  = DecimalField()  # IRD = ((P0+P1+P2) - 200) / 10
    classification = CharField()   # "Excelente", "Muy buena", etc.
    observations = TextField()     # Notas opcionales del test
```

**Fórmula IRD:**
```
IRD = ((P0 + P1 + P2) - 200) / 10
```

**Clasificaciones:**

| IRD | Clasificación |
|-----|---------------|
| ≤ 0 | Excelente |
| 0.1 – 5 | Muy buena |
| 5.1 – 10 | Buena |
| 10.1 – 15 | Regular |
| > 15 | Mala |

### Cálculo en tiempo real (Alpine.js)

En el formulario de valoración (`templates/trainer/assessment_create.html`), los cálculos ocurren en el navegador sin recargar la página:

```javascript
// Alpine.js — se ejecuta en el navegador del usuario
x-data="{
  weight: '', height: '',
  p0: '', p1: '', p2: '',

  get imc() {
    return (weight / (height * height)).toFixed(2);
  },
  get ird() {
    return (((p0 + p1 + p2) - 200) / 10).toFixed(2);
  }
}"
```

`x-model="weight"` conecta el campo del formulario con la variable Alpine. Cada vez que el usuario escribe, `imc` y `ird` se recalculan automáticamente.

---

## 7. App: exercises — Biblioteca de ejercicios

**Carpeta:** `apps/exercises/`

Almacena los ejercicios con su descripción, nivel, músculos trabajados, imagen y video.

### Modelo: ExerciseCategory

Categorías para organizar los ejercicios (ej: "Pecho", "Piernas", "Cardio"):

```python
class ExerciseCategory(models.Model):
    name  = CharField()        # Nombre de la categoría
    order = PositiveIntegerField()  # Número para ordenar (1, 2, 3...)
```

### Modelo: Exercise

```python
class Exercise(models.Model):
    name     = CharField()     # Nombre del ejercicio (ej: "Sentadilla")
    category = ForeignKey(ExerciseCategory)  # A qué categoría pertenece
    level    = CharField(choices=['beginner','intermediate','advanced'])
    description   = TextField()  # Descripción detallada
    muscles       = TextField()  # Músculos que trabaja
    recommendations = TextField()  # Consejos de ejecución

    # Archivos multimedia:
    video_file = FileField(upload_to='exercises/videos/')   # Video del ejercicio
    image      = ImageField(upload_to='exercises/images/')  # Imagen/foto

    is_public = BooleanField()   # Si True, aparece en la web pública
    is_active = BooleanField()   # Si False, está archivado (no se muestra)
```

**¿Dónde se guardan los archivos?**
- En desarrollo local: carpeta `/media/exercises/videos/` y `/media/exercises/images/`
- En producción: Cloudflare R2 (ver sección 14 para cómo subir videos)

---

## 8. App: routines — Rutinas de entrenamiento

**Carpeta:** `apps/routines/`

La entrenadora construye rutinas personalizadas para cada cliente, organizadas por días.

### Modelos

```python
class Routine(models.Model):
    user    = ForeignKey(User)    # Cliente al que se asigna
    trainer = ForeignKey(User)    # Entrenadora que la crea
    name    = CharField()         # Ej: "Rutina de hipertrofia 4 días"
    notes   = TextField()         # Indicaciones generales
    is_active = BooleanField()    # Solo una rutina activa por cliente

class RoutineDay(models.Model):
    routine  = ForeignKey(Routine)  # Pertenece a esta rutina
    day_name = CharField()          # Ej: "Lunes - Pecho y tríceps"
    order    = PositiveIntegerField() # 1, 2, 3... para ordenar los días

class RoutineExercise(models.Model):
    day      = ForeignKey(RoutineDay)   # En qué día está este ejercicio
    exercise = ForeignKey(Exercise)     # Qué ejercicio es
    sets     = PositiveIntegerField()   # Número de series (ej: 4)
    reps     = CharField()              # Repeticiones (ej: "10-12" o "al fallo")
    rest_seconds = PositiveIntegerField()  # Descanso en segundos
    order    = PositiveIntegerField()   # Posición dentro del día
    notes    = TextField()              # Notas específicas del ejercicio
```

---

## 9. App: memberships — Membresías

**Carpeta:** `apps/memberships/`

Controla los planes de membresía y las suscripciones activas de cada cliente.

### Modelo: MembershipPlan

Define los tipos de membresía disponibles:

```python
class MembershipPlan(models.Model):
    name          = CharField()         # Ej: "Plan Mensual"
    duration_days = PositiveIntegerField()  # 30, 90, 180
    reference_price = DecimalField()    # Precio de referencia (puede variar)
    description   = TextField()         # Qué incluye el plan
    is_active     = BooleanField()      # Si está disponible para comprar
```

### Modelo: Membership

La suscripción activa de un cliente específico:

```python
class Membership(models.Model):
    user  = OneToOneField(User)    # Un usuario, una membresía
    plan  = ForeignKey(MembershipPlan)
    start_date = DateField()       # Cuándo comenzó
    end_date   = DateField()       # Cuándo vence

    # Propiedades calculadas:
    # is_valid       → True si hoy está entre start_date y end_date
    # days_remaining → cuántos días quedan
```

La entrenadora puede activar/renovar membresías desde el panel de cliente.

---

## 10. App: payments — Pagos con Wompi

**Carpeta:** `apps/payments/`

Integración con Wompi, la pasarela de pagos colombiana.

### Flujo de pago

```
1. Cliente elige un plan → /memberships/checkout/<plan_id>/
2. Sistema genera un "reference" único y calcula la firma de integridad
3. Wompi muestra su widget de pago (tarjeta, PSE, Nequi, etc.)
4. Cliente paga
5. Wompi redirige al cliente a /payments/return/?id=TRANSACCION
6. Wompi envía un webhook a /payments/webhook/ para confirmar el pago
7. El webhook verifica la firma y activa la membresía
```

### Verificación de integridad

Para evitar fraudes, Wompi usa una firma criptográfica SHA256:

```python
# services.py
import hashlib

def calculate_integrity_hash(reference, amount_cents, currency, secret):
    raw = f"{reference}{amount_cents}{currency}{secret}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

### Modelo: Payment

```python
class Payment(models.Model):
    user              = ForeignKey(User)
    plan              = ForeignKey(MembershipPlan)
    reference         = CharField(unique=True)    # ID único de la transacción
    amount_cents      = PositiveIntegerField()     # Monto en centavos
    status            = CharField()               # pending, approved, declined, etc.
    wompi_transaction_id = CharField()            # ID de Wompi
    webhook_payload   = JSONField()               # Datos completos del webhook
    created_at        = DateTimeField()
    updated_at        = DateTimeField()
```

---

## 11. App: public — Páginas públicas

**Carpeta:** `apps/public/`

Páginas visibles para cualquier visitante sin login:

| URL | Template | Contenido |
|-----|----------|-----------|
| `/` | `public/home.html` | Página de inicio — servicios, planes, CTA |
| `/servicios/` | `public/about.html` | Descripción detallada de servicios |
| `/contacto/` | `public/contact.html` | Formulario de contacto y WhatsApp |
| `/ejercicios/` | `public/exercises.html` | Catálogo público de ejercicios |
| `/planes/` | `public/plans.html` | Planes y precios |
| `/health/` | — | Solo responde 200 OK (para Railway) |

**Context Processor (`apps/public/context_processors.py`):** Agrega automáticamente `WHATSAPP_LINK` e `INSTAGRAM_URL` a todas las plantillas, para que el footer y navbar siempre tengan los datos de contacto sin repetir código.

---

## 12. Plantillas (Templates) y diseño visual

**Carpeta:** `templates/`

### Herencia de plantillas

Todas las páginas heredan de una base para no repetir el HTML del encabezado, CSS y pie de página:

```
base.html                 ← Layout general (carga Tailwind, Alpine, Font Awesome)
  └── trainer/base.html   ← Layout del panel de entrenadora (sidebar, header)
        └── trainer/dashboard.html    ← Una página concreta
        └── trainer/clients.html
        └── trainer/assessment_create.html
        └── ...
```

**Cómo funciona la herencia:**

```html
<!-- trainer/dashboard.html -->
{% extends 'trainer/base.html' %}  <!-- "hereda de esta base" -->

{% block title %}Mi panel{% endblock %}  <!-- rellena el título -->

{% block trainer_content %}
  <!-- aquí va el contenido específico de esta página -->
{% endblock %}
```

### Stack visual

- **Tailwind CSS**: utilidades de estilos por clase. Ejemplos:
  - `bg-zinc-900` = fondo gris muy oscuro
  - `text-white/40` = texto blanco al 40% de opacidad
  - `rounded-2xl` = bordes muy redondeados
  - `grid grid-cols-3 gap-4` = cuadrícula de 3 columnas con separación

- **Alpine.js**: interactividad declarativa. Todo lo que hace `x-`:
  - `x-data="{ variable: '' }"` = define datos reactivos
  - `x-model="variable"` = enlaza un campo del formulario con la variable
  - `x-show="condicion"` = muestra/oculta un elemento
  - `:class="condicion ? 'clase-si' : 'clase-no'"` = clases dinámicas

- **Font Awesome**: iconos con `<i class="fa fa-check">`, `fa fa-user`, etc.

---

## 13. Configuración del sistema

### config/settings/base.py

Configuración compartida entre todos los entornos. Incluye:

- Lista de apps instaladas
- Configuración de plantillas
- URLs de login/logout
- Validadores de contraseñas
- Idioma: español Colombia (`es-co`)
- Zona horaria: `America/Bogota`
- Protección contra fuerza bruta (django-axes)
- Variables de Wompi y contacto desde `.env`

### config/settings/development.py

Para trabajar en tu computador:

```python
DEBUG = True
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': 'db.sqlite3'}}
MEDIA_ROOT = BASE_DIR / 'media'        # Archivos guardados localmente
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'  # Emails en terminal
AXES_ENABLED = False                   # Desactiva el bloqueo por intentos fallidos
```

### config/settings/production.py

Para el servidor real:

```python
DEBUG = False
DATABASES = {'default': env.db('DATABASE_URL')}  # PostgreSQL en Railway
SECURE_SSL_REDIRECT = True             # Fuerza HTTPS
SESSION_COOKIE_SECURE = True           # Cookies solo por HTTPS
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'  # Cloudflare R2
```

---

## 14. Cómo agregar videos de entrenamiento

Los videos se asocian a ejercicios (`Exercise.video_file`). Hay **dos formas**:

### Opción A — Desde el panel de administración (la más fácil)

1. Inicia el servidor: `python manage.py runserver`
2. Ve a `http://localhost:8000/admin/`
3. Entra con tu cuenta de entrenadora (debe tener `is_staff=True`)
4. En el menú izquierdo, haz clic en **Exercises → Ejercicios**
5. Haz clic en el ejercicio al que quieres agregar el video
6. En el campo **Video**, haz clic en **Choose File** y selecciona el archivo `.mp4`
7. Guarda

> El video se guarda en `media/exercises/videos/` (en desarrollo local).

### Opción B — Desde el panel de la entrenadora

1. Ve al menú **Ejercicios** en el panel de entrenadora
2. Haz clic en **Nuevo ejercicio** o edita uno existente
3. El formulario tiene un campo para subir el video
4. Guarda — el video queda asociado al ejercicio

### Formatos de video recomendados

- **Formato:** MP4 (H.264) — compatible con todos los navegadores
- **Resolución:** 720p (1280×720) es suficiente para ahorrar espacio
- **Duración:** 10–60 segundos por ejercicio
- **Peso máximo recomendado:** 50 MB por video (en local), en producción Cloudflare R2 acepta hasta varios GB

### En producción (Cloudflare R2)

Cuando el proyecto está desplegado en Railway, los archivos se suben automáticamente a Cloudflare R2 — no necesitas hacer nada diferente. El mismo formulario de subida funciona igual; solo que el archivo termina en la nube en vez de en tu disco duro.

---

## 15. Cómo agregar la foto de la entrenadora

La foto de perfil se guarda en `User.profile_photo`.

### Opción A — Desde el admin de Django

1. Ve a `http://localhost:8000/admin/accounts/user/`
2. Haz clic en la cuenta de la entrenadora
3. En el campo **Foto de perfil**, haz clic en **Choose File**
4. Selecciona la foto (JPG o PNG recomendado)
5. Guarda

### Opción B — Desde el perfil de usuario

Si hay un formulario de edición de perfil en la app (`/accounts/profile/edit/`):

1. La entrenadora inicia sesión
2. Va a **Editar perfil**
3. Sube la foto

### Cómo mostrar la foto en las plantillas

Una vez subida, en cualquier template puedes usar:

```html
{% if user.profile_photo %}
  <img src="{{ user.profile_photo.url }}" alt="Foto de la entrenadora"
       class="w-16 h-16 rounded-full object-cover">
{% else %}
  <!-- Placeholder si no hay foto -->
  <div class="w-16 h-16 rounded-full bg-zinc-700 flex items-center justify-center">
    <i class="fa fa-user text-white/40"></i>
  </div>
{% endif %}
```

> `user.profile_photo.url` genera automáticamente la URL correcta, ya sea local o de Cloudflare R2.

### En la página de inicio (página pública)

Si quieres mostrar la foto de la entrenadora en `templates/public/home.html` o `templates/public/about.html`:

1. En `apps/public/views.py`, pasa la entrenadora al contexto:
   ```python
   from apps.accounts.models import User
   trainer = User.objects.filter(role='trainer').first()
   return render(request, 'public/about.html', {'trainer': trainer})
   ```
2. En la plantilla:
   ```html
   {% if trainer and trainer.profile_photo %}
     <img src="{{ trainer.profile_photo.url }}" ...>
   {% endif %}
   ```

---

## 16. Cómo pasar a producción en Railway

Railway es el servicio de hosting configurado en este proyecto. Sigue estos pasos:

### Paso 1 — Crear cuenta y proyecto en Railway

1. Ve a [railway.app](https://railway.app) y crea una cuenta (puedes entrar con GitHub)
2. Crea un nuevo proyecto → **New Project**
3. Elige **Deploy from GitHub repo** → conecta tu repositorio

### Paso 2 — Agregar la base de datos PostgreSQL

1. En tu proyecto de Railway, haz clic en **New** → **Database** → **PostgreSQL**
2. Railway crea la base de datos y genera la variable `DATABASE_URL` automáticamente

### Paso 3 — Configurar las variables de entorno

En Railway, ve a tu servicio → pestaña **Variables** → agrega estas variables:

```
DJANGO_SETTINGS_MODULE = config.settings.production
SECRET_KEY              = (genera una clave larga y aleatoria)
ALLOWED_HOSTS           = tudominio.railway.app,tudominio.com
DATABASE_URL            = (Railway lo agrega automáticamente desde el servicio PostgreSQL)

# Cloudflare R2 (para archivos/videos)
R2_ACCESS_KEY_ID        = (tu clave de Cloudflare R2)
R2_SECRET_ACCESS_KEY    = (tu clave secreta de R2)
R2_BUCKET_NAME          = profitstudio-media
R2_ENDPOINT_URL         = https://TU_ACCOUNT_ID.r2.cloudflarestorage.com

# Contacto
WHATSAPP_NUMBER         = +573005638196
WHATSAPP_LINK           = https://wa.me/573005638196
INSTAGRAM_URL           = https://instagram.com/profitstudio75

# Wompi (cuando tengas tu cuenta de comercio)
WOMPI_PUBLIC_KEY        = (de tu cuenta de Wompi)
WOMPI_PRIVATE_KEY       = (de tu cuenta de Wompi)
WOMPI_INTEGRITY_SECRET  = (de tu cuenta de Wompi)
WOMPI_EVENTS_SECRET     = (de tu cuenta de Wompi)
```

**¿Cómo generar el SECRET_KEY?** Puedes usar este comando en Python:
```python
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### Paso 4 — Configurar Cloudflare R2 (para archivos)

1. Ve a [cloudflare.com](https://cloudflare.com) → R2 Object Storage
2. Crea un bucket llamado `profitstudio-media`
3. En **Manage R2 API Tokens**, crea un token con permisos de lectura y escritura
4. Copia el Access Key ID y Secret Access Key al `.env` de Railway

### Paso 5 — Primer despliegue

Railway detecta el `railway.toml` y ejecuta automáticamente:

```
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2
```

Pero antes de que el sitio funcione, necesitas ejecutar los comandos de inicialización. En Railway, ve a tu servicio → **Shell** (o crea un **One-off command**):

```bash
# 1. Aplicar migraciones (crear tablas en PostgreSQL)
python manage.py migrate

# 2. Recolectar archivos estáticos (CSS, imágenes fijas)
python manage.py collectstatic --noinput

# 3. Crear la cuenta de la entrenadora
python manage.py create_trainer

# 4. Cargar datos iniciales (categorías de ejercicios, planes de membresía)
python manage.py load_initial_data
```

### Paso 6 — Dominio personalizado (opcional)

En Railway → tu servicio → **Settings** → **Networking** → puedes agregar tu propio dominio (ej: `www.profitstudio.com`).

### Resumen del flujo completo

```
Código en tu PC → git push → GitHub → Railway detecta cambios
→ Railway construye la imagen → reinicia el servidor
→ Tu web está actualizada en producción
```

---

## 17. Variables de entorno — referencia completa

El archivo `.env` en la raíz del proyecto controla el comportamiento del sistema. **Nunca lo subas a git** (está en `.gitignore`).

| Variable | Requerida en prod | Descripción |
|----------|:-----------------:|-------------|
| `SECRET_KEY` | ✅ | Clave criptográfica de Django. Debe ser larga, aleatoria y única |
| `DEBUG` | ✅ (`False`) | `True` en desarrollo, siempre `False` en producción |
| `ALLOWED_HOSTS` | ✅ | Dominios permitidos, separados por coma |
| `DATABASE_URL` | ✅ | URL de conexión PostgreSQL (Railway la genera automáticamente) |
| `REDIS_URL` | ✅ | URL de Redis para cache y Celery (Railway lo proporciona) |
| `R2_ACCESS_KEY_ID` | ✅ | Credencial de Cloudflare R2 |
| `R2_SECRET_ACCESS_KEY` | ✅ | Credencial secreta de R2 |
| `R2_BUCKET_NAME` | ✅ | Nombre del bucket en R2 (ej: `profitstudio-media`) |
| `R2_ENDPOINT_URL` | ✅ | URL del endpoint de R2 (`https://ACCOUNT_ID.r2.cloudflarestorage.com`) |
| `WHATSAPP_NUMBER` | ❌ | Número con código de país (ej: `+573005638196`) |
| `WHATSAPP_LINK` | ❌ | Link directo de WhatsApp (`https://wa.me/573005638196`) |
| `INSTAGRAM_URL` | ❌ | URL del perfil de Instagram |
| `WOMPI_PUBLIC_KEY` | ❌ | Clave pública de Wompi (se muestra al cliente) |
| `WOMPI_PRIVATE_KEY` | ❌ | Clave privada de Wompi (nunca exponer al cliente) |
| `WOMPI_INTEGRITY_SECRET` | ❌ | Secreto para generar firmas de integridad |
| `WOMPI_EVENTS_SECRET` | ❌ | Secreto para verificar webhooks de eventos |

> Las variables marcadas ❌ en "Requerida" tienen valores por defecto en `settings/base.py` y son opcionales en desarrollo.

---

## 18. Comandos útiles del día a día

Todos los comandos se ejecutan desde la carpeta raíz del proyecto con el entorno virtual activado.

### Iniciar el servidor de desarrollo

```bash
# Windows
.\.venv\Scripts\activate
python manage.py runserver

# macOS/Linux
source .venv/bin/activate
python manage.py runserver
```

El sitio estará disponible en `http://localhost:8000`

### Base de datos

```bash
# Crear migraciones cuando cambias un modelo
python manage.py makemigrations

# Aplicar migraciones a la base de datos
python manage.py migrate

# Ver el estado de las migraciones
python manage.py showmigrations

# Verificar que no hay errores de configuración
python manage.py check
```

### Usuarios y datos

```bash
# Crear la cuenta de entrenadora (primer uso)
python manage.py create_trainer

# Crear un superusuario para el admin de Django
python manage.py createsuperuser

# Cargar categorías de ejercicios y planes de membresía
python manage.py load_initial_data
```

### Archivos estáticos

```bash
# Recolectar estáticos para producción (WhiteNoise los sirve)
python manage.py collectstatic
```

### Abrir la consola de Django (para explorar datos)

```bash
python manage.py shell

# Dentro del shell:
from apps.accounts.models import User
User.objects.all()  # Ver todos los usuarios
User.objects.filter(role='trainer')  # Solo entrenadores
```

### Git (control de versiones)

```bash
# Ver estado de los archivos modificados
git status

# Agregar todos los cambios
git add .

# Crear un punto de guardado (commit)
git commit -m "Descripción del cambio"

# Ver historial de cambios
git log --oneline
```

---

## Notas finales

### Seguridad — cosas que nunca debes hacer

- ❌ Nunca subas el archivo `.env` a GitHub
- ❌ Nunca pongas `DEBUG=True` en producción
- ❌ Nunca compartas el `SECRET_KEY` ni las claves de Wompi
- ❌ Nunca expongas el `WOMPI_PRIVATE_KEY` en el frontend (JavaScript)

### Si algo falla en producción

1. Ve a Railway → tu servicio → pestaña **Logs** para ver errores en tiempo real
2. Verifica que todas las variables de entorno estén configuradas correctamente
3. Ejecuta `python manage.py check --deploy` para detectar problemas de configuración
4. Verifica que las migraciones estén aplicadas: `python manage.py showmigrations`

### Actualizaciones futuras

Cada vez que hagas cambios al código:

```bash
# En tu PC
git add .
git commit -m "Descripción del cambio"
git push origin main

# Railway detecta el push y redesplega automáticamente
```

Si los cambios incluyen nuevos modelos o campos:
```bash
# Después del despliegue, en Railway Shell:
python manage.py migrate
```

---

*Documentación generada el 2026-05-06 para ProFit Studio — versión 1.0*
