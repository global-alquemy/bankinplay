# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
import json
import logging
import re
from datetime import datetime
from odoo.tools.safe_eval import safe_eval

import pytz

from odoo import _, api, fields, models
from odoo.tools import ustr
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = "res.company"

    bankinplay_enabled = fields.Boolean(
        string="BankInPlay Enabled",
        help="Enable BankInPlay Integration.",
    )

    bankinplay_start_date = fields.Date(
        string="BankInPlay Start Date",
        help="Banking Play Init Date.",
    )    

    bankinplay_analytic_plan_id = fields.Char(
        string="Analytic Plan ID",
        help="Analytic Plan ID for BankInPlay.",
    )

    bankinplay_analytic_line_id = fields.Char(
        string="Analytic Line ID",
        help="Analytic Line ID for BankInPlay.",
    )

    bankinplay_manage_third_accounts = fields.Boolean(
        string="Manage Third Party Accounts",
        help="Use generic accounts for BankInPlay.",
    )

    bankinplay_journal_ids = fields.Many2many(
        'account.journal',
        'bankinplay_journal_rel',
        'company_id',
        'journal_id',
        string='Journals',
        help='Journals to export to BankInPlay',
    )

    bankinplay_last_syncdate = fields.Date(
        string="Last Sync Date",
        help="Last Sync Date.",
    )    

    bankinplay_partner_domain = fields.Char(
        string="Partner Domain",
        help="Partner Domain.",
        default='["&","&",["vat","!=",False],["parent_id","=",False],"|","|",["is_customer","=",True],["is_supplier","=",True],["employee","=",True]]'
    )

    def export_account_plan(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        
        data = interface_model._export_account_plan(access_data, self.bankinplay_start_date)
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
        data = interface_model._export_contacts(access_data, safe_eval(self.bankinplay_partner_domain) if self.bankinplay_partner_domain else[])
        return data
    
    def bankinplay_export_documents(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        interface_model._export_document_moves(access_data, self.bankinplay_start_date, self.bankinplay_journal_ids.ids)

    
    def bankinplay_import_documents(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        interface_model._import_conciliate_documents(access_data)
        

    def bankinplay_import_account_moves(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        interface_model._import_account_moves(access_data)

        

    def export_analytic_plan(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        if not self.bankinplay_analytic_plan_id:
            analytic_plan_id = interface_model._create_analytic_plan(access_data)
            self.bankinplay_analytic_plan_id = analytic_plan_id
        
        if not self.bankinplay_analytic_line_id:
            analytic_line_id = interface_model._create_analytic_line(access_data, self.bankinplay_analytic_plan_id)
            self.bankinplay_analytic_line_id = analytic_line_id

        interface_model._export_analytic_plan(access_data, self.bankinplay_analytic_line_id)
        
    
    def bankinplay_export_account_move_line(self):  
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        interface_model._export_account_move_lines(access_data)
        


    #CRON################################
    def bankinplay_export_account_plan_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.with_delay().export_account_plan()

    def bankinplay_export_analytic_plan_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.with_delay().export_analytic_plan()

    def bankinplay_export_documents_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.with_delay().bankinplay_export_documents()
    
    def bankinplay_import_documents_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.with_delay().bankinplay_import_documents()

    def bankinplay_import_account_moves_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.with_delay().bankinplay_import_account_moves()

    def bankinplay_export_account_move_line_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.with_delay().bankinplay_export_account_move_line()
        
    