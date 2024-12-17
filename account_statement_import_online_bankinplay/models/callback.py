import logging
import json

from odoo import _, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class CallbackController(http.Controller):

    @http.route('/webhook/estado', auth='public', methods=['POST'], type='json')
    def callback_estado(self, **kw):
        params = request.env['ir.config_parameter'].sudo()
        _logger.info("Callback estado: %s", kw)
        return {}

    @http.route('/webhook/lectura_intradia', auth='public', methods=['POST'], type='json')
    def callback_lectura_intradia(self, **kw):
        # Obtener los datos del cuerpo de la solicitud
        data = request.jsonrequest

        # Acceder a parámetros específicos
        data_ = data.get('data')
        triggered_event = data.get('triggered_event')
        signature = data.get('signature')
        response_id = data.get('responseId')

        # Mostrar los datos en el log con etiqueta "bankinplay"
        _logger.info("bankinplay - Datos recibidos en webhook: %s", data)
        _logger.info("bankinplay - Parámetro data: %s", data_)
        _logger.info("bankinplay - Parámetro triggered_event: %s",
                     triggered_event)
        _logger.info("bankinplay - Parámetro signature: %s", signature)
        _logger.info("bankinplay - Parámetro responseId: %s", response_id)

        log_entry = self.env['bankinplay.log'].create({
            'operation_type': 'response',
            'request_data': '',
            'response_data': data,
            'status': 'pending',
            'notes': 'Callback - lectura_intradia',
            'response_id': response_id,
            'signature': signature,
        })

        # Responder al cliente
        return {"status": "success", "message": "Datos recibidos correctamente"}
