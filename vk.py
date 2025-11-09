import re
import time
import os
import requests

import core.helpers as Helpers
from core.app_config import APP_CONFIG


from libs.vk.vk_models import *


from libs import selenium_vk_solver as vk_solver

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

    # __captcha_solver: RuCaptcha
    __try_solve_captcha: bool = False
    __session: requests.Session

    def __init__(self):
        self.__session = requests.Session()

    def set_session(self, auth_data: dict):
        self.access_token = auth_data.get('access_token', None)
        self.user_id = auth_data.get('user_id', None)
        self.user_agent = auth_data.get('user_agent', None)
        self.device_id = auth_data.get('device_id', None)
        self.proxy = auth_data.get('proxy', None)

        # self.__captcha_solver = RuCaptcha('b0e22f02e9c0da86b42fca2d3e0ab564')

        return self

    def set_proxy(self, proxy):
        self.proxy = proxy

    def _normalize_proxy(self, proxy):
        """
        Normalize proxy URL for use with requests library.
        Converts https:// proxy URLs to http:// to avoid SSL hostname verification issues.
        """
        if proxy and proxy.startswith('https://'):
            return proxy.replace('https://', 'http://', 1)
        return proxy

    def __solve_captcha_via_browser(self, *, login: str | None, user_agent: str) -> str | None:
        """
        Обёртка над selenium_vk_solver.
        Возвращает строку-решение (token/текст), либо None при неуспехе.
        """
        try:
            # ключ руКапчи можно хранить в APP_CONFIG или env
            api_key = getattr(APP_CONFIG, "rucaptcha_key", None) or os.environ.get("RUCAPTCHA_KEY")
            if not api_key:
                print("[VKCaptcha] Внимание: не задан ключ RuCaptcha (APP_CONFIG.rucaptcha_key или env RUCAPTCHA_KEY)")

            # сам вызов твоего модуля
            # ожидаем, что внутри есть функция solve(...) ИЛИ main_flow(...)
            if hasattr(vk_solver, "solve"):
                return vk_solver.solve(proxy=self.proxy, agent=user_agent, login=login, my_key=api_key)
            elif hasattr(vk_solver, "main_flow"):
                return vk_solver.main_flow(proxy=self.proxy, agent=user_agent, login=login, my_key=api_key)
            else:
                print("[VKCaptcha] В модуле selenium_vk_solver нет функций solve/main_flow")
                return None
        except Exception as e:
            print(f"[VKCaptcha] Ошибка решателя: {e}")
            return None

    def auth(self, username: str, password: str, captcha_sid: str = None, captcha_key: str = None,
             _captcha_attempt: int = 0):
        """
        Авторизация VK с поддержкой новой not_robot капчи (через redirect_uri)
        и с расширенным логированием.
        """

        user_agent: str = (
            'VKAndroidApp/8.52-14102 (Android 13; SDK 33; arm64-v8a; Samsung SM-G998B; ru; 2400x1080)'
        )

        if not hasattr(self, "device_id") or not self.device_id:
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

        if captcha_sid and captcha_key and not getattr(self, "_new_vk_captcha_flow", False):
            data["captcha_sid"] = captcha_sid
            data["captcha_key"] = captcha_key
            print(f"[VKAuth] Retrying auth with legacy captcha: sid={captcha_sid}")

        normalized_proxy = self._normalize_proxy(self.proxy)

        print("=" * 90)
        print(f"[VKAuth] Starting VK OAuth request (attempt {_captcha_attempt})")
        print(f"[VKAuth] Proxy: {normalized_proxy}")
        print("[VKAuth] Data to be sent:")
        for k, v in data.items():
            print(f"    {k}: {v}")
        print("=" * 90)

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
        except requests.exceptions.RequestException as e:
            raise VKExceptions.APIError(VKError({"error_code": -1, "error_msg": str(e)}))

        try:
            json_data: dict = request.json()
        except Exception:
            print(f"[VKAuth] ⚠️ Invalid JSON response: {request.text[:500]}")
            raise VKExceptions.APIError(VKError({"error_code": -999, "error_msg": "Invalid JSON from VK"}))

        error = json_data.get("error")

        print("=" * 80)
        print(f"[VKAuth] VK OAuth response (attempt {_captcha_attempt}):")
        print(json_data)
        print("=" * 80)

        # ✅ Успешная авторизация
        if error is None:
            auth_data = json_data | {"user_agent": user_agent, "device_id": device_id}
            self.set_session(auth_data | {"proxy": self.proxy})
            print("[VKAuth] ✅ Auth successful")
            return auth_data

        # ⚠️ Требуется капча
        if error == "need_captcha":
            captcha_redirect_uri = json_data.get("redirect_uri")
            captcha_sid_value = json_data.get("captcha_sid")

            if _captcha_attempt >= 2:
                print(f"[VKCaptcha] ❌ Капча повторяется {_captcha_attempt} раз. Прерываю.")
                raise VKExceptions.APIError(VKError({"error_code": -200, "error_msg": "Captcha loop detected"}))

            # 🧩 Новый VK ID (redirect_uri)
            if captcha_redirect_uri:
                print(f"[VKCaptcha] Redirect URI detected: {captcha_redirect_uri}")
                print("[VKCaptcha] Solving captcha via selenium_vk_solver...")

                solved_captcha = None
                try:
                    solved_captcha = vk_solver.main_flow(
                        redirect_uri=captcha_redirect_uri,
                        proxy=self.proxy,
                        my_key=os.environ.get("RUCAPTCHA_KEY") or "d4a0f283579c2aecc0d5b47211bf312d",
                        agent=user_agent,
                    )
                except Exception as e:
                    print(f"[VKCaptcha] ❌ Ошибка selenium_vk_solver: {e}")

                if solved_captcha and str(solved_captcha).strip():
                    captcha_text = str(solved_captcha).strip()
                    print(f"[VKCaptcha] ✅ Капча решена: {captcha_text}")

                    # 🔧 Отправляем POST-redirect (активация результата)
                    try:
                        from urllib.parse import urlparse, parse_qs, urlencode

                        parsed = urlparse(captcha_redirect_uri)
                        query = parse_qs(parsed.query)
                        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                        query_str = urlencode(query, doseq=True)
                        final_url = f"{base_url}?{query_str}"

                        resp = self.__session.post(
                            final_url,
                            data={'token': captcha_text},  # <-- именно token, а не captcha_key
                            headers={
                                'User-Agent': user_agent,
                                'Content-Type': 'application/x-www-form-urlencoded',
                                'Referer': 'https://id.vk.com/',
                                'Origin': 'https://id.vk.com',
                            },
                            timeout=15
                        )

                        print(f"[VKCaptcha] Activation response: {resp.status_code}")
                        print(f"[VKCaptcha] Activation response body (first 300 chars): {resp.text[:300]}")

                        if resp.status_code in (200, 302):
                            print("[VKCaptcha] ✅ Redirect OK and logically confirmed (skipping confirm GET).")
                        else:
                            print(f"[VKCaptcha] ⚠️ Unexpected redirect status: {resp.status_code}")

                    except Exception as e:
                        print(f"[VKCaptcha] ⚠️ Redirect activation failed: {e}")

                    # небольшая задержка, чтобы VK применил результат капчи
                    time.sleep(1.0)

                    # 🚫 больше не вызываем selenium
                    self._new_vk_captcha_flow = True
                    print(
                        f"[VKCaptcha] Retrying auth after captcha (attempt {_captcha_attempt + 1}) WITHOUT Selenium...")
                    return self.auth(username, password, None, None, _captcha_attempt + 1)

                else:
                    print("[VKCaptcha] ❌ Не удалось получить результат от selenium_vk_solver")

            elif captcha_sid_value:
                print("[VKCaptcha] Legacy captcha (image) flow detected (no redirect_uri)")
                # старую систему можно вырезать, если она больше не нужна

        print(f"[VKAuth] ❌ Auth error: {error} → {json_data.get('error_description', str(error))}")
        raise VKExceptions.APIError(
            VKError({"error_code": json_data.get("error_code", -100),
                     "error_msg": json_data.get("error_description", str(error))})
        )

    def utils_resolve_screen_name(self, screen_name: str):
        result = self.call_api('utils.resolveScreenName', {
            'screen_name': screen_name
        })

        return VKObject(result)

    def set_online(self):
        return self.call_api('account.setOnline')

    def get_current_user(self):
        return self.users_get([self.user_id], fields=['photo_50', 'photo_100'])

    def messages_mark_as_read(self, peer_id: int):
        return self.call_api('messages.markAsRead', {
            'peer_id': peer_id
        })

    def get_page_content(self, user_id: int):
        code = '''
            var user = API.users.get({
              "user_ids": ''' + str(user_id) + ''', 
              "fields": "photo_id,photo_max_orig,status"
            }).pop();

            var wall;
            var photos;

            if (!user.is_closed) {
              wall = API.wall.get({
                  "owner_id": ''' + str(user_id) + ''', 
                  "count": 5,
                  "extended": 1
              });
            }

            return {"info": user, "wall": wall};
        '''

        user_info = self.call_api('execute', {
            'code': code
        })

        info = VKUser(user_info['info'])
        wall: list[Wall] = []

        for wall_info in user_info['wall']['items']:
            wall.append(Wall(wall_info))

        return info, wall

    def wall_post(self, text: str, attachments: list[str]):
        return self.call_api('wall.post', {
            'message': text,
            'attachment': ','.join(attachments),
        })['post_id']

    def wall_pin(self, post_id: int):
        return self.call_api('wall.pin', {
            'post_id': post_id
        })

    def newsfeed_get(
            self,
            filters=None,
            sources: list[str] = 'friends',
            count: int = 5
    ):
        if filters is None:
            filters = ['post', 'photo']

        newsfeed = self.call_api('newsfeed.get', {
            'source_ids': ','.join(sources),
            'filters': ','.join(filters),
            'count': count
        })

        profiles: list[VKUser] = []
        for profile in newsfeed.get('profiles', []):
            profiles.append(
                VKUser(profile)
            )

        items: list[NewsFeedItem] = []
        for item in newsfeed['items']:
            items.append(
                NewsFeedItem(item)
            )

        return items, profiles

    def users_get(
            self,
            users_ids: list,
            fields=None
    ):
        if fields is None:
            fields = ['photo_50', 'sex', 'photo_100', 'status',
                      'followers_count']

        users_info = self.call_api('users.get', {
            'user_ids': ','.join(map(str, users_ids)),
            'fields': ','.join(fields)
        })

        users: list[VKUser] = []
        for user_info in users_info:
            users.append(
                VKUser(user_info)
            )

        if len(users_ids) == 1:
            if len(users) > 0:
                return users.pop()

        return users

    def friends_get_suggestions(self, count: int) -> list[VKUser]:
        result = self.call_api('friends.getSuggestions', {
            'filter': 'mutual',
            'count': count,
            'fields': 'online'
        })

        users: list[VKUser] = []
        for user in result['items']:
            users.append(
                VKUser(user)
            )

        return users

    def friends_get_requests(self, count: int):
        result = self.call_api('friends.getRequests', {
            'count': count,
            'sort': 0,
            'need_viewed': 0,
            'extended': 1
        })

        requests: list[FriendsRequest] = []
        for request in result['items']:
            requests.append(
                FriendsRequest(request)
            )

        return requests

    def likes_add(self, type_: str, item_id: int, owner_id: int):
        add = self.call_api('likes.add', {
            'type': type_,
            'item_id': item_id,
            'owner_id': owner_id
        })

        return LikesAdd(add)

    def wall_get(self, owner_id: int, count: int, filter_: str = 'owner'):
        result = self.call_api('wall.get', {
            'owner_id': owner_id,
            'count': count,
            'filter': filter_
        })

        walls: list[Wall] = []

        for wall in result['items']:
            walls.append(
                Wall(wall)
            )

        return walls

    def friends_add(self, user_id: int, text: str = None):
        params = {
            'user_id': user_id,
            'add_only': 0,
            'source': 'profile',
            'follow': 0
        }

        if text is None or len(text) > 0:
            params['text'] = text

        add = self.call_api('execute.friendsAddWithRecommendations', params)

        return add.get('status', 0)

    def execute(self, code: str):
        return self.call_api('execute', {
            'code': code
        })

    def repost(self, item: str, message: str = ''):
        return self.call_api('wall.repost', {
            "object": item,
            "message": message
        }).get("post_id", None)

    def status_set(self, text: str):
        return self.call_api('status.set', {
            'text': text
        })

    def messages_send(self, peer_id: int, text: str, attachments=None):
        if attachments is None:
            attachments = []

        result = self.call_api('messages.send', {
            'peer_id': peer_id,
            'message': text,
            'attachment': ','.join(attachments),
            'random_id': 0
        })

        return result

    def upload_file(
            self,
            upload_server: UploadServer,
            file_name: str = None,
            file_path: str = None,
            post_name: str = 'file'
    ):
        if file_path is None:
            cache_path = APP_CONFIG.paths.cache

            file_path = f'{cache_path}/{file_name}'

        ext = file_path.split('.').pop()

        if ext in ['png', 'PNG', 'jpg', 'JPEG']:
            file_path = Helpers.add_noise_to_image(file_path)

        normalized_proxy = self._normalize_proxy(self.proxy)

        upload = self.__session.post(
            upload_server.upload_url,
            files={f'{post_name}': open(file_path, 'rb')},
            proxies={
                'http': normalized_proxy,
                'https': normalized_proxy,
            }
        )

        upload = upload.json()

        return upload

    def upload_photo_for_chat(
            self,
            peer_id: int,
            file_name: str = None,
            file_path: str = None
    ):
        server = self.call_api('photos.getMessagesUploadServer', {
            'peer_id': peer_id
        })

        uploaded_photo = self.upload_file(
            UploadServer(server),
            file_name,
            file_path
        )

        save = self.call_api('photos.saveMessagesPhoto', uploaded_photo)

        photo = save.pop(0)

        return Photo(photo)

    def upload_photo_for_profile(self, file_path: str):
        server = self.call_api('photos.getOwnerPhotoUploadServer')

        uploaded_photo = self.upload_file(
            UploadServer(server),
            file_path=file_path
        )

        save = self.call_api('photos.saveOwnerPhoto', uploaded_photo)

        return save

    def upload_photo_for_wall(self, file_path: str = None):
        server = self.call_api('photos.getWallUploadServer')

        uploaded_photo = self.upload_file(
            UploadServer(server),
            file_path=file_path
        )

        save = self.call_api('photos.saveWallPhoto', uploaded_photo)

        photo = save.pop(0)

        return Photo(photo)

    def upload_video_story(
            self,
            url: str,
            file_name: str = None,
            file_path: str = None
    ):
        server = self.call_api('stories.getVideoUploadServer', {
            'add_to_news': 1,
            'link_url': url
        })

        uploaded_story = self.upload_file(
            UploadServer(server),
            file_name,
            file_path,
            post_name='video_file'
        )

        save = self.call_api('stories.save', {
            'upload_results': uploaded_story['response']['upload_result']
        })

        stories: list[Story] = []

        for story in save.get('items', []):
            stories.append(Story(story))

        return stories

    def upload_image_story(
            self,
            url: str,
            file_name: str = None,
            file_path: str = None
    ):
        server = self.call_api('stories.getPhotoUploadServer', {
            'add_to_news': 1,
            'link_url': url
        })

        uploaded_story = self.upload_file(
            UploadServer(server),
            file_name,
            file_path
        )

        save = self.call_api('stories.save', {
            'upload_results': uploaded_story['response']['upload_result']
        })

        stories: list[Story] = []

        for story in save.get('items', []):
            stories.append(Story(story))

        return stories

    def upload_voice(
            self,
            peer_id: int,
            file_name: str = None,
            file_path: str = None
    ):
        server = self.call_api("docs.getMessagesUploadServer", {
            "peer_id": peer_id,
            "type": "audio_message"
        })

        uploaded_voice = self.upload_file(
            UploadServer(server),
            file_name,
            file_path
        )

        save = self.call_api('docs.save', uploaded_voice)

        voice = save.get('audio_message', {})

        return AudioMessage(voice)

    def groups_get_by_id(self, group_ids: list[int], fields=None):
        if fields is None:
            fields = []

        result = self.call_api('groups.getById', {
            'group_ids': ','.join(map(str, group_ids)),
            'fields': ','.join(fields)
        })

        groups: list[Group] = []

        for group in result['groups']:
            groups.append(Group(group))

        return groups

    def upload_doc(
            self,
            peer_id: int,
            orig_name: str,
            file_name: str = None,
            file_path: str = None,
    ):
        server = self.call_api("docs.getMessagesUploadServer", {
            "peer_id": peer_id,
            "type": "doc"
        })

        uploaded_doc = self.upload_file(
            UploadServer(server),
            file_name,
            file_path
        )

        save = self.call_api('docs.save', {'title': orig_name} | uploaded_doc)

        doc = save.get('doc', {})

        return Doc(doc)

    def messages_get_history(self, count: int, peer_id: int):
        result = self.call_api('messages.getHistory', {
            'count': count,
            'peer_id': peer_id,
            'extended': 1
        })

        messages: list[Message] = []
        profiles: list[VKUser] = []

        for message in result['items']:
            messages.append(Message(message))

        for profile in result.get('profiles', []):
            profiles.append(
                VKUser(profile)
            )

        return messages, profiles

    def messages_get_by_ids(self, ids: list[int]):
        result = self.call_api('messages.getById', {
            'message_ids': ','.join(map(str, ids)),
        })

        messages: list[Message] = []

        for message in result['items']:
            messages.append(Message(message))

        return messages

    def messages_get_conversations_by_id(self, peer_id: int):
        result = self.call_api('messages.getConversationsById', {
            'peer_ids': peer_id,
        })

        conversations: list[ConversationDetails] = []

        for conversation in result['items']:
            conversations.append(
                ConversationDetails(conversation)
            )

        return conversations

    def messages_get_conversations(self, count: int, filter_: str):
        result = self.call_api('messages.getConversations', {
            'filter': filter_,
            'extended': 1,
            'count': count
        })

        conversations: list[Conversation] = []
        profiles: list[VKUser] = []

        for profile in result.get('profiles', []):
            profiles.append(
                VKUser(profile)
            )

        for conversation in result['items']:
            conversations.append(
                Conversation(conversation)
            )

        return conversations, profiles

    def call_api(self, endpoint: str, params=None):
        if params is None:
            params = {}

        params['v'] = 5.199
        params['lang'] = 'ru'
        params['https'] = 1
        params['device_id'] = self.device_id
        params['access_token'] = self.access_token

        if self.proxy is None:
            raise VKExceptions.APIError(VKError({
                'error_code': -5,
                'error_msg': 'proxy is empty'
            }))

        normalized_proxy = self._normalize_proxy(self.proxy)

        try:
            request = self.__session.post(
                f"https://api.vk.com/method/{endpoint}",
                data=params,
                headers={
                    'cache-control': 'no-cache',
                    'user-agent': self.user_agent,
                    'x-vk-android-client': 'new',
                    'accept-encoding': 'gzip',
                    'x-get-processing-time': '1',
                    'content-type': 'application/x-www-form-urlencoded; charset=utf-8'
                },
                proxies={
                    'http': normalized_proxy,
                    'https': normalized_proxy,
                },
                timeout=30
            )
        except requests.exceptions.HTTPError as e:
            raise VKExceptions.APIError(VKError({
                'error_code': -1,
                'error_msg': str(e)
            }))
        except requests.exceptions.ConnectionError as e:
            raise VKExceptions.APIError(VKError({
                'error_code': -2,
                'error_msg': str(e)
            }))
        except requests.exceptions.Timeout as e:
            raise VKExceptions.APIError(VKError({
                'error_code': -3,
                'error_msg': str(e)
            }))
        except requests.exceptions.RequestException as e:
            raise VKExceptions.APIError(VKError({
                'error_code': -4,
                'error_msg': str(e)
            }))

        json_data: dict = request.json()

        response = json_data.get('response', None)
        response_error = json_data.get('error', None)

        if response_error is not None:
            error = VKError(response_error)

            if error.code == 6:
                time.sleep(0.333)

                return self.call_api(endpoint, params)

            if error.code == 14 and not self.__try_solve_captcha:
                self.__try_solve_captcha = True

                captcha_key = None
                try:
                    print(f'API Captcha required (error 14), attempting to solve...')
                    print(f'Captcha SID: {error.captcha_sid}')
                    print(f'Captcha URL: {error.captcha_img}')

                    # Try VK-specific captcha solving with auto token extraction
                    print(f'Using VK-specific captcha solver (auto-extracting session_token)...')
                    captcha_key = self.__captcha_solver.solve_vk_captcha(
                        redirect_uri=error.captcha_img,
                        proxy=self.proxy,
                        user_agent=self.user_agent
                    )
                    print(f'VK captcha solved: {captcha_key}')
                except Exception as e:
                    print(f'VK-specific captcha solving failed: {e}')
                    # Try image solving as fallback
                    try:
                        print(f'Falling back to image solver...')
                        captcha_key = self.__captcha_solver.solve_image(image_url=error.captcha_img)
                        print(f'Image captcha solved: {captcha_key}')
                    except Exception as e2:
                        print(f'Image captcha solving also failed: {e2}')
                        captcha_key = None

                if captcha_key and str(captcha_key).strip() != '':
                    captcha_text = str(captcha_key).strip()

                    # Validate captcha solution
                    # Check for common error messages from RuCaptcha
                    invalid_responses = ['empty', 'error', 'картинка не поддерживается',
                                         'image not supported', 'unsolvable']
                    if (captcha_text.lower() in invalid_responses or
                            len(captcha_text) < 2 or
                            'не поддерживается' in captcha_text.lower()):
                        print(f'Warning: Invalid captcha solution "{captcha_text}", not retrying')
                    else:
                        params['captcha_sid'] = error.captcha_sid
                        params['captcha_key'] = captcha_text

                        return self.call_api(endpoint, params)
                else:
                    print(f'Failed to solve captcha (result: {repr(captcha_key)})')

                self.__try_solve_captcha = False
            else:
                self.__try_solve_captcha = False

            raise VKExceptions.APIError(error)

        self.__try_solve_captcha = False

        return response

    def call_api_as_group(self, endpoint: str, params=None):
        if params is None:
            params = {}

        params['v'] = 5.199
        params['lang'] = 'ru'
        params['https'] = 1
        params['access_token'] = self.access_token

        try:
            request = self.__session.post(
                f"https://api.vk.com/method/{endpoint}",
                data=params,
                timeout=8
            )
        except requests.exceptions.HTTPError as e:
            raise VKExceptions.APIError(VKError({
                'error_code': -1,
                'error_msg': str(e)
            }))
        except requests.exceptions.ConnectionError as e:
            raise VKExceptions.APIError(VKError({
                'error_code': -2,
                'error_msg': str(e)
            }))
        except requests.exceptions.Timeout as e:
            raise VKExceptions.APIError(VKError({
                'error_code': -3,
                'error_msg': str(e)
            }))
        except requests.exceptions.RequestException as e:
            raise VKExceptions.APIError(VKError({
                'error_code': -4,
                'error_msg': str(e)
            }))

        json_data: dict = request.json()

        response = json_data.get('response', None)
        response_error = json_data.get('error', None)

        if response_error is not None:
            error = VKError(response_error)

            if error.code == 6:
                time.sleep(0.333)

                return self.call_api(endpoint, params)

            raise VKExceptions.APIError(error)

        return response

    def get_device_from_ua(self):
        parse_ua = re.findall(
            'VKAndroidApp/6.25-7050 \\(Android 10; SDK (.*?); armeabi-v7a; (.*?) (.*?); en; 2160x1080\\)',
            self.user_agent
        )[0]

        class DeviceData:
            def __init__(
                    self,
                    android_version,
                    android_manufacturer,
                    android_model
            ):
                self.android_model = android_model
                self.android_manufacturer = android_manufacturer
                self.android_version = android_version

        return DeviceData(parse_ua[0], parse_ua[1], parse_ua[2])

    @staticmethod
    def create_user_agent():
        pass
