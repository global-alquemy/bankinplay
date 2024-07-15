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


class ResPartner(models.Model):
    _inherit = "res.partner"

    # bankinplay_sent = fields.Char(
    #     string="BankInPlay Sent",
    #     help="BankInPlay Sent.",
    # )
    

    def bankinplay_send_partner(self):
        company_id = self.env.company
        access_data = company_id.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        data = interface_model._export_contact(access_data, self.id)
        # if data:
        #     self.bankinplay_sent = True
        # return data