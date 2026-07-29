# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
import json
import logging
import re
from datetime import datetime

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.tools import ustr
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Campos que si cambian requieren reenvío a BankinPlay
BANKINPLAY_TRACKED_FIELDS = [
    'date_maturity',
    'reconciled',  # Cuando se concilia/desconcilia
]


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    bankinplay_sent = fields.Boolean(
        string="BankInPlay Sent",
        help="BankInPlay Sent.",
        copy=False
    )
    bankinplay_needs_update = fields.Boolean(
        string="Requiere actualización en BankInPlay",
        help="Indica si el apunte ha sido modificado después de enviarse y requiere reenvío.",
        copy=False
    )

    def write(self, vals):
        """Detectar cambios en campos críticos para marcar actualización pendiente."""
        # Identificamos los registros ya enviados que se modifican en campos relevantes
        if any(field in vals for field in BANKINPLAY_TRACKED_FIELDS):
            records_to_mark = self.filtered(
                lambda r: r.bankinplay_sent and not r.bankinplay_needs_update
            )
            if records_to_mark:
                # Marcamos antes del write para evitar recursión
                super(AccountMoveLine, records_to_mark).write({
                    'bankinplay_needs_update': True
                })

        return super(AccountMoveLine, self).write(vals)

    def reconcile(self):
        """Marcar como pendiente de actualización cuando se concilia (pago)."""
        to_mark = self.filtered(lambda r: r.bankinplay_sent and not r.bankinplay_needs_update)
        if to_mark:
            to_mark.with_context(skip_bankinplay_tracking=True).write({
                'bankinplay_needs_update': True
            })
        return super(AccountMoveLine, self).reconcile()

    def remove_move_reconcile(self):
        """Marcar como pendiente de actualización cuando se desconcilia."""
        to_mark = self.filtered(lambda r: r.bankinplay_sent and not r.bankinplay_needs_update)
        if to_mark:
            to_mark.with_context(skip_bankinplay_tracking=True).write({
                'bankinplay_needs_update': True
            })
        return super(AccountMoveLine, self).remove_move_reconcile()


class AccountMove(models.Model):
    _inherit = "account.move"

    def write(self, vals):
        """Detectar cambios en estado de factura o pago para marcar apuntes como pendientes de actualización."""
        res = super(AccountMove, self).write(vals)

        # Si cambia el estado de pago o el estado de la factura, marcar apuntes para reenvío
        if 'payment_state' in vals or 'state' in vals:
            for move in self:
                move_lines_to_mark = move.line_ids.filtered(
                    lambda l: l.bankinplay_sent
                    and not l.bankinplay_needs_update
                    and l.account_id.user_type_id.type in ['payable', 'receivable']
                )
                if move_lines_to_mark:
                    move_lines_to_mark.with_context(skip_bankinplay_tracking=True).write({
                        'bankinplay_needs_update': True
                    })

        return res

    def _reverse_moves(self, default_values_list=None, cancel=False):
        """Marcar apuntes de factura original como pendientes de actualización cuando se crea rectificativa."""
        for move in self:
            if move.state == 'posted':
                # Buscar apuntes de cuentas a cobrar/pagar que ya fueron enviados
                move_lines_to_mark = move.line_ids.filtered(
                    lambda l: l.bankinplay_sent
                    and not l.bankinplay_needs_update
                    and l.account_id.user_type_id.type in ['payable', 'receivable']
                )
                if move_lines_to_mark:
                    move_lines_to_mark.with_context(skip_bankinplay_tracking=True).write({
                        'bankinplay_needs_update': True
                    })

        return super(AccountMove, self)._reverse_moves(default_values_list, cancel)

    def action_bankinplay_revert_and_reprocess(self):
        """Revolcado del histórico: revierte la conciliación de los extractos
        afectados (vuelven a is_reconciled=False) y relanza la importación con
        exportados=True para repoblar el inbox, que los rehará bien.

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
            stmt_line.button_undo_reconciliation()
            reverted += 1
            comp_id = move.company_id.id
            lo, hi = ranges.get(comp_id, (move.date, move.date))
            ranges[comp_id] = (min(lo, move.date), max(hi, move.date))

        Company = self.env['res.company']
        for company_id, (min_date, max_date) in ranges.items():
            company = Company.browse(company_id)
            # Se pide el backfill acotado por rango de fechas (desde/hasta) con un
            # pequeño margen, sin tocar el last_syncdate del sync normal.
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
            "El inbox los reprocesará.") % (
            reverted, skipped, len(ranges))
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
