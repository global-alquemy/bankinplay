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
        string="Enviado a BankInPlay",
        help="Indica si el apunte ha sido enviado a BankInPlay.",
        copy=False
    )
    bankinplay_needs_update = fields.Boolean(
        string="Requiere actualización en BankInPlay",
        help="Indica si el apunte ha sido modificado después de enviarse y requiere reenvío.",
        copy=False
    )

    def write(self, vals):
        """Detectar cambios en campos críticos para marcar actualización pendiente."""
        # Primero identificamos los registros que ya fueron enviados
        # y que están siendo modificados en campos relevantes
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
        # Marcar los apuntes que ya fueron enviados
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


class AccountPaymentOrder(models.Model):
    _inherit = "account.payment.order"

    def generated2uploaded(self):
        """Marcar apuntes de la remesa como pendientes de actualización en BankinPlay."""
        res = super(AccountPaymentOrder, self).generated2uploaded()
        # Obtener todos los apuntes relacionados con esta remesa
        for order in self:
            move_lines = order.payment_line_ids.mapped('move_line_id')
            to_mark = move_lines.filtered(
                lambda r: r.bankinplay_sent and not r.bankinplay_needs_update
            )
            if to_mark:
                to_mark.with_context(skip_bankinplay_tracking=True).write({
                    'bankinplay_needs_update': True
                })
        return res


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
        # Marcar los apuntes de las facturas originales antes de crear la rectificativa
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

    def unlink(self):
        """Encolar documentos para anular en BankinPlay antes de eliminar la factura."""
        cancel_queue = self.env['bankinplay.cancel.queue']

        for move in self:
            # Solo procesar si la empresa tiene BankinPlay habilitado
            if move.company_id.bankinplay_enabled:
                # Buscar apuntes enviados a BankinPlay
                sent_lines = move.line_ids.filtered(
                    lambda l: l.bankinplay_sent
                    and l.account_id.user_type_id.type in ['payable', 'receivable']
                )
                # Encolar cada apunte para anulación asíncrona
                for line in sent_lines:
                    cancel_queue.create({
                        'company_id': move.company_id.id,
                        'move_line_id': line.id,
                        'move_name': move.name,
                        'partner_name': move.partner_id.name if move.partner_id else '',
                        'state': 'pending',
                    })
                    _logger.info(
                        "Documento %s encolado para anular en BankinPlay",
                        line.id
                    )

        return super(AccountMove, self).unlink()