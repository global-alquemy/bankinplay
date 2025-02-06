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

    @http.route('/webhook/lectura_cierre', auth='public', methods=['POST'], type='json')
    def callback_lectura_cierre(self, **kw):
        data = json.loads(
            request.httprequest.data.decode(
                request.httprequest.charset or "utf-8")
        )
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

        interface_model.sudo().with_delay().manage_lectura_cierre_callback(
            desencrypt_data, event_data, request_id, log_entry
        )

        

        return {"status": "success", "message": "Datos recibidos correctamente"}

    @http.route('/webhook/lectura_intradia', auth='public', methods=['POST'], type='json')
    def callback_lectura_intradia(self, **kw):
        data = json.loads(
            request.httprequest.data.decode(
                request.httprequest.charset or "utf-8")
        )
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

        interface_model.sudo().manage_lectura_intradia_callback(
            desencrypt_data, event_data, request_id, log_entry
        )


        # Responder al cliente
        return {"status": "success", "message": "Datos recibidos correctamente"}

    @http.route('/webhook/lectura_tarjeta', auth='public', methods=['POST'], type='json')
    def callback_lectura_tarjeta(self, **kw):
        data = json.loads(
            request.httprequest.data.decode(
                request.httprequest.charset or "utf-8")
        )
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

        interface_model.sudo().manage_lectura_tarjeta_callback(
            desencrypt_data, event_data, request_id, log_entry
        )

      

        # Responder al cliente
        return {"status": "success", "message": "Datos recibidos correctamente"}
