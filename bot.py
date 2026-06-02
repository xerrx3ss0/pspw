import asyncio
import logging
import json
import os
from telethon import TelegramClient, events
from telethon.tl.types import UpdateDeleteMessages, UpdateDeleteChannelMessages
from telethon.errors import ChatWriteForbiddenError, MessageNotModifiedError
from config import BOT_TOKEN, SOURCE_CHAT_ID, TARGET_CHAT_ID
from filters import ALLOWED_HASHTAGS, REQUIRED_KEYWORDS, BLOCKED_USERS, BANNED_WORDS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")

proxy_server = os.getenv("PROXY_SERVER")
proxy_port = os.getenv("PROXY_PORT")
proxy_user = os.getenv("PROXY_USER")
proxy_pass = os.getenv("PROXY_PASS")

if proxy_server and proxy_port:
    import socks
    proxy = (socks.SOCKS5, proxy_server, int(proxy_port), True, proxy_user, proxy_pass)
    client = TelegramClient('bot_session', api_id, api_hash, proxy=proxy)
else:
    client = TelegramClient('bot_session', api_id, api_hash)

MESSAGE_MAP_FILE = "message_map.json"

message_map = {}

media_groups = {}
media_group_timers = {}

CHECK_INTERVAL = 5
last_check_time = 0


def load_message_map():
    """Загружает соответствие ID сообщений из файла"""
    global message_map
    if os.path.exists(MESSAGE_MAP_FILE):
        try:
            with open(MESSAGE_MAP_FILE, 'r', encoding='utf-8') as f:
                message_map = json.load(f)
                message_map = {int(k): v for k, v in message_map.items()}
            logger.info(f"Загружено {len(message_map)} соответствий сообщений")
        except Exception as e:
            logger.error(f"Ошибка загрузки message_map: {e}")
            message_map = {}
    else:
        message_map = {}


def save_message_map():
    """Сохраняет соответствие ID сообщений в файл"""
    try:
        with open(MESSAGE_MAP_FILE, 'w', encoding='utf-8') as f:
            json.dump(message_map, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения message_map: {e}")


@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    await event.respond(
        "Бот запущен и работает!\n\n"
        f"Слушаю канал: {SOURCE_CHAT_ID}\n"
        f"Отправляю в: {TARGET_CHAT_ID}"
    )
    logger.info(f"Команда /start от пользователя {event.sender_id}")


async def is_user_blocked(event) -> bool:
    if not event.sender:
        return False
    
    user_id = event.sender_id
    sender = await event.get_sender()
    username = sender.username if sender else None
    
    if str(user_id) in BLOCKED_USERS:
        return True
    
    if username:
        if username in BLOCKED_USERS or f"@{username}" in BLOCKED_USERS:
            return True
    
    return False


def contains_banned_words(text: str) -> tuple[bool, str]:
    """Проверяет, содержит ли текст запрещенные слова"""
    if not text or not BANNED_WORDS:
        return False, ""
    
    text_lower = text.lower()
    
    for banned_word in BANNED_WORDS:
        if banned_word.lower() in text_lower:
            return True, banned_word
    
    return False, ""


async def should_forward_message(event) -> bool:
    if await is_user_blocked(event):
        return False
    
    text = event.message.text or ""
    
    if not text:
        return False
    
    has_banned, _ = contains_banned_words(text)
    if has_banned:
        return False
    
    has_allowed_hashtag = any(hashtag in text for hashtag in ALLOWED_HASHTAGS)
    has_required_keyword = any(keyword in text for keyword in REQUIRED_KEYWORDS)
    
    # Пропускаем пост если есть ключевое слово (достаточно только его)
    return has_required_keyword


@client.on(events.NewMessage(chats=SOURCE_CHAT_ID))
async def forward_message(event):
    message = event.message
    
    if message.grouped_id:
        grouped_id = message.grouped_id
        
        if grouped_id not in media_groups:
            media_groups[grouped_id] = {
                'message_ids': [],
                'first_message': event,
                'should_forward': None
            }
        
        media_groups[grouped_id]['message_ids'].append(message.id)
        
        if media_groups[grouped_id]['should_forward'] is None:
            media_groups[grouped_id]['should_forward'] = await should_forward_message(event)
            
            if not media_groups[grouped_id]['should_forward']:
                if await is_user_blocked(event):
                    sender = await event.get_sender()
                    user_info = f"ID: {event.sender_id}"
                    if sender and sender.username:
                        user_info += f", @{sender.username}"
                    logger.info(
                        f"Медиа-группа {grouped_id} пропущена (пользователь заблокирован: {user_info})"
                    )
                else:
                    text = message.text or ""
                    has_banned, banned_word = contains_banned_words(text)
                    if has_banned:
                        logger.info(
                            f"Медиа-группа {grouped_id} пропущена (содержит запрещенное слово: '{banned_word}')"
                        )
                    else:
                        logger.info(
                            f"Медиа-группа {grouped_id} пропущена (не соответствует фильтрам)"
                        )
        
        logger.info(f"Добавлено сообщение {message.id} в медиа-группу {grouped_id}. Всего в группе: {len(media_groups[grouped_id]['message_ids'])}")
        
        if grouped_id in media_group_timers:
            media_group_timers[grouped_id].cancel()
        
        async def process_media_group():
            await asyncio.sleep(1)
            
            if grouped_id in media_groups:
                group_data = media_groups[grouped_id]
                message_ids = group_data['message_ids']
                should_forward = group_data['should_forward']
                
                if not should_forward:
                    if grouped_id in media_groups:
                        del media_groups[grouped_id]
                    if grouped_id in media_group_timers:
                        del media_group_timers[grouped_id]
                    return
                
                try:
                    messages_to_send = []
                    for msg_id in message_ids:
                        msg = await client.get_messages(SOURCE_CHAT_ID, ids=msg_id)
                        messages_to_send.append(msg)
                    
                    media_files = []
                    caption_text = ""
                    
                    for msg in messages_to_send:
                        if msg.media:
                            media_files.append(msg.media)
                        if msg.text and not caption_text:
                            caption_text = msg.text
                    
                    if len(media_files) == 1:
                        sent = await client.send_message(
                            TARGET_CHAT_ID,
                            caption_text,
                            file=media_files[0]
                        )
                        for msg_id in message_ids:
                            message_map[msg_id] = sent.id
                    else:
                        sent_messages = await client.send_file(
                            TARGET_CHAT_ID,
                            media_files,
                            caption=caption_text
                        )
                        
                        if not isinstance(sent_messages, list):
                            sent_messages = [sent_messages]
                        
                        for i, msg_id in enumerate(message_ids):
                            if i < len(sent_messages):
                                message_map[msg_id] = sent_messages[i].id
                    
                    save_message_map()
                    
                    logger.info(
                        f"Медиа-группа ({len(message_ids)} фото/видео) успешно скопирована "
                        f"из {SOURCE_CHAT_ID} в {TARGET_CHAT_ID}"
                    )
                    
                except ChatWriteForbiddenError as e:
                    logger.error(
                        f"Ошибка доступа: бот не имеет прав в группе с постами {TARGET_CHAT_ID}. "
                        f"Убедитесь, что бот является администратором. Ошибка: {e}"
                    )
                except Exception as e:
                    logger.error(f"Неожиданная ошибка при копировании медиа-группы: {e}")
                finally:
                    if grouped_id in media_groups:
                        del media_groups[grouped_id]
                    if grouped_id in media_group_timers:
                        del media_group_timers[grouped_id]
        
        task = asyncio.create_task(process_media_group())
        media_group_timers[grouped_id] = task
        
    else:
        if not await should_forward_message(event):
            if await is_user_blocked(event):
                sender = await event.get_sender()
                user_info = f"ID: {event.sender_id}"
                if sender and sender.username:
                    user_info += f", @{sender.username}"
                logger.info(
                    f"Сообщение {message.id} пропущено (пользователь заблокирован: {user_info})"
                )
            else:
                text = message.text or ""
                has_banned, banned_word = contains_banned_words(text)
                if has_banned:
                    logger.info(
                        f"Сообщение {message.id} пропущено (содержит запрещенное слово: '{banned_word}')"
                    )
                else:
                    logger.info(
                        f"Сообщение {message.id} пропущено (не соответствует фильтрам)"
                    )
            return
        
        try:
            if message.media:
                sent_message = await client.send_message(
                    TARGET_CHAT_ID,
                    message.text or "",
                    file=message.media
                )
            else:
                sent_message = await client.send_message(
                    TARGET_CHAT_ID,
                    message.text or ""
                )
            
            message_map[message.id] = sent_message.id
            save_message_map()
            
            logger.info(
                f"Сообщение {message.id} успешно скопировано "
                f"из {SOURCE_CHAT_ID} в {TARGET_CHAT_ID} (новый ID: {sent_message.id})"
            )
            
        except ChatWriteForbiddenError as e:
            logger.error(
                f"Ошибка доступа: бот не имеет прав в группе с постами {TARGET_CHAT_ID}. "
                f"Убедитесь, что бот является администратором. Ошибка: {e}"
            )
        except Exception as e:
            logger.error(f"Неожиданная ошибка при копировании сообщения {message.id}: {e}")


@client.on(events.MessageEdited(chats=SOURCE_CHAT_ID))
async def handle_edited_message(event):
    message = event.message
    
    logger.info(f"Получено событие редактирования сообщения {message.id}")
    logger.info(f"Сообщение в message_map: {message.id in message_map}")
    
    if not await should_forward_message(event):
        if await is_user_blocked(event):
            sender = await event.get_sender()
            user_info = f"ID: {event.sender_id}"
            if sender and sender.username:
                user_info += f", @{sender.username}"
            logger.info(
                f"Отредактированное сообщение {message.id} от заблокированного пользователя: {user_info}"
            )
        else:
            text = message.text or ""
            has_banned, banned_word = contains_banned_words(text)
            if has_banned:
                logger.info(
                    f"Отредактированное сообщение {message.id} содержит запрещенное слово: '{banned_word}'"
                )
            else:
                logger.info(
                    f"Отредактированное сообщение {message.id} не соответствует фильтрам"
                )
        
        if message.id in message_map:
            target_message_id = message_map[message.id]
            logger.info(f"Попытка удалить сообщение {target_message_id} из целевого чата")
            try:
                # Проверяем, существует ли сообщение перед удалением
                target_msg = await client.get_messages(TARGET_CHAT_ID, ids=target_message_id)
                logger.info(f"Получено сообщение из целевого чата: {target_msg is not None}")
                if target_msg:
                    logger.info(f"Атрибут service: {getattr(target_msg, 'service', 'не найден')}")
                if target_msg and not getattr(target_msg, 'service', False):
                    await client.delete_messages(
                        TARGET_CHAT_ID,
                        target_message_id
                    )
                    logger.info(
                        f"Сообщение {target_message_id} удалено из целевой группы "
                        f"(не соответствует фильтрам после редактирования)"
                    )
                del message_map[message.id]
                save_message_map()
            except Exception as e:
                if "service message" not in str(e).lower():
                    logger.error(f"Ошибка удаления сообщения {target_message_id}: {e}")
        else:
            logger.info(f"Сообщение {message.id} не найдено в message_map, удаление невозможно")
        
        return
    
    if message.id not in message_map:
        logger.info(
            f"Отредактированное сообщение {message.id} не найдено в карте. "
            f"Возможно, оно было отправлено до запуска бота."
        )
        await forward_message(event)
        return
    
    target_message_id = message_map[message.id]
    
    try:
        text = message.text or ""
        
        if message.media:
            await client.edit_message(
                TARGET_CHAT_ID,
                target_message_id,
                text,
                file=message.media
            )
            
            logger.info(
                f"Сообщение {target_message_id} обновлено с медиа в целевой группе "
                f"(исходное: {message.id})"
            )
        else:
            await client.edit_message(
                TARGET_CHAT_ID,
                target_message_id,
                text
            )
            
            logger.info(
                f"Сообщение {target_message_id} в целевой группе обновлено "
                f"(исходное: {message.id})"
            )
        
    except MessageNotModifiedError:
        logger.info(f"Сообщение {target_message_id} не изменилось")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при редактировании сообщения {target_message_id}: {e}")


@client.on(events.Raw(UpdateDeleteMessages))
async def handle_deleted_message_private(event):
    for deleted_id in event.messages:
        if deleted_id in message_map:
            target_message_id = message_map[deleted_id]
            try:
                # Проверяем, существует ли сообщение перед удалением
                target_msg = await client.get_messages(TARGET_CHAT_ID, ids=target_message_id)
                if target_msg and not getattr(target_msg, 'service', False):
                    await client.delete_messages(
                        TARGET_CHAT_ID,
                        target_message_id
                    )
                    logger.info(
                        f"Сообщение {target_message_id} удалено из целевой группы "
                        f"(исходное сообщение {deleted_id} было удалено)"
                    )
                del message_map[deleted_id]
                save_message_map()
            except Exception as e:
                if "service message" not in str(e).lower():
                    logger.error(f"Ошибка удаления сообщения {target_message_id}: {e}")



@client.on(events.Raw(UpdateDeleteChannelMessages))
async def handle_deleted_message_channel(event):
    try:
        peer_id = event.channel_id
        source_id = abs(SOURCE_CHAT_ID)
        
        if peer_id == source_id:
            logger.info(f"Обнаружено удаление сообщений из SOURCE_CHAT_ID: {event.messages}")
            for deleted_id in event.messages:
                if deleted_id in message_map:
                    target_message_id = message_map[deleted_id]
                    try:
                        # Проверяем, существует ли сообщение перед удалением
                        target_msg = await client.get_messages(TARGET_CHAT_ID, ids=target_message_id)
                        if target_msg and not getattr(target_msg, 'service', False):
                            await client.delete_messages(
                                TARGET_CHAT_ID,
                                target_message_id
                            )
                            logger.info(
                                f"Сообщение {target_message_id} удалено из целевой группы "
                                f"(исходное сообщение {deleted_id} было удалено из канала)"
                            )
                        del message_map[deleted_id]
                        save_message_map()
                    except Exception as e:
                        if "service message" not in str(e).lower():
                            logger.error(f"Ошибка удаления сообщения {target_message_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка в обработчике удаления: {e}")


async def check_deleted_messages():
    global last_check_time
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            
            if not message_map:
                continue
            
            current_time = asyncio.get_event_loop().time()
            if current_time - last_check_time < CHECK_INTERVAL:
                continue
            
            last_check_time = current_time
            source_ids = list(message_map.keys())
            
            batch_size = 100
            for i in range(0, len(source_ids), batch_size):
                batch = source_ids[i:i+batch_size]
                try:
                    messages = await client.get_messages(SOURCE_CHAT_ID, ids=batch)
                    
                    if not isinstance(messages, list):
                        messages = [messages]
                    
                    for j, msg in enumerate(messages):
                        source_id = batch[j]
                        if msg is None or (hasattr(msg, 'id') and msg.id != source_id):
                            if source_id in message_map:
                                target_message_id = message_map[source_id]
                                try:
                                    # Проверяем, существует ли сообщение перед удалением
                                    target_msg = await client.get_messages(TARGET_CHAT_ID, ids=target_message_id)
                                    if target_msg and not getattr(target_msg, 'service', False):
                                        await client.delete_messages(
                                            TARGET_CHAT_ID,
                                            target_message_id
                                        )
                                        logger.info(
                                            f"Сообщение {target_message_id} удалено из целевой группы "
                                            f"(исходное сообщение {source_id} было удалено)"
                                        )
                                    del message_map[source_id]
                                    save_message_map()
                                except Exception as e:
                                    if "service message" not in str(e).lower():
                                        logger.error(f"Ошибка удаления сообщения {target_message_id}: {e}")
                    
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.debug(f"Ошибка проверки батча сообщений: {e}")
                    
        except Exception as e:
            logger.error(f"Ошибка в check_deleted_messages: {e}")


async def main():
    load_message_map()
    
    await client.start(bot_token=BOT_TOKEN)
    
    logger.info("Бот запущен и слушает канал...")
    logger.info(f"Чат с постами: {SOURCE_CHAT_ID}")
    logger.info(f"Группа с постами: {TARGET_CHAT_ID}")
    
    asyncio.create_task(check_deleted_messages())
    
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
