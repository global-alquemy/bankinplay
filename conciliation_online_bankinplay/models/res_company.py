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

with_delay_interval = 60
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

    bankinplay_bank_statement_start_date = fields.Date(
        string="Fecha inicio extractos",
        help="Fecha de inicio para la extracción de extractos bancarios desde BankInPlay.",
    )

    bankinplay_bank_statements_synced = fields.Boolean(
        string="Extractos sincronizados",
        default=False,
        help="Indica si los extractos bancarios han sido sincronizados desde BankInPlay.",
    )    

    bankinplay_partner_domain = fields.Char(
        string="Partner Domain",
        help="Partner Domain.",
        default='["&","&",["vat","!=",False],["parent_id","=",False],"|","|",["is_customer","=",True],["is_supplier","=",True],["employee","=",True]]'
    )


    #FUNCIONES PARA EJECUTAR LOS PROCESOS DE BANKINPLAY
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

    def bankinplay_import_documents(self, fecha_desde=None, fecha_hasta=None):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        # Rango de fechas por contexto (revolcado/backfill acotado, §13).
        fecha_desde = fecha_desde or self.env.context.get('bankinplay_fecha_desde')
        fecha_hasta = fecha_hasta or self.env.context.get('bankinplay_fecha_hasta')
        interface_model._import_conciliate_documents(access_data, fecha_desde, fecha_hasta)

    def bankinplay_import_account_moves(self, fecha_desde=None, fecha_hasta=None):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        fecha_desde = fecha_desde or self.env.context.get('bankinplay_fecha_desde')
        fecha_hasta = fecha_hasta or self.env.context.get('bankinplay_fecha_hasta')
        interface_model._import_account_moves(access_data, fecha_desde, fecha_hasta)

    def export_analytic_plan(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        try:
            if not self.bankinplay_analytic_plan_id:
                analytic_plan_id = interface_model._create_analytic_plan(access_data)
                self.bankinplay_analytic_plan_id = analytic_plan_id

            if not self.bankinplay_analytic_line_id:
                analytic_line_id = interface_model._create_analytic_line(access_data, self.bankinplay_analytic_plan_id)
                self.bankinplay_analytic_line_id = analytic_line_id

            interface_model._export_analytic_plan(access_data, self.bankinplay_analytic_line_id)
        except UserError:
            raise
        except Exception as e:
            _logger.exception("Error inesperado al exportar plan analítico para %s", self.name)
            raise UserError(_("Error al exportar plan analítico: %s") % str(e))
        
    def bankinplay_export_account_move_line(self):
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        interface_model._export_account_move_lines(access_data)

    def bankinplay_register_callbacks(self):
        """Registra los callbacks de conciliación y asientos en BankInPlay."""
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

        callbacks = [
            ("exportacion_conciliacion_terceros", base_url + "/webhook/conciliacionTerceros"),
            ("asiento_contable", base_url + "/webhook/asientoContable"),
            ("lectura_cierre", base_url + "/webhook/lectura_cierre"),
            ("lectura_intradia", base_url + "/webhook/lectura_intradia"),
            ("lectura_tarjeta", base_url + "/webhook/lectura_tarjeta"),
        ]
        for event, target in callbacks:
            interface_model._register_callback(access_data, event, target)
            if interface_model._bankinplay_logging_enabled():
                _logger.info("Callback registrado: %s -> %s", event, target)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Callbacks registrados"),
                'message': _("Se han registrado los callbacks de conciliación terceros y asientos contables en BankInPlay."),
                'sticky': False,
            }
        }

    def bankinplay_check_callbacks(self):
        """Consulta los callbacks registrados en BankInPlay y los muestra."""
        access_data = self.check_bankinplay_connection()
        interface_model = self.env["bankinplay.interface"]
        callbacks = interface_model._get_callbacks(access_data)

        if not callbacks:
            message = _("No hay callbacks registrados en BankInPlay.")
        else:
            lines = []
            for cb in callbacks:
                lines.append("• %s → %s" % (cb.get('tipo', ''), cb.get('target', '')))
            message = "\n".join(lines)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Callbacks registrados (%d)") % len(callbacks),
                'message': message,
                'sticky': True,
            }
        }

    #BOTONES DE LA VISTA PARA LLAMAR A LAS FUNCIONES DE BANKINPLAY
    def bankinplay_export_account_plan_button(self):
        self.with_context(company_id=self.id).with_delay(max_retries=0).export_account_plan()

    def bankinplay_export_analytic_plan_button(self):
        self.with_context(company_id=self.id).with_delay(max_retries=0).export_analytic_plan()

    def bankinplay_export_documents_button(self):
        self.with_context(company_id=self.id).with_delay(max_retries=0).bankinplay_export_documents()

    def bankinplay_import_documents_button(self):
        self.with_context(company_id=self.id).with_delay(max_retries=0).bankinplay_import_documents()

    def bankinplay_import_account_moves_button(self):
        self.with_context(company_id=self.id).with_delay(max_retries=0).bankinplay_import_account_moves()

    def bankinplay_export_account_move_line_button(self):
        self.with_context(company_id=self.id).with_delay(max_retries=0).bankinplay_export_account_move_line()

    #CRON################################
    def bankinplay_export_account_plan_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        interval = with_delay_interval
        eta = 0
        for company in company_ids:
            company.with_context(company_id=company.id).with_delay(eta=eta, max_retries=0).export_account_plan()
            eta += interval

    def bankinplay_export_analytic_plan_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        interval = with_delay_interval
        eta = 0
        for company in company_ids:
            company.with_context(company_id=company.id).with_delay(eta=eta, max_retries=0).export_analytic_plan()
            eta += interval

    def bankinplay_export_documents_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        interval = with_delay_interval
        eta = 0
        for company in company_ids:
            company.with_context(company_id=company.id).with_delay(eta=eta, max_retries=0).bankinplay_export_documents()
            eta += interval

    def bankinplay_import_documents_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        interval = with_delay_interval
        eta = 0
        for company in company_ids:
            company.with_context(company_id=company.id).with_delay(eta=eta, max_retries=0).bankinplay_import_documents()
            eta += interval

    def bankinplay_import_account_moves_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        interval = with_delay_interval
        eta = 0
        for company in company_ids:
            company.with_context(company_id=company.id).with_delay(eta=eta, max_retries=0).bankinplay_import_account_moves()
            eta += interval

    def bankinplay_export_account_move_line_cron(self):
        company_ids = self.env['res.company'].search([('bankinplay_enabled', '=', True)])
        interval = with_delay_interval
        eta = 0
        for company in company_ids:
            company.with_context(company_id=company.id).with_delay(eta=eta, max_retries=0).bankinplay_export_account_move_line()
            eta += interval
        
    