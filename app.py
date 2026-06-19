import os
import re
import asyncio
import requests
import traceback
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient
from telethon.sessions import StringSession

app = Flask(__name__)
app.secret_key = 'vest_account_dark_cyber_2026'

# --- Глобальный обработчик ошибок ---
@app.errorhandler(500)
def internal_server_error(e):
    return f"<h2>Ошибка сервера (500)</h2><pre>{traceback.format_exc()}</pre>", 500

# --- Конфигурация PostgreSQL ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://bothost_db_a956eeb808dc:ECngxbt9uo_vUzq4-nLfUKv_PJ_jp111YhQB-LXlO9A@node1.pghost.ru:15791/bothost_db_a956eeb808dc'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Настройки API ---
API_ID = 32480523
API_HASH = '147839735c9fa4e83451209e9b55cfc5'
CRYPTO_BOT_TOKEN = '499354:AATdkiDyuC1tWd1ro5S5wFw6XcePNUNH5Ph'
CRYPTO_BOT_URL = 'https://pay.crypt.bot/api/'

PENDING_REGISTRATIONS = {}

CATEGORIES = {
    'regular': 'Обычные аккаунты',
    'warmed': 'Прогретые аккаунты',
    'aged': 'Аккаунты с отлегой'
}

COUNTRY_MAP = {
    'ru': {'name': 'Россия', 'flag': '🇷🇺'},
    'us': {'name': 'США', 'flag': '🇺🇸'},
    'ua': {'name': 'Украина', 'flag': '🇺🇦'},
    'kz': {'name': 'Казахстан', 'flag': '🇰🇿'},
    'by': {'name': 'Беларусь', 'flag': '🇧🇾'},
    'mm': {'name': 'Мьянма', 'flag': '🇲🇲'}
}

# --- Модели Базы Данных ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    is_admin = db.Column(db.Boolean, default=False)

class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    country = db.Column(db.String(50), nullable=False)
    category = db.Column(db.String(50), nullable=False, default='regular') 
    session_string = db.Column(db.Text, nullable=False)
    is_sold = db.Column(db.Boolean, default=False)

class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    country = db.Column(db.String(50), nullable=False)
    category = db.Column(db.String(50), nullable=False, default='regular')

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    crypto_invoice_id = db.Column(db.BigInteger, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='active')

class AccountPrice(db.Model):
    id = db.Column(db.String(100), primary_key=True) 
    price = db.Column(db.Float, default=250.0)

# Синхронизация структуры и миграции
with app.app_context():
    db.create_all()
    try:
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE account ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT 'regular';"))
            conn.execute(text("ALTER TABLE purchase ADD COLUMN IF NOT EXISTS category VARCHAR(50) DEFAULT 'regular';"))
            conn.commit()
    except Exception:
        pass

    for cat_key in CATEGORIES.keys():
        for c_slug, c_data in COUNTRY_MAP.items():
            price_id = f"{cat_key}_{c_data['name']}"
            if not AccountPrice.query.get(price_id):
                db.session.add(AccountPrice(id=price_id, price=300.0))
    db.session.commit()

# --- Логика работы с Telethon ---
def get_tg_client(session_instance=None):
    if session_instance is None:
        session_instance = StringSession()
    return TelegramClient(session_instance, API_ID, API_HASH, device_model="iPhone 15", system_version="iOS 17.2", app_version="10.4.1")

def tg_send_code(phone):
    async def _main():
        client = get_tg_client()
        await client.connect()
        try:
            res = await client.send_code_request(phone)
            return client.session.save(), res.phone_code_hash, None
        except Exception as e: return None, None, str(e)
        finally: await client.disconnect()
    return asyncio.run(_main())

def tg_sign_in(phone, code, phone_code_hash, session_str):
    async def _main():
        client = get_tg_client(StringSession(session_str))
        await client.connect()
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            return client.session.save(), None
        except Exception as e: return None, str(e)
        finally: await client.disconnect()
    return asyncio.run(_main())

def tg_get_latest_code(session_str):
    async def _main():
        client = get_tg_client(StringSession(session_str))
        await client.connect()
        try:
            if not await client.is_user_authorized(): return None, "Сессия закрыта."
            dialogs = await client.get_dialogs(limit=3)
            if not dialogs: return None, "Чаты пусты."
            messages = await client.get_messages(dialogs[0], limit=5)
            for m in messages:
                if m.text:
                    match = re.search(r'\b\d{5}\b', m.text)
                    if match: return match.group(0), None
            return None, "Код не найден."
        except Exception as e: return None, str(e)
        finally: await client.disconnect()
    return asyncio.run(_main())

def detect_country(phone):
    phone = re.sub(r'\D', '', phone)
    if phone.startswith('79') or phone.startswith('74') or phone.startswith('75'): return 'Россия'
    if phone.startswith('1'): return 'США'
    if phone.startswith('380'): return 'Украина'
    if phone.startswith('77') or phone.startswith('70'): return 'Казахстан'
    if phone.startswith('375'): return 'Беларусь'
    if phone.startswith('95'): return 'Мьянма'
    return 'Неизвестно'

# --- Премиальный Кибер-Темный Шаблон по скриншоту ---
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Vest Account</title>
    <style>
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; 
            background: radial-gradient(circle at top, #0f1626 0%, #070913 100%); 
            color: #ffffff; margin: 0; padding-bottom: 95px;
            -webkit-font-smoothing: antialiased;
        }
        .header { 
            display: flex; justify-content: space-between; align-items: center; 
            padding: 14px 20px; background: rgba(13, 17, 30, 0.6); border-bottom: 1px solid rgba(255,255,255,0.03);
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
        }
        
        @keyframes neonFlow {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        .logo-block { display: flex; align-items: center; gap: 10px; }
        .logo-square { 
            width: 36px; height: 36px; background: linear-gradient(135deg, #0052cc, #0088ff); 
            border-radius: 8px; display: flex; align-items: center; justify-content: center;
            font-weight: 900; font-size: 18px; color: #ffffff; box-shadow: 0 0 10px rgba(0,136,255,0.3);
        }
        .logo-text { display: flex; flex-direction: column; }
        .logo-title { font-size: 16px; font-weight: 700; color: #ffffff; letter-spacing: -0.3px; }
        .logo-subtitle { font-size: 10px; color: #6c768a; font-weight: 600; letter-spacing: 1px; }

        /* Капсула баланса со скриншота */
        .balance-pill {
            display: flex; align-items: center; background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.05); padding: 4px 4px 4px 12px; border-radius: 20px; gap: 8px;
        }
        .balance-label { font-size: 9px; color: #6c768a; font-weight: 700; letter-spacing: 0.5px; }
        .balance-value { font-size: 14px; font-weight: 700; color: #ffffff; }
        .balance-plus {
            width: 24px; height: 24px; background: #0088ff; border-radius: 50%;
            display: flex; align-items: center; justify-content: center; text-decoration: none;
            color: #ffffff; font-weight: 700; font-size: 14px; box-shadow: 0 0 8px rgba(0,136,255,0.4);
        }

        .container { max-width: 620px; width: 92%; margin: 24px auto; }
        .section-label { font-size: 11px; font-weight: 700; color: #6c768a; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 14px; }
        
        /* Карточки в стиле Mini App */
        .card { 
            background: rgba(22, 27, 46, 0.5); border: 1px solid rgba(255,255,255,0.03); border-radius: 14px; 
            padding: 20px; margin-bottom: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        }
        
        /* Интерактивные кнопки категорий со скриншота */
        .category-card {
            background: rgba(22, 27, 46, 0.4); border: 1px solid rgba(255,255,255,0.03); border-radius: 14px; 
            padding: 16px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;
            text-decoration: none; transition: all 0.2s ease;
        }
        .category-card:hover { background: rgba(30, 37, 62, 0.5); border-color: rgba(0,136,255,0.2); }
        .category-left { display: flex; align-items: center; gap: 14px; }
        .category-icon-wrapper {
            width: 42px; height: 42px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.03);
            border-radius: 10px; display: flex; align-items: center; justify-content: center;
        }
        .category-title-text { font-size: 15px; font-weight: 600; color: #ffffff; }
        .category-desc-text { font-size: 11px; color: #6c768a; margin-top: 2px; }
        .category-right { display: flex; align-items: center; gap: 10px; }
        .category-price-tag { font-size: 14px; font-weight: 700; color: #0088ff; }
        .category-arrow { color: rgba(255,255,255,0.15); font-size: 16px; font-weight: 300; }

        /* Переливающиеся синие кнопки */
        .btn-action { 
            background: linear-gradient(90deg, #0044cc, #0088ff, #0044cc); background-size: 200% auto;
            color: #ffffff; border: none; padding: 12px 20px; border-radius: 8px; 
            cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; text-align: center; font-size: 13px;
            animation: neonFlow 3s linear infinite; box-shadow: 0 0 12px rgba(0,136,255,0.25);
        }
        
        .btn-buy-inline {
            background: transparent; border: none; color: #0088ff; font-weight: 700; font-size: 14px;
            cursor: pointer; padding: 6px 12px; transition: all 0.2s;
        }
        .btn-buy-inline:hover { color: #ffffff; text-shadow: 0 0 8px rgba(0,136,255,0.6); }

        /* Строки стран */
        .country-inline-row { display: flex; flex-direction: column; gap: 8px; }
        .country-line-item {
            display: flex; justify-content: space-between; align-items: center;
            background: rgba(22, 27, 46, 0.3); border: 1px solid rgba(255,255,255,0.02); border-radius: 12px; padding: 12px 18px;
        }
        .country-info { display: flex; align-items: center; gap: 12px; font-weight: 600; font-size: 14px; color: #ffffff; }
        .country-flag { font-size: 20px; }
        
        /* Нижний схематичный бар со скриншота */
        .bottom-nav { 
            position: fixed; bottom: 0; left: 0; right: 0; height: 65px; 
            background: rgba(10, 12, 22, 0.9); border-top: 1px solid rgba(255,255,255,0.03); 
            display: flex; justify-content: space-around; align-items: center; z-index: 999;
            backdrop-filter: blur(15px); -webkit-backdrop-filter: blur(15px);
        }
        .nav-item { 
            display: flex; flex-direction: column; align-items: center; gap: 4px;
            text-decoration: none; color: #4e566d; padding: 6px 16px; border-radius: 12px; transition: all 0.2s ease;
        }
        .nav-icon-box { font-size: 18px; font-weight: 700; }
        .nav-text-box { font-size: 10px; font-weight: 700; letter-spacing: 0.2px; }
        
        /* Эффект свечения активной вкладки */
        .nav-item.active { 
            color: #0088ff; 
            background: radial-gradient(circle, rgba(0,136,255,0.12) 0%, rgba(0,136,255,0) 70%);
        }
        
        .form-group { margin-bottom: 14px; }
        .form-group label { display: block; margin-bottom: 6px; color: #6c768a; font-size: 12px; font-weight: 700; text-transform: uppercase; }
        .form-group input, .form-group select { 
            width: 100%; padding: 12px; box-sizing: border-box; background: rgba(255,255,255,0.03); 
            border: 1px solid rgba(255,255,255,0.05); color: #ffffff; border-radius: 8px; font-size: 14px;
        }
        .form-group input:focus, .form-group select:focus { border-color: #0088ff; outline: none; }
        
        .flash { padding: 12px; background: rgba(235, 94, 94, 0.1); color: #ff5e5e; border-radius: 8px; margin-bottom: 16px; border: 1px solid rgba(235,94,94,0.15); font-size: 13px; }
        .flash.success { background: rgba(46, 204, 113, 0.1); color: #2ecc71; border: 1px solid rgba(46,204,113,0.15); }
        
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }
        th, td { padding: 12px 10px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.03); white-space: nowrap; }
        th { background: rgba(255,255,255,0.02); color: #6c768a; font-weight: 600; }
        
        @media (max-width: 480px) {
            .header { padding: 12px 16px; }
            .container { width: 92%; margin: 20px auto; }
            .country-line-item { padding: 12px; }
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo-block">
            <div class="logo-square">V</div>
            <div class="logo-text">
                <span class="logo-title">Vest Account</span>
                <span class="logo-subtitle">МАГАЗИН</span>
            </div>
        </div>
        <div class="auth-buttons">
            {% if not session.get('user_id') %}
                <a href="/login">Войти</a>
                <a href="/register">Регистрация</a>
            {% else %}
                <div class="balance-pill">
                    <span class="balance-label">БАЛАНС</span>
                    <span class="balance-value">{{ current_user_balance }} ₽</span>
                    <a href="/profile" class="balance-plus">+</a>
                </div>
            {% endif %}
        </div>
    </div>
    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="flash {{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        {{ content | safe }}
    </div>
    {% if session.get('user_id') %}
    <div class="bottom-nav">
        <a href="/shop" class="nav-item {% if active_tab == 'shop' %}active{% endif %}">
            <span class="nav-icon-box">☷</span>
            <span class="nav-text-box">Каталог</span>
        </a>
        <a href="/purchases" class="nav-item {% if active_tab == 'purchases' %}active{% endif %}">
            <span class="nav-icon-box">📋</span>
            <span class="nav-text-box">Заказы</span>
        </a>
        <a href="/profile" class="nav-item {% if active_tab == 'profile' %}active{% endif %}">
            <span class="nav-icon-box">👤</span>
            <span class="nav-text-box">Профиль</span>
        </a>
    </div>
    {% endif %}
</body>
</html>
"""

def render_page(inner_template, active_tab='home', **kwargs):
    db.session.expire_all()
    bal = 0
    if session.get('user_id'):
        u = db.session.get(User, session['user_id'])
        if u: bal = int(u.balance)
    return render_template_string(BASE_TEMPLATE, content=render_template_string(inner_template, **kwargs), active_tab=active_tab, current_user_balance=bal)

# --- Маршруты страниц ---

@app.route('/')
def index():
    # Рассчитываем минимальные цены для каждой категории динамически
    min_prices = {}
    for cat_key in CATEGORIES.keys():
        lowest = None
        for c_slug, c_data in COUNTRY_MAP.items():
            rec = AccountPrice.query.get(f"{cat_key}_{c_data['name']}")
            if rec:
                if lowest is None or rec.price < lowest:
                    lowest = int(rec.price)
        min_prices[cat_key] = lowest or 300

    html = """
    <h2 style="font-size: 26px; font-weight: 800; margin: 0 0 4px 0; letter-spacing: -0.5px;">Каталог</h2>
    <p style="color:#6c768a; font-size:13px; margin: 0 0 24px 0;">Telegram-аккаунты — в один тап</p>
    
    <div class="section-label">Все товары</div>
    <div class="category-box">
        <a href="/shop/regular" class="category-card">
            <div class="category-left">
                <div class="category-icon-wrapper" style="color:#0088ff;">📱</div>
                <div class="category-title-text">
                    <div>Обычные аккаунты</div>
                    <div class="category-desc-text">сразу в руки</div>
                </div>
            </div>
            <div class="category-right">
                <span class="category-price-tag">от {{ min_prices['regular'] }} ₽</span>
                <span class="category-arrow">›</span>
            </div>
        </a>
        
        <a href="/shop/warmed" class="category-card">
            <div class="category-left">
                <div class="category-icon-wrapper" style="color:#2ecc71;">🔥</div>
                <div class="category-title-text">
                    <div>Прогретые аккаунты</div>
                    <div class="category-desc-text">высокий траст</div>
                </div>
            </div>
            <div class="category-right">
                <span class="category-price-tag">от {{ min_prices['warmed'] }} ₽</span>
                <span class="category-arrow">›</span>
            </div>
        </a>
        
        <a href="/shop/aged" class="category-card">
            <div class="category-left">
                <div class="category-icon-wrapper" style="color:#f1c40f;">🛡️</div>
                <div class="category-title-text">
                    <div>Аккаунты с отлегой</div>
                    <div class="category-desc-text">по годам</div>
                </div>
            </div>
            <div class="category-right">
                <span class="category-price-tag">от {{ min_prices['aged'] }} ₽</span>
                <span class="category-arrow">›</span>
            </div>
        </a>
    </div>
    """
    return render_page(html, active_tab='shop', min_prices=min_prices)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Этот логин уже занят!', 'error')
            return redirect(url_for('register'))
        is_admin = (username == 'Vestnik' and password == '5533789q')
        db.session.add(User(username=username, password=generate_password_hash(password), is_admin=is_admin))
        db.session.commit()
        flash('Регистрация успешно выполнена!', 'success')
        return redirect(url_for('login'))
    return render_page('<div class="card" style="max-width:380px; margin:auto;"><h3 style="margin-top:0;">Регистрация</h3><form method="POST"><div class="form-group"><label>Логин</label><input type="text" name="username" required></div><div class="form-group"><label>Пароль</label><input type="password" name="password" required></div><button type="submit" class="btn-action" style="width:100%;">Зарегистрироваться</button></form></div>')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            return redirect(url_for('shop'))
        flash('Неверный логин или пароль!', 'error')
    return render_page('<div class="card" style="max-width:380px; margin:auto;"><h3 style="margin-top:0;">Вход в систему</h3><form method="POST"><div class="form-group"><label>Логин</label><input type="text" name="username" required></div><div class="form-group"><label>Пароль</label><input type="password" name="password" required></div><button type="submit" class="btn-action" style="width:100%;">Войти</button></form></div>')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/shop')
def shop():
    return redirect(url_for('index'))

@app.route('/shop/<cat_slug>')
def shop_category(cat_slug):
    if not session.get('user_id'): return redirect(url_for('login'))
    if cat_slug not in CATEGORIES: return redirect(url_for('index'))
    
    prices = {}
    for p in AccountPrice.query.all():
        if p.id.startswith(f"{cat_slug}_"):
            c_name = p.id.replace(f"{cat_slug}_", "")
            prices[c_name] = int(p.price)
            
    html = """
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
        <h2 style="font-size:22px; font-weight:800; margin:0;">{{ cat_name }}</h2>
        <a href="/" style="font-size:12px; color:#0088ff; text-decoration:none; font-weight:600;">Назад</a>
    </div>
    <p style="color:#6c768a; font-size:13px; margin-bottom:20px;">Выберите нужную гео-позицию товара</p>
    
    <div class="country-inline-row">
        {% for c_slug, data in country_map.items() %}
            <div class="country-line-item">
                <div class="country-info">
                    <span class="country-flag">{{ data.flag }}</span>
                    <span>{{ data.name }}</span>
                </div>
                <form action="/buy/{{ cat_slug }}/{{ c_slug }}" method="POST" style="margin:0;">
                    <button type="submit" class="btn-buy-inline">от {{ prices.get(data.name, 300) }} ₽ ›</button>
                </form>
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='shop', cat_slug=cat_slug, cat_name=CATEGORIES[cat_slug], country_map=COUNTRY_MAP, prices=prices)

@app.route('/buy/<cat_slug>/<c_slug>', methods=['POST'])
def buy_account(cat_slug, c_slug):
    if not session.get('user_id'): return redirect(url_for('login'))
    
    db.session.expire_all()
    user = db.session.get(User, session['user_id'])
    
    if cat_slug not in CATEGORIES or c_slug not in COUNTRY_MAP:
        flash('Некорректные параметры конфигурации.', 'error')
        return redirect(url_for('index'))
        
    country_name = COUNTRY_MAP[c_slug]['name']
    price_id = f"{cat_slug}_{country_name}"
    price_rec = AccountPrice.query.get(price_id)
    price = price_rec.price if price_rec else 300.0
    
    if user.balance < price:
        flash(f'Недостаточно рублей на балансе! Требуется {int(price)} ₽.', 'error')
        return redirect(url_for('profile'))
    
    account = Account.query.filter_by(category=cat_slug, country=country_name, is_sold=False).first()
    if not account:
        flash('Аккаунты данной локации временно отсутствуют!', 'error')
        return redirect(url_for('shop_category', cat_slug=cat_slug))
    
    user.balance -= price
    account.is_sold = True
    db.session.add(Purchase(user_id=user.id, account_id=account.id, phone=account.phone, country=account.country, category=cat_slug))
    db.session.commit()
    
    flash(f'Успешная покупка! Номер: {account.phone}', 'success')
    return redirect(url_for('purchases'))

@app.route('/purchases')
def purchases():
    if not session.get('user_id'): return redirect(url_for('login'))
    my_purchases = Purchase.query.filter_by(user_id=session['user_id']).all()
    html = """
    <h2 style="font-size: 24px; font-weight: 800; margin: 0 0 16px 0;">Мои Заказы</h2>
    {% if not my_purchases %}<p style="color:#6c768a; font-size:14px;">У вас нет совершенных покупок.</p>{% endif %}
    {% for p in my_purchases %}
        <div class="card" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; padding:16px;">
            <div style="font-size:14px; font-weight:600;">
                <span style="color:#6c768a; font-size:11px;">{{ cat_names.get(p.category) }}</span><br>
                {{ p.country }}: <code style="color:#0088ff; font-size:15px; background:rgba(255,255,255,0.02); padding:2px 6px; border-radius:4px; border:1px solid rgba(255,255,255,0.05);">{{ p.phone }}</code>
            </div>
            <div>
                <button class="btn-action" style="padding:8px 14px; font-size:12px; animation:none;" onclick="getCode({{ p.account_id }}, this)">Получить код</button>
                <div id="out-{{ p.account_id }}" style="margin-top:6px; font-weight:700; color:#2ecc71; font-size:13px; text-align:right;"></div>
            </div>
        </div>
    {% endfor %}
    <script>
    function getCode(id, btn) {
        btn.innerText = 'Поиск...';
        fetch('/get_code/' + id).then(r => r.json()).then(data => {
            btn.innerText = 'Получить код';
            const el = document.getElementById('out-'+id);
            if(data.success) { el.innerText = 'КОД: ' + data.code; el.style.color = '#2ecc71'; }
            else { el.innerText = data.message; el.style.color = '#ff5e5e'; }
        });
    }
    </script>
    """
    return render_page(html, active_tab='purchases', my_purchases=my_purchases, cat_names=CATEGORIES)

@app.route('/get_code/<int:account_id>')
def get_code(account_id):
    if not session.get('user_id'): return jsonify({'success': False, 'message': 'Войдите'})
    purchase = Purchase.query.filter_by(user_id=session['user_id'], account_id=account_id).first()
    if not purchase: return jsonify({'success': False, 'message': 'Отказ'})
    account = Account.query.get(account_id)
    try:
        code, err = tg_get_latest_code(account.session_string)
        if err: return jsonify({'success': False, 'message': err})
        return jsonify({'success': True, 'code': code})
    except Exception as e: return jsonify({'success': False, 'message': str(e)})

@app.route('/profile')
def profile():
    if not session.get('user_id'): return redirect(url_for('login'))
    db.session.expire_all()
    user = db.session.get(User, session['user_id'])
    html = """
    <div class="card">
        <h3 style="margin-top:0; font-weight:700;">Личный кабинет</h3>
        <p style="color:#6c768a; font-size:13px; margin-bottom:2px;">Баланс счета:</p>
        <div style="font-size:32px; font-weight:800; color:#2ecc71; margin-bottom:20px;">{{ int(user.balance) }} ₽</div>
        <hr style="border:none; border-top:1px solid rgba(255,255,255,0.05); margin:16px 0;">
        <h4 style="margin-top:0; font-weight:600;">Пополнение через Crypto Bot</h4>
        <form action="/deposit" method="POST" style="max-width:320px;">
            <div class="form-group">
                <label>Сумма к оплате в USDT</label>
                <input type="number" step="0.01" name="amount" value="10.00" required>
            </div>
            <button type="submit" class="btn-action" style="width:100%;">Выставить инвойс</button>
        </form>
        {% if session.get('is_admin') %}
            <a href="/admin" class="btn-action btn-secondary" style="width:100%; box-sizing:border-box; margin-top:14px; padding:9px 0; font-weight:600;">Панель Управления</a>
        {% endif %}
    </div>
    """
    return render_page(html, active_tab='profile', user=user, int=int)

@app.route('/deposit', methods=['POST'])
def deposit():
    if not session.get('user_id'): return redirect(url_for('login'))
    amount = request.form.get('amount')
    headers = {'Crypto-Pay-API-Token': CRYPTO_BOT_TOKEN}
    payload = {'asset': 'USDT', 'amount': str(amount), 'description': f'UID: {session["user_id"]}'}
    try:
        r = requests.post(CRYPTO_BOT_URL + 'createInvoice', json=payload, headers=headers).json()
        if r.get('ok'):
            data = r['result']
            inv = Invoice(user_id=session['user_id'], crypto_invoice_id=data['invoice_id'], amount=float(amount))
            db.session.add(inv)
            db.session.commit()
            html = """
            <div class="card" style="text-align:center; padding:24px;">
                <h3>Счет готов</h3>
                <p style="color:#6c768a; font-size:14px; margin-bottom:20px;">Для оплаты нажмите кнопку ниже:</p>
                <p><a href="{{ url }}" target="_blank" class="btn-action" style="padding:12px 24px;">Оплатить в Crypto Bot</a></p>
                <hr style="border:none; border-top:1px solid rgba(255,255,255,0.05); margin:20px 0;">
                <form action="/check_invoice/{{ inv_id }}" method="POST"><button type="submit" class="btn-action btn-secondary" style="width:100%;">Проверить оплату</button></form>
            </div>
            """
            return render_page(html, active_tab='profile', url=data['pay_url'], amount=amount, inv_id=inv.id)
    except Exception as e: flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('profile'))

@app.route('/check_invoice/<int:invoice_id>', methods=['POST'])
def check_invoice(invoice_id):
    inv = Invoice.query.get(invoice_id)
    if not inv or inv.status == 'paid': return redirect(url_for('profile'))
    headers = {'Crypto-Pay-API-Token': CRYPTO_BOT_TOKEN}
    try:
        r = requests.get(CRYPTO_BOT_URL + 'getInvoices', json={'invoice_ids': str(inv.crypto_invoice_id)}, headers=headers).json()
        if r.get('ok') and r['result']['items'] and r['result']['items'][0]['status'] == 'paid':
            user = db.session.get(User, inv.user_id)
            user.balance += (inv.amount * 90)
            inv.status = 'paid'
            db.session.commit()
            flash('Баланс успешно пополнен!', 'success')
        else: flash('Платеж не найден.', 'error')
    except Exception as e: flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('profile'))

# --- Панель Администратора ---

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if not session.get('is_admin'): return "No access", 403
    
    if request.method == 'POST':
        if 'change_balance' in request.form:
            u = User.query.filter_by(username=request.form.get('username').strip()).first()
            if u:
                u.balance = float(request.form.get('balance'))
                db.session.commit()
                flash('Баланс успешно изменен!', 'success')
        elif 'set_price' in request.form:
            cat = request.form.get('category')
            c_name = request.form.get('country')
            p = float(request.form.get('price'))
            price_id = f"{cat}_{c_name}"
            
            cp = AccountPrice.query.get(price_id)
            if cp: cp.price = p
            else: db.session.add(AccountPrice(id=price_id, price=p))
            db.session.commit()
            flash('Новая цена успешно зафиксирована!', 'success')
            
    db.session.expire_all()
    all_users = User.query.order_by(User.id.desc()).all()
    all_accounts = Account.query.order_by(Account.id.desc()).all()
            
    html = """
    <h3>Панель управления</h3>
    
    <div class="card">
        <h4 style="margin-top:0; font-weight:600;">1. Импортировать аккаунт (Telethon)</h4>
        <form action="/admin/add_account" method="POST">
            <div class="form-group">
                <label>Целевая категория добавления</label>
                <select name="category" style="background:#111; color:#fff;">
                    {% for key, val in categories.items() %}<option value="{{ key }}">{{ val }}</option>{% endfor %}
                </select>
            </div>
            <div class="form-group"><label>Номер телефона</label><input type="text" name="phone" placeholder="+7..." required></div>
            <button type="submit" class="btn-action" style="width:100%;">Запросить SMS код</button>
        </form>
    </div>
    
    <div class="card">
        <h4 style="margin-top:0; font-weight:600;">2. Изменение цен конфигураций (₽)</h4>
        <form method="POST"><input type="hidden" name="set_price" value="1">
            <div class="form-group">
                <label>Категория</label>
                <select name="category" style="background:#111; color:#fff;">
                    {% for key, val in categories.items() %}<option value="{{ key }}">{{ val }}</option>{% endfor %}
                </select>
            </div>
            <div class="form-group">
                <label>Страна</label>
                <select name="country" style="background:#111; color:#fff;">
                    {% for c_slug, data in country_map.items() %}<option value="{{ data.name }}">{{ data.name }}</option>{% endfor %}
                </select>
            </div>
            <div class="form-group"><label>Стоимость (₽)</label><input type="number" name="price" required></div>
            <button type="submit" class="btn-action" style="width:100%;">Применить цену</button>
        </form>
    </div>
    
    <div class="card">
        <h4 style="margin-top:0; font-weight:600;">3. Изменение баланса покупателям (₽)</h4>
        <form method="POST"><input type="hidden" name="change_balance" value="1">
            <div class="form-group"><input type="text" name="username" placeholder="Логин" required></div>
            <div class="form-group"><input type="number" step="0.01" name="balance" placeholder="Баланс в ₽" required></div>
            <button type="submit" class="btn-action" style="width:100%;">Изменить баланс</button>
        </form>
    </div>

    <div class="card" style="padding: 16px 8px;">
        <h4 style="margin-left: 10px; margin-top:0;">Список зарегистрированных пользователей</h4>
        <div style="overflow-x:auto;">
            <table>
                <thead>
                    <tr><th>ID</th><th>Логин</th><th>Баланс</th><th>Роль</th></tr>
                </thead>
                <tbody>
                    {% for u in all_users %}
                    <tr>
                        <td>{{ u.id }}</td>
                        <td><b>{{ u.username }}</b></td>
                        <td>{{ int(u.balance) }} ₽</td>
                        <td>{% if u.is_admin %}<span style="color:#0088ff; font-weight:600;">Админ</span>{% else %}Покупатель{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <div class="card" style="padding: 16px 8px;">
        <h4 style="margin-left: 10px; margin-top:0;">Загруженный пул аккаунтов</h4>
        <div style="overflow-x:auto;">
            <table>
                <thead>
                    <tr><th>ID</th><th>Номер телефона</th><th>Локация</th><th>Категория</th><th>Статус</th></tr>
                </thead>
                <tbody>
                    {% for acc in all_accounts %}
                    <tr>
                        <td>{{ acc.id }}</td>
                        <td><code>{{ acc.phone }}</code></td>
                        <td>{{ acc.country }}</td>
                        <td>{{ categories.get(acc.category, acc.category) }}</td>
                        <td>
                            {% if acc.is_sold %}<span style="color:#ff5e5e; font-weight:600;">Продан</span>
                            {% else %}<span style="color:#2ecc71; font-weight:600;">Доступен</span>{% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    """
    return render_page(html, active_tab='profile', categories=CATEGORIES, country_map=COUNTRY_MAP, all_users=all_users, all_accounts=all_accounts, int=int)

@app.route('/admin/add_account', methods=['POST'])
def admin_add_account():
    if not session.get('is_admin'): return "No access", 403
    phone = request.form.get('phone').strip().replace(' ', '')
    category = request.form.get('category')
    country = detect_country(phone)
    
    session_str, phone_code_hash, err = tg_send_code(phone)
    if err:
        flash(f'Ошибка Telethon API: {err}', 'error')
        return redirect(url_for('admin_panel'))
        
    PENDING_REGISTRATIONS[phone] = {'session_str': session_str, 'phone_code_hash': phone_code_hash, 'country': country, 'category': category}
    html = """
    <div class="card" style="max-width:400px; margin:auto;">
        <h3>Ввод SMS-кода</h3>
        <p style="color:#6c768a; font-size:13px;">Ветка: <b>{{ cat_name }}</b> | Гео: <b>{{ country }}</b></p>
        <form action="/admin/verify_account" method="POST">
            <input type="hidden" name="phone" value="{{ phone }}">
            <div class="form-group"><label>Код из Telegram уведомления</label><input type="text" name="code" required autocomplete="off"></div>
            <button type="submit" class="btn-action" style="width:100%;">Добавить аккаунт</button>
        </form>
    </div>
    """
    return render_page(html, active_tab='profile', phone=phone, country=country, cat_name=CATEGORIES[category])

@app.route('/admin/verify_account', methods=['POST'])
def admin_verify_account():
    if not session.get('is_admin'): return "No access", 403
    phone = request.form.get('phone')
    code = request.form.get('code').strip()
    if phone not in PENDING_REGISTRATIONS:
        flash('Сессия закрыта.', 'error')
        return redirect(url_for('admin_panel'))
    data = PENDING_REGISTRATIONS[phone]
    final_session, err = tg_sign_in(phone, code, data['phone_code_hash'], data['session_str'])
    if err:
        flash(f'Ошибка авторизации: {err}', 'error')
        return redirect(url_for('admin_panel'))
        
    db.session.add(Account(phone=phone, country=data['country'].strip(), category=data['category'], session_string=final_session))
    db.session.commit()
    del PENDING_REGISTRATIONS[phone]
    flash('Аккаунт успешно импортирован!', 'success')
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
