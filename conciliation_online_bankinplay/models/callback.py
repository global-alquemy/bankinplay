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
        params = request.env['ir.config_parameter'].sudo()
        _logger.info("Callback estado: %s", kw)
        return {}