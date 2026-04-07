# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BankinplayActionsWizard(models.TransientModel):
    _name = "bankinplay.actions.wizard"
    _description = "BankInPlay Actions Wizard"

    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Compañía",
        required=True,
        default=lambda self: self.env.company,
        domain="[('bankinplay_enabled', '=', True)]",
    )

    def _ensure_company(self):
        self.ensure_one()
        if not self.company_id:
            raise UserError(_("Selecciona una compañía."))
        return self.company_id

    def _action_notify(self, title, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

    def action_import_account_moves(self):
        company = self._ensure_company()
        company.bankinplay_import_account_moves()
        return self._action_notify(
            _("BankInPlay - %s") % company.name,
            _("Importar movimientos contables finalizado."),
        )

    def action_import_documents(self):
        company = self._ensure_company()
        company.bankinplay_import_documents()
        return self._action_notify(
            _("BankInPlay - %s") % company.name,
            _("Importar terceros finalizado."),
        )

    def action_export_account_move_line(self):
        company = self._ensure_company()
        company.bankinplay_export_account_move_line()
        return self._action_notify(
            _("BankInPlay - %s") % company.name,
            _("Exportar apuntes contables finalizado."),
        )

    def action_export_documents(self):
        company = self._ensure_company()
        company.bankinplay_export_documents()
        return self._action_notify(
            _("BankInPlay - %s") % company.name,
            _("Exportar terceros finalizado."),
        )
