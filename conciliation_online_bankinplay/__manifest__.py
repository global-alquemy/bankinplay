# -*- coding: utf-8 -*-
# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
{
    "name": "Online Conciliation: BankInPlay",
    "version": "16.1.4.0.0",
    "category": "Account",
    "author": "Alquemy",
    "website": "https://www.alquemy.es",
    "license": "AGPL-3",
    "installable": True,
    "depends": ["account_statement_import_online_bankinplay", "partner_manual_rank", "account_statement_base", "queue_job"],
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",
        "data/queue_job_function.xml",
        "wizard/bankinplay_mass_company_wizard.xml",
        "wizard/bankinplay_actions_wizard.xml",
        "views/res_company.xml",
        "views/bank_statement.xml",
    ],
}
