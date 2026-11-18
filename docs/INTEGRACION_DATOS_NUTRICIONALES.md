# Integración de datos nutricionales: guía paso a paso

Fecha: 17 de noviembre de 2026. Primera fuente integrada: USDA FoodData Central (FDC).

## Estado de esta entrega

Ya están implementados el cliente REST, buscador, vista previa/importación explícita, historial de fuentes,
campos adicionales de nutrientes y tablas por receta/día. Se aplicó localmente la migración plans.0005
(junto con plans.0004 que estaba pendiente). No se desplegó nada en Railway.

En la implementación de origen, antes del traslado, se consultó realmente USDA con su clave pública DEMO_KEY y se ejecutó el comando nuevo en vista previa,
sin guardar alimentos. No se usó ni se solicitó una clave privada, no se activó Gemini y no se modificó .env.

## 1. Obtener tu clave de USDA

Abre [API Key Signup de FoodData Central](https://fdc.nal.usda.gov/api-key-signup/).
Completa el registro de data.gov que ofrece la página y guarda la clave recibida.
La documentación oficial requiere una clave para las peticiones; la clave de demostración tiene límites
bajos y sirve para explorar, no como configuración del producto.

[Guía oficial de API](https://fdc.nal.usda.gov/api-guide/).

## 2. Guardarla de forma privada

En el .env local del repositorio añade o completa:

```dotenv
FDC_API_KEY=TU_CLAVE_PRIVADA
FDC_TIMEOUT_SECONDS=15
```

TU_CLAVE_PRIVADA es un marcador: reemplázalo tú en el editor. No pegues la clave en el chat, comandos de
terminal, capturas ni archivos versionados. .env.example solo contiene los nombres vacíos de referencia.

La clave FDC es independiente de GEMINI_API_KEY. Django puede importar nutrientes aunque Gemini esté apagado.
Para producción, guardar las variables en Railway → servicio de Django → Variables, durante el despliegue
autorizado. No se configuraron esas variables por esta entrega.

## 3. Abrir la carpeta e iniciar el entorno

En VS Code abre `C:\Proyectos\profit-studio`, que contiene manage.py y CLAUDE.md. Desde su terminal PowerShell:

```powershell
.\.venv\Scripts\python.exe manage.py migrate --settings=config.settings.development
.\.venv\Scripts\python.exe manage.py check --settings=config.settings.development
```

La migración local ya está aplicada; volver a ejecutarla no duplica tablas.
No hace falta instalar una biblioteca nueva: el cliente usa urllib de Python.

## 4. Buscar un alimento

Usa preferiblemente una descripción precisa en inglés, incluyendo su estado:

```powershell
.\.venv\Scripts\python.exe manage.py fdc_search --query "chicken breast cooked" --settings=config.settings.development
```

El resultado muestra hasta diez candidatos con fdc_id, descripción, tipo de dato y marca.
Este comando no guarda nada. No se selecciona automáticamente el primer resultado.

Confirma lo siguiente:
- Alimento y parte comestible correctos.
- Crudo, cocido, escurrido o preparado.
- Con o sin sal; con o sin piel.
- Producto genérico o marca concreta.
- Si se quiere un producto colombiano, que el registro corresponda al producto real, no a otro de nombre parecido.

Foundation Foods y SR Legacy describen alimentos genéricos; Branded contiene datos aportados por fabricantes.
Las etiquetas comerciales no son idénticas a un análisis de laboratorio. El origen queda guardado.

## 5. Ver una importación antes de guardar

Ejemplo reproducible: FDC 171077 es pechuga de pollo CRUDA, no cocida. Solo demuestra el proceso.
No vincularlo a la pechuga cocida que usan las recetas actuales.

```powershell
.\.venv\Scripts\python.exe manage.py fdc_import --fdc-id 171077 --name "Pechuga de pollo cruda USDA" --group proteina_animal --state raw --settings=config.settings.development
```

Sin --apply, el resultado dice preview_only y no crea ni modifica alimentos.
Muestra descripción, unidades, nutrientes disponibles y huella del registro.

Estados admitidos: raw, cooked, as_sold, prepared. La elección es explícita; el programa no deduce la cocción
a partir de un nombre parecido.

En el alimento de demostración se comprobaron 120 kcal, 22,5 g de proteína y 45 mg de sodio por 100 g.
Por tanto, 150 g del mismo alimento corresponden a 180 kcal, 33,75 g de proteína y 67,5 mg de sodio antes
del redondeo de presentación. No son valores intercambiables con 150 g de pollo cocido.

## 6. Guardar un alimento como pendiente de revisión

Tras revisar la descripción de la vista previa:

```powershell
.\.venv\Scripts\python.exe manage.py fdc_import --fdc-id 171077 --name "Pechuga de pollo cruda USDA" --group proteina_animal --state raw --apply --confirm-description "Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw" --settings=config.settings.development
```

Esta operación sí escribe en la base. El alimento queda INACTIVO y sin marcar como revisado.
El ejemplo crea un ingrediente nuevo; no modifica las recetas cocidas del catálogo.

Para actualizar un registro local concreto, reemplaza --name y --group por --food-id ID_LOCAL.
Obtén ese id desde el administrador. No usar números de ejemplo como si fueran los de tu base.

El importador:
- Rechaza sobrescribir un alimento ya revisado cuando cambió la fuente.
- Rechaza cambiar una vinculación USDA existente por otro fdc_id.
- Rechaza cambiar un estado crudo/cocido ya confirmado.
- Devuelve unchanged si la vinculación, estado y datos son iguales.
- Conserva evidencia en FoodImportRevision.
- Evita que load_foods vuelva a reemplazar una importación por cifras aproximadas.

Para reintentar una importación ya creada, usar --food-id. Reutilizar --name sobre un nombre existente se
rechaza para evitar actualizaciones accidentales.

## 7. Revisar y activar el registro

Entra como superusuaria a /admin/ → Alimentos. Abre el alimento importado.

1. Verifica nombre, estado, descripción y fuente.
2. Revisa la correspondencia con el producto o preparación que se utilizará realmente.
3. Completa Dietas en las que cabe, alérgenos, trazas y medidas caseras con evidencia.
4. Marca las cifras como revisadas con el procedimiento del catálogo y activa el alimento cuando corresponda.

Importar nutrientes no certifica ausencia de gluten, trazas o alérgenos. Esos datos no se deducen
automáticamente de proteínas o carbohidratos. La descripción original de ingredientes, marca, GTIN y país de
mercado se conservan cuando USDA los proporciona, como apoyo para revisar la identidad.

Los registros nuevos no reciben automáticamente etiquetas de dieta. Deben revisarse para que el filtro
de ingredientes pueda utilizarlos.

## 8. Reemplazar gradualmente el catálogo aproximado

Comienza por los ingredientes más usados: arroz cocido, avena, huevo cocido, pollo cocido, fríjoles cocidos,
lentejas, yogur, queso, aceite y frutas.

Para cada uno: buscar → confirmar correspondencia → vista previa → importar → revisar → activar.
No importar masivamente por similitud de nombres. Las marcas y la cocción pueden cambiar mucho el resultado.

El ICBF y las etiquetas de productos vendidos en Colombia pueden complementar la fuente USDA, verificando
sus condiciones de reutilización y el significado de cada nutriente. No se implementó un importador del
ICBF ni un lector automático de etiquetas en esta entrega.

## 9. Cómo llegan los datos a las recetas y a Gemini

El motor usa los ingredientes activos y sus valores por 100 g. Por ingrediente:

nutriente por porción = valor por 100 g × gramos registrados / 100.

Se suman ingredientes para la receta y opciones elegidas para el día. La web y el panel del entrenador
muestran fibra, azúcares totales, añadidos, grasa saturada y sodio cuando están disponibles.

Cada opción nueva guarda una copia de las cantidades y fuentes utilizadas en nutrient_snapshot. Una
actualización de Food no recalcula esas opciones históricas. Al editar una opción permitida del borrador,
se genera una nueva copia de sus cifras. Si el catálogo cambia durante el cálculo, el guardado detecta
la inconsistencia; se debe regenerar el borrador con el catálogo actualizado.

Gemini sigue seleccionando recetas del catálogo ofrecido. No se le pide inventar cifras nutricionales.
Para aprovechar un alimento actualizado se genera un borrador nuevo y se revisa como antes.
La clave y activación real de Gemini constituyen otra conexión; esta importación no las habilita.

## 10. Datos faltantes, unidades y precisión

- Sodio en mg; proteína, carbohidratos, grasa, fibra y azúcares en g; energía en kcal.
- Valores operativos con tres decimales para conservar el dato importado. La interfaz redondea al mostrar.
- null significa que la fuente no informó el nutriente; 0 significa que informó cero.
- No se usan los labelNutrients por porción como si fueran valores por 100 g.
- No se infieren azúcares añadidos desde los totales. Los azúcares ya forman parte de los carbohidratos:
  no se vuelven a sumar a estos.
- Productos Branded cuya unidad no sea gramos se rechazan con fdc_volume_basis_unsupported. La documentación
  de USDA admite registros por 100 ml; esta versión no supone densidad 1 ni convierte ml a g.
- No se inventan valores de fibra, sodio o azúcares para los registros existentes.
- En planes anteriores sin copia nutricional nueva, los nutrientes adicionales aparecen sin datos.
- Si falta el sodio de un ingrediente, el total de sodio de esa receta se marca incompleto. No se presenta
  una suma parcial como completa.

La precisión sigue dependiendo del producto, la medición, el estado y la preparación. Añadir sal, aceite,
salsas o caldos sin registrarlos altera el resultado. Para controlar sodio hay que registrar su cantidad;
no se puede calcular la sal al gusto.

Referencia técnica sobre unidades, redondeo y datos ausentes:
[Documentación oficial de Branded Foods](https://fdc.nal.usda.gov/docs/Branded_Foods_Documentation_Apr2024.pdf).

## 11. Restricciones y excesos: siguiente configuración

Ya existe compare_limits(total, limits), que devuelve within, above o unknown según el dato y un límite
proporcionado por el llamador, usando las mismas unidades. No se configuraron límites universales ni
tratamientos por enfermedades.

Antes de activar alertas o sustituciones automáticas se debe acordar cada referencia: fuente, versión,
población a la que aplica, si el límite es por alimento/comida/día y el tratamiento del valor exacto del
límite. Un dato incompleto nunca se marca como cumplimiento.

Los filtros de ingredientes, el alcance de bienestar y la pausa clínica siguen vigentes. Los nuevos
campos permiten analizar sodio o azúcares; no convierten un menú automáticamente en apto para diabetes
o celiaquía.

## 12. Solución de errores

| Código | Qué revisar |
| --- | --- |
| fdc_key_missing | Guardar FDC_API_KEY en el entorno correcto; iniciar un comando nuevo. |
| fdc_http_401 / fdc_http_403 | Revisar la clave o las restricciones del servicio. |
| fdc_http_429 | Esperar a recuperar cuota; no repetir el comando en bucle. |
| fdc_connection_error | Red, DNS o tiempo de espera. |
| fdc_confirm_description_required | Copiar la descripción exacta confirmada de la vista previa. |
| fdc_name_exists_use_food_id | Usar el id del alimento existente si se desea actualizarlo. |
| fdc_reviewed_food_not_overwritten | Mantener el registro revisado; comparar la fuente y decidir una revisión manual. |
| fdc_core_nutrients_missing | Elegir un registro con energía, proteína, carbohidratos y grasa informados. |
| fdc_volume_basis_unsupported | Usar un registro por gramos o preparar un adaptador de densidad verificada. |

## 13. Qué se probó y qué queda de tu parte

Se verificó una conexión real al endpoint oficial con DEMO_KEY y una vista previa completa del importador;
no se guardaron alimentos de demostración en la base local. Se aplicaron las migraciones locales.

Las pruebas automáticas usan datos públicos de muestra y datos sintéticos: no requieren claves privadas
ni consumen cuota. Cubren unidades, faltantes, cero real, errores sin secretos, límites de respuesta,
vista previa sin escrituras, protección de registros revisados, historial, porciones y totales congelados.

Pasaron las 475 pruebas del módulo de planes en la ejecución general; después se verificaron además
66 pruebas focalizadas de fuentes, administración y pantallas de alimentación, y 55 de infraestructura,
incluida la ocultación de FDC_API_KEY. Django check y la comprobación de migraciones no reportan problemas.
Tailwind se recompiló. No se realizó verificación visual en navegador ni despliegue de producción.

Tu siguiente paso es obtener y guardar tu clave, ejecutar la búsqueda y revisar el primer ingrediente
cocido que utilizará realmente el recetario. No es necesario compartir el valor de la clave.

