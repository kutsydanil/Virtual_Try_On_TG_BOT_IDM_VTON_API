import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from services.product_service import ProductService
from services.upload_service import UploadService
from telegram.constants import ParseMode
from helpers.telegram_helpers import escape_special_chars 
import os
import httpx
import asyncio
import base64

class TelegramHandler:
    """Handler for managing Telegram bot interactions."""
    
    def __init__(self, product_service: ProductService, upload_service: UploadService, base_url_api: str) -> None:
        self.product_service = product_service
        self.upload_service = upload_service
        self.base_url_api = base_url_api
        self.current_product_index = 0
        self.products = []
        self.button_handlers = {
            'how_to_send_photo': self.handle_how_to_send_photo,
            'list_of_products': self.handle_list_of_products,
            'faq': self.handle_faq,
            'help': self.help_command,
            'show_catalog':self.show_catalog,
            'start_menu':self.start_menu,
            'return_to_menu':self.return_to_menu
        }

    async def start_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Display the start menu and fetch products."""
        self.products = await self.product_service.fetch_products()
        
        keyboard = [
            [InlineKeyboardButton("📦 Показать каталог", callback_data='show_catalog')],
            [InlineKeyboardButton("❓ Помощь", callback_data='help')]
        ]
    
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Привет! Выберите опцию:", reply_markup=reply_markup)

    async def handle_how_to_send_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("📸 Чтобы отправить изображение, просто выберите интересующий продукт и отправьте отправьте свое фото мне.")

    async def handle_list_of_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        if not self.products:            
            await update.callback_query.message.reply_text("❌ Не удалось получить список товаров.")
            return
        
        product_list = "\n".join([f"{i+1}. {product.name}" for i, product in enumerate(self.products)])
        await update.callback_query.message.reply_text(f"🛍️ Вот список доступных товаров:\n{product_list}")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            await update.callback_query.answer()

        keyboard = [
            [InlineKeyboardButton("🔙 В меню", callback_data='return_to_menu')],
            [InlineKeyboardButton("📸 Как отправить изображение?", callback_data='how_to_send_photo')],
            [InlineKeyboardButton("🛍️ Список товаров", callback_data='list_of_products')],
            [InlineKeyboardButton("❓ Часто задаваемые вопросы", callback_data='faq')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        help_text = "👋 Привет! Я ваш помощник в потенциальном онлайн-магазине. Нажмите на кнопку ниже, чтобы получить помощь."
        
        if update.callback_query:
            await update.callback_query.message.reply_text(help_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(help_text, reply_markup=reply_markup)

    async def handle_faq(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("❓ Имитация пункта меню")

    async def return_to_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("📦 Показать каталог", callback_data='show_catalog')],
            [InlineKeyboardButton("❓ Помощь", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.reply_text("Вы вернулись в меню. Выберите опцию:", reply_markup=reply_markup)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photo uploads from users."""
        
        await update.message.reply_text("⏳ Получаю ваше изображение... Это может занять пару секунд.")
        
        selected_product = self.products[self.current_product_index]
        
        if not selected_product:
            await update.message.reply_text("❌ Сначала выберите продукт.")
            return
        try:
            user_photo_file = await update.message.photo[-1].get_file()
            user_photo_extension = os.path.splitext(user_photo_file.file_path)[1]
            user_photo_bytes = await user_photo_file.download_as_bytearray()

            if user_photo_extension.lower() not in ['.png', '.jpg', '.jpeg']:
                await update.message.reply_text("❌ Пожалуйста, загрузите фото в формате JPG или PNG.")
                return
            
        except Exception as e:
            logging.error(f"Error with input file: {e}")
            await update.message.reply_text("❌ Произошла ошибка при чтении файла. Возможно, у файла нет расширения.")
        try:
            product_info = {
                'product_description': selected_product.description,
            }
            product_image_url = selected_product.image_url
            product_image_extension = os.path.splitext(product_image_url)[1]

            async with httpx.AsyncClient() as client:
                product_image_response = await client.get(product_image_url)
                product_image_response.raise_for_status()
                product_image_bytes = product_image_response.content
                
                product_image_base64 = base64.b64encode(product_image_bytes).decode('utf-8')
                user_photo_base64 = base64.b64encode(user_photo_bytes).decode('utf-8')
                response = await self.upload_service.upload_files(user_photo_base64, user_photo_extension, product_image_base64, 
                                                                  product_image_extension, product_info)
                if response and response.get('task_id'):
                    task_id = response['task_id']
                    await update.message.reply_text(f"✅ Файл загружен. Начинаю отправку на нейронку...")
                    await self.poll_status(update, task_id)
                else:
                    await update.message.reply_text("❌ Произошла ошибка при загрузке файла.")

        except Exception as e:
            logging.error(f"Error: {e}")

    async def poll_status(self, update: Update, task_id) -> None:
        """Poll the status of the processing task."""
        max_attempts = 5
        attempt = 0
        flag = True

        async with httpx.AsyncClient() as client:
            while attempt < max_attempts and flag:
                await asyncio.sleep(12)
                attempt += 1
                try:
                    status_response = await client.get(f"{self.base_url_api}/status/{task_id}")
                    status_response.raise_for_status()
                    
                    if status_response.status_code == 200:
                        status_data = status_response.json()
                        if status_data['status'] == 'completed':
                            await update.message.reply_text("✅ Обработка завершена!")
                            processed_image_base64 = status_data['result']
                            img_bytes = base64.b64decode(processed_image_base64)
                            await update.message.reply_photo(photo=img_bytes)
                            await self.show_catalog(update)
                            flag = False

                        elif status_data['status'] == 'processing':
                            await update.message.reply_text("⏳ Обработка еще продолжается...")
                        else:
                            await update.message.reply_text("❌ Ошибка на стороне нейронки.... Повторите снова")
                            flag = False
                    
                except Exception as e:
                    logging.error(f"Ошибка при получении статуса задачи: {e}")
                    await update.message.reply_text("❌ Не удалось получить статус задачи. Повторная попытка...")
                    await self.show_catalog(update)
                    flag = False
            
            if attempt >= max_attempts:
                await update.message.reply_text("❌ Максимальное количество попыток достигнуто. Пожалуйста, попробуйте снова позже.")

    async def handle_button_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle button clicks in the bot interface."""
        
        query = update.callback_query.data
        
        if query == 'next_product':
            self.current_product_index += 1
            if self.current_product_index >= len(self.products):
                self.current_product_index = 0  
            await self.show_catalog(update, context)
        elif query == 'previous_product':
            self.current_product_index -= 1
            if self.current_product_index < 0:
                self.current_product_index = len(self.products) - 1 
            await self.show_catalog(update, context)
        elif query == 'select_product':
            product = self.products[self.current_product_index]
            await update.callback_query.message.reply_text(f"✅ Вы выбрали: {product.name}.\n*Теперь отправьте свое фото в jpeg/jpg/png*")
        else:
            if query in self.button_handlers:
                await self.button_handlers[query](update, context)

    async def show_catalog(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the catalog of products."""
        
        if not self.products:
            await update.callback_query.message.reply_text("❌ Не удалось получить список товаров.")
            return
        
        product = self.products[self.current_product_index]
        
        product_text = (
            f"🛍️ *Название:* {escape_special_chars(product.name)}\n"
            f"🆔 *Модель:* {escape_special_chars(product.model)}\n"
            f"🎨 *Цвет:* {escape_special_chars(product.color)}\n"
            f"📜 *Описание:* {escape_special_chars(product.description)}\n"
        )
        
        keyboard = [
            [InlineKeyboardButton("◀️ Назад", callback_data='previous_product'),
             InlineKeyboardButton("▶️ Вперед", callback_data='next_product')],
            [InlineKeyboardButton("✅ *Выбрать*", callback_data='select_product'),
             InlineKeyboardButton("🔙 В меню", callback_data='return_to_menu')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.message.reply_photo(photo=product.image_url, caption=product_text,
                                                         reply_markup=reply_markup,
                                                         parse_mode=ParseMode.MARKDOWN_V2)