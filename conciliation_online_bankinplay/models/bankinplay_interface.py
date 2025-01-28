# 2024 Alquemy - José Antonio Fernández Valls <jafernandez@alquemy.es>
# 2024 Alquemy - Javier de las Heras Gómez <jheras@alquemy.es>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

import base64
import json
import logging
import time

import requests
from dateutil.relativedelta import relativedelta

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.base.models.res_bank import sanitize_account_number

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import base64
from datetime import datetime

_logger = logging.getLogger(__name__)

BANKINPLAY_ENDPOINT = "https://app.bankinplay.com/intradia-core"
BANKINPLAY_ENDPOINT_V1 = BANKINPLAY_ENDPOINT + "/api/v1"
BANKINPLAY_ENDPOINT_V2 = BANKINPLAY_ENDPOINT + "/api/v2"


class BankinPlayInterface(models.AbstractModel):
    _inherit = "bankinplay.interface"
    _description = "Interface to all interactions with Bankinplay API"

    def _get_companies(self, access_data):
        """Get companies from bankingplay."""
        url = BANKINPLAY_ENDPOINT_V2 + "/entidad/sociedades"
        
        data = self._get_request(access_data, url, {})
        return data

    #PLANES CONTABLES

    def _get_account_plans(self, access_data):
        """Get companies from bankingplay."""
        url = BANKINPLAY_ENDPOINT_V1 + "/planes-contables"
        
        data = self._get_request(access_data, url, {})
        return data
    
    def _export_account_plan(self, access_data, start_date):
        
        url = BANKINPLAY_ENDPOINT_V1 + "/planContableApi/plan_contable"
        company_id = self.env.company

        date = start_date.strftime("%d/%m/%Y")
        account = self.env['account.account'].search([], limit=1)
        
        account_plan = self.env['account.account'].search([])
        code_size = len(account_plan[0].code)
        accounts = []
        for account in account_plan:
            cuenta = {
                "nombre": account.code,
                "codigo": account.code,
                "descripcion": account.name
            }
            accounts.append(cuenta)

        params = {
            "agrupaciones": [],
            "planes": [{
                "codigo": "PC" + company_id.vat.replace('ES', ''),
                "nombre": "Plan contable - " + company_id.name,
                "fechaInicio": date,
                "pais": company_id.country_id.code,
                "numeroDigitosCuentasContables": str(code_size),
                "gestionarCCTerceros": "S" if company_id.bankinplay_manage_third_accounts else "N",
                "cuentas": accounts
                # "cuentas": []
            }]
        }

        data = self._get_pending_async_request(access_data, self._post_request(access_data, url, {}, json.dumps(params)))
        if data:
            if data.get('errors', False):
                raise UserError("BANKINPLAY: \n" + data.get('errors')[0]['description'])
            
            account_plan = False
            account_plans = self._get_account_plans(access_data)
            for plan in account_plans:
                if plan['codigo'] == "PC" + company_id.vat.replace('ES', ''):
                    account_plan = plan
                
            if not account_plan:
                raise UserError('No se ha podido generar el plan contable')
            
            self._set_company_account_plan(access_data, account_plan)

        return data
    
    def _get_account_plans(self, access_data):
        """Get account plans from bankingplay."""
        url = BANKINPLAY_ENDPOINT_V1 + "/planes-contables"
        
        data = self._get_request(access_data, url, {})
        return data
    
    def _set_company_account_plan(self, access_data, account_plan):
        url = BANKINPLAY_ENDPOINT_V2 + "/entidad/sociedades/" + self.env.company.bankinplay_company_id
        
        params = {
            "planContableId": account_plan['id']
        }
        
        data = self._put_request(access_data, url, params)
        statusCode = data.get('statusCode', 400)
        if not statusCode == 200:
            raise UserError('No se ha podido enlazar el plan contable')

    
    # CONTACTOS

    def _export_contacts(self, access_data, domain=[]):
        url = BANKINPLAY_ENDPOINT_V1 + "/tercero-cliente"
        company_id = self.env.company
        
        #domain.extend(['|', ('bankinplay_sent', '=', False), ('bankinplay_update', '=', True)])

        contact_ids = self.env['res.partner'].search(domain)
        contacts = []
        for c in contact_ids:
            print(c.name)
            configuracion_contable = []
            if c.is_customer:
                configuracion_contable.append({
                    "sociedad_cif": company_id.vat.replace('ES', ''),
                    "tipo_tercero": "C",
                    "estado": "A",
                    "cuenta_contable": c.property_account_receivable_id.code,
                    "codigo_tercero": c.id
                })

            if c.is_supplier:
                configuracion_contable.append({
                    "sociedad_cif": company_id.vat.replace('ES', ''),
                    "tipo_tercero": "P",
                    "estado": "A",
                    "cuenta_contable": c.property_account_payable_id.code,
                    "codigo_tercero": c.id
                })
            
            if c.employee:
                configuracion_contable.append({
                    "sociedad_cif": company_id.vat.replace('ES', ''),
                    "tipo_tercero": "E",
                    "estado": "A",
                    "cuenta_contable": c.property_account_receivable_id.code,
                    "codigo_tercero": c.id
                })

            contact = {
                "nif": c.vat,
                "nombre": c.name,
                "alias": c.comercial if c.comercial else '',
                "pais": c.country_id.code if c.country_id else '',
                "domicilio": c.street if c.street else '',
                "provincia": c.state_id.name if c.state_id else '',
                "localidad": c.city if c.city else '',
                "codigo_postal": c.zip if c.zip else '',
                "administracion_email": c.email or '',
                "telefono": c.phone if c.phone else c.mobile if c.mobile else '',
                "configuracion_contable": configuracion_contable
            }
            
            contacts.append(contact)
        
        params = {
            "terceros": contacts,
        }
        
        data = self._get_pending_async_request(access_data, self._post_request(access_data, url, {}, json.dumps(params)))
        
        for tercero in data.get('terceros', []):
            if tercero.get('estado', 'Incorrecto') == 'correcto':
                partner = self.env['res.partner'].search([]).filtered(lambda x: tercero.get('nif', False) in x.vat if x.vat else False)
                if partner:
                    partner.write({
                        "bankinplay_sent": True
                    })

        return data
    
    # DOCUMENTOS TERCEROS

    def _export_documents(self, access_data, start_date, journal_ids):    
        url = BANKINPLAY_ENDPOINT_V1 + "/documentos-terceros"
        company_id = self.env.company
        
        document_ids = self.env['account.move'].search([('invoice_date', '>', start_date), ('state', '=', 'posted'), ('bankinplay_sent', '=', False), ('journal_id', 'in', journal_ids)])
        
        for document in document_ids:
            name_job = "[BANKINPLAY] - FACTURA " + document.name
            document.with_delay(priority=20, max_retries=5, description=name_job).bankinplay_send_move()
       

        return document_ids
        
    def _export_document(self, access_data, move_id):

        account_move_id = self.env['account.move'].search([('id', '=', move_id)], limit=1)

        self._export_contact(access_data, self.env['res.partner'].search([('id', '=', account_move_id.partner_id.id)]).id)

        url = BANKINPLAY_ENDPOINT_V1 + "/documentos-terceros"
        company_id = self.env.company
        
        document_ids = self.env['account.move'].search([('id', '=', move_id)], limit=1)
        documents = []
        
        
        for d in document_ids:
            estado_pago = "COBRADO" if d.payment_state == 'paid' else "PDTE",
            tipo_documento_codigo = "FV"
            if d.move_type == 'out_refund':
                tipo_documento_codigo = "AC"
                estado_pago = "COBRADO" if d.payment_state == 'paid' else "PDTE"
            elif d.move_type == 'in_invoice':
                tipo_documento_codigo = "FC"
                estado_pago = "PAGADO" if d.payment_state == 'paid' else "PDTE",
            elif d.move_type == 'in_refund':
                tipo_documento_codigo = "AP"
                estado_pago = "COBRADO" if d.payment_state == 'paid' else "PDTE",

            document = {
                "id_documento_erp": str(d.id),
                "sociedad_cif": company_id.vat.replace('ES', ''),
                "tipo_documento_codigo": tipo_documento_codigo,
                "fecha_emision": d.invoice_date.strftime("%d/%m/%Y"),
                "fecha_vencimiento": d.invoice_date_due.strftime("%d/%m/%Y"),
                "fecha_emision_remesa": None,
                "fecha_cobro": None,
                "no_documento": d.name,
                "no_remesa": None,
                "importe_total": d.amount_total_signed,
                "importe_pendiente": d.amount_residual_signed,
                "importe_impuestos": d.amount_tax_signed,
                "divisa": "EUR",
                "estado_codigo": estado_pago,
                "nif_tercero": d.partner_id.vat.replace('ES', ''),
                "referencias": [d.ref] if d.ref else []
            }
            documents.append(document)
        

        params = {
            "documentos": documents,
        }
        
        data = self._get_pending_async_request(access_data, self._post_request(access_data, url, {}, json.dumps(params)))

        for tercero in data.get('documentos', []):
            if tercero.get('estado', 'Incorrecto') == 'correcto':
                move_line = self.env['account.move.line'].search([('id', '=',tercero.get('id_documento_erp'))], limit=1)
                if move_line:
                    move_line.write({
                        "bankinplay_sent": True,
                    })
        
        return data
    
    def _cancel_document(self, access_data, move_id):

        account_move_id = self.env['account.move'].search([('id', '=', move_id)], limit=1)

        url_get_document = BANKINPLAY_ENDPOINT_V1 + "/sociedades/" + account_move_id.company_id.vat.replace('ES', '') + "/documentos-terceros/" + str(account_move_id.id)

        params = {
        
        }

        document_data = self._get_request(access_data, url_get_document, params)

        if isinstance(document_data, int):    
            
            url = BANKINPLAY_ENDPOINT_V1 + "/documentos-terceros/anular/" + str(document_data)
        
            data = self._delete_request(access_data, url, {}, json.dumps(params))
            responseId = data.get('responseId', '')
            if not responseId:
                raise UserError('No se ha podido anular el documento en bankinplay')
            
            if data.get('statusCode', 400) != 200:
                raise UserError('No se ha podido anular el documento en bankinplay')

        
        else:
            if document_data.get('errors', False):
                raise UserError("BANKINPLAY: \n" + document_data.get('errors')[0]['description'])
        
    


    def _export_document_moves(self, access_data, start_date, journal_ids):
        url = BANKINPLAY_ENDPOINT_V1 + "/documentos-terceros"
        company_id = self.env.company

        
        document_ids = self.env['account.move.line'].search([('date', '>=', start_date), ("partner_id", '!=', False), ('parent_state', '=', 'posted'), ('bankinplay_sent', '=', True), ('journal_id', 'in', journal_ids)]).filtered(lambda x: x.partner_id.vat and x.account_id.user_type_id.type in ['payable', 'receivable'])
        
        # partner_ids = document_ids.mapped('partner_id').filtered(lambda x: not x.bankinplay_sent or x.bankinplay_update)
        # partner_ids = document_ids.mapped('partner_id')
        # if partner_ids:
        #     self._export_contacts(access_data, [('id', 'in', partner_ids.ids)])
        
        documents = []
        for d in document_ids:
            tipo_documento_codigo = 'FC'
            if d.move_id.move_type == 'out_invoice':
                tipo_documento_codigo = "FV"
            if d.move_id.move_type == 'out_refund':
                tipo_documento_codigo = "AC"
            elif d.move_id.move_type == 'in_invoice':
                tipo_documento_codigo = "FC"
            elif d.move_id.move_type == 'in_refund':
                tipo_documento_codigo = "AP"


            
            amount_residual = abs(d.amount_residual)
            payment_order_id = False
            if d.payment_line_ids and d.payment_line_ids[:1].payment_ids and d.payment_line_ids[:1].payment_ids[:1].payment_order_id:
                amount_residual = abs(d.amount_currency)
                payment_order_id = d.payment_line_ids[:1].payment_ids[:1].payment_order_id


            document_type = 'PDTE'
            if payment_order_id:
                document_type = 'REMESADO'
            elif amount_residual == 0:
                if tipo_documento_codigo in ['AP', 'FV']:
                    document_type = 'COBRADO'
                else:
                    document_type = 'PAGADO'

            document = {
                "id_documento_erp": str(d.id),
                "sociedad_cif": company_id.vat.replace('ES', ''),
                "tipo_documento_codigo": tipo_documento_codigo,
                "fecha_emision": d.move_id.invoice_date.strftime("%d/%m/%Y") if d.move_id.invoice_date else d.date.strftime("%d/%m/%Y"),
                "fecha_vencimiento": d.date_maturity.strftime("%d/%m/%Y") if d.date_maturity else d.date.strftime("%d/%m/%Y"),
                "fecha_emision_remesa": payment_order_id.date_uploaded.strftime("%d/%m/%Y") if payment_order_id else None,
                "fecha_cobro": None,
                "no_documento": d.move_id.name,
                "no_remesa": payment_order_id.name if payment_order_id else None,
                "importe_total": abs(d.amount_currency),
                "importe_pendiente": amount_residual,
                "divisa": d.currency_id.name,
                "nif_tercero": d.partner_id.vat if d.partner_id.vat else '',
                "razon_social_tercero": d.partner_id.name,
                "referencias": [d.ref] if d.ref else [],
                "estado_codigo": document_type
            }

            documents.append(document)
        

        params = {
            "documentos": documents,
        }
        
        data = self._get_pending_async_request(access_data, self._post_request(access_data, url, {}, json.dumps(params)))
        
        for tercero in data.get('documentos', []):
            if tercero.get('estado', 'Incorrecto') == 'correcto':
                move_line = self.env['account.move.line'].search([('id', '=',tercero.get('id_documento_erp'))], limit=1)
                if move_line:
                    move_line.write({
                        "bankinplay_sent": True,
                    })


        return data
            
    
    
    # PLAN ANALÍTICO
    
    def _create_analytic_plan(self, access_data):
        
        url = BANKINPLAY_ENDPOINT_V1 + "/planes-analiticos"
        company_id = self.env.company

        params = {
            "nombre": "PA_" + company_id.vat.replace('ES', '')
        }
        
        data = self._post_request(access_data, url, {}, json.dumps(params))
        analytic_plan_id = data.get('plan_analitico_id', '')
        if not analytic_plan_id:
            
            raise UserError('No se han podido dar de alta el plan analitico')
        
        return analytic_plan_id

    def _create_analytic_line(self, access_data, analytic_plan_id):
        
        url = BANKINPLAY_ENDPOINT_V1 + "/linea-analitica/planes/" + analytic_plan_id + "/lineas"
        company_id = self.env.company

        params = {
            "nombre": "PL_" + company_id.vat.replace('ES', ''),
            "codigoContabilidad": "Linea analitica - " + company_id.name
        }
        
        data = self._post_request(access_data, url, {}, json.dumps(params))
        analytic_line_id = data.get('linea_analitica_id', '')
        if not analytic_line_id:
            raise UserError('No se han podido dar de alta la linea analitica')
        
        return analytic_line_id
    
    def _export_analytic_plan(self, access_data, analytic_line_id):

        url = BANKINPLAY_ENDPOINT_V1 + "/codigo-analitico/lineas/" + analytic_line_id + "/codigo"
        company_id = self.env.company
        
        account_analytic_ids = self.env['account.analytic.account'].search([])
        analytics = []
        for a in account_analytic_ids:
            analytic = {
                "codigo": a.name,
            }
            analytics.append(analytic)

        params = {
            "codigos": analytics
        }

        for a in account_analytic_ids:
            params = {
                "codigo": a.name,
            }
            data = self._post_request(access_data, url, {}, json.dumps(params))
            print(data)

        return data


    # CONCILIACIÓN
    def _import_conciliate_documents(self, access_data):    
        url = BANKINPLAY_ENDPOINT_V1 + "/conciliacion-terceros"
        company_id = self.env.company

        params = {
            "sociedades": [company_id.bankinplay_company_id],
            "deshabilitar_callback": True,
            "exportados": True
        }

        if company_id.bankinplay_last_syncdate:
            params['fecha_conciliacion_desde'] = (company_id.bankinplay_last_syncdate - relativedelta(days=1)).strftime("%d/%m/%Y")
        else:
            params['fecha_conciliacion_desde'] = company_id.bankinplay_start_date.strftime("%d/%m/%Y")
            
        
        data = self._get_pending_async_request(access_data, self._post_request(access_data, url, {}, json.dumps(params)))

        company_id.bankinplay_last_syncdate = datetime.today()
        
        return data

    def _import_account_moves(self, access_data):    
        url = BANKINPLAY_ENDPOINT_V1 + "/asientoContableApi/asiento_contable"
        company_id = self.env.company
        params = {
            "fechaHasta" : (datetime.today() + relativedelta(days=1)).strftime("%d/%m/%Y"),
            "sociedades": [company_id.bankinplay_company_id],
            "deshabilitar_callback": True
        }
        
        data = self._get_pending_async_request(access_data, self._post_request(access_data, url, {}, json.dumps(params)))
        
        return data

    def _export_account_move_lines(self, access_data):
        url = BANKINPLAY_ENDPOINT_V1 + "/apunteContableApi/apunte_contable"
        company_id = self.env.company

        statement_line_ids = self.env['account.bank.statement.line'].search([('is_reconciled', '=', True)]).filtered(lambda x: x.unique_import_id and x.date >= company_id.bankinplay_start_date and not x.bankinplay_sent)
        account_move_lines = []
        for st in statement_line_ids:
            movimiento_id = st.unique_import_id.split('-')[-1]
            account_code = st.move_id.journal_id.default_account_id.code

            for line in st.move_id.line_ids.filtered(lambda x: x.account_id.code == account_code):
                account_move_line = {
                    "movimiento_id": movimiento_id,
                    "cuenta_contable": account_code,
                    "sociedad_cif": company_id.vat.replace('ES', ''),
                    "fecha_contable": st.move_id.date.strftime("%d/%m/%Y"),
                    "importe": line.amount_currency,
                    "debe_haber": "D" if line.amount_currency > 0 else "H",
                    "descripcion": line.name,
                    "asiento_id": st.move_id.id,
                    "apunte_id": line.id
                }
           
                account_move_lines.append(account_move_line)

        params = {
            "apuntes": account_move_lines
        }

        data = self._post_request(access_data, url, {}, json.dumps(params))

        for st in statement_line_ids:
            st.bankinplay_sent = True

        return data