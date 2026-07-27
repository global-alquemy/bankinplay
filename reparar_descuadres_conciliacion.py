# -*- coding: utf-8 -*-
# Alquemy - Reparación de asientos descuadrados por conciliación de terceros (BankInPlay)
#
# QUÉ ES EL DESCUADRE
# -------------------
# Al conciliar un cobro con varios documentos (facturas + rectificativa/anticipo),
# la rectificativa se contabilizaba en el lado equivocado (Haber en vez de Debe en
# un cobro). Resultado: el asiento del extracto queda con DEBE != HABER (le falta
# la contrapartida correcta). Ejemplo EROSKI (BNK3/2026/00118): banco 27.329,21 al
# Debe + 4 líneas de cliente 40.744,03 al Haber => descuadre de 13.414,82
# (= 2 x 6.707,41, la rectificativa 26R0011 mal puesta).
#
# QUÉ HACE ESTE SCRIPT
# --------------------
#   1) DETECTA (contable, en TODOS los diarios y TODAS las empresas): asientos de
#      extracto cuyo DEBE != HABER. No depende de bankinplay_journal_ids ni de
#      is_reconciled (por eso antes salía 0: se limitaba a esos diarios).
#   2) ENLAZA cada descuadre con su payload de BankInPlay: del unique_import_id de
#      la línea saca el id_movimiento (parte final) y busca en los logs los
#      documentos de ese movimiento (facturas + rectificativa, con importe y signo).
#   3) REPARA (vía directa, autocontenida, NO depende del conector desplegado):
#      deshace el asiento (action_undo_reconciliation) y lo rehace con el signo
#      correcto (rectificativa al lado contrario). Va envuelto en _check_balanced:
#      si no cuadra, revierte y avisa. NUNCA deja un asiento descuadrado.
#   4) Los descuadres SIN payload en logs (purgado >90 días) se listan aparte con
#      la sugerencia de redescargar() para regenerarlos.
#
# CÓMO EJECUTAR
#   ./odoo-bin shell -d <BASE_DE_DATOS> --no-http < reparar_descuadres_conciliacion.py
#   1ª vez: DRY_RUN=True (solo informa). Acotables: FECHA_DESDE/HASTA (periodo),
#   BUSCAR_TEXTO='EROSKI', ONLY_MOVEMENT_IDS, COMPANY_IDS.
#
# Antes de reparar en real: copia de seguridad + prueba en staging.

import json
import logging

_logger = logging.getLogger("bankinplay.reparacion")

# ==========================================================================
# CONFIGURACIÓN
# ==========================================================================
DRY_RUN = True                 # True = solo diagnostica. False = repara.

TRIGGERED_EVENT = 'exportacion_conciliacion_terceros'

COMPANY_IDS = []               # p.ej. [1]. VACÍO = TODAS las empresas (recomendado).
                               # (El detector recorre TODOS los diarios y TODAS las
                               #  empresas; no depende de bankinplay_journal_ids.)
FECHA_DESDE = False            # 'YYYY-MM-DD' por fecha del asiento (periodo contable)
FECHA_HASTA = False            # 'YYYY-MM-DD'
ONLY_MOVEMENT_IDS = []         # p.ej. ['471656237']
BUSCAR_TEXTO = ''              # subcadena de payment_ref (p.ej. 'EROSKI')

EPS = 0.005                    # tolerancia de descuadre (moneda compañía)
MAX_DETALLE = 500              # máximo de líneas de detalle a imprimir
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
    return env['account.move.line'].sudo().browse(mlid).exists()


def _es_reversal(doc, st_amount):
    """True si el documento es reversión (rectificativa/anticipo) respecto al signo
    del movimiento. Robusto a las dos codificaciones: importe_conciliado < 0, o
    signo_movimiento contrario al del cobro/pago."""
    imp = _f(doc.get('importe_conciliado'))
    if imp < 0:
        return True
    signo = (doc.get('signo_movimiento') or '').lower()
    mov = 'cobro' if st_amount > 0 else 'pago'
    return bool(signo) and signo != mov


def _mapa_payloads(env):
    """Construye {id_movimiento: {sociedad, docs, event_data}} a partir de los logs
    de conciliación con payload. Consolida por id_movimiento el payload más completo
    (más documentos; a igualdad, el más reciente), aunque venga en varios logs."""
    dom = [
        ('triggered_event', '=', TRIGGERED_EVENT),
        ('operation_type', '=', 'response'),
        ('desencrypt_data', '!=', False),
    ]
    if COMPANY_IDS:
        dom.append(('company_id', 'in', COMPANY_IDS))
    logs = env['bankinplay.log'].sudo().search(dom, order='date_time asc')

    def _upd(mp, key, entry):
        prev = mp.get(key)
        if prev is None or len(entry['docs']) > len(prev['docs']) or (
                len(entry['docs']) == len(prev['docs'])
                and entry['date_time'] > prev['date_time']):
            mp[key] = entry

    por_id, por_desc = {}, {}   # {id_movimiento: entry}, {descripcion_norm: entry}
    for log in logs:
        try:
            data = json.loads(log.desencrypt_data)
        except (TypeError, ValueError):
            continue
        req_log = log.related_log_id or log
        if not req_log.event_data:
            continue
        try:
            event_data = json.loads(req_log.event_data)
        except (TypeError, ValueError):
            continue
        for soc in (data.get('sociedades') or []):
            por_mov = {}
            for doc in (soc.get('documentos') or []):
                idm = str(doc.get('id_movimiento') or '')
                if idm:
                    por_mov.setdefault(idm, []).append(doc)
            for idm, docs in por_mov.items():
                entry = {'sociedad': soc, 'docs': docs,
                         'event_data': event_data, 'date_time': log.date_time}
                _upd(por_id, idm, entry)
                descr = _norm(docs[0].get('descripcion_movimiento') if docs else '')
                if descr:
                    _upd(por_desc, descr, entry)
    return por_id, por_desc


def _norm(s):
    """Normaliza una descripción para comparar (colapsa espacios, minúsculas)."""
    return ' '.join((s or '').split()).strip().lower()


def _buscar_payload(por_id, por_desc, st_line):
    """Enlaza la línea descuadrada con su payload. Prioriza la DESCRIPCIÓN
    (payment_ref == descripcion_movimiento), que es el enlace fiable; usa el
    id_movimiento del unique_import_id solo como último recurso."""
    key = _norm(st_line.payment_ref)
    if key and key in por_desc:
        return por_desc[key]
    # tolerancia: una contiene a la otra (diferencias menores de formato)
    if key:
        for k, v in por_desc.items():
            if key in k or k in key:
                return v
    idm = (st_line.unique_import_id or '').split('-')[-1]
    return por_id.get(idm)


def _buscar_descuadres(env):
    """Asientos de extracto (TODOS los diarios) con DEBE != HABER.
    Devuelve [(statement_line, descuadre_importe)]."""
    sql = """
        SELECT am.statement_line_id,
               COALESCE(SUM(aml.debit), 0)  AS d,
               COALESCE(SUM(aml.credit), 0) AS c
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        WHERE am.statement_line_id IS NOT NULL
    """
    params = []
    if COMPANY_IDS:
        sql += " AND am.company_id IN %s"
        params.append(tuple(COMPANY_IDS))
    if FECHA_DESDE:
        sql += " AND am.date >= %s"
        params.append(FECHA_DESDE)
    if FECHA_HASTA:
        sql += " AND am.date <= %s"
        params.append(FECHA_HASTA)
    sql += """
        GROUP BY am.statement_line_id
        HAVING ABS(COALESCE(SUM(aml.debit), 0) - COALESCE(SUM(aml.credit), 0)) > %s
        ORDER BY am.statement_line_id
    """
    params.append(EPS)
    env.cr.execute(sql, params)
    rows = env.cr.fetchall()

    Line = env['account.bank.statement.line'].sudo()
    txt = (BUSCAR_TEXTO or '').lower()
    out = []
    for slid, d, c in rows:
        sl = Line.browse(slid)
        idm = (sl.unique_import_id or '').split('-')[-1]
        if ONLY_MOVEMENT_IDS and idm not in ONLY_MOVEMENT_IDS:
            continue
        if txt and txt not in (sl.payment_ref or '').lower():
            continue
        out.append((sl, round(d - c, 2)))
    return out


def _descuadre(st_line):
    """Descuadre actual del asiento = Debe - Haber (0 = cuadrado)."""
    move = st_line.move_id
    return round(sum(move.line_ids.mapped('debit'))
                 - sum(move.line_ids.mapped('credit')), 2)


def _reconciliar_directo(env, st_line, docs):
    """Rehace la conciliación sobre `st_line` con el signo correcto, sin depender
    del conector. Autovalida con _check_balanced: si no cuadra, revierte y devuelve
    el error. Devuelve (ok, mensaje)."""
    st_line = st_line.sudo()
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
        return False, 'sin documentos contables válidos en el payload'

    neto = sum(a * (-1 if rev else 1) for _ml, a, rev in docs_rec)
    if abs(st_line.amount - neto) > EPS:
        return False, ('neto documentos %.2f != importe extracto %.2f (revisar signo)'
                       % (neto, st_line.amount))

    # Partir de base limpia (banco + transitoria) deshaciendo lo que haya.
    move = st_line.move_id
    if st_line.is_reconciled or abs(_descuadre(st_line)) > EPS:
        st_line.action_undo_reconciliation()
    _liq, suspense_lines, other_lines = st_line._seek_for_lines()
    if not suspense_lines:
        return False, 'sin línea transitoria tras preparar'

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
                new_line = move.env['account.move.line'].with_context(
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
        return True, 'ok'
    except Exception as e:
        env.cr.rollback()
        return False, str(e)


def reparar(env):
    print("=" * 92)
    print("ASIENTOS DESCUADRADOS (debe != haber) - TODOS los diarios | MODO: %s"
          % ('DIAGNÓSTICO (dry-run)' if DRY_RUN else '*** REPARACIÓN REAL ***'))
    print("=" * 92)

    por_id, por_desc = _mapa_payloads(env)
    descuadres = _buscar_descuadres(env)

    total = sum(abs(d) for _sl, d in descuadres)
    con, sin = [], []
    for sl, desc in descuadres:
        idm = (sl.unique_import_id or '').split('-')[-1]
        m = _buscar_payload(por_id, por_desc, sl)
        (con if m else sin).append((sl, desc, idm, m))

    print("  Asientos descuadrados detectados . . . . . . . . . : %d" % len(descuadres))
    print("  DESCUADRE TOTAL (suma |debe - haber|) . . . . . . . : %.2f" % total)
    print("  Con payload en logs (reparables) . . . . . . . . . : %d" % len(con))
    print("  SIN payload (purgado -> redescargar) . . . . . . . : %d" % len(sin))
    if not descuadres:
        print("  No hay asientos descuadrados con los filtros dados.")
        return

    reparados, fallidos = 0, 0
    detalle = 0
    for sl, desc, idm, m in con + sin:
        detalle += 1
        if detalle <= MAX_DETALLE:
            print("-" * 92)
            print("Mov %s | %s | %s | importe %.2f | DESCUADRE %.2f | %s"
                  % (idm, sl.date, sl.journal_id.code, sl.amount, desc,
                     'CON payload' if m else 'SIN payload'))
            print("    %s" % (sl.payment_ref or '')[:88])

        if DRY_RUN:
            continue
        if not m:
            print("    -> SIN REPARAR: falta payload. Usa redescargar() del periodo.")
            fallidos += 1
            continue
        ok, msg = _reconciliar_directo(env, sl, m['docs'])
        if ok:
            desc2 = _descuadre(sl)
            ok = abs(desc2) <= EPS
            msg = "%s | descuadre tras reparar=%.2f" % (msg, desc2)
        print("    -> %s %s" % ('OK' if ok else 'REVISAR', msg))
        reparados += 1 if ok else 0
        fallidos += 0 if ok else 1

    print("=" * 92)
    if DRY_RUN:
        print("DIAGNÓSTICO terminado. Pon DRY_RUN=False para reparar los 'CON payload'.")
    else:
        print("HECHO. Reparados: %d | Para revisar/fallidos: %d" % (reparados, fallidos))
    if sin:
        por_cia = {}
        for sl, desc, idm, m in sin:
            por_cia.setdefault(sl.company_id, []).append(sl.date)
        print("Re-descarga sugerida para los SIN payload:")
        for cia, fechas in por_cia.items():
            print("    redescargar(env, %s, '%s', '%s')   # %s"
                  % (cia.id, str(min(fechas)), str(max(fechas)), cia.name))
    print("=" * 92)


def redescargar(env, company_id, fecha_desde, fecha_hasta=None):
    """OPT-IN. Vuelve a pedir a BankInPlay la conciliación de terceros de un rango
    [fecha_desde, fecha_hasta] ('YYYY-MM-DD' o date) para regenerar el payload de un
    periodo cuyo log se purgó. Requiere conciliation_online_bankinplay >= 16.1.5.
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
    print("  Petición encolada. Espera el callback, revisa logs y re-ejecuta reparar().")


# En `odoo-bin shell` la variable `env` ya existe.
try:
    env  # noqa: F821
except NameError:
    raise SystemExit("Ejecuta este script dentro de `odoo-bin shell` (no hay 'env').")

reparar(env)
