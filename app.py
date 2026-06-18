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
app.secret_key = 'vest_account_absolute_secret_key_999'

# --- Логирование ошибок для отладки ---
@app.errorhandler(500)
def internal_server_error(e):
    return f"<h2>Внутренняя ошибка сервера (500)</h2><pre>{traceback.format_exc()}</pre>", 500

# --- Конфигурация Базы Данных PostgreSQL ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://bothost_db_a956eeb808dc:ECngxbt9uo_vUzq4-nLfUKv_PJ_jp111YhQB-LXlO9A@node1.pghost.ru:15791/bothost_db_a956eeb808dc'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Настройки API ---
API_ID = 32480523
API_HASH = '147839735c9fa4e83451209e9b55cfc5'
CRYPTO_BOT_TOKEN = '499354:AATdkiDyuC1tWd1ro5S5wFw6XcePNUNH5Ph'
CRYPTO_BOT_URL = 'https://pay.cryptobots.net/api/' 

PENDING_REGISTRATIONS = {}

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
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='active')

# Инициализация таблиц
with app.app_context():
    db.create_all()

# --- Словарь Флагов ---
FLAGS = {
    'Россия': '🇷🇺',
    'США': '🇺🇸',
    'Украина': '🇺🇦',
    'Казахстан': '🇰🇿',
    'Беларусь': '🇧🇾',
    'Мьянма': '🇲🇲'
}

# --- Вспомогательные функции Telethon ---
def detect_country(phone):
    phone = re.sub(r'\D', '', phone)
    if phone.startswith('79') or phone.startswith('74') or phone.startswith('75'): return 'Россия'
    if phone.startswith('1'): return 'США'
    if phone.startswith('380'): return 'Украина'
    if phone.startswith('77') or phone.startswith('70'): return 'Казахстан'
    if phone.startswith('375'): return 'Беларусь'
    if phone.startswith('95'): return 'Мьянма'
    return 'Неизвестно'

def tg_send_code(phone):
    async def _main():
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        res = await client.send_code_request(phone)
        session_str = client.session.save()
        await client.disconnect()
        return session_str, res.phone_code_hash
    return asyncio.run(_main())

def tg_sign_in(phone, code, phone_code_hash, session_str):
    async def _main():
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        final_session = client.session.save()
        await client.disconnect()
        return final_session
    return asyncio.run(_main())

def tg_get_latest_code(session_str):
    async def _main():
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None, "Сессия неавторизована"
        
        dialogs = await client.get_dialogs(limit=5)
        if not dialogs:
            await client.disconnect()
            return None, "Чаты пусты"
        
        for dialog in dialogs[:3]:
            messages = await client.get_messages(dialog, limit=5)
            for m in messages:
                if m.text:
                    match = re.search(r'\b\d{5}\b', m.text)
                    if match:
                        await client.disconnect()
                        return match.group(0), None
        await client.disconnect()
        return None, "Код не найден"
    return asyncio.run(_main())

# --- Премиум Адаптивный Анимационный Шаблон ---
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Vest Account</title>
    <style>
        :root {
            --bg-main: #09090b;
            --bg-card: #14141b;
            --border-color: #232330;
            --text-main: #f4f4f7;
            --text-muted: #8f8f9e;
        }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; 
            background-color: var(--bg-main); 
            color: var(--text-main); 
            margin: 0; 
            padding-bottom: 90px; 
            -webkit-font-smoothing: antialiased;
        }
        
        /* Эффект переливания градиента */
        @keyframes shimmerAnimation {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        .header { 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            padding: 15px 20px; 
            background-color: var(--bg-card); 
            border-bottom: 1px solid var(--border-color);
        }
        
        /* Переливающийся логотип */
        .logo { 
            font-size: 22px; 
            font-weight: 800; 
            text-decoration: none; 
            background: linear-gradient(90deg, #00f0ff, #7000ff, #ff007f, #00f0ff);
            background-size: 300% 300%;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: shimmerAnimation 5s ease infinite;
            letter-spacing: -0.5px;
        }
        
        .auth-buttons a { 
            color: #fff; 
            text-decoration: none; 
            margin-left: 10px; 
            padding: 8px 14px; 
            border-radius: 8px; 
            font-size: 14px;
            font-weight: 600;
            background-color: #1c1c24; 
            border: 1px solid var(--border-color);
            transition: 0.2s;
        }
        .container { max-width: 750px; width: 92%; margin: 25px auto; }
        
        .card { 
            background-color: var(--bg-card); 
            border: 1px solid var(--border-color); 
            border-radius: 16px; 
            padding: 20px; 
            margin-bottom: 20px; 
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
        }

        /* Переливающиеся Premium Кнопки */
        .btn { 
            background: linear-gradient(45deg, #00f0ff, #7000ff, #ff007f, #00f0ff);
            background-size: 400% 400%;
            animation: shimmerAnimation 6s ease infinite;
            color: #fff; 
            border: none; 
            padding: 12px 24px; 
            border-radius: 10px; 
            cursor: pointer; 
            font-weight: 700; 
            text-decoration: none; 
            display: inline-block; 
            text-align: center;
            box-shadow: 0 0 15px rgba(0, 240, 255, 0.25);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .btn:hover { 
            transform: translateY(-2px);
            box-shadow: 0 0 25px rgba(255, 0, 127, 0.45);
        }
        .btn:active { transform: translateY(0); }
        
        .btn-secondary { background: #22222e; border: 1px solid var(--border-color); color: #fff; animation: none; box-shadow: none; }
        .btn-secondary:hover { background: #2c2c3a; box-shadow: none; transform: none; }

        /* Адаптивная сетка стран */
        .country-list { 
            display: grid; 
            grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); 
            gap: 15px; 
        }
        .country-card { 
            background: var(--bg-card); 
            border: 1px solid var(--border-color); 
            padding: 22px 15px; 
            border-radius: 16px; 
            text-align: center;
            transition: 0.2s;
        }
        .country-card:hover { border-color: #7000ff; transform: translateY(-3px); }
        .country-flag { font-size: 38px; margin-bottom: 10px; display: block; }
        .country-name { font-size: 16px; font-weight: 700; margin-bottom: 15px; color: var(--text-main); }

        /* Нижнее меню без надписей */
        .bottom-nav { 
            position: fixed; 
            bottom: 0; 
            left: 0; 
            right: 0; 
            height: 68px; 
            background-color: rgba(20, 20, 27, 0.94); 
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border-top: 1px solid var(--border-color); 
            display: flex; 
            justify-content: space-around; 
            align-items: center; 
            z-index: 9999;
        }
        .nav-item { 
            font-size: 24px; 
            text-decoration: none; 
            color: #626275; 
            padding: 12px;
            transition: 0.2s ease;
        }
        .nav-item.active { 
            color: #00f0ff; 
            text-shadow: 0 0 10px rgba(0,240,255,0.4);
            transform: scale(1.15);
        }

        .form-group { margin-bottom: 18px; }
        .form-group label { display: block; margin-bottom: 6px; color: var(--text-muted); font-size: 14px; font-weight: 500; }
        .form-group input { 
            width: 100%; padding: 12px; box-sizing: border-box; 
            background: #0d0d12; border: 1px solid var(--border-color); 
            color: #fff; border-radius: 10px; font-size: 15px;
            transition: border-color 0.2s;
        }
        .form-group input:focus { border-color: #00f0ff; outline: none; }
        
        .flash { padding: 12px 16px; background-color: #2a1414; color: #ff5c5c; border-radius: 10px; margin-bottom: 20px; border: 1px solid #401a1a; font-size: 14px; font-weight: 500; }
        .flash.success { background-color: #132617; color: #4ade80; border: 1px solid #1e4625; }

        /* Адаптив под мобильные устройства */
        @media (max-width: 480px) {
            .header { padding: 12px 15px; }
            .logo { font-size: 19px; }
            .auth-buttons a { padding: 6px 10px; font-size: 13px; }
            .container { width: 90%; margin: 15px auto; }
            .country-list { grid-template-columns: repeat(2, 1fr); gap: 12px; }
            .country-card { padding: 15px 10px; }
            .country-flag { font-size: 32px; }
            .btn { width: 100%; box-sizing: border-box; padding: 12px; }
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
                <a href="/logout" class="btn-secondary" style="padding: 6px 12px; font-size:13px; margin:0;">Выйти</a>
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
    rendered_inner = render_template_string(inner_template, **kwargs)
    return render_template_string(BASE_TEMPLATE, content=rendered_inner, active_tab=active_tab)

# --- Маршруты ---

@app.route('/')
def index():
    countries = ['Россия', 'США', 'Украина', 'Казахстан', 'Беларусь', 'Мьянма']
    html = """
    <h2 style="margin-bottom:20px; font-weight:800; letter-spacing:-0.5px;">Выбор локации</h2>
    <div class="country-list">
        {% for country in countries %}
            <div class="country-card">
                <span class="country-flag">{{ flags[country] }}</span>
                <div class="country-name">{{ country }}</div>
                {% if session.get('user_id') %}
                    <a href="/shop" class="btn" style="padding: 8px 16px; font-size:14px;">Купить</a>
                {% else %}
                    <a href="/login" class="btn" style="padding: 8px 16px; font-size:14px;" onclick="alert('Для покупки необходимо авторизоваться!');">Купить</a>
                {% endif %}
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='home', countries=countries, flags=FLAGS)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Этот логин уже занят!', 'error')
            return redirect(url_for('register'))
        
        is_admin = (username == 'Vestnik' and password == '5533789q')
        new_user = User(username=username, password=generate_password_hash(password), is_admin=is_admin)
        db.session.add(new_user)
        db.session.commit()
        flash('Регистрация успешна!', 'success')
        return redirect(url_for('login'))
    html = """
    <div class="card" style="max-width:400px; margin:20px auto;">
        <h2 style="margin-top:0; font-weight:800;">Регистрация</h2>
        <form method="POST">
            <div class="form-group"><label>Логин</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Пароль</label><input type="password" name="password" required></div>
            <button type="submit" class="btn" style="width:100%;">Создать аккаунт</button>
        </form>
    </div>
    """
    return render_page(html, active_tab='home')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            return redirect(url_for('shop'))
        flash('Неверный логин или пароль!', 'error')
    html = """
    <div class="card" style="max-width:400px; margin:20px auto;">
        <h2 style="margin-top:0; font-weight:800;">Вход</h2>
        <form method="POST">
            <div class="form-group"><label>Логин</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Пароль</label><input type="password" name="password" required></div>
            <button type="submit" class="btn" style="width:100%;">Войти</button>
        </form>
    </div>
    """
    return render_page(html, active_tab='home')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/shop')
def shop():
    if not session.get('user_id'): return redirect(url_for('login'))
    countries = ['Россия', 'США', 'Украина', 'Казахстан', 'Беларусь', 'Мьянма']
    html = """
    <h2 style="margin-bottom:20px; font-weight:800; letter-spacing:-0.5px;">Магазин аккаунтов</h2>
    <div class="country-list">
        {% for country in countries %}
            <div class="country-card">
                <span class="country-flag">{{ flags[country] }}</span>
                <div class="country-name">{{ country }}</div>
                <form action="/buy/{{ country }}" method="POST">
                    <button type="submit" class="btn" style="padding: 8px 16px; font-size:14px; width:100%;">Купить</button>
                </form>
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='shop', countries=countries, flags=FLAGS)

@app.route('/buy/<country>', methods=['POST'])
def buy_account(country):
    if not session.get('user_id'): return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    price = 100.0
    if user.balance < price:
        flash('Недостаточно средств на балансе!', 'error')
        return redirect(url_for('profile'))
    
    account = Account.query.filter_by(country=country, is_sold=False).first()
    if not account:
        flash('Аккаунты данной страны временно закончились!', 'error')
        return redirect(url_for('shop'))
    
    user.balance -= price
    account.is_sold = True
    purchase = Purchase(user_id=user.id, account_id=account.id, phone=account.phone, country=account.country)
    db.session.add(purchase)
    db.session.commit()
    flash(f'Успешно куплено! Номер: {account.phone}', 'success')
    return redirect(url_for('purchases'))

@app.route('/purchases')
def purchases():
    if not session.get('user_id'): return redirect(url_for('login'))
    my_purchases = Purchase.query.filter_by(user_id=session['user_id']).all()
    html = """
    <h2 style="margin-bottom:20px; font-weight:800; letter-spacing:-0.5px;">Мои покупки</h2>
    {% if not my_purchases %} <p style="color:var(--text-muted);">У вас пока нет купленных аккаунтов.</p> {% endif %}
    {% for p in my_purchases %}
        <div class="card" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; padding:16px;">
            <div style="font-size:15px; font-weight:600;">{{ flags[p.country] }} {{ p.country }}: <code style="color:#00f0ff; font-size:16px; margin-left:5px;">{{ p.phone }}</code></div>
            <div style="flex-grow:1; text-align:right; min-width:140px;">
                <button class="btn" style="padding:8px 16px; font-size:13px;" onclick="getCode({{ p.account_id }}, this)">Получить код</button>
                <div id="out-{{ p.account_id }}" style="margin-top:6px; font-weight:bold; color:#4ade80; font-size:14px;"></div>
            </div>
        </div>
    {% endfor %}
    <script>
    function getCode(id, btn) {
        btn.innerText = 'Запрос...';
        fetch('/get_code/' + id).then(r => r.json()).then(data => {
            btn.innerText = 'Получить код';
            const el = document.getElementById('out-'+id);
            if(data.success) {
                el.innerText = 'Код: ' + data.code;
                el.style.color = '#4ade80';
            } else {
                el.innerText = data.message;
                el.style.color = '#ff5c5c';
            }
        });
    }
    </script>
    """
    return render_page(html, active_tab='purchases', my_purchases=my_purchases, flags=FLAGS)

@app.route('/get_code/<int:account_id>')
def get_code(account_id):
    if not session.get('user_id'): return jsonify({'success': False, 'message': 'Log in required'})
    purchase = Purchase.query.filter_by(user_id=session['user_id'], account_id=account_id).first()
    if not purchase: return jsonify({'success': False, 'message': 'No access'})
    
    account = Account.query.get(account_id)
    try:
        code, err = tg_get_latest_code(account.session_string)
        if err: return jsonify({'success': False, 'message': err})
        return jsonify({'success': True, 'code': code})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/profile')
def profile():
    if not session.get('user_id'): return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    html = """
    <div class="card">
        <h2 style="margin-top:0; font-weight:800; letter-spacing:-0.5px;">Профиль пользователя</h2>
        <p style="font-size:16px; color:var(--text-muted);">Текущий баланс:</p>
        <div style="font-size:32px; font-weight:800; color:#4ade80; margin-bottom:20px;">{{ user.balance }} USDT</div>
        
        <hr style="border-color:var(--border-color); margin:20px 0;">
        
        <h3 style="font-weight:700;">Пополнение баланса (Crypto Bot)</h3>
        <form action="/deposit" method="POST" style="max-width:100%;">
            <div class="form-group">
                <label>Сумма пополнения (USDT)</label>
                <input type="number" step="0.01" name="amount" value="10.00" required>
            </div>
            <button type="submit" class="btn" style="width:100%;">Выставить счет</button>
        </form>
        {% if session.get('is_admin') %}
            <br>
            <a href="/admin" class="btn btn-secondary" style="width:100%; box-sizing:border-box; text-align:center; padding:12px 0; border-radius:10px; font-weight:600;">💼 Панель Администратора</a>
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
            <div class="card" style="text-align:center; padding:30px 20px;">
                <h2 style="margin-top:0; font-weight:800;">Инвойс создан</h2>
                <p style="color:var(--text-muted); margin-bottom:25px;">Для зачисления инвойса на сумму {{ amount }} USDT нажмите кнопку ниже:</p>
                <p><a href="{{ url }}" target="_blank" class="btn" style="padding:14px 30px; font-size:16px;">Перейти к оплате</a></p>
                <hr style="border-color:var(--border-color); margin:25px 0;">
                <form action="/check_invoice/{{ inv_id }}" method="POST"><button type="submit" class="btn btn-secondary" style="width:100%;">Проверить статус платежа</button></form>
            </div>
            """
            return render_page(html, active_tab='profile', url=data['pay_url'], amount=amount, inv_id=inv.id)
    except Exception as e:
        flash(f'Ошибка платежной системы: {e}', 'error')
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
            user.balance += inv.amount
            inv.status = 'paid'
            db.session.commit()
            flash('Баланс успешно пополнен!', 'success')
        else:
            flash('Оплата не обнаружена в Crypto Bot.', 'error')
    except Exception as e:
        flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('profile'))

# --- Панель Администратора ---

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if not session.get('is_admin'): return "No access", 403
    if request.method == 'POST' and 'change_balance' in request.form:
        u = User.query.filter_by(username=request.form.get('username').strip()).first()
        if u:
            u.balance = float(request.form.get('balance'))
            db.session.commit()
            flash('Баланс пользователя изменен!', 'success')
    html = """
    <h2 style="font-weight:800; letter-spacing:-0.5px; margin-bottom:20px;">Управление системой</h2>
    <div class="card">
        <h3 style="margin-top:0;">Импорт аккаунта Telethon</h3>
        <form action="/admin/add_account" method="POST">
            <div class="form-group"><label>Номер телефона</label><input type="text" name="phone" placeholder="+7..." required></div>
            <button type="submit" class="btn" style="width:100%;">Выслать код авторизации</button>
        </form>
    </div>
    <div class="card">
        <h3>Управление балансом</h3>
        <form method="POST"><input type="hidden" name="change_balance" value="1">
            <div class="form-group"><input type="text" name="username" placeholder="Логин" required></div>
            <div class="form-group"><input type="number" step="0.01" name="balance" placeholder="Новый баланс" required></div>
            <button type="submit" class="btn" style="width:100%;">Обновить баланс</button>
        </form>
    </div>
    """
    return render_page(html, active_tab='profile')

@app.route('/admin/add_account', methods=['POST'])
def admin_add_account():
    if not session.get('is_admin'): return "No access", 403
    phone = request.form.get('phone').strip().replace(' ', '')
    country = detect_country(phone)
    try:
        session_str, phone_code_hash = tg_send_code(phone)
        PENDING_REGISTRATIONS[phone] = {
            'session_str': session_str,
            'phone_code_hash': phone_code_hash,
            'country': country
        }
        html = """
        <div class="card" style="max-width:400px; margin:20px auto;">
            <h3 style="margin-top:0;">Подтверждение кода</h3>
            <p style="color:var(--text-muted);">Локация: <b>{{ country }}</b> | Телефон: <code>{{ phone }}</code></p>
            <form action="/admin/verify_account" method="POST">
                <input type="hidden" name="phone" value="{{ phone }}">
                <div class="form-group"><label>Код из Telegram-уведомления</label><input type="text" name="code" required></div>
                <button type="submit" class="btn" style="width:100%;">Добавить аккаунт</button>
            </form>
        </div>
        """
        return render_page(html, active_tab='profile', phone=phone, country=country)
    except Exception as e:
        flash(f'Ошибка отправки: {e}', 'error')
        return redirect(url_for('admin_panel'))

@app.route('/admin/verify_account', methods=['POST'])
def admin_verify_account():
    if not session.get('is_admin'): return "No access", 403
    phone = request.form.get('phone')
    code = request.form.get('code').strip()
    if phone not in PENDING_REGISTRATIONS:
        flash('Сессия устарела.', 'error')
        return redirect(url_for('admin_panel'))
        
    data = PENDING_REGISTRATIONS[phone]
    try:
        final_session_string = tg_sign_in(phone, code, data['phone_code_hash'], data['session_str'])
        new_acc = Account(phone=phone, country=data['country'], session_string=final_session_string)
        db.session.add(new_acc)
        db.session.commit()
        del PENDING_REGISTRATIONS[phone]
        flash('Аккаунт успешно добавлен в базу!', 'success')
    except Exception as e:
        flash(f'Ошибка авторизации: {e}', 'error')
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
