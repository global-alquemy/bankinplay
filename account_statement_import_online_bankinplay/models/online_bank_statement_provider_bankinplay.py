# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
import json
import logging
import re
from datetime import datetime

import pytz

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class OnlineBankStatementProviderBankInPlay(models.Model):
    _inherit = "online.bank.statement.provider"

    bankinplay_import_type = fields.Selection(
        [
            ("intraday", "Intraday"),
            ("close", "Close"),
        ],
        string="BankinPlay Import Type",
        default="close"
    )

    bankinplay_date_field = fields.Selection(
        [
            ("execution_date", "Execution Date"),
            ("value_date", "Value Date"),
        ],
        string="BankinPlay Date Field",
        default="execution_date",
        help="Select the Bankinplay date field that will be used for "
        "the Odoo bank statement line date. If you change this parameter "
        "on a provider that already has transactions, you will have to "
        "purge the BankInPlay buffers.",
    )

    bankinplay_is_card = fields.Boolean(
        string='¿Es tarjeta?',
    )

    bankinplay_card_number = fields.Char(
        string='Número de tarjeta',
    )

    @api.model
    def _get_available_services(self):
        """Each provider model must register its service."""
        return super()._get_available_services() + [
            ("bankinplay", "BankInPlay"),
        ]

    def _obtain_statement_data(self, date_since, date_until):
        """Check wether called for bankinplay servide, otherwise pass the buck."""
        self.ensure_one()
        if self.service != "bankinplay":  # pragma: no cover
            return super()._obtain_statement_data(
                date_since,
                date_until,
            )
        return self._bankinplay_obtain_statement_data(date_since, date_until)

    def _bankinplay_obtain_statement_data(self, date_since, date_until):
        """Translate information from BankInPlay to Odoo bank statement lines."""
        self.ensure_one()
        _logger.debug(
            _("BankInPlay obtain statement data for journal %s from %s to %s"),
            self.journal_id.name,
            date_since,
            date_until,
        )
        self._bankinplay_retrieve_data(date_since, date_until)
        new_transactions = []
        return new_transactions, {}

    def _bankinplay_retrieve_data(self, date_since, date_until):
        interface_model = self.env["bankinplay.interface"]
        access_data = interface_model._login(self.username, self.password)

        if self.bankinplay_is_card:
            if self.bankinplay_card_number:
                access_data = interface_model._set_access_card(
                    access_data, self.bankinplay_card_number)
        elif self.account_number:
            access_data = interface_model._set_access_account(
                access_data, self.account_number)

        if self.bankinplay_is_card:
            interface_model._get_card_transactions(
                access_data, date_since, date_until, self)
        elif self.bankinplay_import_type == "intraday":
            interface_model._get_transactions(
                access_data, date_since, date_until, self)
        else:
            interface_model._get_closing_transactions(
                access_data, date_since, date_until, self)

    def _bankinplay_get_transaction_vals(self, transaction, sequence):
        """Translate information from BankInPlay to statement line vals."""

        if self.bankinplay_is_card:
            return self._bankinplay_get_card_transaction_vals(transaction, sequence)

        side = -1 if transaction["signo"] == "Pago" else 1
        date = self._bankinplay_get_transaction_datetime(transaction)
        vals_line = {
            "sequence": sequence,
            "date": date,
            "ref": '/',
            "payment_ref": transaction["descripcion"],
            "unique_import_id": str(transaction["id"]),
            "transaction_type": transaction["instrumento"],
            "narration": transaction["notas"],
            "amount": transaction["importeAbsoluto"] * side,
        }
        return vals_line

    def _bankinplay_get_card_transaction_vals(self, transaction, sequence):
        """Translate information from BankInPlay to statement line vals."""
        _logger.info("BankInPlay get card transaction vals")
        _logger.info(transaction)
        datetime_str = transaction.get("fecha")
        date = self._bankinplay_datetime_from_string(datetime_str)

        side = -1 if transaction["signo"] == "pago" else 1
        vals_line = {
            "sequence": sequence,
            "date": date,
            "ref": '/',
            "payment_ref": transaction["descripcion"],
            "unique_import_id": str(transaction["id"]),
            "transaction_type": '',
            "narration": transaction["notas"],
            "amount": transaction["importe"] * side,
        }
        return vals_line

    def _bankinplay_get_transaction_datetime(self, transaction):
        """Get execution datetime for a transaction.

        Odoo often names variables containing date and time just xxx_date or
        date_xxx. We try to avoid this misleading naming by using datetime as
        much for variables and fields of type datetime.
        """
        if self.bankinplay_date_field == "value_date":
            datetime_str = transaction.get("fechaValor")
        else:
            datetime_str = transaction.get("fechaOperacion")
        return self._bankinplay_datetime_from_string(datetime_str)

    def _bankinplay_datetime_from_string(self, datetime_str):
        """Dates in BankInPlay are expressed in UTC, so we need to convert them
        to supplied tz for proper classification.
        """
        dt = datetime.strptime(datetime_str, "%Y-%m-%dT%H:%M:%SZ")
        dt = dt.replace(tzinfo=pytz.utc).astimezone(
            pytz.timezone(self.tz or "utc"))
        return dt.replace(tzinfo=None)

    def get_keys_from_company(self):
        self.ensure_one()
        company = self.company_id
        self.username = company.bankinplay_apikey
        self.password = company.bankinplay_apisecret
