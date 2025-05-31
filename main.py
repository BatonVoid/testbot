import asyncio
from aiogram import DefaultBotProperties
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, JSON, select
from sqlalchemy.sql import func
from sqlalchemy.orm import declarative_base, sessionmaker
import random
import logging
import json

TOKEN = "7909566566:AAEPuzHlvuME-WTOaL7jbGB_FHHCFtfG40Q"
TEST_START = datetime(2025, 5, 31, 0, 0)
TEST_END = datetime(2025, 5, 31, 23, 59, 59)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

# Вопросы
def load_questions_from_file():
    with open("questions.json", "r", encoding="utf-8") as f:
        question_data = json.load(f)

    with Session() as session:
        if session.query(Question).count() == 0:
            for q in question_data:
                session.add(Question(text=q["text"], options=q["options"], correct_option=q["correct"]))
            session.commit()
            logger.info("✅ Вопросы из файла загружены в БД.")

load_questions_from_file()

# Команда /start
@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    now = datetime.now()
    if not (TEST_START <= now <= TEST_END):
        await message.answer("Тест доступен только 31 мая с 00:00 до 23:59.")
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

    questions = db.query(Question.id).order_by(func.random()).limit(40).all()
    if not questions:
        logger.error("No questions found in database")
        await message.answer("Ошибка: вопросы не найдены.")
        await state.clear()
        return

    question_ids = [q.id for q in questions]
    logger.info(f"Selected 40 questions: {question_ids}")
    await state.update_data(index=0, questions=question_ids, score=0, full_name=full_name)
    await state.set_state(TestStates.in_test)
    await send_next_question(message.chat.id, state)

# Отправка вопроса
async def send_next_question(chat_id, state: FSMContext):
    data = await state.get_data()
    index = data.get("index", 0)
    question_ids = data.get("questions", [])

    if index >= len(question_ids):
        score = data.get("score", 0)
        user_name = data.get("full_name", "Путник")
        total_questions = len(question_ids)
        percentage = (score / total_questions * 100) if total_questions > 0 else 0

        user = db.query(User).filter_by(telegram_id=chat_id).first()
        if user:
            user.score = score
            user.completed = True
            db.commit()
        else:
            logger.error(f"User with telegram_id {chat_id} not found")
            await bot.send_message(chat_id, "Ошибка: пользователь не найден.")
            await state.clear()
            return

        result_message = (
            f"🌟 {user_name}, тест завершён! 🌟\n"
            f"Вы набрали <b>{score}</b> из <b>{total_questions}</b> баллов.\n"
            f"Процент правильных ответов: <b>{percentage:.1f}%</b>.\n"
        )
        if percentage >= 90:
            result_message += "Фантастический результат! Вы настоящий звёздный герой! 🚀"
        elif percentage >= 70:
            result_message += "Отличная работа! Вы сияете, как яркая звезда! ✨"
        elif percentage >= 50:
            result_message += "Хороший результат! Продолжайте идти к звёздам! 🌠"
        else:
            result_message += "Не сдавайтесь! Каждая попытка приближает вас к вершинам! 💪"

        await bot.send_message(chat_id, result_message)
        await state.clear()
        return

    q_id = question_ids[index]
    question = db.get(Question, q_id)
    if not question:
        logger.error(f"Question with id {q_id} not found")
        await bot.send_message(chat_id, "Вопрос не найден.")
        await state.update_data(index=index + 1)
        await send_next_question(chat_id, state)
        return

    kb = InlineKeyboardBuilder()
    for i, opt in enumerate(question.options):
        callback_data = f"q{q_id}o{i}"
        byte_length = len(callback_data.encode('utf-8'))
        if byte_length > 64:
            logger.error(f"Callback data too long ({byte_length} bytes): {callback_data}")
            callback_data = f"q{q_id}o{i}"[:64]
        kb.button(text=opt, callback_data=callback_data)
    kb.adjust(1)
    logger.info(f"Sending question {q_id}: {question.text} with callback_data: {[b.callback_data for row in kb.as_markup().inline_keyboard for b in row]}")

    await bot.send_message(chat_id, question.text, reply_markup=kb.as_markup())

    await asyncio.sleep(40)
    data = await state.get_data()
    if data.get("index", 0) == index:
        logger.info(f"User {chat_id} did not answer question {q_id} in time")
        await bot.send_message(chat_id, "Время вышло! Переходим к следующему вопросу.")
        await state.update_data(index=index + 1)
        await send_next_question(chat_id, state)

# Обработка ответа
@router.callback_query(TestStates.in_test)
async def handle_answer(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    index = data.get("index", 0)
    question_ids = data.get("questions", [])
    if index >= len(question_ids):
        await callback.answer("Тест завершён.", show_alert=True)
        return

    q_id = question_ids[index]
    question = db.get(Question, q_id)
    if not question:
        logger.error(f"Question with id {q_id} not found")
        await callback.answer("Ошибка: вопрос не найден.", show_alert=True)
        return

    try:
        if callback.data.startswith('q'):
            parts = callback.data.split('o')
            opt_id = int(parts[1])
            selected_option = question.options[opt_id]
            if selected_option == question.correct_option:
                score = data.get("score", 0) + 1
                await state.update_data(score=score)
                await callback.message.answer("Правильный ответ! 🎉")
            else:
                await callback.message.answer(f"Неправильно. Правильный ответ: {question.correct_option}")
    except (IndexError, ValueError) as e:
        logger.error(f"Invalid callback data: {callback.data}, error: {e}")
        await callback.answer("Ошибка при обработке ответа.", show_alert=True)
        return

    await callback.answer()
    await state.update_data(index=index + 1)
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
