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
app.secret_key = 'vest_account_tailwind_glass_2026'

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

# Миграция и синхронизация структуры таблиц
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

# --- МАСТЕР-ШАБЛОН TAILWIND GLASSMORPHISM ---
BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>VestAccs — Премиальные Telegram-аккаунты</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #0F172A; }
        .glass { background: rgba(30, 41, 59, 0.45); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border: 1px solid rgba(255, 255, 255, 0.07); }
        .glass-premium { background: linear-gradient(135deg, rgba(36, 161, 222, 0.1), rgba(30, 41, 59, 0.7)); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border: 1px solid rgba(36, 161, 222, 0.3); }
        @keyframes textShimmer { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
        .shimmer-text { background: linear-gradient(90deg, #24A1DE, #60A5FA, #A78BFA, #24A1DE); background-size: 200% auto; -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; animation: textShimmer 4s linear infinite; }
        @keyframes buttonShimmer { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
        .btn-shimmer { background: linear-gradient(90deg, #24A1DE, #1d82b5, #24A1DE); background-size: 200% auto; animation: buttonShimmer 3s linear infinite; }
    </style>
</head>
<body class="text-slate-200 min-h-screen flex flex-col justify-between overflow-x-hidden antialiased selection:bg-[#24A1DE]/30 selection:text-white">

    <div class="absolute top-0 left-1/4 w-[500px] h-[500px] bg-[#24A1DE]/10 rounded-full blur-[140px] pointer-events-none"></div>
    <div class="absolute top-[60vh] right-1/4 w-[500px] h-[500px] bg-purple-600/10 rounded-full blur-[140px] pointer-events-none"></div>

    <header class="sticky top-0 z-50 glass border-b border-white/5">
        <div class="max-w-4xl mx-auto px-4 h-20 flex items-center justify-between">
            <a href="/" class="flex items-center gap-2 group">
                <span class="text-xl font-black tracking-wider text-white group-hover:text-[#24A1DE] transition-colors">
                    VEST<span class="text-[#24A1DE]">ACCS</span>
                </span>
            </a>
            
            <div class="flex items-center gap-3">
                {% if not session.get('user_id') %}
                    <a href="/login" class="px-4 py-2 rounded-xl text-xs font-bold text-white bg-white/5 border border-white/10 hover:bg-white/10 transition-all">Войти</a>
                    <a href="/register" class="px-4 py-2 rounded-xl text-xs font-bold text-white bg-[#24A1DE] hover:bg-[#1d82b5] transition-all">Регистрация</a>
                {% else %}
                    <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-800/60 border border-white/10 text-xs font-bold">
                        <span class="text-slate-400">БАЛАНС:</span>
                        <span class="text-white">{{ current_user_balance }} ₽</span>
                        <a href="/profile" class="w-5 h-5 rounded-full bg-[#24A1DE] flex items-center justify-center text-white font-black text-xs hover:bg-[#1d82b5]">+</a>
                    </div>
                {% endif %}
            </div>
        </div>
    </header>

    <main class="flex-grow max-w-3xl w-full mx-auto px-4 py-8 relative z-10">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="flash {% if category == 'success' %}bg-emerald-500/10 border-emerald-500/30 text-emerald-400{% else %}bg-rose-500/10 border-rose-500/30 text-rose-400{% endif %} p-4 rounded-xl border mb-6 text-sm font-medium">
                        {{ message }}
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        {{ content | safe }}
    </main>

    {% if session.get('user_id') %}
    <div class="fixed bottom-0 left-0 right-0 h-16 glass border-t border-white/5 flex justify-center items-center z-50">
        <div class="max-w-3xl w-full flex justify-around text-xs font-bold tracking-wider uppercase">
            <a href="/shop" class="px-4 py-2 transition-colors {% if active_tab == 'shop' %}text-[#24A1DE] border-b-2 border-[#24A1DE]{% else %}text-slate-400 hover:text-white{% endif %}">Каталог</a>
            <a href="/purchases" class="px-4 py-2 transition-colors {% if active_tab == 'purchases' %}text-[#24A1DE] border-b-2 border-[#24A1DE]{% else %}text-slate-400 hover:text-white{% endif %}">Заказы</a>
            <a href="/profile" class="px-4 py-2 transition-colors {% if active_tab == 'profile' %}text-[#24A1DE] border-b-2 border-[#24A1DE]{% else %}text-slate-400 hover:text-white{% endif %}">Профиль</a>
        </div>
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

# --- Маршруты ---

@app.route('/')
def index():
    html = """
    <div class="text-center py-6">
        <h1 class="text-3xl sm:text-5xl font-black tracking-tight text-white mb-4 leading-tight">
            Премиальные Telegram-аккаунты <br>
            <span class="shimmer-text">на физических SIM-картах</span>
        </h1>
        <p class="text-slate-400 text-sm sm:text-base max-w-xl mx-auto mb-8">
            Полная автоматизация: покупайте в 3 клика с моментальной выдачей. Максимальный уровень траста.
        </p>
    </div>
    
    <div class="text-xs font-bold text-slate-500 uppercase tracking-widest mb-4">Выбор категории</div>
    <div class="flex flex-col gap-3">
        {% for key, name in categories.items() %}
            <a href="/shop/{{ key }}" class="glass p-5 rounded-xl flex justify-between items-center hover:border-[#24A1DE]/40 transition-all duration-200 group">
                <span class="font-bold text-white group-hover:text-[#24A1DE] transition-colors">{{ name }}</span>
                <span class="text-[#24A1DE] font-black text-sm">Открыть →</span>
            </a>
        {% endfor %}
    </div>
    """
    return render_page(html, active_tab='shop', categories=CATEGORIES)

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
        flash('Регистрация успешно выполнена! Авторизуйтесь.', 'success')
        return redirect(url_for('login'))
    return render_page("""
    <div class="glass p-8 rounded-2xl max-w-md mx-auto border border-white/5">
        <h3 class="text-xl font-bold text-white mb-6">Создать аккаунт</h3>
        <form method="POST" class="space-y-4">
            <div><label class="block text-xs font-bold uppercase text-slate-400 mb-2">Логин</label><input type="text" name="username" required class="w-full p-3 bg-slate-900 border border-white/10 rounded-xl text-white focus:outline-none focus:border-[#24A1DE]"></div>
            <div><label class="block text-xs font-bold uppercase text-slate-400 mb-2">Пароль</label><input type="password" name="password" required class="w-full p-3 bg-slate-900 border border-white/10 rounded-xl text-white focus:outline-none focus:border-[#24A1DE]"></div>
            <button type="submit" class="w-full py-3.5 text-sm font-bold text-white btn-shimmer rounded-xl shadow-[0_0_20px_rgba(36,161,222,0.2)] transition-transform active:scale-[0.98]">Зарегистрироваться</button>
        </form>
    </div>
    """)

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
    return render_page("""
    <div class="glass p-8 rounded-2xl max-w-md mx-auto border border-white/5">
        <h3 class="text-xl font-bold text-white mb-6">Вход в панель</h3>
        <form method="POST" class="space-y-4">
            <div><label class="block text-xs font-bold uppercase text-slate-400 mb-2">Логин</label><input type="text" name="username" required class="w-full p-3 bg-slate-900 border border-white/10 rounded-xl text-white focus:outline-none focus:border-[#24A1DE]"></div>
            <div><label class="block text-xs font-bold uppercase text-slate-400 mb-2">Пароль</label><input type="password" name="password" required class="w-full p-3 bg-slate-900 border border-white/10 rounded-xl text-white focus:outline-none focus:border-[#24A1DE]"></div>
            <button type="submit" class="w-full py-3.5 text-sm font-bold text-white btn-shimmer rounded-xl shadow-[0_0_20px_rgba(36,161,222,0.2)] transition-transform active:scale-[0.98]">Войти</button>
        </form>
    </div>
    """)

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
    <div class="flex justify-between items-center mb-6">
        <h3 class="text-xl font-black text-white tracking-tight">{{ cat_name }}</h3>
        <a href="/" class="text-xs font-bold text-[#24A1DE] hover:underline">← Назад к категориям</a>
    </div>
    
    <div class="space-y-3">
        {% for c_slug, data in country_map.items() %}
            <div class="glass p-4 rounded-xl flex justify-between items-center">
                <div class="flex items-center gap-3 font-semibold text-sm text-white">
                    <span class="text-xl">{{ data.flag }}</span>
                    <span>{{ data.name }}</span>
                </div>
                <div class="flex items-center gap-4">
                    <span class="text-base font-black text-white">{{ prices.get(data.name, 300) }} ₽</span>
                    <form action="/buy/{{ cat_slug }}/{{ c_slug }}" method="POST" class="m-0">
                        <button type="submit" class="px-4 py-2 text-xs font-bold text-white btn-shimmer rounded-lg shadow-[0_0_10px_rgba(36,161,222,0.15)] transition-transform active:scale-95">Купить</button>
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
        flash('Некорректная конфигурация.', 'error')
        return redirect(url_for('index'))
        
    country_name = COUNTRY_MAP[c_slug]['name']
    price_id = f"{cat_slug}_{country_name}"
    price_rec = AccountPrice.query.get(price_id)
    price = price_rec.price if price_rec else 300.0
    
    if user.balance < price:
        flash(f'Недостаточно баланса! Цена: {int(price)} ₽. У вас: {int(user.balance)} ₽.', 'error')
        return redirect(url_for('profile'))
    
    account = Account.query.filter_by(category=cat_slug, country=country_name, is_sold=False).first()
    if not account:
        flash('Нет в наличии аккаунтов данной гео-позиции!', 'error')
        return redirect(url_for('shop_category', cat_slug=cat_slug))
    
    user.balance -= price
    account.is_sold = True
    db.session.add(Purchase(user_id=user.id, account_id=account.id, phone=account.phone, country=account.country, category=cat_slug))
    db.session.commit()
    
    flash(f'Успешный заказ! Номер телефона: {account.phone}', 'success')
    return redirect(url_for('purchases'))

@app.route('/purchases')
def purchases():
    if not session.get('user_id'): return redirect(url_for('login'))
    my_purchases = Purchase.query.filter_by(user_id=session['user_id']).all()
    html = """
    <h3 class="text-xl font-black text-white mb-6 tracking-tight">Ваши Заказы</h3>
    {% if not my_purchases %}<p class="text-sm text-slate-400">Список покупок пуст.</p>{% endif %}
    <div class="space-y-3">
        {% for p in my_purchases %}
            <div class="glass p-5 rounded-xl flex justify-between items-center flex-wrap gap-4">
                <div>
                    <span class="text-[10px] font-bold text-slate-500 uppercase tracking-widest">{{ cat_names.get(p.category) }}</span>
                    <div class="text-sm font-bold text-white mt-1">{{ p.country }}: <code class="text-[#24A1DE] font-mono text-base px-2 py-0.5 bg-white/5 rounded border border-white/5">{{ p.phone }}</code></div>
                </div>
                <div class="flex items-center gap-3">
                    <button class="px-4 py-2 text-xs font-bold text-white bg-white/5 hover:bg-white/10 border border-white/10 rounded-xl transition-all" onclick="getCode({{ p.account_id }}, this)">Получить код</button>
                    <span id="out-{{ p.account_id }}" class="text-sm font-black text-emerald-400 tracking-wider"></span>
                </div>
            </div>
        {% endfor %}
    </div>
    <script>
    function getCode(id, btn) {
        btn.innerText = 'Поиск...';
        fetch('/get_code/' + id).then(r => r.json()).then(data => {
            btn.innerText = 'Получить код';
            const el = document.getElementById('out-'+id);
            if(data.success) { el.innerText = 'КОД: ' + data.code; el.style.color = '#34d399'; }
            else { el.innerText = data.message; el.style.color = '#f87171'; }
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
    <div class="glass p-6 rounded-2xl">
        <h3 class="text-xl font-bold text-white mb-2">Личный кабинет</h3>
        <div class="text-xs text-slate-500 font-bold uppercase tracking-widest">Текущий баланс:</div>
        <div class="text-4xl font-black text-emerald-400 mt-2 mb-6">{{ int(user.balance) }} ₽</div>
        
        <hr class="border-white/5 my-6">
        
        <h4 class="text-sm font-bold text-white mb-4">Пополнение счета через Crypto Bot</h4>
        <form action="/deposit" method="POST" class="max-w-xs space-y-4">
            <div>
                <label class="block text-xs font-semibold text-slate-400 mb-2">Введите сумму в USDT</label>
                <input type="number" step="0.01" name="amount" value="10.00" required class="w-full p-3 bg-slate-900 border border-white/10 rounded-xl text-white focus:outline-none focus:border-[#24A1DE]">
            </div>
            <button type="submit" class="w-full py-3 text-sm font-bold text-white btn-shimmer rounded-xl shadow-[0_0_15px_rgba(36,161,222,0.2)]">Сгенерировать счет</button>
        </form>
        {% if session.get('is_admin') %}
            <a href="/admin" class="w-full block text-center py-3 bg-white/5 hover:bg-white/10 text-white font-bold text-xs uppercase tracking-wider rounded-xl border border-white/10 mt-6">💼 Админ Панель управления</a>
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
            <div class="glass p-8 rounded-2xl text-center max-w-md mx-auto">
                <h3 class="text-xl font-bold text-white mb-4">Инвойс выставлен</h3>
                <p class="text-slate-400 text-sm mb-6">Для завершения перевода на сумму {{ amount }} USDT нажмите кнопку оплаты ниже:</p>
                <a href="{{ url }}" target="_blank" class="inline-block w-full py-3.5 btn-shimmer text-white font-bold rounded-xl shadow-lg mb-4">Оплатить в Crypto Bot</a>
                <hr class="border-white/5 my-4">
                <form action="/check_invoice/{{ inv_id }}" method="POST"><button type="submit" class="w-full py-3 bg-white/5 hover:bg-white/10 text-white font-bold text-xs uppercase rounded-xl border border-white/10">Проверить зачисление</button></form>
            </div>
            """
            return render_page(html, active_tab='profile', url=data['pay_url'], amount=amount, inv_id=inv.id)
    except Exception as e: flash(f'Ошибка платежной системы: {e}', 'error')
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
            flash('Счет успешно оплачен! Баланс обновлен.', 'success')
        else: flash('Оплата не зафиксирована. Попробуйте снова чуть позже.', 'error')
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
            flash('Цена конфигурации успешно обновлена!', 'success')
            
    db.session.expire_all()
    all_users = User.query.order_by(User.id.desc()).all()
    all_accounts = Account.query.order_by(Account.id.desc()).all()
            
    html = """
    <h3 class="text-xl font-black text-white tracking-tight mb-6">Панель управления</h3>
    
    <div class="space-y-6">
        <div class="glass p-6 rounded-xl">
            <h4 class="text-sm font-bold uppercase tracking-wider text-slate-300 mb-4">1. Импортировать аккаунт (Telethon)</h4>
            <form action="/admin/add_account" method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-400 mb-2">Категория</label>
                    <select name="category" class="w-full p-3 bg-slate-950 border border-white/10 rounded-xl text-white focus:outline-none">
                        {% for key, val in categories.items() %}<option value="{{ key }}">{{ val }}</option>{% endfor %}
                    </select>
                </div>
                <div><label class="block text-xs font-semibold text-slate-400 mb-2">Номер телефона</label><input type="text" name="phone" placeholder="+7..." required class="w-full p-3 bg-slate-950 border border-white/10 rounded-xl text-white focus:outline-none"></div>
                <button type="submit" class="w-full py-3 text-xs font-bold uppercase tracking-widest text-white btn-shimmer rounded-xl">Выслать код в Telegram</button>
            </form>
        </div>
        
        <div class="glass p-6 rounded-xl">
            <h4 class="text-sm font-bold uppercase tracking-wider text-slate-300 mb-4">2. Изменение цен категорий (₽)</h4>
            <form method="POST" class="space-y-4"><input type="hidden" name="set_price" value="1">
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 mb-2">Категория</label>
                        <select name="category" class="w-full p-3 bg-slate-950 border border-white/10 rounded-xl text-white focus:outline-none">
                            {% for key, val in categories.items() %}<option value="{{ key }}">{{ val }}</option>{% endfor %}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 mb-2">Страна</label>
                        <select name="country" class="w-full p-3 bg-slate-950 border border-white/10 rounded-xl text-white focus:outline-none">
                            {% for c_slug, data in country_map.items() %}<option value="{{ data.name }}">{{ data.name }}</option>{% endfor %}
                        </select>
                    </div>
                </div>
                <div><label class="block text-xs font-semibold text-slate-400 mb-2">Цена в рублях</label><input type="number" name="price" required class="w-full p-3 bg-slate-950 border border-white/10 rounded-xl text-white focus:outline-none"></div>
                <button type="submit" class="w-full py-3 text-xs font-bold uppercase tracking-widest text-white btn-shimmer rounded-xl">Применить стоимость</button>
            </form>
        </div>
        
        <div class="glass p-6 rounded-xl">
            <h4 class="text-sm font-bold uppercase tracking-wider text-slate-300 mb-4">3. Изменение баланса клиентам (₽)</h4>
            <form method="POST" class="space-y-4"><input type="hidden" name="change_balance" value="1">
                <div class="grid grid-cols-2 gap-4">
                    <input type="text" name="username" placeholder="Логин" required class="w-full p-3 bg-slate-950 border border-white/10 rounded-xl text-white focus:outline-none">
                    <input type="number" step="0.01" name="balance" placeholder="Сумма в ₽" required class="w-full p-3 bg-slate-950 border border-white/10 rounded-xl text-white focus:outline-none">
                </div>
                <button type="submit" class="w-full py-3 text-xs font-bold uppercase tracking-widest text-white btn-shimmer rounded-xl">Обновить баланс</button>
            </form>
        </div>

        <div class="glass p-6 rounded-xl">
            <h4 class="text-sm font-bold uppercase tracking-wider text-slate-300 mb-4">Пользователи</h4>
            <div class="overflow-x-auto">
                <table class="text-slate-300">
                    <thead><tr><th>ID</th><th>Логин</th><th>Баланс</th><th>Роль</th></tr></thead>
                    <tbody>
                        {% for u in all_users %}
                        <tr><td>{{ u.id }}</td><td><b>{{ u.username }}</b></td><td>{{ int(u.balance) }} ₽</td><td>{% if u.is_admin %}Админ{% else %}Юзер{% endif %}</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="glass p-6 rounded-xl">
            <h4 class="text-sm font-bold uppercase tracking-wider text-slate-300 mb-4">Загруженный пул сессий</h4>
            <div class="overflow-x-auto">
                <table class="text-slate-300">
                    <thead><tr><th>ID</th><th>Номер</th><th>Гео</th><th>Категория</th><th>Статус</th></tr></thead>
                    <tbody>
                        {% for acc in all_accounts %}
                        <tr><td>{{ acc.id }}</td><td><code>{{ acc.phone }}</code></td><td>{{ acc.country }}</td><td>{{ categories.get(acc.category, acc.category) }}</td><td>{% if acc.is_sold %}<span class="text-rose-400 font-bold">Продан</span>{% else %}<span class="text-emerald-400 font-bold">Доступен</span>{% endif %}</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
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
        flash(f'Ошибка Telethon: {err}', 'error')
        return redirect(url_for('admin_panel'))
        
    PENDING_REGISTRATIONS[phone] = {'session_str': session_str, 'phone_code_hash': phone_code_hash, 'country': country, 'category': category}
    html = """
    <div class="glass p-8 rounded-2xl max-w-md mx-auto text-center">
        <h3 class="text-xl font-bold text-white mb-2">Ввод SMS-кода</h3>
        <p class="text-slate-400 text-xs mb-6">Категория: <b>{{ cat_name }}</b> | Гео: <b>{{ country }}</b></p>
        <form action="/admin/verify_account" method="POST" class="space-y-4">
            <input type="hidden" name="phone" value="{{ phone }}">
            <input type="text" name="code" placeholder="Введите 5-значный код" required autocomplete="off" class="w-full p-3.5 bg-slate-900 border border-white/10 rounded-xl text-center text-white tracking-widest text-lg font-black focus:outline-none focus:border-[#24A1DE]">
            <button type="submit" class="w-full py-3.5 font-bold text-white btn-shimmer rounded-xl">Активировать сессию</button>
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
        flash('Регистрация аннулирована.', 'error')
        return redirect(url_for('admin_panel'))
    data = PENDING_REGISTRATIONS[phone]
    final_session, err = tg_sign_in(phone, code, data['phone_code_hash'], data['session_str'])
    if err:
        flash(f'Ошибка авторизации: {err}', 'error')
        return redirect(url_for('admin_panel'))
        
    db.session.add(Account(phone=phone, country=data['country'].strip(), category=data['category'], session_string=final_session))
    db.session.commit()
    del PENDING_REGISTRATIONS[phone]
    flash('Сессия Telethon успешно импортирована!', 'success')
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
