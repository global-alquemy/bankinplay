# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
import logging

from dateutil.relativedelta import relativedelta

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    bankinplay_sent = fields.Boolean(
        string="BankInPlay Sent",
        help="BankInPlay Sent.",
        copy=False
    )


class AccountMove(models.Model):
    _inherit = "account.move"

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
            stmt_line.action_undo_reconciliation()  # odoo16 (antes button_undo_reconciliation)
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
