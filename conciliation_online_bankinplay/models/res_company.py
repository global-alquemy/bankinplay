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


class ResCompany(models.Model):
    _inherit = "res.company"

    bankinplay_apikey = fields.Char(
        string="Api Key",
        help="Key for BankInPlay API access.",
    )

    bankinplay_apisecret = fields.Char(
        string="Api Secret",
        help="Secret for BankInPlay API access.",
    )

    bankinplay_manage_third_accounts = fields.Boolean(
        string="Manage Third Party Accounts",
        help="Use generic accounts for BankInPlay.",
    )

    bankinplay_company_id = fields.Char(
        string="Company ID",
        help="Company ID for BankInPlay.",
    )

    bankinplay_analytic_plan_id = fields.Char(
        string="Analytic Plan ID",
        help="Analytic Plan ID for BankInPlay.",
    )

    bankinplay_analytic_line_id = fields.Char(
        string="Analytic Line ID",
        help="Analytic Line ID for BankInPlay.",
    )

    bankinplay_contacts_cron = fields.Boolean( 
        string="Export Contacts Cron",
        help="Export Contacts to BankInPlay",
    )

    bankinplay_account_plan_cron = fields.Boolean( 
        string="Export Account Plan Cron",
        help="Export Account Plan to BankInPlay",
    )

    bankinplay_account_move_cron = fields.Boolean(  
        string="Export Account Moves Cron",
        help="Export Account Moves to BankInPlay",
    )

    bankinplay_analytic_plan_cron = fields.Boolean( 
        string="Export Analytic Plan Cron",
        help="Export Analytic Plan to BankInPlay",
    )

    def check_bankinplay_connection(self):
        if not self.bankinplay_apikey or not self.bankinplay_apisecret:
            raise UserError(_("Please provide both the API Key and the API Secret."))
        interface_model = self.env["bankinplay.interface"]
        access_data = interface_model._login(self.bankinplay_apikey, self.bankinplay_apisecret)
        if not access_data:
            raise UserError(_("Connection Test Failed!"))
        
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
            raise UserError(_("The company NIF does not match any of the companies in BankInPlay."))
        
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
    
    def test_bankinplay_connection_bank_accounts(self):
        
        access_data = self.check_bankinplay_connection()
        
        interface_model = self.env["bankinplay.interface"]
        
        check_company = False
        get_companies = interface_model._get_sociedades(access_data)
        for company in get_companies:
            if company['nif'] == self.vat.replace('ES', ''): 
                check_company = True
                break
                
        if not check_company:
            raise UserError(_("The company NIF does not match any of the companies in BankInPlay."))
        
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

    def export_account_plan(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        data = interface_model._export_account_plan(access_data)
        title = _("Export Succeded!")
        message = _("Account plan has been exported successfully.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'sticky': False,
            }
        }

    def bankinplay_export_contacts(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        data = interface_model._export_contacts(access_data)
        return data
    
    def bankinplay_export_documents(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        data = interface_model._export_documents(access_data)
        return data



    def bankinplay_import_account_moves(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        data = interface_model._import_account_moves(access_data)
        #data = '{"results": {"sociedades": [{"cif": "F72880792", "cod_contable": "BROTICS", "nombre": "BROTICS S.MICROCOOP. DE C-LM", "pais": "ES"}], "asientos": [{"descripcion": "#FR46ZZZ005002 NN982442535DGFIP2024050129LSK6HUI8 #229030020700127249256 #B2B DGFIP # TAXSTVA-062024-3310CA3  REFERENCIA ADEUDO A SUCARG NUMDOCUMENTO 0000000000", "divisa": "EUR", "apuntes": [{"no_apunte": 1, "cuenta_contable": "626000", "debe_haber": "D", "importe": 278}, {"no_apunte": 2, "cuenta_contable": "572002", "debe_haber": "H", "importe": 278}], "no_asiento": 1, "cuenta_bancaria": "ES8001822857190201704590", "banco": "BBVAESMM", "sociedad_cod_erp": "BROTICS", "fecha_asiento": "2024-06-23T22:00:00Z", "fecha_operacion": "2024-06-23T22:00:00Z", "movimiento_id": 177019659, "pdfBancario": "https://app.bankinplay.com/intradia/consultaMovimientos/detalle?origen=cierre&_id=177019659&token=77c73e0c-93fc-4eae-bd26-bf2ec568f91e"}], "request_code": "b46305c5-c510-4b3a-a2ae-deef877f190f"}, "timestamp": "2024-06-25T22:43:41.483Z"}'
        data = json.loads(data)
        for asiento in data.get('results').get('asientos'):
            statement_line = self.env['account.bank.statement.line'].search([('is_reconciled', '=', False)]).filtered(lambda x: str(asiento.get('movimiento_id')) in x.unique_import_id)
            if statement_line and not statement_line.is_reconciled:
                journal_id = statement_line.journal_id
                cuenta_bancaria = asiento.get('cuenta_bancaria')
                number = (cuenta_bancaria + '-'
                    + str(journal_id.id)
                    + "-"
                    + str(asiento.get('movimiento_id'))
                )
                if statement_line.unique_import_id == number:
                    
                    statement_line.line_ids.remove_move_reconcile()
                    statement_line.payment_ids.unlink() 

                    line_vals = []

                    account_account = False

                    for apunte in asiento.get('apuntes'):
                        if apunte.get('cuenta_contable') != journal_id.default_account_id.code:
                            account_account = apunte.get('cuenta_contable')

                    if account_account:
                        account_account_id = self.env['account.account'].search([('code', '=', account_account)], limit=1)
                        if account_account_id:
                            statement_line.with_context(force_delete=True).write({
                                'to_check': False,
                                'line_ids': [(5, 0)] + [(0, 0, line_vals) for line_vals in statement_line._prepare_move_line_default_vals(account_account_id.id)],
                            })

    def export_analytic_plan(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        if not self.bankinplay_analytic_plan_id:
            analytic_plan_id = interface_model._create_analytic_plan(access_data)
            self.bankinplay_analytic_plan_id = analytic_plan_id
        
        if not self.bankinplay_analytic_line_id:
            analytic_line_id = interface_model._create_analytic_line(access_data, self.bankinplay_analytic_plan_id)
            self.bankinplay_analytic_line_id = analytic_line_id

        data = interface_model._export_analytic_plan(access_data, self.bankinplay_analytic_line_id)
        title = _("Export Succeded!")
        message = _("Analytic plan has been exported successfully.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'sticky': False,
            }
        }
    

    def bankinplay_export_account_move_line(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        data = interface_model._export_account_move_lines(access_data)
        return data