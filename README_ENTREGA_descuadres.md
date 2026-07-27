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
1. **Detecta** (a nivel **contable**, en **TODOS los diarios y TODAS las
   empresas**) los asientos de extracto cuyo **DEBE ≠ HABER**. Ese es el
   descuadre real: la rectificativa/anticipo se contabilizó en el lado equivocado
   y al asiento le falta la contrapartida. NO depende de `bankinplay_journal_ids`
   ni de `is_reconciled` (por eso versiones previas devolvían 0).
2. **Enlaza** cada descuadre con su payload de BankInPlay: del `unique_import_id`
   de la línea saca el `id_movimiento` (la parte final) y busca en los logs los
   documentos de ese movimiento (facturas + rectificativa, con importe y signo).
3. **Repara** (vía directa, autocontenida — **no depende del conector
   desplegado**): deshace el asiento (`action_undo_reconciliation`) y lo rehace
   con el signo correcto (rectificativa al lado contrario). Envuelto en
   `_check_balanced`: si no cuadra, **revierte** y avisa. Nunca deja un asiento
   descuadrado. Verifica que tras reparar `debe = haber`.
4. Los descuadres **sin payload** en logs (purgado >90 días) se listan aparte con
   la sugerencia de `redescargar()` del periodo.

### Cómo ejecutarlo
Arranca en **modo simulación** (`DRY_RUN = True`): no escribe nada, solo informa.

```
./odoo-bin shell -d <BASE_DE_DATOS> --no-http < reparar_descuadres_conciliacion.py
```

1. **Primera pasada (simulación)**. Sale el total de asientos descuadrados, el
   `DESCUADRE TOTAL`, y cuántos son reparables (con payload) vs sin payload.
   Acotables en la cabecera: `BUSCAR_TEXTO='EROSKI'`, `ONLY_MOVEMENT_IDS`,
   `COMPANY_IDS`, o por periodo `FECHA_DESDE`/`FECHA_HASTA`.
2. Cuando estéis conformes, **`DRY_RUN = False`** y volved a ejecutarlo. Repara los
   *CON payload* y muestra por movimiento `OK` (con `descuadre tras reparar=0.00`)
   o `REVISAR`.

### Casos sin payload (log purgado >90 días)
Sin el payload no se sabe contra qué documentos reconciliar. Para regenerarlo,
`redescargar()` vuelve a pedir a BankInPlay la conciliación de un rango acotado:

```python
# dentro de odoo-bin shell, tras cargar el script:
redescargar(env, <company_id>, '2026-06-01', '2026-06-30')
```
Al llegar la respuesta se regenera el log; después, volved a lanzar `reparar(env)`.

> **Nota**: el script repara lo existente. Desplegad igualmente el módulo (punto 1)
> para **cortar los descuadres nuevos** (el bug de signo sigue vivo hasta desplegar).

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
