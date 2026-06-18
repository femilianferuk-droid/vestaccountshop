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
app.secret_key = 'vest_account_slug_fixed_2026'

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

# ИСПРАВЛЕНО: Карта локаций со слагами для безопасной передачи через URL без искажения кириллицы
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
    session_string = db.Column(db.Text, nullable=False)
    is_sold = db.Column(db.Boolean, default=False)

class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    country = db.Column(db.String(50), nullable=False)

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    crypto_invoice_id = db.Column(db.BigInteger, nullable=False)
    amount = db.Column(db.Float, nullable=False) # В USDT
    status = db.Column(db.String(20), default='active')

class CountryPrice(db.Model):
    country = db.Column(db.String(50), primary_key=True)
    price = db.Column(db.Float, default=250.0) # В Рублях (₽)

# Синхронизация таблиц и цен администратора
with app.app_context():
    db.create_all()
    for slug, data in COUNTRY_MAP.items():
        if not CountryPrice.query.get(data['name']):
            db.session.add(CountryPrice(country=data['name'], price=250.0))
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

# --- Минималистичный Шаблон (Apple / Stripe style) ---
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Vest Account</title>
    <style>
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; 
            background-color: #f6f8fa; color: #24292f; margin: 0; padding-bottom: 90px;
        }
        .header { 
            display: flex; justify-content: space-between; align-items: center; 
            padding: 14px 20px; background: #ffffff; border-bottom: 1px solid #d0d7de;
        }
        
        @keyframes blueFlow {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        .logo { 
            font-size: 19px; font-weight: 700; color: #0066cc; text-decoration: none; letter-spacing: -0.3px;
            background: linear-gradient(90deg, #0055cc, #0088ff, #0055cc); background-size: 200% auto;
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            animation: blueFlow 4s ease infinite;
        }
        
        .auth-buttons { display: flex; gap: 8px; align-items: center; justify-content: flex-end; }
        .auth-buttons a { 
            color: #24292f; text-decoration: none; padding: 7px 12px; 
            border-radius: 6px; font-size: 13px; font-weight: 600; background: #f6f8fa; 
            border: 1px solid #d0d7de; transition: all 0.15s ease; white-space: nowrap;
        }
        .auth-buttons a:hover { background: #e2e8f0; color: #0066cc; border-color: #0066cc; }
        
        .container { max-width: 680px; width: 92%; margin: 24px auto; }
        .card { background: #ffffff; border: 1px solid #d0d7de; border-radius: 10px; padding: 20px; margin-bottom: 16px; box-shadow: 0 3px 6px rgba(140,149,159,0.04); }
        
        .btn { 
            background: #0066cc; color: #ffffff; border: none; padding: 10px 18px; border-radius: 6px; 
            cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; text-align: center; font-size: 13px;
            transition: background 0.15s ease, transform 0.1s ease;
        }
        .btn:hover { background: #0055b3; }
        .btn:active { transform: scale(0.98); }
        
        .btn-secondary { background: #f6f8fa; color: #24292f; border: 1px solid #d0d7de; }
        .btn-secondary:hover { background: #f3f4f6; }
        
        .country-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; }
        .country-card { background: #ffffff; border: 1px solid #d0d7de; padding: 18px 12px; border-radius: 10px; text-align: center; }
        .country-flag { font-size: 34px; margin-bottom: 6px; display: block; }
        .country-name { font-size: 14px; font-weight: 600; color: #24292f; margin-bottom: 4px; }
        .country-price { font-size: 13px; color: #57606a; margin-bottom: 12px; font-weight: 500; }
        
        .bottom-nav { 
            position: fixed; bottom: 0; left: 0; right: 0; height: 62px; 
            background: #ffffff; border-top: 1px solid #d0d7de; display: flex; justify-content: space-around; align-items: center; z-index: 999;
        }
        .nav-item { font-size: 22px; text-decoration: none; color: #8c959f; padding: 10px; transition: color 0.15s; }
        .nav-item.active { color: #0066cc; }
        
        .form-group { margin-bottom: 14px; }
        .form-group label { display: block; margin-bottom: 5px; color: #24292f; font-size: 13px; font-weight: 600; }
        .form-group input, .form-group select { 
            width: 100%; padding: 10px; box-sizing: border-box; background: #ffffff; 
            border: 1px solid #cbd5e1; color: #0f172a; border-radius: 6px; font-size: 14px;
        }
        .form-group input:focus { border-color: #0066cc; outline: none; box-shadow: 0 0 0 3px rgba(0,102,204,0.15); }
        
        .flash { padding: 10px 14px; background: #ffebe9; color: #cf222e; border-radius: 6px; margin-bottom: 16px; border: 1px solid #ffcecb; font-size: 13px; }
        .flash.success { background: #dafbe1; color: #1a7f37; border: 1px solid #cee7d3; }
        
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #d0d7de; white-space: nowrap; }
        th { background: #f6f8fa; color: #57606a; font-weight: 600; }
        
        @media (max-width: 480px) {
            .header { padding: 10px 14px; }
            .logo { font-size: 17px; }
            .auth-buttons { gap: 6px; }
            .auth-buttons a { padding: 5px 9px; font-size: 12px; }
            .country-list { grid-template-columns: repeat(2, 1fr); gap: 8px; }
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
                <a href="/logout" class="btn-secondary" style="padding: 5px 10px; font-size:12px; margin:0; border-radius:5px;">Выйти</a>
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
    prices = {p.country: int(p.price) for p in CountryPrice.query.all()}
    html = """
    <h3 style="font-weight:700; margin-bottom:14px;">Каталог локаций</h3>
    <div class="country-list">
        {% for slug, data in country_map.items() %}
            <div class="country-card">
                <span class="country-flag">{{ data.flag }}</span>
                <div class="country-name">{{ data.name }}</div>
                <div class="country-price">{{ prices.get(data.name, 250) }} ₽</div>
                {% if session.get('user_id') %}
                    <a href="/shop" class="btn" style="padding:7px 10px; font-size:12px; width:100%; box-sizing:border-box;">Купить</a>
                {% else %}
                    <a href="/login" class="btn" style="padding:7px 10px; font-size:12px; width:100%; box-sizing:border-box;" onclick="alert('Для покупки необходимо авторизоваться!');">Купить</a>
                {% endif %}
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='home', country_map=COUNTRY_MAP, prices=prices)

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
def shop():
    if not session.get('user_id'): return redirect(url_for('login'))
    prices = {p.country: int(p.price) for p in CountryPrice.query.all()}
    html = """
    <h3 style="margin-bottom:14px; font-weight:700;">Покупка аккаунтов</h3>
    <div class="country-list">
        {% for slug, data in country_map.items() %}
            <div class="country-card">
                <span class="country-flag">{{ data.flag }}</span>
                <div class="country-name">{{ data.name }}</div>
                <div class="country-price">{{ prices.get(data.name, 250) }} ₽</div>
                <form action="/buy/{{ slug }}" method="POST">
                    <button type="submit" class="btn" style="padding:8px 12px; font-size:12px; width:100%;">Купить</button>
                </form>
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='shop', country_map=COUNTRY_MAP, prices=prices)

# ИСПРАВЛЕНО: Маршрут принимает безопасный буквенный slug вместо сырого русского текста
@app.route('/buy/<slug>', methods=['POST'])
def buy_account(slug):
    if not session.get('user_id'): return redirect(url_for('login'))
    
    # Принудительное обновление состояния сессии СУБД для получения свежего баланса
    db.session.expire_all()
    user = db.session.get(User, session['user_id'])
    
    # Извлекаем данные страны по полученному слагу
    target_data = COUNTRY_MAP.get(slug.lower().strip())
    if not target_data:
        flash('Выбранная локация не поддерживается платформой.', 'error')
        return redirect(url_for('shop'))
        
    country_name = target_data['name']
    price_rec = CountryPrice.query.get(country_name)
    price = price_rec.price if price_rec else 250.0
    
    if user.balance < price:
        flash(f'Недостаточно рублей на балансе! Требуется {int(price)} ₽ (У вас {int(user.balance)} ₽).', 'error')
        return redirect(url_for('profile'))
    
    # ИСПРАВЛЕНО: Строгий поиск по точному совпадению внутренней строки (минуя баги ILIKE в СУБД)
    account = Account.query.filter_by(country=country_name, is_sold=False).first()
    if not account:
        flash('Аккаунты данной локации временно закончились в базе!', 'error')
        return redirect(url_for('shop'))
    
    user.balance -= price
    account.is_sold = True
    db.session.add(Purchase(user_id=user.id, account_id=account.id, phone=account.phone, country=account.country))
    db.session.commit()
    
    flash(f'Успешная покупка! Выдан номер: {account.phone}', 'success')
    return redirect(url_for('purchases'))

@app.route('/purchases')
def purchases():
    if not session.get('user_id'): return redirect(url_for('login'))
    my_purchases = Purchase.query.filter_by(user_id=session['user_id']).all()
    html = """
    <h3 style="margin-bottom:14px; font-weight:700;">Мои покупки</h3>
    {% if not my_purchases %}<p style="color:#57606a; font-size:14px;">У вас нет совершенных покупок.</p>{% endif %}
    {% for p in my_purchases %}
        <div class="card" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; padding:14px 20px;">
            <div style="font-weight:600; font-size:14px;">{{ p.country }}: <code style="color:#0066cc; font-size:15px; background:#f6f8fa; padding:3px 6px; border-radius:4px; border:1px solid #d0d7de;">{{ p.phone }}</code></div>
            <div>
                <button class="btn" style="padding:6px 12px; font-size:12px;" onclick="getCode({{ p.account_id }}, this)">Получить код</button>
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
    return render_page(html, active_tab='purchases', my_purchases=my_purchases)

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
            user.balance += (inv.amount * 90)  # Скрытый курс: 1 USDT = 90 рублей
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
                flash('Баланс успешно обновлен!', 'success')
        elif 'set_price' in request.form:
            c = request.form.get('country')
            p = float(request.form.get('price'))
            cp = CountryPrice.query.get(c)
            if cp: cp.price = p
            else: db.session.add(CountryPrice(country=c, price=p))
            db.session.commit()
            flash(f'Новая цена для {c} успешно применена!', 'success')
            
    db.session.expire_all()
    all_users = User.query.order_by(User.id.desc()).all()
    all_accounts = Account.query.order_by(Account.id.desc()).all()
            
    html = """
    <h3>Панель управления</h3>
    <div class="card">
        <h4>1. Добавить аккаунт (Telethon)</h4>
        <form action="/admin/add_account" method="POST">
            <div class="form-group"><label>Номер телефона</label><input type="text" name="phone" placeholder="+7..." required></div>
            <button type="submit" class="btn" style="width:100%;">Выслать код авторизации</button>
        </form>
    </div>
    <div class="card">
        <h4>2. Настройка цен (в Рублях)</h4>
        <form method="POST"><input type="hidden" name="set_price" value="1">
            <div class="form-group">
                <label>Страна</label>
                <select name="country">
                    {% for slug, data in country_map.items() %}<option value="{{ data.name }}">{{ data.name }}</option>{% endfor %}
                </select>
            </div>
            <div class="form-group"><label>Цена (₽)</label><input type="number" name="price" required></div>
            <button type="submit" class="btn" style="width:100%;">Установить стоимость</button>
        </form>
    </div>
    <div class="card">
        <h4>3. Изменение баланса (в Рублях)</h4>
        <form method="POST"><input type="hidden" name="change_balance" value="1">
            <div class="form-group"><input type="text" name="username" placeholder="Логин" required></div>
            <div class="form-group"><input type="number" step="0.01" name="balance" placeholder="Баланс в ₽" required></div>
            <button type="submit" class="btn" style="width:100%;">Изменить баланс</button>
        </form>
    </div>

    <div class="card" style="padding: 16px 8px;">
        <h4 style="margin-left: 10px;">Зарегистрированные пользователи</h4>
        <div style="overflow-x:auto;">
            <table>
                <thead>
                    <tr><th>ID</th><th>Логин</th><th>Баланс</th><th>Статус</th></tr>
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
        <h4 style="margin-left: 10px;">Загруженный пул аккаунтов</h4>
        <div style="overflow-x:auto;">
            <table>
                <thead>
                    <tr><th>ID</th><th>Номер телефона</th><th>Локация</th><th>Статус</th></tr>
                </thead>
                <tbody>
                    {% for acc in all_accounts %}
                    <tr>
                        <td>{{ acc.id }}</td>
                        <td><code>{{ acc.phone }}</code></td>
                        <td>{{ acc.country }}</td>
                        <td>
                            {% if acc.is_sold %}
                                <span style="color:#cf222e; font-weight:600;">Продан</span>
                            {% else %}
                                <span style="color:#1a7f37; font-weight:600;">Доступен</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    """
    return render_page(html, active_tab='profile', country_map=COUNTRY_MAP, all_users=all_users, all_accounts=all_accounts, int=int)

@app.route('/admin/add_account', methods=['POST'])
def admin_add_account():
    if not session.get('is_admin'): return "No access", 403
    phone = request.form.get('phone').strip().replace(' ', '')
    country = detect_country(phone)
    session_str, phone_code_hash, err = tg_send_code(phone)
    if err:
        flash(f'Ошибка Telethon: {err}', 'error')
        return redirect(url_for('admin_panel'))
    PENDING_REGISTRATIONS[phone] = {'session_str': session_str, 'phone_code_hash': phone_code_hash, 'country': country}
    html = """
    <div class="card" style="max-width:400px; margin:auto;">
        <h3>Ввод кода подтверждения</h3>
        <p style="color:#64748b; font-size:14px;">Страна: <b>{{ country }}</b> | Телефон: <code>{{ phone }}</code></p>
        <form action="/admin/verify_account" method="POST">
            <input type="hidden" name="phone" value="{{ phone }}">
            <div class="form-group"><label>Код из Telegram</label><input type="text" name="code" required autocomplete="off"></div>
            <button type="submit" class="btn" style="width:100%;">Добавить аккаунт</button>
        </form>
    </div>
    """
    return render_page(html, active_tab='profile', phone=phone, country=country)

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
        
    db.session.add(Account(phone=phone, country=data['country'].strip(), session_string=final_session))
    db.session.commit()
    del PENDING_REGISTRATIONS[phone]
    flash('Аккаунт успешно сохранен в пул продаж!', 'success')
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
