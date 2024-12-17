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

        interface_model = request.env["bankinplay.interface"]
        # Obtener los datos del cuerpo de la solicitud
        data = request.jsonrequest

        # Acceder a parámetros específicos
        _data = data.get('data')
        signature = data.get('signature')
        response_id = data.get('responseId')

        request_id = request.env['bankinplay.log'].sudo().search(
            [('response_id', '=', response_id),
             ('signature', '=', signature)])

        event_data = json.loads(request_id.event_data)
        access_data = event_data.get('access_data')

        _logger.info("Request ID: %s", request_id)
        _logger.info("Request Event Data: %s", request_id.event_data)
        _logger.info("Request Event Data: %s",
                     access_data)

        desencrypt_data = interface_model._desencrypt_data(
            data, access_data)

        log_entry = request.env['bankinplay.log'].create({
            'operation_type': 'response',
            'request_data': '',
            'response_data': data,
            'desencrypt_data': desencrypt_data,
            'status': 'pending',
            'notes': 'Callback - lectura_intradia',
            'response_id': response_id,
            'signature': signature,
        })

        if desencrypt_data.get('results') and len(desencrypt_data.get('results')) == 0:
            request_id.write({
                'status': 'error',
                'related_log_id': log_entry.id,
            })
            log_entry.write({
                'status': 'error',
                'related_log_id': request_id.id,
            })

            return {"status": "success", "message": "Datos recibidos correctamente"}

        response = interface_model.sudo().manage_lectura_intradia_callback(
            desencrypt_data, event_data
        )

        if response:
            request_id.write({
                'status': 'success',
                'related_log_id': log_entry.id,
            })
            log_entry.write({
                'status': 'success',
                'related_log_id': request_id.id,
            })

        # Responder al cliente
        return {"status": "success", "message": "Datos recibidos correctamente"}
