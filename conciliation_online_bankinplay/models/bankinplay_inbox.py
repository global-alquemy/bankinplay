# 2026 Alquemy
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Base común del inbox durable de BankInPlay.

Patrón bandeja de entrada: el webhook solo registra (upsert) y un cron
procesa de forma idempotente, con estado, reintentos e histórico.
Ver SPEC_inbox_bankinplay.md.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

INBOX_STATES = [
    ('waiting', 'En espera'),
    ('ready', 'Listo'),
    ('done', 'Contabilizado'),
    ('error', 'Error'),
    ('skipped', 'Omitido'),
    ('superseded', 'Sustituido'),
    ('conflict', 'Conflicto'),
]

DEFAULT_BATCH = 10
DEFAULT_RETRY_MINUTES = 60


class BankinplayInboxMixin(models.AbstractModel):
    _name = 'bankinplay.inbox.mixin'
    _description = 'BankInPlay Inbox - base común'
    _order = 'create_date desc'

    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True, index=True)
    id_movimiento = fields.Char(
        string='ID movimiento BankInPlay', required=True, index=True)
    statement_line_id = fields.Many2one(
        'account.bank.statement.line', string='Línea de extracto')
    currency_id = fields.Many2one(
        'res.currency', string='Divisa',
        default=lambda self: self.env.company.currency_id)
    fecha = fields.Date(string='Fecha')
    descripcion = fields.Char(string='Descripción')
    state = fields.Selection(
        INBOX_STATES, string='Estado', default='waiting',
        required=True, index=True, copy=False)
    attempts = fields.Integer(string='Intentos', default=0, copy=False)
    last_attempt = fields.Datetime(string='Último intento', copy=False)
    last_error = fields.Text(string='Último error', copy=False)
    log_id = fields.Many2one(
        'bankinplay.log', string='Log origen', ondelete='set null')

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------
    @api.model
    def _get_param_int(self, key, default):
        val = self.env['ir.config_parameter'].sudo().get_param(key, default)
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # Resolución de línea de extracto (§5.1)
    # ------------------------------------------------------------------
    def _find_statement_line(self):
        """Resuelve la línea de extracto por unique_import_id (sufijo id_movimiento)."""
        self.ensure_one()
        if not self.id_movimiento:
            return self.env['account.bank.statement.line']
        return self.env['account.bank.statement.line'].search([
            ('company_id', '=', self.company_id.id),
            ('unique_import_id', 'like', self.id_movimiento),
        ], limit=1)

    def _is_ready(self):
        """Readiness específico del flujo. Por defecto, con línea de extracto basta."""
        self.ensure_one()
        return bool(self.statement_line_id)

    def _recompute_state(self):
        """Recalcula waiting/ready. No toca estados terminales."""
        self.ensure_one()
        if self.state not in ('waiting', 'ready'):
            return
        if not self.statement_line_id:
            line = self._find_statement_line()
            if line:
                self.statement_line_id = line.id
        self.state = 'ready' if (self.statement_line_id and self._is_ready()) else 'waiting'

    # ------------------------------------------------------------------
    # Procesado por lotes (cron) (§8)
    # ------------------------------------------------------------------
    @api.model
    def _process_pending(self):
        """Punto de entrada del cron. Re-evalúa waiting y procesa un lote."""
        # 1) re-evaluar waiting (solo lectura, sin gastar cupo)
        self._reevaluate_waiting()
        # 2) coger lote de ready/error (con backoff en error)
        batch = self._get_param_int('bankinplay.conciliation_batch_size', DEFAULT_BATCH)
        retry_minutes = self._get_param_int('bankinplay.error_retry_minutes', DEFAULT_RETRY_MINUTES)
        cutoff = fields.Datetime.now() - timedelta(minutes=retry_minutes)
        domain = [
            '|', ('state', '=', 'ready'),
            '&', ('state', '=', 'error'),
            '|', ('last_attempt', '=', False), ('last_attempt', '<', cutoff),
        ]
        records = self.search(
            domain, order='state desc, last_attempt asc, id asc', limit=batch)
        for rec in records:
            try:
                with self.env.cr.savepoint():
                    rec._process_one()
                self.env.cr.commit()
            except Exception as e:  # noqa: BLE001 - aislamos por registro
                _logger.exception(
                    "BankInPlay inbox: error procesando %s %s",
                    self._name, rec.id_movimiento)
                rec._mark_error(str(e))
                self.env.cr.commit()

    @api.model
    def _reevaluate_waiting(self):
        """Resuelve línea y readiness de los waiting; promociona a ready."""
        for rec in self.search([('state', '=', 'waiting')]):
            try:
                rec._recompute_state()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "BankInPlay inbox: error re-evaluando %s %s",
                    self._name, rec.id_movimiento)

    def _process_one(self):
        """Contabiliza un movimiento. A implementar por cada flujo."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Transiciones de estado + histórico de intentos
    # ------------------------------------------------------------------
    def _set_state(self, state, result, message=''):
        self.ensure_one()
        vals = {'state': state, 'last_attempt': fields.Datetime.now()}
        if state == 'error':
            vals['attempts'] = self.attempts + 1
            vals['last_error'] = message
        elif state == 'done':
            vals['attempts'] = self.attempts + 1
            vals['last_error'] = False
        self.write(vals)
        self._create_attempt(result, message)

    def _mark_done(self, message=''):
        self._set_state('done', 'success', message)

    def _mark_error(self, message):
        self._set_state('error', 'error', message)

    def _create_attempt(self, result, message):
        """Crea el registro de histórico. A implementar por cada flujo."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Reproceso manual (botón)
    # ------------------------------------------------------------------
    def action_reprocess(self):
        """Fuerza el reproceso: error/superseded/conflict -> ready y procesa."""
        reprocessable = self.filtered(
            lambda r: r.state in ('error', 'superseded', 'conflict', 'ready'))
        reprocessable.write({'state': 'ready', 'last_attempt': False})
        for rec in reprocessable:
            try:
                with self.env.cr.savepoint():
                    rec._process_one()
            except Exception as e:  # noqa: BLE001
                rec._mark_error(str(e))
        return True
