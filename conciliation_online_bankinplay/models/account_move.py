# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
import logging

from dateutil.relativedelta import relativedelta

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

# Campos del apunte que, si cambian tras el envío, requieren reenviarlo a
# BankInPlay. Los cobros/pagos (incluidas las remesas, que en 16.0 concilian al
# subirse) se detectan en reconcile()/remove_move_reconcile().
BANKINPLAY_TRACKED_FIELDS = ['date_maturity']
RECEIVABLE_PAYABLE = ('asset_receivable', 'liability_payable')


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    bankinplay_sent = fields.Boolean(
        string="BankInPlay Sent",
        help="BankInPlay Sent.",
        copy=False
    )
    bankinplay_needs_update = fields.Boolean(
        string="Requiere actualización en BankInPlay",
        help="El apunte ha cambiado después de enviarse (cobro, vencimiento, "
             "rectificativa...) y debe reenviarse a BankInPlay.",
        copy=False,
    )

    def _bankinplay_mark_needs_update(self):
        """Marca para reenvío los apuntes ya enviados a BankInPlay."""
        to_mark = self.filtered(
            lambda aml: aml.bankinplay_sent and not aml.bankinplay_needs_update)
        if to_mark:
            to_mark.write({'bankinplay_needs_update': True})

    def write(self, vals):
        if any(field in vals for field in BANKINPLAY_TRACKED_FIELDS):
            self._bankinplay_mark_needs_update()
        return super().write(vals)

    def reconcile(self):
        self._bankinplay_mark_needs_update()
        return super().reconcile()

    def remove_move_reconcile(self):
        self._bankinplay_mark_needs_update()
        return super().remove_move_reconcile()


class AccountMove(models.Model):
    _inherit = "account.move"

    def _bankinplay_term_lines(self):
        return self.line_ids.filtered(
            lambda aml: aml.account_id.account_type in RECEIVABLE_PAYABLE)

    def write(self, vals):
        res = super().write(vals)
        # Pasar a borrador / volver a contabilizar / cancelar: reenviar
        if 'state' in vals:
            self._bankinplay_term_lines()._bankinplay_mark_needs_update()
        return res

    def _reverse_moves(self, default_values_list=None, cancel=False):
        # Rectificativa de una factura ya enviada: reenviar la original
        self.filtered(lambda m: m.state == 'posted')._bankinplay_term_lines(
        )._bankinplay_mark_needs_update()
        return super()._reverse_moves(default_values_list, cancel)

    def action_bankinplay_revert_and_reprocess(self):
        """Revolcado del histórico: revierte la conciliación de los extractos
        afectados (vuelven a is_reconciled=False) y relanza la importación
        acotada por rango de fechas para repoblar el inbox, que los rehará bien.

        Pensado para lanzarse desde la vista 'BankInPlay: Asientos sin conciliar'.
        """
        StmtLine = self.env['account.bank.statement.line']
        ranges = {}
        reverted = 0
        skipped = 0
        for move in self:
            stmt_line = StmtLine.search([('move_id', '=', move.id)], limit=1)
            if not stmt_line:
                skipped += 1
                continue
            # Reset robusto: tolera asientos ya descuadrados (los históricos del
            # conector antiguo), donde action_undo_reconciliation del core falla.
            stmt_line._bankinplay_reset_move()
            reverted += 1
            comp_id = move.company_id.id
            lo, hi = ranges.get(comp_id, (move.date, move.date))
            ranges[comp_id] = (min(lo, move.date), max(hi, move.date))

        Company = self.env['res.company']
        for company_id, (min_date, max_date) in ranges.items():
            company = Company.browse(company_id)
            # Backfill acotado por rango de fechas (desde/hasta) con margen, sin
            # tocar el last_syncdate del sync normal. Fuerza exportados=True.
            ctx = dict(
                bankinplay_force_exported=True,
                bankinplay_fecha_desde=min_date - relativedelta(days=2),
                bankinplay_fecha_hasta=max_date + relativedelta(days=1),
            )
            try:
                company.with_context(**ctx).bankinplay_import_documents()
                company.with_context(**ctx).bankinplay_import_account_moves()
            except Exception:
                _logger.exception(
                    "BankInPlay: fallo al relanzar importación para %s", company.name)

        message = _(
            "Revertidos: %s · Omitidos (sin extracto): %s · "
            "Backfill (por rango de fechas) lanzado en %s compañía(s). "
            "El inbox los reprocesará.") % (reverted, skipped, len(ranges))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Revertir y reprocesar"),
                'message': message,
                'type': 'success',
                'sticky': True,
            },
        }
