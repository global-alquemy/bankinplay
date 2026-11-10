# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Cola de anulaciones de BankInPlay.

La exportación de terceros solo envía apuntes contabilizados. Si un apunte de
cliente/proveedor ya enviado se borra (o se cancela su factura), BankInPlay lo
seguiría teniendo como pendiente y proponiéndolo para conciliar cobros. Esos
apuntes se encolan aquí y un cron los anula en BankInPlay.
"""
import logging
from itertools import groupby

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

RECEIVABLE_PAYABLE = ('asset_receivable', 'liability_payable')
# Reintentos automáticos de los registros en error (el cron solo los reintenta
# mientras no se alcance este número; después quedan para revisión manual).
MAX_AUTO_ATTEMPTS = 5


class BankinplayCancelQueue(models.Model):
    _name = 'bankinplay.cancel.queue'
    _description = 'BankInPlay - Documento a anular'
    _order = 'create_date desc, id desc'
    _rec_name = 'move_name'

    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True, ondelete='cascade', index=True)
    move_line_id = fields.Integer(
        string='ID del apunte', required=True, index=True,
        help='Identificador con el que se envió el documento a BankInPlay '
             '(id_documento_erp). El apunte puede no existir ya en Odoo.')
    move_name = fields.Char(string='Documento')
    partner_name = fields.Char(string='Cliente/Proveedor')
    amount = fields.Monetary(string='Importe', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Moneda')
    reason = fields.Selection(
        [('deleted', 'Apunte eliminado'), ('cancelled', 'Factura cancelada')],
        string='Motivo', required=True, default='deleted')
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('done', 'Anulado'),
        ('not_found', 'No estaba en BankInPlay'),
        ('skipped', 'Sin efecto'),
        ('error', 'Error'),
    ], string='Estado', default='pending', required=True, index=True)
    attempts = fields.Integer(string='Nº de intentos', default=0, copy=False)
    error_message = fields.Text(string='Resultado')
    process_date = fields.Datetime(string='Fecha de proceso')

    # ------------------------------------------------------------------
    # Alta en la cola
    # ------------------------------------------------------------------
    @api.model
    def _enqueue_lines(self, lines, reason):
        """Encola la anulación de los apuntes de cliente/proveedor ya enviados.

        No duplica: si el apunte ya tiene una anulación pendiente o en error,
        no se crea otra. Se crea con sudo porque lo dispara quien borra o
        cancela la factura, que no tiene por qué ver la cola.
        """
        lines = lines.filtered(
            lambda aml: aml.bankinplay_sent
            and aml.account_id.account_type in RECEIVABLE_PAYABLE)
        if not lines:
            return self.browse()
        Queue = self.sudo()
        open_ids = set(Queue.search([
            ('move_line_id', 'in', lines.ids),
            ('state', 'in', ('pending', 'error')),
        ]).mapped('move_line_id'))
        vals_list = [{
            'company_id': line.company_id.id,
            'move_line_id': line.id,
            'move_name': line.move_id.name,
            'partner_name': line.partner_id.name or '',
            'amount': line.balance,
            'currency_id': line.company_currency_id.id,
            'reason': reason,
        } for line in lines if line.id not in open_ids]
        records = Queue.create(vals_list)
        for record in records:
            _logger.info("BankInPlay: apunte %s (%s) encolado para anular (%s)",
                         record.move_line_id, record.move_name, reason)
        return records

    # ------------------------------------------------------------------
    # Proceso
    # ------------------------------------------------------------------
    def _set_result(self, state, message):
        self.ensure_one()
        self.write({
            'state': state,
            'error_message': message,
            'process_date': fields.Datetime.now(),
            'attempts': self.attempts + 1,
        })

    def _process(self):
        """Anula en BankInPlay estos registros (una conexión por compañía).

        - Si el apunte vuelve a estar contabilizado, no se anula: la exportación
          de terceros lo actualizará (``skipped``).
        - Si BankInPlay no lo tiene, se da por resuelto (``not_found``).
        - Cualquier otro fallo deja el registro en ``error`` para reintentar.

        :return: dict con el número de registros por resultado.
        """
        counts = dict.fromkeys(('done', 'not_found', 'skipped', 'error'), 0)
        Interface = self.env['bankinplay.interface']
        AML = self.env['account.move.line'].sudo()
        records = self.sudo().filtered(lambda r: r.state in ('pending', 'error'))
        for company, group in groupby(records.sorted('company_id'), key=lambda r: r.company_id):
            group = self.browse([r.id for r in group]).sudo()
            try:
                access_data = company.check_bankinplay_connection()
            except Exception as e:  # sin conexión: todo el grupo queda en error
                for record in group:
                    record._set_result('error', str(e))
                counts['error'] += len(group)
                continue
            for record in group:
                line = AML.browse(record.move_line_id).exists()
                if line and line.parent_state == 'posted':
                    record._set_result('skipped', _(
                        "El apunte vuelve a estar contabilizado: no se anula, "
                        "la exportación de terceros lo actualizará."))
                    counts['skipped'] += 1
                    continue
                try:
                    with self.env.cr.savepoint():
                        state, message = Interface._cancel_document_line(
                            access_data, record.move_line_id)
                except Exception as e:
                    _logger.warning("BankInPlay: error al anular el apunte %s: %s",
                                    record.move_line_id, e)
                    record._set_result('error', str(e))
                    counts['error'] += 1
                    continue
                record._set_result(state, message)
                counts[state] += 1
                if line:
                    # Ya no está en BankInPlay: si vuelve a contabilizarse, la
                    # exportación lo enviará como nuevo.
                    line.write({'bankinplay_sent': False, 'bankinplay_needs_update': False})
        return counts

    @api.model
    def _cron_process_pending(self):
        self.search([
            '|', ('state', '=', 'pending'),
            '&', ('state', '=', 'error'), ('attempts', '<', MAX_AUTO_ATTEMPTS),
        ])._process()

    def process_cancel_queue(self):
        """Compatibilidad con el cron de la versión 15.0."""
        return self._cron_process_pending()

    def action_process(self):
        counts = self._process()
        parts = []
        labels = (('done', _("anulados")), ('not_found', _("no estaban en BankInPlay")),
                  ('skipped', _("sin efecto")), ('error', _("con error")))
        for key, label in labels:
            if counts[key]:
                parts.append("%s %s" % (counts[key], label))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Cola de anulaciones BankInPlay"),
                'message': ", ".join(parts) if parts else _("No había nada pendiente."),
                'type': 'warning' if counts['error'] else 'success',
                'sticky': bool(counts['error']),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_discard(self):
        """Descarta a mano anulaciones que no deben enviarse."""
        self.filtered(lambda r: r.state in ('pending', 'error')).write({
            'state': 'skipped',
            'error_message': _("Descartado a mano por %s.") % self.env.user.name,
            'process_date': fields.Datetime.now(),
        })
