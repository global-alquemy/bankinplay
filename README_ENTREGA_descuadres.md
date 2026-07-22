# Entrega: corrección de asientos descuadrados (conciliación de terceros BankInPlay)

Este paquete resuelve el problema de los **asientos descuadrados** que se generaban
al conciliar cobros que incluían rectificativas o anticipos (importe negativo).

Contiene dos cosas:

1. **Actualización del módulo** — para que **no vuelvan a producirse** nuevos descuadres.
2. **Script de reparación** — para **corregir los asientos ya descuadrados** en la base de datos.

---

## 1. Actualización del módulo (evitar nuevos descuadres)

### Qué corrige
- **Signo de la conciliación**: los documentos con importe conciliado negativo
  (anticipos / rectificativas) se contabilizaban siempre en el Haber; ahora se
  respeta el signo y van al Debe cuando corresponde. Esto elimina el residual
  que quedaba atrapado en la cuenta transitoria.
- **Clasificación de logs "sin datos"**: las respuestas vacías de BankInPlay se
  marcaban como `error`. Ahora se marcan con un estado propio **"Sin datos"**,
  para que el volumen de errores refleje solo errores reales. Se añadieron
  filtros en la vista de *Registros BankInPlay* (Error real / Sin datos / Éxito
  / Pendiente).

### Módulos y versiones
- `account_statement_import_online_bankinplay` → **16.1.2.0.0**
- `conciliation_online_bankinplay` → **16.1.4.0.0**

### Cómo desplegar
1. Sustituir el código de ambos módulos por el de esta entrega.
2. Actualizar en Odoo:
   ```
   ./odoo-bin -d <BASE_DE_DATOS> -u account_statement_import_online_bankinplay,conciliation_online_bankinplay --stop-after-init
   ```
3. Reiniciar el servicio de Odoo.

> Requisito ya existente del conector: `queue_job >= 16.0.3.0.0`.

---

## 2. Script de reparación de asientos existentes

Fichero: **`reparar_descuadres_conciliacion.py`**

Es un proceso **puntual** (NO un cron): se ejecuta, se revisa y se termina. Una
vez desplegado el módulo del punto 1, no se generan casos nuevos.

### Qué hace
- Localiza los movimientos bancarios cuya conciliación quedó mal (residual en la
  transitoria / facturas del movimiento sin saldar) por culpa del signo.
- Para cada uno: deshace la conciliación (`action_undo_reconciliation`, método
  estándar de Odoo) y la vuelve a aplicar con el código ya corregido, y luego
  **verifica que el asiento cuadra** y la transitoria queda a cero.
- No toca importes a mano: reprocesa con el propio conector, de forma controlada.

### Cómo ejecutarlo
Arranca en **modo simulación** (`DRY_RUN = True`): no escribe nada, solo informa.

```
./odoo-bin shell -d <BASE_DE_DATOS> --no-http < reparar_descuadres_conciliacion.py
```

1. **Primera pasada (simulación)**: revisad el listado de movimientos que
   detecta. En la cabecera del script podéis acotar por compañía, fechas o
   `id_movimiento` concretos (`COMPANY_IDS`, `DATE_FROM`, `DATE_TO`,
   `ONLY_MOVEMENT_IDS`).
2. Cuando estéis conformes, poned **`DRY_RUN = False`** y volved a ejecutarlo.
   Reparará y mostrará, por cada movimiento, `OK` o `REVISAR`.

### Casos "sin log" (payload antiguo purgado)
Los logs con más de 90 días se purgan, y sin ese payload un caso no se puede
reprocesar directamente. El script los **lista aparte** (sección *"LÍNEAS
BANKINPLAY SIN LOG (requieren RE-DESCARGA)"*) y ofrece una función para
volver a pedir esos documentos a BankInPlay:

```python
# dentro de odoo-bin shell, tras cargar el script:
redescargar(env, <company_id>, '2026-06-01')   # fecha desde la que re-descargar
```

Esto vuelve a solicitar la conciliación de terceros de ese periodo; al llegar la
respuesta se regenera el log **y se reconcilian bien las líneas pendientes**.
Después, volved a lanzar la reparación normal para cualquier caso que siguiera
conciliado incorrectamente.

---

## ✅ Checklist antes de ejecutar en producción

- [ ] **Copia de seguridad** de la base de datos.
- [ ] Desplegar y actualizar el módulo (punto 1).
- [ ] Probar el script primero en una **copia de staging**.
- [ ] Ejecutar el script en **DRY_RUN = True** y revisar el listado.
- [ ] Ejecutarlo en real (`DRY_RUN = False`) en una **ventana controlada**
      (evitar que coincida con el cron de importación).
- [ ] Revisar los movimientos marcados como `REVISAR` (si los hubiera).

---

Ante cualquier duda quedamos a vuestra disposición para revisarlo conjuntamente.
