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


# --- ИСПРАВЛЕНИЕ: Принудительное создание таблиц при инициализации на хостинге ---
with app.app_context():
    db.create_all()


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

# --- Шаблонизатор интерфейса ---
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Vest Account</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #121214; color: #e1e1e6; margin: 0; padding-bottom: 80px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px; background-color: #1a1a1e; border-bottom: 1px solid #29292e; }
        .logo { font-size: 24px; font-weight: bold; color: #00b0ff; text-decoration: none; }
        .auth-buttons a { color: #fff; text-decoration: none; margin-left: 15px; padding: 8px 16px; border-radius: 6px; background-color: #29292e; }
        .auth-buttons a:hover { background-color: #00b0ff; }
        .container { max-width: 800px; width: 90%; margin: 30px auto; }
        .card { background-color: #1a1a1e; border: 1px solid #29292e; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
        .btn { background-color: #00b0ff; color: #fff; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: bold; text-decoration: none; display: inline-block; text-align:center; }
        .btn:hover { background-color: #0088cc; }
        .btn-secondary { background-color: #29292e; color: #fff; }
        .country-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 20px; }
        .country-card { background: #1a1a1e; border: 1px solid #29292e; padding: 20px; border-radius: 12px; text-align: center; }
        .bottom-nav { position: fixed; bottom: 0; left: 0; right: 0; height: 65px; background-color: #1a1a1e; border-top: 1px solid #29292e; display: flex; justify-content: space-around; align-items: center; }
        .nav-item { font-size: 26px; text-decoration: none; color: #7a7a80; padding: 10px; }
        .nav-item.active { color: #00b0ff; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; color: #a9a9b3; }
        .form-group input { width: 100%; padding: 10px; box-sizing: border-box; background: #121214; border: 1px solid #29292e; color: #fff; border-radius: 6px; }
        .flash { padding: 12px; background-color: #2b1a1a; color: #ff6b6b; border-radius: 6px; margin-bottom: 15px; border: 1px solid #422222; }
        .flash.success { background-color: #1a2b1a; color: #6bff6b; border: 1px solid #224222; }
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
                <span style="color: #a9a9b3;">Вы: <b>{{ session.get('username') }}</b></span>
                <a href="/logout" class="btn-secondary" style="padding: 5px 10px; font-size:12px;">Выйти</a>
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
    counts = {c: Account.query.filter_by(country=c, is_sold=False).count() for c in countries}
    html = """
    <h2>Список доступных стран</h2>
    <div class="country-list">
        {% for country, count in counts.items() %}
            <div class="country-card">
                <h3>{{ country }}</h3>
                <p>В наличии: {{ count }} шт.</p>
                <a href="/shop" class="btn">Купить</a>
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='home', counts=counts)

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
    <div class="card" style="max-width:400px; margin:0 auto;">
        <h2>Регистрация</h2>
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
    <div class="card" style="max-width:400px; margin:0 auto;">
        <h2>Вход</h2>
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
    counts = {c: Account.query.filter_by(country=c, is_sold=False).count() for c in countries}
    html = """
    <h2>Купить аккаунт (Фиксированная цена: 100 USDT)</h2>
    <div class="country-list">
        {% for country, count in counts.items() %}
            <div class="country-card">
                <h3>{{ country }}</h3>
                <p>Доступно: {{ count }} шт.</p>
                <form action="/buy/{{ country }}" method="POST">
                    <button type="submit" class="btn" {% if count == 0 %}disabled style="background:#444;"{% endif %}>Купить</button>
                </form>
            </div>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='shop', counts=counts)

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
        flash('Аккаунты закончились!', 'error')
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
    <h2>Мои покупки</h2>
    {% if not my_purchases %} <p>У вас пока нет покупок.</p> {% endif %}
    {% for p in my_purchases %}
        <div class="card" style="display:flex; justify-content:space-between; align-items:center;">
            <div><b>{{ p.country }}</b>: <code>{{ p.phone }}</code></div>
            <div>
                <button class="btn" onclick="getCode({{ p.account_id }}, this)">Получить код</button>
                <span id="out-{{ p.account_id }}" style="margin-left:15px; font-weight:bold; color:#6bff6b;"></span>
            </div>
        </div>
    {% endfor %}
    <script>
    function getCode(id, btn) {
        btn.innerText = 'Запрос...';
        fetch('/get_code/' + id).then(r => r.json()).then(data => {
            btn.innerText = 'Получить код';
            document.getElementById('out-'+id).innerText = data.success ? 'Код: ' + data.code : 'Ошибка: ' + data.message;
        });
    }
    </script>
    """
    return render_page(html, active_tab='purchases', my_purchases=my_purchases)

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
        <h2>Профиль</h2>
        <p>Баланс: <b>{{ user.balance }} USDT</b></p>
        <form action="/deposit" method="POST" style="max-width:300px;">
            <div class="form-group"><label>Сумма пополнения (USDT)</label><input type="number" step="0.01" name="amount" value="10.00" required></div>
            <button type="submit" class="btn">Пополнить через Crypto Bot</button>
        </form>
        {% if session.get('is_admin') %}<br><a href="/admin" class="btn" style="background:#ff9100;">💼 Панель Администратора</a>{% endif %}
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
            <div class="card" style="text-align:center;">
                <h2>Счет создан</h2>
                <p><a href="{{ url }}" target="_blank" class="btn" style="background:#00c853;">Оплатить {{ amount }} USDT</a></p>
                <form action="/check_invoice/{{ inv_id }}" method="POST"><button type="submit" class="btn btn-secondary">Проверить платеж</button></form>
            </div>
            """
            return render_page(html, active_tab='profile', url=data['pay_url'], amount=amount, inv_id=inv.id)
    except Exception as e:
        flash(f'Ошибка: {e}', 'error')
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
            flash('Баланс пополнен!', 'success')
        else:
            flash('Оплата еще не найдена.', 'error')
    except Exception as e:
        flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('profile'))

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if not session.get('is_admin'): return "No access", 403
    if request.method == 'POST' and 'change_balance' in request.form:
        u = User.query.filter_by(username=request.form.get('username').strip()).first()
        if u:
            u.balance = float(request.form.get('balance'))
            db.session.commit()
            flash('Баланс изменен!', 'success')
    html = """
    <h2>Админ Панель</h2>
    <div class="card">
        <h3>Добавить аккаунт (Telethon)</h3>
        <form action="/admin/add_account" method="POST">
            <div class="form-group"><label>Номер телефона</label><input type="text" name="phone" placeholder="+7..." required></div>
            <button type="submit" class="btn">Отправить СМС</button>
        </form>
    </div>
    <div class="card">
        <h3>Изменение баланса</h3>
        <form method="POST"><input type="hidden" name="change_balance" value="1">
            <div class="form-group"><input type="text" name="username" placeholder="Логин" required></div>
            <div class="form-group"><input type="number" step="0.01" name="balance" placeholder="Баланс" required></div>
            <button type="submit" class="btn">Сохранить</button>
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
        <div class="card" style="max-width:400px; margin:0 auto;">
            <h3>Ввод кода</h3>
            <p>Страна: <b>{{ country }}</b> | Номер: <code>{{ phone }}</code></p>
            <form action="/admin/verify_account" method="POST">
                <input type="hidden" name="phone" value="{{ phone }}">
                <div class="form-group"><label>Код из Telegram</label><input type="text" name="code" required></div>
                <button type="submit" class="btn" style="width:100%;">Активировать</button>
            </form>
        </div>
        """
        return render_page(html, active_tab='profile', phone=phone, country=country)
    except Exception as e:
        flash(f'Ошибка: {e}', 'error')
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
        flash('Аккаунт успешно добавлен!', 'success')
    except Exception as e:
        flash(f'Ошибка: {e}', 'error')
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
