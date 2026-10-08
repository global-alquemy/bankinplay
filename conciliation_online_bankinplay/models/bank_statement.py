# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
import itertools
import re

from odoo import _, api, models, fields, Command
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang

# Tope de contrapartidas candidatas al buscar la combinación que cuadra un
# asiento descuadrado (2^12 combinaciones como mucho).
DESCUADRE_MAX_CANDIDATES = 12


def _bankinplay_norm(text):
    """Texto en mayúsculas solo con letras y dígitos (para buscar nº de factura)."""
    return re.sub(r'[^0-9A-Z]', '', (text or '').upper())


class BankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    bankinplay_sent = fields.Boolean(string='Enviado a BankinPlay', default=False)
    bankinplay_conciliation = fields.Boolean(string='Bankinplay conciliation', default=False)

    # ------------------------------------------------------------------
    # Reset robusto (tolerante a asientos YA descuadrados)
    # ------------------------------------------------------------------
    def _bankinplay_reset_move(self):
        """Deshace la conciliación y reconstruye el asiento a su estado limpio
        (banco + transitoria), TOLERANTE a asientos que ya estén descuadrados.

        El `action_undo_reconciliation` del core falla si el asiento actual no
        cuadra (p. ej. los descuadres históricos del conector antiguo: debe != haber),
        porque `_check_balanced` salta sobre el estado roto. Aquí reescribimos las
        líneas con `check_move_validity=False`, que desactiva ese chequeo durante
        la escritura; el resultado (banco + transitoria) SÍ cuadra.
        """
        self.ensure_one()
        self.line_ids.remove_move_reconcile()
        self.payment_ids.unlink()
        self.with_context(force_delete=True, check_move_validity=False).write({
            'to_check': False,
            'line_ids': [Command.clear()] + [
                Command.create(vals) for vals in self._prepare_move_line_default_vals()],
        })
        return True

    # ------------------------------------------------------------------
    # Conciliación 16.0 Community (reemplaza process_reconciliation_oca de 15.0)
    # ------------------------------------------------------------------
    def _bankinplay_apply_reconciliation(self, counterparts, new_aml, replace_bank_line=False):
        """Rehace el asiento de la línea de extracto: quita la transitoria y las
        líneas previas, crea las contrapartidas de factura (que se reconcilian
        contra su apunte) y las líneas write-off / apuntes (que no se reconcilian),
        todo envuelto en ``move._check_balanced``.

        Reemplaza en odoo16 Community el ``process_reconciliation_oca`` de odoo15.

        :param counterparts: lista de dicts
            ``{'move_line': account.move.line, 'name': str, 'debit': float, 'credit': float}``
            La contrapartida se crea en la MISMA cuenta que ``move_line`` (430/400)
            y en el lado que indique debit/credit, y se reconcilia con ``move_line``.
        :param new_aml: lista de dicts para líneas que NO se reconcilian
            ``{'name', 'debit', 'credit', 'account_id', 'partner_id'?, 'analytic_distribution'?}``
        :param replace_bank_line: si True, elimina también la línea de liquidez del
            diario (la 572...). En ese caso la pata de banco la aporta ``new_aml`` en
            la cuenta que manda BankInPlay (p.ej. la 5201 de una línea de crédito
            histórica). Odoo la reconoce como línea de banco vía el fallback de
            ``_seek_for_lines`` si esa cuenta es de tipo 'Banco y efectivo' o
            'Tarjeta de crédito'. Así el histórico queda en la cuenta antigua.

        Si el asiento no cuadra, ``_check_balanced`` lanza y el llamador (el
        procesador del inbox) lo captura y deja el registro en ``error``.
        NUNCA deja un asiento descuadrado.
        """
        self.ensure_one()
        AML = self.env['account.move.line']
        liquidity_lines, suspense_lines, other_lines = self._seek_for_lines()
        move = self.move_id
        container = {"records": move, "self": move}
        to_reconcile = []
        lines_to_remove = suspense_lines + other_lines
        if replace_bank_line:
            lines_to_remove += liquidity_lines
        with move._check_balanced(container):
            move.with_context(
                skip_account_move_synchronization=True,
                force_delete=True,
                skip_invoice_sync=True,
            ).write({"line_ids": [(2, line.id) for line in lines_to_remove]})

            for cp in counterparts:
                move_line = cp['move_line']
                new_line = AML.with_context(
                    check_move_validity=False,
                    skip_sync_invoice=True,
                    skip_invoice_sync=True,
                ).create({
                    'move_id': move.id,
                    'account_id': move_line.account_id.id,
                    'partner_id': move_line.partner_id.id,
                    'name': cp.get('name') or move_line.name,
                    'debit': cp.get('debit', 0.0),
                    'credit': cp.get('credit', 0.0),
                })
                to_reconcile.append(move_line + new_line)

            for vals in new_aml:
                AML.with_context(
                    check_move_validity=False,
                    skip_sync_invoice=True,
                    skip_invoice_sync=True,
                ).create({
                    'move_id': move.id,
                    'account_id': vals['account_id'],
                    'partner_id': vals.get('partner_id', False),
                    'name': vals.get('name', ''),
                    'debit': vals.get('debit', 0.0),
                    'credit': vals.get('credit', 0.0),
                    'analytic_distribution': vals.get('analytic_distribution', False),
                })

        for pair in to_reconcile:
            pair.reconcile()
        return True

    # ------------------------------------------------------------------
    # Reparación de asientos descuadrados históricos (Debe != Haber)
    # ------------------------------------------------------------------
    @api.model
    def _bankinplay_find_descuadres(self, companies, date_from=False, date_to=False):
        """Líneas de extracto cuyo asiento contabilizado tiene Debe != Haber.

        El conector antiguo creaba estos asientos con ``check_move_validity=False``
        (rectificativa o compra compensada en el lado equivocado). Odoo no los
        avisa: sin línea en la transitoria marca la línea de extracto como
        conciliada y el widget OCA pinta una transitoria ficticia. Además, como
        todo ``write`` del asiento pasa por ``_check_balanced``, no se pueden
        pasar a borrador. Por eso se detectan por SQL.
        """
        if not companies:
            return self.browse()
        self.env['account.move.line'].flush_model(['move_id', 'balance'])
        self.env['account.move'].flush_model(
            ['state', 'statement_line_id', 'company_id', 'date'])
        query = """
            SELECT am.statement_line_id
              FROM account_move_line aml
              JOIN account_move am ON am.id = aml.move_id
              JOIN res_company comp ON comp.id = am.company_id
              JOIN res_currency cur ON cur.id = comp.currency_id
             WHERE am.state = 'posted'
               AND am.statement_line_id IS NOT NULL
               AND am.company_id IN %s
        """
        params = [tuple(companies.ids)]
        if date_from:
            query += " AND am.date >= %s"
            params.append(date_from)
        if date_to:
            query += " AND am.date <= %s"
            params.append(date_to)
        query += """
          GROUP BY am.statement_line_id, am.date, cur.decimal_places
            HAVING ROUND(SUM(aml.balance), cur.decimal_places) != 0
          ORDER BY am.date, am.statement_line_id
        """
        self.env.cr.execute(query, params)
        return self.browse([row[0] for row in self.env.cr.fetchall()])

    def _bankinplay_descuadre_target(self, line):
        """Factura pendiente que debía conciliar ``line``: misma empresa, cuenta
        e importe pendiente que el saldo de la línea (con el MISMO signo, porque
        la línea quedó en el lado equivocado). Si hay varias, desempata por el
        número de factura dentro del concepto bancario."""
        self.ensure_one()
        currency = line.company_currency_id
        items = self.env['account.move.line'].search([
            ('id', '!=', line.id),
            ('move_id', '!=', self.move_id.id),
            ('company_id', '=', line.company_id.id),
            ('account_id', '=', line.account_id.id),
            ('partner_id', '=', line.partner_id.id),
            ('parent_state', '=', 'posted'),
            ('reconciled', '=', False),
        ]).filtered(lambda aml: currency.compare_amounts(
            aml.amount_residual, line.balance) == 0)
        if len(items) > 1:
            ref = _bankinplay_norm(self.payment_ref)
            by_ref = items.filtered(lambda aml: any(
                len(code) >= 4 and code in ref
                for code in (_bankinplay_norm(aml.move_id.name),
                             _bankinplay_norm(aml.move_id.ref))))
            if len(by_ref) == 1:
                return by_ref
        return items

    def _bankinplay_descuadre_plan(self):
        """Propone cómo cuadrar el asiento de la línea dando la vuelta
        (Debe <-> Haber) a las contrapartidas que quedaron en el lado equivocado.

        Candidatas: líneas de cuenta conciliable SIN ninguna conciliación (la
        contrapartida mal puesta tiene el mismo signo que su factura y no se
        pudo conciliar). Se busca la combinación más pequeña cuyo giro deja el
        asiento a cero (suma de saldos = descuadre / 2) y, para cada línea, su
        factura con ``_bankinplay_descuadre_target``.

        :return: dict con ``status`` ('ok' | 'ok_unmatched' | 'review'),
            ``diff`` (Debe - Haber), ``flips`` [(línea, factura o vacío)] y
            ``detail`` (explicación legible).
        """
        self.ensure_one()
        move = self.move_id
        currency = move.company_id.currency_id
        diff = sum(move.line_ids.mapped('balance'))
        plan = {'status': 'review', 'diff': diff, 'flips': [], 'detail': ''}
        if currency.is_zero(diff):
            plan['detail'] = _("El asiento ya está cuadrado.")
            return plan
        if move.inalterable_hash:
            plan['detail'] = _("Asiento sellado (diario en modo estricto): "
                               "no se puede modificar.")
            return plan

        _liquidity, _suspense, other_lines = self._seek_for_lines()
        candidates = other_lines.filtered(
            lambda aml: aml.account_id.reconcile
            and not aml.matched_debit_ids and not aml.matched_credit_ids
            and not currency.is_zero(aml.balance))
        if not candidates:
            plan['detail'] = _("No hay contrapartidas sin conciliar que expliquen "
                               "el descuadre.")
            return plan
        if len(candidates) > DESCUADRE_MAX_CANDIDATES:
            plan['detail'] = _("Demasiadas contrapartidas sin conciliar (%s) para "
                               "proponer una corrección.") % len(candidates)
            return plan

        solutions = []
        for size in range(1, len(candidates) + 1):
            for combo in itertools.combinations(candidates, size):
                if currency.compare_amounts(
                        sum(aml.balance for aml in combo), diff / 2) == 0:
                    solutions.append(combo)
            if solutions:
                break
        if not solutions:
            plan['detail'] = _("Ninguna combinación de contrapartidas sin conciliar "
                               "cuadra el asiento.")
            return plan

        options = []
        for combo in solutions:
            flips = [(aml, self._bankinplay_descuadre_target(aml)) for aml in combo]
            targets = [target for _aml, target in flips if len(target) == 1]
            matched = (len(targets) == len(flips)
                       and len(set(targets)) == len(targets))
            options.append((matched, flips))
        matched_options = [flips for matched, flips in options if matched]
        if len(matched_options) == 1:
            plan.update(status='ok', flips=matched_options[0])
        elif len(options) == 1:
            plan.update(status='ok_unmatched', flips=options[0][1])
        else:
            plan['detail'] = _("Hay varias combinaciones posibles para cuadrar el "
                               "asiento; revisar a mano.")
            return plan

        parts = []
        for line, target in plan['flips']:
            amount = formatLang(self.env, abs(line.balance), currency_obj=currency)
            side = (_("Debe → Haber") if line.balance > 0
                    else _("Haber → Debe"))
            if len(target) == 1:
                with_what = _("concilia con %s") % (
                    target.move_id.name or target.name)
            elif target:
                with_what = _("varias facturas posibles; queda pendiente de "
                              "conciliar")
            else:
                with_what = _("sin factura pendiente que encaje; queda pendiente "
                              "de conciliar")
            parts.append("%s %s · %s · %s · %s" % (
                line.account_id.code, line.partner_id.name or '',
                amount, side, with_what))
        plan['detail'] = "\n".join(parts)
        return plan

    def _bankinplay_fix_descuadre(self, allow_unmatched=False):
        """Cuadra el asiento según ``_bankinplay_descuadre_plan``: da la vuelta a
        las contrapartidas mal puestas y las concilia con su factura. No toca el
        resto del asiento (las facturas bien conciliadas siguen igual).

        Envuelto en ``_check_balanced``: si al final no cuadra, lanza y no se
        escribe nada. Las fechas de bloqueo las protege el core.
        """
        self.ensure_one()
        plan = self._bankinplay_descuadre_plan()
        if plan['status'] == 'review' or (
                plan['status'] == 'ok_unmatched' and not allow_unmatched):
            raise UserError(plan['detail'] or _("Asiento no reparable."))
        move = self.move_id
        container = {'records': move, 'self': move}
        with move._check_balanced(container):
            for line, _target in plan['flips']:
                line.with_context(skip_account_move_synchronization=True).write({
                    'balance': -line.balance,
                    'amount_currency': -line.amount_currency,
                })
        for line, target in plan['flips']:
            if len(target) == 1:
                (line + target).reconcile()
        move.message_post(body=_(
            "Asiento cuadrado por la reparación de descuadres de BankInPlay "
            "(descuadre previo %s): %s") % (
                formatLang(self.env, plan['diff'],
                           currency_obj=move.company_id.currency_id),
                plan['detail'].replace("\n", "; ")))
        return plan
