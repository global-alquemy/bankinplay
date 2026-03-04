# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

import logging
import json

from odoo import _, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class ConciliationCallbackController(http.Controller):

    @http.route('/webhook/conciliacionTerceros', auth='public', methods=['POST'], type='json')
    def callback_conciliacion_terceros(self, **kw):
        data = json.loads(
            request.httprequest.data.decode(
                request.httprequest.charset or "utf-8")
        )
        interface_model = request.env["bankinplay.interface"]
        log_entry, desencrypt_data, request_id = interface_model.manage_generic_callback(
            data)

        if not request_id or not request_id.event_data:
            return {"status": "error", "message": "No se encontró petición original"}
        event_data = json.loads(request_id.event_data)

        if not desencrypt_data or (desencrypt_data.get('sociedades') and len(desencrypt_data.get('sociedades')) == 0):
            request_id.write({
                'status': 'error',
                'related_log_id': log_entry.id,
            })
            log_entry.write({
                'status': 'error',
                'related_log_id': request_id.id,
            })
            return {"status": "success", "message": "Datos recibidos correctamente (vacío)"}

        response = interface_model.sudo().manage_conciliacion_terceros_callback(
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

        return {"status": "success", "message": "Datos recibidos correctamente"}

    @http.route('/webhook/asientoContable', auth='public', methods=['POST'], type='json')
    def callback_asiento_contable(self, **kw):
        data = json.loads(
            request.httprequest.data.decode(
                request.httprequest.charset or "utf-8")
        )
        interface_model = request.env["bankinplay.interface"]
        log_entry, desencrypt_data, request_id = interface_model.manage_generic_callback(
            data)

        if not request_id or not request_id.event_data:
            return {"status": "error", "message": "No se encontró petición original"}
        event_data = json.loads(request_id.event_data)

        if not desencrypt_data or (desencrypt_data.get('results') and len(desencrypt_data.get('results')) == 0):
            request_id.write({
                'status': 'error',
                'related_log_id': log_entry.id,
            })
            log_entry.write({
                'status': 'error',
                'related_log_id': request_id.id,
            })
            return {"status": "success", "message": "Datos recibidos correctamente (vacío)"}

        response = interface_model.sudo().manage_asiento_contable_callback(
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

        return {"status": "success", "message": "Datos recibidos correctamente"}
