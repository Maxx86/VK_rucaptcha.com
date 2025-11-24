import time
import random
import logging
import json
import asyncio
import requests
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# -------------------------------
#  Настройки
# -------------------------------

CLIENT_ID = "2685278"
API_VERSION = "5.236"
REDIRECT_URI = "https://oauth.vk.com/blank.html"
SCOPE = "all"

OAUTH_URL = (
    f"https://oauth.vk.com/authorize?client_id={CLIENT_ID}"
    f"&display=page&redirect_uri={REDIRECT_URI}"
    f"&scope={SCOPE}&response_type=token&v={API_VERSION}"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
log = logging.getLogger("vkcaptcha")

USER_AGENTS = [
    # Windows Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",

    # Linux Chrome
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",

    # macOS Chrome
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",

    # Mobile
    "Mozilla/5.0 (Linux; Android 14; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
]

# RuCaptcha
RUCAPTCHA_KEY = "d4a0f283579c2aecc0d5b47211bf312d"
RUCAPTCHA_CREATE_TASK_URL = "https://api.rucaptcha.com/createTask"
RUCAPTCHA_GET_RESULT_URL = "https://api.rucaptcha.com/getTaskResult"


# -------------------------------
#   УТИЛИТЫ
# -------------------------------

def parse_fragment(url: str) -> dict:
    """Парсим фрагмент access_token из URL blank.html"""
    parsed = urlparse(url)
    if not parsed.fragment:
        return {}
    q = parse_qs(parsed.fragment)
    return {k: v[0] for k, v in q.items()}


def parse_proxy(proxy_str: str) -> dict:
    """
    Принимает строку:
        https://user:pass@IP:PORT
        http://user:pass@IP:PORT
        user:pass@IP:PORT
    Возвращает dict для Playwright:
        { "server": "http://ip:port", "username": "user", "password": "pass" }
    """
    proxy_str = proxy_str.strip()

    if "://" in proxy_str:
        _, rest = proxy_str.split("://", 1)
    else:
        rest = proxy_str

    creds, addr = rest.split("@", 1)

    if ":" not in creds:
        raise ValueError("Формат proxy должен быть user:pass@ip:port")

    user, pwd = creds.split(":", 1)
    host, port = addr.split(":", 1)

    return {
        "server": f"http://{host}:{port}",
        "username": user,
        "password": pwd
    }


# ====================== CAPTCHA PARSING / RUCAPTCHA ==========================

def parse_captcha_notrobot(data: dict) -> dict | None:
    """Парсим данные captchaNotRobot.getContent → status, extension, steps, image"""
    if not data or "response" not in data:
        print("[VKCaptcha] [!] Некорректный ответ VK captchaNotRobot")
        return None

    resp = data["response"]

    parsed = {
        "status": resp.get("status"),
        "extension": resp.get("extension"),
        "steps": resp.get("steps", []),
        "image": resp.get("image")  # base64
    }

    # print("\n=== CAPTCHA PARSED (VK) ===")
    # print(json.dumps(parsed, indent=2, ensure_ascii=False))

    return parsed


def solve_captcha_rucaptcha(captcha: dict) -> int | None:
    image_b64 = captcha.get("image")
    steps = captcha.get("steps") or []

    if not image_b64 or not steps:
        print("[RuCaptcha] ❌ Нет image или steps — не могу отправить задачу.")
        return None

    try:
        steps = [int(x) for x in steps]
    except Exception:
        print("[RuCaptcha] ❌ steps не приводятся к int:", steps)
        return None

    payload = {
        "clientKey": RUCAPTCHA_KEY,
        "task": {
            "type": "VKCaptchaImageTask",
            "image": image_b64,
            "steps": steps,
        }
    }

    print("\n[RuCaptcha] → createTask...")
    try:
        r = requests.post(RUCAPTCHA_CREATE_TASK_URL, json=payload, timeout=20)
        resp = r.json()
    except Exception as e:
        print("[RuCaptcha] ❌ Ошибка запроса createTask:", e)
        return None

    # print("[RuCaptcha] Ответ createTask:", json.dumps(resp, indent=2, ensure_ascii=False))

    if resp.get("errorId") != 0:
        print("[RuCaptcha] ❌ errorId != 0:", resp)
        return None

    task_id = resp.get("taskId")
    if not task_id:
        print("[RuCaptcha] ❌ Нет taskId в ответе")
        return None

    print(f"[RuCaptcha] ✔ taskId = {task_id}")
    print("[RuCaptcha] ⏳ Жду решение...")

    best_step = None
    started = time.time()
    while True:
        try:
            rr = requests.post(
                RUCAPTCHA_GET_RESULT_URL,
                json={"clientKey": RUCAPTCHA_KEY, "taskId": task_id},
                timeout=15
            )
            rd = rr.json()
        except Exception as e:
            print("[RuCaptcha] ❌ Ошибка getTaskResult:", e)
            break

        if rd.get("status") == "ready":
            print("[RuCaptcha] 🎉 Решение готово!")
            # print(json.dumps(rd, indent=2, ensure_ascii=False))
            solution = rd.get("solution") or {}
            best_step = solution.get("best_step")
            break

        if time.time() - started > 180:
            print("[RuCaptcha] ❌ Таймаут ожидания решения (>180 сек)")
            break

        time.sleep(1)

    if best_step is None:
        print("[RuCaptcha] ❌ best_step не получен")
    else:
        print(f"[RuCaptcha] ✔ best_step = {best_step}")

    return best_step


# ====================== SLIDER MOVE (Playwright, async) ======================

async def move_slider_by_best_step(page, best_step: int) -> bool:
    try:
        # Ищем iframe капчи
        frame = None
        for fr in page.frames:
            if any(x in (fr.url or "") for x in ["captcha", "is_robot", "not_robot"]):
                frame = fr
                break

        base = frame or page

        # Находим "input" + thumb
        slider_input = base.locator("input.vkc__SliderThumb-module__nativeInput")
        if await slider_input.count() == 0:
            print("[Slider] ❌ Не найден input.vkc__SliderThumb")
            return False

        slider_input = slider_input.first
        thumb = slider_input.locator("xpath=./parent::*")
        await thumb.wait_for(state="visible")

        # Получаем размеры
        track = thumb.locator("xpath=../..")
        track_box = await track.bounding_box()
        if not track_box:
            print("[Slider] ❌ track_box = None")
            return False

        track_width = track_box["width"]
        px_per_step = track_width / 100 * 2.04082  # как в Selenium

        print(f"[Slider] px_per_step = {px_per_step:.2f}px, steps = {best_step}")

        # JS код как в SeleniumBase — pointerdown + pointermove
        js_drag = """
            async (args) => {
                const [input, deltaX] = args;

                const thumb = input.closest('span[data-type="thumb"]');
                const rect = thumb.getBoundingClientRect();

                let x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;

                function firePointer(type, x, y){
                    const ev = new PointerEvent(type, {
                        pointerId: 1,
                        pointerType: 'mouse',
                        bubbles: true,
                        cancelable: true,
                        clientX: x,
                        clientY: y,
                        buttons: 1,
                    });
                    thumb.dispatchEvent(ev);
                }

                function fireMouse(type, x, y){
                    const ev = new MouseEvent(type, {
                        bubbles: true,
                        cancelable: true,
                        clientX: x,
                        clientY: y,
                        buttons: 1,
                    });
                    thumb.dispatchEvent(ev);
                }

                firePointer('pointerdown', x, y);
                fireMouse('mousedown', x, y);

                let steps = 1;
                x += deltaX;
                firePointer('pointermove', x, y);
                fireMouse('mousemove', x, y);

                return true;
            }
        """

        js_up = """
            async (args) => {
                const [input] = args;

                const thumb = input.closest('span[data-type="thumb"]');
                const rect = thumb.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;

                const ev = new MouseEvent('mouseup', {
                    bubbles: true,
                    cancelable: true,
                    clientX: x,
                    clientY: y
                });

                thumb.dispatchEvent(ev);
                return true;
            }
        """

        # Выполняем нужное количество шагов
        for i in range(best_step):
            delta = px_per_step
            await base.evaluate(js_drag, [await slider_input.element_handle(), delta])
            await asyncio.sleep(0.35)

        # Отпускаем мышь
        await base.evaluate(js_up, [await slider_input.element_handle()])
        print("[Slider] 🖱 Ползунок отпущен")

        return True

    except Exception as e:
        print(f"[Slider] ❌ JS move ERROR: {e}")
        return False




# ----------------------------------------------------
#   ВНУТРЕННЯЯ async-РЕАЛИЗАЦИЯ OAUTH + CAPTCHA
# ----------------------------------------------------

async def _obtain_token_selenium_async(login, password, proxy=None, headless=False):
    print("[*] Запуск VK OAuth через Playwright (async)…")

    # --- Proxy ---
    proxy_config = None
    if proxy:
        try:
            proxy_config = parse_proxy(proxy)
            print("[*] Прокси включён:", proxy)
        except Exception as e:
            print("[!] Ошибка парсинга proxy, продолжаю без него:", e)

    ua = random.choice(USER_AGENTS)
    print("[*] User-Agent:", ua)

    async with async_playwright() as p:
        launch_args = {"headless": headless}
        if proxy_config:
            launch_args["proxy"] = proxy_config

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent=ua,
            locale="ru",
            viewport={"width": 600, "height": 800}
        )

        page = await context.new_page()

        # ======================= Открываем OAuth =======================
        print("[*] Открываю OAuth:", OAUTH_URL)
        await page.goto(OAUTH_URL, timeout=60000)

        # === VK popup: "Ввести данные вручную" ===
        try:
            await asyncio.sleep(2)
            btn = page.locator("span.vkuiButton__content", has_text="Ввести данные вручную")
            await btn.wait_for(timeout=2000)
            await btn.click()
            print("[VKUI] Нажал кнопку 'Ввести данные вручную'")
        except Exception:
            pass

        # ---- Логин ----
        try:
            await asyncio.sleep(3)
            login_input = page.locator("input[name='login'], input[type='text']")
            await login_input.fill(login)
            print("[*] Ввёл логин")
        except Exception:
            print("[!] Не нашёл поле логина")

        await asyncio.sleep(2)
        await page.keyboard.press("Enter")

        # ---- Пароль ----
        await asyncio.sleep(2)
        try:
            pwd_input = page.locator("input[type='password']")
            await pwd_input.fill(password)
            print("[*] Ввёл пароль")
        except Exception:
            print("[!] Не нашёл поле пароля")

        await asyncio.sleep(2)
        await page.keyboard.press("Enter")

        # --- Опционально ждём iframe капчи (чисто для логов) ---
        print("[*] Ожидаю появления iframe капчи (если она есть)…")
        try:
            await page.wait_for_selector(
                "iframe[src*='captcha'], iframe[src*='is_robot'], iframe[src*='not_robot']",
                timeout=15000
            )
            print("[*] iframe капчи найден")
            has_iframe = True
        except PlaywrightTimeoutError:
            print("[*] iframe капчи не появился — возможно, капчи нет")
            has_iframe = False

        # === Корутина ожидания captchaNotRobot.getContent ===
        async def wait_captcha_content():
            if not has_iframe:
                print("[*] Пропускаю ожидание captchaNotRobot.getContent (iframe не найден)")
                return None

            print("[*] Жду вызов captchaNotRobot.getContent")
            try:
                resp = await page.wait_for_event(
                    "response",
                    timeout=180000,
                    predicate=lambda r: "captchaNotRobot.getContent" in r.url
                )
                print("\n[*] Пойман ответ captchaNotRobot.getContent!")
                data = await resp.json()
                # print(json.dumps(data, indent=2, ensure_ascii=False))
                return data
            except PlaywrightTimeoutError:
                print("[!] captchaNotRobot.getContent не был вызван (таймаут wait_for_event)")
                return None
            except Exception as e:
                print("[!] Ошибка при ожидании captchaNotRobot.getContent:", e)
                return None

        # === Корутина ожидания redirect ===
        async def wait_redirect():
            print("[*] Жду redirect на blank.html…")
            try:
                await page.wait_for_url("**/blank.html*", timeout=180000)
                return page.url
            except PlaywrightTimeoutError:
                print("[VKAuth] VK не сделал redirect вовремя.")
                return None
            except Exception as e:
                print("[VKAuth] Ошибка ожидания redirect:", e)
                return None

        # Запускаем оба ожидания ПАРАЛЛЕЛЬНО:
        captcha_task = asyncio.create_task(wait_captcha_content())
        redirect_task = asyncio.create_task(wait_redirect())

        captcha_data = None
        final_url = None

        # Ждём, что сработает первым — капча или redirect
        done, pending = await asyncio.wait(
            {captcha_task, redirect_task},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=180
        )

        # --- Если первой пришла капча ---
        if captcha_task in done:
            captcha_data = await captcha_task
            if captcha_data:
                vk_captcha = parse_captcha_notrobot(captcha_data)
                if vk_captcha:
                    best_step = solve_captcha_rucaptcha(vk_captcha)
                    if best_step is not None:
                        moved = await move_slider_by_best_step(page, best_step)
                        if moved:
                            print("[*] Слайдер успешно продвинут по best_step.")
                        else:
                            print("[!] Не удалось подвигать слайдер по best_step.")

            # После капчи всё равно нужен redirect
            if not redirect_task.done():
                print("[*] После решения капчи жду redirect на blank.html…")
                final_url = await redirect_task
            else:
                final_url = await redirect_task

        # --- Если первым пришёл redirect (капчи не было) ---
        elif redirect_task in done:
            final_url = await redirect_task
            if not captcha_task.done():
                captcha_task.cancel()
                try:
                    await captcha_task
                except Exception:
                    pass
        else:
            print("[!] Ни капча, ни redirect не произошли за 180 сек.")
            for t in pending:
                t.cancel()
            final_url = page.url

        print("[*] Final URL:", final_url)

        token_data = None
        if final_url and (REDIRECT_URI in final_url) and ("#access_token=" in final_url):
            token_data = parse_fragment(final_url)
            print("[VKAuth] OAuth SUCCESS!")
        else:
            print("[VKAuth] Токен не найден в URL")

        try:
            await browser.close()
        except Exception:
            pass

        return token_data


# ============================================================
# ВНЕШНЯЯ ФУНКЦИЯ ДЛЯ vk.py (СИНХРОННЫЙ ИНТЕРФЕЙС)
# ============================================================

def obtain_token_selenium(login, password, proxy=None, headless=False):
    """
    Снаружи — обычная синхронная функция с той же сигнатурой,
    внутри — async Playwright + правильный wait_for_event.
    """
    return asyncio.run(_obtain_token_selenium_async(login, password, proxy, headless))