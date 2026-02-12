# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import datetime
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BankinplayMassCompanyWizardLine(models.TransientModel):
    _name = "bankinplay.mass.company.wizard.line"
    _description = "Bankinplay Mass Company Wizard Line"

    wizard_id = fields.Many2one(
        comodel_name="bankinplay.mass.company.wizard",
        string="Wizard",
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Compañía",
        readonly=True,
    )
    bankinplay_enabled = fields.Boolean(
        string="Habilitado",
        compute="_compute_bankinplay_status",
    )
    bankinplay_company_id = fields.Char(
        string="BIP Company ID",
        compute="_compute_bankinplay_status",
    )
    bankinplay_start_date = fields.Date(
        string="Fecha inicio",
        compute="_compute_bankinplay_status",
    )
    bankinplay_bank_statement_start_date = fields.Date(
        string="Fecha extractos",
        compute="_compute_bankinplay_status",
    )
    bankinplay_bank_statements_synced = fields.Boolean(
        string="Extractos sync",
        compute="_compute_bankinplay_status",
    )
    provider_ratio = fields.Char(
        string="Proveedores",
        compute="_compute_bankinplay_status",
    )
    journal_names = fields.Char(
        string="Diarios habilitados",
        compute="_compute_bankinplay_status",
    )
    status = fields.Selection(
        [
            ("not_configured", "Sin configurar"),
            ("partial", "Parcial"),
            ("configured", "Configurado"),
        ],
        string="Estado",
        compute="_compute_bankinplay_status",
    )
    result_message = fields.Char(
        string="Resultado",
        readonly=True,
    )

    @api.depends("company_id")
    def _compute_bankinplay_status(self):
        Provider = self.env["online.bank.statement.provider"].sudo()
        Journal = self.env["account.journal"].sudo()
        for line in self:
            company = line.company_id.sudo()
            line.bankinplay_enabled = company.bankinplay_enabled
            line.bankinplay_company_id = company.bankinplay_company_id or ""
            line.bankinplay_start_date = company.bankinplay_start_date
            line.bankinplay_bank_statement_start_date = company.bankinplay_bank_statement_start_date
            line.bankinplay_bank_statements_synced = company.bankinplay_bank_statements_synced
            journals = Journal.search([
                ("type", "=", "bank"),
                ("company_id", "=", company.id),
            ])
            providers = Provider.search([
                ("journal_id", "in", journals.ids),
                ("service", "=", "bankinplay"),
                ("active", "=", True),
            ])
            prov_count = len(providers)
            journal_count = len(journals)
            line.provider_ratio = "%d/%d" % (prov_count, journal_count)
            line.journal_names = ", ".join(
                company.bankinplay_journal_ids.mapped("name")
            ) if company.bankinplay_journal_ids else ""
            has_apikey = bool(company.bankinplay_apikey and company.bankinplay_apisecret)
            has_company_id = bool(company.bankinplay_company_id)
            if (has_apikey and has_company_id
                    and line.bankinplay_enabled
                    and prov_count == journal_count
                    and journal_count > 0):
                line.status = "configured"
            elif has_apikey or prov_count > 0:
                line.status = "partial"
            else:
                line.status = "not_configured"

    def action_open_company(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.company",
            "res_id": self.company_id.id,
            "view_mode": "form",
            "target": "current",
        }



class BankinplayMassCompanyWizard(models.TransientModel):
    _name = "bankinplay.mass.company.wizard"
    _description = "Bankinplay Mass Company Configuration Wizard"

    line_ids = fields.One2many(
        comodel_name="bankinplay.mass.company.wizard.line",
        inverse_name="wizard_id",
        string="Compañías",
    )
    company_ids = fields.Many2many(
        comodel_name="res.company",
        string="Compañías a configurar",
    )
    select_all = fields.Boolean(
        string="Todas las compañías",
    )
    select_pending = fields.Boolean(
        string="Compañías pendientes",
    )

    # --- Config fields ---
    bankinplay_apikey = fields.Char(
        string="API Key",
    )
    bankinplay_apisecret = fields.Char(
        string="API Secret",
    )
    config_enabled = fields.Boolean(
        string="Habilitar BankInPlay",
        default=True,
    )
    bankinplay_start_date = fields.Date(
        string="Fecha inicio conciliación",
    )
    bankinplay_bank_statement_start_date = fields.Date(
        string="Fecha inicio extractos",
    )
    config_bank_statements_synced = fields.Boolean(
        string="Extractos sincronizados",
        default=False,
    )

    # --- Actions tab ---
    action_company_id = fields.Many2one(
        comodel_name="res.company",
        string="Compañía",
    )

    def _ensure_action_company(self):
        if not self.action_company_id:
            raise UserError(_("Selecciona una compañía para ejecutar acciones."))
        return self.action_company_id

    def _action_notify(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("BankInPlay - %s") % self.action_company_id.name,
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

    def action_wiz_test_connection(self):
        return self._ensure_action_company().test_bankinplay_connection()

    def action_wiz_export_account_plan(self):
        return self._ensure_action_company().export_account_plan()

    def action_wiz_export_analytic_plan(self):
        return self._ensure_action_company().export_analytic_plan()

    def action_wiz_export_documents(self):
        self._ensure_action_company().bankinplay_export_documents()
        return self._action_notify(_("Exportar documentos lanzado"))

    def action_wiz_import_documents(self):
        self._ensure_action_company().bankinplay_import_documents()
        return self._action_notify(_("Importar documentos lanzado"))

    def action_wiz_import_account_moves(self):
        self._ensure_action_company().bankinplay_import_account_moves()
        return self._action_notify(_("Importar movimientos contables lanzado"))

    def action_wiz_export_account_move_line(self):
        self._ensure_action_company().bankinplay_export_account_move_line()
        return self._action_notify(_("Exportar apuntes contables lanzado"))

    @api.model
    def action_open_wizard(self):
        """Create the wizard with lines already in DB, then open it."""
        companies = self.env["res.company"].sudo().search([])
        vals = {
            "line_ids": [(0, 0, {"company_id": c.id}) for c in companies],
        }
        for company in companies:
            if company.bankinplay_apikey and company.bankinplay_apisecret:
                vals["bankinplay_apikey"] = company.bankinplay_apikey
                vals["bankinplay_apisecret"] = company.bankinplay_apisecret
                break
        wizard = self.create(vals)
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    @api.onchange("select_all")
    def _onchange_select_all(self):
        if self.select_all:
            self.select_pending = False
            self.company_ids = self.line_ids.mapped("company_id")
        elif not self.select_pending:
            self.company_ids = False

    @api.onchange("select_pending")
    def _onchange_select_pending(self):
        if self.select_pending:
            self.select_all = False
            pending = self.env["res.company"].sudo().search([
                ("bankinplay_enabled", "=", False),
                ("id", "in", self.line_ids.mapped("company_id").ids),
            ])
            self.company_ids = pending
        elif not self.select_all:
            self.company_ids = False

    @api.onchange("company_ids")
    def _onchange_company_ids(self):
        """Pre-fill config fields if all selected companies share the same value."""
        if not self.company_ids:
            return
        companies = self.company_ids.sudo()
        # Dates: set if all share the same value
        start_dates = set(companies.mapped("bankinplay_start_date"))
        if len(start_dates) == 1:
            self.bankinplay_start_date = start_dates.pop()
        stmt_dates = set(companies.mapped("bankinplay_bank_statement_start_date"))
        if len(stmt_dates) == 1:
            self.bankinplay_bank_statement_start_date = stmt_dates.pop()
        # Booleans: set if all share the same value
        enabled_vals = set(companies.mapped("bankinplay_enabled"))
        if len(enabled_vals) == 1:
            self.config_enabled = enabled_vals.pop()
        synced_vals = set(companies.mapped("bankinplay_bank_statements_synced"))
        if len(synced_vals) == 1:
            self.config_bank_statements_synced = synced_vals.pop()

    def action_import_pending_statements(self):
        """Import bank statements for companies with pending sync."""
        self.ensure_one()
        Provider = self.env["online.bank.statement.provider"].sudo()
        companies = self.env["res.company"].sudo().search([
            ("bankinplay_bank_statement_start_date", "!=", False),
            ("bankinplay_bank_statements_synced", "=", False),
        ])
        if not companies:
            raise UserError(_("No hay compañías con extractos pendientes."))

        errors = []
        success_count = 0
        for company in companies:
            date_since = datetime.datetime.combine(
                company.bankinplay_bank_statement_start_date,
                datetime.time.min,
            )
            date_until = datetime.datetime.now()
            providers = Provider.search([
                ("active", "=", True),
                ("service", "=", "bankinplay"),
                ("journal_id.company_id", "=", company.id),
            ])
            for provider in providers:
                try:
                    provider._pull(date_since, date_until)
                    success_count += 1
                except Exception as e:
                    errors.append(
                        "%s - %s: %s" % (
                            company.name,
                            provider.journal_id.name,
                            str(e),
                        )
                    )
                    _logger.error(
                        "Error pulling journal %s (%s): %s",
                        provider.journal_id.name,
                        company.name,
                        str(e),
                    )

            company_has_error = any(
                company.name in e for e in errors
            )
            if not company_has_error:
                company.bankinplay_bank_statements_synced = True

            line = self.line_ids.filtered(
                lambda l: l.company_id == company
            )
            if line:
                if company_has_error:
                    line.result_message = _("Error importando extractos")
                else:
                    line.result_message = _("Extractos importados")

        message = _("%d proveedores procesados") % success_count
        if errors:
            message += "\n" + "\n".join(errors)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Importar extractos pendientes"),
                "message": message,
                "type": "warning" if errors else "success",
                "sticky": bool(errors),
            },
        }

    def action_apply_config(self):
        """Apply config, login BIP if needed, match VAT, assign journals."""
        self.ensure_one()
        if not self.company_ids:
            raise UserError(_("Selecciona al menos una compañía."))
        if not self.bankinplay_apikey or not self.bankinplay_apisecret:
            raise UserError(_("Introduce API Key y API Secret."))

        # Check if any company needs BIP API call
        companies_sudo = self.company_ids.sudo()
        needs_bip = companies_sudo.filtered(
            lambda c: not c.bankinplay_company_id
            or c.bankinplay_apikey != self.bankinplay_apikey
            or c.bankinplay_apisecret != self.bankinplay_apisecret
        )
        bip_companies = []
        if needs_bip:
            interface_model = self.env["bankinplay.interface"]
            try:
                access_data = interface_model._login(
                    self.bankinplay_apikey, self.bankinplay_apisecret
                )
            except Exception as e:
                raise UserError(
                    _("Conexión a BankInPlay fallida: %s") % str(e)
                )
            try:
                bip_companies = interface_model._get_companies(access_data)
            except Exception as e:
                raise UserError(
                    _("No se pudieron obtener las compañías de BankInPlay: %s") % str(e)
                )

        for company in companies_sudo:
            line = self.line_ids.filtered(
                lambda l: l.company_id == company
            )
            messages = []

            write_vals = {
                "bankinplay_apikey": self.bankinplay_apikey,
                "bankinplay_apisecret": self.bankinplay_apisecret,
                "bankinplay_enabled": self.config_enabled,
            }
            if self.config_bank_statements_synced:
                write_vals["bankinplay_bank_statements_synced"] = True
            if self.bankinplay_start_date:
                write_vals["bankinplay_start_date"] = self.bankinplay_start_date
            if self.bankinplay_bank_statement_start_date:
                write_vals["bankinplay_bank_statement_start_date"] = (
                    self.bankinplay_bank_statement_start_date
                )
            company.write(write_vals)

            # Match NIF only if needed
            if company in needs_bip:
                vat_clean = (company.vat or "").replace("ES", "").strip()
                matched = False
                if vat_clean:
                    for bip_company in bip_companies:
                        if bip_company.get("nif") == vat_clean:
                            company.bankinplay_company_id = bip_company["id"]
                            matched = True
                            break
                if not matched:
                    messages.append(
                        _("NIF '%s' no encontrado") % (company.vat or _("vacío"))
                    )
                else:
                    messages.append(_("NIF vinculado"))
            else:
                messages.append(_("NIF ya vinculado"))

            # Assign sale/purchase journals only if company has none
            if not company.sudo().bankinplay_journal_ids:
                invoice_journals = self.env["account.journal"].sudo().search([
                    ("type", "in", ("sale", "purchase")),
                    ("company_id", "=", company.id),
                ])
                if invoice_journals:
                    company.sudo().write({
                        "bankinplay_journal_ids": [(4, j.id) for j in invoice_journals],
                    })

            if line:
                line.result_message = " | ".join(messages)

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
