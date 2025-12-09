# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

import logging
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class BankinplayCancelQueue(models.Model):
    _name = 'bankinplay.cancel.queue'
    _description = 'Cola de documentos a anular en BankinPlay'
    _order = 'create_date desc'

    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        ondelete='cascade'
    )
    move_line_id = fields.Integer(
        string='ID del apunte',
        required=True,
        help='ID original del account.move.line eliminado'
    )
    move_name = fields.Char(
        string='Nombre factura',
        help='Nombre de la factura para referencia'
    )
    partner_name = fields.Char(
        string='Cliente/Proveedor',
        help='Nombre del partner para referencia'
    )
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('done', 'Procesado'),
        ('error', 'Error'),
    ], string='Estado', default='pending', required=True)
    error_message = fields.Text(string='Mensaje de error')
    process_date = fields.Datetime(string='Fecha de proceso')

    def process_cancel_queue(self):
        """Procesar la cola de anulaciones pendientes."""
        pending_records = self.search([('state', '=', 'pending')])

        for record in pending_records:
            try:
                access_data = record.company_id.check_bankinplay_connection()
                interface_model = self.env["bankinplay.interface"]

                result = interface_model._cancel_document_line(
                    access_data,
                    record.move_line_id
                )

                if result is False:
                    # Documento no encontrado en BankinPlay, lo marcamos como procesado
                    record.write({
                        'state': 'done',
                        'process_date': fields.Datetime.now(),
                        'error_message': 'Documento no encontrado en BankinPlay (ya anulado o nunca existió)'
                    })
                else:
                    record.write({
                        'state': 'done',
                        'process_date': fields.Datetime.now()
                    })

                _logger.info(
                    "Documento %s anulado en BankinPlay (cola)",
                    record.move_line_id
                )

            except Exception as e:
                record.write({
                    'state': 'error',
                    'error_message': str(e),
                    'process_date': fields.Datetime.now()
                })
                _logger.error(
                    "Error al anular documento %s en BankinPlay: %s",
                    record.move_line_id,
                    e
                )

        return True

    def retry_failed(self):
        """Reintentar los registros con error."""
        self.filtered(lambda r: r.state == 'error').write({
            'state': 'pending',
            'error_message': False,
            'process_date': False
        })
