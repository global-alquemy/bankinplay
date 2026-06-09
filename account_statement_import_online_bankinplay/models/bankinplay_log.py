import logging
from odoo import models, fields, api
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90


class BankinplayLog(models.Model):
    _name = 'bankinplay.log'
    _description = 'Log Bankinplay'
    _order = 'date_time desc'

    operation_type = fields.Selection([
        ('request', 'Request'),
        ('response', 'Response'),
        ('error', 'Error'),
    ], string='Operation Type', required=True)

    response_id = fields.Char(string='Response ID')

    signature = fields.Char(string='Signature')

    related_log_id = fields.Many2one(
        'bankinplay.log', string='Related Bankinplay Log')

    date_time = fields.Datetime(
        string='Datetime', default=lambda self: fields.Datetime.now(), readonly=True)
    request_data = fields.Text(string='Request Data')
    response_data = fields.Text(string='Response Data')
    desencrypt_data = fields.Text(string='Desencrypt Data')
    event_data = fields.Text(string='Event Data')
    status = fields.Selection([
        ('success', 'Success'),
        ('error', 'Error'),
        ('pending', 'Pending'),
    ], string='Status', default='pending')

    notes = fields.Char(string='Notes')
    triggered_event = fields.Char(string='Triggered Event')
    company_id = fields.Many2one('res.company', string='Company')

    def set_status(self, status):
        self.ensure_one()
        self.status = status

    @api.model
    def _cron_purge_logs(self):
        """Purga programada del histórico de logs.

        Elimina los registros 'bankinplay.log' más antiguos que el periodo de
        retención configurado en el parámetro global
        'bankinplay.log_retention_days' (en días). Un valor menor o igual a 0
        desactiva la purga automática.
        """
        param = self.env['ir.config_parameter'].sudo().get_param(
            'bankinplay.log_retention_days', DEFAULT_RETENTION_DAYS)
        try:
            retention_days = int(param)
        except (TypeError, ValueError):
            retention_days = DEFAULT_RETENTION_DAYS
        if retention_days <= 0:
            return
        cutoff = fields.Datetime.now() - timedelta(days=retention_days)
        old_logs = self.sudo().search([('date_time', '<', cutoff)])
        count = len(old_logs)
        if count:
            old_logs.unlink()
            _logger.info(
                "Bankinplay: purgados %d registro(s) de log anteriores a %s "
                "(retención: %d días)", count, cutoff, retention_days)
