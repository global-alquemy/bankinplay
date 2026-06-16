# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
from odoo import fields, models


class BankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    # NOTA (community): la lógica de conciliación (process_reconciliation,
    # _create_counterpart_and_new_aml, move_name, etc.) la aporta el módulo
    # OCA 'account_reconciliation_widget', del que ahora depende este módulo.
    # En la variante enterprise (rama 15.0) esos métodos van embebidos aquí
    # porque enterprise no instala dicho widget.
    bankinplay_sent = fields.Boolean(
        string='Enviado a BankinPlay', default=False)
    bankinplay_conciliation = fields.Boolean(
        string='Bankinplay conciliation', default=False)
