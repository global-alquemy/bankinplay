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
        data = interface_model._export_document_moves(access_data, self.bankinplay_start_date, self.bankinplay_journal_ids.ids)
        return data
    
    def bankinplay_import_documents(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        data = interface_model._import_conciliate_documents(access_data)
        if data.get('sociedades'):
            for conciliation in data.get('sociedades')[0].get('documentos'):
                statement_line = self.env['account.bank.statement.line'].search([('is_reconciled', '=', False)]).filtered(lambda x: str(conciliation.get('id_movimiento')) in x.unique_import_id)
                if statement_line:
                    
                    journal_id = statement_line.journal_id
                    cuenta_bancaria = conciliation.get('cuenta_bancaria')
                    number = (cuenta_bancaria + '-'
                        + str(journal_id.id)
                        + "-"
                        + str(conciliation.get('id_movimiento'))
                    )
                    if statement_line.unique_import_id == number:
                        if statement_line.is_reconciled:
                            statement_line.button_undo_reconciliation()           

                        payable_account_type = self.env.ref("account.data_account_type_payable")
                        receivable_account_type = self.env.ref("account.data_account_type_receivable")

                        counterparts = []
                        move_id = int(conciliation.get('id_documento_erp'))
                        if move_id:
                            move = self.env['account.move'].search([('id', '=', move_id), ('state', '=', 'posted')], limit=1)
                            if move:
                                for line in move.line_ids:
                                    if line.account_id.user_type_id in [payable_account_type, receivable_account_type]:
                                        counterparts.append({
                                            'name': line.name,
                                            'credit': line.debit,
                                            'debit': line.credit,
                                            'move_line': line,
                                        })
                       
                                moves = statement_line.process_reconciliation_oca(
                                    counterparts,
                                    [],
                                    []
                                )

                                #move.bankinplay_send_move()

                        
        return data

    def bankinplay_import_account_moves(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        data = interface_model._import_account_moves(access_data)

        _logger.info("DATA: %s", data)

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

                    new_line_vals = []

                    for apunte in asiento.get('apuntes'):
                        if apunte.get('cuenta_contable') != journal_id.default_account_id.code:
                            account_account = self.env['account.account'].search([('code', '=', apunte.get('cuenta_contable'))], limit=1)
                            if not account_account:
                               raise UserError(_("Account %s not found in the system." % apunte.get('cuenta_contable')))
                            
                            credit = 0
                            debit = 0
                            if apunte.get('debe_haber') == 'D':
                                credit = apunte.get('importe')
                            else:
                                debit = apunte.get('importe')

                            analytic_account_id = False
                            if apunte.get('analitica'):
                                for analitica in apunte.get('analitica'):
                                    for desglose in analitica.get('desglose'):
                                        account_analytic = self.env['account.analytic.account'].search([('name', '=', desglose.get('codigo_analitico'))], limit=1)
                                        if not account_analytic:
                                            raise UserError(_("Analytic Account %s not found in the system." % desglose.get('codigo_analitico'))
                                        )
                                        analytic_account_id = account_analytic.id
                                        
                            
                            new_line_vals.append({
                                'name': asiento.get('descripcion'),
                                'credit': debit,
                                'debit': credit,
                                'account_id': account_account.id,
                                'analytic_account_id': analytic_account_id
                                
                            })

                    moves = statement_line.process_reconciliation_oca(
                            [],
                            [],
                            new_line_vals
                    )

                    statement_line.write({'bankinplay_conciliation': True})

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


    #CRON################################
    def bankinplay_export_account_plan_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.export_account_plan()

    def bankinplay_export_analytic_plan_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.export_analytic_plan()

    def bankinplay_export_documents_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.bankinplay_export_documents()
    
    def bankinplay_import_documents_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.bankinplay_import_documents()

    def bankinplay_import_account_moves_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.bankinplay_import_account_moves()

    def bankinplay_export_account_move_line_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        for company in company_ids:
            company.bankinplay_export_account_move_line()
        
    