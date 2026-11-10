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
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = "res.company"

    bankinplay_apikey = fields.Char(
        string="Api Key",
        help="Key for BankInPlay API access.",
        groups="account.group_account_manager,base.group_system",
    )

    bankinplay_apisecret = fields.Char(
        string="Api Secret",
        help="Secret for BankInPlay API access.",
        groups="account.group_account_manager,base.group_system",
    )

    bankinplay_company_id = fields.Char(
        string="BankInPlay Company ID",
        help="Company ID for BankInPlay.",
    )

    def check_bankinplay_connection(self):
        self.ensure_one()
        # Conecta con las claves de la compañía y es un método público (RPC):
        # solo responsables de contabilidad, administradores o el sistema
        # (crons y jobs del sistema).
        user = self.env.user
        if not (self.env.su or user.has_group('account.group_account_manager')
                or user.has_group('base.group_system')):
            raise AccessError(_(
                "Solo los responsables de contabilidad pueden conectar con BankInPlay."))
        # BankInPlay identifica la sociedad por su NIF en todas las llamadas.
        if not self.vat:
            raise UserError(_(
                "BankInPlay - %(company)s: la compañía no tiene NIF.\n\n"
                "BankInPlay identifica la sociedad por su NIF. Añádelo en "
                "Ajustes > Usuarios y compañías > Compañías > %(company)s.",
                company=self.name))
        if not self.bankinplay_apikey or not self.bankinplay_apisecret:
            raise UserError(
                _("Please provide both the API Key and the API Secret."))
        interface_model = self.env["bankinplay.interface"]
        access_data = interface_model._login(
            self.bankinplay_apikey, self.bankinplay_apisecret)
        if not access_data:
            raise UserError(_("Connection Test Failed!"))

        access_data.update({
            'company_id': self,
        })
        return access_data

    def test_bankinplay_connection(self):

        access_data = self.check_bankinplay_connection()

        interface_model = self.env["bankinplay.interface"]
        check_company = False
        get_companies = interface_model._get_companies(access_data)
        for company in get_companies:
            if company['nif'] == self.vat.replace('ES', ''):
                check_company = True
                self.bankinplay_company_id = company['id']
                break

        if not check_company:
            raise UserError(
                _("The company NIF does not match any of the companies in BankInPlay."))

        title = _("Connection Test Succeeded!")
        message = _("Everything seems properly set up!")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'sticky': False,
            }
        }
