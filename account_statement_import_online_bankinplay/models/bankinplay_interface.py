# Copyright 2020 Florent de Labarre
# Copyright 2022 Therp BV <https://therp.nl>.
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

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
                access_data["bankinplay_account"] = bankinplay_account.get("id")
                return access_data
        # If we get here, we did not find Ponto account for bank account.
        raise UserError(
            _("BankInPlay : wrong configuration, account %s not found in %s")
            % (account_number, data)
        )

    def _get_transactions(self, access_data, date_since, date_until):
        """Get transactions from bankingplay, using last_identifier as pointer.

        Note that Ponto has the transactions in descending order. The first
        transaction, retrieved by not passing an identifier, is the latest
        present in Ponto. If you read transactions 'after' a certain identifier
        (Ponto id), you will get transactions with an earlier date.
        """
        url = BANKINPLAY_ENDPOINT_V1 + "/statement/lectura_intradia"
        
        params = {
            "exportados": True,
            "deshabilitar_callback": True,
            "fechaDesdeOperacion": date_since.strftime("%d/%m/%Y"),
            "fechaHastaOperacion": date_until.strftime("%d/%m/%Y"),
            "cuentasBancarias": [access_data.get("bankinplay_account")] if access_data.get("bankinplay_account", False) else []
        }
        
        data = self._post_request(access_data, url, params)
        responseId = data.get('responseId', '')
        if not responseId:
            raise UserError('No se han podido traer las transacciones')
        
        url = BANKINPLAY_ENDPOINT_V1 + "/statement/status/" + responseId
        data = self._get_request(access_data, url, params)
        
        while data['estado'] != 'procesado':
            data = self._get_request(access_data, url, params)
            estado = data.get('estado', '')
            if estado == 'erroneo':
                raise UserError('Error en la solicitud de transacciones')
            time.sleep(2)

        url = BANKINPLAY_ENDPOINT_V1 + "/respuestaAsincronaApi/recogida?responseId="+responseId
        data = self._get_request(access_data, url, params)
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
        else:
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
    
    def _post_request(self, access_data, url, params, data=None):
        """Interact with Ponto to get next page of data."""
        headers = self._get_request_headers(access_data)

        _logger.info(
            _("`POST` request to %s with headers %s and params %s"), url, headers, params
        )
        response = requests.post(url, params=params, headers=headers, data=data)
        return self._get_response_data(response, access_data)
    
    def _put_request(self, access_data, url, params, data=None):
        """Interact with Ponto to get next page of data."""
        headers = self._get_request_headers(access_data)

        _logger.info(
            _("`POST` request to %s with headers %s and params %s"), url, headers, params
        )
        response = requests.put(url, params=params, headers=headers, data=data)
        return self._get_response_data(response, access_data)

    def _get_response_data(self, response, access_data=False):
        """Get response data for GET or POST request."""
        _logger.info(_("HTTP answer code %s from BankInPlay"), response.status_code)
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
            cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, iv.encode('utf-8'))
            decrypted_bytes = unpad(cipher.decrypt(encrypted_bytes), AES.block_size)
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
        
        data = self._post_request(access_data, url, params)
        responseId = data.get('responseId', '')
        if not responseId:
            raise UserError('No se han podido traer las transacciones de cierre')
        
        data = self._get_pending_async_request(access_data, responseId)
        transactions = self._get_transactions_from_data(data)
        return transactions
    

    def _get_pending_async_request(self, access_data, responseId):
        url = BANKINPLAY_ENDPOINT_V1 + "/statement/status/" + responseId
        data = self._get_request(access_data, url, {})
        
        while data['estado'] != 'procesado' and data['estado'] != 'terminado':
            data = self._get_request(access_data, url, {})
            estado = data.get('estado', '')
            if estado == 'erroneo':
                _logger.info(
                    _("Error en la solicitud"),
                )
                raise UserError('Error en la solicitud')
            time.sleep(2)
        url = BANKINPLAY_ENDPOINT_V1 + "/respuestaAsincronaApi/recogida?responseId="+responseId
        data = self._get_request(access_data, url, {})
        return data

        