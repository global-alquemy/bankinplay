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
        _logger.info("URL: %s", request.httprequest.url)
        _logger.info("Método HTTP: %s", request.httprequest.method)
        _logger.info("Encabezados: %s", dict(request.httprequest.headers))
        _logger.info("Datos crudos: %s", request.httprequest.data.decode(
            'utf-8', errors='ignore'))
        _logger.info("Parámetros GET: %s", request.httprequest.args)
        _logger.info("Formulario POST: %s", request.httprequest.form)
        data = request.jsonrequest
        interface_model = request.env["bankinplay.interface"]
        log_entry, desencrypt_data, request_id = interface_model.manage_generic_callback(
            data)
        # Obtener los datos del cuerpo de la solicitud
        event_data = json.loads(request_id.event_data)

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

    @http.route('/webhook/lectura_tarjeta', auth='public', methods=['POST'], type='json')
    def callback_lectura_tarjeta(self, **kw):
        data = request.jsonrequest
        interface_model = request.env["bankinplay.interface"]
        log_entry, desencrypt_data, request_id = interface_model.manage_generic_callback(
            data)
        # Obtener los datos del cuerpo de la solicitud
        event_data = json.loads(request_id.event_data)

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

        response = interface_model.sudo().manage_lectura_tarjeta_callback(
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
