import asyncio
import json
import logging
import re
import random
import string
import os
import csv
from io import StringIO
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
    FSInputFile,
    BufferedInputFile
)

# ======================== ВЕБ-СЕРВЕР ========================
from aiohttp import web

# ======================== КОНФИГУРАЦИЯ ========================
BOT_TOKEN = "8879578713:AAGCKEyDeCejAJUzIj8fXodAe3Ln9Dk5R2w"
ADMIN_IDS = [8558292177]

# ======================== PIARFLOW ========================
PIARFLOW_API_KEY = "SKe24s0xScwQzxm0ww0mWewxOzjepHwt"
PIARFLOW_API_URL = "https://piarflow.com/v1"
PIARFLOW_MAX_SPONSORS = 6
PIARFLOW_API_KEYS = [k for k in (PIARFLOW_API_KEY,) if k]

CURRENCY_RUB = "₽"

DEFAULT_REFERRAL_REWARD_RUB = 3.0

TASK_REWARD_RUB = 0.5
MAX_TASKS_PER_DAY = 5

MIN_WITHDRAW_RUB = 40

PAYMENTS_CHANNEL = "@Galavipla"

# Штраф за отписку от спонсора PiarFlow (в рублях)
PENALTY_RUB = 1.2

INACTIVE_DAYS = 7

# ======================== ТЕКСТЫ ========================
TEXTS = {
    "welcome": "👋 Добро пожаловать в бот заработка!",
    "unsubscribed_penalty": "⚠️ Вы отписались от спонсора!\n\nШтраф: −{penalty:.2f} ₽",
    "referral_unsubscribed": "⚠️ Ваш реферал {username} отписался от спонсора.\n\nСписано: −{reward:.2f} ₽\nРефералов: −1",
    "no_referrals": "😔 У вас пока нет рефералов.\n\nПоделитесь своей ссылкой с друзьями!",
    "referral_stats": "Статистика рефералов",
    "total": "Всего",
    "active": "Активных",
    "inactive": "Неактивных",
    "promo_enter": "🎫 Введите промокод:",
    "promo_invalid": "❌ Неверный или уже использованный промокод!",
    "promo_success": "✅ Промокод активирован!\n\nНачислено: +{reward:.2f} ₽",
    "transaction_history": "История операций",
    "empty_history": "📭 История операций пока пуста.",
    "referral_list": "📋 Ваши рефералы:\n\n",
    "inactive_reminder": "⏰ Вы давно не заходили!\n\nПоявились новые задания и промокоды. Загляните в бота, чтобы заработать 🚀",
    "invite_friend": "Пригласить друга",
    "my_referrals": "Мои рефералы",
    "referral_stats_btn": "Статистика рефералов",
    "withdraw_request": "✅ Заявка на вывод принята!",
    "withdraw_pending": "⏳ Ожидайте обработки — обычно это занимает 1–12 часов.",
    "enter_amount": "💵 Введите сумму вывода:",
    "enter_details": "💳 Введите реквизиты для перевода (карта / кошелёк):",
}

# ======================== ИНИЦИАЛИЗАЦИЯ ========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ======================== БАЗА ДАННЫХ ========================
class Database:
    def __init__(self, filename="referral_bot_db.json"):
        if os.path.exists("/app/data"):
            self.filename = "/app/data/referral_bot_db.json"
            logger.info("📁 Использую Persistent Disk: /app/data")
        else:
            self.filename = filename
            logger.info(f"📁 Использую локальный файл: {filename}")

        self.data = self._load()
        self._ensure_defaults()
        self._migrate_referral_reward_paid()
        self._validate_banner()

    def save(self):
        dirname = os.path.dirname(self.filename)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def _migrate_referral_reward_paid(self):
        changed = False
        for uid, user in self.data.get("users", {}).items():
            if user.get("referred_by") is not None and "referral_reward_paid" not in user:
                user["referral_reward_paid"] = True
                changed = True
        if changed:
            self.save()

    def _validate_banner(self):
        banner_path = self.data.get("banner_path")
        if banner_path and os.path.exists(banner_path):
            try:
                if os.path.getsize(banner_path) < 100:
                    self.remove_banner()
                    logger.warning("Баннер удалён: слишком маленький файл")
                    return
                with open(banner_path, 'rb') as f:
                    header = f.read(10)
                    if header[:2] != b'\xff\xd8':
                        self.remove_banner()
                        logger.warning("Баннер удалён: неверный формат файла")
                        return
            except Exception as e:
                logger.error(f"Ошибка проверки баннера: {e}")
                self.remove_banner()
        elif banner_path:
            self.remove_banner()

    def _load(self):
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return self._default_data()

    def _default_data(self):
        return {
            "users": {},
            "referrals": {},
            "sponsors": [],
            "admins": ADMIN_IDS,
            "start_text": (
                "👋 <b>Добро пожаловать в бот заработка!</b>\n\n"
                "Здесь всё просто: выполняй задания от спонсоров, приглашай друзей "
                "и выводи заработанное на свою карту.\n\n"
                "💰 <b>За каждого приглашённого друга — {REFERRAL_REWARD_RUB:.2f} ₽</b>\n"
                "🎯 До 5 заданий в день — по 0.50 ₽ за каждое\n"
                "⚡️ Выплаты обрабатываются в течение 1–12 часов\n\n"
                "Жми «💰 Заработок», чтобы начать прямо сейчас!"
            ),
            "button_texts": {
                "earn": "Заработок",
                "referrals": "Рефералы",
                "top": "Топ",
                "profile": "Профиль",
                "withdraw": "Вывод",
                "promo": "Промокод",
                "history": "История",
                "back": "Назад",
                "invite_friend": "Пригласить друга",
                "my_referrals": "Мои рефералы",
                "referral_stats": "Статистика рефералов",
            },
            "button_emojis": {
                "earn": "💰",
                "referrals": "👥",
                "top": "🏆",
                "profile": "👤",
                "withdraw": "💳",
                "promo": "🎫",
                "history": "📜",
                "back": "⬅️",
            },
            "banner_path": None,
            "referral_reward_rub": DEFAULT_REFERRAL_REWARD_RUB,
            "withdrawals": [],
            "statistics": {
                "total_users": 0,
                "total_referrals": 0,
                "total_withdrawn": 0,
                "total_earned": 0.0,
                "total_spent": 0.0,
                "active_users": 0
            },
            "bot_stopped": False,
            "promocodes": {},
            "transactions": {},
        }

    def _ensure_defaults(self):
        changed = False
        defaults = self._default_data()
        for key in defaults:
            if key not in self.data:
                self.data[key] = defaults[key]
                changed = True
        if changed:
            self.save()

    def get_user(self, user_id: int) -> Dict:
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = {
                "id": user_id,
                "balance_rub": 0.0,
                "referral_count": 0,
                "tasks_completed": 0,
                "tasks_today": 0,
                "tasks_today_date": datetime.now().date().isoformat(),
                "referral_code": self._generate_code(user_id),
                "referred_by": None,
                "created_at": datetime.now().isoformat(),
                "is_banned": False,
                "verified_sponsors": False,
                "last_activity": datetime.now().isoformat(),
                "last_verified_date": None,
                "username": None
            }
            self.save()
        return self.data["users"][uid]

    def get_text(self, key: str, **kwargs) -> str:
        text = TEXTS.get(key, key)
        try:
            return text.format(**kwargs)
        except Exception:
            return text

    def get_button_text(self, key: str) -> str:
        return self.data.get("button_texts", {}).get(key, key.capitalize())

    def get_start_text(self) -> str:
        return self.data.get("start_text", "")

    def set_start_text(self, text: str):
        self.data["start_text"] = text
        self.save()

    def update_activity(self, user_id: int):
        user = self.get_user(user_id)
        user["last_activity"] = datetime.now().isoformat()
        self.save()

    def _generate_code(self, user_id: int) -> str:
        chars = string.ascii_uppercase + string.digits
        code = ''.join(random.choices(chars, k=8))
        for uid, user in self.data["users"].items():
            if user.get("referral_code") == code:
                return self._generate_code(user_id)
        return code

    def link_referral(self, referrer_id: int, new_user_id: int) -> bool:
        new_user = self.get_user(new_user_id)
        if new_user.get("referred_by") is not None:
            return False
        new_user["referred_by"] = referrer_id
        new_user["referral_reward_paid"] = False
        uid = str(referrer_id)
        if uid not in self.data["referrals"]:
            self.data["referrals"][uid] = []
        self.data["referrals"][uid].append(new_user_id)
        self.save()
        return True

    def confirm_referral_reward(self, new_user_id: int) -> Optional[int]:
        new_user = self.get_user(new_user_id)
        referrer_id = new_user.get("referred_by")
        if referrer_id is None:
            return None
        if new_user.get("referral_reward_paid"):
            return None
        referrer = self.get_user(referrer_id)
        reward_rub = self.get_referral_reward_rub()
        referrer["balance_rub"] += reward_rub
        referrer["referral_count"] += 1
        new_user["referral_reward_paid"] = True
        self.data["statistics"]["total_referrals"] += 1
        self.add_transaction(referrer_id, "earn", reward_rub, f"Реферал {new_user_id}")
        self.save()
        return referrer_id

    def get_referrals(self, user_id: int) -> List[int]:
        uid = str(user_id)
        return self.data["referrals"].get(uid, [])

    def add_balance(self, user_id: int, amount_rub: float):
        user = self.get_user(user_id)
        user["balance_rub"] += amount_rub
        self.data["statistics"]["total_earned"] += amount_rub
        self.save()

    def deduct_balance(self, user_id: int, amount_rub: float) -> bool:
        user = self.get_user(user_id)
        if user["balance_rub"] < amount_rub:
            return False
        user["balance_rub"] -= amount_rub
        self.data["statistics"]["total_spent"] += amount_rub
        self.save()
        return True

    def create_withdrawal(self, user_id: int, amount_rub: float, details: str) -> int:
        withdrawal_id = len(self.data["withdrawals"]) + 1
        self.data["withdrawals"].append({
            "id": withdrawal_id,
            "user_id": user_id,
            "amount_rub": amount_rub,
            "details": details,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "channel_message_id": None
        })
        self.save()
        return withdrawal_id

    def get_withdrawal(self, withdrawal_id: int) -> Optional[Dict]:
        for w in self.data["withdrawals"]:
            if w["id"] == withdrawal_id:
                return w
        return None

    def set_withdrawal_channel_message(self, withdrawal_id: int, message_id: int):
        w = self.get_withdrawal(withdrawal_id)
        if w:
            w["channel_message_id"] = message_id
            self.save()

    def set_withdrawal_status(self, withdrawal_id: int, status: str) -> bool:
        w = self.get_withdrawal(withdrawal_id)
        if not w or w["status"] != "pending":
            return False
        w["status"] = status
        if status == "paid":
            self.data["statistics"]["total_withdrawn"] += w["amount_rub"]
        elif status == "rejected":
            user = self.get_user(w["user_id"])
            user["balance_rub"] += w["amount_rub"]
        self.save()
        return True

    def _reset_daily_tasks_if_needed(self, user: Dict):
        today = datetime.now().date().isoformat()
        if user.get("tasks_today_date") != today:
            user["tasks_today_date"] = today
            user["tasks_today"] = 0

    def get_tasks_left_today(self, user_id: int) -> int:
        user = self.get_user(user_id)
        self._reset_daily_tasks_if_needed(user)
        self.save()
        return max(MAX_TASKS_PER_DAY - user.get("tasks_today", 0), 0)

    def register_task_completed(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        self._reset_daily_tasks_if_needed(user)
        if user.get("tasks_today", 0) >= MAX_TASKS_PER_DAY:
            return False
        user["tasks_today"] += 1
        user["tasks_completed"] += 1
        user["balance_rub"] += TASK_REWARD_RUB
        self.add_transaction(user_id, "earn", TASK_REWARD_RUB, "Выполнение задания")
        self.save()
        return True

    def get_top_referrals(self, limit: int = 10) -> List[Tuple[int, int]]:
        users = self.data["users"]
        top = []
        for uid, user in users.items():
            if user["referral_count"] > 0:
                top.append((int(uid), user["referral_count"]))
        top.sort(key=lambda x: x[1], reverse=True)
        return top[:limit]

    def admin_add_referrals(self, user_id: int, count: int):
        user = self.get_user(user_id)
        user["referral_count"] = max(0, user.get("referral_count", 0) + count)
        self.save()

    def admin_reset_referrals(self, user_id: int):
        user = self.get_user(user_id)
        user["referral_count"] = 0
        self.save()

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.data["admins"]

    def is_banned(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return user.get("is_banned", False)

    def add_sponsor(self, button_text: str, link: str, channel_id: str = None, order: int = 0, sponsor_type: str = "channel", channel_ids: List[str] = None):
        entry = {
            "button_text": button_text,
            "link": link,
            "order": order,
            "type": sponsor_type
        }
        if sponsor_type == "addlist" and channel_ids:
            entry["channel_ids"] = channel_ids
        else:
            entry["channel_id"] = channel_id
        self.data["sponsors"].append(entry)
        self.save()

    def remove_sponsor(self, index: int):
        if 0 <= index < len(self.data["sponsors"]):
            self.data["sponsors"].pop(index)
            self.save()

    def get_sponsors(self) -> List[Dict]:
        return sorted(self.data["sponsors"], key=lambda x: x.get("order", 0))

    def set_bot_stopped(self, stopped: bool):
        self.data["bot_stopped"] = stopped
        self.save()

    def is_bot_stopped(self) -> bool:
        return self.data.get("bot_stopped", False)

    def set_verified(self, user_id: int, value: bool = True):
        user = self.get_user(user_id)
        user["verified_sponsors"] = value
        if value:
            user["last_verified_date"] = datetime.now().isoformat()
        self.save()

    def is_verified(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return user.get("verified_sponsors", False)

    def get_last_verified_date(self, user_id: int) -> Optional[datetime]:
        user = self.get_user(user_id)
        date_str = user.get("last_verified_date")
        if date_str:
            try:
                return datetime.fromisoformat(date_str)
            except Exception:
                pass
        return None

    def get_button_emoji(self, key: str) -> str:
        return self.data.get("button_emojis", {}).get(key, "💰")

    def set_button_emoji(self, key: str, emoji: str):
        self.data["button_emojis"][key] = emoji
        self.save()

    def set_banner(self, path: str):
        self.data["banner_path"] = path
        self.save()

    def get_banner(self) -> Optional[str]:
        return self.data.get("banner_path")

    def remove_banner(self):
        banner_path = self.data.get("banner_path")
        if banner_path and os.path.exists(banner_path):
            try:
                os.remove(banner_path)
                logger.info(f"Файл баннера удалён: {banner_path}")
            except Exception as e:
                logger.error(f"Ошибка удаления баннера: {e}")
        self.data["banner_path"] = None
        self.save()

    def get_referral_reward_rub(self) -> float:
        return self.data.get("referral_reward_rub", DEFAULT_REFERRAL_REWARD_RUB)

    def set_referral_reward_rub(self, rub: float):
        self.data["referral_reward_rub"] = rub
        self.save()

    def create_promocode(self, code: str, reward: float, uses: int):
        self.data["promocodes"][code] = {
            "reward": reward,
            "uses": uses,
            "used_by": []
        }
        self.save()

    def use_promocode(self, user_id: int, code: str) -> Optional[float]:
        promo = self.data["promocodes"].get(code)
        if not promo:
            return None
        if len(promo["used_by"]) >= promo["uses"]:
            return None
        if user_id in promo["used_by"]:
            return None

        promo["used_by"].append(user_id)
        reward = promo["reward"]
        self.add_balance(user_id, reward)
        self.add_transaction(user_id, "promo", reward, f"Промокод: {code}")
        self.save()
        return reward

    def add_transaction(self, user_id: int, trans_type: str, amount: float, description: str = ""):
        uid = str(user_id)
        if uid not in self.data["transactions"]:
            self.data["transactions"][uid] = []
        self.data["transactions"][uid].append({
            "type": trans_type,
            "amount": amount,
            "date": datetime.now().isoformat(),
            "description": description
        })
        self.save()

    def get_transactions(self, user_id: int, limit: int = 20) -> List[Dict]:
        uid = str(user_id)
        return self.data["transactions"].get(uid, [])[-limit:]

    def get_referral_stats(self, user_id: int) -> Dict:
        referrals = self.get_referrals(user_id)
        active_count = 0
        for ref_id in referrals:
            user = self.get_user(ref_id)
            if user.get("tasks_completed", 0) > 0:
                active_count += 1
        return {
            "total": len(referrals),
            "active": active_count,
            "inactive": len(referrals) - active_count
        }

    def export_stats_csv(self) -> str:
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["User ID", "Balance RUB", "Referrals", "Tasks Done", "Banned", "Joined At"])
        for uid, user in self.data["users"].items():
            writer.writerow([
                uid,
                user.get("balance_rub", 0),
                user.get("referral_count", 0),
                user.get("tasks_completed", 0),
                user.get("is_banned", False),
                user.get("created_at", ""),
            ])
        return output.getvalue()

    def get_inactive_users(self, days: int = INACTIVE_DAYS) -> List[int]:
        threshold = datetime.now() - timedelta(days=days)
        inactive = []
        for uid, user in self.data["users"].items():
            if user.get("is_banned", False):
                continue
            last_activity = user.get("last_activity")
            if last_activity:
                last_date = datetime.fromisoformat(last_activity)
                if last_date < threshold:
                    inactive.append(int(uid))
        return inactive

db = Database()

# ======================== PIARFLOW API ========================
_piar_cache = {}
PIAR_CACHE_TTL = 60
_shown_piar_links: Dict[int, List[str]] = {}
_piar_link_key: Dict[str, str] = {}


async def _fetch_piar_sponsors_with_key(user_id: int, chat_id: int, api_key: str) -> Tuple[Optional[List[Dict]], int]:
    url = f"{PIARFLOW_API_URL}/sponsors"
    payload = {"user_id": user_id, "chat_id": chat_id, "max_sponsors": PIARFLOW_MAX_SPONSORS}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    all_sponsors = data.get("sponsors", [])
                    sponsors = [
                        s for s in all_sponsors
                        if s.get("status") not in ("subscribed", "not_counted")
                    ]
                    return sponsors, 200
                elif resp.status == 404:
                    return [], 404
                else:
                    logger.error(f"PiarFlow ошибка: HTTP {resp.status} user_id={user_id}")
                    return None, resp.status
    except Exception as e:
        logger.error(f"PiarFlow ошибка сети user_id={user_id}: {e}")
        return None, 0


async def fetch_piar_sponsors(user_id: int, chat_id: int) -> List[Dict]:
    if not PIARFLOW_API_KEYS:
        return []

    cache_key = f"{user_id}_{chat_id}"
    now = asyncio.get_event_loop().time()
    if cache_key in _piar_cache:
        cached_time, data = _piar_cache[cache_key]
        if now - cached_time < PIAR_CACHE_TTL:
            return data

    had_hard_error = False
    for api_key in PIARFLOW_API_KEYS:
        sponsors, status = await _fetch_piar_sponsors_with_key(user_id, chat_id, api_key)
        if sponsors is None:
            had_hard_error = True
            continue
        if sponsors:
            for s in sponsors:
                link = s.get("link")
                if link:
                    _piar_link_key[f"{user_id}:{link}"] = api_key
            _piar_cache[cache_key] = (now, sponsors)
            logger.info(f"PiarFlow: получено {len(sponsors)} активных заданий для user_id={user_id}")
            return sponsors
        logger.info(f"PiarFlow: заданий нет (status={status}) для user_id={user_id}")

    if had_hard_error:
        logger.warning(f"PiarFlow: сбой запроса для user_id={user_id} — не кэширую как 'спонсоров нет'")
        return _piar_cache.get(cache_key, (0, []))[1]

    _piar_cache[cache_key] = (now, [])
    return []


async def check_piar_sponsors(user_id: int, links: List[str]) -> bool:
    if not links:
        return True

    links_by_key: Dict[str, List[str]] = {}
    for link in links:
        api_key = _piar_link_key.get(f"{user_id}:{link}", PIARFLOW_API_KEYS[0] if PIARFLOW_API_KEYS else PIARFLOW_API_KEY)
        links_by_key.setdefault(api_key, []).append(link)

    for api_key, key_links in links_by_key.items():
        url = f"{PIARFLOW_API_URL}/sponsors/check"
        payload = {"user_id": user_id, "links": key_links}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        sponsors = data.get("sponsors", [])
                        if not all(s.get("status") in ["subscribed", "not_counted"] for s in sponsors):
                            return False
                    elif resp.status == 404:
                        logger.info(f"PiarFlow check: 404 (заданий нет) для user_id={user_id}")
                        continue
                    else:
                        logger.error(f"PiarFlow check ошибка: HTTP {resp.status} user_id={user_id}")
                        return False
        except Exception as e:
            logger.error(f"PiarFlow check ошибка сети user_id={user_id}: {e}")
            return False

    return True

async def check_manual_sponsors(user_id: int) -> bool:
    for sponsor in db.get_sponsors():
        sponsor_type = sponsor.get("type", "channel")
        if sponsor_type == "channel":
            if "channel_id" in sponsor and sponsor["channel_id"]:
                if not await check_channel_subscription(user_id, sponsor["channel_id"]):
                    return False
        elif sponsor_type == "addlist":
            for cid in sponsor.get("channel_ids", []):
                if not await check_channel_subscription(user_id, cid):
                    return False
    return True

async def check_channel_subscription(user_id: int, channel_id: str) -> bool:
    try:
        if channel_id.startswith("@"):
            channel_id = channel_id[1:]
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

async def check_all_subscriptions(user_id: int, chat_id: int, use_shown_links: bool = False) -> bool:
    manual_task = asyncio.create_task(check_manual_sponsors(user_id))

    if use_shown_links and user_id in _shown_piar_links:
        links = _shown_piar_links[user_id]
        manual_ok = await manual_task
        if not manual_ok:
            return False
        if links:
            return await check_piar_sponsors(user_id, links)
        return True

    piar_task = asyncio.create_task(fetch_piar_sponsors(user_id, chat_id))

    manual_ok = await manual_task
    if not manual_ok:
        return False

    piar_sponsors = await piar_task
    links = [s.get("link") for s in piar_sponsors if s.get("link")]
    if links:
        return await check_piar_sponsors(user_id, links)

    return True

def invalidate_piar_cache(user_id: int):
    for key in list(_piar_cache.keys()):
        if key.startswith(str(user_id)):
            del _piar_cache[key]

# ======================== ВЕБХУК ОТ PIARFLOW ========================
_processed_webhooks = set()

async def piarflow_webhook(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        logger.info(f"📨 Получен вебхук от PiarFlow: {payload}")

        if payload.get("test"):
            logger.info("🧪 Тестовый вебхук от PiarFlow — успешно!")
            return web.json_response({"ok": True})

        if payload.get("status") != "unsubscribed":
            return web.json_response({"ok": True})

        tg_user_id = int(payload.get("tg_user_id"))
        offer_link = payload.get("offer_link", "")

        webhook_key = f"{tg_user_id}_{offer_link}_{datetime.now().strftime('%Y%m%d%H%M')}"
        if webhook_key in _processed_webhooks:
            logger.info(f"⏭️ Вебхук уже обработан: {webhook_key}")
            return web.json_response({"ok": True})
        _processed_webhooks.add(webhook_key)

        if len(_processed_webhooks) > 1000:
            _processed_webhooks.clear()

        await handle_unsubscribe_from_webhook(tg_user_id, offer_link)

        return web.json_response({"ok": True})

    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}")
        return web.json_response({"ok": False}, status=500)

async def handle_unsubscribe_from_webhook(user_id: int, offer_link: str):
    try:
        user = db.get_user(user_id)
        if not user:
            logger.warning(f"⚠️ Пользователь {user_id} не найден в базе")
            return

        if not user.get("verified_sponsors", False):
            logger.info(f"ℹ️ Пользователь {user_id} не был верифицирован")
            return

        last_verified = db.get_last_verified_date(user_id)
        if last_verified:
            days_passed = (datetime.now() - last_verified).days
            if days_passed > 7:
                logger.info(f"ℹ️ Прошло {days_passed} дней > 7, не штрафуем")
                return

        logger.info(f"⚠️ Пользователь {user_id} отписался от {offer_link}")

        # ===== ШТРАФ ДЛЯ РЕФЕРАЛА =====
        penalty_rub = PENALTY_RUB
        user["balance_rub"] -= penalty_rub
        db.add_transaction(user_id, "penalty", -penalty_rub, "Штраф за отписку от спонсора")
        db.set_verified(user_id, False)
        db.save()

        # ===== ШТРАФ ДЛЯ РЕФЕРЕРА =====
        referrer_id = user.get("referred_by")
        if referrer_id is not None:
            referrer = db.get_user(referrer_id)
            reward_rub = db.get_referral_reward_rub()
            referrer["balance_rub"] -= reward_rub
            referrer["referral_count"] = max(0, referrer.get("referral_count", 0) - 1)
            db.add_transaction(referrer_id, "penalty", -reward_rub, f"Штраф за отписку реферала {user_id}")
            db.save()

            try:
                username = user.get("username") or f"ID {user_id}"
                text = db.get_text("referral_unsubscribed", username=username, reward=reward_rub)
                await bot.send_message(referrer_id, text)
            except Exception as e:
                logger.error(f"Ошибка уведомления реферера: {e}")

        try:
            text = db.get_text("unsubscribed_penalty", penalty=penalty_rub)
            await bot.send_message(user_id, text)
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя: {e}")

    except Exception as e:
        logger.error(f"Ошибка handle_unsubscribe_from_webhook: {e}")

# ======================== ЗАПУСК ВЕБ-СЕРВЕРА ========================
async def run_web_server():
    try:
        app = web.Application()

        async def handle_ping(request):
            return web.Response(text="OK", status=200)

        app.router.add_get("/", handle_ping)
        app.router.add_get("/ping", handle_ping)
        app.router.add_get("/health", handle_ping)
        app.router.add_post("/webhook/piarflow", piarflow_webhook)

        runner = web.AppRunner(app)
        await runner.setup()

        port = int(os.environ.get("PORT", 10000))
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()

        logger.info(f"✅ Веб-сервер запущен на порту {port}")
        logger.info(f"✅ Проверьте: http://0.0.0.0:{port}/ping")

        await asyncio.Event().wait()

    except Exception as e:
        logger.error(f"❌ Ошибка веб-сервера: {e}")
        import traceback
        logger.error(traceback.format_exc())

# ======================== КЛАВИАТУРЫ ========================
def create_button(text: str, emoji: str = "", callback_data: str = None, url: str = None, style: str = None) -> InlineKeyboardButton:
    display = f"{emoji} {text}".strip() if emoji else text
    kwargs = {"text": display}
    if url:
        kwargs["url"] = url
    else:
        kwargs["callback_data"] = callback_data
    if style:
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)

def create_sponsor_keyboard(piar_sponsors: List[Dict] = None) -> Optional[InlineKeyboardMarkup]:
    sponsors = db.get_sponsors()
    piar_sponsors = piar_sponsors or []

    if not sponsors and not piar_sponsors:
        return None

    keyboard = []

    row = []
    for sponsor in sponsors:
        row.append(create_button(sponsor["button_text"], "📢", url=sponsor["link"], style="primary"))
        if len(row) >= 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    row = []
    for s in piar_sponsors:
        link = s.get("link")
        if not link:
            continue
        row.append(create_button("Канал", "📢", url=link, style="primary"))
        if len(row) >= 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([create_button("Я подписался, проверить", "✅", callback_data="verify_sponsors", style="success")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def main_menu_keyboard() -> InlineKeyboardMarkup:
    btn_earn = db.get_button_text("earn")
    btn_referrals = db.get_button_text("referrals")
    btn_top = db.get_button_text("top")
    btn_profile = db.get_button_text("profile")
    btn_withdraw = db.get_button_text("withdraw")
    btn_promo = db.get_button_text("promo")
    btn_history = db.get_button_text("history")

    emoji_earn = db.get_button_emoji("earn")
    emoji_referrals = db.get_button_emoji("referrals")
    emoji_top = db.get_button_emoji("top")
    emoji_profile = db.get_button_emoji("profile")
    emoji_withdraw = db.get_button_emoji("withdraw")
    emoji_promo = db.get_button_emoji("promo")
    emoji_history = db.get_button_emoji("history")

    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(btn_earn, emoji_earn, callback_data="menu_earn", style="primary")],
        [
            create_button(btn_referrals, emoji_referrals, callback_data="menu_referrals", style="primary"),
            create_button(btn_top, emoji_top, callback_data="menu_top", style="primary"),
        ],
        [create_button(btn_profile, emoji_profile, callback_data="menu_profile", style="primary")],
        [create_button(btn_withdraw, emoji_withdraw, callback_data="menu_withdraw", style="success")],
        [
            create_button(btn_promo, emoji_promo, callback_data="menu_promo", style="primary"),
            create_button(btn_history, emoji_history, callback_data="menu_history", style="primary"),
        ],
    ])

def earn_keyboard(has_offer: bool) -> InlineKeyboardMarkup:
    btn_back = db.get_button_text("back")
    btn_referrals = db.get_button_text("referrals")
    emoji_referrals = db.get_button_emoji("referrals")
    emoji_back = db.get_button_emoji("back")

    rows = []
    if has_offer:
        rows.append([create_button("Проверить выполнение", "🔄", callback_data="check_task", style="success")])
    rows.append([
        create_button(btn_referrals, emoji_referrals, callback_data="menu_referrals", style="primary"),
        create_button(btn_back, emoji_back, callback_data="menu_main", style="primary"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def referral_keyboard(user_id: int) -> InlineKeyboardMarkup:
    btn_back = db.get_button_text("back")
    btn_invite = db.get_button_text("invite_friend")
    btn_my_refs = db.get_button_text("my_referrals")
    btn_stats = db.get_button_text("referral_stats")
    emoji_back = db.get_button_emoji("back")

    bot_username = bot.username if hasattr(bot, 'username') and bot.username else "your_bot"
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(btn_invite, "📨", url=f"tg://msg?text=Присоединяйся к боту для заработка!\n{link}", style="primary")],
        [create_button(btn_my_refs, "📋", callback_data="referrals_list", style="primary")],
        [create_button(btn_stats, "📊", callback_data="referrals_stats", style="primary")],
        [create_button(btn_back, emoji_back, callback_data="menu_main", style="primary")]
    ])

def profile_keyboard() -> InlineKeyboardMarkup:
    btn_back = db.get_button_text("back")
    btn_withdraw = db.get_button_text("withdraw")
    btn_history = db.get_button_text("history")
    emoji_back = db.get_button_emoji("back")
    emoji_withdraw = db.get_button_emoji("withdraw")
    emoji_history = db.get_button_emoji("history")

    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(btn_withdraw, emoji_withdraw, callback_data="menu_withdraw", style="success")],
        [create_button(btn_history, emoji_history, callback_data="menu_history", style="primary")],
        [create_button(btn_back, emoji_back, callback_data="menu_main", style="primary")]
    ])

def withdraw_keyboard() -> InlineKeyboardMarkup:
    btn_back = db.get_button_text("back")
    emoji_back = db.get_button_emoji("back")

    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Вывести средства", "💵", callback_data="withdraw_rub", style="success")],
        [create_button(btn_back, emoji_back, callback_data="menu_main", style="primary")]
    ])

def back_keyboard(callback_data: str = "menu_main") -> InlineKeyboardMarkup:
    btn_back = db.get_button_text("back")
    emoji_back = db.get_button_emoji("back")
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(btn_back, emoji_back, callback_data=callback_data, style="primary")]
    ])

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button("Статистика", "📊", callback_data="admin_stats", style="primary"),
            create_button("Пользователи", "👥", callback_data="admin_users", style="primary")
        ],
        [create_button("Текст приветствия", "📝", callback_data="admin_start_text", style="primary")],
        [create_button("Спонсоры/Задания", "📺", callback_data="admin_sponsors", style="primary")],
        [
            create_button("Текст кнопок", "🧩", callback_data="admin_button_texts", style="primary"),
            create_button("Эмодзи кнопок", "🎨", callback_data="admin_button_emojis", style="primary")
        ],
        [
            create_button("Баннер", "🖼", callback_data="admin_banner", style="primary"),
            create_button("Награда реферала", "🎁", callback_data="admin_referral_reward", style="primary")
        ],
        [
            create_button("Промокоды", "🎫", callback_data="admin_promocode", style="primary"),
            create_button("Экспорт CSV", "📁", callback_data="admin_export_csv", style="primary")
        ],
        [
            create_button("Рассылка", "📢", callback_data="admin_broadcast", style="primary"),
            create_button("Бан/Разбан", "🚫", callback_data="admin_ban", style="danger")
        ],
        [
            create_button("Статус бота", "⚙️", callback_data="admin_status", style="primary"),
            create_button("PiarFlow debug", "🔍", callback_data="admin_piarflow_debug", style="primary")
        ],
        [create_button("Назад", "⬅️", callback_data="menu_main", style="primary")]
    ])

def admin_sponsors_keyboard() -> InlineKeyboardMarkup:
    sponsors = db.get_sponsors()
    keyboard = []

    for i, sponsor in enumerate(sponsors):
        s_type = sponsor.get("type", "channel")
        type_emoji = "📺" if s_type == "channel" else "📋" if s_type == "addlist" else "🔘"
        display = f"{i+1}. {type_emoji} {sponsor['button_text']}"
        keyboard.append([
            create_button(display[:50], callback_data=f"admin_sponsor_{i}", style="primary"),
            create_button("🗑", callback_data=f"admin_sponsor_del_{i}", style="danger")
        ])

    keyboard.append([create_button("➕ Добавить спонсора", "", callback_data="admin_sponsor_add", style="success")])
    keyboard.append([create_button("Назад", "⬅️", callback_data="admin_panel", style="primary")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def admin_button_texts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(f"💰 {db.data.get('button_texts', {}).get('earn', 'Заработок')}", "", callback_data="admin_btn_earn", style="primary")],
        [create_button(f"👥 {db.data.get('button_texts', {}).get('referrals', 'Рефералы')}", "", callback_data="admin_btn_referrals", style="primary")],
        [create_button(f"🏆 {db.data.get('button_texts', {}).get('top', 'Топ')}", "", callback_data="admin_btn_top", style="primary")],
        [create_button(f"👤 {db.data.get('button_texts', {}).get('profile', 'Профиль')}", "", callback_data="admin_btn_profile", style="primary")],
        [create_button(f"💳 {db.data.get('button_texts', {}).get('withdraw', 'Вывод')}", "", callback_data="admin_btn_withdraw", style="primary")],
        [create_button("Назад", "⬅️", callback_data="admin_panel", style="primary")]
    ])

def admin_button_emojis_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button(f"💰 {db.data.get('button_texts', {}).get('earn', 'Заработок')}", "", callback_data="admin_emoji_earn", style="primary")],
        [create_button(f"👥 {db.data.get('button_texts', {}).get('referrals', 'Рефералы')}", "", callback_data="admin_emoji_referrals", style="primary")],
        [create_button(f"🏆 {db.data.get('button_texts', {}).get('top', 'Топ')}", "", callback_data="admin_emoji_top", style="primary")],
        [create_button(f"👤 {db.data.get('button_texts', {}).get('profile', 'Профиль')}", "", callback_data="admin_emoji_profile", style="primary")],
        [create_button(f"💳 {db.data.get('button_texts', {}).get('withdraw', 'Вывод')}", "", callback_data="admin_emoji_withdraw", style="primary")],
        [create_button("Назад", "⬅️", callback_data="admin_panel", style="primary")]
    ])

def admin_banner_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Загрузить баннер", "📤", callback_data="banner_upload", style="success")],
        [create_button("Удалить баннер", "🗑", callback_data="banner_delete", style="danger")],
        [create_button("Назад", "⬅️", callback_data="admin_panel", style="primary")]
    ])

def admin_referral_reward_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Изменить награду (₽)", "🎁", callback_data="admin_reward_rub", style="primary")],
        [create_button("Назад", "⬅️", callback_data="admin_panel", style="primary")]
    ])

def admin_users_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    users = list(db.data["users"].values())
    per_page = 10
    total_pages = max((len(users) + per_page - 1) // per_page, 1)

    start = page * per_page
    end = min(start + per_page, len(users))
    current_users = users[start:end]

    keyboard = []
    for user in current_users:
        status = "🚫" if user.get("is_banned", False) else "✅"
        keyboard.append([create_button(f"{status} ID {user['id']}", callback_data=f"admin_user_{user['id']}", style="primary")])

    nav = []
    if page > 0:
        nav.append(create_button("⬅️", callback_data=f"admin_users_page_{page-1}", style="primary"))
    nav.append(create_button(f"{page+1}/{total_pages}", callback_data="admin_users_page_info", style="primary"))
    if page < total_pages - 1:
        nav.append(create_button("➡️", callback_data=f"admin_users_page_{page+1}", style="primary"))
    if nav:
        keyboard.append(nav)

    keyboard.append([create_button("Назад", "⬅️", callback_data="admin_panel", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def user_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button("+Баланс", "➕", callback_data=f"admin_user_add_balance_{user_id}", style="success"),
            create_button("-Баланс", "➖", callback_data=f"admin_user_sub_balance_{user_id}", style="danger")
        ],
        [
            create_button("+Рефералы", "👥", callback_data=f"admin_user_add_ref_{user_id}", style="success"),
            create_button("Обнулить рефералы", "🔄", callback_data=f"admin_user_reset_ref_{user_id}", style="danger")
        ],
        [
            create_button("Бан", "🚫", callback_data=f"admin_user_ban_{user_id}", style="danger"),
            create_button("Разбан", "✅", callback_data=f"admin_user_unban_{user_id}", style="success")
        ],
        [create_button("Назад", "⬅️", callback_data="admin_users", style="primary")]
    ])

# ======================== СОСТОЯНИЯ FSM ========================
class AdminStates(StatesGroup):
    waiting_for_start_text = State()
    waiting_for_sponsor_name = State()
    waiting_for_sponsor_link = State()
    waiting_for_broadcast_text = State()
    waiting_for_balance_amount = State()
    waiting_for_referral_count = State()
    waiting_for_ban_user = State()
    waiting_for_button_text = State()
    waiting_for_button_emoji = State()
    waiting_for_banner = State()
    waiting_for_reward_rub = State()
    waiting_for_promocode = State()

class WithdrawStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_details = State()

class UserStates(StatesGroup):
    waiting_for_promo = State()

# ======================== ХЕЛПЕРЫ ========================
async def is_banner_valid() -> bool:
    banner_path = db.get_banner()
    if not banner_path:
        return False
    if not os.path.exists(banner_path):
        db.remove_banner()
        return False
    try:
        if os.path.getsize(banner_path) < 100:
            db.remove_banner()
            return False
        with open(banner_path, 'rb') as f:
            header = f.read(10)
            if header[:2] != b'\xff\xd8':
                db.remove_banner()
                return False
        return True
    except Exception:
        db.remove_banner()
        return False

async def safe_edit_or_send(callback_message: Message, text: str, reply_markup: InlineKeyboardMarkup = None):
    if callback_message.photo:
        if len(text) <= 1024:
            try:
                await callback_message.edit_caption(caption=text, reply_markup=reply_markup)
                return
            except Exception as e:
                logger.error(f"Ошибка edit_caption: {e}")

        chat_id = callback_message.chat.id
        try:
            await callback_message.delete()
        except Exception:
            pass
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
    else:
        try:
            await callback_message.edit_text(text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Ошибка edit_text: {e}")
            try:
                await bot.send_message(callback_message.chat.id, text, reply_markup=reply_markup)
            except Exception as e2:
                logger.error(f"Финальная ошибка отправки: {e2}")

async def send_main_menu(target_message: Message, user_id: int, edit: bool = False):
    start_text = db.get_start_text()
    reward_rub = db.get_referral_reward_rub()

    try:
        text = start_text.format(REFERRAL_REWARD_RUB=reward_rub)
    except Exception:
        text = start_text

    kb = main_menu_keyboard()

    if await is_banner_valid():
        try:
            photo = FSInputFile(db.get_banner())
            if edit:
                await safe_edit_or_send(target_message, text, kb)
            else:
                await target_message.answer_photo(photo=photo, caption=text, reply_markup=kb)
            return
        except Exception as e:
            logger.error(f"Ошибка отправки баннера: {e}")
            db.remove_banner()

    if edit:
        await safe_edit_or_send(target_message, text, kb)
    else:
        await target_message.answer(text, reply_markup=kb)

async def send_sponsors_gate(target_message: Message, user_id: int, chat_id: int, edit: bool = False):
    piar_sponsors = await fetch_piar_sponsors(user_id, chat_id)
    manual_sponsors = db.get_sponsors()
    logger.info(f"[sponsors_gate] user_id={user_id} manual={len(manual_sponsors)} piar={len(piar_sponsors)}")

    _shown_piar_links[user_id] = [s.get("link") for s in piar_sponsors if s.get("link")]

    text = "🔒 Для доступа к боту подпишитесь на все каналы ниже, затем нажмите «Проверить»."
    kb = create_sponsor_keyboard(piar_sponsors)

    if edit:
        await safe_edit_or_send(target_message, text, kb)
    else:
        await target_message.answer(text, reply_markup=kb)

async def user_needs_gate(user_id: int, chat_id: int) -> bool:
    if db.is_verified(user_id):
        return False
    if db.get_sponsors():
        return True
    piar_sponsors = await fetch_piar_sponsors(user_id, chat_id)
    return bool(piar_sponsors)

async def try_confirm_referral(user_id: int):
    referrer_id = db.confirm_referral_reward(user_id)
    if referrer_id is None:
        return
    try:
        reward_rub = db.get_referral_reward_rub()
        text = f"🎉 Новый реферал!\n\n💰 Вам начислено: +{reward_rub:.2f} ₽"
        await bot.send_message(referrer_id, text)
    except Exception:
        pass

# ======================== ФОНОВЫЕ ЗАДАЧИ ========================
async def check_inactive_users():
    while True:
        try:
            inactive_users = db.get_inactive_users(INACTIVE_DAYS)
            for user_id in inactive_users:
                try:
                    text = db.get_text("inactive_reminder")
                    await bot.send_message(user_id, text, parse_mode="HTML")
                    db.update_activity(user_id)
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
            await asyncio.sleep(3600 * 12)
        except Exception as e:
            logger.error(f"Ошибка в check_inactive_users: {e}")
            await asyncio.sleep(60)

# ======================== ОБРАБОТЧИКИ КОМАНД ========================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    args = message.text.split()

    if db.is_banned(user_id):
        await message.answer("🚫 Вы забанены в этом боте!")
        return

    if db.is_bot_stopped():
        await message.answer("⏸ Бот временно не работает. Попробуйте позже.")
        return

    db.get_user(user_id)
    db.update_activity(user_id)

    if len(args) > 1 and args[1].startswith("ref_"):
        ref_part = args[1][4:]
        if ref_part.isdigit():
            referrer_id = int(ref_part)
            if referrer_id != user_id and str(referrer_id) in db.data["users"]:
                db.link_referral(referrer_id, user_id)

    needs_gate = await user_needs_gate(user_id, message.chat.id)
    if needs_gate:
        await send_sponsors_gate(message, user_id, message.chat.id)
        return

    await try_confirm_referral(user_id)
    await send_main_menu(message, user_id)

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    user_id = message.from_user.id
    if not db.is_admin(user_id):
        await message.answer("⛔ У вас нет доступа к админ-панели.")
        return
    await message.answer("🛠 Админ-панель", reply_markup=admin_panel_keyboard())

# ======================== ПРОВЕРКА ПОДПИСКИ ========================
@dp.callback_query(F.data == "verify_sponsors")
async def verify_sponsors(callback: CallbackQuery):
    user_id = callback.from_user.id

    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    ok = await check_all_subscriptions(user_id, callback.message.chat.id, use_shown_links=True)

    if ok:
        db.set_verified(user_id, True)
        _shown_piar_links.pop(user_id, None)
        await callback.answer("✅ Подписка подтверждена!")
        await try_confirm_referral(user_id)
        await send_main_menu(callback.message, user_id, edit=True)
    else:
        await callback.answer(
            "❌ Вы подписаны не на все каналы!\nПроверьте подписки и нажмите снова.",
            show_alert=True
        )

@dp.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()

# ======================== ГЛАВНОЕ МЕНЮ ========================
@dp.callback_query(F.data == "menu_main")
async def menu_main(callback: CallbackQuery):
    user_id = callback.from_user.id

    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    db.update_activity(user_id)

    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return

    await send_main_menu(callback.message, user_id, edit=True)
    await callback.answer()

# ======================== ЗАРАБОТОК ========================
@dp.callback_query(F.data == "menu_earn")
async def menu_earn(callback: CallbackQuery):
    user_id = callback.from_user.id

    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    db.update_activity(user_id)

    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return

    tasks_left = db.get_tasks_left_today(user_id)

    if tasks_left <= 0:
        text = (
            f"⏳ <b>Заработок на сегодня</b>\n\n"
            f"Вы выполнили максимум заданий на сегодня ({MAX_TASKS_PER_DAY}/{MAX_TASKS_PER_DAY}).\n"
            f"Новые задания появятся после полуночи."
        )
        await safe_edit_or_send(callback.message, text, earn_keyboard(has_offer=False))
        await callback.answer()
        return

    sponsors = db.get_sponsors()

    task_sponsor = None
    for sponsor in sponsors:
        channel = sponsor.get("channel_id")
        if channel:
            is_subscribed = await check_channel_subscription(user_id, channel)
            if not is_subscribed:
                task_sponsor = sponsor
                break

    if not task_sponsor:
        if sponsors:
            text = (
                f"✅ <b>Все задания выполнены!</b>\n\n"
                f"Вы подписаны на все каналы.\n"
                f"Осталось заданий сегодня: {tasks_left}/{MAX_TASKS_PER_DAY}"
            )
            await safe_edit_or_send(callback.message, text, earn_keyboard(has_offer=False))
            await callback.answer()
            return

        text = "😔 <b>Сейчас нет доступных заданий</b>\n\nПопробуйте позже — новые задания появляются регулярно."
        await safe_edit_or_send(callback.message, text, earn_keyboard(has_offer=False))
        await callback.answer()
        return

    link = task_sponsor.get("link", "")
    title = task_sponsor.get("button_text", "Задание")

    text = (
        f"💰 <b>Заработок</b>\n\n"
        f"📋 Задание: {title}\n"
        f"💵 Награда: {TASK_REWARD_RUB:.2f} ₽\n"
        f"📊 Осталось заданий сегодня: {tasks_left}/{MAX_TASKS_PER_DAY}\n\n"
        f"Подпишитесь по кнопке ниже, затем нажмите «Проверить выполнение»."
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [create_button("Подписаться", "✅", url=link, style="primary")],
        [create_button("Проверить выполнение", "🔄", callback_data="check_task", style="success")],
        [create_button(db.get_button_text("back"), db.get_button_emoji("back"), callback_data="menu_main", style="primary")]
    ])

    await safe_edit_or_send(callback.message, text, keyboard)
    await callback.answer()

@dp.callback_query(F.data == "check_task")
async def check_task(callback: CallbackQuery):
    user_id = callback.from_user.id

    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    tasks_left = db.get_tasks_left_today(user_id)
    if tasks_left <= 0:
        await callback.answer("Лимит заданий на сегодня исчерпан.", show_alert=True)
        return

    sponsors = db.get_sponsors()
    all_subscribed = True
    failed = []

    for sponsor in sponsors:
        channel = sponsor.get("channel_id")
        if channel:
            if not await check_channel_subscription(user_id, channel):
                all_subscribed = False
                failed.append(sponsor.get("button_text"))

    if all_subscribed:
        counted = db.register_task_completed(user_id)
        if counted:
            new_left = db.get_tasks_left_today(user_id)
            text = (
                f"✅ <b>Задание выполнено!</b>\n\n"
                f"💵 Начислено: +{TASK_REWARD_RUB:.2f} ₽\n"
                f"Осталось заданий сегодня: {new_left}/{MAX_TASKS_PER_DAY}"
            )
            await safe_edit_or_send(callback.message, text, earn_keyboard(has_offer=new_left > 0))
            await callback.answer("✅ Награда начислена!")
        else:
            await callback.answer("Лимит заданий на сегодня исчерпан.", show_alert=True)
    else:
        failed_text = "\n".join(f"• {f}" for f in failed)
        await callback.answer(f"❌ Вы подписаны не на все каналы:\n{failed_text}", show_alert=True)

# ======================== РЕФЕРАЛЫ ========================
@dp.callback_query(F.data == "menu_referrals")
async def menu_referrals(callback: CallbackQuery):
    user_id = callback.from_user.id

    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    db.update_activity(user_id)

    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return

    user = db.get_user(user_id)
    count = user["referral_count"]
    reward_rub = db.get_referral_reward_rub()
    bot_username = bot.username if hasattr(bot, 'username') and bot.username else "your_bot"
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"

    text = (
        f"👥 <b>Реферальная система</b>\n\n"
        f"🆔 Ваш ID: {user_id}\n"
        f"🔗 Ваша ссылка (нажмите, чтобы скопировать):\n"
        f"<code>{ref_link}</code>\n\n"
        f"💰 Приглашено: {count} чел.\n"
        f"🚀 Заработано: {count * reward_rub:.2f} ₽\n\n"
        f"Поделитесь ссылкой с друзьями и получайте бонусы!"
    )

    await safe_edit_or_send(callback.message, text, referral_keyboard(user_id))
    await callback.answer()

@dp.callback_query(F.data == "referrals_list")
async def referrals_list(callback: CallbackQuery):
    user_id = callback.from_user.id
    referrals = db.get_referrals(user_id)

    if not referrals:
        await callback.answer(db.get_text("no_referrals"), show_alert=True)
        return

    text = db.get_text("referral_list")
    for i, ref_id in enumerate(referrals, 1):
        try:
            chat = await bot.get_chat(ref_id)
            name = chat.full_name or chat.username or str(ref_id)
            text += f"{i}. {name}\n"
        except Exception:
            text += f"{i}. {ref_id}\n"
    text += f"\n{db.get_text('total')}: {len(referrals)}"

    await safe_edit_or_send(callback.message, text, back_keyboard("menu_referrals"))
    await callback.answer()

@dp.callback_query(F.data == "referrals_stats")
async def referrals_stats(callback: CallbackQuery):
    user_id = callback.from_user.id
    stats = db.get_referral_stats(user_id)

    text = (
        f"📊 <b>{db.get_text('referral_stats')}</b>\n\n"
        f"{db.get_text('total')}: {stats['total']}\n"
        f"{db.get_text('active')}: {stats['active']}\n"
        f"{db.get_text('inactive')}: {stats['inactive']}\n"
    )

    await safe_edit_or_send(callback.message, text, back_keyboard("menu_referrals"))
    await callback.answer()

# ======================== ТОП ========================
@dp.callback_query(F.data == "menu_top")
async def menu_top(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    db.update_activity(user_id)

    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return

    top = db.get_top_referrals(10)
    text = "🏆 <b>Топ рефералов</b>\n\n"

    if not top:
        text += "Пока нет лидеров. Будьте первым!"
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, count) in enumerate(top, 1):
            medal = medals[i-1] if i <= 3 else f"{i}."
            try:
                chat = await bot.get_chat(uid)
                name = chat.full_name or chat.username or str(uid)
            except Exception:
                name = str(uid)
            text += f"{medal} {name[:20]} — {count} реф.\n"

    await safe_edit_or_send(callback.message, text, back_keyboard("menu_main"))
    await callback.answer()

# ======================== ПРОФИЛЬ ========================
@dp.callback_query(F.data == "menu_profile")
async def menu_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    db.update_activity(user_id)

    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return

    user = db.get_user(user_id)
    tasks_left = db.get_tasks_left_today(user_id)

    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: {user_id}\n\n"
        f"💰 Баланс: {user['balance_rub']:.2f} ₽\n\n"
        f"👥 Рефералов: {user['referral_count']}\n"
        f"🚀 Заданий выполнено всего: {user['tasks_completed']}\n"
        f"📊 Заданий сегодня: {MAX_TASKS_PER_DAY - tasks_left}/{MAX_TASKS_PER_DAY}"
    )

    await safe_edit_or_send(callback.message, text, profile_keyboard())
    await callback.answer()

# ======================== ИСТОРИЯ ТРАНЗАКЦИЙ ========================
@dp.callback_query(F.data == "menu_history")
async def menu_history(callback: CallbackQuery):
    user_id = callback.from_user.id
    transactions = db.get_transactions(user_id, 20)

    if not transactions:
        text = db.get_text("empty_history")
    else:
        text = f"📜 <b>{db.get_text('transaction_history')}</b>\n\n"
        for t in reversed(transactions):
            emoji = "💰" if t["type"] in ["earn", "promo"] else "💸" if t["type"] == "withdraw" else "📉"
            sign = "+" if t["amount"] > 0 else ""
            text += f"{emoji} {t['date'][:10]} {sign}{t['amount']:.2f} ₽ — {t.get('description', t['type'])}\n"

    await safe_edit_or_send(callback.message, text, back_keyboard("menu_profile"))
    await callback.answer()

# ======================== ПРОМОКОДЫ ========================
@dp.callback_query(F.data == "menu_promo")
async def menu_promo(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    db.update_activity(user_id)

    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return

    await state.set_state(UserStates.waiting_for_promo)
    await safe_edit_or_send(callback.message, db.get_text("promo_enter"), back_keyboard("menu_main"))
    await callback.answer()

@dp.message(UserStates.waiting_for_promo)
async def process_promo_code(message: Message, state: FSMContext):
    user_id = message.from_user.id
    code = message.text.strip()
    reward = db.use_promocode(user_id, code)

    if reward is None:
        await message.answer(db.get_text("promo_invalid"))
    else:
        text = db.get_text("promo_success", reward=reward)
        await message.answer(text)

    await state.clear()

# ======================== ВЫВОД СРЕДСТВ ========================
@dp.callback_query(F.data == "menu_withdraw")
async def menu_withdraw(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.is_banned(user_id):
        await callback.answer("Вы забанены!")
        return

    db.update_activity(user_id)

    if await user_needs_gate(user_id, callback.message.chat.id):
        await send_sponsors_gate(callback.message, user_id, callback.message.chat.id, edit=True)
        await callback.answer()
        return

    user = db.get_user(user_id)

    text = (
        f"💳 <b>Вывод средств</b>\n\n"
        f"⭐ Ваш баланс: {user['balance_rub']:.2f} ₽\n\n"
        f"Минимальная сумма вывода: {MIN_WITHDRAW_RUB:.0f} ₽\n\n"
        f"Нажмите кнопку ниже, чтобы оформить заявку:"
    )

    await safe_edit_or_send(callback.message, text, withdraw_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "withdraw_rub")
async def withdraw_request(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user = db.get_user(user_id)

    if user["balance_rub"] < MIN_WITHDRAW_RUB:
        await callback.answer(f"❌ Недостаточно средств. Минимум {MIN_WITHDRAW_RUB:.0f} ₽", show_alert=True)
        return

    await state.set_state(WithdrawStates.waiting_for_amount)
    await safe_edit_or_send(
        callback.message,
        f"💳 <b>Вывод средств</b>\n\n"
        f"Ваш баланс: {user['balance_rub']:.2f} ₽\n"
        f"Минимум: {MIN_WITHDRAW_RUB:.0f} ₽\n\n"
        f"{db.get_text('enter_amount')}\n\n"
        f"<i>После ввода суммы укажите реквизиты для перевода (номер карты/кошелька)</i>",
        back_keyboard("menu_withdraw")
    )
    await callback.answer()

@dp.message(WithdrawStates.waiting_for_amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительной.")
            return
    except ValueError:
        await message.answer("❌ Введите корректное число.")
        return

    await state.update_data(amount=amount)
    await state.set_state(WithdrawStates.waiting_for_details)
    await message.answer(db.get_text("enter_details"))

@dp.message(WithdrawStates.waiting_for_details)
async def process_withdraw_details(message: Message, state: FSMContext):
    details = message.text.strip()
    if len(details) < 5:
        await message.answer("❌ Введите корректные реквизиты (минимум 5 символов)")
        return

    await state.update_data(details=details)
    await finalize_withdrawal(message, state)

async def finalize_withdrawal(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    details = data.get("details")

    user_id = message.from_user.id
    user = db.get_user(user_id)

    if amount > user["balance_rub"]:
        await message.answer(f"❌ Недостаточно средств. Ваш баланс: {user['balance_rub']:.2f} ₽")
        await state.clear()
        return

    if db.deduct_balance(user_id, amount):
        withdrawal_id = db.create_withdrawal(user_id, amount, details)
        db.add_transaction(user_id, "withdraw", -amount, f"Вывод {amount:.2f} ₽")
        await send_withdrawal_check(withdrawal_id, user_id, amount, details)
        await message.answer(
            f"✅ {db.get_text('withdraw_request')}\n"
            f"Реквизиты: {details}\n\n{db.get_text('withdraw_pending')}",
            reply_markup=main_menu_keyboard()
        )

    await state.clear()

async def send_withdrawal_check(withdrawal_id: int, user_id: int, amount_rub: float, details: str):
    try:
        user_chat = await bot.get_chat(user_id)
        username_part = f" (@{user_chat.username})" if user_chat.username else ""
    except Exception:
        username_part = ""

    text = (
        f"💵 <b>Новая заявка на вывод</b>\n\n"
        f"Заявка №{withdrawal_id}\n"
        f"Пользователь: {user_id}{username_part}\n"
        f"Сумма: {amount_rub:.2f} ₽\n"
        f"Реквизиты: {details}\n\n"
        f"Статус: ⏳ Ожидает обработки"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            create_button("Отклонить", "❌", callback_data=f"wd_reject_{withdrawal_id}", style="danger"),
            create_button("Выплачено", "✅", callback_data=f"wd_paid_{withdrawal_id}", style="success")
        ]
    ])

    try:
        sent = await bot.send_message(PAYMENTS_CHANNEL, text, reply_markup=keyboard)
        db.set_withdrawal_channel_message(withdrawal_id, sent.message_id)
    except Exception as e:
        logger.error(f"Не удалось отправить чек на вывод в канал {PAYMENTS_CHANNEL}: {e}")

# ======================== ОБРАБОТЧИКИ ВЫВОДОВ (АДМИН) ========================
@dp.callback_query(F.data.startswith("wd_paid_") | F.data.startswith("wd_reject_"))
async def handle_withdrawal_action(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Только администратор может это сделать!", show_alert=True)
        return

    is_paid = callback.data.startswith("wd_paid_")
    withdrawal_id = int(callback.data.split("_")[2])

    withdrawal = db.get_withdrawal(withdrawal_id)
    if not withdrawal:
        await callback.answer("❌ Заявка не найдена!", show_alert=True)
        return

    new_status = "paid" if is_paid else "rejected"
    changed = db.set_withdrawal_status(withdrawal_id, new_status)

    if not changed:
        await callback.answer("⚠️ Эта заявка уже обработана!", show_alert=True)
        return

    withdrawal = db.get_withdrawal(withdrawal_id)
    user_id = withdrawal["user_id"]
    amount_rub = withdrawal["amount_rub"]
    details = withdrawal.get("details", "Не указаны")

    try:
        user_chat = await bot.get_chat(user_id)
        username_part = f" (@{user_chat.username})" if user_chat.username else ""
    except Exception:
        username_part = ""

    if is_paid:
        status_line = "✅ Выплачено"
        user_notify_text = (
            f"✅ <b>Ваша заявка на вывод выплачена!</b>\n\n"
            f"Сумма: {amount_rub:.2f} ₽\n"
            f"Реквизиты: {details}\n"
            f"Спасибо, что пользуетесь ботом!"
        )
    else:
        status_line = "❌ Отклонено"
        user_notify_text = (
            f"❌ <b>Ваша заявка на вывод отклонена</b>\n\n"
            f"Сумма {amount_rub:.2f} ₽ возвращена на ваш баланс.\n"
            f"Если это ошибка — свяжитесь с поддержкой."
        )

    updated_text = (
        f"💵 <b>Заявка на вывод №{withdrawal_id}</b>\n\n"
        f"Пользователь: {user_id}{username_part}\n"
        f"Сумма: {amount_rub:.2f} ₽\n"
        f"Реквизиты: {details}\n\n"
        f"Статус: {status_line}"
    )

    try:
        await callback.message.edit_text(updated_text, reply_markup=None)
    except Exception as e:
        logger.error(f"Ошибка edit_text чека в канале: {e}")

    try:
        await bot.send_message(user_id, user_notify_text)
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя о статусе выплаты: {e}")

    await callback.answer("✅ Готово!" if is_paid else "❌ Заявка отклонена")

# ======================== АДМИН-ПАНЕЛЬ ========================
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "🛠 <b>Админ-панель</b>\n\nВыберите нужный раздел для управления ботом:",
        admin_panel_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    users = db.data.get("users", {})
    total_users = len(users)
    banned_users = sum(1 for u in users.values() if u.get("is_banned"))
    total_rub = sum(u.get("balance_rub", 0.0) for u in users.values())
    stats = db.data.get("statistics", {})

    threshold = datetime.now() - timedelta(days=7)
    active_users = 0
    for user in users.values():
        last_activity = user.get("last_activity")
        if last_activity:
            try:
                last_date = datetime.fromisoformat(last_activity)
                if last_date > threshold:
                    active_users += 1
            except Exception:
                pass

    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Активных (7 дней): {active_users}\n"
        f"🚫 Заблокировано: {banned_users}\n"
        f"👥 Всего рефералов: {stats.get('total_referrals', 0)}\n\n"
        f"💰 Суммарный баланс пользователей: {total_rub:.2f} ₽\n\n"
        f"💳 Всего выплачено: {stats.get('total_withdrawn', 0.0):.2f} ₽\n"
        f"💰 Всего заработано: {stats.get('total_earned', 0.0):.2f} ₽"
    )

    await safe_edit_or_send(callback.message, text, back_keyboard("admin_panel"))
    await callback.answer()

# ======================== АДМИН-ТЕКСТ СТАРТА ========================
@dp.callback_query(F.data == "admin_start_text")
async def admin_start_text(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    current_text = db.get_start_text()
    text = (
        f"📝 <b>Текущий текст приветствия:</b>\n\n"
        f"<code>{current_text}</code>\n\n"
        f"Доступная переменная: <code>{{REFERRAL_REWARD_RUB}}</code>\n\n"
        f"<b>Введите новый текст (HTML-теги поддерживаются):</b>"
    )
    await state.set_state(AdminStates.waiting_for_start_text)
    await safe_edit_or_send(callback.message, text, back_keyboard("admin_panel"))
    await callback.answer()

@dp.message(AdminStates.waiting_for_start_text)
async def process_start_text(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    new_text = message.text
    try:
        new_text.format(REFERRAL_REWARD_RUB=0)
    except Exception as e:
        await message.answer(f"❌ Ошибка в переменных форматирования: {e}\nПопробуйте ещё раз.")
        return

    db.set_start_text(new_text)
    await state.clear()
    await message.answer("✅ Текст приветствия успешно обновлён!", reply_markup=back_keyboard("admin_panel"))

# ======================== АДМИН-СПОНСОРЫ ========================
@dp.callback_query(F.data == "admin_sponsors")
async def admin_sponsors(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    await safe_edit_or_send(
        callback.message,
        "📺 <b>Управление спонсорами и заданиями</b>\n\nНиже список текущих обязательных каналов:",
        admin_sponsors_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_sponsor_del_"))
async def admin_sponsor_del(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    idx = int(callback.data.replace("admin_sponsor_del_", ""))
    db.remove_sponsor(idx)
    await callback.answer("🗑 Спонсор удалён!", show_alert=True)
    await safe_edit_or_send(
        callback.message,
        "📺 <b>Управление спонсорами и заданиями</b>\n\nНиже список текущих обязательных каналов:",
        admin_sponsors_keyboard()
    )

@dp.callback_query(F.data == "admin_sponsor_add")
async def admin_sponsor_add(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_sponsor_name)
    await safe_edit_or_send(
        callback.message,
        "📝 <b>Добавление спонсора</b>\n\nШаг 1/2: Введите название кнопки спонсора:",
        back_keyboard("admin_sponsors")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_sponsor_name)
async def process_sponsor_name_simple(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    await state.update_data(sponsor_name=message.text.strip())
    await state.set_state(AdminStates.waiting_for_sponsor_link)
    await message.answer("Шаг 2/2: Введите ссылку на канал/чат (например: https://t.me/mychannel):")

@dp.message(AdminStates.waiting_for_sponsor_link)
async def process_sponsor_link_simple(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    data = await state.get_data()
    link = message.text.strip()
    button_text = data.get("sponsor_name", "Спонсор")

    db.add_sponsor(button_text, link, None, len(db.get_sponsors()), "button")
    await message.answer("✅ Спонсор успешно добавлен!")

    await state.clear()
    await safe_edit_or_send(
        message,
        "📺 <b>Управление спонсорами и заданиями</b>\n\nНиже список текущих обязательных каналов:",
        admin_sponsors_keyboard()
    )

# ======================== АДМИН-КНОПКИ ========================
@dp.callback_query(F.data == "admin_button_texts")
async def admin_button_texts(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "🧩 <b>Настройка названий кнопок меню</b>\nВыберите кнопку для изменения:",
        admin_button_texts_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_btn_"))
async def admin_btn_text_edit(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    btn_key = callback.data.replace("admin_btn_", "")
    await state.update_data(target_btn_key=btn_key)
    await state.set_state(AdminStates.waiting_for_button_text)

    current = db.data.get("button_texts", {}).get(btn_key, btn_key.capitalize())
    await safe_edit_or_send(
        callback.message,
        f"📝 Введите новое название для кнопки <code>{btn_key}</code>\n"
        f"Текущее: <code>{current}</code>\n\n"
        f"Например: <code>Заработок</code>",
        back_keyboard("admin_button_texts")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_button_text)
async def process_button_text(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    data = await state.get_data()
    btn_key = data.get("target_btn_key")
    if btn_key:
        new_text = message.text.strip()
        db.data["button_texts"][btn_key] = new_text
        db.save()
        await message.answer(f"✅ Название кнопки <code>{btn_key}</code> обновлено!\nНовое значение: {new_text}")

    await state.clear()

# ======================== АДМИН-ЭМОДЗИ ========================
@dp.callback_query(F.data == "admin_button_emojis")
async def admin_button_emojis(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "🎨 <b>Настройка эмодзи для кнопок</b>\nВыберите кнопку для смены эмодзи:",
        admin_button_emojis_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_emoji_"))
async def admin_emoji_edit(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    btn_key = callback.data.replace("admin_emoji_", "")
    await state.update_data(target_emoji_key=btn_key)
    await state.set_state(AdminStates.waiting_for_button_emoji)

    current = db.get_button_emoji(btn_key)
    await safe_edit_or_send(
        callback.message,
        f"🎨 Отправьте новый эмодзи для кнопки <code>{btn_key}</code>\n(текущий: {current}):",
        back_keyboard("admin_button_emojis")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_button_emoji)
async def process_button_emoji(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    data = await state.get_data()
    btn_key = data.get("target_emoji_key")
    emoji = message.text.strip()
    if btn_key:
        db.set_button_emoji(btn_key, emoji)
        await message.answer(f"✅ Эмодзи кнопки <code>{btn_key}</code> обновлено!", reply_markup=back_keyboard("admin_button_emojis"))
    await state.clear()

# ======================== АДМИН-БАННЕР ========================
@dp.callback_query(F.data == "admin_banner")
async def admin_banner(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    banner_path = db.get_banner()
    has_banner = "Да" if banner_path and os.path.exists(banner_path) else "Нет"

    await safe_edit_or_send(
        callback.message,
        f"🖼 <b>Управление баннером меню</b>\n\nТекущий баннер: {has_banner}",
        admin_banner_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "banner_upload")
async def banner_upload(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_banner)
    await safe_edit_or_send(
        callback.message,
        "📤 Отправьте изображение (фото) для установки в качестве баннера меню:",
        back_keyboard("admin_banner")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_banner, F.photo)
async def process_banner_photo(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    photo = message.photo[-1]
    file_path = "banner.jpg"
    await bot.download(photo, destination=file_path)
    db.set_banner(file_path)

    await state.clear()
    await message.answer("✅ Баннер успешно сохранён!", reply_markup=back_keyboard("admin_banner"))

@dp.callback_query(F.data == "banner_delete")
async def banner_delete(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    db.remove_banner()
    await callback.answer("🗑 Баннер удалён!", show_alert=True)
    await safe_edit_or_send(
        callback.message,
        "🖼 <b>Управление баннером меню</b>\n\nТекущий баннер: Нет",
        admin_banner_keyboard()
    )

# ======================== АДМИН-РЕФЕРАЛЫ ========================
@dp.callback_query(F.data == "admin_referral_reward")
async def admin_referral_reward(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    rub = db.get_referral_reward_rub()

    text = (
        f"🎁 <b>Настройка награды за реферала</b>\n\n"
        f"Текущая награда за приглашённого пользователя: {rub:.2f} ₽"
    )

    await safe_edit_or_send(callback.message, text, admin_referral_reward_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_reward_rub")
async def admin_reward_rub(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_reward_rub)
    await safe_edit_or_send(
        callback.message,
        "🎁 Введите новое значение награды за реферала в рублях (₽):",
        back_keyboard("admin_referral_reward")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_reward_rub)
async def process_reward_rub(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    try:
        val = float(message.text.replace(",", "."))
        if val < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное положительное число!")
        return

    db.set_referral_reward_rub(val)
    await state.clear()
    await message.answer("✅ Награда за реферала обновлена!", reply_markup=back_keyboard("admin_referral_reward"))

# ======================== АДМИН-ПРОМОКОДЫ ========================
@dp.callback_query(F.data == "admin_promocode")
async def admin_promocode(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_for_promocode)
    await safe_edit_or_send(
        callback.message,
        "🎫 <b>Создание промокода</b>\n\nВведите данные в формате:\n<code>КОД|СУММА|КОЛ-ВО_АКТИВАЦИЙ</code>\n\nПример: <code>BONUS50|5|100</code>",
        back_keyboard("admin_panel")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_promocode)
async def process_promocode(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    try:
        code, reward, uses = message.text.split("|")
        reward = float(reward)
        uses = int(uses)
        db.create_promocode(code.strip(), reward, uses)
        await message.answer(f"✅ Промокод <code>{code}</code> создан!\nНаграда: {reward:.2f} ₽\nАктиваций: {uses}")
    except Exception as e:
        await message.answer(f"❌ Неверный формат! Используйте: КОД|СУММА|КОЛ-ВО\nОшибка: {e}")

    await state.clear()

# ======================== АДМИН-ЭКСПОРТ ========================
@dp.callback_query(F.data == "admin_export_csv")
async def admin_export_csv(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    csv_data = db.export_stats_csv()
    await callback.message.answer_document(
        BufferedInputFile(csv_data.encode('utf-8'), filename="statistics.csv"),
        caption="📊 Экспорт статистики пользователей"
    )
    await callback.answer()

# ======================== АДМИН-БАН ========================
@dp.callback_query(F.data == "admin_ban")
async def admin_ban(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_ban_user)
    await safe_edit_or_send(
        callback.message,
        "🚫 <b>Бан / Разбан пользователя</b>\n\nВведите Telegram ID пользователя:",
        back_keyboard("admin_panel")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_ban_user)
async def process_admin_ban_user(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите корректный числовой ID!")
        return

    user = db.get_user(target_id)
    is_banned = not user.get("is_banned", False)
    user["is_banned"] = is_banned
    db.save()

    status_str = "заблокирован" if is_banned else "разблокирован"
    await state.clear()
    await message.answer(f"✅ Пользователь {target_id} успешно {status_str}!", reply_markup=back_keyboard("admin_panel"))

# ======================== АДМИН-СТАТУС ========================
@dp.callback_query(F.data == "admin_status")
async def admin_status(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    current_status = db.is_bot_stopped()
    new_status = not current_status
    db.set_bot_stopped(new_status)

    status_str = "⏸ Остановлен" if new_status else "⚡ Активен"
    await callback.answer(f"Статус бота изменён: {status_str}", show_alert=True)
    await safe_edit_or_send(
        callback.message,
        f"🛠 <b>Управление статусом бота</b>\n\nТекущее состояние: {status_str}",
        admin_panel_keyboard()
    )

# ======================== АДМИН-PIARFLOW ========================
@dp.callback_query(F.data == "admin_piarflow_debug")
async def admin_piarflow_debug(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    user_id = callback.from_user.id
    piar_sponsors = await fetch_piar_sponsors(user_id, callback.message.chat.id)

    debug_info = (
        f"🔍 <b>PiarFlow Debug</b>\n\n"
        f"API URL: {PIARFLOW_API_URL}\n"
        f"Ключей активно: {len(PIARFLOW_API_KEYS)}\n"
        f"Max sponsors: {PIARFLOW_MAX_SPONSORS}\n\n"
        f"Получено спонсоров: {len(piar_sponsors)}\n"
    )

    if piar_sponsors:
        debug_info += "\nСписок спонсоров:\n"
        for i, s in enumerate(piar_sponsors[:5], 1):
            debug_info += f"{i}. {s.get('button_text', 'Без названия')}\n"

    await safe_edit_or_send(callback.message, debug_info, back_keyboard("admin_panel"))
    await callback.answer()

# ======================== АДМИН-ПОЛЬЗОВАТЕЛИ ========================
@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message,
        "👥 <b>Список пользователей</b> (выберите для управления):",
        admin_users_keyboard(page=0)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_users_page_"))
async def admin_users_page(callback: CallbackQuery):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return
    page_str = callback.data.replace("admin_users_page_", "")
    if page_str == "info":
        await callback.answer()
        return
    page = int(page_str)
    await safe_edit_or_send(
        callback.message,
        f"👥 <b>Список пользователей</b> (Страница {page + 1}):",
        admin_users_keyboard(page=page)
    )
    await callback.answer()

async def show_user_card(message: Message, user: Dict):
    status = "🚫 Забанен" if user.get("is_banned") else "✅ Активен"
    text = (
        f"👤 <b>Карточка пользователя</b> <code>{user['id']}</code>\n\n"
        f"Статус: {status}\n"
        f"Баланс: {user.get('balance_rub', 0.0):.2f} ₽\n"
        f"Рефералов: {user.get('referral_count', 0)}\n"
        f"Заданий выполнено: {user.get('tasks_completed', 0)}\n"
        f"Дата регистрации: {user.get('created_at', 'Н/Д')[:10]}"
    )
    await safe_edit_or_send(message, text, user_actions_keyboard(user["id"]))

@dp.callback_query(F.data.startswith("admin_user_") & ~F.data.startswith("admin_users_"))
async def admin_user_info(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    data = callback.data

    if data.startswith("admin_user_add_balance_"):
        target_id = int(data.replace("admin_user_add_balance_", ""))
        await state.update_data(target_user_id=target_id, action="add_balance")
        await state.set_state(AdminStates.waiting_for_balance_amount)
        await safe_edit_or_send(
            callback.message,
            f"💰 Введите сумму в ₽ для добавления к балансу пользователя {target_id}:",
            back_keyboard(f"admin_user_{target_id}")
        )
        await callback.answer()
        return

    if data.startswith("admin_user_sub_balance_"):
        target_id = int(data.replace("admin_user_sub_balance_", ""))
        await state.update_data(target_user_id=target_id, action="sub_balance")
        await state.set_state(AdminStates.waiting_for_balance_amount)
        await safe_edit_or_send(
            callback.message,
            f"💰 Введите сумму в ₽ для списания с баланса пользователя {target_id}:",
            back_keyboard(f"admin_user_{target_id}")
        )
        await callback.answer()
        return

    if data.startswith("admin_user_add_ref_"):
        target_id = int(data.replace("admin_user_add_ref_", ""))
        await state.update_data(target_user_id=target_id)
        await state.set_state(AdminStates.waiting_for_referral_count)
        await safe_edit_or_send(
            callback.message,
            f"👥 Введите количество рефералов для добавления пользователю {target_id}:",
            back_keyboard(f"admin_user_{target_id}")
        )
        await callback.answer()
        return

    if data.startswith("admin_user_reset_ref_"):
        target_id = int(data.replace("admin_user_reset_ref_", ""))
        db.admin_reset_referrals(target_id)
        await callback.answer("🔄 Счётчик рефералов обнулён!", show_alert=True)
        user = db.get_user(target_id)
        await show_user_card(callback.message, user)
        return

    if data.startswith("admin_user_ban_"):
        target_id = int(data.replace("admin_user_ban_", ""))
        user = db.get_user(target_id)
        user["is_banned"] = True
        db.save()
        await callback.answer("🚫 Пользователь забанен!", show_alert=True)
        await show_user_card(callback.message, user)
        return

    if data.startswith("admin_user_unban_"):
        target_id = int(data.replace("admin_user_unban_", ""))
        user = db.get_user(target_id)
        user["is_banned"] = False
        db.save()
        await callback.answer("🔓 Пользователь разбанен!", show_alert=True)
        await show_user_card(callback.message, user)
        return

    target_id = int(data.replace("admin_user_", ""))
    user = db.get_user(target_id)
    await show_user_card(callback.message, user)
    await callback.answer()

@dp.message(AdminStates.waiting_for_balance_amount)
async def process_admin_balance(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите число!")
        return

    data = await state.get_data()
    target_id = data.get("target_user_id")
    action = data.get("action")

    if target_id:
        if action == "add_balance":
            db.add_balance(target_id, amount)
            await message.answer(f"✅ Добавлено {amount:.2f} ₽ пользователю ID {target_id}")
        elif action == "sub_balance":
            db.deduct_balance(target_id, amount)
            await message.answer(f"✅ Списано {amount:.2f} ₽ у пользователя ID {target_id}")

    await state.clear()

@dp.message(AdminStates.waiting_for_referral_count)
async def process_admin_referrals(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return
    try:
        count = int(message.text)
    except ValueError:
        await message.answer("❌ Введите целое число!")
        return

    data = await state.get_data()
    target_id = data.get("target_user_id")

    if target_id:
        db.admin_add_referrals(target_id, count)
        await message.answer(f"✅ Пользователю ID {target_id} добавлено {count} рефералов")

    await state.clear()

# ======================== АДМИН-РАССЫЛКА ========================
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await safe_edit_or_send(
        callback.message,
        "📢 <b>Рассылка сообщений</b>\n\nВведите текст сообщения для рассылки всем пользователям (поддерживаются HTML-теги):",
        back_keyboard("admin_panel")
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_broadcast_text)
async def process_broadcast_text(message: Message, state: FSMContext):
    if not db.is_admin(message.from_user.id):
        return

    text = message.text
    users = list(db.data["users"].keys())

    await message.answer(f"⏳ Запуск рассылки на {len(users)} пользователей...")

    success = 0
    failed = 0

    for uid in users:
        try:
            await bot.send_message(int(uid), text)
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await state.clear()
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\nУспешно: {success}\nОшибок: {failed}",
        reply_markup=back_keyboard("admin_panel")
    )

# ======================== ЗАПУСК БОТА ========================
async def main():
    logger.info("🚀 Запуск бота...")

    web_task = asyncio.create_task(run_web_server())
    await asyncio.sleep(1)

    asyncio.create_task(check_inactive_users())

    logger.info("🤖 Бот запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
