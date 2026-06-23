# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
import json
import logging
import re
from datetime import datetime

import pytz

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
