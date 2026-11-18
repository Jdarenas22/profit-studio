# Precisión del catálogo alimenticio USDA

Fecha de consulta y comprobación: 2026-11-18. Alcance: demostración universitaria,
cuentas ficticias y base `.demo/demo.sqlite3`. No se despliega ni se migra producción.

## Resultado

Se revisaron los 90 nombres del catálogo inicial: 70 tienen una referencia de composición
USDA identificada y 20 necesitan concretar variedad, receta o preparación. En la demo,
Gemini y el generador simulado solo reciben las 70 referencias cuya identidad, estado,
composición y versión coinciden con el registro local. No se inventan valores para completar
los 20 pendientes. La revisión es de correspondencia de datos, no una certificación profesional.

Los perfiles son referencias genéricas estadounidenses y no equivalen necesariamente a una
marca o receta colombiana. La pantalla explica el perfil usado: por ejemplo, leche al 2 %,
contramuslo asado sin piel o atún claro en lata, en agua y escurrido. No acreditan ausencia
de alérgenos ni certificación sin gluten. Los perfiles clínicos permanecen pausados.

## Qué cambió

- Registro reproducible en `apps/plans/data/usda_reference.json`: FDC ID, descripción exacta,
  preparación, etiqueta española y evidencia de nutrientes con hash de integridad.
- Se rechazan contradicciones crudo/cocido, duplicados de nutrientes con cifras distintas,
  identificadores o unidades inválidos y modificaciones locales incompatibles con la fuente.
- Valores por **100 g**: energía en kcal; macronutrientes, fibra y azúcares en g; sodio en mg.
  Las porciones importadas se muestran en gramos. No se presupone que un vaso, una pieza o
  1 ml equivalgan a un peso determinado sin una fuente que lo respalde.
- Un nutriente ausente permanece desconocido (`null`), nunca cero. Ninguna de las 70
  referencias aporta azúcares añadidos en esta selección; no se promete ese cálculo.
- Gemini recibe composición y unidades; Django sigue calculando porciones y totales.
  El resultado conserva por ingrediente la referencia y el perfil usados al crearlo.
- Actualizar el catálogo no reescribe planes históricos. La cobertura de un plan antiguo
  no pasa a verificada automáticamente; hay que crear y revisar un nuevo borrador.
- La importación es atómica, conserva revisiones y no sobrescribe validaciones profesionales.
  Una revisión de fuente diferente requiere evaluación explícita; no se rebaja silenciosamente
  a una versión anterior. Fuera de la demo, las nuevas importaciones quedan inactivas.

## Uso

Desde el proyecto, con su entorno Python instalado:

```powershell
# Sin red, sin clave y sin escribir en la base:
python manage.py audit_food_catalog --settings=config.settings.demo
python manage.py load_usda_catalog --settings=config.settings.demo

# Aplicar exclusivamente a la muestra aislada; operación repetible:
python manage.py load_usda_catalog --apply --activate-demo --settings=config.settings.demo

# Revisar además la base académica:
python manage.py audit_food_catalog --database --settings=config.settings.demo
```

`scripts/start-demo.ps1` sincroniza este catálogo antes de servir la muestra.
`--activate-demo` se rechaza fuera de la configuración académica y sin `--apply`.
`--apply` sin esa opción importa inactivo. No usar estos comandos con la base de producción.
La vista previa predeterminada y la auditoría sin `--database` no consultan la base.

Para ampliar los 20 pendientes: precisar alimento/variedad/método y peso comestible; buscar
FDC; verificar la descripción y los ingredientes de la receta; importar primero inactivo;
revisar alergias y composición antes de activarlo. Una consulta que coincida en nombre no
basta. La yuca cocida y la clara cocida encontradas incluyen sal añadida; el queso fresco
mexicano no demuestra equivalencia con queso campesino; cacao 70–85 % no especifica 70 %.

Para cambiar una referencia estricta, actualizar de forma revisada el registro, su evidencia,
su etiqueta y las pruebas, y aprobar la revisión de base correspondiente. Reiniciar los
procesos para refrescar las etiquetas cacheadas. No editar cantidades a mano para que pase
un hash ni eliminar revisiones/planes para evitar el control.

## Comprobación

- Suite completa: **1.527 pruebas aprobadas**, incluyendo identidad del atún en agua,
  integridad, preparación, unidades, importación repetible, reversión atómica, aislamiento
  de demo, protección de revisiones y conservación de etiquetas históricas.
- Tres perfiles ficticios (reducción de grasa, ganancia muscular y mantenimiento) con
  generación **simulada**: 5 comidas y 15 opciones por perfil; cobertura de fuente completa
  en las 45 opciones y páginas de cliente HTTP 200. Esto no es una nueva prueba de Gemini real.
- Sodio calculable en 45/45 opciones; azúcares totales en 39/45; azúcares añadidos
  desconocidos en 45/45. Son resultados de esa muestra, no una garantía de cualquier menú.
- Sin cambios de modelos ni migraciones. Las pruebas automatizadas no consumen APIs reales.

Durante la comprobación aislada se detectó y corrigió una asociación de atún fresco
cocinado: el catálogo final usa **FDC 173709**, atún claro en agua y escurrido. El prototipo
descartado quedó inactivo con su historial en la base de prueba; no se aplicó a la demo principal.

## Procedencia

La selección incluye 69 registros SR Legacy y uno Foundation. SR Legacy es una colección
histórica (versión final de 2018); fecha de descarga reciente no significa análisis reciente.
Se conserva el valor publicado, no se estima energía ausente a partir de macros.

Fuente: USDA Agricultural Research Service, FoodData Central; datos de dominio público
(CC0). Documentación oficial: [guía API](https://fdc.nal.usda.gov/api-guide/) y
[tipos de datos](https://fdc.nal.usda.gov/data-documentation/).
Para buscar manualmente: [Food Search](https://fdc.nal.usda.gov/food-search/).
El endpoint de cada registro es `https://api.nal.usda.gov/fdc/v1/food/{fdcId}`;
las consultas remotas requieren la clave privada del backend, enviada por el cliente FDC.
El paquete incluido permite la demo sin nuevas consultas USDA ni claves en el navegador.

## Inventario revisado

| Alimento local | Estado | FDC ID | Perfil utilizado o precisión pendiente |
| --- | --- | --- | --- |
| Arroz blanco cocido | Referencia | 169757 | Grano largo, cocido sin sal |
| Arroz integral cocido | Referencia | 169704 | Grano largo, cocido |
| Avena en hojuelas (cruda) | Referencia | 173904 | Avena seca sin fortificar |
| Arepa de maíz | Pendiente | — | Definir receta y cocción. Se descartó la arepa de restaurante como equivalencia universal de una arepa casera. |
| Pan integral tajado | Referencia | 172688 | Pan integral comercial, sin tostar |
| Pan blanco tajado | Referencia | 174924 | Pan blanco comercial, sin tostar |
| Pasta cocida | Referencia | 168928 | Cocida sin sal, no enriquecida |
| Tortilla de maíz | Referencia | 175036 | Tortilla de maíz como se vende; no frita |
| Galletas de soda | Referencia | 172746 | Galleta salada genérica de referencia |
| Quinua cocida | Referencia | 168917 | Quinua cocida |
| Choclo (maíz tierno) cocido | Pendiente | — | Confirmar variedad: el maíz dulce amarillo de USDA no identifica cualquier choclo colombiano. |
| Papa criolla cocida | Pendiente | — | Se necesita una referencia de la variedad criolla; no se sustituye por papa genérica. |
| Papa pastusa cocida | Pendiente | — | Se necesita una referencia de la variedad pastusa; no se sustituye por papa genérica. |
| Yuca cocida | Pendiente | — | El candidato FNDDS 2709564 incorpora aceite y sal; no equivale a yuca hervida sin añadidos. El SR 169985 es crudo. |
| Plátano maduro asado o cocido | Pendiente | — | Separar madurez y método de cocción; no usar plátano verde hervido para representar plátano maduro. |
| Plátano verde cocido | Referencia | 168216 | Plátano verde hervido |
| Arracacha cocida | Pendiente | — | No se confirmó una referencia USDA de esta especie y preparación; no se sustituye por otro tubérculo. |
| Ñame cocido | Referencia | 170072 | Cocido sin sal, según la referencia USDA |
| Batata (camote) cocida | Referencia | 168484 | Hervida sin piel |
| Ahuyama cocida | Pendiente | — | Confirmar especie/variedad y preparación; pumpkin genérico no demuestra identidad de la ahuyama disponible. |
| Tomate | Referencia | 170457 | Tomate rojo maduro, crudo |
| Cebolla cabezona | Referencia | 170000 | Cebolla cruda |
| Lechuga | Referencia | 169249 | Lechuga de hoja verde, cruda |
| Zanahoria cruda | Referencia | 170393 | Zanahoria cruda |
| Pepino cohombro | Referencia | 168409 | Pepino con cáscara, crudo |
| Brócoli cocido | Referencia | 169967 | Hervido, escurrido, sin sal |
| Espinaca cocida | Referencia | 168463 | Hervida, escurrida, sin sal |
| Habichuela cocida | Referencia | 169141 | Habichuela verde hervida, sin sal |
| Repollo crudo | Referencia | 169975 | Repollo crudo de referencia |
| Remolacha cocida | Referencia | 169146 | Remolacha hervida y escurrida |
| Pimentón | Pendiente | — | Precisar color/variedad; las referencias roja, verde y amarilla difieren. |
| Coliflor cocida | Referencia | 170397 | Hervida, escurrida, sin sal |
| Champiñones | Referencia | 169251 | Champiñones blancos, crudos |
| Calabacín cocido | Referencia | 169292 | Con piel, hervido y sin sal |
| Banano | Referencia | 173944 | Banano crudo |
| Manzana | Referencia | 171688 | Cruda con piel |
| Naranja | Referencia | 169097 | Naranja cruda, variedades comerciales |
| Mandarina | Referencia | 169105 | Mandarina cruda |
| Papaya | Referencia | 169926 | Papaya cruda |
| Piña | Referencia | 169124 | Piña cruda |
| Mango | Referencia | 169910 | Mango crudo |
| Fresa | Referencia | 167762 | Fresa cruda |
| Mora | Pendiente | — | Confirmar especie: la mora andina no se identifica automáticamente con blackberry genérico estadounidense. |
| Sandía | Referencia | 167765 | Sandía cruda |
| Melón | Pendiente | — | Definir variedad: cantalupo, honeydew y casaba tienen composiciones diferentes. |
| Pera | Referencia | 169118 | Pera cruda |
| Uvas | Referencia | 174683 | Tipo europeo, rojas o verdes |
| Guayaba | Referencia | 173044 | Guayaba común, cruda |
| Kiwi | Referencia | 168153 | Kiwi verde, crudo |
| Leche entera | Referencia | 171265 | Entera, referencia de 3,25 % de grasa |
| Leche semidescremada | Referencia | 171267 | Referencia de 2 % de grasa |
| Leche descremada | Referencia | 171269 | Descremada, con vitaminas A y D |
| Yogur natural sin azúcar | Referencia | 171284 | Natural entero; no es yogur descremado |
| Yogur griego natural | Referencia | 171304 | Natural entero, referencia de 5 % de grasa |
| Queso fresco campesino | Pendiente | — | Requiere etiqueta o referencia colombiana del producto; queso fresco mexicano no demuestra equivalencia. |
| Queso mozzarella semidescremado | Referencia | 171244 | Baja humedad, parcialmente descremado |
| Queso cottage | Referencia | 172179 | Tipo cremoso, no descremado |
| Bebida de soya sin azúcar | Referencia | 1999630 | Sin azúcar, natural, de larga vida |
| Bebida de almendras sin azúcar | Referencia | 174832 | Sin azúcar, de larga vida |
| Pechuga de pollo cocida sin piel | Referencia | 171477 | Pechuga asada, solo carne, sin piel |
| Muslo de pollo cocido sin piel | Referencia | 172388 | Parte superior (contramuslo), asada y sin piel |
| Carne de res magra cocida | Pendiente | — | Definir corte, grasa separable y método de cocción. |
| Carne molida de res magra cocida | Pendiente | — | Precisar porcentaje de grasa y método; no asumir 90/10 como composición de toda carne magra. |
| Lomo de cerdo magro cocido | Referencia | 168250 | Solomillo magro, asado |
| Pechuga de pavo cocida | Referencia | 171496 | Pechuga asada, solo carne |
| Tilapia cocida | Referencia | 175177 | Tilapia cocida con calor seco |
| Atún en agua (escurrido) | Referencia | 173709 | Atún claro en lata, en agua y escurrido |
| Salmón cocido | Pendiente | — | Precisar especie, origen silvestre/cría y método antes de fijar el perfil. |
| Camarón cocido | Referencia | 175180 | Camarón cocido de referencia |
| Huevo entero cocido | Referencia | 173424 | Huevo entero duro (hervido) |
| Clara de huevo cocida | Pendiente | — | El candidato FNDDS 2707170 incorpora sal. No usar clara cruda, deshidratada ni una mezcla sazonada para representar una clara cocida sin especificación. |
| Fríjol rojo cocido | Referencia | 175194 | Rojo tipo kidney, hervido sin sal |
| Fríjol negro cocido | Referencia | 173735 | Negro, hervido sin sal |
| Lentejas cocidas | Referencia | 172421 | Hervidas sin sal |
| Garbanzos cocidos | Referencia | 173757 | Hervidos sin sal |
| Arveja verde cocida | Referencia | 170420 | Arveja verde, hervida sin sal |
| Tofu firme | Referencia | 172475 | Firme, preparado con sulfato de calcio |
| Hummus | Referencia | 172454 | Preparación casera de referencia |
| Aceite de oliva | Referencia | 171413 | Aceite de oliva genérico para cocina |
| Aceite vegetal (girasol o canola) | Pendiente | — | Separar canola y girasol: una mezcla de nombre no identifica su perfil de grasas saturadas. |
| Aguacate | Referencia | 171705 | Crudo, variedades comerciales |
| Almendras | Referencia | 170567 | Almendra natural de referencia |
| Maní tostado sin sal | Referencia | 173806 | Tostado en seco, sin sal |
| Nueces | Referencia | 170187 | Nuez de nogal (tipo inglés) |
| Semillas de chía | Referencia | 170554 | Semillas secas |
| Linaza molida | Referencia | 169414 | Perfil de semilla sin desgrasar |
| Mantequilla de maní sin azúcar | Pendiente | — | Exigir ingredientes/etiqueta sin azúcares añadidos. Un registro sin sal no demuestra ausencia de azúcar añadido. |
| Mantequilla | Pendiente | — | Precisar con o sin sal antes de asignar sodio. |
| Miel de abeja | Referencia | 169640 | Miel de referencia |
| Chocolate oscuro 70 % | Pendiente | — | Requiere producto al 70 % o su etiqueta; no asignar la media del intervalo 70–85 % como si fuera exactamente 70 %. |
