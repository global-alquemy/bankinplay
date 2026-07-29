# Especificación — Inbox durable de conciliación BankInPlay

> Rama: `15.0`  ·  Módulos: `account_statement_import_online_bankinplay` (base) y `conciliation_online_bankinplay` (conciliación/asientos).
> Estado: **Fases 0–5 implementadas** (pendiente de upgrade del módulo y pruebas en staging). Fase 6 (SII) pendiente. Ver §11–§12.
> Versión módulo `conciliation_online_bankinplay`: **15.0.2.0.0**.

---

## 1. Problema

Los callbacks de BankInPlay se procesan **directamente en el webhook** y, si algo falla, el error se pierde (el `bankinplay.log` se purga a los 7 días) y la línea de extracto puede quedar mal. Se han identificado **tres causas** de "factura sin conciliar":

1. **Bloqueo SII.** El `reconcile()` contra una factura ya enviada al SII lanza *"Factura sii bloqueada"*; abortaba todo el movimiento y dejaba el asiento posteado a medias. (Mitigado en Fase 0; solución definitiva = desbloqueo SII, Fase 6.)
2. **Coordinación terceros ↔ asiento.** Ambos flujos contabilizan el mismo movimiento; si el **asiento** llega antes, lo contabiliza en genérico (431) y marca la línea conciliada → terceros ya no puede cerrar la factura. Confirmado con el movimiento `487619009` (log: *"no se encontró línea sin conciliar"*).
3. **Anticipos/retenciones ignorados.** El callback solo lee `documentos`, ignora `anticipos` → facturas con retención quedan cortas. Confirmado con `487619009` (FIXEMER): factura 1.800, banco 1.746, retención 54.

## 2. Objetivo

Sustituir el "procesar en el callback" por un **inbox durable** (patrón bandeja de entrada / cola de comandos):

- El webhook **solo registra** (upsert) la intención en modelos propios y responde rápido.
- Un **cron procesador** ejecuta la contabilización, de forma idempotente, con estado y reintentos.
- Los datos accionables **no se purgan** (el log crudo sí puede seguir purgándose).

Beneficios: no se pierde nada con la purga; los errores quedan aparcados y se auto-reintentan; trazabilidad completa; el revolcado se vuelve "reprocesar lo pendiente".

## 3. Los dos flujos

| | **Terceros** | **Asiento contable** |
|---|---|---|
| Endpoint | `/conciliacion-terceros` | `/asientoContableApi/asiento_contable` |
| Callback | `manage_conciliacion_terceros_callback` | `manage_asiento_contable_callback` |
| Qué hace | **Reconcilia** banco ↔ facturas (cierra la factura) | **Contabiliza** el asiento (banco + contrapartida), sin conciliar |
| Cuándo | Cuando BankInPlay casa documentos | Para cualquier movimiento (cobros genéricos, traspasos, intereses, nóminas…) |
| Llegada | Documentos **a trozos** en varios callbacks | Asiento **completo y cuadrado** de una vez |
| Agrupación | Por movimiento → N documentos + anticipos | Por movimiento → N apuntes |

**Clave de unión de todo:** `id_movimiento` (en asientos viene como `movimiento_id`). Es el id del **movimiento bancario** en BankInPlay, y coincide con el sufijo del `unique_import_id` de la línea de extracto (`cuenta-diario-<id_movimiento>`).

## 4. Modelos de datos

### 4.1 Mixin común — `bankinplay.inbox.mixin` (AbstractModel)

Campos compartidos por las dos cabeceras:

| Campo | Tipo | Notas |
|---|---|---|
| `company_id` | M2O res.company | required, index |
| `id_movimiento` | Char | required, index — id del movimiento BankInPlay |
| `statement_line_id` | M2O account.bank.statement.line | resuelto por `unique_import_id like id_movimiento` |
| `fecha` | Date | |
| `descripcion` | Char | |
| `state` | Selection | ver §5 |
| `attempts` | Integer | contador |
| `last_attempt` | Datetime | para el backoff |
| `last_error` | Text | último error (vistazo rápido) |
| `log_ids` | M2M bankinplay.log | de qué callbacks vino (`ondelete` set null) |
| `attempt_ids` | O2M | histórico de intentos (§4.4) |

Métodos comunes: `_upsert(vals)` (find-or-create por `(company_id, id_movimiento)`), `_process()` (abstracto), `_cron_process_pending()` (§8), `_register_attempt(result, message, ...)`.

### 4.2 Terceros — `bankinplay.conciliation.movement` (cabecera)

Hereda del mixin + :

| Campo | Tipo | Notas |
|---|---|---|
| `cuenta_bancaria` | Char | |
| `importe_movimiento` | Monetary | = `statement_line.amount` (objetivo) |
| `importe_documentos` | Monetary | computed = Σ `importe_conciliado` de las líneas tipo documento |
| `document_ids` | O2M | líneas (§4.3) |

**Clave única:** `(company_id, id_movimiento)`.

### 4.3 Terceros — `bankinplay.conciliation.document` (línea)

| Campo | Tipo | Notas |
|---|---|---|
| `movement_id` | M2O | cascade |
| `line_type` | Selection | `documento` / `anticipo` |
| `id_documento_erp` | Char | id del `account.move.line` en Odoo (documento) |
| `conciliacion_id` | Char | id de conciliación BankInPlay (enlaza documento + anticipo) |
| `no_documento` | Char | nº de factura |
| `importe` | Monetary | importe del documento |
| `importe_conciliado` | Monetary | parte casada con el banco |
| `importe_pendiente` | Monetary | resto |
| `importe_retencion` | Monetary | retención |
| `signo` | Char | cobro/pago |
| `nif_tercero` / `razon_social_tercero` / `codigo_erp_tercero` | Char | (sobre todo para anticipos) |
| `move_line_id` | M2O account.move.line | factura resuelta |
| `state` | Selection | `pending/valid/invalid/done` |
| `error_message` | Text | p. ej. "apunte no válido" |
| `log_id` | M2O bankinplay.log | origen |

**Clave única:** `(movement_id, line_type, conciliacion_id, id_documento_erp)` → upsert.

### 4.4 Asientos — `bankinplay.accounting.entry` (cabecera)

Hereda del mixin + :

| Campo | Tipo | Notas |
|---|---|---|
| `cuenta_bancaria` | Char | |
| `banco` | Char | BIC |
| `no_asiento` | Integer | correlativo del lote (**no** es clave) |
| `divisa` | Char | |
| `pdf_bancario` | Char | URL al detalle en BankInPlay |
| `line_ids` | O2M | apuntes (§4.5) |
| `is_tercero_type` | Boolean (computed) | contrapartida = cliente/proveedor (§9.4) |
| `hold_until` | Datetime | fin de la gracia (§9.4) |

**Clave única:** `(company_id, id_movimiento)`.

### 4.5 Asientos — `bankinplay.accounting.entry.line` (apunte)

| Campo | Tipo | Notas |
|---|---|---|
| `entry_id` | M2O | cascade |
| `no_apunte` | Integer | |
| `cuenta_contable` | Char | |
| `debe_haber` | Selection | `D` / `H` |
| `importe` | Monetary | |
| `account_id` | M2O account.account | resuelto por código + compañía |
| `codigo_analitico` | Char | de `analitica.desglose` |
| `analytic_account_id` | M2O account.analytic.account | resuelto |
| `state` / `error_message` | | p. ej. "cuenta 626 no encontrada" |

### 4.6 Histórico de intentos — `bankinplay.*.attempt`

Un modelo hijo por cada cabecera (misma forma). Un registro por ejecución del procesador:

| Campo | Tipo | Notas |
|---|---|---|
| `parent_id` | M2O (movement/entry) | cascade |
| `date` | Datetime | default now, readonly |
| `result` | Selection | `success/error/skipped/waiting` |
| `message` | Text | detalle/traza |
| `document_id` / `line_id` | M2O | la línea que provocó el fallo (p. ej. factura SII bloqueada) |
| `log_id` | M2O bankinplay.log | callback origen |
| `user_id` | M2O res.users | cron o "Reprocesar" manual |

Permite analítica: agrupar por `message`/`result` para ver, p. ej., cuántos fallos por bloqueo SII o qué facturas atascan.

## 5. Estados y transiciones

| Estado | Significado | Lo pone |
|---|---|---|
| `waiting` | Recibido, no procesable aún. Terceros: documentos no cubren el banco, o la línea de extracto aún no está importada. Asiento: en gracia esperando a terceros. | upsert / procesador |
| `ready` | Listo. Terceros: documentos cubren el banco. Asiento: no es tercero-type, o gracia expirada. | upsert / recompute |
| `done` | Contabilizado correctamente. **Terminal.** | procesador |
| `error` | Intento fallido, reintentable con backoff. | procesador |
| `skipped` / `superseded` | Cedido al otro flujo (la línea ya la contabilizó el otro). | procesador |
| `conflict` | Ambos contabilizaron / inconsistencia → **revisión manual**. | procesador |

Recompute de `waiting → ready` en cada upsert (al llegar nuevos documentos) y en el cron.

### 5.1 Resolución de la línea de extracto y re-evaluación de `waiting`

Un movimiento puede llegar **antes** de que su línea de extracto esté importada en Odoo (la importación de extractos es un flujo/cron aparte). En ese caso:

- El upsert **se hace igual** (nada se pierde); `statement_line_id` queda sin resolver.
- El estado queda en **`waiting`**, **no** `error` → no cuenta como intento fallido, no entra en el backoff ni ensucia el histórico de intentos.

La `statement_line_id` se resuelve por `unique_import_id like id_movimiento` (+ comprobación exacta `cuenta-diario-<id_movimiento>`). El paso `waiting → ready` se dispara por:

1. **El cron re-evalúa los `waiting`** (resolver la línea + comprobar readiness es barato: una búsqueda y una suma). Al importarse la línea, la siguiente pasada la resuelve y promociona a `ready`. Esta re-evaluación es **solo lectura** y **no consume el cupo de contabilización** del lote §8 (solo los `ready`/`error` gastan lote).
2. **Hook opcional al crear la línea de extracto** de BankInPlay (mejor latencia): despierta directamente al movimiento en `waiting` con ese `id_movimiento`, sin esperar al barrido.

**Borde:** si la línea **nunca** llega, el movimiento queda `waiting` indefinidamente. Es correcto (no es procesable), pero debe ser **visible**: filtro/vista *"`waiting` sin línea de extracto desde hace más de N días"* para revisión manual.

## 6. Idempotencia — "guardar ≠ ejecutar"

1. **Claves únicas** (§4) → los reenvíos de BankInPlay **actualizan**, no duplican.
2. **El estado es el candado:** el procesador solo toca `ready`/`error`; `done` es intocable.
3. **Chequeo previo:** antes de contabilizar, verificar `statement_line.is_reconciled == False`. Si ya está conciliada → `done`/`skipped` sin rehacer (cubre caídas entre contabilizar y marcar `done`).
4. **Atomicidad:** contabilizar + marcar `done` en la **misma transacción** (savepoint). Si falla → rollback (nada de asiento a medias) y queda `error`.

Garantía: aunque el mismo movimiento llegue por varios callbacks o el cron lo mire varias veces, se **contabiliza una única vez**.

## 7. Flujo

```
BankInPlay ──callback──▶ webhook ──UPSERT──▶ inbox (waiting/ready)   [rápido, sin lógica de negocio]
                                                   │
                              cron cada minuto ────┘
                                                   ▼
                                       procesador (_process en savepoint)
                                                   ▼
                                       done / error / skipped / conflict
```

## 8. Cron procesador

- **Frecuencia:** cada 1 minuto.
- **Lote:** `search(limit = bankinplay.conciliation_batch_size)` (default **10**). Nunca sin límite.
- **Orden justo:** `order='state desc, last_attempt asc, id asc'` → primero `ready` (nunca intentados), rota por los más antiguos.
- **Backoff de `error`:** solo reintentar si `last_attempt < now - bankinplay.error_retry_minutes` (p. ej. 30–60 min). Evita que los fallidos (SII) copen el lote y bloqueen lo nuevo.
- **Atómico por movimiento:** cada uno en su `savepoint` + `commit`.
- **Re-evaluación de `waiting`:** además de contabilizar `ready`/`error`, el cron re-evalúa los `waiting` (resolver línea de extracto + readiness) y promociona a `ready` los que ya se puedan. Es solo lectura y **no gasta cupo del lote** (§5.1).
- **Sin solapes:** garantizado por el lock de fila de `ir_cron` (el job corre en un cursor aparte del que sostiene el lock, así que los `commit` por movimiento no lo sueltan).

## 9. Reglas de negocio

### 9.1 Readiness (terceros)
`ready` cuando `Σ documentos.importe_conciliado == importe_movimiento` (importe del banco). La referencia es **cubrir el banco**, no la factura.

### 9.2 Pagos parciales
Cada documento se reconcilia por su `importe_conciliado` contra la factura. Si no llega al total → **la factura queda abierta por el resto** (`importe_pendiente`). Ej. `00899`: banco 100 / factura 242 → concilia 100, abierta 142.

### 9.3 Anticipos / retenciones
- Van al modelo de terceros como línea `line_type = anticipo`, enlazados al documento por `conciliacion_id`.
- Se contabilizan **siempre contra una cuenta fija**: parámetro `bankinplay.anticipo_account_id`.
- El importe del anticipo **también cierra factura**: el asiento del banco lleva `banco (importe_conciliado) + anticipo (a la cuenta fija) = 430 factura`, que se reconcilia entera. Ej. FIXEMER: 1.746 + 54 → factura 1.800 **cerrada**.
- Distinción: `importe_pendiente` **con** anticipo → lo cierra la cuenta fija; **sin** anticipo → factura abierta (parcial real).

### 9.4 Coordinación terceros ↔ asiento
**Principio: terceros manda; el asiento es el cajón de lo demás, con red de seguridad.** La línea de extracto (`is_reconciled`) es el candado único.

- **Vía rápida (tesorería):** si la contrapartida del asiento **no** es de cliente/proveedor (532, 520, 662, 465…) → contabiliza ya.
- **Hold + gracia:** si la contrapartida **sí** es de cliente/proveedor (43/40) → el asiento espera (`hold_until = now + bankinplay.asiento_grace_days`, default 5), estado `waiting`:
  - Llegan documentos de terceros → **gana terceros**; el asiento pasa a `superseded`.
  - Expira la gracia sin documentos → el asiento contabiliza en **genérico (fallback)**, marcado *"genérico por falta de documentos"* (visible).
- **Cesión:** el primero que contabiliza marca `is_reconciled`; el otro pasa a `skipped/superseded`.
- **Conflicto real** (ambos contabilizaron) → estado `conflict`, **sin auto-revertir** (revertir un posteado nos devuelve al bloqueo SII). Revisión manual.
- La clasificación cliente/proveedor se hace por `account.internal_type in (receivable, payable)` del `cuenta_contable` resuelto; opcionalmente excluir cuentas con `bankinplay.asiento_hold_account_excludes`. Peor caso de un falso positivo: el movimiento espera la gracia y luego cae al fallback — nunca se pierde.

## 10. Parámetros de configuración (`ir.config_parameter`)

| Parámetro | Default | Uso |
|---|---|---|
| `bankinplay.conciliation_include_exported` | `True` | `exportados` en la petición de terceros. Con el inbox montado → poner `False` (solo nuevos). **Ya implementado.** |
| `bankinplay.conciliation_batch_size` | `10` | Lote del cron. Subir puntualmente para el revolcado. |
| `bankinplay.anticipo_account_id` | — | Cuenta fija de anticipos/retenciones. |
| `bankinplay.asiento_grace_days` | `5` | Gracia del asiento antes del fallback. |
| `bankinplay.asiento_hold_account_excludes` | — | Cuentas a excluir del "hold" (falsos positivos). |
| `bankinplay.error_retry_minutes` | `60` | Backoff de reintentos en `error`. |
| `bankinplay.log_retention_days` | `7` | Purga del log crudo (ya existe). |

> Confirmar con BankInPlay la semántica de `exportados` (¿cuándo marca un documento como exportado?) antes de fiarlo a `False`.

## 11. Fase 0 — ya aplicado

- **Savepoint** en `manage_conciliacion_terceros_callback`: si el `reconcile()` falla, no queda asiento a medias y la línea queda pendiente (reintentable).
- **Etiqueta**: en las 430 va el nº de factura (`move_line.move_id.name`); el concepto de la transferencia queda en la 572.
- **`exportados` configurable** (`bankinplay.conciliation_include_exported`, default `True`).
- **Vista "BankInPlay: Asientos sin conciliar"** (`action_bankinplay_affected_moves_server`): lista los asientos de extracto con 430/400 sin conciliar (para diagnóstico y revolcado). Incluye la acción **"Revertir y reprocesar (inbox)"** (§13).

## 12. Fases de implementación

1. **Modelos + mixin + estados + claves + vistas + histórico de intentos.**
2. **Webhooks → upsert** (dejar de procesar en el callback; solo registrar).
3. **Procesador terceros** (readiness §9.1, parciales §9.2, anticipos §9.3) + cron §8.
4. **Procesador asientos** + **coordinación** §9.4 (hold/gracia/fallback, conflictos).
5. **Botón "Reprocesar"** en las vistas + **revolcado/backfill** (§13).
6. **SII (A)** — desbloqueo (aparte; requiere el grupo `xmlid` y el `raise` del módulo SII).

## 13. Revolcado / migración

El revolcado del histórico está cableado como un **flujo de un clic** desde la vista
de afectados (§11):

1. Menú **"BankInPlay: Asientos sin conciliar"** → lista los asientos antiguos con
   430/400 sin conciliar.
2. Seleccionar → **⚙ Acción → "BankInPlay: Revertir y reprocesar (inbox)"**
   (`account.move.action_bankinplay_revert_and_reprocess`, solo `group_account_manager`).
   Por cada asiento:
   - `button_undo_reconciliation()` → la línea de extracto vuelve a `is_reconciled = False`
     (quita las 430, restaura la transitoria).
   - Se calcula el **rango de fechas** (mín/máx) de los revertidos por compañía.
3. Se relanza la importación **acotada por rango de fechas**, vía contexto:
   - `bankinplay_fecha_desde` / `bankinplay_fecha_hasta` → `fecha_conciliacion_desde` /
     `fecha_conciliacion_hasta` (con margen ±días). **No** se toca el `last_syncdate` del
     sync normal.
   - `bankinplay_force_exported = True` → fuerza `exportados: True` (reenvía lo ya exportado).
4. Los callbacks upsertean al inbox y el **cron** los reprocesa bien (coordinación
   terceros > asiento, anticipos, parciales). Se sigue el resultado en
   **BankInPlay Inbox → Conciliación terceros / Asientos contables**.

Notas:
- Para drenar rápido un backfill grande, subir puntualmente `bankinplay.conciliation_batch_size`
  y luego bajarlo a 10.
- Confirmar con BankInPlay si el endpoint respeta `fecha_conciliacion_hasta` (el código
  antiguo solo enviaba `desde`). Si lo ignora, trae de más y el inbox deduplica; no rompe.
- En régimen normal, cuando el inbox esté validado, poner `bankinplay.conciliation_include_exported = False`.
- **Probar en staging con pocos asientos** antes de lanzarlo masivo.

## 14. Casos de prueba (datos reales de los logs)

| Caso | Movimiento | Qué valida |
|---|---|---|
| **Multi-factura + SII** | `484597041` (PEÑACOBA) | 3 facturas cuadran, pero `reconcile` falla por SII → con Fase 0 no deja basura; con Fase 6 concilia. |
| **Retención (anticipo)** | `487619009` (FIXEMER), concil. `487833228` | Documento 1.746 + anticipo 54 → factura 1.800 cerrada, 54 a cuenta fija. |
| **Parcial real** | `484597036` (EXTRAICE) / doc `00899` | Banco 100 / factura 242 → concilia 100, factura abierta 142. |
| **Coordinación** | `487619009` | Terceros vio la línea ya conciliada → confirmar precedencia terceros > asiento. |
| **Asiento tesorería** | `484500245` (traspaso 532301) | Vía rápida: contabiliza ya, sin esperar a terceros. |

## 15. Dudas pendientes (no bloqueantes)

- **SII (A):** grupo `xmlid` + `raise` del módulo de bloqueo (no está en el repo).
- **`exportados`:** confirmar con BankInPlay cuándo marca "exportado".
- **`anticipos`:** confirmar si siempre son retención o hay anticipos de cliente reales / otras diferencias (afectaría a si la cuenta fija basta).
- **Disjunción de flujos:** confirmar cuántos movimientos aparecen en terceros **y** asiento (define cuánto pesa la coordinación §9.4).
