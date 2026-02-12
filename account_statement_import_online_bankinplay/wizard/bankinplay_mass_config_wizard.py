# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BankinplayMassConfigWizard(models.TransientModel):
    _name = "bankinplay.mass.config.wizard"
    _description = "Bankinplay Mass Configuration Wizard"

    company_ids = fields.Many2many(
        comodel_name="res.company",
        string="Companies",
        required=True,
        default=lambda self: self.env["res.company"].sudo().search([]),
    )
    journal_ids = fields.Many2many(
        comodel_name="account.journal",
        string="Bank Journals",
        domain="[('type', '=', 'bank'), ('company_id', 'in', company_ids)]",
    )
    all_journals = fields.Boolean(
        string="Select all bank journals",
        default=True,
        help="If checked, all bank journals of the selected companies will be configured.",
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
        default=4,
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
        try:
            import pytz
            return [(tz, tz) for tz in sorted(pytz.common_timezones)]
        except ImportError:
            return [('Europe/Madrid', 'Europe/Madrid'), ('UTC', 'UTC')]

    @api.onchange('company_ids')
    def _onchange_company_ids(self):
        if self.company_ids:
            self.journal_ids = self.env['account.journal'].search([
                ('type', '=', 'bank'),
                ('company_id', 'in', self.company_ids.ids),
            ])
        else:
            self.journal_ids = False

    def _get_journals(self):
        """Get journals to configure based on selection."""
        if self.all_journals:
            return self.env['account.journal'].sudo().search([
                ('type', '=', 'bank'),
                ('company_id', 'in', self.company_ids.ids),
            ])
        return self.journal_ids

    def _get_provider_vals(self, journal):
        """Prepare values for creating/updating the provider."""
        company = journal.company_id
        return {
            'journal_id': journal.id,
            'service': 'bankinplay',
            'username': company.bankinplay_apikey,
            'password': company.bankinplay_apisecret,
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

        if not self.company_ids:
            raise UserError(_("Please select at least one company."))

        # Check all companies have API keys
        companies_sudo = self.company_ids.sudo()
        missing = companies_sudo.filtered(
            lambda c: not c.bankinplay_apikey or not c.bankinplay_apisecret
        )
        if missing:
            raise UserError(_(
                "The following companies don't have Bankinplay API Key/Secret configured:\n%s\n\n"
                "Go to: Settings > Companies > BankInPlay tab"
            ) % "\n".join("- %s" % c.name for c in missing))

        journals = self._get_journals()
        if not journals:
            raise UserError(_("No bank journals found for the selected companies."))

        Provider = self.env['online.bank.statement.provider'].sudo()
        created_count = 0
        updated_count = 0
        skipped_journals = []

        for journal in journals:
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
