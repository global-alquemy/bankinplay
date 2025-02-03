from odoo import models, fields, api
from datetime import datetime


class BankinplayLog(models.Model):
    _name = 'bankinplay.log'
    _description = 'Log Bankinplay'
    _order = 'date_time desc'

    operation_type = fields.Selection([
        ('request', 'Request'),
        ('response', 'Response'),
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

    def set_status(self, status):
        self.ensure_one()
        self.status = status
