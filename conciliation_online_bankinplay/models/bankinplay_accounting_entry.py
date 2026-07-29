# 2026 Alquemy
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Inbox de asientos contables.

Un movimiento -> un asiento completo y cuadrado (banco + contrapartidas).
Contabiliza sin conciliar. Coordina con terceros: si la contrapartida es de
cliente/proveedor, cede a terceros (con gracia + fallback). Ver SPEC §4.4/4.5, §9.4.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

DEFAULT_GRACE_DAYS = 5


class BankinplayAccountingEntry(models.Model):
    _name = 'bankinplay.accounting.entry'
    _inherit = 'bankinplay.inbox.mixin'
    _description = 'BankInPlay - Asiento contable'

    cuenta_bancaria = fields.Char(string='Cuenta bancaria')
    banco = fields.Char(string='Banco (BIC)')
    no_asiento = fields.Integer(string='Nº asiento (lote)')
    divisa = fields.Char(string='Divisa BankInPlay')
    pdf_bancario = fields.Char(string='PDF bancario')
    hold_until = fields.Datetime(string='En gracia hasta')
    line_ids = fields.One2many(
        'bankinplay.accounting.entry.line', 'entry_id', string='Apuntes')
    attempt_ids = fields.One2many(
        'bankinplay.accounting.entry.attempt', 'entry_id', string='Intentos')

    _sql_constraints = [
        ('uniq_company_movimiento',
         'unique(company_id, id_movimiento)',
         'Ya existe un asiento con ese id de movimiento para la compañía.'),
    ]

    def _create_attempt(self, result, message):
        self.env['bankinplay.accounting.entry.attempt'].create({
            'entry_id': self.id,
            'result': result,
            'message': message or '',
            'log_id': self.log_id.id if self.log_id else False,
        })

    # ------------------------------------------------------------------
    # Clasificación cliente/proveedor (§9.4)
    # ------------------------------------------------------------------
    def _is_tercero_type(self):
        """True si alguna contrapartida (no banco) es de cliente/proveedor."""
        self.ensure_one()
        excludes = (self.env['ir.config_parameter'].sudo().get_param(
            'bankinplay.asiento_hold_account_excludes') or '')
        excludes = [c.strip() for c in excludes.split(',') if c.strip()]
        bank_code = self.statement_line_id.journal_id.default_account_id.code \
            if self.statement_line_id else None
        for line in self.line_ids:
            if bank_code and line.cuenta_contable == bank_code:
                continue
            if line.cuenta_contable in excludes:
                continue
            account = self.env['account.account'].search([
                ('code', '=', line.cuenta_contable),
                ('company_id', '=', self.company_id.id),
            ], limit=1)
            if account and account.internal_type in ('receivable', 'payable'):
                return True
        return False

    def _terceros_movement(self):
        self.ensure_one()
        return self.env['bankinplay.conciliation.movement'].search([
            ('company_id', '=', self.company_id.id),
            ('id_movimiento', '=', self.id_movimiento),
        ], limit=1)

    # ------------------------------------------------------------------
    # Procesado (§9.4 coordinación)
    # ------------------------------------------------------------------
    def _process_one(self):
        self.ensure_one()
        statement_line = self.statement_line_id or self._find_statement_line()
        if not statement_line:
            self._set_state('waiting', 'waiting', 'Sin línea de extracto')
            return
        if statement_line != self.statement_line_id:
            self.statement_line_id = statement_line.id

        # Coordinación: si es de cliente/proveedor, terceros tiene precedencia.
        if self._is_tercero_type():
            terceros = self._terceros_movement()
            if terceros and terceros.state == 'done':
                self._set_state('superseded', 'superseded',
                                'Contabilizado por terceros')
                return
            if terceros and terceros.state in ('waiting', 'ready', 'error'):
                self._set_state('superseded', 'superseded',
                                'En manos de terceros')
                return
            # No hay terceros todavía: esperar la gracia.
            if self.hold_until and fields.Datetime.now() < self.hold_until:
                self._set_state('waiting', 'waiting',
                                'En gracia esperando documentos de terceros')
                return
            # Gracia expirada -> fallback genérico (se contabiliza igual, marcado).

        if statement_line.is_reconciled:
            self._set_state('superseded', 'superseded',
                            'La línea de extracto ya estaba conciliada')
            return

        new_aml = self._build_apuntes()
        if not new_aml:
            self._mark_error('Sin apuntes válidos para contabilizar')
            return
        statement_line.process_reconciliation_oca([], [], new_aml)
        statement_line.write({'bankinplay_conciliation': True})
        fallback = self._is_tercero_type()
        self._mark_done(
            _('Contabilizado en genérico por falta de documentos') if fallback else '')

    def _build_apuntes(self):
        """new_aml_dicts a partir de los apuntes, saltando la cuenta de banco."""
        self.ensure_one()
        bank_code = self.statement_line_id.journal_id.default_account_id.code
        new_aml = []
        for line in self.line_ids:
            if line.cuenta_contable == bank_code:
                continue
            account = self.env['account.account'].search([
                ('code', '=', line.cuenta_contable),
                ('company_id', '=', self.company_id.id),
            ], limit=1)
            if not account:
                line.write({
                    'state': 'invalid',
                    'error_message': _('Cuenta %s no encontrada') % line.cuenta_contable,
                })
                raise UserError(_(
                    "Cuenta contable %s no encontrada para el movimiento %s")
                    % (line.cuenta_contable, self.id_movimiento))
            analytic = line._resolve_analytic()
            debit = line.importe if line.debe_haber == 'D' else 0.0
            credit = line.importe if line.debe_haber == 'H' else 0.0
            new_aml.append({
                'name': self.descripcion or line.cuenta_contable,
                'debit': debit,
                'credit': credit,
                'account_id': account.id,
                'analytic_account_id': analytic.id if analytic else False,
                'partner_id': self.statement_line_id.partner_id.id
                if self.statement_line_id.partner_id else False,
            })
            line.state = 'done'
        return new_aml

    # ------------------------------------------------------------------
    # Upsert desde el callback
    # ------------------------------------------------------------------
    @api.model
    def _upsert_from_payload(self, data, company, log_entry=False):
        results = (data or {}).get('results') or {}
        asientos = results.get('asientos') or []
        grace_days = self._get_param_int('bankinplay.asiento_grace_days', DEFAULT_GRACE_DAYS)
        hold_until = fields.Datetime.now() + timedelta(days=grace_days)
        entries = self.browse()
        for asiento in asientos:
            id_mov = str(asiento.get('movimiento_id') or '')
            if not id_mov or id_mov == 'None':
                continue
            entry = self.search([
                ('company_id', '=', company.id),
                ('id_movimiento', '=', id_mov),
            ], limit=1)
            head_vals = {
                'company_id': company.id,
                'id_movimiento': id_mov,
                'cuenta_bancaria': asiento.get('cuenta_bancaria'),
                'banco': asiento.get('banco'),
                'no_asiento': asiento.get('no_asiento') or 0,
                'divisa': asiento.get('divisa'),
                'descripcion': asiento.get('descripcion'),
                'pdf_bancario': asiento.get('pdfBancario'),
                'log_id': log_entry.id if log_entry else False,
            }
            if entry:
                if entry.state != 'done':
                    entry.write(head_vals)
            else:
                head_vals['hold_until'] = hold_until
                entry = self.create(head_vals)
            entry._upsert_apuntes(asiento.get('apuntes') or [], log_entry)
            entry._recompute_state()
            entries |= entry
        return entries

    def _upsert_apuntes(self, apuntes, log_entry=False):
        self.ensure_one()
        Line = self.env['bankinplay.accounting.entry.line']
        for ap in apuntes:
            codigo_analitico = False
            for analitica in (ap.get('analitica') or []):
                for desglose in (analitica.get('desglose') or []):
                    codigo_analitico = desglose.get('codigo_analitico')
            vals = {
                'entry_id': self.id,
                'no_apunte': ap.get('no_apunte') or 0,
                'cuenta_contable': str(ap.get('cuenta_contable') or ''),
                'debe_haber': ap.get('debe_haber'),
                'importe': ap.get('importe') or 0.0,
                'codigo_analitico': codigo_analitico,
                'log_id': log_entry.id if log_entry else False,
            }
            existing = Line.search([
                ('entry_id', '=', self.id),
                ('no_apunte', '=', vals['no_apunte']),
                ('cuenta_contable', '=', vals['cuenta_contable']),
            ], limit=1)
            if existing:
                if existing.state != 'done':
                    existing.write(vals)
            else:
                Line.create(vals)


class BankinplayAccountingEntryLine(models.Model):
    _name = 'bankinplay.accounting.entry.line'
    _description = 'BankInPlay - Apunte de asiento contable'
    _order = 'no_apunte'

    entry_id = fields.Many2one(
        'bankinplay.accounting.entry', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(related='entry_id.company_id', store=True)
    currency_id = fields.Many2one(related='entry_id.currency_id')
    no_apunte = fields.Integer(string='Nº apunte')
    cuenta_contable = fields.Char(string='Cuenta contable')
    debe_haber = fields.Selection([('D', 'Debe'), ('H', 'Haber')])
    importe = fields.Monetary(currency_field='currency_id')
    account_id = fields.Many2one('account.account', string='Cuenta')
    codigo_analitico = fields.Char(string='Código analítico')
    analytic_account_id = fields.Many2one(
        'account.analytic.account', string='Cuenta analítica')
    state = fields.Selection(
        [('pending', 'Pendiente'), ('invalid', 'No válido'),
         ('done', 'Contabilizado')], default='pending')
    error_message = fields.Text()
    log_id = fields.Many2one('bankinplay.log', ondelete='set null')

    def _resolve_analytic(self):
        self.ensure_one()
        if not self.codigo_analitico:
            return self.env['account.analytic.account']
        Analytic = self.env['account.analytic.account']
        account = Analytic.search([
            ('code', '=', self.codigo_analitico),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not account:
            account = Analytic.search([
                ('name', 'ilike', self.codigo_analitico),
                ('company_id', '=', self.company_id.id),
            ], limit=1)
        if account:
            self.analytic_account_id = account.id
        return account


class BankinplayAccountingEntryAttempt(models.Model):
    _name = 'bankinplay.accounting.entry.attempt'
    _description = 'BankInPlay - Intento de asiento contable'
    _order = 'date desc'

    entry_id = fields.Many2one(
        'bankinplay.accounting.entry', required=True,
        ondelete='cascade', index=True)
    date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    result = fields.Selection(
        [('success', 'OK'), ('error', 'Error'), ('skipped', 'Omitido'),
         ('waiting', 'En espera'), ('superseded', 'Sustituido'),
         ('conflict', 'Conflicto')])
    message = fields.Text()
    line_id = fields.Many2one(
        'bankinplay.accounting.entry.line', ondelete='set null')
    log_id = fields.Many2one('bankinplay.log', ondelete='set null')
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
