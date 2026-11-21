# Camino guiado de la clienta hasta su plan con IA

Estado: **APROBADO por la persona dueña el 2026-11-20** (rama `feature/camino-guiado`). Backend: `apps/plans/journey.py`. Pantallas: las construye `frontend-architect` contra esta forma exacta (`templates/plans/partials/journey_card.html`). Complementa `plan-ia.md` (sección 3.1, requisitos `D..`) y `ficha-basica.md`.

## 1. Objetivo

Hoy «Pedir mi plan» solo lista lo que FALTA, con «Ir ahora». El panel de la clienta no muestra el camino y no se ve lo ya hecho. Esta función entrega **un diccionario con los pasos (hechos, por hacer y bloqueados), el avance y el estado de cada plan**, para dibujarlo en el panel y en «Pedir mi plan». Además, los pasos que mandan a la clienta a otra pantalla (consentimiento, ficha, medidas) la **devuelven** a donde empezó (`next`).

No cambia ninguna regla: la elegibilidad sigue siendo `services.assess` y el motor `rules/eligibility.py`. El camino solo la presenta.

## 2. `client_journey(user, back_url=None, base=None) -> dict | None`

Módulo `apps/plans/journey.py`.

- `back_url`: ruta interna a la que vuelve la clienta al terminar un paso. Por defecto, el panel (`reverse('dashboard')`). `member_plan_request` pasa `/plans/request/?kind=<kind>`.
- `base`: opcional, para la vista que ya calculó `services.assess_base(...)` (evita repetir consultas). Quien llama de afuera no lo necesita.
- Nunca llama a la IA, no crea trabajos y no escribe nada. Solo lee.

### 2.1 Cuándo devuelve `None` (no se dibuja nada)

- Función apagada: `services.feature_available()` falso (salud apagada, `AI_PLANS_MODE=off` o textos legales con marcadores en producción).
- La usuaria no es clienta (entrenador/a o superusuaria) o no está autenticada.
- Sin membresía vigente.
- Cuenta desactivada o eliminada.
- Menor de 18 años (`health.is_minor`).

### 2.2 Forma del resultado

```
{
  'steps': [ {                       # orden fijo, siempre 5
      'code': 'consent_health' | 'profile' | 'measure' | 'consent_ai' | 'activity',
      'title': str,                  # imperativo corto
      'description': str,            # una o dos frases, sin códigos técnicos
      'status': 'done' | 'todo' | 'locked',
      'url': str | None,
      'cta': str | None,
      'scope': 'all' | 'nutrition',
  }, ... ],
  'done_count': int,                 # pasos con status 'done' (de 'total')
  'total': 5,
  'next_step': dict | None,          # el primer paso 'todo' (es el mismo objeto de `steps`)
  'plans': {
      'exercise':  {'state': str, 'url': str | None, 'cta': str | None, 'message': str},
      'nutrition': {'state': str, 'url': str | None, 'cta': str | None, 'message': str},
  },
  'all_ready': bool,                 # ningún paso de scope 'all' está pendiente
  'all_done': bool,                  # los 5 pasos están hechos (incluido 'activity')
}
```

`all_done` es un campo **agregado** a la forma acordada (no cambia ninguno). Existe porque `all_ready` puede ser verdadero con `done_count == 4` (falta solo el nivel de actividad, que únicamente necesita el plan de alimentación): el texto «5 de 5 pasos hechos» debe usar `all_done`, no `all_ready`.

El contexto **no lleva** datos de salud, ni códigos de bandera, ni los códigos `D..`: solo estados, textos genéricos y direcciones.

### 2.3 Los pasos

| `code` | `scope` | Depende de | Título | Texto del botón (`cta`) |
| --- | --- | --- | --- | --- |
| `consent_health` | `all` | nada | Autoriza el uso de tus datos de salud | Autorizar |
| `profile` | `all` | `consent_health` | Completa tu ficha de salud y alimentación (si el entrenador la corrigió: «Confirma tu ficha de salud y alimentación») | Llenar mi ficha (o «Revisar y confirmar») |
| `measure` | `all` | `consent_health` | Registra tus medidas (si los datos no cuadran: «Revisa tus datos») | Registrar mis medidas (o «Revisar mis datos») |
| `consent_ai` | `all` | `consent_health` | Autoriza el apoyo de la inteligencia artificial | Autorizar |
| `activity` | `nutrition` | `profile` | Cuéntanos tu nivel de actividad diaria | Completar más detalles |

Cálculo de `status` (siempre en este orden; la dependencia es un paso anterior en la lista):

1. `done`: el requisito ya no falta. Excepción: `activity` solo es `done` si `profile` también lo está (sin ficha no hay nivel de actividad que dar).
2. `locked`: falta, pero el paso del que depende no está `done`. Sin consentimiento de salud, todo está `locked` salvo `consent_health`.
3. `todo`: falta y se puede hacer ya.

Un paso `done` no vuelve a `locked` si después se retira el consentimiento: se queda `done` y `consent_health` pasa a `todo`.

Asignación desde `services.assess(user, kind, user)` (`eligibility.missing`) de **los dos tipos** de plan:

| Código `D..` que falta | Paso |
| --- | --- |
| `D01_CONSENT_HEALTH` | `consent_health` |
| `D02_PROFILE` / `D02_PROFILE_CONFIRM` | `profile` (la descripción de «confirmar» es la de `REQUIREMENT_TEXTS`) |
| `D03_MEASURE` / `D04_INCOHERENT` | `measure` (si faltan los dos, manda `D03`; con `D04` la descripción es la de `REQUIREMENT_TEXTS` y la dirección lleva a editar la ficha, como hoy) |
| `D01_CONSENT_AI` | `consent_ai` |
| `D07_NUTRITION_DETAILS` | `activity` |
| `D05_QUOTA`, `D06_*`, `D00_*` | no son pasos: dan el estado del plan (`quota` / `blocked`) |

Reglas de `url` y `cta`:

- `todo`: la dirección para resolverlo, **con `?next=<back_url>`** (el mismo `requirement_url` que usa «Pedir mi plan»). La de `activity` termina en `#mas-detalles` (abre el asistente directo en el paso 5).
- `done`: dirección para **revisarlo** (sin `next`): el consentimiento, la ficha o el historial de medidas. `cta` es `None`.
- `locked`: `url` y `cta` son `None`.

### 2.4 Los planes (`plans.exercise` y `plans.nutrition`)

`state`, en este orden de prioridad:

| `state` | Cuándo | `url` | `cta` | `message` |
| --- | --- | --- | --- | --- |
| `approved` | Tiene el plan aprobado (el de alimentación, además, de una clienta dentro del alcance de bienestar y con su plan armado) | `member_plans` (ejercicio) / `member_nutrition_plan` (alimentación) | Ver mi plan | «Tu plan de … está aprobado y listo para usar.» |
| `generating` | Hay un trabajo en cola o en curso | `member_plan_request_status` | Ver cómo va | «Estamos armando el borrador de tu plan. Esto puede tardar uno o dos minutos.» |
| `in_review` | Hay un borrador esperando al entrenador | `None` | `None` | «Tu entrenador está revisando tu plan. Te avisaremos cuando esté aprobado.» |
| `missing_steps` | Faltan pasos para ese tipo (ejercicio: los de `scope` `all`; alimentación: también `activity`) | la dirección del primer paso pendiente de ese tipo | el `cta` de ese paso | «Te faltan pasos para pedir tu plan de …» (para alimentación, si solo falta `activity`: el texto de `D07`) |
| `quota` | Solo falta el cupo (`D05`) | `None` | `None` | el texto de `D05` |
| `blocked` | Bandera roja, sin ejercicios o alimentos compatibles, o fuera del alcance de bienestar | `None` | `None` | Genérico, **sin diagnósticos** y **sin decir que el entrenador ya fue avisado** (ver abajo) |
| `ready` | Puede pedirlo | `member_plan_request?kind=exercise` / `?kind=nutrition` | Pedir mi plan de ejercicio / alimentación | «Ya puedes pedir tu plan de …. Tu entrenador lo revisa antes de que lo veas.» |

Decisiones que el frontend puede dar por hechas:

- Si ya hay un plan **aprobado**, ese estado manda sobre `generating` e `in_review` (lo que la clienta puede ver hoy es lo que cuenta; un borrador nuevo no tapa su plan vigente).
- Texto de `blocked` (decisión de la persona dueña, 2026-11-20). En el panel la clienta todavía **no pidió nada**: no hay trabajo ni aviso al entrenador, así que el camino **no** reutiliza `BLOCKED_MESSAGE` («Tu entrenador ya fue avisado y te contactará»), que solo es cierto en la pantalla del trabajo, donde sí se avisó (esa sigue igual). Lo mismo vale para «Pedir mi plan», que muestra el texto junto al botón «Avisar a mi entrenador»: antes de pulsarlo no se avisó. Una sola función los arma, `journey.blocked_message(assessment, on_request_screen=False)`, con dos cierres según la pantalla:

  | Causa | Panel (`journey.plans.<tipo>.message`) | «Pedir mi plan» (`eligibility.blocked_message`) |
  | --- | --- | --- |
  | Bandera roja (genérico) | «Por la información de salud que registraste, lo más seguro es que tu médico o nutricionista te valore antes de armar un plan. **Habla con tu entrenador.**» | «…antes de armar un plan. **Usa el botón para avisar a tu entrenador.**» |
  | Pocos ejercicios compatibles | «Por ahora no hay suficientes ejercicios compatibles contigo. Habla con tu entrenador para armar tu plan.» | «Por ahora no hay suficientes ejercicios compatibles contigo. Usa el botón para avisar a tu entrenador.» |
  | Pocos alimentos compatibles | «Por ahora no hay suficientes alimentos compatibles contigo. Habla con tu entrenador para armar tu guía de alimentación.» | «Por ahora no hay suficientes alimentos compatibles contigo. Usa el botón para avisar a tu entrenador.» |
  | Alimentación fuera del alcance de bienestar | «Los menús de bienestar no cubren esta solicitud. La alimentación clínica está pausada.» (sin cambios) | igual (sin cambios) |

  Después de pulsar «Avisar a mi entrenador» (se crea el trabajo `blocked` y sí se avisa), la pantalla de estado del trabajo conserva `BLOCKED_MESSAGE` y el resto de sus textos. Pruebas: ningún texto del panel ni de «Pedir mi plan» contiene «ya fue avisado» ni «te contactará», y la pantalla de estado tras pulsar sí conserva el original.
- `missing_steps` manda sobre `quota` y `blocked`: primero se completan los pasos.
- Los estados `approved`, `generating` e `in_review` no dependen de que los pasos estén completos: se muestran aunque un paso (p. ej. un consentimiento retirado después) esté pendiente.
- Sugerencia de pantalla: ocultar «Tu camino» cuando los dos planes están `approved`.

## 3. `next` seguro en las pantallas a donde manda el camino

Regla común: `next` se lee del POST o de la URL, y **solo se acepta una ruta interna** (empieza con `/`, no con `//`, y pasa `url_has_allowed_host_and_scheme` con el host de la petición). Un `next` externo o malformado se ignora y rige el destino de siempre. Ayudante único: `apps/health/navigation.py` (`safe_next`, `with_next`).

| Pantalla (nombre de URL) | Al terminar con éxito, sin `next` | Con `next` válido |
| --- | --- | --- |
| `member_consent` (otorgar) y `member_consent_revoke` | `member_measurement_list` | `next` (ya existía) |
| `member_health_profile` (POST: guardar la ficha) | `member_health_profile` | `next` |
| `member_health_profile_confirm` (POST) | `member_health_profile` | `next` |
| `member_measurement_add` (POST: registrar medida) | `member_measurement_list` | `next` |

- Si falta el consentimiento de salud y la clienta es mandada a `member_consent`, el `next` de la ficha y de las medidas **se conserva** (vuelve a la pantalla de origen y de ahí a `next`).
- Contexto para las plantillas: `next` (texto; vacío si no hay uno válido) en `member_health_profile` (formulario y resumen) y en `member_measurement_add`. **Cada formulario que envía un POST debe repetirlo** como `<input type="hidden" name="next" value="{{ next }}">` cuando `next` no esté vacío: el asistente de la ficha envía a `action_url` (sin la query) y la confirmación de la ficha es otro formulario. El de medidas envía a la misma URL y conserva la query, pero conviene el campo oculto igual.
- En el modo entrenador (`trainer_health_profile_edit`) no hay `next`: el contexto lo trae vacío.

## 4. Integración

- `dashboard` (`accounts/member_dashboard.html`): `journey = client_journey(request.user)` (vuelve al panel).
- `member_plan_request` (`plans/request.html`): `journey = client_journey(request.user, '/plans/request/?kind=<kind>', base=...)`. **Se conserva `eligibility` tal cual** (la lista «Antes de pedir tu plan te falta» no cambia).
- Cuando `journey` es `None`, las plantillas no dibujan nada nuevo.

## 5. Privacidad, seguridad y rendimiento

- Todo sobre `request.user`; ninguna ruta nueva y ninguna recibe el id de otra persona.
- El panel no cambia sus cabeceras (no se agrega `no-store`). El contexto solo trae estados y textos genéricos: ningún dato de salud ni código de bandera.
- Una sola evaluación de requisitos por tipo de plan, con lo común (ficha, medidas, banderas, consentimientos, cupo) calculado una vez (`services.assess_base`) y compartido por las dos evaluaciones. Si el estado del plan ya lo decide un plan aprobado, un trabajo o un borrador, no se corre el filtro del catálogo ni el cálculo de energía.
- Consultas que el camino añade al panel (medidas el 2026-11-20 en las pruebas; el panel solo hace 10): **11** para una clienta nueva o con pocos pasos, **14** cuando ya puede pedir los dos planes (el caso más caro: filtra los dos catálogos), **12** con los dos planes aprobados. No crecen con la cantidad de planes ni de trabajos (dos consultas con filtro por estado, sin importar el historial). `apps/plans/tests/test_journey.py::QueryCountTests` fija el tope en 16.
- Sin llamadas a la IA, sin red, sin trabajos nuevos.

## 6. Pruebas

`apps/plans/tests/test_journey.py` (lógica, `next`, panel y consultas). Las plantillas las prueba el frontend en `apps/plans/tests/test_journey_templates.py`.
