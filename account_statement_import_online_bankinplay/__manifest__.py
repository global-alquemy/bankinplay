# Copyright 2024 - Global Alquemy SL
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Online Bank Statements: BankInPlay",
    "version": "15.0.0.0.0",
    "category": "Account",
    "website": "https://github.com/OCA/bank-statement-import",
    "author": "Global Alquemy, Odoo Community Association (OCA)",
    "license": "AGPL-3",
    "installable": True,
    "depends": ["account_statement_import_online"],
    "data": [
        "security/ir.model.access.csv",
        "views/online_bank_statement_provider.xml",
    ],
}
