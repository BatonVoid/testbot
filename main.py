import asyncio
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from telegram import Bot, DefaultBotProperties
from telegram.ext import Application
import random
import logging
import json

TOKEN = "7909566566:AAEPuzHlvuME-WTOaL7jbGB_FHHCFtfG40Q"
TEST_START = datetime(2025, 5, 31, 0, 0)
TEST_END = datetime(2025, 5, 31, 23, 59, 59)

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode='HTML',  # Укажи нужный parse_mode
        # disable_web_page_preview=True,  # Если нужно
        # protect_content=True  # Если нужно
    )
)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# База данных
Base = declarative_base()
engine = create_engine("sqlite:///test.db")
Session = sessionmaker(bind=engine)
db = Session()

# Модели
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True)
    full_name = Column(String)
    score = Column(Integer, default=0)
    completed = Column(Boolean, default=False)

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    text = Column(String)
    options = Column(JSON)
    correct_option = Column(String)

Base.metadata.create_all(engine)

# Состояния FSM
class TestStates(StatesGroup):
    waiting_name = State()
    in_test = State()

# Вопросы (добавь свои реальные вопросы в БД заранее)
def load_questions_from_file():
    with open("questions.json", "r", encoding="utf-8") as f:
        question_data = json.load(f)

    with Session() as db:
        if db.query(Question).count() == 0:
            for q in question_data:
                db.add(Question(text=q["text"], options=q["options"], correct_option=q["correct"]))
            db.commit()
            logger.info("✅ Вопросы из файла загружены в БД.")

load_questions_from_file()

# Команда /start
@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    now = datetime.now()
    if not (TEST_START <= now <= TEST_END):
        await message.answer("Тест доступен только 1 мая с 15:00 до 16:00.")
        return
    await message.answer("Введите ваше ФИО для участия:")
    await state.set_state(TestStates.waiting_name)

# Ввод ФИО
@router.message(TestStates.waiting_name)
async def get_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    telegram_id = message.from_user.id

    user = db.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id, full_name=full_name)
        db.add(user)
        db.commit()

    questions = db.query(Question).all()
    random.shuffle(questions)
    await state.update_data(index=0, questions=[q.id for q in questions], score=0)
    await state.set_state(TestStates.in_test)
    await send_next_question(message.chat.id, state)

# Отправка вопроса
async def send_next_question(chat_id, state: FSMContext):
    data = await state.get_data()
    index = data["index"]
    question_ids = data["questions"]

    if index >= len(question_ids):
        score = data["score"]
        user = db.query(User).filter_by(telegram_id=chat_id).first()
        user.score = score
        user.completed = True
        db.commit()
        await bot.send_message(chat_id, f"Тест завершён! Вы набрали {score} баллов.")
        return

    q_id = question_ids[index]
    question = db.query(Question).get(q_id)

    kb = InlineKeyboardBuilder()
    for opt in question.options:
        kb.button(text=opt, callback_data=opt)
    await bot.send_message(chat_id, question.text, reply_markup=kb.as_markup())

    # Таймер 40 сек
    await asyncio.sleep(40)
    data = await state.get_data()
    if data["index"] == index:  # пользователь не ответил
        await state.update_data(index=index+1)
        await send_next_question(chat_id, state)

# Обработка ответа
@router.callback_query(TestStates.in_test)
async def handle_answer(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    index = data["index"]
    question_ids = data["questions"]
    q_id = question_ids[index]
    question = db.query(Question).get(q_id)

    if callback.data == question.correct_option:
        score = data["score"] + 1
        await state.update_data(score=score)
    await callback.answer("Ответ принят.")
    await state.update_data(index=index+1)
    await send_next_question(callback.message.chat.id, state)

# Команда /top10
@router.message(Command("top10"))
async def show_top10(message: Message):
    top_users = db.query(User).filter(User.completed == True).order_by(User.score.desc()).limit(10).all()
    text = "<b>Топ 10 участников:</b>\n"
    for i, user in enumerate(top_users, 1):
        text += f"{i}. {user.full_name} — {user.score} баллов\n"
    await message.answer(text)

# Команда /stats
@router.message(Command("stats"))
async def show_stats(message: Message):
    total = db.query(User).count()
    completed = db.query(User).filter(User.completed == True).count()
    await message.answer(f"Участвовало: {total} человек.\nЗавершили тест: {completed}")

# Запуск
if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
