import logging

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

        # Mostrar los datos en el log para depuración

        # _logger.info('Datos recibidos en webhook: %s', data)

        # Acceder a parámetros específicos
        data_ = data.get('data')
        triggered_event = data.get('triggered_event')
        signature = data.get('signature')
        response_id = data.get('responseId')

        # Realizar alguna operación con los datos
        _logger.info(f"Parámetro 1: {data_}")
        _logger.info(f"Parámetro 1: {triggered_event}")
        _logger.info(f"Parámetro 1: {signature}")
        _logger.info(f"Parámetro 1: {response_id}")

        # Responder al cliente
        return {"status": "success", "message": "Datos recibidos correctamente"}
