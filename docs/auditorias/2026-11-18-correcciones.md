# Correcciones de la auditoría de ProFit Studio

Fecha: 18 de noviembre de 2026. Rama: `fix/auditoria-seguridad-rendimiento`.
La persona autorizó aplicar las correcciones; la reserva de archivos está en el tablero de main.

## Cambios implementados

| Hallazgo | Corrección | Comportamiento comprobado |
| --- | --- | --- |
| Lista de clientes con consultas N+1 | Precarga de membresía y entrenador; orden estable y páginas de 25 filas | Administradora: 6 consultas y 77.847 bytes en la primera página, frente a 1.005 consultas y 2.078.817 bytes |
| Listas de pagos sin límite | Páginas de 25 filas para entrenador y miembro; suma SQL de todos los pagos autorizados y conteo global | La suma y el contador incluyen todas las páginas, conservando los filtros de acceso |
| Recuperación de contraseña sin límite | Dos envíos por cuenta y diez intentos por IP cada 15 minutos; correo normalizado, claves de caché con HMAC | Tres solicitudes consecutivas envían dos correos ficticios; bloqueos, correo inexistente y fallos de caché mantienen la misma confirmación pública |
| Error Wompi con JSON anidado | Lectura de hasta 1 MiB más un byte de detección; límite comprobado aun sin Content-Length; manejo de RecursionError | JSON excesivamente anidado: 400; cuerpo demasiado grande: 413; firmas e idempotencia conservadas |
| Respuestas externas sin límites | WhatsApp bloquea redirecciones y acota cuerpos normales y de error a 2 MiB más un byte de detección | No contacta el segundo destino; errores controlados sin imprimir token ni cuerpo del proveedor |
| Fallback de comprobantes al bucket público | Con R2, falta de bucket separado bloquea almacenamiento/lectura/URL de comprobantes | La aplicación sigue arrancando; la carga presenta un error de formulario sin crear el pago ni usar el bucket de media |
| Python local rezagado | Python 3.11.18 y dependencias del proyecto en `.venv-secure` | Entorno separado, probado sin reemplazar el `.venv` compartido con Claude |

Se incorporan los commits del **PR #27** de Gemini, conservando su autoría e historial. El nuevo PR
incluye ese refuerzo hasta que se fusione el #27; cuando ocurra, la diferencia contra main se ajustará.
No se cerró ni fusionó automáticamente ninguno de los PR previos.

## Detalles de seguridad y compatibilidad

- El limitador de recuperación usa la caché configurada por Django. Para que el límite sea global
  entre procesos se necesita una caché compartida como Redis. Con LocMem el contador es por
  proceso; no se afirma que Railway tenga Redis configurado. Si falla la caché, no se envía correo.
- El límite por cuenta persiste aunque cambie la IP y trata mayúsculas/minúsculas de forma uniforme.
  Las claves no contienen el correo ni la IP en texto claro; los registros no imprimen esos datos.
- Se conserva la vista y el flujo de recuperación de Django, incluidos CSRF, tokens y confirmación
  genérica. Se probaron el presupuesto por IP, cuenta inexistente, fallo de caché y expiración del contador.
- El límite Wompi no desactiva las firmas ni cambia importes, moneda, idempotencia o validación de pagos.
- El bloqueo de almacenamiento no verifica la política real del bucket indicado: un nombre separado
  no certifica que Cloudflare lo mantenga privado. Esa configuración sigue requiriendo comprobación.
- Un error de almacenamiento privado revierte el guardado mediante un savepoint; no rompe la
  transacción de la petición ni crea un registro de pago incompleto.
- Los filtros conocidos se conservan en los enlaces de paginación. No se mezclan clientes ni pagos de
  otros entrenadores. Los totales no se calculan únicamente sobre la página visible.
- No se modificó fase D, `app.css`, modelos, migraciones, secretos ni configuración de producción.

Referencia de las API empleadas: [paginación de Django](https://docs.djangoproject.com/en/5.2/topics/pagination/),
[caché y contadores](https://docs.djangoproject.com/en/5.2/topics/cache/) y
[PasswordResetView](https://docs.djangoproject.com/en/5.2/topics/auth/default/#django.contrib.auth.views.PasswordResetView).

## Validación y comparación de carga

La suite completa aprobó **1.391 pruebas** con Python 3.11.18, DeprecationWarning como error y cuatro procesos de prueba aislados (127.056 s). Pasaron también 107 pruebas de despliegue/pagos y cinco de paginación con el contador global.

Las 22 regresiones nuevas pasaron. La repetición del escenario adversarial de la auditoría dio
**14/14 resultados esperados**, incluido el JSON Wompi que antes producía 500. Las tres peticiones
de recuperación generaron dos correos en memoria en vez de tres.

La carga usó el mismo escenario: 1.000 miembros ficticios con membresía, mezcla 40 % inicio,
50 % dashboard de miembro y 10 % lista de entrenador; ventanas de 20 s más drenaje de peticiones.
Servidor local Waitress con dos hilos y SQLite, sin llamadas a proveedores ni tráfico a Railway.

| Usuarios virtuales | Antes: p95 | Después: p95 | Antes: peticiones/s | Después: peticiones/s |
| ---: | ---: | ---: | ---: | ---: |
| 25 | 1.767 ms | 190 ms | 25,52 | 164,79 |
| 50 | 2.159 ms | 318 ms | 26,55 | 174,99 |
| 100 | 4.427 ms | 634 ms | 23,51 | 170,21 |

La repetición de las correcciones completó **10.359 peticiones de carga, sin errores HTTP**.
En la lista del entrenador se midieron cinco consultas y 64.413 bytes en la primera página.

Esta comparación es orientativa: se cambió el código y el parche de Python, y son ejecuciones
distintas en el mismo equipo. No aísla el efecto de cada cambio ni demuestra capacidad máxima,
estabilidad prolongada o rendimiento del PostgreSQL/Gunicorn de Railway. No usar estas cifras
para prometer un máximo de personas en producción.

## Uso del entorno actualizado

El intérprete nuevo está disponible en:

```powershell
.\.venv-secure\Scripts\python.exe --version
```

Se conservó `.venv` porque Claude lo está usando desde su worktree. El intérprete predeterminado
de VS Code y la declaración `.python-version` de producción no se cambiaron. La actualización del
runtime compartido debe coordinarse en el tablero; la nueva instalación no activa IA ni usa claves.

`.venv-secure` queda excluido de Git mediante `.venv-*/`. No se versionan entornos, bases ficticias,
cookies ni claves. La evidencia de esta corrección contiene únicamente métricas y flags.

## Pendientes externos o de arquitectura

1. Revisar el PR y CI, y fusionar siguiendo el acuerdo con Claude. El código no se desplegó.
2. Coordinar la adopción de Python 3.11.18 en el entorno compartido y verificar el parche que usa Railway.
3. Confirmar versión vigente de PostgreSQL y la configuración efectiva de Redis. No se abrió la base real.
4. Corregir permisos y comprobar privacidad del bucket de comprobantes en Cloudflare. No se cambiaron tokens.
5. CSP, MFA, separación de workers de IA/WhatsApp y migraciones mayores de dependencias requieren tareas
   específicas y pruebas de integración; no se activaron políticas que pudieran romper pantallas existentes.
6. Para Gemini real siguen haciendo falta clave, modelo y configuración válida; no se habilitó modo live.

Estos pendientes no se presentan como resueltos por la ejecución de pruebas locales.
