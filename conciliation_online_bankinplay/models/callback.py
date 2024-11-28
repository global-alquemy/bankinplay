import logging

from odoo import _, http
from odoo.http import request

_logger = logging.getLogger(__name__)

class CallbackController(http.Controller):

    @http.route('/webhook/estado', type='json', auth='public', methods=['POST'], csrf=False)
    def callback_estado(self, **kw):
        params = request.env['ir.config_parameter'].sudo()
        _logger.info("Callback estado: %s", kw)
        return {}