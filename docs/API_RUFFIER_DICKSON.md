# API de evaluación Ruffier / Ruffier-Dickson — ProFit Studio

Implementación: 17 de noviembre de 2026. API interna v1, del mismo origen que la web.

## Qué se construyó

Una API es una entrada del backend que recibe y entrega datos mediante HTTP. Aquí la pantalla del entrenador envía tres frecuencias cardíacas en JSON. Django comprueba la sesión, la autorización y el consentimiento, calcula dos índices y guarda una evaluación nueva. La respuesta vuelve a la pantalla sin recargarla.

No necesita un proveedor de cálculo ni una clave de API. El cálculo es determinista y se ejecuta en Python, con Decimal para guardar dos decimales sin errores de coma flotante.

Flujo: formulario de la valoración → fetch con sesión y CSRF → API Django → validación y autorización → cálculo → fila nueva en la base de datos → respuesta JSON e historial.

## Fórmulas y protocolo

Nombres usados por esta API:

- P0 / resting_hr_bpm: frecuencia en reposo.
- P1 / post_exercise_hr_bpm: frecuencia inmediatamente después del esfuerzo.
- P2 / recovery_hr_bpm: frecuencia al minuto desde que termina el esfuerzo.

IR = (P0 + P1 + P2 − 200) / 10.

IRD = (P1 − 70 + 2 × (P2 − P0)) / 10.

Referencia documental: [documento del médico federal de Wushu France](https://www.fwfwushu.fr/wp-content/uploads/2023/05/07-TEST-DE-RUFFIER.pdf), páginas 3 y 4. Su notación P1/P2/P3 equivale aquí a P0/P1/P2. Describe reposo acostado durante unos minutos, 30 flexiones en 45 segundos y frecuencia de recuperación al minuto, nuevamente acostado.

La API guarda protocol_version=squats-30-45s-recovery-60s/1 y formula_version=ruffier-dickson/1. El entrenador confirma que se completó ese protocolo; el servidor no puede comprobar físicamente su ejecución. Los campos se expresan en latidos por minuto: un conteo tomado durante 15 segundos no debe introducirse directamente como si fuera bpm. No hay sensor, medición automática, cronómetro ni conexión con reloj.

Los tres valores deben ser enteros entre 20 y 250. Es un límite técnico de entrada heredado de los formularios existentes, no un intervalo de normalidad clínica ni un criterio de aptitud. Los resultados negativos se conservan. No se devuelve clasificación clínica: interpretation=null e interpretation_status=professional_review_required. La selección, supervisión e interpretación corresponden al profesional responsable; el resultado no certifica aptitud ni descarta enfermedad. No se adapta a menores.

## Archivos y responsabilidades

| Archivo | Responsabilidad |
| --- | --- |
| apps/assessments/cardio.py | Fórmulas puras, versiones, metadatos del protocolo y serialización de resultados. |
| apps/assessments/api.py | HTTP/JSON, control de acceso, validación estricta, protección de duplicados, guardado e historial. |
| apps/assessments/api_urls.py | Ruta de la API. |
| apps/assessments/models.py | CardioEvaluation: pulsos, índices, valoración, autor, consentimiento y versiones. |
| apps/assessments/migrations/0007_cardio_evaluation.py | Crea la tabla; no actualiza ni recalcula DixonTest. |
| apps/assessments/migrations/0008_legacy_ruffier_label.py | Corrige el nombre del registro anterior en el administrador; no altera sus datos. |
| config/urls.py | Publica la ruta bajo /api/v1/assessments/. |
| config/settings/base.py | Respuesta CSRF de API en JSON; las demás rutas conservan el error habitual de Django. |
| templates/trainer/partials/cardio_api.html | Formulario y listado dentro del detalle de la valoración. |
| static/js/cardio-api.js | Envío seguro por fetch, estado de guardado y presentación del historial con textContent. |
| apps/health/services.py | Incluye las nuevas evaluaciones en la exportación de datos propios de salud. |
| apps/assessments/tests/test_cardio_api.py | Pruebas de cálculo, API, privacidad, integración de plantilla y regresión histórica. |

No se agregaron dependencias ni un framework REST: Django JsonResponse y sus controles existentes son suficientes para este endpoint interno.

## Datos y conservación del histórico

CardioEvaluation se relaciona mediante una clave foránea con InitialAssessment. Una valoración puede contener varias evaluaciones. Cada fila registra fecha/hora de guardado, entrenador, consentimiento utilizado, tres frecuencias, ambos índices y las versiones. created_at indica cuándo se guardó, no una hora de realización verificada por dispositivo.

El token de petición es único por valoración. En PostgreSQL se bloquea la valoración durante el guardado para serializar envíos concurrentes; una restricción única en la base refuerza la protección de duplicados.

La API solo permite agregar y consultar; no expone edición ni borrado. Esto no hace inmutable la tabla frente a SQL, al ORM o a un administrador de base de datos. Los datos desaparecen si se borra la valoración o la cuenta por las relaciones CASCADE; una solicitud de supresión selectiva debe atenderse mediante el procedimiento de gestión de datos del proyecto.

Los registros antiguos siguen en DixonTest. Su función, cifras y clasificación heredada se conservaron, pero la web los identifica correctamente como Ruffier. La escala antigua no se ha revalidado clínicamente y no se aplica al IRD nuevo. El formulario anterior de Ruffier conserva su comportamiento de edición; no escribe CardioEvaluation. Las nuevas evaluaciones se registran desde el panel superior del detalle.

## Acceso y privacidad

- Solo usuarios activos con sesión y rol trainer; la superusuaria trainer puede gestionar todas las clientas.
- Otros entrenadores solo acceden a valoraciones de clientas asignadas mediante clients_for_trainer. Una valoración ajena o inexistente responde 404.
- HEALTH_FEATURES_ENABLED debe estar encendido. La API y el panel nuevo permanecen apagados con el interruptor desactivado.
- Consentimiento health_data vigente, no retirado y sobre la versión actual del texto.
- Se bloquea si la edad de la valoración es menor de 18 o el dato vigente conocido indica minoría de edad.
- POST exige el token CSRF de Django en X-CSRFToken; no se usa csrf_exempt. Ver [documentación oficial de Django](https://docs.djangoproject.com/en/5.2/howto/csrf/).
- Las respuestas de la API, incluidos errores controlados y fallos CSRF, usan Cache-Control con no-store.
- Las lecturas y altas dejan entradas view/edit en HealthAccessLog sin pulsos ni contenido del cuerpo.
- Sin llamadas a Gemini, Meta u otros terceros. La API no registra el JSON recibido. Revisar por separado cualquier proxy o APM externo que pudiera registrar cuerpos HTTP.
- La API no habilita CORS, claves públicas o acceso desde apps móviles externas. Una integración externa futura requiere diseñar autenticación y permisos específicos.

La configuración de desarrollo puede mostrar plantillas de consentimiento borrador; en producción se conserva el interruptor legal ya existente. Esta implementación no publica consentimientos ni activa salud en producción.

## Endpoint para registrar

POST /api/v1/assessments/{assessment_id}/ruffier-dickson/

Cabeceras: Content-Type: application/json y X-CSRFToken. La cookie de sesión debe pertenecer al entrenador autorizado.

Ejemplo con una valoración existente y autorizada:

```json
{
  "resting_hr_bpm": 60,
  "post_exercise_hr_bpm": 120,
  "recovery_hr_bpm": 80,
  "protocol_confirmed": true,
  "request_token": "99fda1e5-f843-4697-bba8-137384343277"
}
```

Respuesta 201 al crear (ids y fecha de ejemplo):

```json
{
  "id": 1,
  "assessment_id": 123,
  "request_token": "99fda1e5-f843-4697-bba8-137384343277",
  "created_at": "2026-11-17T15:00:00+00:00",
  "resting_hr_bpm": 60,
  "post_exercise_hr_bpm": 120,
  "recovery_hr_bpm": 80,
  "ruffier_index": "6.00",
  "ruffier_dickson_index": "9.00",
  "formula_version": "ruffier-dickson/1",
  "protocol_version": "squats-30-45s-recovery-60s/1",
  "protocol_confirmed": true,
  "interpretation": null,
  "interpretation_status": "professional_review_required"
}
```

Los índices son cadenas decimales intencionalmente; así la representación exacta del dato persiste en JSON. Para gráficas se pueden convertir a Number en el cliente.

El navegador genera request_token mediante crypto.randomUUID(). Si se repite la misma petición con el mismo token y pulsos, devuelve 200 con la fila original. El mismo token con otros pulsos devuelve 409. Una medición realmente nueva usa otro token.

El servidor rechaza campos desconocidos, resultados calculados enviados por el cliente, pulsos como texto, decimales, booleanos, nulos, y confirmaciones distintas de true. El cuerpo tiene un máximo de 4096 bytes.

## Endpoint para consultar

GET /api/v1/assessments/{assessment_id}/ruffier-dickson/

Devuelve results, protocol y next_before. La lista contiene hasta 20 evaluaciones, ordenadas de la más nueva a la más antigua. Para seguir: GET con ?before={next_before}. Cuando next_before=null no hay más filas.

El panel web muestra la primera página; para consultar todo el historial se usa la paginación de la API. No se mezclan los registros DixonTest con los de la API nueva.

## Errores

```json
{
  "error": {
    "code": "validation_error",
    "message": "Revisa los campos.",
    "fields": {"resting_hr_bpm": "Introduce un entero entre 20 y 250 bpm."}
  }
}
```

| HTTP | Significado |
| --- | --- |
| 200 | Consulta correcta o reintento de un registro ya guardado. |
| 201 | Evaluación creada. |
| 400 | Campos/JSON/cursor inválidos. |
| 401 | Sin sesión activa (una petición insegura sin CSRF puede ser rechazada antes con 403). |
| 403 | Rol no permitido, consentimiento ausente, menor de edad o CSRF inválido. |
| 404 | Función apagada o valoración inexistente/ajena. |
| 405 | Método distinto de GET/POST; no admite PUT, PATCH, DELETE ni HEAD. |
| 409 | Token ya usado con otros datos. |
| 413 | Cuerpo demasiado grande. |
| 415 | Content-Type distinto de application/json. |

Los errores no copian los pulsos del request. Fallos inesperados de infraestructura siguen el tratamiento habitual de Django y deben monitorearse sin capturar cuerpos de salud.

## Uso desde ProFit Studio

1. Abre en VS Code la carpeta que contiene manage.py y CLAUDE.md, y selecciona el intérprete .venv.
2. Ejecuta los comandos siguientes en PowerShell desde esa carpeta.
3. Inicia sesión como entrenador y abre Clientes → clienta asignada → valoración inicial.
4. Con salud habilitada en desarrollo y consentimiento health_data vigente de la clienta, aparece el panel nuevo en el detalle. No se autoacepta consentimiento desde la cuenta del entrenador.
5. Introduce los bpm, confirma el protocolo y pulsa Calcular y guardar evaluación. Aparecen IR, IRD y el registro en el historial.
6. Para otra medición vuelve a completar el formulario; para resultados antiguos consulta el bloque Ruffier anterior.

```powershell
.\.venv\Scripts\python.exe manage.py migrate --settings=config.settings.development
.\.venv\Scripts\python.exe manage.py runserver --settings=config.settings.development
```

En esta implementación ya se aplicaron localmente assessments.0007 y assessments.0008; repetir migrate no crea filas duplicadas. No se insertaron datos de salud de prueba en la base local ni se aceptaron consentimientos por la clienta.

En producción, entregar el código por el proceso habitual; start.sh ejecutará la migración. Mantener HEALTH_FEATURES_ENABLED apagado hasta cumplir las condiciones legales ya documentadas. La migración agrega una tabla y no recalcula el histórico. No se hizo despliegue desde esta implementación.

## Ejemplo desde el navegador autenticado

En la consola de la página de la valoración (reemplaza 123 por el id real):

```javascript
const token = document.querySelector('[name=csrfmiddlewaretoken]').value;
const response = await fetch('/api/v1/assessments/123/ruffier-dickson/', {
  method: 'POST',
  mode: 'same-origin',
  credentials: 'same-origin',
  headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
  body: JSON.stringify({
    resting_hr_bpm: 60,
    post_exercise_hr_bpm: 120,
    recovery_hr_bpm: 80,
    protocol_confirmed: true,
    request_token: crypto.randomUUID()
  })
});
const result = await response.json();
```

Usa únicamente datos reales autorizados o un entorno de pruebas con datos sintéticos. Este ejemplo crea un registro; abrir la URL GET en un navegador con sesión solo consulta.

## Verificación y mantenimiento

```powershell
.\.venv\Scripts\python.exe manage.py test apps.assessments apps.accounts.tests.test_authorization apps.health.tests.test_profile_services --settings=config.settings.development
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-css.ps1
node --check static/js/cardio-api.js
git diff --check
```

Resultado de esta implementación: 172 pruebas de evaluaciones, autorización y servicios de salud pasaron, además de las 6 pruebas de Tailwind; check no reportó problemas y makemigrations no detectó cambios sin migración. Las migraciones nuevas se aplicaron en SQLite local. La compilación de Tailwind terminó correctamente tras resolver el bloqueo inicial de red; no cambió el CSS generado porque las clases utilizadas ya estaban incluidas. La sintaxis de JavaScript y git diff --check también pasaron.

Las pruebas cubren fórmulas que producen resultados distintos, índices negativos, validación de tipos, autorización por clienta, consentimiento retirado/versionado, menores, interruptor apagado, CSRF real, tamaño de cuerpo, paginación, reintentos sin duplicados, exportación y preservación de resultados anteriores. No sustituyen validación clínica, prueba de carga, navegador real ni prueba de concurrencia sobre PostgreSQL.

Si se cambia una fórmula o protocolo, crear una nueva versión explícita y documentar compatibilidad. No recalcular silenciosamente registros existentes ni añadir una escala clínica sin su fuente, límites exactos y validación profesional.

