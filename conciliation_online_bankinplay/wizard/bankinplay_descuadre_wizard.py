# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Reparación de asientos de extracto descuadrados (Debe != Haber).

"Analizar" solo lista y propone (no escribe nada en contabilidad); "Reparar"
aplica la propuesta asiento a asiento, cada uno en su propio savepoint.
La lógica contable está en account.bank.statement.line (bank_statement.py).
"""
import logging

from odoo import _, fields, models, Command
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DESCUADRE_STATUS = [
    ('ok', 'Reparable'),
    ('ok_unmatched', 'Reparable sin factura'),
    ('review', 'Revisar a mano'),
    ('repaired', 'Reparado'),
    ('error', 'Error'),
]


class BankinplayDescuadreWizard(models.TransientModel):
    _name = "bankinplay.descuadre.wizard"
    _description = "BankInPlay - Reparar asientos descuadrados"

    company_ids = fields.Many2many(
        comodel_name="res.company",
        string="Compañías",
        required=True,
        default=lambda self: self.env.companies,
    )
    date_from = fields.Date(string="Desde")
    date_to = fields.Date(string="Hasta")
    allow_unmatched = fields.Boolean(
        string="Reparar también sin factura",
        help="Cuadra también los asientos en los que no se encuentra una única "
             "factura pendiente que encaje: la contrapartida pasa al lado "
             "correcto pero queda pendiente de conciliar a mano.",
    )
    state = fields.Selection(
        [('draft', 'Inicio'), ('analyzed', 'Analizado'), ('done', 'Reparado')],
        default='draft',
    )
    line_ids = fields.One2many(
        comodel_name="bankinplay.descuadre.wizard.line",
        inverse_name="wizard_id",
        string="Asientos descuadrados",
    )

    def _companies(self):
        """Compañías elegidas a las que el usuario tiene acceso."""
        self.ensure_one()
        return self.company_ids & self.env.user.company_ids

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Reparar asientos descuadrados"),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {'dialog_size': 'extra-large'},
        }

    def action_analyze(self):
        """Simulación: detecta los descuadres y propone la corrección."""
        self.ensure_one()
        companies = self._companies()
        if not companies:
            raise UserError(_("Selecciona al menos una compañía."))
        StLine = self.env['account.bank.statement.line'].with_context(
            allowed_company_ids=companies.ids)
        vals_list = []
        for st_line in StLine._bankinplay_find_descuadres(
                companies, self.date_from, self.date_to):
            plan = st_line._bankinplay_descuadre_plan()
            vals_list.append({
                'statement_line_id': st_line.id,
                'move_id': st_line.move_id.id,
                'company_id': st_line.company_id.id,
                'journal_id': st_line.journal_id.id,
                'date': st_line.date,
                'payment_ref': st_line.payment_ref,
                'currency_id': st_line.company_id.currency_id.id,
                'amount': st_line.amount,
                'diff': plan['diff'],
                'status': plan['status'],
                'detail': plan['detail'],
                'selected': plan['status'] in ('ok', 'ok_unmatched'),
            })
        self.write({
            'line_ids': [Command.clear()] + [Command.create(v) for v in vals_list],
            'state': 'analyzed',
        })
        return self._reopen()

    def action_repair(self):
        """Repara los seleccionados, cada asiento en su propio savepoint."""
        self.ensure_one()
        statuses = ('ok', 'ok_unmatched') if self.allow_unmatched else ('ok',)
        todo = self.line_ids.filtered(
            lambda line: line.selected and line.status in statuses)
        if not todo:
            raise UserError(_("No hay asientos seleccionados que se puedan reparar."))
        companies = self._companies()
        for wline in todo:
            st_line = wline.statement_line_id.with_context(
                allowed_company_ids=companies.ids)
            try:
                with self.env.cr.savepoint():
                    plan = st_line._bankinplay_fix_descuadre(self.allow_unmatched)
                wline.write({'status': 'repaired', 'detail': plan['detail']})
            except UserError as e:
                # Error de negocio esperado (fecha de bloqueo, validaciones...)
                _logger.warning(
                    "BankInPlay: no se pudo reparar el descuadre de %s: %s",
                    wline.move_id.name, e)
                wline.write({'status': 'error', 'detail': str(e)})
            except Exception as e:  # noqa: BLE001 - aislamos por asiento
                _logger.exception(
                    "BankInPlay: error reparando el descuadre de %s",
                    wline.move_id.name)
                wline.write({'status': 'error', 'detail': str(e)})
        self.state = 'done'
        return self._reopen()


class BankinplayDescuadreWizardLine(models.TransientModel):
    _name = "bankinplay.descuadre.wizard.line"
    _description = "BankInPlay - Asiento descuadrado"
    _order = "date, id"

    wizard_id = fields.Many2one(
        comodel_name="bankinplay.descuadre.wizard",
        required=True,
        ondelete="cascade",
    )
    selected = fields.Boolean(string="Reparar", default=True)
    statement_line_id = fields.Many2one(
        comodel_name="account.bank.statement.line",
        string="Línea de extracto",
        readonly=True,
    )
    move_id = fields.Many2one(
        comodel_name="account.move", string="Asiento", readonly=True)
    company_id = fields.Many2one(
        comodel_name="res.company", string="Compañía", readonly=True)
    journal_id = fields.Many2one(
        comodel_name="account.journal", string="Diario", readonly=True)
    date = fields.Date(string="Fecha", readonly=True)
    payment_ref = fields.Char(string="Concepto", readonly=True)
    currency_id = fields.Many2one(comodel_name="res.currency", readonly=True)
    amount = fields.Monetary(
        string="Importe banco", currency_field="currency_id", readonly=True)
    diff = fields.Monetary(
        string="Descuadre (Debe - Haber)", currency_field="currency_id",
        readonly=True)
    status = fields.Selection(DESCUADRE_STATUS, string="Estado", readonly=True)
    detail = fields.Text(string="Propuesta / resultado", readonly=True)

    def action_open_move(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.move_id.id,
            'view_mode': 'form',
            'target': 'new',
        }
