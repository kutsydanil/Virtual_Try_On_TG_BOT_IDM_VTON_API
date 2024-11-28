import logging
import os
import base64
import httpx
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.product_service import ProductService
from services.upload_service import UploadService
from telegram.constants import ParseMode
from helpers.telegram_helpers import escape_special_chars

class TelegramHandler:
    """Handler for managing Telegram bot interactions."""
    
    def __init__(self, product_service: ProductService, upload_service: UploadService, base_url_api: str) -> None:
        self.product_service = product_service
        self.upload_service = upload_service
        self.base_url_api = base_url_api
        self.products = []

        self.command_map = {
            'start': self.start_menu,
            'next_product': self.next_product,
            'previous_product': self.previous_product,
            'select_product': self.select_product,
            'help': self.help_command,
            'how_to_send_photo': self.how_to_send_photo,
            'list_of_products': self.handle_list_of_products,
            'return_to_menu': self.start_menu,
            'show_catalog':self.show_catalog
        }

    async def start_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data['current_product_index'] = 0
        self.products = await self.product_service.fetch_products()

        if not self.products:
            await self.send_message(update, "❌ Не удалось получить список товаров. Попробуйте позже.")
            return

        welcome_text = (
            "👋 Привет! Я ваш помощник в онлайн-магазине.\n"
            "С помощью меня вы можете просматривать товары и отправлять свои фото для обработки.\n"
            "Нажмите |Начать|, чтобы начать, или |Помощь|, если у вас есть вопросы."
        )

        keyboard = self.get_main_menu_keyboard()
        await self.send_message(update, welcome_text, keyboard)

    async def send_message(self, update: Update, text: str, reply_markup=None):
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)

    def get_main_menu_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("🔄 Начать", callback_data='show_catalog')],
            [InlineKeyboardButton("❓ Помощь", callback_data='help')]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = self.get_help_menu_keyboard()
        help_text = (
            "👋 Привет! Я ваш помощник в онлайн-магазине.\n"
            "Вот что я могу:\n"
            "1. Просмотр товаров.\n"
            "2. Отправка фотографий для обработки.\n"
            "3. Получение помощи по использованию бота."
        )
        await self.send_message(update, help_text, keyboard)

    def get_help_menu_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("🔙 В меню", callback_data='return_to_menu')],
            [InlineKeyboardButton("📸 Как отправить изображение?", callback_data='how_to_send_photo')],
            [InlineKeyboardButton("🛍️ Список товаров", callback_data='list_of_products')],
        ]
        return InlineKeyboardMarkup(keyboard)

    async def how_to_send_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.send_message(update, "📸 Чтобы отправить изображение, выберите продукт и отправьте фото в формате JPG или PNG.")

    async def handle_list_of_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.products:
            await self.send_message(update, "❌ Не удалось получить список товаров.")
            return
        
        product_list = "\n".join([f"{i+1}. {product.name}" for i, product in enumerate(self.products)])
        await self.send_message(update, f"🛍️ Доступные товары:\n{product_list}")

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.send_message(update, "⏳ Получаю ваше изображение...")
        
        current_index = context.user_data.get('current_product_index', 0)
        selected_product = self.products[current_index]

        if not selected_product:
            await self.send_message(update, "❌ Сначала выберите продукт.")
            return
        
        try:
            user_photo_file = await update.message.photo[-1].get_file()
            user_photo_bytes = await user_photo_file.download_as_bytearray()
            user_photo_extension = os.path.splitext(user_photo_file.file_path)[1].lower()

            if user_photo_extension not in ['.png', '.jpg', '.jpeg']:
                await self.send_message(update, "❌ Пожалуйста, загрузите фото в формате JPG или PNG.")
                return
            
            product_info = {'product_description': selected_product.description}
            product_image_bytes = product_image_base64 = await self.upload_service.fetch_product_image(selected_product.image_url)

            user_photo_base64 = base64.b64encode(user_photo_bytes).decode('utf-8')
            product_image_base64 = base64.b64encode(product_image_bytes).decode('utf-8')
            response = await self.upload_service.upload_files(user_photo_base64, user_photo_extension, product_image_base64, 
                                                              os.path.splitext(selected_product.image_url)[1], product_info)
            
            if response and response.get('task_id'):
                await self.send_message(update, "✅ Файл загружен. Начинаю обработку...")
                await self.poll_status(update, response['task_id'], context)
            else:
                await self.send_message(update, "❌ Ошибка при загрузке файла.")
        
        except Exception as e:
            logging.error(f"Error: {e}")
            await self.send_message(update, "❌ Произошла ошибка. Попробуйте снова.")

    async def poll_status(self, update: Update, task_id, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_attempts = 2
        for attempt in range(max_attempts):
            await asyncio.sleep(12)
            try:
                async with httpx.AsyncClient() as client:
                    status_response = await client.get(f"{self.base_url_api}/status/{task_id}")
                    status_response.raise_for_status()
                    status_data = status_response.json()

                    if status_data['status'] == 'completed':
                        await self.send_message(update, "✅ Обработка завершена!")
                        processed_image_base64 = status_data['result']
                        img_bytes = base64.b64decode(processed_image_base64)
                        await update.message.reply_photo(photo=img_bytes)
                        await self.show_catalog(update, context)
                        return

                    elif status_data['status'] == 'processing':
                        await self.send_message(update, "⏳ Обработка продолжается...")
                    else:
                        await self.send_message(update, "❌ Ошибка на стороне нейронки. Повторите позже.")
                        await self.show_catalog(update, context)
                        return

            except Exception as e:
                logging.error(f"Error with status image: {e}")
                await self.send_message(update, "❌ Не удалось получить статус. Повторная попытка...")
    
        await self.send_message(update, "❌ Максимальное количество попыток достигнуто. Попробуйте позже.")
        await asyncio.sleep(3)
        await self.show_catalog(update, context)
        
    async def show_catalog(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        current_index = context.user_data.get('current_product_index', 0)

        if current_index < 0 or current_index >= len(self.products):
            await self.send_message(update, "❌ Продукт не найден.")
            return

        product = self.products[current_index]
        product_text = (
            f"🛍️ *Название:* {escape_special_chars(product.name)}\n"
            f"🆔 *Модель:* {escape_special_chars(product.model)}\n"
            f"🎨 *Цвет:* {escape_special_chars(product.color)}\n"
            f"📜 *Описание:* {escape_special_chars(product.description)}\n"
        )
        
        keyboard = self.get_product_keyboard()

        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_photo(
                photo=product.image_url,
                caption=product_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await update.message.reply_photo(
                photo=product.image_url,
                caption=product_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )

    def get_product_keyboard(self):
        keyboard = [
            [InlineKeyboardButton("◀️ Назад", callback_data='previous_product'),
             InlineKeyboardButton("▶️ Вперед", callback_data='next_product')],
            [InlineKeyboardButton("✅ *Выбрать*", callback_data='select_product'),
             InlineKeyboardButton("🔙 В меню", callback_data='return_to_menu')]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def next_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['current_product_index'] = (context.user_data['current_product_index'] + 1) % len(self.products)
        await self.show_catalog(update, context)

    async def previous_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['current_product_index'] = (context.user_data['current_product_index'] - 1) % len(self.products)
        await self.show_catalog(update, context)

    async def select_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        product = self.products[context.user_data['current_product_index']]
        await self.send_message(update, f"✅ Вы выбрали: {product.name}.\n*Теперь отправьте фото в jpeg/jpg/png*")

    async def handle_button_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query.data
        await update.callback_query.answer()

        command = self.command_map.get(query)
        if command:
            await command(update, context)
        else:
            await self.send_message(update, "❌ Неизвестная команда.")