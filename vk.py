import re
import time
import os
import requests
import core.helpers as Helpers

from core.app_config import APP_CONFIG
from libs.vk.vk_models import *
from libs.vk.vk_auth_with_solver import obtain_token_selenium

__all__ = ['VK', 'VKExceptions']


class VKExceptions:
    class APIError(Exception):
        code: int
        msg: str

        def __init__(self, error: VKError):
            self.code = error.code
            self.msg = error.msg

        def to_dict(self):
            return {
                "error": {
                    "code": self.code,
                    "msg": self.msg
                }
            }


class VK:
    access_token: str
    user_id: int
    user_agent: str
    device_id: str
    proxy: str

    __session: requests.Session

    def __init__(self):
        self.__session = requests.Session()

    def set_session(self, auth_data: dict):
        self.access_token = auth_data.get('access_token')
        self.user_id = auth_data.get('user_id')
        self.user_agent = auth_data.get('user_agent')
        self.device_id = auth_data.get('device_id')
        self.proxy = auth_data.get('proxy')
        return self

    def set_proxy(self, proxy):
        self.proxy = proxy

    def _normalize_proxy(self, proxy):
        if proxy and proxy.startswith('https://'):
            return proxy.replace('https://', 'http://', 1)
        return proxy

    # --------------------------------------------------------------------
    #                             AUTH
    # --------------------------------------------------------------------

    def auth(self, username: str, password: str, captcha_sid=None, captcha_key=None, _captcha_attempt=0):
        """
        Авторизация через VK Password Grant.
        Если VK отвечает need_captcha — переключаемся на Selenium-flow.
        """

        user_agent = (
            'VKAndroidApp/8.52-14102 (Android 13; SDK 33; arm64-v8a; Samsung SM-G998B; ru; 2400x1080)'
        )

        if not getattr(self, "device_id", None):
            self.device_id = Helpers.get_random_string(16)

        device_id = self.device_id

        data = {
            "client_id": 2274003,
            "client_secret": "hHbZxrka2uZ6jB1inYsH",
            "https": 1,
            "libverify_support": 1,
            "scope": "all",
            "grant_type": "password",
            "username": username,
            "password": password,
            "2fa_supported": 1,
            "v": 5.199,
            "lang": "ru",
            "device_id": device_id,
            "api_id": 2274003,
        }

        normalized_proxy = self._normalize_proxy(self.proxy)

        try:
            request = self.__session.post(
                "https://oauth.vk.com/token",
                data=data,
                headers={
                    "cache-control": "no-cache",
                    "user-agent": user_agent,
                    "x-vk-android-client": "new",
                    "accept-encoding": "gzip",
                },
                proxies={"http": normalized_proxy, "https": normalized_proxy},
                timeout=30,
            )
        except Exception as e:
            raise VKExceptions.APIError(
                VKError({"error_code": -1, "error_msg": str(e)})
            )

        try:
            json_data = request.json()
        except Exception:
            print(request.text)
            raise VKExceptions.APIError(
                VKError({"error_code": -999, "error_msg": "Invalid JSON"})
            )

        error = json_data.get("error")

        # --------------------------------------------------------------------
        #                     SUCCESS
        # --------------------------------------------------------------------
        if error is None:
            auth_data = json_data | {"user_agent": user_agent, "device_id": device_id}
            self.set_session(auth_data | {"proxy": self.proxy})
            print("[VKAuth] SUCCESS")
            return auth_data

        # --------------------------------------------------------------------
        #                     CAPTCHA → Fallback to Selenium
        # --------------------------------------------------------------------
        if error == "need_captcha":
            print("[VKAuth] VK requires captcha → switching to Selenium OAuth flow")

            token_data = obtain_token_selenium(username, password, proxy=self.proxy)

            if token_data and token_data.get("access_token"):
                print("[VKAuth] Selenium auth success")
                self.set_session(token_data | {"proxy": self.proxy})
                return token_data

            print("[VKAuth] Selenium returned no token (manual captcha probably needed).")
            return None  # <-- НЕ кидаем ошибку!

        # --------------------------------------------------------------------
        #                     OTHER AUTH ERRORS
        # --------------------------------------------------------------------
        print("[VKAuth] ERROR:", json_data)
        raise VKExceptions.APIError(
            VKError({
                "error_code": json_data.get("error_code", -100),
                "error_msg": json_data.get("error_description", error)
            })
        )

    # -------------------------------------------------------------------------
    #                         API METHODS (unchanged)
    # -------------------------------------------------------------------------

    def call_api(self, endpoint: str, params=None):
        if params is None:
            params = {}

        params['v'] = 5.199
        params['lang'] = 'ru'
        params['https'] = 1
        params['device_id'] = self.device_id
        params['access_token'] = self.access_token

        if not self.proxy:
            raise VKExceptions.APIError(VKError({'error_code': -5, 'error_msg': 'proxy is empty'}))

        normalized_proxy = self._normalize_proxy(self.proxy)

        try:
            request = self.__session.post(
                f"https://api.vk.com/method/{endpoint}",
                data=params,
                headers={
                    'cache-control': 'no-cache',
                    'user-agent': self.user_agent,
                    'x-vk-android-client': 'new',
                    'content-type': 'application/x-www-form-urlencoded; charset=utf-8'
                },
                proxies={'http': normalized_proxy, 'https': normalized_proxy},
                timeout=30
            )
        except Exception as e:
            raise VKExceptions.APIError(VKError({'error_code': -1, 'error_msg': str(e)}))

        json_data = request.json()

        if "error" in json_data:
            raise VKExceptions.APIError(VKError(json_data["error"]))

        return json_data.get("response")
