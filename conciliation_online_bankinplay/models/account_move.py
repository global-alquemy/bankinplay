# Copyright 2024 Global Alquemy SL
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

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
    _inherit = "account.move"

    bankinplay_sent = fields.Char(
        string="BankInPlay Sent",
        help="BankInPlay Sent.",
    )
    
    def action_send_account_moves_bankinplay(self):
        for move in self:
            name_job = "[BANKINPLAY] - FACTURA " + move.name
            name_job_partner = "[BANKINPLAY] - FACTURA " + move.name + ' - ' + move.partner_id.name

            #move.partner_id.with_delay(priority=30, max_retries=5, description=name_job_partner).bankinplay_send_partner()
            move.with_delay(priority=20, max_retries=5, description=name_job).bankinplay_send_move()


    def bankinplay_send_move(self):
        company_id = self.env.company
        access_data = company_id.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        data = interface_model._export_document(access_data, self.id)
        if data:
            self.bankinplay_sent = True
        return data