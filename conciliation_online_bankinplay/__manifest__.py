# Copyright 2024 - Global Alquemy SL
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Online Conciliation: BankInPlay",
    "version": "14.0.2.0.0",
    "category": "Account",
    "website": "www.alquemy.es",
    "author": "Global Alquemy SL",
    "license": "AGPL-3",
    "installable": True,
    "depends": ["account_statement_import_online_bankinplay", "queue_job_batch"],
    "data": [
        "views/res_company.xml",
        "views/account_move_view.xml",
    ],
}
