from odoo import models, fields

class BankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    bankinplay_sent = fields.Boolean(string='Enviado a BankinPlay', default=False)
    bankinplay_conciliation = fields.Boolean(string='Bankinplay conciliation', default=False)

    