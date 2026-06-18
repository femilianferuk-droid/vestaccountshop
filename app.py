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
from telethon.errors import SessionPasswordNeededError, FloodWaitError, PhoneNumberInvalidError

app = Flask(__name__)
app.secret_key = 'vest_account_premium_secret_key_2026'

# --- Отображение ошибок для оперативной отладки ---
@app.errorhandler(500)
def internal_server_error(e):
    return f"<h2>Внутренняя ошибка сервера (500)</h2><pre>{traceback.format_exc()}</pre>", 500

# --- Конфигурация Базы Данных PostgreSQL ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://bothost_db_a956eeb808dc:ECngxbt9uo_vUzq4-nLfUKv_PJ_jp111YhQB-LXlO9A@node1.pghost.ru:15791/bothost_db_a956eeb808dc'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Настройки API и Эндпоинтов ---
API_ID = 32480523
API_HASH = '147839735c9fa4e83451209e9b55cfc5'
CRYPTO_BOT_TOKEN = '499354:AATdkiDyuC1tWd1ro5S5wFw6XcePNUNH5Ph'
CRYPTO_BOT_URL = 'https://pay.crypt.bot/api/'  # ИСПРАВЛЕНО: Ваш точный URL-адрес платежной системы

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

# Создание таблиц при инициализации
with app.app_context():
    db.create_all()

# --- Конфигурация стран ---
FLAGS = {
    'Россия': '🇷🇺',
    'США': '🇺🇸',
    'Украина': '🇺🇦',
    'Казахстан': '🇰🇿',
    'Беларусь': '🇧🇾',
    'Мьянма': '🇲🇲'
}

def detect_country(phone):
    phone = re.sub(r'\D', '', phone)
    if phone.startswith('79') or phone.startswith('74') or phone.startswith('75'): return 'Россия'
    if phone.startswith('1'): return 'США'
    if phone.startswith('380'): return 'Украина'
    if phone.startswith('77') or phone.startswith('70'): return 'Казахстан'
    if phone.startswith('375'): return 'Беларусь'
    if phone.startswith('95'): return 'Мьянма'
    return 'Неизвестно'

# --- ИСПРАВЛЕНО: Обертки Telethon с маскировкой под официальный клиент (iOS) ---
def get_tg_client(session_instance=None):
    """Возвращает настроенный клиент, мимикрирующий под официальное приложение Apple для обхода флуд-фильтров"""
    if session_instance is None:
        session_instance = StringSession()
    return TelegramClient(
        session_instance, 
        API_ID, 
        API_HASH,
        device_model="iPhone 15 Pro",
        system_version="iOS 17.5",
        app_version="10.11.2",
        lang_code="ru",
        system_lang_code="ru-RU"
    )

def tg_send_code(phone):
    async def _main():
        client = get_tg_client()
        await client.connect()
        try:
            res = await client.send_code_request(phone)
            session_str = client.session.save()
            return session_str, res.phone_code_hash, None
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
            final_session = client.session.save()
            return final_session, None
        except SessionPasswordNeededError:
            return None, "На аккаунте установлен двухэтапный пароль (2FA). Его ввод не поддерживается текущей формой."
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
                return None, "Сессия аннулирована владельцем или Telegram."
            
            dialogs = await client.get_dialogs(limit=3)
            if not dialogs:
                return None, "Список чатов пуст."
            
            # Поиск кода в самом первом (самом новом) диалоге
            messages = await client.get_messages(dialogs[0], limit=5)
            for m in messages:
                if m.text:
                    match = re.search(r'\b\d{5}\b', m.text)
                    if match:
                        return match.group(0), None
            return None, "5-значный код в самом новом чате не найден."
        except Exception as e:
            return None, str(e)
        finally:
            await client.disconnect()
    return asyncio.run(_main())

# --- Шаблон интерфейса (Премиальный Неоново-Синий UI) ---
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Vest Account</title>
    <style>
        :root {
            --bg-base: #0a0c10;
            --bg-surface: #121620;
            --border-glow: #1e293b;
            --neon-blue: #0088ff;
            --neon-blue-glow: rgba(0, 136, 255, 0.4);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
        }
        
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', Roboto, sans-serif; 
            background-color: var(--bg-base); 
            color: var(--text-primary); 
            margin: 0; 
            padding-bottom: 90px;
            -webkit-font-smoothing: antialiased;
        }

        @keyframes blueShimmer {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        .header { 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            padding: 18px 24px; 
            background-color: var(--bg-surface); 
            border-bottom: 1px solid var(--border-glow);
        }
        
        .logo { 
            font-size: 24px; 
            font-weight: 900; 
            text-decoration: none; 
            background: linear-gradient(90deg, #0052d4, #4364f7, #6fb1fc, #0052d4);
            background-size: 300% 100%;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: blueShimmer 4s linear infinite;
            letter-spacing: -0.5px;
        }
        
        .auth-buttons a { 
            color: var(--text-primary); 
            text-decoration: none; 
            margin-left: 12px; 
            padding: 9px 16px; 
            border-radius: 10px; 
            font-size: 14px;
            font-weight: 600;
            background-color: #1a2030; 
            border: 1px solid var(--border-glow);
            transition: all 0.25s ease;
        }
        .auth-buttons a:hover {
            border-color: var(--neon-blue);
            box-shadow: 0 0 10px var(--neon-blue-glow);
        }

        .container { max-width: 700px; width: 92%; margin: 30px auto; }
        
        .card { 
            background-color: var(--bg-surface); 
            border: 1px solid var(--border-glow); 
            border-radius: 20px; 
            padding: 24px; 
            margin-bottom: 24px;
        }

        /* Синяя переливающаяся кнопка */
        .btn { 
            background: linear-gradient(90deg, #0044ff, #00aaff, #0044ff);
            background-size: 200% auto;
            animation: blueShimmer 3s linear infinite;
            color: #fff; 
            border: none; 
            padding: 14px 28px; 
            border-radius: 12px; 
            cursor: pointer; 
            font-weight: 700; 
            text-decoration: none; 
            display: inline-block; 
            text-align: center;
            box-shadow: 0 4px 15px rgba(0, 110, 255, 0.3);
            transition: transform 0.2s;
        }
        .btn:hover { 
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(0, 110, 255, 0.5);
        }
        .btn:active { transform: translateY(0); }
        
        .btn-secondary { background: #1e2433; border: 1px solid var(--border-glow); color: #fff; animation: none; box-shadow: none; }
        .btn-secondary:hover { background: #252d40; transform: none; box-shadow: none;}

        .country-list { 
            display: grid; 
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); 
            gap: 16px; 
        }
        .country-card { 
            background: var(--bg-surface); 
            border: 1px solid var(--border-glow); 
            padding: 24px 16px; 
            border-radius: 20px; 
            text-align: center;
            transition: all 0.25s ease;
        }
        .country-card:hover { 
            border-color: var(--neon-blue); 
            box-shadow: 0 0 15px var(--neon-blue-glow);
            transform: translateY(-2px); 
        }
        .country-flag { font-size: 42px; margin-bottom: 12px; display: block; }
        .country-name { font-size: 16px; font-weight: 700; margin-bottom: 16px; color: var(--text-primary); }

        .bottom-nav { 
            position: fixed; 
            bottom: 0; left: 0; right: 0; 
            height: 72px; 
            background-color: rgba(18, 22, 32, 0.95); 
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-top: 1px solid var(--border-glow); 
            display: flex; 
            justify-content: space-around; 
            align-items: center; 
            z-index: 9999;
        }
        .nav-item { 
            font-size: 26px; 
            text-decoration: none; 
            color: #475569; 
            padding: 14px;
            transition: all 0.2s ease;
        }
        .nav-item.active { 
            color: var(--neon-blue); 
            text-shadow: 0 0 12px var(--neon-blue-glow);
            transform: scale(1.15);
        }

        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; color: var(--text-secondary); font-size: 14px; font-weight: 600; }
        .form-group input { 
            width: 100%; padding: 14px; box-sizing: border-box; 
            background: #090b11; border: 1px solid var(--border-glow); 
            color: #fff; border-radius: 12px; font-size: 16px;
            transition: all 0.25s ease;
        }
        .form-group input:focus { border-color: var(--neon-blue); box-shadow: 0 0 10px var(--neon-blue-glow); outline: none; }
        
        .flash { padding: 14px 18px; background-color: #2e1616; color: #f87171; border-radius: 12px; margin-bottom: 24px; border: 1px solid #4a1d1d; font-size: 14px; }
        .flash.success { background-color: #14291b; color: #4ade80; border: 1px solid #1b4728; }

        @media (max-width: 480px) {
            .header { padding: 14px 16px; }
            .logo { font-size: 21px; }
            .container { width: 92%; margin: 20px auto; }
            .country-list { grid-template-columns: repeat(2, 1fr); gap: 12px; }
            .country-card { padding: 20px 12px; }
            .country-flag { font-size: 36px; }
            .btn { width: 100%; box-sizing: border-box; padding: 14px; }
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
                <a href="/logout" class="btn-secondary" style="padding: 7px 14px; font-size:13px; margin:0; border-radius:8px;">Выйти</a>
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

# --- Маршрутизация эндпоинтов ---

@app.route('/')
def index():
    countries = ['Россия', 'США', 'Украина', 'Казахстан', 'Беларусь', 'Мьянма']
    html = """
    <h2 style="margin-bottom:24px; font-weight:800; letter-spacing:-0.5px;">Выбор локации</h2>
    <div class="country-list">
        {% for country in countries %}
            <div class="country-card">
                <span class="country-flag">{{ flags[country] }}</span>
                <div class="country-name">{{ country }}</div>
                {% if session.get('user_id') %}
                    <a href="/shop" class="btn" style="padding: 10px 20px; font-size:14px; width:100%; box-sizing:border-box;">Купить</a>
                {% else %}
                    <a href="/login" class="btn" style="padding: 10px 20px; font-size:14px; width:100%; box-sizing:border-box;" onclick="alert('Для покупки необходимо авторизоваться!');">Купить</a>
                {% endif %}
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='home', countries=countries, flags=FLAGS)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Этот логин уже занят!', 'error')
            return redirect(url_for('register'))
        
        is_admin = (username == 'Vestnik' and password == '5533789q')
        new_user = User(username=username, password=generate_password_hash(password), is_admin=is_admin)
        db.session.add(new_user)
        db.session.commit()
        flash('Регистрация успешно пройдена!', 'success')
        return redirect(url_for('login'))
    html = """
    <div class="card" style="max-width:420px; margin:40px auto;">
        <h2 style="margin-top:0; font-weight:800; letter-spacing:-0.5px;">Регистрация</h2>
        <form method="POST">
            <div class="form-group"><label>Логин</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Пароль</label><input type="password" name="password" required></div>
            <button type="submit" class="btn" style="width:100%;">Зарегистрироваться</button>
        </form>
    </div>
    """
    return render_page(html, active_tab='home')

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
        flash('Неверный логин или текущий пароль!', 'error')
    html = """
    <div class="card" style="max-width:420px; margin:40px auto;">
        <h2 style="margin-top:0; font-weight:800; letter-spacing:-0.5px;">Вход</h2>
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
    <h2 style="margin-bottom:24px; font-weight:800; letter-spacing:-0.5px;">Доступные категории</h2>
    <div class="country-list">
        {% for country in countries %}
            <div class="country-card">
                <span class="country-flag">{{ flags[country] }}</span>
                <div class="country-name">{{ country }}</div>
                <form action="/buy/{{ country }}" method="POST">
                    <button type="submit" class="btn" style="padding: 10px 16px; font-size:14px; width:100%;">Купить</button>
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
        flash('Недостаточно средств на балансе! Перейдите во вкладку профиля.', 'error')
        return redirect(url_for('profile'))
    
    account = Account.query.filter_by(country=country, is_sold=False).first()
    if not account:
        flash('Товары выбранной локации временно отсутствуют!', 'error')
        return redirect(url_for('shop'))
    
    user.balance -= price
    account.is_sold = True
    purchase = Purchase(user_id=user.id, account_id=account.id, phone=account.phone, country=account.country)
    db.session.add(purchase)
    db.session.commit()
    flash(f'Успешная покупка! Сгенерирован номер: {account.phone}', 'success')
    return redirect(url_for('purchases'))

@app.route('/purchases')
def purchases():
    if not session.get('user_id'): return redirect(url_for('login'))
    my_purchases = Purchase.query.filter_by(user_id=session['user_id']).all()
    html = """
    <h2 style="margin-bottom:24px; font-weight:800; letter-spacing:-0.5px;">Мои покупки</h2>
    {% if not my_purchases %} <p style="color:var(--text-secondary);">У вас пока нет оплаченных ордеров.</p> {% endif %}
    {% for p in my_purchases %}
        <div class="card" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:16px; padding:20px;">
            <div style="font-size:16px; font-weight:600;">{{ flags[p.country] }} {{ p.country }}: <code style="color:var(--neon-blue); font-size:17px; margin-left:6px; background:#090b11; padding:4px 8px; border-radius:6px; border:1px solid var(--border-glow)">{{ p.phone }}</code></div>
            <div style="flex-grow:1; text-align:right; min-width:160px;">
                <button class="btn" style="padding:10px 20px; font-size:14px;" onclick="getCode({{ p.account_id }}, this)">Получить код</button>
                <div id="out-{{ p.account_id }}" style="margin-top:10px; font-weight:800; color:#4ade80; font-size:16px; letter-spacing:1px;"></div>
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
                el.innerText = 'КОД: ' + data.code;
                el.style.color = '#4ade80';
            } else {
                el.innerText = data.message;
                el.style.color = '#f87171';
            }
        });
    }
    </script>
    """
    return render_page(html, active_tab='purchases', my_purchases=my_purchases, flags=FLAGS)

@app.route('/get_code/<int:account_id>')
def get_code(account_id):
    if not session.get('user_id'): return jsonify({'success': False, 'message': 'Auth required'})
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
        <h2 style="margin-top:0; font-weight:800; letter-spacing:-0.5px;">Профиль</h2>
        <p style="font-size:14px; color:var(--text-secondary); margin-bottom:5px;">Баланс счета:</p>
        <div style="font-size:36px; font-weight:900; color:#4ade80; margin-bottom:24px;">{{ user.balance }} USDT</div>
        
        <hr style="border-color:var(--border-glow); margin:24px 0;">
        
        <h3 style="font-weight:700; margin-bottom:16px;">Пополнение баланса (Crypto Bot)</h3>
        <form action="/deposit" method="POST">
            <div class="form-group">
                <label>Сумма к зачислению (USDT)</label>
                <input type="number" step="0.01" name="amount" value="10.00" required>
            </div>
            <button type="submit" class="btn" style="width:100%;">Выставить счет</button>
        </form>
        {% if session.get('is_admin') %}
            <br>
            <a href="/admin" class="btn btn-secondary" style="width:100%; box-sizing:border-box; text-align:center; padding:14px 0; border-radius:12px; font-weight:700; margin-top:10px;">💼 Панель Администратора</a>
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
            <div class="card" style="text-align:center; padding:35px 20px;">
                <h2 style="margin-top:0; font-weight:800;">Счет сформирован</h2>
                <p style="color:var(--text-secondary); margin-bottom:30px; font-size:15px;">Для оплаты счета на сумму {{ amount }} USDT нажмите на кнопку перехода к боту:</p>
                <p><a href="{{ url }}" target="_blank" class="btn" style="padding:14px 35px; font-size:16px;">Оплатить в Crypto Bot</a></p>
                <hr style="border-color:var(--border-glow); margin:30px 0;">
                <form action="/check_invoice/{{ inv_id }}" method="POST"><button type="submit" class="btn btn-secondary" style="width:100%;">Проверить статус транзакции</button></form>
            </div>
            """
            return render_page(html, active_tab='profile', url=data['pay_url'], amount=amount, inv_id=inv.id)
        else:
            flash(f"Ошибка Crypto Bot API: {r.get('explanation')}", 'error')
    except Exception as e:
        flash(f'Ошибка отправки запроса к Crypto Bot: {e}', 'error')
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
            flash('Оплата зафиксирована! Баланс успешно пополнен.', 'success')
        else:
            flash('Платеж не найден в системе. Попробуйте еще раз через минуту.', 'error')
    except Exception as e:
        flash(f'Ошибка проверки счета: {e}', 'error')
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
            flash('Баланс аккаунта успешно скорректирован!', 'success')
    html = """
    <h2 style="font-weight:800; letter-spacing:-0.5px; margin-bottom:24px;">Панель администратора</h2>
    <div class="card">
        <h3 style="margin-top:0; font-weight:700;">Импорт аккаунтов Telethon</h3>
        <form action="/admin/add_account" method="POST">
            <div class="form-group"><label>Номер телефона (в международном формате)</label><input type="text" name="phone" placeholder="+7..." required></div>
            <button type="submit" class="btn" style="width:100%;">Запросить код у Telegram</button>
        </form>
    </div>
    <div class="card">
        <h3 style="font-weight:700;">Редактирование балансов</h3>
        <form method="POST"><input type="hidden" name="change_balance" value="1">
            <div class="form-group"><input type="text" name="username" placeholder="Логин пользователя" required></div>
            <div class="form-group"><input type="number" step="0.01" name="balance" placeholder="Сумма баланса" required></div>
            <button type="submit" class="btn" style="width:100%;">Применить изменения</button>
        </form>
    </div>
    """
    return render_page(html, active_tab='profile')

@app.route('/admin/add_account', methods=['POST'])
def admin_add_account():
    if not session.get('is_admin'): return "No access", 403
    phone = request.form.get('phone').strip().replace(' ', '')
    country = detect_country(phone)
    
    # Запрос кода авторизации у Telegram с расширенной обработкой ошибок
    session_str, phone_code_hash, error_msg = tg_send_code(phone)
    if error_msg:
        flash(f'Ошибка Telegram API: {error_msg}', 'error')
        return redirect(url_for('admin_panel'))
        
    PENDING_REGISTRATIONS[phone] = {
        'session_str': session_str,
        'phone_code_hash': phone_code_hash,
        'country': country
    }
    html = """
    <div class="card" style="max-width:420px; margin:40px auto;">
        <h3 style="margin-top:0; font-weight:800;">Активация сессии</h3>
        <p style="color:var(--text-secondary); font-size:15px;">Локация: <b>{{ country }}</b> | Номер телефона: <code>{{ phone }}</code></p>
        <form action="/admin/verify_account" method="POST">
            <input type="hidden" name="phone" value="{{ phone }}">
            <div class="form-group"><label>Введите код авторизации</label><input type="text" name="code" required autocomplete="off"></div>
            <button type="submit" class="btn" style="width:100%;">Подтвердить и сохранить</button>
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
        flash('Регистрационная сессия устарела или не найдена.', 'error')
        return redirect(url_for('admin_panel'))
        
    data = PENDING_REGISTRATIONS[phone]
    final_session_string, error_msg = tg_sign_in(phone, code, data['phone_code_hash'], data['session_str'])
    
    if error_msg:
        flash(f'Ошибка входа: {error_msg}', 'error')
        return redirect(url_for('admin_panel'))
        
    new_acc = Account(phone=phone, country=data['country'], session_string=final_session_string)
    db.session.add(new_acc)
    db.session.commit()
    del PENDING_REGISTRATIONS[phone]
    flash(f'Авторизация успешна! Аккаунт {phone} добавлен в базу данных.', 'success')
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
