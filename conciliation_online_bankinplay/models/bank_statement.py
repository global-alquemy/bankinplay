# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
from odoo import models, fields, Command


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
