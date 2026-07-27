# -*- coding: utf-8 -*-
# Alquemy - Diagnóstico y reparación de descuadres por conciliación de terceros (BankInPlay)
#
# CONTEXTO
# --------
# Al conciliar cobros/pagos de terceros, algunos movimientos quedaban mal
# resueltos: la factura que BankInPlay da por conciliada (importe_pendiente=0)
# sigue ABIERTA en Odoo y/o queda residual en la cuenta transitoria.
#
# CÓMO EMPAREJA EL CONECTOR (y por qué a veces no encuentra nada)
# --------------------------------------------------------------
# El conector localiza la línea de extracto así:
#     search([('unique_import_id', 'like', id_movimiento)])
#     y exige  unique_import_id == "{cuenta_bancaria}-{journal_id}-{id_movimiento}"
# Pero la importación del extracto guarda  unique_import_id = str(transaction['id']).
# Si ese 'id' del extracto NO contiene el 'id_movimiento' de la conciliación,
# el conector NO concilia ese movimiento (y una búsqueda por id_movimiento no lo
# encuentra). Por eso este script NO se apoya en id_movimiento para detectar:
# usa 'id_documento_erp', que es DIRECTAMENTE el id de la account.move.line de la
# factura (referencia estable de Odoo).
#
# QUÉ HACE
# --------
#   1) DIAGNÓSTICO (siempre, solo lectura): recorre los logs de conciliación con
#      payload, CONSOLIDA los documentos de cada id_movimiento (aunque vengan en
#      varios logs / reenvíos), resuelve cada id_documento_erp -> move.line y
#      detecta las facturas que BankInPlay concilió pero en Odoo siguen abiertas.
#   2) REPARACIÓN (solo si DRY_RUN=False). Para cada movimiento elige vía:
#        a) LOCALIZABLE POR ID (unique_import_id == cuenta-diario-id_movimiento):
#           se repara por REPLAY -> deshace la conciliación
#           (action_undo_reconciliation) y reejecuta el conector ya corregido.
#        b) NO localizable por id pero LOCALIZABLE POR DATOS (misma descripción e
#           importe neto): se repara en DIRECTO -> replica la lógica corregida
#           creando las contrapartidas con el signo correcto.
#        c) NO localizable de ninguna forma: se informa y NO se toca.
#      Ambas vías van envueltas en _check_balanced: si el asiento no cuadra,
#      se revierte y se informa. NUNCA deja un asiento descuadrado.
#
# CÓMO EJECUTAR
#   ./odoo-bin shell -d <BASE_DE_DATOS> --no-http < reparar_descuadres_conciliacion.py
#   Recomendado 1ª vez:  DRY_RUN=True  y  BUSCAR_TEXTO='EROSKI'  (o el movimiento
#   concreto que queráis revisar) para ver el diagnóstico antes de tocar nada.
#   Para acotar a un periodo/cierre:  FECHA_DESDE='2026-06-01'  FECHA_HASTA='2026-06-30'
#   (por fecha del movimiento bancario).
#
# ⚠️ Antes de reparar en real: copia de seguridad + prueba en staging.

import json
import logging

_logger = logging.getLogger("bankinplay.reparacion")

# ==========================================================================
# CONFIGURACIÓN
# ==========================================================================
DRY_RUN = True                 # True = solo diagnostica. False = repara.

TRIGGERED_EVENT = 'exportacion_conciliacion_terceros'

# Filtros opcionales (vacío = sin filtro):
COMPANY_IDS = []               # p.ej. [1]

# Acotar por FECHA DEL MOVIMIENTO bancario = periodo contable (lo habitual para
# un cierre). Formato 'YYYY-MM-DD'. Se usa la fecha de la línea de extracto y, si
# no hay línea, la fecha de operación del payload.
FECHA_DESDE = False            # p.ej. '2026-06-01'
FECHA_HASTA = False            # p.ej. '2026-06-30'

ONLY_MOVEMENT_IDS = []         # p.ej. ['471752127']
BUSCAR_TEXTO = ''              # subcadena de descripcion_movimiento (p.ej. 'EROSKI')

# Prefiltro TÉCNICO opcional por fecha de RECEPCIÓN del log (no es el periodo
# contable; sólo reduce los logs a parsear). Formato 'YYYY-MM-DD HH:MM:SS'.
LOG_DATE_FROM = False
LOG_DATE_TO = False

EPS = 0.005                    # tolerancia de residual (moneda compañía)
MAX_DETALLE = 500              # límite de líneas de detalle a imprimir
# ==========================================================================


def _f(x):
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _move_line(env, id_documento_erp):
    """id_documento_erp es el id de una account.move.line."""
    if not id_documento_erp:
        return env['account.move.line'].browse()
    try:
        mlid = int(id_documento_erp)
    except (TypeError, ValueError):
        return env['account.move.line'].browse()
    return env['account.move.line'].browse(mlid).exists()


def _iter_movimientos(desencrypt_data):
    """Recorre TODAS las sociedades del payload y agrupa documentos por
    id_movimiento. Devuelve lista de (sociedad, id_movimiento, docs)."""
    out = []
    for soc in (desencrypt_data.get('sociedades') or []):
        por_mov = {}
        for doc in (soc.get('documentos') or []):
            idm = str(doc.get('id_movimiento') or '')
            if not idm:
                continue
            por_mov.setdefault(idm, []).append(doc)
        for idm, docs in por_mov.items():
            out.append((soc, idm, docs))
    return out


def _statement_line_por_movimiento(env, id_movimiento):
    """Busca como el conector: unique_import_id LIKE id_movimiento (substring)."""
    return env['account.bank.statement.line'].search(
        [('unique_import_id', 'like', id_movimiento)], limit=1)


def _unique_import_esperado(docs, st_line):
    """El unique_import_id que el conector EXIGE (== para conciliar)."""
    if not st_line:
        return ''
    cuenta = (docs[0].get('cuenta_bancaria') or '') if docs else ''
    return "%s-%s-%s" % (cuenta, st_line.journal_id.id,
                         str(docs[0].get('id_movimiento')) if docs else '')


def _suspense_residual(st_line):
    if not st_line:
        return 0.0
    _liq, suspense_lines, _other = st_line._seek_for_lines()
    return sum(suspense_lines.mapped('amount_residual'))


def _docs_abiertos(env, docs):
    """move.line de los documentos que BankInPlay concilió pero siguen abiertos."""
    abiertos = []
    for doc in docs:
        ml = _move_line(env, doc.get('id_documento_erp'))
        if not ml:
            continue
        if ml.parent_state != 'posted':
            continue
        if ml.account_id.account_type not in ('asset_receivable', 'liability_payable'):
            continue
        if abs(ml.amount_residual) > EPS:
            abiertos.append((doc, ml))
    return abiertos


def _coincide_texto(docs):
    if not BUSCAR_TEXTO:
        return True
    t = BUSCAR_TEXTO.lower()
    return any(t in (d.get('descripcion_movimiento') or '').lower() for d in docs)


def _fecha_movimiento(st_line, st_datos, docs):
    """Fecha del movimiento como 'YYYY-MM-DD': la de la línea de extracto si se
    localiza (verdad contable); si no, la fecha de operación del payload."""
    sl = st_line or st_datos
    if sl and sl.date:
        return str(sl.date)[:10]
    if docs:
        f = (docs[0].get('fecha_operacion_movimiento')
             or docs[0].get('fecha_confirmacion') or '')
        return f[:10]
    return ''


def _fuera_de_periodo(fecha):
    """True si `fecha` ('YYYY-MM-DD') queda fuera de [FECHA_DESDE, FECHA_HASTA].
    Si no se puede determinar la fecha, NO se descarta (se incluye por seguridad)."""
    if not fecha:
        return False
    if FECHA_DESDE and fecha < FECHA_DESDE:
        return True
    if FECHA_HASTA and fecha > FECHA_HASTA:
        return True
    return False


def _payload_reducido(sociedad, docs):
    soc = dict(sociedad)
    soc['documentos'] = docs
    return {'sociedades': [soc]}


# --------------------------------------------------------------------------
# REPARACIÓN DIRECTA (para movimientos NO emparejables por id_movimiento)
# Empareja la línea de extracto por descripción + importe, y concilia
# replicando la lógica corregida del conector, autovalidando el cuadre.
# --------------------------------------------------------------------------
def _iban(x):
    return (x or '').replace(' ', '').upper()


def _es_reversal(doc, st_amount):
    """True si el documento es una reversión (rectificativa/anticipo) respecto al
    signo del movimiento. Robusto a las dos codificaciones posibles:
      - importe_conciliado negativo, o
      - signo_movimiento contrario al del movimiento bancario."""
    imp = _f(doc.get('importe_conciliado'))
    if imp < 0:
        return True
    signo = (doc.get('signo_movimiento') or '').lower()
    mov = 'cobro' if st_amount > 0 else 'pago'
    return bool(signo) and signo != mov


def _buscar_statement_line_por_datos(env, docs):
    """Localiza la línea de extracto SIN usar id_movimiento: por descripción
    (payment_ref) + importe, restringido al diario del IBAN. Devuelve la línea
    (o vacío) y un texto explicativo."""
    Line = env['account.bank.statement.line']
    if not docs:
        return Line.browse(), 'sin docs'
    desc = docs[0].get('descripcion_movimiento') or ''
    iban = _iban(docs[0].get('cuenta_bancaria'))

    journals = env['account.journal'].search([('type', '=', 'bank')]).filtered(
        lambda j: j.bank_account_id and _iban(j.bank_account_id.acc_number) == iban)
    base = [('journal_id', 'in', journals.ids)] if journals else []

    cand = Line.search(base + [('payment_ref', '=', desc)]) if desc else Line.browse()
    if not cand and desc:
        cand = Line.search(base + [('payment_ref', 'ilike', desc[:60])])
    if not cand:
        return Line.browse(), 'no encontrada por descripción'

    # importe neto firmado del movimiento (según reversión)
    def neto(st_amount):
        return sum(abs(_f(d.get('importe_conciliado'))) *
                   (-1 if _es_reversal(d, st_amount) else 1) for d in docs)
    coincide = cand.filtered(lambda l: abs(l.amount - neto(l.amount)) <= EPS)
    if not coincide:
        return Line.browse(), ('descripción OK pero importe no cuadra (candidatas: %s)'
                               % cand.ids[:5])
    no_rec = coincide.filtered(lambda l: not l.is_reconciled)
    elegido = (no_rec or coincide)[:1]
    return elegido, 'emparejada por descripción+importe'


def _reconciliar_directo(env, st_line, docs):
    """Concilia el movimiento sobre `st_line` replicando la lógica corregida del
    conector, sin depender de id_movimiento. Autovalida con _check_balanced:
    si no cuadra, revierte y devuelve el error. Devuelve (ok, mensaje)."""
    is_credit = st_line.amount > 0
    docs_rec = []
    for doc in docs:
        ml = _move_line(env, doc.get('id_documento_erp'))
        importe = _f(doc.get('importe_conciliado'))
        if not ml or not importe:
            continue
        if ml.account_id.account_type not in ('asset_receivable', 'liability_payable'):
            continue
        docs_rec.append((ml, abs(importe), _es_reversal(doc, st_line.amount)))
    if not docs_rec:
        return False, 'sin documentos contables válidos'

    neto = sum(a * (-1 if rev else 1) for _ml, a, rev in docs_rec)
    if abs(st_line.amount - neto) > EPS:
        return False, ('neto documentos %.2f != importe extracto %.2f (revisar signo)'
                       % (neto, st_line.amount))

    if st_line.is_reconciled:
        st_line.action_undo_reconciliation()
    _liq, suspense_lines, other_lines = st_line._seek_for_lines()
    if not suspense_lines:
        return False, 'sin línea suspense tras preparar'

    move = st_line.move_id
    container = {"records": move, "self": move}
    to_reconcile = []
    try:
        with move._check_balanced(container):
            move.with_context(skip_account_move_synchronization=True,
                              force_delete=True, skip_invoice_sync=True).write(
                {"line_ids": [(2, l.id) for l in (suspense_lines + other_lines)]})
            for ml, amount, is_reversal in docs_rec:
                if is_credit:
                    debit = amount if is_reversal else 0.0
                    credit = 0.0 if is_reversal else amount
                else:
                    debit = 0.0 if is_reversal else amount
                    credit = amount if is_reversal else 0.0
                new_line = env['account.move.line'].with_context(
                    check_move_validity=False, skip_sync_invoice=True,
                    skip_invoice_sync=True).create({
                        'move_id': move.id,
                        'account_id': ml.account_id.id,
                        'partner_id': ml.partner_id.id,
                        'name': st_line.payment_ref or ml.name,
                        'debit': debit,
                        'credit': credit,
                    })
                to_reconcile.append(ml + new_line)
        for pair in to_reconcile:
            pair.reconcile()
        env.cr.commit()
        return True, 'ok (directo)'
    except Exception as e:
        env.cr.rollback()
        return False, str(e)


def analizar(env):
    dom = [
        ('triggered_event', '=', TRIGGERED_EVENT),
        ('operation_type', '=', 'response'),
        ('desencrypt_data', '!=', False),
    ]
    if COMPANY_IDS:
        dom.append(('company_id', 'in', COMPANY_IDS))
    if LOG_DATE_FROM:
        dom.append(('date_time', '>=', LOG_DATE_FROM))
    if LOG_DATE_TO:
        dom.append(('date_time', '<=', LOG_DATE_TO))
    logs = env['bankinplay.log'].sudo().search(dom, order='date_time asc')

    print("=" * 90)
    print("DESCUADRES CONCILIACIÓN TERCEROS | MODO: %s"
          % ('DIAGNÓSTICO (dry-run)' if DRY_RUN else '*** REPARACIÓN REAL ***'))
    print("Logs de conciliación con payload: %d" % len(logs))
    print("=" * 90)

    # 1) CONSOLIDACIÓN: para cada id_movimiento nos quedamos con el payload MÁS
    # COMPLETO (más documentos; a igualdad, el log más reciente). Así, si los
    # documentos de un movimiento vinieran repartidos en varios logs o reenviados,
    # trabajamos con el conjunto completo y agrupamos bien los N documentos.
    mejor = {}          # id_movimiento -> {sociedad, docs, event_data, log}
    multiples = set()   # id_movimiento visto en más de un log
    for log in logs:
        try:
            desencrypt_data = json.loads(log.desencrypt_data)
        except (TypeError, ValueError):
            continue
        req_log = log.related_log_id or log
        if not req_log.event_data:
            continue
        try:
            event_data = json.loads(req_log.event_data)
        except (TypeError, ValueError):
            continue

        for sociedad, id_movimiento, docs in _iter_movimientos(desencrypt_data):
            if ONLY_MOVEMENT_IDS and id_movimiento not in ONLY_MOVEMENT_IDS:
                continue
            if not _coincide_texto(docs):
                continue
            prev = mejor.get(id_movimiento)
            if prev is not None:
                multiples.add(id_movimiento)
            if (prev is None
                    or len(docs) > len(prev['docs'])
                    or (len(docs) == len(prev['docs'])
                        and log.date_time > prev['log'].date_time)):
                mejor[id_movimiento] = {
                    'sociedad': sociedad, 'docs': docs,
                    'event_data': event_data, 'log': log,
                }

    # 2) CANDIDATOS: movimientos con alguna factura conciliada-pero-abierta.
    candidatos = []
    for id_movimiento, m in mejor.items():
        abiertos = _docs_abiertos(env, m['docs'])
        if not abiertos:
            continue  # todos los documentos están saldados -> nada que reparar

        st_line = _statement_line_por_movimiento(env, id_movimiento)
        esperado = _unique_import_esperado(m['docs'], st_line)
        emparejable = bool(st_line) and st_line.unique_import_id == esperado
        # Vía alternativa (por datos) para los NO localizables por id_movimiento.
        if emparejable:
            st_datos, motivo_datos = st_line, 'no aplica (localizable por id)'
        else:
            st_datos, motivo_datos = _buscar_statement_line_por_datos(env, m['docs'])

        fecha_mov = _fecha_movimiento(st_line, st_datos, m['docs'])
        if _fuera_de_periodo(fecha_mov):
            continue  # fuera del periodo contable solicitado

        candidatos.append({
            'log': m['log'], 'sociedad': m['sociedad'], 'event_data': m['event_data'],
            'id_movimiento': id_movimiento, 'docs': m['docs'], 'abiertos': abiertos,
            'st_line': st_line, 'esperado': esperado, 'emparejable': emparejable,
            'st_datos': st_datos, 'motivo_datos': motivo_datos,
            'multiple': id_movimiento in multiples, 'fecha_mov': fecha_mov,
        })

    print("\nMovimientos con facturas abiertas (candidatos): %d\n" % len(candidatos))
    if not candidatos:
        print("No se detectan facturas conciliadas-pero-abiertas con el payload disponible.")
        print("Si esperabais casos aquí, revisad BUSCAR_TEXTO / fechas, o puede que el")
        print("payload del log esté purgado (>90 días) -> ver redescargar().")
        return

    print("LEYENDA:")
    print("  - 'localizable por id'    = la línea de extracto tiene el unique_import_id")
    print("                              que espera el conector (cuenta-diario-id_movimiento).")
    print("                              Se repara por REPLAY (reejecutar el conector corregido).")
    print("  - 'localizable por datos' = no casa por id, pero se encuentra la línea por")
    print("                              descripción + importe. Se repara en DIRECTO.")
    print("  - 'no localizable'        = no se encuentra la línea; revisión manual.\n")

    n_por_id = sum(1 for c in candidatos if c['emparejable'])
    n_por_datos = sum(1 for c in candidatos if not c['emparejable'] and c['st_datos'])
    n_manual = len(candidatos) - n_por_id - n_por_datos
    print("  Localizables por id (vía replay) . : %d" % n_por_id)
    print("  Localizables por datos (vía directa): %d" % n_por_datos)
    print("  No localizables (revisión manual) .: %d" % n_manual)

    reparados, fallidos = 0, 0
    detalle = 0
    for c in candidatos:
        st_line = c['st_line']
        detalle += 1
        if detalle <= MAX_DETALLE:
            print("-" * 90)
            desc = (c['abiertos'][0][0].get('descripcion_movimiento') or '')[:65]
            print("Mov %s | fecha %s | docs: %d | abiertas: %d%s | %s"
                  % (c['id_movimiento'], c.get('fecha_mov') or '?', len(c['docs']),
                     len(c['abiertos']),
                     ' | (docs en varios logs)' if c.get('multiple') else '', desc))
            for doc, ml in c['abiertos']:
                print("    doc erp=%s (%s) tipo=%s signo=%s importe=%.2f "
                      "conciliado=%.2f | move.line %s residual=%.2f"
                      % (doc.get('id_documento_erp'), ml.move_id.name,
                         doc.get('tipo_documento_codigo'), doc.get('signo_movimiento'),
                         _f(doc.get('importe')), _f(doc.get('importe_conciliado')),
                         ml.id, ml.amount_residual))
            if st_line:
                print("    extracto (por id): line %s | unique_import_id ACTUAL='%s'"
                      % (st_line.id, st_line.unique_import_id))
                print("              ESPERADO='%s' | localizable_por_id=%s | transitoria=%.2f"
                      % (c['esperado'], c['emparejable'], _suspense_residual(st_line)))
            else:
                print("    extracto (por id): NO localizado por id_movimiento (%s)"
                      % c['id_movimiento'])
            if not c['emparejable']:
                sd = c['st_datos']
                print("    extracto (por datos): %s%s"
                      % (('line %s (uii=%s) ' % (sd.id, sd.unique_import_id)) if sd else '',
                         c['motivo_datos']))

        if DRY_RUN:
            continue

        # Elección de vía de reparación.
        if c['emparejable']:
            try:
                st_line.action_undo_reconciliation()
                env['bankinplay.interface'].sudo().manage_conciliacion_terceros_callback(
                    _payload_reducido(c['sociedad'], c['docs']), c['event_data'])
                st_line.invalidate_recordset()
                residual = _suspense_residual(st_line)
                abiertos2 = _docs_abiertos(env, c['docs'])
                ok = st_line.is_reconciled and abs(residual) <= EPS and not abiertos2
                print("    -> %s (replay) residual=%.2f abiertas=%d"
                      % ('OK' if ok else 'REVISAR', residual, len(abiertos2)))
                reparados += 1 if ok else 0
                fallidos += 0 if ok else 1
            except Exception as e:
                env.cr.rollback()
                print("    -> ERROR (replay): %s" % e)
                _logger.exception("Fallo replay movimiento %s", c['id_movimiento'])
                fallidos += 1
        elif c['st_datos']:
            ok, msg = _reconciliar_directo(env, c['st_datos'], c['docs'])
            if ok:
                # Verificación post-reparación: transitoria a 0 y facturas cerradas.
                c['st_datos'].invalidate_recordset()
                residual = _suspense_residual(c['st_datos'])
                abiertos2 = _docs_abiertos(env, c['docs'])
                ok = abs(residual) <= EPS and not abiertos2
                msg = "%s | residual=%.2f abiertas=%d" % (msg, residual, len(abiertos2))
            print("    -> %s (directo) %s" % ('OK' if ok else 'REVISAR', msg))
            reparados += 1 if ok else 0
            fallidos += 0 if ok else 1
        else:
            print("    -> SIN REPARAR: no localizable (revisión manual).")
            fallidos += 1

    print("=" * 90)
    if DRY_RUN:
        print("DIAGNÓSTICO terminado. Revisa el listado.")
        print("Para reparar (replay + directo) pon DRY_RUN=False.")
        if n_manual:
            print("Quedan %d movimientos no localizables ni por id ni por datos: los"
                  " revisamos juntos (posible conciliación manual)." % n_manual)
    else:
        print("HECHO. Reparados: %d | Para revisar/fallidos: %d" % (reparados, fallidos))
        if n_manual:
            print("Quedan %d movimientos no localizables sin tocar (revisión manual)."
                  % n_manual)
    print("=" * 90)


def redescargar(env, company_id, fecha_desde, fecha_hasta=None):
    """OPT-IN. Vuelve a pedir a BankInPlay la conciliación de terceros de un RANGO
    acotado [fecha_desde, fecha_hasta] (str 'YYYY-MM-DD' o date), para regenerar
    el log/payload de un periodo cuyo log se purgó SIN bajar todo el histórico.
    Requiere conciliation_online_bankinplay >= 16.1.5 (soporte fecha_hasta). Uso:
        redescargar(env, <company_id>, '2026-06-01', '2026-06-30')
    """
    company = env['res.company'].sudo().browse(company_id).exists()
    if not company:
        print("Compañía %s no encontrada." % company_id)
        return
    if hasattr(fecha_desde, 'strftime'):
        fecha_desde = fecha_desde.strftime('%Y-%m-%d')
    if hasattr(fecha_hasta, 'strftime'):
        fecha_hasta = fecha_hasta.strftime('%Y-%m-%d')
    print("Cía %s: re-descarga conciliación %s -> %s"
          % (company.id, fecha_desde, fecha_hasta or '(hoy)'))
    if DRY_RUN:
        print("  [DRY_RUN] No se lanza. Pon DRY_RUN=False para ejecutar.")
        return
    company.with_context(company_id=company.id).with_delay(
        max_retries=0).bankinplay_import_documents(fecha_desde, fecha_hasta)
    env.cr.commit()
    print("  Petición encolada. Espera el callback, revisa logs y re-ejecuta analizar().")


# En `odoo-bin shell` la variable `env` ya existe.
try:
    env  # noqa: F821
except NameError:
    raise SystemExit("Ejecuta este script dentro de `odoo-bin shell` (no hay 'env').")

analizar(env)
