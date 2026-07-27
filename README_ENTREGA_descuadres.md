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
1. **Detecta** los movimientos afectados por `id_documento_erp` (el id de la
   `account.move.line` de la factura, referencia estable de Odoo): una factura
   que BankInPlay da por conciliada pero que en Odoo sigue **abierta**. No
   depende del emparejamiento por `id_movimiento`. **Consolida** los N documentos
   de un mismo movimiento aunque vengan en varios logs.
2. **Repara**, eligiendo vía para cada movimiento:
   - **Localizable por id** (el `unique_import_id` es el que espera el conector):
     se repara por **replay** — deshace la conciliación
     (`action_undo_reconciliation`) y reejecuta el conector ya corregido.
   - **Localizable por datos** (no casa por `id_movimiento`, pero se encuentra la
     línea de extracto por **descripción + importe**): se repara en **directo**,
     replicando la lógica corregida con el signo correcto.
   - **No localizable**: se informa y **no se toca** (revisión manual).
3. **Verifica** el resultado (transitoria a 0 y facturas saldadas) y va todo
   envuelto en `_check_balanced`: si un asiento no cuadra, **revierte** y avisa.
   Nunca deja un asiento descuadrado ni una conciliación a medias.

> **`FORZAR_DIRECTO`** (por defecto `True`): repara SIEMPRE por la vía directa
> (lógica corregida dentro del script), así que **no hace falta tener el conector
> desplegado con el fix** para reparar el histórico. Ponlo a `False` solo si el
> conector ya está actualizado y preferís que sea él quien rehaga la conciliación
> (replay). En cualquier caso, **desplegad el módulo** para cortar los descuadres
> nuevos (el bug de signo sigue vivo hasta que se despliegue).

### Cómo ejecutarlo
Arranca en **modo simulación** (`DRY_RUN = True`): no escribe nada, solo informa.

```
./odoo-bin shell -d <BASE_DE_DATOS> --no-http < reparar_descuadres_conciliacion.py
```

1. **Primera pasada (simulación)**. Recomendado acotar a un caso conocido para
   revisarlo con calma, con las variables de la cabecera:
   `DRY_RUN=True`, `BUSCAR_TEXTO='EROSKI'` (o `ONLY_MOVEMENT_IDS`, `COMPANY_IDS`).
   Para acotar a un **periodo contable / cierre** (por fecha del movimiento
   bancario): `FECHA_DESDE='2026-06-01'` y `FECHA_HASTA='2026-06-30'`. La salida
   indica, por movimiento, su fecha y si es *localizable por id / por datos / no
   localizable*, y muestra `unique_import_id` ACTUAL vs ESPERADO.
2. Cuando estéis conformes, poned **`DRY_RUN = False`** y volved a ejecutarlo.
   Reparará (replay + directo) y mostrará por cada movimiento `OK` o `REVISAR`.

### Asientos descuadrados + control de cobertura (¿los cubre todos?)
Al final de cada ejecución, el script imprime la foto **contable** (independiente
de los logs) de los **asientos descuadrados** = moves de extracto BankInPlay cuyo
**DEBE ≠ HABER** (el conector los dejó así al escribir con
`check_move_validity=False`). Muestra el descuadre por línea (debe−haber) y el
**TOTAL**, y cruza con los logs: cuántos son **reparables** ya y cuántos están
**SIN cobertura** (log purgado). Para estos últimos sugiere el `redescargar()` del
periodo. Objetivo: dejar *SIN cobertura = 0* y, tras reparar, *DESCUADRE TOTAL = 0*.

### Casos "sin log" (payload antiguo purgado)
Los logs con más de 90 días se purgan, y sin ese payload un caso no se puede
reprocesar. Para regenerarlo, el script incluye una función que vuelve a pedir a
BankInPlay la conciliación de un periodo:

```python
# dentro de odoo-bin shell, tras cargar el script:
redescargar(env, <company_id>, '2026-06-01')   # fecha desde la que re-descargar
```

Al llegar la respuesta se regenera el log; después, volved a lanzar `analizar(env)`.

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
