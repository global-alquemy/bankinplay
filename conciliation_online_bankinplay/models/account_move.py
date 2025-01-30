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

class AccountMove(models.Model):
    _inherit = "account.move.line"

    bankinplay_sent = fields.Boolean(
        string="BankInPlay Sent",
        help="BankInPlay Sent.",
    )

# class AccountMove(models.Model):
#     _inherit = "account.move"

#     bankinplay_sent = fields.Boolean(
#         string="BankInPlay Sent",
#         help="BankInPlay Sent.",
#     )

#     def check_bankinplay_move(self):
#         for move in self:
#             if move.company_id.bankinplay_enabled:
#                 if move.journal_id in move.company_id.bankinplay_journal_ids:
#                     return True
#         return False

#     def action_post(self):
#         res = super(AccountMove, self).action_post()
#         if self.check_bankinplay_move():
#             for move in self:
#                 if move.state == 'posted':
#                     name_job = "[BANKINPLAY] - FACTURA " + move.name
#                     move.with_delay(priority=20, max_retries=5, description=name_job).bankinplay_send_move()
#         return res

#     def button_draft(self):
#         res = super(AccountMove, self).button_draft()

#         if self.check_bankinplay_move():
#             for move in self:
#                 if move.bankinplay_sent:
#                     name_job = "[BANKINPLAY] - CANCELAR FACTURA " + move.name
#                     move.with_delay(priority=20, max_retries=5, description=name_job).bankinplay_cancel_move()

#         return res
    
   
    
#     def (self):
#         company_id = self.env.company
#         access_data = company_id.check_bankinplay_connection()
#         interface_model = self.env["bankinplay.interface"]
#         data = interface_model._cancel_document(access_data, self.id)
#         if data:
#             if data.get('documentos', False):
#                 estado = data['documentos'][0]['estado']
#                 if estado == 'correcto':
#                     self.bankinplay_sent = True
#                 else:
#                     raise UserError(_('Error: %s' % data['documentos'][0]['description']))
#         return data
    
    
#     def bankinplay_send_move(self):
#         company_id = self.env.company
#         access_data = company_id.check_bankinplay_connection()
#         interface_model = self.env["bankinplay.interface"]
#         data = interface_model._export_document(access_data, self.id)
#         if data:
#             if data.get('documentos', False):
#                 estado = data['documentos'][0]['estado']
#                 if estado == 'correcto':
#                     self.bankinplay_sent = True
#                 else:
#                     raise UserError(_('Error: %s' % data['documentos'][0]['description']))
#         return data