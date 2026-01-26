# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BankinplayMassConfigWizard(models.TransientModel):
    _name = "bankinplay.mass.config.wizard"
    _description = "Bankinplay Mass Configuration Wizard"

    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    journal_ids = fields.Many2many(
        comodel_name="account.journal",
        string="Bank Journals",
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]",
        required=True,
    )
    bankinplay_import_type = fields.Selection(
        [
            ("intraday", "Intraday"),
            ("close", "Close"),
        ],
        string="Import Type",
        default="close",
        required=True,
    )
    bankinplay_date_field = fields.Selection(
        [
            ("execution_date", "Execution Date"),
            ("value_date", "Value Date"),
        ],
        string="Date Field",
        default="execution_date",
        required=True,
    )
    interval_type = fields.Selection(
        selection=[
            ("minutes", "Minute(s)"),
            ("hours", "Hour(s)"),
            ("days", "Day(s)"),
            ("weeks", "Week(s)"),
        ],
        default="hours",
        required=True,
    )
    interval_number = fields.Integer(
        string="Scheduled update interval",
        default=1,
        required=True,
    )
    statement_creation_mode = fields.Selection(
        selection=[
            ("daily", "Day"),
            ("weekly", "Week"),
            ("monthly", "Month"),
        ],
        default="daily",
        string="Statement Creation Mode",
        required=True,
    )
    tz = fields.Selection(
        selection="_tz_get",
        string="Timezone",
        default=lambda self: self.env.context.get("tz") or "Europe/Madrid",
    )
    overwrite_existing = fields.Boolean(
        string="Overwrite existing providers",
        default=False,
        help="If checked, existing Bankinplay providers will be updated with the new configuration. "
             "If unchecked, journals with existing providers will be skipped.",
    )

    @api.model
    def _tz_get(self):
        return [(tz, tz) for tz in sorted(
            set(fields.Datetime().python_type.now().astimezone().tzinfo._tzinfos.keys())
            if hasattr(fields.Datetime().python_type.now().astimezone().tzinfo, '_tzinfos')
            else []
        )] or [('Europe/Madrid', 'Europe/Madrid'), ('UTC', 'UTC')]

    @api.onchange('company_id')
    def _onchange_company_id(self):
        self.journal_ids = False
        return {
            'domain': {
                'journal_ids': [('type', '=', 'bank'), ('company_id', '=', self.company_id.id)]
            }
        }

    def _get_provider_vals(self, journal):
        """Prepare values for creating/updating the provider."""
        return {
            'journal_id': journal.id,
            'service': 'bankinplay',
            'username': self.company_id.bankinplay_apikey,
            'password': self.company_id.bankinplay_apisecret,
            'bankinplay_import_type': self.bankinplay_import_type,
            'bankinplay_date_field': self.bankinplay_date_field,
            'interval_type': self.interval_type,
            'interval_number': self.interval_number,
            'statement_creation_mode': self.statement_creation_mode,
            'tz': self.tz,
            'active': True,
        }

    def action_configure(self):
        """Configure Bankinplay for selected journals."""
        self.ensure_one()

        if not self.company_id.bankinplay_apikey or not self.company_id.bankinplay_apisecret:
            raise UserError(_(
                "Please configure Bankinplay API Key and Secret in the company settings first.\n"
                "Go to: Settings > Companies > Your Company > Bankinplay tab"
            ))

        if not self.journal_ids:
            raise UserError(_("Please select at least one bank journal."))

        Provider = self.env['online.bank.statement.provider']
        created_count = 0
        updated_count = 0
        skipped_journals = []

        for journal in self.journal_ids:
            existing_provider = Provider.search([
                ('journal_id', '=', journal.id)
            ], limit=1)

            if existing_provider:
                if self.overwrite_existing:
                    existing_provider.write(self._get_provider_vals(journal))
                    updated_count += 1
                else:
                    skipped_journals.append(journal.name)
            else:
                Provider.create(self._get_provider_vals(journal))
                created_count += 1

        message_parts = []
        if created_count:
            message_parts.append(_("%d provider(s) created") % created_count)
        if updated_count:
            message_parts.append(_("%d provider(s) updated") % updated_count)
        if skipped_journals:
            message_parts.append(
                _("%d journal(s) skipped (already configured): %s") % (
                    len(skipped_journals),
                    ", ".join(skipped_journals)
                )
            )

        message = "\n".join(message_parts) if message_parts else _("No changes made")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bankinplay Configuration'),
                'message': message,
                'type': 'success' if (created_count or updated_count) else 'warning',
                'sticky': False,
            }
        }

    def action_select_all_journals(self):
        """Select all bank journals for the current company."""
        self.ensure_one()
        journals = self.env['account.journal'].search([
            ('type', '=', 'bank'),
            ('company_id', '=', self.company_id.id),
        ])
        self.journal_ids = journals
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
