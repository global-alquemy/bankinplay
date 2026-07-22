# -*- coding: utf-8 -*-
# Alquemy - Reparación de asientos descuadrados por conciliación de terceros (BankInPlay)
#
# CONTEXTO
# --------
# Antes del fix de signo (conciliation_online_bankinplay >= 16.1.4.0.0), los
# documentos con importe_conciliado < 0 (anticipos / rectificativas) se
# contabilizaban SIEMPRE en el Haber, ignorando que en un cobro deben ir al
# Debe. Resultado: la conciliación de ese movimiento bancario quedaba mal
# (residual atascado en la cuenta transitoria/suspense + facturas del
# movimiento sin saldar).
#
# El código nuevo YA impide que se generen nuevos casos. Este script repara los
# HISTÓRICOS y NO debe programarse como cron: es un proceso puntual (se ejecuta,
# se revisa y se termina).
#
# ESTRATEGIA (robusta, no toca importes a mano)
# ---------------------------------------------
#   1. Recorre los logs de respuesta de conciliación de terceros que aún
#      conservan el payload descifrado (desencrypt_data). El payload lleva los
#      documentos con su importe_conciliado (signo incluido).
#   2. Detecta los movimientos "de riesgo": los que incluían algún documento con
#      importe_conciliado < 0 Y cuya línea de extracto quedó mal conciliada
#      (residual en transitoria) o cuyas facturas siguen abiertas.
#   3. Para cada uno: action_undo_reconciliation() -> deja la línea de extracto
#      como recién importada (banco + transitoria), y re-ejecuta la conciliación
#      con el código YA corregido, pero SOLO para ese movimiento (payload
#      reducido). Después verifica que el asiento cuadra y la transitoria queda a
#      cero.
#
# CASOS SIN LOG (payload purgado, >90 días de retención)
# ------------------------------------------------------
# Si un descuadre no tiene log/payload no se puede "replayar". El script los
# LISTA aparte (listar_sin_log) para que se vean, y ofrece redescargar() para
# volver a pedir a BankInPlay los documentos de conciliación de ese periodo:
# regenera el log y, de paso, reconcilia bien las líneas aún pendientes. Después
# se vuelve a ejecutar reparar() para los que siguieran conciliados-pero-mal.
#
# ARRANCA EN MODO SIMULACIÓN (DRY_RUN = True): no escribe nada, solo informa.
# Revisa el listado, y cuando estés conforme pon DRY_RUN = False.
#
# CÓMO EJECUTAR
# -------------
#   ./odoo-bin shell -d <BASE_DE_DATOS> --no-http < reparar_descuadres_conciliacion.py
#   (o pega el contenido dentro de `odoo-bin shell`)
#
# ⚠️ ANTES DE EJECUTAR EN REAL:
#   - Haz copia de seguridad de la base de datos.
#   - Pruébalo primero en una copia de staging.
#   - El re-procesado hace commits parciales (uno por movimiento reparado), igual
#     que el flujo normal del conector.

import json
import logging

_logger = logging.getLogger("bankinplay.reparacion")

# ==========================================================================
# CONFIGURACIÓN
# ==========================================================================
DRY_RUN = True                 # True = solo informa. False = repara de verdad.

TRIGGERED_EVENT = 'exportacion_conciliacion_terceros'

# Filtros opcionales para acotar el barrido (dejar vacío = sin filtro):
COMPANY_IDS = []               # p.ej. [1] para limitar a una compañía
DATE_FROM = False              # p.ej. '2026-06-01 00:00:00' (filtra logs por date_time)
DATE_TO = False                # p.ej. '2026-07-31 23:59:59'
ONLY_MOVEMENT_IDS = []         # p.ej. ['471656237'] para reparar solo esos id_movimiento

# Tolerancia para considerar "residual 0" (moneda de la compañía).
EPS = 0.005
# ==========================================================================


def _f(x):
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _iter_movimientos(desencrypt_data):
    """Reproduce el troceo del conector: sociedades[0].documentos agrupados por
    id_movimiento. Devuelve (sociedad, {id_movimiento: [docs]})."""
    if not desencrypt_data or not desencrypt_data.get('sociedades'):
        return None, {}
    sociedades = desencrypt_data.get('sociedades') or []
    if not sociedades:
        return None, {}
    sociedad = sociedades[0]
    por_mov = {}
    for doc in sociedad.get('documentos', []) or []:
        idm = str(doc.get('id_movimiento') or '')
        if not idm:
            continue
        por_mov.setdefault(idm, []).append(doc)
    return sociedad, por_mov


def _statement_line(env, id_movimiento):
    """Localiza la línea de extracto por unique_import_id, SIN filtrar por
    is_reconciled (queremos también las ya conciliadas / mal conciliadas)."""
    return env['account.bank.statement.line'].search(
        [('unique_import_id', 'like', id_movimiento)], limit=1)


def _suspense_residual(st_line):
    """Suma de residual de las líneas en cuenta suspense/transitoria del asiento."""
    if not st_line:
        return 0.0
    _liq, suspense_lines, _other = st_line._seek_for_lines()
    return sum(suspense_lines.mapped('amount_residual'))


def _docs_abiertos(env, docs):
    """Devuelve las move.line de los documentos que siguen SIN saldar."""
    abiertos = []
    for doc in docs:
        mlid = doc.get('id_documento_erp')
        if not mlid:
            continue
        ml = env['account.move.line'].browse(int(mlid)).exists()
        if ml and abs(ml.amount_residual) > EPS:
            abiertos.append(ml)
    return abiertos


def _es_candidato(env, docs, st_line):
    """Precondición del bug (algún importe_conciliado < 0) + evidencia de que la
    conciliación quedó mal (transitoria con residual, línea no conciliada o
    facturas abiertas)."""
    tiene_ac = any(_f(d.get('importe_conciliado')) < 0 for d in docs)
    if not tiene_ac:
        return False, ''
    motivos = []
    if st_line and not st_line.is_reconciled:
        motivos.append('linea no conciliada')
    if abs(_suspense_residual(st_line)) > EPS:
        motivos.append('residual en transitoria')
    if _docs_abiertos(env, docs):
        motivos.append('facturas abiertas')
    if not motivos:
        return False, ''
    return True, ', '.join(motivos)


def _payload_reducido(sociedad, docs):
    """Payload de un único movimiento, respetando la estructura que espera
    manage_conciliacion_terceros_callback (sociedades[0].documentos)."""
    soc = dict(sociedad)
    soc['documentos'] = docs
    return {'sociedades': [soc]}


def reparar(env):
    interface = env['bankinplay.interface'].sudo()

    dom = [
        ('triggered_event', '=', TRIGGERED_EVENT),
        ('operation_type', '=', 'response'),
        ('desencrypt_data', '!=', False),
    ]
    if COMPANY_IDS:
        dom.append(('company_id', 'in', COMPANY_IDS))
    if DATE_FROM:
        dom.append(('date_time', '>=', DATE_FROM))
    if DATE_TO:
        dom.append(('date_time', '<=', DATE_TO))

    logs = env['bankinplay.log'].sudo().search(dom, order='date_time asc')
    print("=" * 78)
    print("REPARACIÓN DESCUADRES CONCILIACIÓN TERCEROS  |  MODO: %s"
          % ('SIMULACIÓN (dry-run)' if DRY_RUN else '*** EJECUCIÓN REAL ***'))
    print("Logs de conciliación con payload a revisar: %d" % len(logs))
    print("=" * 78)

    candidatos = []    # (log, sociedad, id_movimiento, docs, st_line, motivo, event_data)
    vistos = set()     # id_movimiento ya evaluados como candidato (evita duplicados)
    ids_en_logs = set()  # TODOS los id_movimiento que aparecen en algún log (para "sin log")

    for log in logs:
        try:
            desencrypt_data = json.loads(log.desencrypt_data)
        except (TypeError, ValueError):
            continue

        # event_data vive en el log de petición (related_log_id) o, en su
        # defecto, en este mismo log.
        req_log = log.related_log_id or log
        if not req_log.event_data:
            continue
        try:
            event_data = json.loads(req_log.event_data)
        except (TypeError, ValueError):
            continue

        sociedad, por_mov = _iter_movimientos(desencrypt_data)
        if not sociedad:
            continue

        for id_movimiento, docs in por_mov.items():
            ids_en_logs.add(id_movimiento)
            if ONLY_MOVEMENT_IDS and id_movimiento not in ONLY_MOVEMENT_IDS:
                continue
            if id_movimiento in vistos:
                continue
            vistos.add(id_movimiento)

            st_line = _statement_line(env, id_movimiento)
            es_cand, motivo = _es_candidato(env, docs, st_line)
            if not es_cand:
                continue
            candidatos.append((log, sociedad, id_movimiento, docs,
                               st_line, motivo, event_data))

    print("\nMovimientos candidatos a reparar (CON log/payload): %d\n" % len(candidatos))

    reparados, fallidos = 0, 0
    for (log, sociedad, id_movimiento, docs, st_line, motivo, event_data) in candidatos:
        ref = st_line.display_name if st_line else '(sin línea de extracto)'
        acs = [d for d in docs if _f(d.get('importe_conciliado')) < 0]
        print("-" * 78)
        print("Mov %s | %s" % (id_movimiento, ref))
        print("  Log #%s (%s) | docs: %d | rectificativas/AC: %d | motivo: %s"
              % (log.id, log.date_time, len(docs), len(acs), motivo))
        print("  Transitoria residual: %.2f" % _suspense_residual(st_line))

        if DRY_RUN:
            continue
        if not st_line:
            print("  -> SALTADO: no se encuentra la línea de extracto.")
            fallidos += 1
            continue

        try:
            # 1) Deshacer la conciliación mala (idempotente: si ya estaba
            #    limpia, la deja igual).
            st_line.action_undo_reconciliation()
            # 2) Re-ejecutar SOLO este movimiento con el código corregido.
            interface.manage_conciliacion_terceros_callback(
                _payload_reducido(sociedad, docs), event_data)
            # 3) Verificar.
            st_line.invalidate_recordset()
            residual = _suspense_residual(st_line)
            abiertos = _docs_abiertos(env, docs)
            if st_line.is_reconciled and abs(residual) <= EPS and not abiertos:
                print("  -> OK: conciliado, transitoria a 0, facturas saldadas.")
                reparados += 1
            else:
                print("  -> REVISAR: reconciled=%s residual=%.2f abiertas=%d"
                      % (st_line.is_reconciled, residual, len(abiertos)))
                fallidos += 1
        except Exception as e:
            env.cr.rollback()
            print("  -> ERROR: %s" % e)
            _logger.exception("Fallo reparando movimiento %s", id_movimiento)
            fallidos += 1

    print("=" * 78)
    if DRY_RUN:
        print("SIMULACIÓN terminada. Revisa el listado y pon DRY_RUN = False para reparar.")
    else:
        print("HECHO. Reparados: %d | Para revisar/fallidos: %d" % (reparados, fallidos))
    print("=" * 78)

    # Siempre (read-only): descuadres SIN log/payload -> requieren re-descarga.
    listar_sin_log(env, ids_en_logs)


def listar_sin_log(env, ids_en_logs):
    """READ-ONLY. Lista líneas de extracto de diarios BankInPlay que están SIN
    conciliar / con residual en transitoria y cuyo id_movimiento NO aparece en
    ningún log (payload purgado o nunca guardado). Estas NO se pueden reparar
    por replay: hay que RE-DESCARGARLAS de BankInPlay (ver redescargar()).
    """
    companies = env['res.company'].sudo().search([('bankinplay_enabled', '=', True)])
    if COMPANY_IDS:
        companies = companies.filtered(lambda c: c.id in COMPANY_IDS)

    journal_ids = companies.mapped('bankinplay_journal_ids').ids
    if not journal_ids:
        return

    dom = [
        ('journal_id', 'in', journal_ids),
        ('is_reconciled', '=', False),
    ]
    if DATE_FROM:
        dom.append(('date', '>=', DATE_FROM[:10]))
    if DATE_TO:
        dom.append(('date', '<=', DATE_TO[:10]))
    lineas = env['account.bank.statement.line'].sudo().search(dom, order='date asc')

    sin_log = []
    for sl in lineas:
        uii = sl.unique_import_id or ''
        idm = uii.rsplit('-', 1)[-1] if uii else ''
        if idm and idm in ids_en_logs:
            continue  # tiene payload -> ya lo cubre la reparación por replay
        if abs(sl.amount_residual) <= EPS:
            continue
        sin_log.append((sl, idm))

    print("\n" + "=" * 78)
    print("LÍNEAS BANKINPLAY SIN LOG (requieren RE-DESCARGA): %d" % len(sin_log))
    print("=" * 78)
    if not sin_log:
        print("Ninguna. Todo descuadre detectado tiene su payload disponible.")
        return

    por_cia = {}
    for sl, idm in sin_log:
        print("  Cía %-3s | diario %-10s | %s | mov %s | residual %.2f"
              % (sl.company_id.id, sl.journal_id.code, sl.date, idm or '?',
                 sl.amount_residual))
        d = por_cia.setdefault(sl.company_id, [])
        d.append(sl.date)

    print("\n  Sugerencia de re-descarga por compañía (fecha más antigua detectada):")
    for cia, fechas in por_cia.items():
        print("    - Cía %s (%s): redescargar(env, %s, '%s')"
              % (cia.id, cia.name, cia.id, min(fechas)))
    print("  (Ver la función redescargar() más abajo para lanzar la petición.)")


def redescargar(env, company_id, fecha_desde):
    """OPT-IN. Vuelve a pedir a BankInPlay los documentos de conciliación de
    terceros desde `fecha_desde` (str 'YYYY-MM-DD' o date), para regenerar los
    logs/payload de un periodo cuyo log se purgó.

    Mecánica: la petición usa `company.bankinplay_last_syncdate - 2 días` como
    `fecha_conciliacion_desde`. Aquí bajamos temporalmente esa fecha, lanzamos
    la importación (queue job) y BankInPlay responde por webhook regenerando el
    log con payload. El propio callback:
      - reconcilia bien las líneas que estén is_reconciled=False (las repara), y
      - al terminar deja bankinplay_last_syncdate = hoy (se restaura solo).

    Tras la re-descarga, vuelve a ejecutar reparar() para los casos que
    siguieran conciliados-pero-mal (is_reconciled=True, que el callback salta).

    ⚠️ Llama a la API real de BankInPlay. Hazlo en una ventana controlada
    (evita solape con el cron). Uso:
        redescargar(env, <company_id>, '2026-06-01')
    """
    company = env['res.company'].sudo().browse(company_id).exists()
    if not company:
        print("Compañía %s no encontrada." % company_id)
        return
    if hasattr(fecha_desde, 'strftime'):
        fecha_desde = fecha_desde.strftime('%Y-%m-%d')

    anterior = company.bankinplay_last_syncdate
    print("Cía %s: bankinplay_last_syncdate %s -> %s (re-descarga)"
          % (company.id, anterior, fecha_desde))
    if DRY_RUN:
        print("  [DRY_RUN] No se lanza la petición. Pon DRY_RUN=False para ejecutar.")
        return

    from odoo import fields as _fields
    company.bankinplay_last_syncdate = _fields.Date.to_date(fecha_desde)
    # Encola el job (igual que el botón del asistente). Al llegar el callback se
    # regenera el log y se reconcilian las líneas pendientes.
    company.with_context(company_id=company.id).with_delay(
        max_retries=0).bankinplay_import_documents()
    env.cr.commit()
    print("  Petición encolada. Espera a que llegue el callback de BankInPlay,")
    print("  revisa los nuevos logs y luego re-ejecuta reparar().")


# En `odoo-bin shell` la variable `env` ya existe.
try:
    env  # noqa: F821
except NameError:
    raise SystemExit("Ejecuta este script dentro de `odoo-bin shell` (no hay 'env').")

reparar(env)
