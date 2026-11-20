# Menús de bienestar con recetas reales y Gemini

Implementado el 17 de noviembre de 2026. Este documento actualiza el alcance de alimentación del contrato anterior: dietas clínicas y asesoramiento para tratar enfermedades quedan pausados.

## Alcance actual

Se generan borradores de menús cotidianos para objetivos de entrenamiento: pérdida de peso/grasa, mantenimiento y ganancia muscular según los objetivos ya existentes. El motor Python estima energía y macronutrientes; Gemini selecciona entre preparaciones completas compatibles.

No se quitaron restricciones de alimentos, permisos, revisión del entrenador, consentimiento ni controles del proveedor. Cambió el servicio ofrecido: ya no se genera automáticamente una adaptación para condiciones médicas. Si la ficha declara condiciones, medicamentos, embarazo/lactancia/posparto o antecedentes de dificultades alimentarias (incluida respuesta reservada), la generación de alimentación queda bloqueada antes de contactar al proveedor. Un menú anterior deja de mostrarse y no puede aprobarse para una ficha fuera de ese alcance. Los registros previos no se borran.

Las alergias e intolerancias se conservan como exclusiones de ingredientes; el sistema no recomienda tratamientos. Las pantallas de salud y sus datos existentes no se eliminaron: siguen siendo necesarios para el resto de funciones del proyecto.

## Recetario y variedad

El catálogo técnico contiene 117 plantillas de preparación en apps/plans/rules/recipes.py, versión everyday-world/2 (la versión 1, del 17 de noviembre, tenía 52; ver "Variedad dentro del día"). Estos son los estilos de la versión inicial:

- Arepa con huevo y aguacate; arepa con queso y huevo.
- Avena con yogur, fruta y frutos secos; bowls con soya.
- Tostadas, sándwich frío de atún, tortillas.
- Arroz con proteína y ensalada; yuca, papa o plátano con acompañamientos.
- Bowls de quinua, tacos, pasta con atún y preparaciones de inspiración mediterránea.
- Lentejas, fríjoles, ensalada de garbanzos y opciones con tofu.

Cada plantilla define nombre, estilo culinario, tiempos de comida, ingredientes exactos, intervalos de porción y preparación. No se limita a cocina colombiana; los ingredientes deben existir en el catálogo local permitido. Las cifras nutricionales siguen siendo las del catálogo Food del proyecto: este cambio no las convierte en datos verificados ni importa nuevas tablas de nutrientes.

Una receta desaparece de las opciones si falta un ingrediente o si está excluido. No se reemplaza arbitrariamente por otro alimento. Para quienes declaran que no cocinan se ofrecen solo montajes fríos/sin cocción etiquetados como tales; se presupone que los ingredientes indicados como cocidos se consiguen listos. Las porciones respetan los límites técnicos y las comprobaciones de energía y macronutrientes existentes.

## Qué cambió en la generación

1. Se verifican acceso, consentimientos, alcance y datos necesarios.
2. Se calculan los objetivos en Python usando el motor ya existente.
3. Se filtra Food por dieta, alergias, trazas, intolerancias y disponibilidad.
4. Para el tiempo de comida, se buscan recetas completas y porciones compatibles.
5. Antes de llamar a Gemini se comprueba que hay suficientes opciones distintas.
6. Gemini recibe recipe_options, una lista de nombres y porciones autorizados, y devuelve opciones con el contrato meal-slot/1. El esquema limita los nombres a los ofrecidos.
7. El backend rechaza cambios de ingredientes o porciones con V09_RECIPE. Recalcula totales y mantiene energía, macros, variedad y revisión de texto. La preparación final se toma del recetario.
8. Se revisa el conjunto del día y se guarda el borrador. El entrenador lo revisa y aprueba antes de publicarlo.

Los prompts son meal/5 y meal/6 (con historial); antes eran meal/3 y meal/4. El catálogo debe versionarse junto con esos prompts cuando se cambien recetas o reglas de porción. No se actualizan automáticamente menús ya aprobados: se debe generar un borrador nuevo para usar el recetario.

El simulador también selecciona recetas completas. La ruta normal de generación ya no utiliza el combinador aleatorio de alimentos. La función anterior queda únicamente para pruebas unitarias que ejercitan el contrato antiguo sin recipe_options.

Cuando no hay tres opciones válidas, no se improvisan combinaciones ni se relajan alergias: se informa que no hay suficientes recetas compatibles. Puede ocurrir con catálogos muy pequeños, restricciones simultáneas o porciones fuera de lo que cubren las recetas iniciales. Ampliar el recetario es una tarea continua.

## Variedad dentro del día (everyday-world/2)

Actualizado el 19 de noviembre de 2026. Con Gemini real y perfiles ficticios se midió que, en menús de cinco comidas, el almuerzo y la cena ofrecían casi siempre los mismos tres platos de pollo y que la media mañana y la merienda también se repetían. Causas y cambios:

1. **Recetario más amplio.** De 52 a 117 plantillas. Por tiempo de comida (una receta puede servir en varios): desayuno 13 a 30, media mañana 19 a 37, merienda 19 a 37, almuerzo 29 a 68, cena 29 a 68. Hay proteínas nuevas (camarón, cerdo, pavo, muslo de pollo, carne molida, clara de huevo, fríjol negro, arveja, más legumbres y tofu) y acompañantes nuevos (papa criolla, arracacha, ñame, plátano verde, ahuyama, choclo, habichuela, coliflor, champiñones, pimentón, cebolla, repollo, remolacha, frutas, leche, yogur natural, linaza, pan blanco, galletas de soda). Ya no queda ningún alimento del catálogo sin usar. Las recetas sin cocción (para quien declara que no cocina) pasaron a ser 16 de desayuno, 31 de media mañana y merienda y 11 de almuerzo y cena; antes no quedaba ninguna de almuerzo o cena con objetivos altos de proteína y el trabajo terminaba en `recipes_unavailable`.
2. **Orden sin sesgo.** La lista que viaja a Gemini ya no sale siempre en el orden del catálogo (el pollo primero): se reordena de forma determinista con el identificador del trabajo y el código de la comida (`candidates(..., seed=job.job_ref)`). El mismo trabajo y comida dan el mismo orden; cada trabajo y cada comida, otro.
3. **Nombres no repetidos en el día (restricción dura, con respaldo).** Los nombres usados en comidas anteriores del día salen de `recipe_options` y del enum del esquema, de modo que el modelo no puede escogerlos (`recipes.offer`). Si al excluirlos no queda un grupo válido de tres (perfiles muy restringidos: vegetarianas, veganas, alergias, sin cocción), se vuelve a la lista completa para no romper la generación; `avoid_repeating_names` sigue viajando como sugerencia. Las opciones existentes de la comida y las que se parecen demasiado a ellas nunca se ofrecen.
4. **Proteína principal distinta.** Cada receta declara su proteína principal (`main_protein`: pollo, res, cerdo, pavo, tilapia, salmón, atún, camarón, huevo, tofu, lentejas, fríjol, garbanzo, yogur, queso, leche, bebida de soya, maní). `select_options` prefiere grupos sin proteína repetida y, si no existe ninguno, usa la regla de variedad de siempre. Cuando existe un grupo así, el validador lo exige (V06_VARIETY, `SlotRules.distinct_protein`): la salida de Gemini con tres platos de pollo se rechaza y se reintenta una vez enviando solo el código. `main_protein` viaja dentro de cada receta ofrecida (no es parte de la opción que devuelve el modelo).
5. **"Otra opción".** Usa el mismo mecanismo: no repite platos de las otras comidas del plan y prefiere una proteína que la comida todavía no tiene.
6. **Prompt.** Se añadió la instrucción de variar `main_protein` y de no escoger siempre las primeras recetas; las versiones pasan a meal/5 y meal/6.
7. **Tope de recetas por llamada.** Los nombres ofrecidos van en el enum del esquema y la documentación de salida estructurada de Gemini (consultada el 19 de noviembre de 2026) advierte que los esquemas muy grandes pueden rechazarse con `400 INVALID_ARGUMENT` y recomienda recortar los enum largos, sin dar un número. Antes el máximo era 29 recetas por comida; ahora la lista se recorta a `MAX_OFFERED = 36` (constante en `rules/recipes.py`), conservando el orden de cada trabajo y repartiendo el cupo entre proteínas principales para no perder variedad. Como el orden cambia en cada trabajo, el recorte también cambia: no hay recetas "escondidas" de forma permanente. Si el proveedor llegara a rechazar el esquema, se baja esa constante.

Pendiente: las cifras del catálogo `Food` siguen siendo aproximadas y sin validar; las recetas nuevas heredan esa limitación. Los perfiles veganos con objetivos altos de proteína siguen teniendo pocas opciones por comida (casi solo tofu), y ahí se repiten platos entre media mañana, merienda y desayuno por el respaldo. No se ha probado con Gemini real: conviene vigilar cuántos trabajos terminan en `validation_failed:V06_VARIETY` (el modelo repite la proteína dos veces seguidas).

## Cinco tiempos y entrenamiento

Las fichas nuevas proponen desayuno, media mañana, almuerzo, merienda y cena. El usuario puede mantener tres a cinco tiempos; los horarios y selecciones guardados anteriormente se conservan.

El payload de alimentación incluye ahora frecuencia semanal, duración de sesión y nivel de experiencia. El objetivo y nivel de actividad ya alimentaban el cálculo energético. Esta entrega no agrega un selector nuevo de modalidad deportiva ni un cálculo específico por deporte; no debe confundirse la duración con una medición real del gasto.

Los datos de condiciones y medicamentos no se envían a Gemini. Las notas libres del entrenador tampoco se envían en alimentación, para evitar introducir información clínica o instrucciones que cambien el alcance. Esas notas se conservan en el trabajo local; el flujo de ejercicio mantiene sus notas como antes.

## API y configuración

Se reutiliza la integración REST existente en apps/plans/ai/gemini.py; no se necesita otra API de cálculo ni se añadieron dependencias. La clave solo vive en el servidor.

Variables ya existentes para usar Gemini real:

- AI_PLANS_MODE=live
- GEMINI_API_KEY: clave del proyecto, guardada como secreto.
- GEMINI_MODEL: identificador de un modelo disponible y compatible con salida JSON estructurada.
- GEMINI_BILLING_CONFIRMED=true: confirmar la facturación del proyecto.
- GEMINI_SCHEMA_FIELD=responseJsonSchema, salvo que la versión del proveedor requiera el modo alternativo ya admitido.
- HEALTH_FEATURES_ENABLED y los consentimientos de salud/IA: los requisitos existentes de disponibilidad se conservan.

No se activó live ni se modificaron secretos o variables de producción. Si la página está en simulated, seguirá sin hacer llamadas a Gemini, pero sus ejemplos nuevos usan el recetario.

Para comprobar conectividad, el comando existente check_gemini usa datos ficticios y realiza una llamada real con costo potencial; no fue ejecutado en esta entrega. Ese comando comprueba el cliente/formato general: la nueva selección de recetas se prueba a través del flujo de alimentación en un entorno de ensayo.

## Uso

1. Crear o revisar la ficha de una persona dentro del alcance de bienestar, con objetivo, medidas, preferencias y tiempos.
2. Pedir un nuevo plan de alimentación desde el flujo habitual.
3. Revisar el borrador del entrenador: recetas, porciones y compatibilidad.
4. Aprobarlo para que aparezca en la cuenta de la clienta.
5. Pedir otra opción usa el mismo recetario y excluye las opciones ya activas.

No hay migraciones nuevas por estos cambios. Conservar las migraciones pendientes de la API Ruffier que ya existían en el trabajo local.

## Verificación

Las pruebas nuevas están en apps/plans/tests/test_wellness_recipes.py: platos internacionales, ingredientes permitidos, restricciones de cocina, independencia de los datos del simulador, rechazo de platos inventados, generación de cinco comidas y pausa del alcance clínico antes de la red y al aprobar/publicar.

Las pruebas anteriores se actualizaron únicamente donde cambió el contrato: versiones del prompt, campos nuevos del payload, pausa de condiciones clínicas, texto de bienestar y cinco tiempos iniciales.

Resultado final: pasaron 184 pruebas de menús, simulador, prompts, vistas de alimentación y formularios
de perfil, incluidas las 8 pruebas nuevas de recetas/bienestar. Se comprobaron pérdida de peso y
ganancia muscular con cinco comidas, así como las restricciones existentes de ingredientes.
Django check, makemigrations --check --dry-run y git diff --check terminaron sin problemas.
Tailwind se recompiló correctamente. No se realizó llamada real a Gemini, prueba visual en navegador,
commit ni despliegue.

### Verificación de everyday-world/2 (19 de noviembre de 2026)

- Pruebas nuevas en apps/plans/tests/test_recipe_variety.py (37): catálogo (nombres únicos, alimentos exactos de foods_data.py, texto sin cantidades ni recomendaciones prohibidas, ningún plato permitido tropieza con la revisión de alérgenos y dieta, alimentos antes sin uso, recetas sin cocción por comida), orden determinista por trabajo y comida, selección y oferta (nombres excluidos, respaldo a la lista completa, proteína distinta, tope de recetas), validación V06 por proteína repetida y el flujo completo con el simulador (almuerzo y cena sin platos comunes en pérdida de grasa, ganancia muscular y mantenimiento; reintento cuando el modelo repite la proteína; respaldo con muy pocas recetas; "otra opción").
- Resultado: apps.plans 636 pruebas OK; suite completa con `-W error::DeprecationWarning` 1547 pruebas OK; `check` y `makemigrations --check --dry-run` sin cambios. No hay migraciones nuevas.
- Contrapruebas (se quitó una pieza y la prueba correspondiente falló): ignorar los nombres a evitar, quitar la semilla, no exigir proteína distinta, que `select_options` ignore la proteína, quitar el respaldo a la lista completa, quitar V06 por proteína, quitar los nombres del día en "otra opción" y un nombre de receta con la palabra "crema" sin lácteos.
- Experimento local sin red con perfiles ficticios (pérdida de grasa, ganancia muscular, mantenimiento, vegetariana, sin cocción, vegana, alergia a maní y lácteos; cinco comidas): con el simulador y con un "modelo ansioso" que toma las primeras recetas e ignora las sugerencias, el almuerzo y la cena no comparten ningún plato en los siete perfiles. Con el código anterior, ese mismo modelo repetía exactamente los tres platos de pollo en almuerzo y cena y cuatro de los siete perfiles (vegetariana, sin cocción, vegana, alergia) terminaban en `recipes_unavailable`.
- Nada de esto se probó con Gemini real ni se desplegó.
