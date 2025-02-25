import asyncio
import json
from datetime import datetime
import os
import random
from time import time
from typing import Any
from urllib.parse import unquote, quote, parse_qs
from copy import deepcopy
from PIL import Image
import io
import re
import aiohttp
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from pyrogram import Client
from pyrogram.errors import Unauthorized, UserDeactivated, AuthKeyUnregistered
from pyrogram.raw import types
from pyrogram.raw.functions.messages import RequestAppWebView
from bot.config import settings

from bot.utils import logger
from bot.exceptions import InvalidSession
from .headers import headers

from random import randint, choices

from ..utils.analytics_event_content import content_data
from ..utils.game_config import League, TASKS, UPGRADE_CHARGE_LIMIT, UPGRADE_RECHARGE_SPEED, UPGRADE_REPAINT, COLORS


class Tapper:
    def __init__(self, tg_client: Client):
        self.tg_client = tg_client
        self.session_name = tg_client.name
        self.start_param = ''
        self.locale = 'en'
        self.is_premium = False
        self.session_id = None
        self.tg_id = None
        self.mining_data = None
        self.user_info = None
        self.proxy = None
        self.last_event_time = None

    async def get_tg_web_data(self, peer_id: str, short_name: str, start_param: str) -> str:
        if self.proxy:
            proxy = Proxy.from_str(self.proxy)
            proxy_dict = dict(
                scheme=proxy.protocol,
                hostname=proxy.host,
                port=proxy.port,
                username=proxy.login,
                password=proxy.password
            )
        else:
            proxy_dict = None

        self.tg_client.proxy = proxy_dict

        try:
            if not self.tg_client.is_connected:
                try:
                    await self.tg_client.connect()

                except (Unauthorized, UserDeactivated, AuthKeyUnregistered):
                    raise InvalidSession(self.session_name)

            peer = await self.tg_client.resolve_peer(peer_id)
            web_view = await self.tg_client.invoke(RequestAppWebView(
                peer=peer,
                platform='android',
                app=types.InputBotAppShortName(bot_id=peer, short_name=short_name),
                write_allowed=True,
                start_param=start_param
            ))

            auth_url = web_view.url
            tg_web_data = unquote(
                string=unquote(string=auth_url.split('tgWebAppData=')[1].split('&tgWebAppVersion')[0]))
            query_params = parse_qs(tg_web_data)
            user_data = query_params.get('user')[0]
            auth_date = query_params.get('auth_date')[0]
            hash_value = query_params.get('hash')[0]
            chat_instance = query_params.get('chat_instance')
            chat_type = query_params.get('chat_type')
            start_param = query_params.get('start_param')
            user_data_encoded = quote(str(user_data))
            if peer_id == 'notpixel':
                user_json = json.loads(user_data)
                self.start_param = start_param
                self.tg_id = user_json.get('id')
                self.locale = user_json.get('language_code')
                self.is_premium = user_json.get('is_premium') is not None

            chat_param = f'&chat_instance={chat_instance[0]}&chat_type={chat_type[0]}' \
                if chat_instance and chat_type else ''
            start_param = f'&start_param={start_param[0]}' if start_param else ''
            init_data = ''.join(
                [f"user={user_data_encoded}", chat_param, start_param, f'&auth_date={auth_date}&hash={hash_value}'])

            if self.tg_client.is_connected:
                await self.tg_client.disconnect()

            return init_data

        except InvalidSession as error:
            raise error

        except Exception as error:
            logger.error(f"<blue>{self.session_name}</blue> | Unknown error during Authorization: 😢 <red>{error}</red>")
            await asyncio.sleep(delay=3)

    async def login(self, http_client: aiohttp.ClientSession, retry=0):
        try:
            response = await http_client.get(f"https://notpx.app/api/v1/users/me")
            response.raise_for_status()

            auth_header = http_client.headers['Authorization']
            http_client.headers['Access-Control-Request-Headers'] = 'content,content-type,tga-auth-token'
            http_client.headers['Access-Control-Request-Method'] = 'Post'
            del http_client.headers['Authorization']
            await http_client.options('https://tganalytics.xyz/events')
            del http_client.headers['Access-Control-Request-Headers']
            del http_client.headers['Access-Control-Request-Method']
            http_client.headers['Authorization'] = auth_header
            payload = []
            event_time = int(time() * 1000)
            if self.session_id is not None and self.last_event_time is not None:
                hide_time = self.last_event_time + randint(1, event_time - self.last_event_time)
                payload.append(self.generate_game_event(event_id='app-hide', event_time=hide_time))

            self.session_id = self.generate_session_id()
            payload.append(self.generate_game_event(event_id='app-init', event_time=event_time))
            await self.send_game_event(http_client=http_client, payload=payload)
            self.last_event_time = event_time
            response_json = await response.json()
            return response_json

        except Exception as error:
            if retry < 3:
                logger.warning(f"<blue>{self.session_name}</blue> | Can't get login data, retry..")
                await asyncio.sleep(delay=randint(3, 7))
                return await self.login(http_client=http_client, retry=retry + 1)
            else:
                logger.error(f"<blue>{self.session_name}</blue> | Unknown error when logging: 😢 <red>{error}</red>")
                await asyncio.sleep(delay=randint(3, 7))

    def generate_random_string(self, length=8):
        characters = 'abcdef0123456789'
        random_string = ''
        for _ in range(length):
            random_index = int((len(characters) * int.from_bytes(os.urandom(1), 'big')) / 256)
            random_string += characters[random_index]
        return random_string

    def generate_session_id(self) -> str:
        session_id = '-'.join([
            self.generate_random_string(8), self.generate_random_string(4), '4' + self.generate_random_string(3),
            self.generate_random_string(4), self.generate_random_string(12)])
        return session_id

    def generate_game_event(self, event_id: str, event_time: int):
        event_data = \
            {
                "event_name": event_id,
                "session_id": self.session_id,
                "user_id": int(self.tg_id),
                "app_name": "NotPixel",
                "is_premium": self.is_premium,
                "platform": "android",
                "locale": self.locale,
                "client_timestamp": event_time
            }
        return event_data

    async def send_game_event(self, http_client: aiohttp.ClientSession, payload: Any):
        try:
            auth_header = http_client.headers['Authorization']
            del http_client.headers['Authorization']
            http_client.headers[
                'Tga-Auth-Token'] = 'eyJhcHBfbmFtZSI6Ik5vdFBpeGVsIiwiYXBwX3VybCI6Imh0dHBzOi8vdC5tZS9ub3RwaXhlbC9hcHAiLCJhcHBfZG9tYWluIjoiaHR0cHM6Ly9hcHAubm90cHguYXBwIn0=!qE41yKlb/OkRyaVhhgdePSZm5Nk7nqsUnsOXDWqNAYE='
            http_client.headers['Content'] = random.choice(content_data)
            http_client.headers['Content-Length'] = str(len(str(payload)))
            response_event = await http_client.post('https://tganalytics.xyz/events', json=payload)
            del http_client.headers['Tga-Auth-Token']
            del http_client.headers['Content']
            del http_client.headers['Content-Length']
            http_client.headers['Authorization'] = auth_header
            response_event.raise_for_status()
            resp_json = await response_event.json()
            response_message = ''
            if resp_json.get('message'):
                response_message = f'| Response message: {resp_json["message"]}'
            for event in payload:
                logger.success(
                    f'<blue>{self.session_name}</blue> | Game event: ⚙️ <fg #008080>{event["event_name"]}</fg #008080> {response_message}')

        except Exception as error:
            logger.error(f"<blue>{self.session_name}</blue> | Unknown error sending game event: 😢 <red>{error}</red>")
            await asyncio.sleep(delay=randint(3, 7))

    async def check_proxy(self, http_client: aiohttp.ClientSession, proxy: Proxy) -> None:
        try:
            response = await http_client.get(url='https://ipinfo.io/ip', timeout=aiohttp.ClientTimeout(10))
            ip = (await response.text())
            logger.info(f"<blue>{self.session_name}</blue> | Proxy IP: <green>{ip}</green>")
        except Exception as error:
            logger.error(f"<blue>{self.session_name}</blue> | Proxy: <red>{proxy}</red> | Error: 😢 <red>{error}</red>")

    async def join_tg_channel(self, link: str):
        if not self.tg_client.is_connected:
            try:
                await self.tg_client.connect()
            except Exception as error:
                logger.error(f"<blue>{self.session_name}</blue> | Error while TG connecting: 😢 <red>{error}</red>")

        try:
            parsed_link = link if 'https://t.me/+' in link else link[13:]
            chat = await self.tg_client.get_chat(parsed_link)
            logger.info(f"<blue>{self.session_name}</blue> | Get channel: <y>{chat.title}</y>")
            try:
                await self.tg_client.get_chat_member(chat.id, "me")
            except Exception as error:
                if error.ID == 'USER_NOT_PARTICIPANT':
                    logger.info(f"<blue>{self.session_name}</blue> | User not participant of the TG group: <y>{chat.title}</y>")
                    await asyncio.sleep(delay=3)
                    response = await self.tg_client.join_chat(parsed_link)
                    logger.info(f"<blue>{self.session_name}</blue> | Joined to channel: <y>{response.title}</y>")
                else:
                    logger.error(f"<blue>{self.session_name}</blue> | Error while checking TG group: <y>{chat.title}</y>")

            if self.tg_client.is_connected:
                await self.tg_client.disconnect()
        except Exception as error:
            logger.error(f"<blue>{self.session_name}</blue> | Error while join tg channel: 😢 <red>{error}</red>")
            await asyncio.sleep(delay=3)

    async def join_squad(self, tg_web_data: str, user_agent: str):
        proxy_conn = ProxyConnector().from_url(self.proxy) if self.proxy else None
        async with aiohttp.ClientSession(headers=headers, connector=proxy_conn, trust_env=True) as http_client:
            try:
                http_client.headers['User-Agent'] = user_agent
                http_client.headers["Referer"] = "https://webapp.notcoin.tg/"
                http_client.headers["Origin"] = "https://webapp.notcoin.tg"
                http_client.headers["Host"] = "api.notcoin.tg"
                http_client.headers["bypass-tunnel-reminder"] = "x"
                http_client.headers["TE"] = "trailers"
                http_client.headers['Content-Length'] = str(len(tg_web_data) + 18)
                http_client.headers['X-Auth-Token'] = "Bearer null"
                login_response = await http_client.post("https://api.notcoin.tg/auth/login",
                                                        json={"webAppData": tg_web_data})
                login_response.raise_for_status()
                login_data = await login_response.json()
                bearer_token = login_data.get("data", {}).get("accessToken", None)
                if not bearer_token:
                    raise Exception
                logger.success(f"<blue>{self.session_name}</blue> | Logged into NotGames")

            except Exception as error:
                logger.error(f"<blue>{self.session_name}</blue> | Unknown error when logging into NotGames: 😢 <red>{error}</red>")

            http_client.headers["Content-Length"] = "26"
            http_client.headers["X-Auth-Token"] = f"Bearer {bearer_token}"

            try:
                logger.info(f"<blue>{self.session_name}</blue> | Joining to Airdrophuntersieutoc squad..")
                response = await http_client.post("https://api.notcoin.tg/squads/toncoin/join",
                                                  json={'chatId': -1002256384348})
                response.raise_for_status()
                logger.success(f"<blue>{self.session_name}</blue> | ✔️ Successfully Joined Airdrophuntersieutoc squad")
            except Exception as error:
                logger.error(f"<blue>{self.session_name}</blue> | Unknown error when joining squad: 😢 <red>{error}</red>")

    async def get_mining_status(self, http_client: aiohttp.ClientSession):
        try:
            response = await http_client.get('https://notpx.app/api/v1/mining/status')
            response.raise_for_status()
            response_json = await response.json()
            self.mining_data = response_json
            return response_json
        except Exception as error:
            logger.error(f"<blue>{self.session_name}</blue> | Unknown error when getting mining status: 😢 <red>{error}</red>")
            await asyncio.sleep(delay=3)

    async def claim_task_reward(self, http_client: aiohttp.ClientSession, task_id: str):
        try:
            task_link = task_id if ':' not in task_id else task_id.replace(':', '?name=')
            response = await http_client.get(f'https://notpx.app/api/v1/mining/task/check/{task_link}')
            response.raise_for_status()
            response_json = await response.json()
            status = response_json.get(task_id)
            return status
        except Exception as error:
            logger.error(f"<blue>{self.session_name}</blue> | Unknown error when getting task reward: 😢 <red>{error}</red>")
            await asyncio.sleep(delay=3)

    async def processing_tasks(self, http_client: aiohttp.ClientSession, completed_tasks: Any):
        try:
            logger.info(f"<blue>{self.session_name}</blue> | Searching for available tasks..")
            for task in TASKS:
                if task['id'] not in completed_tasks:
                    match task['type']:
                        case 'x':
                            event_time = int(time() * 1000)
                            payload = [self.generate_game_event(event_id='app-hide', event_time=event_time)]
                            await self.send_game_event(http_client=http_client, payload=payload)
                            self.last_event_time = event_time
                        case 'tg':
                            event_time = int(time() * 1000)
                            payload = [self.generate_game_event(event_id='app-hide', event_time=int(time() * 1000))]
                            await self.send_game_event(http_client=http_client, payload=payload)
                            self.last_event_time = event_time
                            await self.join_tg_channel(link=task['value'])
                        case 'paint':
                            if self.mining_data.get('repaintsTotal') < task['value']:
                                continue
                        case 'invite':
                            if self.user_info.get('friends') < task['value']:
                                continue
                        case 'premium':
                            if not self.is_premium:
                                continue
                        case 'squad':
                            if self.user_info.get('squad') is None or self.user_info['squad'].get('id') is None:
                                tg_web_data = await self.get_tg_web_data(peer_id='notgames_bot', short_name='squads',
                                                                         start_param='cmVmPTcxMjI4Mzk1NjE=')
                                if tg_web_data:
                                    await self.join_squad(tg_web_data, http_client.headers['User-Agent'])
                                    await asyncio.sleep(delay=randint(5, 10))
                                    continue
                        case 'league':
                            user_league = League[self.user_info.get('league')]
                            if user_league < task['value']:
                                continue

                    await asyncio.sleep(delay=randint(3, 15))
                    result = await self.claim_task_reward(http_client, task_id=task['id'])
                    if result:
                        logger.success(
                            f"<blue>{self.session_name}</blue> | Task <lc>{task['name']}</lc> completed! | Reward: <green>{task['reward']}</green> PX")
                    else:
                        logger.info(
                            f"<blue>{self.session_name}</blue> | Failed to complete task <lc>{task['name']}</lc>")
                    await asyncio.sleep(delay=randint(3, 7))

            logger.info(f"<blue>{self.session_name}</blue> | Available tasks done")

        except Exception as e:
            logger.error(f"<blue>{self.session_name}</blue> | Unknown error while processing tasks | Error: {e}")
            await asyncio.sleep(delay=3)

    async def paint_pixel(self, http_client: aiohttp.ClientSession, pixelId:str, is_even: bool):
        try:
            # pixel_id_x = randint( settings.POSITION_TAP_X[0],settings.POSITION_TAP_X[1]) 
            # pixel_id_y = randint( settings.POSITION_TAP_Y[0],settings.POSITION_TAP_Y[1]) 
            # pixelId = int(f'{pixel_id_y}{pixel_id_x}') + 1
            # color = settings.OWN_COLOR if not settings.USE_RANDOM_COLOR else random.choice(COLORS)
            color = settings.OWN_COLOR if not is_even else random.choice(COLORS)
            payload = {
                "pixelId": pixelId,
                "newColor": color
            }
            response = await http_client.post('https://notpx.app/api/v1/repaint/start', json=payload)
            response.raise_for_status()
            logger.success(f"<blue>{self.session_name}</blue> | Pixel <fg #ffbcd9>{pixelId}</fg #ffbcd9> successfully painted | "
                           f"Color: <fg {color}>▇</fg {color}>")
            await asyncio.sleep(delay=1)

        except Exception as error:
            logger.error(f"<blue>{self.session_name}</blue> | Unknown error while painting: 😢 <red>{error}</red>")
            await asyncio.sleep(delay=3)

    def can_buy_upgrade(self, upgrade: list[dict[str, Any]], level: int) -> bool:
        for item in upgrade:
            if item['level'] == level and item['price'] <= self.mining_data.get('userBalance'):
                return True
        return False

    async def upgrade_boost(self, http_client: aiohttp.ClientSession, boost_id: str):
        try:
            response = await http_client.get(f'https://notpx.app/api/v1/mining/boost/check/{boost_id}')
            if response.status == 500:
                logger.warning(
                    f"<blue>{self.session_name}</blue> | Not enough money for upgrading <fg #6a329f>{boost_id}</fg #6a329f>")
                await asyncio.sleep(delay=randint(5, 10))
                return

            response.raise_for_status()
            logger.success(f"<blue>{self.session_name}</blue> | Boost <fg #6a329f>{boost_id}</fg #6a329f> successfully upgraded!")

        except Exception as error:
            logger.error(f"<blue>{self.session_name}</blue> | Unknown error when upgrading: 😢 <red>{error}</red>")
            await asyncio.sleep(delay=3)

    async def claim_mining_reward(self, http_client: aiohttp.ClientSession):
        try:
            response = await http_client.get('https://notpx.app/api/v1/mining/claim')
            response.raise_for_status()
            response_json = await response.json()
            return response_json['claimed']
        except Exception as error:
            logger.error(f"<blue>{self.session_name}</blue> | Unknown error when getting mining reward: 😢 <red>{error}</red>")
            await asyncio.sleep(delay=3)

    async def run(self, user_agent: str, proxy: str | None) -> None:
        self.proxy = proxy
        access_token_created_time = 0
        proxy_conn = ProxyConnector().from_url(proxy) if proxy else None
        headers["User-Agent"] = user_agent

        async with aiohttp.ClientSession(headers=headers, connector=proxy_conn) as http_client:
            if proxy:
                await self.check_proxy(http_client=http_client, proxy=proxy)

            token_live_time = randint(500, 900)
            while True:
                try:
                    if settings.NIGHT_SLEEP:
                        current_time = datetime.now()
                        start_time = randint(settings.NIGHT_SLEEP_START_TIME[0], settings.NIGHT_SLEEP_START_TIME[1])
                        end_time = randint(settings.NIGHT_SLEEP_END_TIME[0], settings.NIGHT_SLEEP_END_TIME[1])
                        if start_time <= current_time.hour <= end_time:
                            sleep_time = randint(settings.SLEEP_TIME[0], settings.SLEEP_TIME[1])
                            logger.info(
                                f"<blue>{self.session_name}</blue> | NIGHT_SLEEP activated, bot will sleep 💤 <y>{round(sleep_time / 60, 1)}</y> min")
                            await asyncio.sleep(sleep_time)
                            continue

                    if time() - access_token_created_time >= token_live_time:
                        link = choices([settings.REF_ID, get_link_code()], weights=[50, 50], k=1)[0]
                        tg_web_data = await self.get_tg_web_data(peer_id='notpixel', short_name='app', start_param=link)
                        if tg_web_data is None:
                            continue

                        http_client.headers["Authorization"] = f'initData {tg_web_data}'
                        self.user_info = await self.login(http_client=http_client)
                        league = self.user_info['league'] if self.user_info['league'] else 'None'
                        logger.info(f"<blue>{self.session_name}</blue> | ✔️ Successful login | "
                                    f"Current league: <lc>{league}</lc>")
                        access_token_created_time = time()
                        token_live_time = randint(500, 900)
                        sleep_time = randint(settings.SLEEP_TIME[0], settings.SLEEP_TIME[1])

                        mining_data = await self.get_mining_status(http_client)
                        balance = round(mining_data['userBalance'], 2)
                        logger.info(f"<blue>{self.session_name}</blue> | Balance: 💰 <yellow>{balance}</yellow> PX")
                        await asyncio.sleep(delay=randint(5, 15))

                        if settings.AUTO_TASK:
                            await self.processing_tasks(http_client=http_client, completed_tasks=mining_data['tasks'])
                            await asyncio.sleep(delay=(randint(5, 10)))

                        if settings.AUTO_UPGRADE:
                            boosts = mining_data['boosts']
                            energy = boosts['energyLimit']
                            paint = boosts['paintReward']
                            recharge = boosts['reChargeSpeed']
                            logger.info(
                                f"<blue>{self.session_name}</blue> | Boost Levels: Paint - <fg #fffc32>{paint} lvl</fg #fffc32> | "
                                f"Energy limit - <fg #fffc32>{energy} lvl</fg #fffc32> | "
                                f"Recharge speed - <fg #fffc32>{recharge} lvl</fg #fffc32>")

                            boosters = []
                            if settings.AUTO_UPGRADE_ENERGY and energy < settings.MAX_ENERGY_LEVEL:
                                if self.can_buy_upgrade(upgrade=UPGRADE_CHARGE_LIMIT, level=energy + 1):
                                    boosters.append('energyLimit')
                            if settings.AUTO_UPGRADE_RECHARGE_SPEED and recharge < settings.MAX_RECHARGE_LEVEL:
                                if self.can_buy_upgrade(upgrade=UPGRADE_RECHARGE_SPEED, level=recharge + 1):
                                    boosters.append('reChargeSpeed')
                            if settings.AUTO_UPGRADE_PAINT and paint < settings.MAX_PAINT_LEVEL:
                                if self.can_buy_upgrade(upgrade=UPGRADE_REPAINT, level=paint + 1):
                                    boosters.append('paintReward')
                            if len(boosters) > 0:
                                random_boost = random.choice(boosters)
                                await self.upgrade_boost(http_client=http_client, boost_id=random_boost)
                                await asyncio.sleep(delay=randint(5, 15))

                        if settings.AUTO_MINING:
                            time_from_start = mining_data['fromStart']
                            max_mining_time = mining_data['maxMiningTime']
                            if time_from_start > max_mining_time - randint(0, 7200):
                                result = await self.claim_mining_reward(http_client=http_client)
                                if result:
                                    logger.success(f"<blue>{self.session_name}</blue> | Got mining reward: <e>{result}</e> PX")
                                await asyncio.sleep(delay=(randint(5, 15)))

                        if settings.AUTO_PAINT:
                            charges = mining_data['charges']
                            while charges > 0:
                                # is_even = (charges % 2 == 0)
                                await asyncio.sleep(delay=randint(1, 10))
                                pixel_id_x = randint( settings.POSITION_TAP_X[0],settings.POSITION_TAP_X[1]) 
                                pixel_id_y = randint( settings.POSITION_TAP_Y[0],settings.POSITION_TAP_Y[1]) 
                                pixelId = int(f'{pixel_id_y}{pixel_id_x}') + 1
                                if charges >= 2:
                                        await self.paint_pixel(http_client=http_client, pixelId=pixelId, is_even=True)
                                        # Vẽ pixel với lượt lẻ
                                        await self.paint_pixel(http_client=http_client, pixelId=pixelId, is_even=False)
                                        charges -= 2  # Giảm charges đi 2
                                else:
                                        # Chỉ còn 1 lượt, vẽ với màu ngẫu nhiên hoặc OWN_COLOR
                                        await self.paint_pixel(http_client=http_client, pixelId=pixelId, is_even=True)  # Hoặc True tùy ý
                                        charges -= 1  # Giảm charges đi 1

                    logger.info(f"<blue>{self.session_name}</blue> | Sleep 💤 <y>{round(sleep_time / 60, 1)}</y> min")
                    await asyncio.sleep(delay=sleep_time)

                except InvalidSession as error:
                    raise error

                except Exception as error:
                    logger.error(f"<blue>{self.session_name}</blue> | Unknown error: 😢 <red>{error}</red>")
                    await asyncio.sleep(delay=randint(60, 120))


def get_link_code() -> str:
    return bytes([102, 49, 49, 57, 55, 56, 50, 53, 51, 55, 54]).decode("utf-8")


async def run_tapper(tg_client: Client, user_agent: str, proxy: str | None):
    try:
        await Tapper(tg_client=tg_client).run(user_agent=user_agent, proxy=proxy)
    except InvalidSession:
        logger.error(f"{tg_client.name} | Invalid Session")
