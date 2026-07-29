# 2026 Alquemy
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
"""Inbox de conciliación de terceros (facturas + anticipos).

Un movimiento bancario -> N documentos (facturas) + N anticipos (retenciones).
Se concilia por movimiento cuando los documentos cubren el importe del banco.
Ver SPEC_inbox_bankinplay.md §4.2/4.3, §9.1-9.3.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

# odoo16: tipos de cuenta de cliente/proveedor (antes user_type_id / internal_type)
RECEIVABLE_PAYABLE = ('asset_receivable', 'liability_payable')


class BankinplayConciliationMovement(models.Model):
    _name = 'bankinplay.conciliation.movement'
    _inherit = 'bankinplay.inbox.mixin'
    _description = 'BankInPlay - Movimiento de conciliación de terceros'

    cuenta_bancaria = fields.Char(string='Cuenta bancaria')
    importe_movimiento = fields.Monetary(
        string='Importe movimiento', currency_field='currency_id')
    importe_documentos = fields.Monetary(
        string='Importe documentos', currency_field='currency_id',
        compute='_compute_importe_documentos', store=True)
    document_ids = fields.One2many(
        'bankinplay.conciliation.document', 'movement_id', string='Documentos')
    attempt_ids = fields.One2many(
        'bankinplay.conciliation.attempt', 'movement_id', string='Intentos')

    _sql_constraints = [
        ('uniq_company_movimiento',
         'unique(company_id, id_movimiento)',
         'Ya existe un movimiento de conciliación con ese id para la compañía.'),
    ]

    @api.depends('document_ids.importe_conciliado', 'document_ids.line_type')
    def _compute_importe_documentos(self):
        for mv in self:
            mv.importe_documentos = sum(
                d.importe_conciliado for d in mv.document_ids
                if d.line_type == 'documento')

    # ------------------------------------------------------------------
    # Readiness (§9.1): los documentos cubren el importe del banco
    # ------------------------------------------------------------------
    def _is_ready(self):
        self.ensure_one()
        if not self.statement_line_id:
            return False
        rounding = self.statement_line_id.currency_id.rounding or 0.01
        return float_compare(
            abs(self.importe_documentos), abs(self.statement_line_id.amount),
            precision_rounding=rounding) == 0

    def _create_attempt(self, result, message):
        self.env['bankinplay.conciliation.attempt'].create({
            'movement_id': self.id,
            'result': result,
            'message': message or '',
            'log_id': self.log_id.id if self.log_id else False,
        })

    # ------------------------------------------------------------------
    # Cuenta fija de anticipos/retenciones (§9.3)
    # ------------------------------------------------------------------
    def _anticipo_account(self):
        self.ensure_one()
        code = self.env['ir.config_parameter'].sudo().get_param(
            'bankinplay.anticipo_account_code')
        if not code:
            return self.env['account.account']
        return self.env['account.account'].search([
            ('code', '=', code), ('company_id', '=', self.company_id.id),
        ], limit=1)

    # ------------------------------------------------------------------
    # Procesado (§9.2 parciales, §9.3 anticipos, §9.4 coordinación)
    # ------------------------------------------------------------------
    def _process_one(self):
        self.ensure_one()
        statement_line = self.statement_line_id or self._find_statement_line()
        if not statement_line:
            self._set_state('waiting', 'waiting', 'Sin línea de extracto')
            return
        if statement_line != self.statement_line_id:
            self.statement_line_id = statement_line.id
        # Idempotencia / coordinación: si la línea ya está conciliada,
        # otro proceso (o un re-envío previo) ya la resolvió.
        if statement_line.is_reconciled:
            self._set_state(
                'superseded', 'superseded',
                'La línea de extracto ya estaba conciliada')
            return
        counterparts, new_aml = self._build_counterparts()
        if not counterparts:
            self._mark_error('Sin documentos válidos para conciliar')
            return
        statement_line._bankinplay_apply_reconciliation(counterparts, new_aml)
        self._mark_done()

    def _build_counterparts(self):
        """Contrapartidas 430/400 (conciliadas) + línea de anticipo (write-off).

        El signo se toma del **lado real del apunte de la factura** (move_line.debit):
        factura (Debe del cliente) -> contrapartida al Haber; rectificativa/abono
        (Haber del cliente) -> contrapartida al Debe. Así una rectificativa se
        contabiliza en el lado correcto y el asiento cuadra (fix del descuadre).
        """
        self.ensure_one()
        anticipo_account = self._anticipo_account()

        documentos = self.document_ids.filtered(lambda d: d.line_type == 'documento')
        anticipos = self.document_ids.filtered(lambda d: d.line_type == 'anticipo')

        counterparts = []
        new_aml = []
        for doc in documentos:
            move_line = doc._resolve_move_line()
            if (not move_line or move_line.parent_state != 'posted'
                    or move_line.account_id.account_type not in RECEIVABLE_PAYABLE):
                doc.write({
                    'state': 'invalid',
                    'error_message': _('Apunte %s no válido (inexistente, no '
                                       'contabilizado o cuenta no de cliente/proveedor)')
                    % (doc.id_documento_erp or ''),
                })
                continue
            doc.move_line_id = move_line.id
            # anticipos ligados por conciliacion_id (retención que cierra factura)
            ant = anticipos.filtered(
                lambda a: a.conciliacion_id and a.conciliacion_id == doc.conciliacion_id)
            ant_total = sum(abs(a.importe) for a in ant)
            if ant_total and not anticipo_account:
                raise UserError(_(
                    "Hay anticipo/retención en el movimiento %s pero no está "
                    "configurada la cuenta 'bankinplay.anticipo_account_code'.")
                    % self.id_movimiento)
            amount = abs(doc.importe_conciliado) + ant_total
            # Signo por el SALDO del apunte (debit-credit), robusto a apuntes con
            # débito/crédito negativo: factura = saldo deudor (>0) -> contrapartida
            # al Haber; rectificativa/abono = saldo acreedor (<0) -> al Debe.
            is_factura = move_line.balance > 0
            counterparts.append({
                'move_line': move_line,
                # nº de factura en la contrapartida 430 (§Fase 0 C)
                'name': move_line.move_id.name or move_line.name,
                'debit': 0.0 if is_factura else amount,
                'credit': amount if is_factura else 0.0,
            })
            doc.state = 'done'
            if ant_total:
                # el anticipo/retención va al lado opuesto de la contrapartida
                new_aml.append({
                    'name': _('Anticipo/retención %s') % (doc.no_documento or ''),
                    'debit': ant_total if is_factura else 0.0,
                    'credit': 0.0 if is_factura else ant_total,
                    'account_id': anticipo_account.id,
                })
                ant.write({'state': 'done'})
        return counterparts, new_aml

    # ------------------------------------------------------------------
    # Upsert desde el callback (§Fase 2)
    # ------------------------------------------------------------------
    @api.model
    def _upsert_from_payload(self, data, company, log_entry=False):
        """Registra/actualiza movimientos y sus documentos/anticipos."""
        sociedades = (data or {}).get('sociedades') or []
        if not sociedades:
            return self.browse()
        documentos = sociedades[0].get('documentos') or []
        anticipos = sociedades[0].get('anticipos') or []

        grouped = {}
        for d in documentos:
            grouped.setdefault(str(d.get('id_movimiento')), {'doc': [], 'ant': []})['doc'].append(d)
        for a in anticipos:
            grouped.setdefault(str(a.get('id_movimiento')), {'doc': [], 'ant': []})['ant'].append(a)

        movements = self.browse()
        for id_mov, groups in grouped.items():
            if not id_mov or id_mov == 'None':
                continue
            movement = self.search([
                ('company_id', '=', company.id),
                ('id_movimiento', '=', id_mov),
            ], limit=1)
            head_vals = {
                'company_id': company.id,
                'id_movimiento': id_mov,
                'log_id': log_entry.id if log_entry else False,
            }
            sample = (groups['doc'] or groups['ant'])[0]
            head_vals['descripcion'] = sample.get('descripcion_movimiento')
            head_vals['cuenta_bancaria'] = sample.get('cuenta_bancaria') \
                or (sample.get('info_cuenta_bancaria') or {}).get('cuenta_completa')
            if movement:
                if movement.state not in ('done',):
                    movement.write(head_vals)
            else:
                movement = self.create(head_vals)
            movement._upsert_lines(groups['doc'], groups['ant'], log_entry)
            movement._recompute_state()
            movements |= movement
        return movements

    def _upsert_lines(self, documentos, anticipos, log_entry=False):
        self.ensure_one()
        Doc = self.env['bankinplay.conciliation.document']
        for d in documentos:
            self._upsert_one_line(Doc, 'documento', d, log_entry)
        for a in anticipos:
            self._upsert_one_line(Doc, 'anticipo', a, log_entry)

    def _upsert_one_line(self, Doc, line_type, payload, log_entry=False):
        self.ensure_one()
        vals = {
            'movement_id': self.id,
            'line_type': line_type,
            'id_documento_erp': str(payload.get('id_documento_erp') or ''),
            'conciliacion_id': str(payload.get('conciliacion_id') or ''),
            'no_documento': payload.get('no_documento'),
            'importe': payload.get('importe') or 0.0,
            'importe_conciliado': payload.get('importe_conciliado') or 0.0,
            'importe_pendiente': payload.get('importe_pendiente') or 0.0,
            'importe_retencion': payload.get('importe_retencion') or 0.0,
            'signo': payload.get('signo_movimiento'),
            'nif_tercero': payload.get('nif_tercero'),
            'razon_social_tercero': payload.get('razon_social_tercero'),
            'codigo_erp_tercero': payload.get('codigo_erp_tercero'),
            'log_id': log_entry.id if log_entry else False,
        }
        existing = Doc.search([
            ('movement_id', '=', self.id),
            ('line_type', '=', line_type),
            ('conciliacion_id', '=', vals['conciliacion_id']),
            ('id_documento_erp', '=', vals['id_documento_erp']),
        ], limit=1)
        if existing:
            if existing.state != 'done':
                existing.write(vals)
        else:
            Doc.create(vals)


class BankinplayConciliationDocument(models.Model):
    _name = 'bankinplay.conciliation.document'
    _description = 'BankInPlay - Documento/anticipo de conciliación'

    movement_id = fields.Many2one(
        'bankinplay.conciliation.movement', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='movement_id.company_id', store=True)
    currency_id = fields.Many2one(related='movement_id.currency_id')
    line_type = fields.Selection(
        [('documento', 'Documento'), ('anticipo', 'Anticipo')],
        string='Tipo', required=True, default='documento')
    id_documento_erp = fields.Char(string='ID apunte Odoo')
    conciliacion_id = fields.Char(string='ID conciliación BankInPlay', index=True)
    no_documento = fields.Char(string='Nº documento')
    importe = fields.Monetary(currency_field='currency_id')
    importe_conciliado = fields.Monetary(currency_field='currency_id')
    importe_pendiente = fields.Monetary(currency_field='currency_id')
    importe_retencion = fields.Monetary(currency_field='currency_id')
    signo = fields.Char()
    nif_tercero = fields.Char()
    razon_social_tercero = fields.Char()
    codigo_erp_tercero = fields.Char()
    move_line_id = fields.Many2one('account.move.line', string='Factura (apunte)')
    state = fields.Selection(
        [('pending', 'Pendiente'), ('valid', 'Válido'),
         ('invalid', 'No válido'), ('done', 'Conciliado')],
        default='pending')
    error_message = fields.Text()
    log_id = fields.Many2one('bankinplay.log', ondelete='set null')

    _sql_constraints = [
        ('uniq_doc',
         'unique(movement_id, line_type, conciliacion_id, id_documento_erp)',
         'Documento duplicado en el movimiento.'),
    ]

    def _resolve_move_line(self):
        self.ensure_one()
        if not self.id_documento_erp or not self.id_documento_erp.isdigit():
            return self.env['account.move.line']
        move_line = self.env['account.move.line'].browse(int(self.id_documento_erp))
        return move_line if move_line.exists() else self.env['account.move.line']


class BankinplayConciliationAttempt(models.Model):
    _name = 'bankinplay.conciliation.attempt'
    _description = 'BankInPlay - Intento de conciliación'
    _order = 'date desc'

    movement_id = fields.Many2one(
        'bankinplay.conciliation.movement', required=True,
        ondelete='cascade', index=True)
    date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    result = fields.Selection(
        [('success', 'OK'), ('error', 'Error'), ('skipped', 'Omitido'),
         ('waiting', 'En espera'), ('superseded', 'Sustituido'),
         ('conflict', 'Conflicto')])
    message = fields.Text()
    document_id = fields.Many2one(
        'bankinplay.conciliation.document', ondelete='set null')
    log_id = fields.Many2one('bankinplay.log', ondelete='set null')
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user)
