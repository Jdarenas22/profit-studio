# Verificación de protección de contraseñas — 24 de noviembre de 2026

## Alcance y resultado

Se revisaron los caminos de registro, creación y edición de clientes/entrenadores, perfil,
administrador Django y comando de creación de entrenadores. Se auditaron metadatos de formato de
todas las cuentas de la base local, sin imprimir, copiar, exportar ni recuperar contraseñas, hashes
completos o salts. No se inspeccionó una base de producción ni el almacenamiento del navegador.

Todas las cuentas locales revisadas usan **PBKDF2-SHA256**, incluida la cuenta superusuaria cambiada
por la persona. No hubo formatos desconocidos ni MD5 en esas cuentas. Las nuevas contraseñas usan
el valor real de Django instalado: 1.000.000 de iteraciones. Dos cuentas conservan un factor
anterior (mínimo encontrado: 720.000); siguen siendo hashes. Django los actualiza al verificar
correctamente la contraseña. No se pueden regenerar hashes nuevos sin la contraseña o sin cambiar
el esquema de verificación: no se forzó ningún restablecimiento ni se alteraron esas cuentas.

La aplicación debe conservar un hash para autenticar al usuario: no conservar ningún verificador
impediría validar el acceso mediante contraseña. El valor original aparece transitoriamente en
memoria al introducirlo y verificarlo; no se afirma que jamás exista en memoria.

`create_user`, `set_password` y la administración estándar de Django se usan para escribir las
contraseñas. No se detectó una asignación directa de claves legibles al campo persistente ni su
envío en correos de registro, respuestas JSON o código JavaScript del proyecto revisado.

El ejecutor rápido del CI usa un hasher rápido exclusivamente durante pruebas explícitas. No
modifica los hashers de la aplicación: la auditoría del entorno normal confirmó PBKDF2-SHA256.

## Exposiciones corregidas

1. Los formularios de creación y edición de clientes tenían campos de contraseña de tipo texto.
   Ahora son de tipo password, con autocomplete apropiado.
2. La creación de clientes repintaba la clave enviada tras errores de validación. Se eliminó ese
   valor del HTML y de los contextos de formularios. También se excluyen los campos de contraseña
   de los contextos de registro, perfil y creación de entrenadores.
3. Las vistas personalizadas de credenciales no marcaban explícitamente los POST y variables
   locales como sensibles. Se añadieron los decoradores estándar de Django para ocultarlos en
   informes de error con DEBUG apagado.
4. Todos los campos Django de credenciales usan PasswordInput sin repoblación automática,
   incluidos los del perfil, aunque actualmente sus plantillas ya los ocultaban.

Las validaciones, permisos, cuentas y contraseñas existentes permanecen iguales. No se agregó
ninguna migración ni dependencia. No se creó una aplicación alternativa.

## Límites y trabajo operativo

Se comprobó solo la presencia de TRAINER_INITIAL_PASSWORD y TRAINER_PASSWORD en la configuración
local cargada: ambas ausentes. El comando interactivo es preferible al argumento --password,
que puede quedar en el historial del terminal y la lista de procesos.

Existen `USUARIOS-PRUEBA.local.txt` y `.demo/access.local.txt`, archivos privados históricos de
accesos de prueba. **Solo se comprobó su existencia; no se leyeron ni eliminaron.** Por ello no
se puede certificar que ningún archivo del proyecto contenga claves antiguas en texto. Su revisión
y retirada segura es una tarea operativa pendiente; una credencial que siga vigente debe rotarse.
No se copiaron esos archivos a esta documentación ni al PR. Las copias históricas de datos tampoco
se eliminaron. La auditoría no cubre gestores de contraseñas ni copias externas al proyecto.

Por Internet deben usarse las URLs HTTPS del túnel. El HTTP de localhost sirve solo al mismo PC.
El hashing protege el almacenamiento, no sustituye el cifrado durante el transporte.

## Pruebas

82 pruebas específicas aprobadas: validación de entradas, autorización, escritura/verificación de
hashes para los tres roles, salts distintos, actualización de iteraciones antiguas, campos ocultos,
ausencia de claves en HTML/contextos y redacción de informes de error forzados. Las nuevas pruebas
usan únicamente secretos ficticios en bases de prueba; no leen ni prueban contraseñas reales.
La suite completa pasó **1.808 pruebas** con avisos de obsolescencia como errores. El CI
definitivo se registra en el tablero al entregar. Los dos archivos históricos mencionados no
están rastreados por Git y están excluidos por las reglas actuales de .gitignore.

Fuentes oficiales:

- [Almacenamiento y actualización de contraseñas de Django 5.2](https://docs.djangoproject.com/en/5.2/topics/auth/passwords/).
- [Filtrado de información sensible en informes de error](https://docs.djangoproject.com/en/5.2/howto/error-reporting/#filtering-sensitive-information).


## Integración con la transferencia de administración de Claude

Se incorporó main con PR #43 (commit da45e4b) mientras se cerraba esta auditoría. Se conservaron
el formulario de transferencia, el servicio, los controles de contraseña/permisos y sus pruebas.
Su campo ya usaba PasswordInput y su contexto ya excluía la clave. Se añadieron además los mismos
decoradores de información sensible al nuevo endpoint, y quedó cubierto por las pruebas de widgets,
POST ocultos y ausencia de claves en respuestas.

La migración accounts 0006 pertenece a main: añade la opción transfer_admin al registro de auditoría,
sin cambios de contraseñas o permisos de cuentas. No se editó esa migración. Se regeneró CSS después
de combinar las plantillas, conservando la documentación de ambas entregas. El resultado anterior
de 1.808 pruebas corresponde al estado anterior a integrar main; el CI final registra la suite combinada.
