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


class ResPartner(models.Model):
    _inherit = "res.partner"

    bankinplay_sent = fields.Boolean(string="Bankinplay sent", default=False)
    bankinplay_update = fields.Boolean(string="Bankinplay update", default=False)

    def bankinplay_send_partner(self):
        for record in self:
            if not record.bankinplay_sent or record.bankinplay_update:
                company_id = record.env.company
                access_data = company_id.check_bankinplay_connection()
                interface_model = record.env["bankinplay.interface"]
                interface_model._export_contacts(access_data, [('id', '=', record.id)])
               
            