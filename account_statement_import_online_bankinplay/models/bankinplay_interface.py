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

_logger = logging.getLogger(__name__)

BANKINPLAY_ENDPOINT = "https://app.bankinplay.com/intradia-core"
BANKINPLAY_ENDPOINT_V1 = BANKINPLAY_ENDPOINT + "/api/v1"
BANKINPLAY_ENDPOINT_V2 = BANKINPLAY_ENDPOINT + "/api/v2"


class BankinPlayInterface(models.AbstractModel):
    _name = "bankinplay.interface"
    _description = "Interface to all interactions with Bankinplay API"

    def _get_companies(self, access_data):
        """Get companies from bankingplay."""
        url = BANKINPLAY_ENDPOINT_V2 + "/entidad/sociedades"

        data = self._get_request(access_data, url, {})
        return data

    def _login(self, username, password):
        """BamkInPlay login returns an access dictionary for further requests."""
        url = BANKINPLAY_ENDPOINT + "/clienteApi/jwt_token"
        if not (username and password):
            raise UserError(_("Please fill login and key."))
        login_params = {
            'user': username,
            'pass': password
        }
        login_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        _logger.info(_("POST request on %s"), url)
        response = requests.post(
            url,
            params=login_params,
            headers=login_headers,
        )
        data = self._get_response_data(response)
        access_token = data.get("access_token", False)
        if not access_token:
            raise UserError(_("BankInPlay : no token"))
        return {
            "access_token": access_token,
            "user": username,
            "pass": password
        }

    def _get_request_headers(self, access_data):
        """Get headers with authorization for further bankinplay requests."""
        return {
            "Accept": "application/json",
            "Authorization": "Bearer %s" % access_data["access_token"],
            "Content-Type": "application/json"
        }

    def _get_pending_async_request(self, access_data, data, params=[]):
        responseId = data.get('responseId', '')
        if not responseId:
            raise UserError('No se han podido traer las transacciones')

        url = BANKINPLAY_ENDPOINT_V1 + "/statement/status/" + responseId
        data = self._get_request(access_data, url, params)

        while data['estado'] != 'procesado' and data['estado'] != 'terminado':
            time.sleep(5)
            data = self._get_request(access_data, url, params)
            estado = data.get('estado', '')
            if estado == 'erroneo':
                raise UserError('Error en la solicitud de transacciones')

        url = (
            BANKINPLAY_ENDPOINT_V1
            + "/respuestaAsincronaApi/recogida?responseId="
            + responseId
        )
        data = self._get_request(access_data, url, params)
        return data

    def _set_access_account(self, access_data, account_number):
        """Set bankinplay account for bank account in access_data."""
        url = BANKINPLAY_ENDPOINT_V2 + "/entidad/cuentaBancaria"
        _logger.info(_("GET request on %s"), url)
        response = requests.get(
            url, params={}, headers=self._get_request_headers(access_data)
        )
        data = self._get_response_data(response, access_data)
        for bankinplay_account in data:
            bankinplay_iban = sanitize_account_number(
                bankinplay_account.get("cuentaCompleta", {})
            )
            if bankinplay_iban == account_number:
                access_data["bankinplay_account"] = bankinplay_account.get(
                    "id")
                return access_data
        # If we get here, we did not find Ponto account for bank account.
        raise UserError(
            _("BankInPlay : wrong configuration, account %s not found in %s")
            % (account_number, data)
        )

    def _set_access_card(self, access_data, account_number):
        """Set bankinplay account for bank card in access_data."""
        url = BANKINPLAY_ENDPOINT_V2 + "/entidad/tarjeta"
        _logger.info(_("GET request on %s"), url)
        response = requests.get(
            url, params={}, headers=self._get_request_headers(access_data)
        )
        data = self._get_response_data(response, access_data)
        for bankinplay_account in data:
            bankinplay_card_number = sanitize_account_number(
                bankinplay_account.get("num_tarjeta", {})
            )
            check_company = False
            if bankinplay_card_number == account_number:
                get_companies = self._get_companies(access_data)
                for company in get_companies:
                    if company['nif'] == bankinplay_account.get("cif_sociedad", ''):
                        check_company = True
                        access_data["bankinplay_company_card"] = company.get(
                            "id")
                        break

                if not check_company:
                    raise UserError(
                        _("BankInPlay : wrong configuration, company %s not found in %s")
                        % (self.vat, get_companies)
                    )

                return access_data
        # If we get here, we did not find Ponto account for bank account.
        raise UserError(
            _("BankInPlay : wrong configuration, account %s not found in %s")
            % (account_number, data)
        )

    def _get_transactions(self, access_data, date_since, date_until, provider_id):
        """Get transactions from bankingplay, using last_identifier as pointer.

        Note that Ponto has the transactions in descending order. The first
        transaction, retrieved by not passing an identifier, is the latest
        present in Ponto. If you read transactions 'after' a certain identifier
        (Ponto id), you will get transactions with an earlier date.
        """
        url = BANKINPLAY_ENDPOINT_V1 + "/statement/lectura_intradia"

        params = {
            "exportados": True,
            "deshabilitar_callback": False,
            "fechaDesdeOperacion": date_since.strftime("%d/%m/%Y"),
            "fechaHastaOperacion": date_until.strftime("%d/%m/%Y"),
            "cuentasBancarias": [access_data.get("bankinplay_account")] if access_data.get("bankinplay_account", False) else []
        }

        data = self._simple_post_request(access_data, url, params)

        event_data = {
            "event": "lectura_intradia",
            "date_since": date_since.strftime("%Y/%m/%d"),
            "date_until": date_until.strftime("%Y/%m/%d"),
            "provider_id": provider_id.id,
            "access_data": access_data
        }

        log_entry = self.env['bankinplay.log'].create({
            'operation_type': 'request',
            'request_data': json.dumps(params),
            'response_data': json.dumps(data),
            'status': 'pending',
            'notes': 'Petición de transacciones enviada a BankInPlay',
            'response_id': data.get('responseId', ''),
            'signature': data.get('signature', ''),
            'event_data': event_data
        })

    def _get_card_transactions(self, access_data, date_since, date_until):
        """Get transactions from bankingplay, using last_identifier as pointer.

        Note that Ponto has the transactions in descending order. The first
        transaction, retrieved by not passing an identifier, is the latest
        present in Ponto. If you read transactions 'after' a certain identifier
        (Ponto id), you will get transactions with an earlier date.
        """
        url = BANKINPLAY_ENDPOINT_V1 + "/movimientoTarjetaApi/lectura_tarjeta"

        params = {
            "exportados": True,
            "deshabilitar_callback": True,
            "fechaDesde": date_since.strftime("%d/%m/%Y"),
            "fechaHasta": date_until.strftime("%d/%m/%Y"),
            "sociedades": [access_data.get("bankinplay_company_card")] if access_data.get("bankinplay_company_card", False) else []
        }

        data = self._get_pending_async_request(
            access_data, self._post_request(access_data, url, params))
        transactions = self._get_transactions_from_data(data)
        return transactions

    def _get_transactions_from_data(self, data):
        """Get all transactions that are in the ponto response data."""
        transactions = data.get("results", [])
        if not transactions:
            _logger.info(
                _("No transactions where found in data %s"),
                data,
            )
        elif isinstance(transactions, dict):
            movimientos = transactions.get("movimientos", [])
            if movimientos:
                transactions = movimientos

        _logger.info(
            _("%d transactions present in response data"),
            len(transactions),
        )
        return transactions

    def _get_request(self, access_data, url, params):
        """Interact with Ponto to get next page of data."""
        headers = self._get_request_headers(access_data)
        _logger.info(
            _("GET request to %s with headers %s and params %s"), url, headers, params
        )
        response = requests.get(url, params=params, headers=headers)
        return self._get_response_data(response, access_data)

    def _simple_post_request(self, access_data, url, params, data=None):
        """Interact with Ponto to get next page of data."""
        headers = self._get_request_headers(access_data)

        _logger.info(
            _("`POST` request to %s with headers %s and params %s"), url, headers, params
        )
        response = requests.post(
            url, params=params, headers=headers, data=data)
        data = json.loads(response.text)
        return data

    def _post_request(self, access_data, url, params, data=None):
        """Interact with Ponto to get next page of data."""
        headers = self._get_request_headers(access_data)

        _logger.info(
            _("`POST` request to %s with headers %s and params %s"), url, headers, params
        )
        response = requests.post(
            url, params=params, headers=headers, data=data)
        return self._get_response_data(response, access_data)

    def _put_request(self, access_data, url, params, data=None):
        """Interact with Ponto to get next page of data."""
        headers = self._get_request_headers(access_data)

        _logger.info(
            _("`POST` request to %s with headers %s and params %s"), url, headers, params
        )
        response = requests.put(url, params=params, headers=headers, data=data)
        return self._get_response_data(response, access_data)

    def _delete_request(self, access_data, url, params, data=None):
        """Interact with Ponto to get next page of data."""
        headers = self._get_request_headers(access_data)

        _logger.info(
            _("`POST` request to %s with headers %s and params %s"), url, headers, params
        )
        response = requests.delete(
            url, params=params, headers=headers, data=data)
        return self._get_response_data(response, access_data)

    def _get_response_data(self, response, access_data=False):
        """Get response data for GET or POST request."""
        _logger.info(_("HTTP answer code %s from BankInPlay"),
                     response.status_code)
        if response.status_code not in (200, 201):
            raise UserError(
                _("Server returned status code %s: %s")
                % (response.status_code, response.text)
            )
        data = json.loads(response.text)
        return self._desencrypt_data(data, access_data)

    def _desencrypt_data(self, data, access_data=False):

        if data.get('data', False) and data.get('signature', False):
            data = data.get('data', False)
            if not isinstance(data, str):
                if data.get('resultados', False):
                    data = data.get('resultados')
                elif data.get('planes_contables', False):
                    data = data.get('planes_contables')
                elif data.get('documento_tercero_id', False):
                    data = data.get('documento_tercero_id')
                else:
                    return data
            key = access_data["user"].ljust(16, '$')[:16]
            iv = access_data["pass"].ljust(16, '$')[:16]

            encrypted_bytes = base64.b64decode(data)
            cipher = AES.new(key.encode('utf-8'),
                             AES.MODE_CBC, iv.encode('utf-8'))
            decrypted_bytes = unpad(cipher.decrypt(
                encrypted_bytes), AES.block_size)
            return json.loads(decrypted_bytes.decode('utf-8'))
        else:
            return data

    def _get_closing_transactions(self, access_data, date_since, date_until):
        """Get closing transactions from bankingplay."""
        url = BANKINPLAY_ENDPOINT_V1 + "/statement/lectura_cierre"

        params = {
            "exportados": True,
            "deshabilitar_callback": True,
            "fechaDesdeOperacion": date_since.strftime("%d/%m/%Y"),
            "fechaHastaOperacion": date_until.strftime("%d/%m/%Y"),
            "cuentasBancarias": [access_data.get("bankinplay_account")] if access_data.get("bankinplay_account", False) else []
        }

        data = self._get_pending_async_request(
            access_data, self._post_request(access_data, url, params))
        transactions = self._get_transactions_from_data(data)
        return transactions

    def manage_lectura_intradia_callback(self, data, event_data):
        """Manage the callback for intraday transactions."""
        transactions = self._get_transactions_from_data(data)
        provider_id = self.env["account.online.provider"].browse(
            event_data.get("provider_id")
        )
        statement_date_since = event_data.get("date_since")
        statement_date_until = event_data.get("date_until")
        provider_id._create_or_update_statement(
            transactions, statement_date_since, statement_date_until
        )
