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
app.secret_key = 'vest_account_clean_minimal_2026'

# --- Логирование ошибок ---
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

# --- Модели Базы Данных ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    balance = db.Column(db.Float, default=0.0) # Баланс хранится в Рублях (₽)
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
    amount = db.Column(db.Float, nullable=False) # Сумма в USDT
    status = db.Column(db.String(20), default='active')

class CountryPrice(db.Model):
    country = db.Column(db.String(50), primary_key=True)
    price = db.Column(db.Float, default=300.0) # Цена в Рублях (₽)

# Инициализация таблиц и дефолтных цен
COUNTRIES_LIST = ['Россия', 'США', 'Украина', 'Казахстан', 'Беларусь', 'Мьянма']
FLAGS = {'Россия': '🇷🇺', 'США': '🇺🇸', 'Украина': '🇺🇦', 'Казахстан': '🇰🇿', 'Беларусь': '🇧🇾', 'Мьянма': '🇲🇲'}

with app.app_context():
    db.create_all()
    for c in COUNTRIES_LIST:
        if not CountryPrice.query.get(c):
            db.session.add(CountryPrice(country=c, price=250.0))
    db.session.commit()

# --- Логика работы с Telethon ---
def get_tg_client(session_instance=None):
    if session_instance is None:
        session_instance = StringSession()
    return TelegramClient(session_instance, API_ID, API_HASH, device_model="iPhone 15", system_version="iOS 17.0", app_version="10.0.1")

def tg_send_code(phone):
    async def _main():
        client = get_tg_client()
        await client.connect()
        try:
            res = await client.send_code_request(phone)
            return client.session.save(), res.phone_code_hash, None
        except Exception as e:
            return None, None, str(e)
        finally:
            await client.disconnect()
    return asyncio.run(_main())

def tg_sign_in(phone, code, phone_code_hash, session_str):
    async def _main():
        client = get_tg_client(StringSession(session_str))
        await client.connect()
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            return client.session.save(), None
        except Exception as e:
            return None, str(e)
        finally:
            await client.disconnect()
    return asyncio.run(_main())

def tg_get_latest_code(session_str):
    async def _main():
        client = get_tg_client(StringSession(session_str))
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return None, "Сессия закрыта."
            dialogs = await client.get_dialogs(limit=3)
            if not dialogs:
                return None, "Нет диалогов."
            messages = await client.get_messages(dialogs[0], limit=5)
            for m in messages:
                if m.text:
                    match = re.search(r'\b\d{5}\b', m.text)
                    if match: return match.group(0), None
            return None, "Код не найден."
        except Exception as e:
            return None, str(e)
        finally:
            await client.disconnect()
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

# --- Минималистичный бизнес-шаблон (Белый + Синий) ---
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
            background-color: #f8fafc; color: #0f172a; margin: 0; padding-bottom: 90px;
        }
        .header { 
            display: flex; justify-content: space-between; align-items: center; 
            padding: 16px 24px; background: #ffffff; border-bottom: 1px solid #e2e8f0;
        }
        .logo { 
            font-size: 20px; font-weight: 700; color: #0066cc; text-decoration: none; letter-spacing: -0.5px;
            transition: color 0.3s;
        }
        .logo:hover { color: #004499; }
        
        .auth-buttons { display: flex; gap: 8px; align-items: center; }
        .auth-buttons a { 
            color: #0f172a; text-decoration: none; padding: 8px 14px; 
            border-radius: 8px; font-size: 14px; font-weight: 500; background: #f1f5f9; transition: all 0.2s;
        }
        .auth-buttons a:hover { background: #e2e8f0; color: #0066cc; }
        
        .container { max-width: 650px; width: 92%; margin: 24px auto; }
        .card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        
        /* Кнопка с мягким профессиональным переливом синего */
        .btn { 
            background: linear-gradient(90deg, #0066cc, #0088ff, #0066cc); background-size: 200% auto;
            color: #ffffff; border: none; padding: 12px 20px; border-radius: 8px; 
            cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; text-align: center; font-size: 14px;
            transition: all 0.3s ease;
        }
        .btn:hover { background-position: right center; transform: translateY(-1px); }
        .btn-secondary { background: #f1f5f9; color: #0f172a; border: 1px solid #cbd5e1; }
        .btn-secondary:hover { background: #e2e8f0; transform: none; }
        
        .country-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 14px; }
        .country-card { background: #ffffff; border: 1px solid #e2e8f0; padding: 20px 14px; border-radius: 12px; text-align: center; }
        .country-flag { font-size: 36px; margin-bottom: 8px; display: block; }
        .country-name { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
        .country-price { font-size: 13px; color: #64748b; margin-bottom: 12px; font-weight: 500; }
        
        .bottom-nav { 
            position: fixed; bottom: 0; left: 0; right: 0; height: 64px; 
            background: #ffffff; border-top: 1px solid #e2e8f0; display: flex; justify-content: space-around; align-items: center; z-index: 999;
        }
        .nav-item { font-size: 22px; text-decoration: none; color: #94a3b8; padding: 10px; transition: color 0.2s; }
        .nav-item.active { color: #0066cc; transform: scale(1.05); }
        
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; margin-bottom: 6px; color: #334155; font-size: 13px; font-weight: 600; }
        .form-group input { 
            width: 100%; padding: 11px; box-sizing: border-box; background: #ffffff; 
            border: 1px solid #cbd5e1; color: #0f172a; border-radius: 8px; font-size: 14px;
        }
        .form-group input:focus { border-color: #0066cc; outline: none; }
        
        .flash { padding: 12px 16px; background: #fef2f2; color: #dc2626; border-radius: 8px; margin-bottom: 16px; border: 1px solid #fee2e2; font-size: 13px; }
        .flash.success { background: #f0fdf4; color: #16a34a; border: 1px solid #dcfce7; }
        
        @media (max-width: 480px) {
            .header { padding: 12px 16px; }
            .logo { font-size: 18px; }
            .auth-buttons a { padding: 6px 10px; font-size: 12px; }
            .country-list { grid-template-columns: repeat(2, 1fr); gap: 10px; }
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

# --- Эндпоинты ---

@app.route('/')
def index():
    prices = {p.country: int(p.price) for p in CountryPrice.query.all()}
    html = """
    <h3 style="font-weight:700; margin-bottom:16px;">Каталог локаций</h3>
    <div class="country-list">
        {% for country in countries %}
            <div class="country-card">
                <span class="country-flag">{{ flags[country] }}</span>
                <div class="country-name">{{ country }}</div>
                <div class="country-price">{{ prices.get(country, 300) }} ₽</div>
                {% if session.get('user_id') %}
                    <a href="/shop" class="btn" style="padding:8px 12px; font-size:13px; width:100%; box-sizing:border-box;">Купить</a>
                {% else %}
                    <a href="/login" class="btn" style="padding:8px 12px; font-size:13px; width:100%; box-sizing:border-box;" onclick="alert('Для покупки необходимо авторизоваться!');">Купить</a>
                {% endif %}
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='home', countries=COUNTRIES_LIST, flags=FLAGS, prices=prices)

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
        flash('Вы успешно зарегистрировались!', 'success')
        return redirect(url_for('login'))
    return render_page('<div class="card" style="max-width:400px; margin:auto;"><h3>Регистрация</h3><form method="POST"><div class="form-group"><label>Логин</label><input type="text" name="username" required></div><div class="form-group"><label>Пароль</label><input type="password" name="password" required></div><button type="submit" class="btn" style="width:100%;">Создать аккаунт</button></form></div>')

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
        flash('Неверные учетные данные!', 'error')
    return render_page('<div class="card" style="max-width:400px; margin:auto;"><h3>Вход в систему</h3><form method="POST"><div class="form-group"><label>Логин</label><input type="text" name="username" required></div><div class="form-group"><label>Пароль</label><input type="password" name="password" required></div><button type="submit" class="btn" style="width:100%;">Войти</button></form></div>')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/shop')
def shop():
    if not session.get('user_id'): return redirect(url_for('login'))
    prices = {p.country: int(p.price) for p in CountryPrice.query.all()}
    html = """
    <h3 style="margin-bottom:16px; font-weight:700;">Покупка аккаунтов</h3>
    <div class="country-list">
        {% for country in countries %}
            <div class="country-card">
                <span class="country-flag">{{ flags[country] }}</span>
                <div class="country-name">{{ country }}</div>
                <div class="country-price">{{ prices.get(country, 300) }} ₽</div>
                <form action="/buy/{{ country }}" method="POST">
                    <button type="submit" class="btn" style="padding:8px 12px; font-size:13px; width:100%;">Купить</button>
                </form>
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='shop', countries=COUNTRIES_LIST, flags=FLAGS, prices=prices)

@app.route('/buy/<country>', methods=['POST'])
def buy_account(country):
    if not session.get('user_id'): return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    
    clean_country = country.strip()
    price_rec = CountryPrice.query.get(clean_country)
    price = price_rec.price if price_rec else 300.0
    
    if user.balance < price:
        flash('Недостаточно рублей на балансе! Перейдите в профиль.', 'error')
        return redirect(url_for('profile'))
    
    # ИСПРАВЛЕНО: Принудительное очищение строки и сопоставление данных
    account = Account.query.filter_by(country=clean_country, is_sold=False).first()
    if not account:
        flash('Аккаунты данной страны временно закончились в базе!', 'error')
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
    <h3 style="margin-bottom:16px;">Мои покупки</h3>
    {% if not my_purchases %}<p style="color:#64748b; font-size:14px;">У вас нет совершенных покупок.</p>{% endif %}
    {% for p in my_purchases %}
        <div class="card" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; padding:16px;">
            <div style="font-weight:600;">{{ flags.get(p.country, '') }} {{ p.country }}: <code style="color:#0066cc; font-size:15px; background:#f1f5f9; padding:3px 6px; border-radius:4px;">{{ p.phone }}</code></div>
            <div>
                <button class="btn" style="padding:8px 14px; font-size:13px;" onclick="getCode({{ p.account_id }}, this)">Получить код</button>
                <span id="out-{{ p.account_id }}" style="margin-left:10px; font-weight:700; color:#16a34a;"></span>
            </div>
        </div>
    {% endfor %}
    <script>
    function getCode(id, btn) {
        btn.innerText = 'Поиск...';
        fetch('/get_code/' + id).then(r => r.json()).then(data => {
            btn.innerText = 'Получить код';
            const el = document.getElementById('out-'+id);
            if(data.success) { el.innerText = 'КОД: ' + data.code; el.style.color = '#16a34a'; }
            else { el.innerText = data.message; el.style.color = '#dc2626'; }
        });
    }
    </script>
    """
    return render_page(html, active_tab='purchases', my_purchases=my_purchases, flags=FLAGS)

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
    user = User.query.get(session['user_id'])
    html = """
    <div class="card">
        <h3>Личный кабинет</h3>
        <p style="color:#64748b; font-size:13px; margin-bottom:4px;">Текущий баланс:</p>
        <div style="font-size:32px; font-weight:700; color:#0f172a; margin-bottom:20px;">{{ user.balance }} ₽</div>
        <hr style="border:none; border-top:1px solid #e2e8f0; margin:20px 0;">
        <h4>Пополнение баланса через Crypto Bot</h4>
        <form action="/deposit" method="POST" style="max-width:320px;">
            <div class="form-group">
                <label>Сумма пополнения (введите в USDT)</label>
                <input type="number" step="0.01" name="amount" value="10.00" required>
            </div>
            <button type="submit" class="btn" style="width:100%;">Выставить счет</button>
        </form>
        {% if session.get('is_admin') %}
            <a href="/admin" class="btn btn-secondary" style="width:100%; box-sizing:border-box; margin-top:16px; padding:10px 0;">Панель Администратора</a>
        {% endif %}
    </div>
    """
    return render_page(html, active_tab='profile', user=user)

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
                <p style="color:#64748b; font-size:14px; margin-bottom:20px;">Для оплаты нажмите кнопку ниже:</p>
                <p><a href="{{ url }}" target="_blank" class="btn" style="padding:12px 24px;">Оплатить в Crypto Bot</a></p>
                <hr style="border:none; border-top:1px solid #e2e8f0; margin:20px 0;">
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
            user = User.query.get(inv.user_id)
            # ИСПРАВЛЕНО: Скрытый курс: добавляем рубли (USDT * 90)
            user.balance += (inv.amount * 90)
            inv.status = 'paid'
            db.session.commit()
            flash('Баланс пополнен рублей успешно!', 'success')
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
                flash('Баланс изменен!', 'success')
        elif 'set_price' in request.form:
            c = request.form.get('country')
            p = float(request.form.get('price'))
            cp = CountryPrice.query.get(c)
            if cp: cp.price = p
            else: db.session.add(CountryPrice(country=c, price=p))
            db.session.commit()
            flash(f'Установлена новая цена для {c}!', 'success')
            
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
                <select name="country" style="width:100%; padding:11px; border:1px solid #cbd5e1; border-radius:8px; background:#fff;">
                    {% for c in countries %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
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
    """
    return render_page(html, active_tab='profile', countries=COUNTRIES_LIST)

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
            <div class="form-group"><label>Код из Telegram</label><input type="text" name="code" required></div>
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
