# -*- coding: utf-8 -*-
# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
{
    "name": "Online Bank Statements: BankInPlay",
    "version": "15.0.3.0.1",
    "category": "Account",
    "author": "Alquemy",
    "website": "https://www.alquemy.es",
    "license": "AGPL-3",
    "installable": True,
    "depends": ["account_statement_import_online"],
    "data": [
        "security/ir.model.access.csv",
        "views/online_bank_statement_provider.xml",
        "views/res_company.xml",
        "views/bankinplay_log.xml",
    ],
}
