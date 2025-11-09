import os
import time
import logging
from pprint import pprint
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from selenium.webdriver.common.by import By
from seleniumbase import SB

# ==== Настройки - замените на свои ====
proxy_russia = "https://u327dc3fc51bf0599-zone-custom-region-ru:u327dc3fc51bf0599@165.154.179.147:2333"  # формат прокси "login:pass@ip:port"
login = "1053893238"
my_key = "d4a0f283579c2aecc0d5b47211bf312d"
agent = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
url = "https://vk.com/"
# ======================================

# Параметры работы
CREATE_TASK_URL = "https://api.rucaptcha.com/createTask"
GET_RESULT_URL = "https://api.rucaptcha.com/getTaskResult"
POLL_INTERVAL = 5               # секунд между опросами результата
POLL_TIMEOUT = 180              # максимальное общее ожидание в секундах

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("vkcaptcha")

def parse_proxy(proxy_str: str):
    """Парсит строку proxy вида login:pass@host:port -> dict"""
    if not proxy_str:
        raise ValueError("proxy string is empty")
    try:
        creds, hostpart = proxy_str.split("@", 1)
        login, password = creds.split(":", 1)
        address, port = hostpart.split(":", 1)
        return dict(proxyLogin=login, proxyPassword=password,
                    proxyAddress=address, proxyPort=int(port))
    except Exception as e:
        raise ValueError(f"Can't parse proxy string '{proxy_str}': {e}")



def create_vkcaptcha_task(api_key, redirect_uri, user_agent, proxy_dict):
    """
    Создаёт задачу VKCaptchaTask в ruCaptcha (createTask).
    Возвращает taskId или бросает исключение.
    """
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "VKCaptchaTask",
            "redirectUri": redirect_uri,
            "userAgent": user_agent,
            "proxyType": "HTTP",  # можно менять исходя из вашего прокси
            "proxyAddress": proxy_dict["proxyAddress"],
            "proxyPort": proxy_dict["proxyPort"],
        }
    }
    # добавим логин/пароль если есть
    if proxy_dict.get("proxyLogin"):
        payload["task"]["proxyLogin"] = proxy_dict["proxyLogin"]
        payload["task"]["proxyPassword"] = proxy_dict["proxyPassword"]

    log.info("Отправляем createTask в ruCaptcha...")
    r = requests.post(CREATE_TASK_URL, json=payload, timeout=30)
    r.raise_for_status()
    resp = r.json()
    log.debug("createTask response: %s", resp)
    if resp.get("errorId") != 0:
        raise RuntimeError(f"createTask error: {resp}")
    task_id = resp.get("taskId")
    if not task_id:
        raise RuntimeError(f"createTask returned no taskId: {resp}")
    log.info("Задача создана, taskId=%s", task_id)
    return task_id

def poll_result(api_key, task_id, timeout=POLL_TIMEOUT, interval=POLL_INTERVAL):
    """Опрос getTaskResult пока не будет готово или пока timeout."""
    data = {"clientKey": api_key, "taskId": task_id}
    started = time.time()
    while True:
        r = requests.post(GET_RESULT_URL, json=data, timeout=30)
        r.raise_for_status()
        resp = r.json()
        log.debug("getTaskResult: %s", resp)
        if resp.get("errorId") != 0:
            raise RuntimeError(f"getTaskResult error: {resp}")
        status = resp.get("status")
        if status == "ready":
            solution = resp.get("solution", {})
            token = solution.get("token") or solution.get("gRecaptchaResponse") or solution.get("request")  # fallback fields
            return resp
        if time.time() - started > timeout:
            raise TimeoutError("Timeout while waiting for captcha solution")
        time.sleep(interval)

def main_flow(redirect_uri, proxy=None, my_key=None, agent=None):
    """
    Решает капчу VK по redirect_uri, без входа в VK.
    Возвращает токен решения RuCaptcha.
    """
    if not redirect_uri:
        raise ValueError("Не передан redirect_uri от VK API (need_captcha)")

    proxy = proxy or "165.154.179.147:2333"
    my_key = my_key or os.environ.get("RUCAPTCHA_KEY") or "d4a0f283579c2aecc0d5b47211bf312d"
    agent = agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    log.info(f"[VKCaptcha] Открываю капчу: {redirect_uri}")
    proxy_dict = parse_proxy(proxy)
    session_token = None

    try:
        # Запуск браузера
        with SB(uc=True, locale_code="en") as sb:
            sb.set_window_size(600, 800)
            sb.activate_demo_mode()  # делает окно видимым и управляемым
            sb.driver.execute_cdp_cmd(
                "Network.setUserAgentOverride", {"userAgent": agent}
            )
            sb.open(redirect_uri)
            time.sleep(2)

            # log.info("Ищу iframe с капчей и извлекаю redirect_uri/session_token...")
            # redirect_uri_extracted, session_token, iframe_src = extract_from_iframe(sb)
            # log.info(
            #     f"→ redirect_uri={redirect_uri_extracted}, session_token={session_token}, iframe={iframe_src}"
            # )
    except Exception as e:
        log.exception(f"Ошибка при работе Selenium: {e}")
        raise

    # используем redirect_uri (если найден), иначе тот, что из API
    # task_input = redirect_uri_extracted or redirect_uri

# --- Создание задачи в RuCaptcha ---
    log.info("Создаю задачу VKCaptchaTask в RuCaptcha...")
    task_payload = {
        "clientKey": my_key,
        "task": {
            "type": "VKCaptchaTask",
            "userAgent": agent,
            "proxyType": "HTTP",
            "proxyAddress": proxy_dict["proxyAddress"],
            "proxyPort": proxy_dict["proxyPort"],
        }
    }

    # если найден session_token → используем его, иначе redirect_uri
    if session_token:
        task_payload["task"]["sessionToken"] = session_token
        log.info("Используется session_token для VKCaptchaTask")
    else:
        task_payload["task"]["redirectUri"] = redirect_uri
        log.info("Используется redirect_uri для VKCaptchaTask")

    if proxy_dict.get("proxyLogin"):
        task_payload["task"]["proxyLogin"] = proxy_dict["proxyLogin"]
        task_payload["task"]["proxyPassword"] = proxy_dict["proxyPassword"]

    # --- Отправка задачи ---
    r = requests.post("https://api.rucaptcha.com/createTask", json=task_payload, timeout=30)
    r.raise_for_status()
    resp = r.json()
    if resp.get("errorId") != 0:
        raise RuntimeError(f"createTask error: {resp}")
    task_id = resp.get("taskId")
    log.info(f"Задача создана, taskId={task_id}")

    # --- Ожидание решения ---
    log.info("Ожидаю решение...")
    result = poll_result(my_key, task_id)

    token = (
        result.get("solution", {}).get("token")
        or result.get("solution", {}).get("gRecaptchaResponse")
        or result.get("solution", {}).get("request")
    )

    if not token:
        raise RuntimeError("Не удалось извлечь token из ответа RuCaptcha")

    log.info(f"[VKCaptcha] ✅ Капча решена успешно! token={token}")
    return token



if __name__ == "__main__":
    try:
        token = main_flow()
        print("Final solved token:", token)
    except Exception as e:
        log.exception("Ошибка в основном потоке: %s", e)
        raise
