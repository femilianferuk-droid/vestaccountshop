import os
import re
import asyncio
import requests
import traceback
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from telethon import TelegramClient
from telethon.sessions import StringSession

app = Flask(__name__)
app.secret_key = 'vest_account_matte_premium_2026'

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

# Структура типов аккаунтов и стран
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
    balance = db.Column(db.Float, default=0.0) # В Рублях (₽)
    is_admin = db.Column(db.Boolean, default=False)

class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    country = db.Column(db.String(50), nullable=False)
    category = db.Column(db.String(50), nullable=False) # regular, warmed, aged
    session_string = db.Column(db.Text, nullable=False)
    is_sold = db.Column(db.Boolean, default=False)

class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    country = db.Column(db.String(50), nullable=False)
    category = db.Column(db.String(50), nullable=False)

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    crypto_invoice_id = db.Column(db.BigInteger, nullable=False)
    amount = db.Column(db.Float, nullable=False) # В USDT
    status = db.Column(db.String(20), default='active')

class AccountPrice(db.Model):
    id = db.Column(db.String(100), primary_key=True) # Формат: "category_country"
    price = db.Column(db.Float, default=250.0)

# Синхронизация БД и генерация матрицы цен
with app.app_context():
    db.create_all()
    for cat_key in CATEGORIES.keys():
        for c_slug, c_data in COUNTRY_MAP.items():
            price_id = f"{cat_key}_{c_data['name']}"
            if not AccountPrice.query.get(price_id):
                db.session.add(AccountPrice(id=price_id, price=300.0))
    db.session.commit()

# --- Логика Telethon ---
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

# --- Матовый Высококлассный Шаблон (White + Matte Gray + Shimmer Blue) ---
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Vest Account</title>
    <style>
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Arial, sans-serif; 
            background-color: #f4f6f8; color: #1e2329; margin: 0; padding-bottom: 95px;
        }
        .header { 
            display: flex; justify-content: space-between; align-items: center; 
            padding: 16px 24px; background: #ffffff; border-bottom: 1px solid #e1e4e8;
            box-shadow: 0 2px 4px rgba(0,0,0,0.02);
        }
        
        @keyframes blueShimmer {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        .logo { 
            font-size: 20px; font-weight: 700; color: #0066cc; text-decoration: none; letter-spacing: -0.4px;
            background: linear-gradient(90deg, #0052cc, #0088ff, #0033aa, #0052cc); background-size: 300% auto;
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            animation: blueShimmer 5s ease infinite;
        }
        
        .auth-buttons { display: flex; gap: 10px; align-items: center; }
        .auth-buttons a { 
            color: #475467; text-decoration: none; padding: 8px 14px; border: 1px solid #d0d5dd;
            border-radius: 8px; font-size: 13px; font-weight: 600; background: #ffffff; 
            transition: all 0.2s ease; white-space: nowrap; box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }
        .auth-buttons a:hover { background: #f8f9fa; border-color: #0066cc; color: #0066cc; }
        
        .container { max-width: 680px; width: 92%; margin: 28px auto; }
        
        /* Матовые премиальные карточки */
        .card { 
            background: #ffffff; border: 1px solid #e1e4e8; border-radius: 12px; 
            padding: 24px; margin-bottom: 18px; box-shadow: 0 4px 12px rgba(140,149,159,0.06);
        }
        .card-gray {
            background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; margin-top: 12px;
        }
        
        /* Кнопки с матовым глубоким переливом синего */
        .btn { 
            background: linear-gradient(90deg, #0052cc, #0088ff, #0052cc); background-size: 200% auto;
            color: #ffffff; border: none; padding: 12px 22px; border-radius: 8px; 
            cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; text-align: center; font-size: 13px;
            animation: blueShimmer 4s linear infinite; box-shadow: 0 2px 6px rgba(0,102,204,0.2);
            transition: transform 0.15s ease;
        }
        .btn:hover { background-position: right center; transform: translateY(-1px); }
        .btn:active { transform: scale(0.98); }
        
        .btn-secondary { background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; animation: none; box-shadow: none; }
        .btn-secondary:hover { background: #e2e8f0; transform: none; }
        
        /* Категории на главной */
        .category-box {
            display: flex; flex-direction: column; gap: 12px; margin-top: 16px;
        }
        .category-card {
            background: #ffffff; border: 1px solid #e1e4e8; border-radius: 12px; padding: 20px;
            display: flex; justify-content: space-between; align-items: center; text-decoration: none; color: inherit;
            transition: all 0.2s ease; box-shadow: 0 2px 4px rgba(0,0,0,0.02);
        }
        .category-card:hover { border-color: #0066cc; background: #f8faff; transform: translateX(3px); }
        .category-title { font-size: 16px; font-weight: 600; color: #1f2328; }
        .category-arrow { color: #0066cc; font-weight: bold; font-size: 16px; }

        /* В СТРОЧКУ страны */
        .country-inline-row { 
            display: flex; flex-direction: column; gap: 10px; margin-top: 16px;
        }
        .country-line-item {
            display: flex; justify-content: space-between; align-items: center;
            background: #ffffff; border: 1px solid #e1e4e8; border-radius: 10px; padding: 12px 18px;
        }
        .country-info { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 14px; }
        .country-flag { font-size: 24px; }
        
        .bottom-nav { 
            position: fixed; bottom: 0; left: 0; right: 0; height: 64px; 
            background: #ffffff; border-top: 1px solid #e1e4e8; display: flex; justify-content: space-around; align-items: center; z-index: 999;
            box-shadow: 0 -2px 10px rgba(0,0,0,0.02);
        }
        .nav-item { font-size: 22px; text-decoration: none; color: #94a3b8; padding: 10px; transition: color 0.15s; }
        .nav-item.active { color: #0066cc; }
        
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; margin-bottom: 6px; color: #344054; font-size: 13px; font-weight: 600; }
        .form-group input, .form-group select { 
            width: 100%; padding: 11px; box-sizing: border-box; background: #f8fafc; 
            border: 1px solid #cbd5e1; color: #1f2328; border-radius: 8px; font-size: 14px; transition: all 0.2s;
        }
        .form-group input:focus, .form-group select:focus { background: #ffffff; border-color: #0066cc; outline: none; box-shadow: 0 0 0 3px rgba(0,102,204,0.12); }
        
        .flash { padding: 12px 16px; background: #fef2f2; color: #b91c1c; border-radius: 8px; margin-bottom: 16px; border: 1px solid #fee2e2; font-size: 13px; }
        .flash.success { background: #f0fdf4; color: #15803d; border: 1px solid #dcfce7; }
        
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; background: #ffffff; }
        th, td { padding: 12px 10px; text-align: left; border-bottom: 1px solid #e2e8f0; white-space: nowrap; }
        th { background: #f1f5f9; color: #475569; font-weight: 600; }
        
        @media (max-width: 480px) {
            .header { padding: 12px 16px; }
            .logo { font-size: 18px; }
            .auth-buttons { gap: 6px; }
            .auth-buttons a { padding: 6px 10px; font-size: 12px; }
            .country-line-item { flex-direction: column; align-items: flex-start; gap: 12px; padding: 14px; }
            .country-line-item form { width: 100%; }
            .btn { width: 100%; box-sizing: border-box; }
        }
    </style>
</head>
<body>
    <div class="header">
        <a href="/" class="logo">Vest Account</a>
        <div class="auth-buttons">
            {% if not session.get('user_id') %}
                <a href="/login">Войти</a>
                <a href="/register">Регистрация</a>
            {% else %}
                <a href="/logout" class="btn-secondary" style="padding: 6px 12px; font-size:12px; margin:0; border-radius:6px;">Выйти</a>
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
        <a href="/shop" class="nav-item {% if active_tab == 'shop' %}active{% endif %}">🛍️</a>
        <a href="/purchases" class="nav-item {% if active_tab == 'purchases' %}active{% endif %}">📦</a>
        <a href="/profile" class="nav-item {% if active_tab == 'profile' %}active{% endif %}">👤</a>
    </div>
    {% endif %}
</body>
</html>
"""

def render_page(inner_template, active_tab='home', **kwargs):
    return render_template_string(BASE_TEMPLATE, content=render_template_string(inner_template, **kwargs), active_tab=active_tab)

# --- Маршруты страниц ---

@app.route('/')
def index():
    html = """
    <h3 style="font-weight:700; margin-bottom:4px; letter-spacing:-0.2px;">Категории аккаунтов</h3>
    <p style="color:#64748b; font-size:13px; margin-bottom:20px;">Выберите нужный класс кодов для перехода к локациям</p>
    <div class="category-box">
        {% for key, name in categories.items() %}
            <a href="/shop/{{ key }}" class="category-card">
                <span class="category-title">{{ name }}</span>
                <span class="category-arrow">→</span>
            </a>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='home', categories=CATEGORIES)

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
        flash('Успешная регистрация!', 'success')
        return redirect(url_for('login'))
    return render_page('<div class="card" style="max-width:380px; margin:auto;"><h3 style="margin-top:0;">Регистрация</h3><form method="POST"><div class="form-group"><label>Логин</label><input type="text" name="username" required></div><div class="form-group"><label>Пароль</label><input type="password" name="password" required></div><button type="submit" class="btn" style="width:100%;">Зарегистрироваться</button></form></div>')

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
    return render_page('<div class="card" style="max-width:380px; margin:auto;"><h3 style="margin-top:0;">Вход в систему</h3><form method="POST"><div class="form-group"><label>Логин</label><input type="text" name="username" required></div><div class="form-group"><label>Пароль</label><input type="password" name="password" required></div><button type="submit" class="btn" style="width:100%;">Войти</button></form></div>')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/shop')
def shop_default():
    return redirect(url_for('shop_category', cat_slug='regular'))

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
        <h3 style="font-weight:700; margin:0;">{{ cat_name }}</h3>
        <a href="/" style="font-size:13px; color:#0066cc; text-decoration:none; font-weight:600;">Назад к категориям</a>
    </div>
    <p style="color:#64748b; font-size:13px; margin-bottom:20px;">Доступные гео-локации в данной ветке аккаунтов</p>
    
    <div class="country-inline-row">
        {% for c_slug, data in country_map.items() %}
            <div class="country-line-item">
                <div class="country-info">
                    <span class="country-flag">{{ data.flag }}</span>
                    <span>{{ data.name }}</span>
                </div>
                <div style="display:flex; align-items:center; gap:16px;">
                    <span style="font-size:14px; font-weight:700; color:#475569;">{{ prices.get(data.name, 300) }} ₽</span>
                    <form action="/buy/{{ cat_slug }}/{{ c_slug }}" method="POST" style="margin:0;">
                        <button type="submit" class="btn" style="padding:8px 16px; font-size:12px;">Купить</button>
                    </form>
                </div>
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
        flash('Некорректные параметры покупки.', 'error')
        return redirect(url_for('index'))
        
    country_name = COUNTRY_MAP[c_slug]['name']
    price_id = f"{cat_slug}_{country_name}"
    price_rec = AccountPrice.query.get(price_id)
    price = price_rec.price if price_rec else 300.0
    
    if user.balance < price:
        flash(f'Недостаточно рублей на балансе! Требуется {int(price)} ₽ (У вас {int(user.balance)} ₽).', 'error')
        return redirect(url_for('profile'))
    
    # Поиск по точным параметрам категории и страны
    account = Account.query.filter_by(category=cat_slug, country=country_name, is_sold=False).first()
    if not account:
        flash('Аккаунты данной конфигурации временно отсутствуют!', 'error')
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
    <h3 style="margin-bottom:14px; font-weight:700;">Мои покупки</h3>
    {% if not my_purchases %}<p style="color:#57606a; font-size:14px;">У вас нет совершенных покупок.</p>{% endif %}
    {% for p in my_purchases %}
        <div class="card" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; padding:16px 20px;">
            <div style="font-size:14px; font-weight:600;">
                <span style="color:#64748b; font-size:12px; block-size:display;">{{ cat_names.get(p.category) }}</span><br>
                {{ p.country }}: <code style="color:#0066cc; font-size:15px; background:#f1f5f9; padding:2px 6px; border-radius:4px;">{{ p.phone }}</code>
            </div>
            <div>
                <button class="btn" style="padding:8px 14px; font-size:12px;" onclick="getCode({{ p.account_id }}, this)">Получить код</button>
                <span id="out-{{ p.account_id }}" style="margin-left:10px; font-weight:700; color:#1a7f37; font-size:14px;"></span>
            </div>
        </div>
    {% endfor %}
    <script>
    function getCode(id, btn) {
        btn.innerText = 'Поиск...';
        fetch('/get_code/' + id).then(r => r.json()).then(data => {
            btn.innerText = 'Получить код';
            const el = document.getElementById('out-'+id);
            if(data.success) { el.innerText = 'КОД: ' + data.code; el.style.color = '#1a7f37'; }
            else { el.innerText = data.message; el.style.color = '#cf222e'; }
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
        <h3>Личный кабинет</h3>
        <p style="color:#57606a; font-size:13px; margin-bottom:2px;">Текущий баланс:</p>
        <div style="font-size:30px; font-weight:700; color:#24292f; margin-bottom:20px;">{{ int(user.balance) }} ₽</div>
        <hr style="border:none; border-top:1px solid #d0d7de; margin:16px 0;">
        <h4 style="margin-top:0; font-weight:600;">Пополнение через Crypto Bot</h4>
        <form action="/deposit" method="POST" style="max-width:320px;">
            <div class="form-group">
                <label>Сумма к оплате в USDT</label>
                <input type="number" step="0.01" name="amount" value="10.00" required>
            </div>
            <button type="submit" class="btn" style="width:100%;">Выставить инвойс</button>
        </form>
        {% if session.get('is_admin') %}
            <a href="/admin" class="btn btn-secondary" style="width:100%; box-sizing:border-box; margin-top:14px; padding:9px 0; font-weight:600;">Панель Управления</a>
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
                <p style="color:#57606a; font-size:14px; margin-bottom:20px;">Для оплаты нажмите кнопку ниже:</p>
                <p><a href="{{ url }}" target="_blank" class="btn" style="padding:12px 24px;">Оплатить в Crypto Bot</a></p>
                <hr style="border:none; border-top:1px solid #d0d7de; margin:20px 0;">
                <form action="/check_invoice/{{ inv_id }}" method="POST"><button type="submit" class="btn btn-secondary" style="width:100%;">Проверить оплату</button></form>
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
            user.balance += (inv.amount * 90) # Авто-конвертация без вывода текста курса
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
    <h3>Панель управления платформой</h3>
    
    <div class="card">
        <h4 style="margin-top:0; font-weight:600;">1. Импортировать аккаунт (Telethon)</h4>
        <form action="/admin/add_account" method="POST">
            <div class="form-group">
                <label>Целевая категория добавления</label>
                <select name="category">
                    {% for key, val in categories.items() %}<option value="{{ key }}">{{ val }}</option>{% endfor %}
                </select>
            </div>
            <div class="form-group"><label>Номер телефона</label><input type="text" name="phone" placeholder="+7..." required></div>
            <button type="submit" class="btn" style="width:100%;">Запросить SMS код</button>
        </form>
    </div>
    
    <div class="card">
        <h4 style="margin-top:0; font-weight:600;">2. Изменение цен конфигураций (₽)</h4>
        <form method="POST"><input type="hidden" name="set_price" value="1">
            <div class="form-group">
                <label>Категория</label>
                <select name="category">
                    {% for key, val in categories.items() %}<option value="{{ key }}">{{ val }}</option>{% endfor %}
                </select>
            </div>
            <div class="form-group">
                <label>Страна</label>
                <select name="country">
                    {% for c_slug, data in country_map.items() %}<option value="{{ data.name }}">{{ data.name }}</option>{% endfor %}
                </select>
            </div>
            <div class="form-group"><label>Стоимость (₽)</label><input type="number" name="price" required></div>
            <button type="submit" class="btn" style="width:100%;">Применить цену</button>
        </form>
    </div>
    
    <div class="card">
        <h4 style="margin-top:0; font-weight:600;">3. Изменение баланса покупателям (₽)</h4>
        <form method="POST"><input type="hidden" name="change_balance" value="1">
            <div class="form-group"><input type="text" name="username" placeholder="Логин" required></div>
            <div class="form-group"><input type="number" step="0.01" name="balance" placeholder="Баланс в ₽" required></div>
            <button type="submit" class="btn" style="width:100%;">Изменить баланс</button>
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
                        <td>{% if u.is_admin %}<span style="color:#0066cc; font-weight:600;">Админ</span>{% else %}Покупатель{% endif %}</td>
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
                            {% if acc.is_sold %}<span style="color:#cf222e; font-weight:600;">Продан</span>
                            {% else %}<span style="color:#1a7f37; font-weight:600;">Доступен</span>{% endif %}
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
        <p style="color:#64748b; font-size:13px;">Ветка: <b>{{ cat_name }}</b> | Гео: <b>{{ country }}</b></p>
        <form action="/admin/verify_account" method="POST">
            <input type="hidden" name="phone" value="{{ phone }}">
            <div class="form-group"><label>Код из Telegram уведомления</label><input type="text" name="code" required autocomplete="off"></div>
            <button type="submit" class="btn" style="width:100%;">Добавить аккаунт</button>
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
