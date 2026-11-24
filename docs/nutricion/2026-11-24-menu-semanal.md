# Menús semanales y variedad

El cambio solicitado sustituye el menú diario reutilizado por cuatro semanas por **siete días
distintos**, con tres opciones por cada horario de comida configurado. Un perfil de cinco
comidas recibe 35 horarios y 105 opciones guardadas. Son alternativas: no se consumen las tres.

## Generación y consumo

Gemini prioriza exclusivamente recetas compatibles del catálogo. Recibe nombres, cocina,
proteína principal y preparación; no recibe identidad, ficha, peso, objetivos calóricos ni
gramos personales. Django calcula porciones, construye la semana y valida cada opción y cada
día antes de guardar el borrador completo. Una receta ajena, duplicada o ausente en la
clasificación del proveedor provoca un reintento limitado; nunca se muestra salida sin validar.

Se hace una llamada por horario, no una por día: cinco llamadas en el caso habitual, además
de los reintentos que sean necesarios. Se conservan el presupuesto diario y el límite de
duración del trabajo. La variedad no se obtiene multiplicando por siete el uso del proveedor.

La planificación favorece recetas aún no usadas y excluye las tres del día anterior para la
misma comida. Una receta puede volver más adelante en la semana si el catálogo compatible
es limitado. No se promete que las 105 opciones sean todas únicas ni se cambian ingredientes
para simular variedad. Si no hay grupos suficientes, se informa `weekly_variety_unavailable`.

## Duración y compatibilidad

La semana comienza al aprobar el entrenador. Se cuenta ese día como primero y el sexto día
posterior como último. Las elecciones solo pueden pertenecer al horario y día indicados.
La lista de compras considera los días restantes de esa semana y las elecciones reales;
para comidas sin elección usa una opción indicada como supuesto.

Los planes existentes conservan sus recetas, autorizaciones, tablas nutricionales y vigencia;
no se reescriben automáticamente. Para obtener el formato semanal se genera y aprueba un plan
nuevo. La migración `plans 0007` añade duración y día del menú sin borrar datos. La clonación
conserva ambos campos y vuelve a pasar por revisión.

## Criterios alimenticios

La fórmula `mifflin_st_jeor/2` conserva el cálculo local de energía y los límites de déficit
y superávit. Usa proteína por peso de referencia y objetivo (1,2 g/kg general, 1,6 en pérdida
de peso, 1,8 en ganancia muscular, techo 2,0), con porcentajes orientativos de proteína 15–25 %,
carbohidratos 45–60 % y grasas 20–35 %. Los porcentajes no se suman por sus máximos ni se
fuerzan por encima del techo de proteína por kilogramo. Son objetivos estimados; las tablas
de las alternativas muestran su composición real y pueden diferir del objetivo del día.

La aplicación no dispone de medición del gasto de cada sesión ni de una clasificación
completa de cargas de resistencia prolongada; no asigna automáticamente 6–10 g/kg de
carbohidratos a partir de un simple número de entrenamientos. No se habilitan planes clínicos.

El método visual del plato se presenta como orientación para almuerzo y cena, separado de
las porciones calculadas. Se recomienda repartir proteína entre comidas y mantener agua
disponible alrededor del entrenamiento, sin inventar horarios personales de entrenamiento.

Fuentes consultadas el 24 de noviembre de 2026:

- [MedlinePlus / National Library of Medicine: Nutrition and athletic performance](https://medlineplus.gov/ency/article/002458.htm): proteína 1,2–2,0 g/kg, grasas 20–35 % y carbohidratos según carga. Su revisión visible es del 20 de agosto de 2026.
- [Posición conjunta AND/DC/ACSM (2016), ficha bibliográfica](https://pubmed.ncbi.nlm.nih.gov/26920240/).

La clasificación de recetas mediante IA no constituye una validación profesional de la
fórmula ni una garantía de resultados. Se conservan restricciones, consentimiento y revisión.
