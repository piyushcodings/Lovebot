"""
Telegram Mass Reporter Bot
Advanced session-based reporting system with admin controls
"""

import os
import sys
import json
import time
import random
import string
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from telethon import TelegramClient, events, Button
from telethon.tl.functions.account import ReportPeer
from telethon.tl.types import (
    InputReportReasonSpam, InputReportReasonViolence, 
    InputReportReasonPornography, InputReportReasonChildAbuse,
    InputReportReasonCopyright, InputReportReasonFake,
    InputReportReasonOther, InputPeerUser, InputPeerChannel
)
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from telethon.network import ConnectionTcpFull, ConnectionTcpMTProxyRandomizedIntermediate

# Configuration
API_ID = 123456  # Your API ID
API_HASH = "your_api_hash_here"  # Your API Hash
BOT_TOKEN = "your_bot_token_here"  # Your Bot Token
ADMIN_IDS = [123456789]  # List of admin Telegram IDs

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database setup
class Database:
    def __init__(self, db_path: str = "reporter_bot.db"):
        self.db_path = db_path
        self.init_db()
    
    def get_conn(self):
        return sqlite3.connect(self.db_path)
    
    def init_db(self):
        conn = self.get_conn()
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                approved INTEGER DEFAULT 0,
                subscription_end TIMESTAMP,
                reports_used INTEGER DEFAULT 0,
                reports_limit INTEGER DEFAULT 100,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_banned INTEGER DEFAULT 0
            )
        ''')
        
        # Sessions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_string TEXT UNIQUE,
                phone TEXT UNIQUE,
                proxy TEXT,
                is_active INTEGER DEFAULT 1,
                last_used TIMESTAMP,
                report_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Redeem codes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS redeem_codes (
                code TEXT PRIMARY KEY,
                days INTEGER,
                reports_limit INTEGER,
                max_sessions INTEGER,
                uses_left INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Reports log table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                target TEXT,
                reason TEXT,
                session_id INTEGER,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    
    def add_user(self, user_id: int, username: str = None):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        conn.commit()
        conn.close()
    
    def approve_user(self, user_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET approved = 1 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        conn.close()
    
    def ban_user(self, user_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET is_banned = 1 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        conn.close()
    
    def is_approved(self, user_id: int) -> bool:
        if user_id in ADMIN_IDS:
            return True
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT approved, is_banned FROM users WHERE user_id = ?",
            (user_id,)
        )
        result = cursor.fetchone()
        conn.close()
        if not result:
            return False
        return result[0] == 1 and result[1] == 0
    
    def is_banned(self, user_id: int) -> bool:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT is_banned FROM users WHERE user_id = ?",
            (user_id,)
        )
        result = cursor.fetchone()
        conn.close()
        return result[0] == 1 if result else False
    
    def get_user(self, user_id: int) -> Optional[dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        )
        result = cursor.fetchone()
        conn.close()
        if result:
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, result))
        return None
    
    def add_subscription(self, user_id: int, days: int, reports_limit: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        end_date = datetime.now() + timedelta(days=days)
        cursor.execute(
            """UPDATE users SET 
                subscription_end = ?, 
                reports_limit = reports_limit + ?,
                approved = 1
            WHERE user_id = ?""",
            (end_date, reports_limit, user_id)
        )
        conn.commit()
        conn.close()
    
    def has_active_subscription(self, user_id: int) -> bool:
        if user_id in ADMIN_IDS:
            return True
        user = self.get_user(user_id)
        if not user or not user['subscription_end']:
            return False
        return datetime.now() < datetime.fromisoformat(user['subscription_end'])
    
    def can_report(self, user_id: int) -> bool:
        if user_id in ADMIN_IDS:
            return True
        user = self.get_user(user_id)
        if not user:
            return False
        return user['reports_used'] < user['reports_limit']
    
    def increment_reports(self, user_id: int, count: int = 1):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET reports_used = reports_used + ? WHERE user_id = ?",
            (count, user_id)
        )
        conn.commit()
        conn.close()
    
    def add_session(self, session_string: str, phone: str, proxy: str = None):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO sessions (session_string, phone, proxy) VALUES (?, ?, ?)",
            (session_string, phone, proxy)
        )
        conn.commit()
        conn.close()
    
    def get_all_sessions(self) -> List[dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE is_active = 1")
        results = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        conn.close()
        return [dict(zip(columns, row)) for row in results]
    
    def update_session_usage(self, session_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE sessions SET 
                last_used = CURRENT_TIMESTAMP,
                report_count = report_count + 1
            WHERE id = ?""",
            (session_id,)
        )
        conn.commit()
        conn.close()
    
    def delete_session(self, session_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()
    
    def create_redeem_code(self, code: str, days: int, reports_limit: int, max_sessions: int, uses: int, admin_id: int):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO redeem_codes 
                (code, days, reports_limit, max_sessions, uses_left, created_by)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (code, days, reports_limit, max_sessions, uses, admin_id)
        )
        conn.commit()
        conn.close()
    
    def redeem_code(self, code: str, user_id: int) -> Optional[dict]:
        conn = self.get_conn()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM redeem_codes WHERE code = ? AND uses_left > 0",
            (code,)
        )
        result = cursor.fetchone()
        
        if result:
            columns = [desc[0] for desc in cursor.description]
            code_data = dict(zip(columns, result))
            
            # Decrement uses
            cursor.execute(
                "UPDATE redeem_codes SET uses_left = uses_left - 1 WHERE code = ?",
                (code,)
            )
            
            # Add subscription
            self.add_subscription(user_id, code_data['days'], code_data['reports_limit'])
            
            conn.commit()
            conn.close()
            return code_data
        
        conn.close()
        return None
    
    def log_report(self, user_id: int, target: str, reason: str, session_id: int, status: str):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO reports_log 
                (user_id, target, reason, session_id, status)
            VALUES (?, ?, ?, ?, ?)""",
            (user_id, target, reason, session_id, status)
        )
        conn.commit()
        conn.close()
    
    def get_stats(self) -> dict:
        conn = self.get_conn()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE approved = 1")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM sessions WHERE is_active = 1")
        total_sessions = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM reports_log")
        total_reports = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM reports_log WHERE date(created_at) = date('now')")
        today_reports = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_users': total_users,
            'total_sessions': total_sessions,
            'total_reports': total_reports,
            'today_reports': today_reports
        }

# Session Manager
class SessionManager:
    def __init__(self, db: Database):
        self.db = db
        self.clients: Dict[int, TelegramClient] = {}
        self.current_index = 0
    
    async def load_sessions(self):
        """Load all sessions from database"""
        sessions = self.db.get_all_sessions()
        for session_data in sessions:
            try:
                client = await self.create_client_from_session(session_data)
                if client:
                    self.clients[session_data['id']] = {
                        'client': client,
                        'data': session_data
                    }
            except Exception as e:
                logger.error(f"Failed to load session {session_data['id']}: {e}")
        
        logger.info(f"Loaded {len(self.clients)} sessions")
    
    async def create_client_from_session(self, session_data: dict) -> Optional[TelegramClient]:
        """Create TelegramClient from session string"""
        try:
            session = StringSession(session_data['session_string'])
            
            proxy = None
            if session_data['proxy']:
                proxy = json.loads(session_data['proxy'])
            
            client = TelegramClient(
                session,
                API_ID,
                API_HASH,
                proxy=proxy
            )
            
            await client.connect()
            if await client.is_user_authorized():
                return client
            else:
                await client.disconnect()
                return None
        except Exception as e:
            logger.error(f"Session creation error: {e}")
            return None
    
    def get_next_session(self) -> Optional[Tuple[TelegramClient, dict]]:
        """Get next session in rotation"""
        if not self.clients:
            return None
        
        session_ids = list(self.clients.keys())
        session_id = session_ids[self.current_index % len(session_ids)]
        self.current_index += 1
        
        session_info = self.clients[session_id]
        return session_info['client'], session_info['data']
    
    async def add_session_from_string(self, session_string: str, phone: str, proxy: str = None):
        """Add new session from string"""
        self.db.add_session(session_string, phone, proxy)
        # Reload sessions
        await self.load_sessions()
    
    async def add_session_from_login(self, phone: str, code: str = None, password: str = None, proxy: str = None):
        """Login and create new session"""
        session = StringSession()
        
        proxy_dict = None
        if proxy:
            proxy_dict = json.loads(proxy)
        
        client = TelegramClient(session, API_ID, API_HASH, proxy=proxy_dict)
        
        try:
            await client.connect()
            
            if not await client.is_user_authorized():
                if not code:
                    # Send code
                    await client.send_code_request(phone)
                    return {'status': 'code_sent', 'phone': phone}
                
                try:
                    await client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    if not password:
                        return {'status': '2fa_required', 'phone': phone}
                    await client.sign_in(password=password)
            
            # Get session string
            session_string = client.session.save()
            await client.disconnect()
            
            # Save to database
            self.db.add_session(session_string, phone, proxy)
            await self.load_sessions()
            
            return {'status': 'success', 'session_string': session_string}
            
        except PhoneCodeInvalidError:
            await client.disconnect()
            return {'status': 'error', 'message': 'Invalid code'}
        except Exception as e:
            await client.disconnect()
            return {'status': 'error', 'message': str(e)}
    
    def get_session_count(self) -> int:
        return len(self.clients)

# Report Manager
class ReportManager:
    REASONS = {
        'spam': InputReportReasonSpam(),
        'violence': InputReportReasonViolence(),
        'pornography': InputReportReasonPornography(),
        'child_abuse': InputReportReasonChildAbuse(),
        'copyright': InputReportReasonCopyright(),
        'fake': InputReportReasonFake(),
        'other': InputReportReasonOther()
    }
    
    def __init__(self, db: Database, session_manager: SessionManager):
        self.db = db
        self.session_manager = session_manager
        self.active_reports: Dict[int, bool] = {}  # Track active reporting
    
    async def report_entity(self, target: str, reason: str, message: str = None) -> Tuple[bool, str]:
        """Report a single entity"""
        client, session_data = self.session_manager.get_next_session()
        if not client:
            return False, "No sessions available"
        
        try:
            # Get entity
            try:
                entity = await client.get_entity(target)
            except Exception as e:
                return False, f"Failed to get entity: {e}"
            
            # Get reason
            report_reason = self.REASONS.get(reason, InputReportReasonOther())
            
            # Send report
            result = await client(ReportPeer(
                peer=entity,
                reason=report_reason,
                message=message or "Reported for violating Telegram's Terms of Service"
            ))
            
            # Update usage
            self.db.update_session_usage(session_data['id'])
            self.db.log_report(0, target, reason, session_data['id'], 'success')
            
            return True, "Report sent successfully"
            
        except Exception as e:
            self.db.log_report(0, target, reason, session_data['id'], f'error: {e}')
            return False, str(e)
    
    async def mass_report(self, user_id: int, target: str, reason: str, count: int, delay: Tuple[float, float], message: str = None):
        """Mass report with rotation"""
        if not self.db.can_report(user_id):
            return False, "Report limit reached"
        
        if self.active_reports.get(user_id):
            return False, "Already running a report"
        
        self.active_reports[user_id] = True
        
        successful = 0
        failed = 0
        
        for i in range(count):
            if not self.active_reports.get(user_id):
                break
            
            success, msg = await self.report_entity(target, reason, message)
            if success:
                successful += 1
            else:
                failed += 1
            
            # Random delay
            delay_time = random.uniform(delay[0], delay[1])
            await asyncio.sleep(delay_time)
        
        self.active_reports[user_id] = False
        self.db.increment_reports(user_id, successful)
        
        return True, f"Completed: {successful} successful, {failed} failed"

# Bot Handlers
class ReporterBot:
    def __init__(self):
        self.db = Database()
        self.session_manager = SessionManager(self.db)
        self.report_manager = ReportManager(self.db, self.session_manager)
        self.bot = TelegramClient('bot_session', API_ID, API_HASH)
        
        # Temporary storage for user states
        self.user_states: Dict[int, dict] = {}
        self.login_states: Dict[int, dict] = {}
    
    async def start(self):
        await self.session_manager.load_sessions()
        await self.bot.start(bot_token=BOT_TOKEN)
        
        # Register handlers
        self.register_handlers()
        
        logger.info("Bot started")
        await self.bot.run_until_disconnected()
    
    def register_handlers(self):
        @self.bot.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            user_id = event.sender_id
            username = event.sender.username
            
            self.db.add_user(user_id, username)
            
            if self.db.is_banned(user_id):
                await event.respond("🚫 **You are banned from using this bot.**", parse_mode='markdown')
                return
            
            if not self.db.is_approved(user_id):
                await event.respond(
                    "⏳ **Your account is pending approval.**\n\n"
                    "Please wait for an admin to approve your account.",
                    parse_mode='markdown'
                )
                return
            
            await self.show_main_menu(event)
        
        @self.bot.on(events.CallbackQuery())
        async def callback_handler(event):
            await self.handle_callback(event)
        
        @self.bot.on(events.NewMessage())
        async def message_handler(event):
            await self.handle_message(event)
    
    async def show_main_menu(self, event, edit=False):
        user_id = event.sender_id
        
        # Check subscription
        has_sub = self.db.has_active_subscription(user_id)
        user_data = self.db.get_user(user_id)
        
        text = (
            "🤖 **Welcome to Advanced Mass Reporter Bot**\n\n"
            f"👤 **User:** `{user_id}`\n"
            f"📊 **Status:** {'✅ Active' if has_sub else '⚠️ No Subscription'}\n"
        )
        
        if user_data and user_data['subscription_end']:
            text += f"⏰ **Expires:** {user_data['subscription_end'][:10]}\n"
            text += f"📈 **Reports:** {user_data['reports_used']}/{user_data['reports_limit']}\n"
        
        text += f"\n🔌 **Active Sessions:** {self.session_manager.get_session_count()}"
        
        buttons = [
            [Button.inline("🚀 Start Reporting", b"start_report")],
            [Button.inline("🔑 Add Session", b"add_session"), Button.inline("📱 Login", b"phone_login")],
            [Button.inline("🎫 Redeem Code", b"redeem"), Button.inline("📊 Statistics", b"stats")],
            [Button.inline("⚙️ Settings", b"settings"), Button.inline("❓ Help", b"help")]
        ]
        
        if user_id in ADMIN_IDS:
            buttons.insert(0, [Button.inline("🔧 Admin Panel", b"admin")])
        
        if edit:
            await event.edit(text, buttons=buttons, parse_mode='markdown')
        else:
            await event.respond(text, buttons=buttons, parse_mode='markdown')
    
    async def handle_callback(self, event):
        user_id = event.sender_id
        data = event.data.decode('utf-8')
        
        # Check ban
        if self.db.is_banned(user_id) and user_id not in ADMIN_IDS:
            await event.answer("🚫 You are banned!", alert=True)
            return
        
        # Admin panel
        if data == "admin":
            await self.show_admin_panel(event)
            return
        
        if data.startswith("approve_"):
            target_id = int(data.split("_")[1])
            self.db.approve_user(target_id)
            await event.answer("✅ User approved!")
            await self.show_admin_panel(event)
            return
        
        if data.startswith("ban_"):
            target_id = int(data.split("_")[1])
            self.db.ban_user(target_id)
            await event.answer("🚫 User banned!")
            await self.show_admin_panel(event)
            return
        
        if data == "create_code":
            await event.edit(
                "🎫 **Create Redeem Code**\n\n"
                "Send in format:\n"
                "`DAYS REPORTS_LIMIT MAX_SESSIONS USES`\n\n"
                "Example: `30 500 10 5`",
                parse_mode='markdown'
            )
            self.user_states[user_id] = {'action': 'create_code'}
            return
        
        if data == "list_sessions":
            sessions = self.db.get_all_sessions()
            text = "📱 **Active Sessions:**\n\n"
            for s in sessions:
                text += f"ID: `{s['id']}` | Phone: `{s['phone']}` | Reports: {s['report_count']}\n"
            
            await event.edit(text, buttons=[[Button.inline("🔙 Back", b"admin")]], parse_mode='markdown')
            return
        
        # Main menu actions
        if data == "start_report":
            if not self.db.has_active_subscription(user_id):
                await event.answer("❌ No active subscription!", alert=True)
                return
            
            await event.edit(
                "🎯 **Mass Reporting**\n\n"
                "Send target in format:\n"
                "`TARGET|REASON|COUNT|MIN_DELAY|MAX_DELAY|MESSAGE`\n\n"
                "**Reasons:** spam, violence, pornography, child_abuse, copyright, fake, other\n\n"
                "**Example:**\n"
                "`@channelname|spam|50|2|5|This channel sends spam`",
                parse_mode='markdown',
                buttons=[[Button.inline("🔙 Back", b"back_main")]]
            )
            self.user_states[user_id] = {'action': 'mass_report'}
            return
        
        if data == "add_session":
            await event.edit(
                "🔑 **Add Session**\n\n"
                "Choose method:",
                buttons=[
                    [Button.inline("📋 Paste Session String", b"paste_session")],
                    [Button.inline("📱 Phone Login", b"phone_login")],
                    [Button.inline("🔙 Back", b"back_main")]
                ]
            )
            return
        
        if data == "paste_session":
            await event.edit(
                "📋 **Add Session from String**\n\n"
                "Send session string in format:\n"
                "`SESSION_STRING|PHONE|PROXY(optional)`\n\n"
                "Proxy format (optional):\n"
                "`{\"proxy_type\": \"socks5\", \"addr\": \"host\", \"port\": 1080, \"username\": \"user\", \"password\": \"pass\"}`",
                parse_mode='markdown',
                buttons=[[Button.inline("🔙 Back", b"add_session")]]
            )
            self.user_states[user_id] = {'action': 'add_session_string'}
            return
        
        if data == "phone_login":
            await event.edit(
                "📱 **Phone Login**\n\n"
                "Send your phone number with country code:\n"
                "Example: `+1234567890`",
                parse_mode='markdown',
                buttons=[[Button.inline("🔙 Back", b"add_session")]]
            )
            self.user_states[user_id] = {'action': 'phone_login'}
            return
        
        if data == "redeem":
            await event.edit(
                "🎫 **Redeem Code**\n\n"
                "Enter your redeem code:",
                parse_mode='markdown',
                buttons=[[Button.inline("🔙 Back", b"back_main")]]
            )
            self.user_states[user_id] = {'action': 'redeem'}
            return
        
        if data == "stats":
            stats = self.db.get_stats()
            user_data = self.db.get_user(user_id)
            
            text = (
                "📊 **Statistics**\n\n"
                f"👥 Total Users: `{stats['total_users']}`\n"
                f"📱 Active Sessions: `{stats['total_sessions']}`\n"
                f"📢 Total Reports: `{stats['total_reports']}`\n"
                f"📅 Today: `{stats['today_reports']}`\n\n"
                f"**Your Stats:**\n"
                f"Reports Used: `{user_data['reports_used']}/{user_data['reports_limit']}`\n"
            )
            
            await event.edit(text, buttons=[[Button.inline("🔙 Back", b"back_main")]], parse_mode='markdown')
            return
        
        if data == "settings":
            await event.edit(
                "⚙️ **Settings**\n\n"
                "Configure your preferences:",
                buttons=[
                    [Button.inline("🔌 Sessions", b"my_sessions")],
                    [Button.inline("📊 Report History", b"report_history")],
                    [Button.inline("🔙 Back", b"back_main")]
                ]
            )
            return
        
        if data == "my_sessions":
            sessions = self.db.get_all_sessions()
            text = "🔌 **Your Sessions:**\n\n"
            for s in sessions[:10]:
                text += f"📱 `{s['phone']}` - Reports: {s['report_count']}\n"
            
            await event.edit(text, buttons=[[Button.inline("🔙 Back", b"settings")]], parse_mode='markdown')
            return
        
        if data == "help":
            await event.edit(
                "❓ **Help Guide**\n\n"
                "**Getting Started:**\n"
                "1. Get approved by admin\n"
                "2. Redeem a code or purchase subscription\n"
                "3. Add sessions via login or session string\n"
                "4. Start reporting!\n\n"
                "**Session Management:**\n"
                "- Sessions rotate automatically\n"
                - All sessions are used in round-robin fashion\n"
                "- Supports SOCKS5 proxies\n\n"
                "**Reporting:**\n"
                "- Set custom delays between reports\n"
                "- Choose from multiple report reasons\n"
                "- Track your usage in real-time",
                buttons=[[Button.inline("🔙 Back", b"back_main")]],
                parse_mode='markdown'
            )
            return
        
        if data == "back_main":
            await self.show_main_menu(event, edit=True)
            return
    
    async def handle_message(self, event):
        user_id = event.sender_id
        text = event.text
        
        if user_id not in self.user_states:
            return
        
        state = self.user_states[user_id]
        action = state.get('action')
        
        if action == 'create_code' and user_id in ADMIN_IDS:
            try:
                parts = text.split()
                days = int(parts[0])
                reports = int(parts[1])
                sessions = int(parts[2])
                uses = int(parts[3])
                
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
                self.db.create_redeem_code(code, days, reports, sessions, uses, user_id)
                
                await event.respond(
                    f"✅ **Code Created:**\n\n"
                    f"`{code}`\n\n"
                    f"Days: {days}\n"
                    f"Reports: {reports}\n"
                    f"Max Sessions: {sessions}\n"
                    f"Uses: {uses}",
                    parse_mode='markdown'
                )
                del self.user_states[user_id]
                
            except Exception as e:
                await event.respond(f"❌ Error: {e}")
        
        elif action == 'mass_report':
            try:
                parts = text.split('|')
                if len(parts) < 5:
                    await event.respond("❌ Invalid format!")
                    return
                
                target = parts[0]
                reason = parts[1]
                count = int(parts[2])
                min_delay = float(parts[3])
                max_delay = float(parts[4])
                message = parts[5] if len(parts) > 5 else None
                
                if not self.db.can_report(user_id):
                    await event.respond("❌ Report limit reached!")
                    return
                
                # Start reporting
                msg = await event.respond("🚀 **Starting mass report...**", parse_mode='markdown')
                
                success, result = await self.report_manager.mass_report(
                    user_id, target, reason, count, (min_delay, max_delay), message
                )
                
                await msg.edit(f"✅ **Report Complete**\n\n{result}", parse_mode='markdown')
                del self.user_states[user_id]
                
            except Exception as e:
                await event.respond(f"❌ Error: {e}")
        
        elif action == 'add_session_string':
            try:
                parts = text.split('|')
                session_string = parts[0]
                phone = parts[1] if len(parts) > 1 else "Unknown"
                proxy = parts[2] if len(parts) > 2 else None
                
                await self.session_manager.add_session_from_string(session_string, phone, proxy)
                
                await event.respond(
                    "✅ **Session added successfully!**\n\n"
                    f"Phone: `{phone}`\n"
                    f"Total Sessions: {self.session_manager.get_session_count()}",
                    parse_mode='markdown'
                )
                del self.user_states[user_id]
                
            except Exception as e:
                await event.respond(f"❌ Error: {e}")
        
        elif action == 'phone_login':
            phone = text.strip()
            result = await self.session_manager.add_session_from_login(phone)
            
            if result['status'] == 'code_sent':
                await event.respond(
                    "📱 **Code sent!**\n\n"
                    "Enter the code you received:",
                    parse_mode='markdown'
                )
                self.login_states[user_id] = {'phone': phone, 'step': 'code'}
                
            else:
                await event.respond(f"❌ Error: {result.get('message', 'Unknown')}")
        
        elif action == 'redeem':
            code = text.strip().upper()
            result = self.db.redeem_code(code, user_id)
            
            if result:
                await event.respond(
                    "🎉 **Code Redeemed Successfully!**\n\n"
                    f"Days: {result['days']}\n"
                    f"Reports Limit: +{result['reports_limit']}\n"
                    f"Max Sessions: {result['max_sessions']}",
                    parse_mode='markdown'
                )
            else:
                await event.respond("❌ Invalid or expired code!")
            
            del self.user_states[user_id]
        
        # Handle login flow
        if user_id in self.login_states:
            login_state = self.login_states[user_id]
            step = login_state.get('step')
            
            if step == 'code':
                code = text.strip()
                phone = login_state['phone']
                result = await self.session_manager.add_session_from_login(phone, code=code)
                
                if result['status'] == '2fa_required':
                    await event.respond("🔐 **2FA Required!**\n\nEnter your password:")
                    login_state['step'] = '2fa'
                    login_state['code'] = code
                    
                elif result['status'] == 'success':
                    await event.respond(
                        "✅ **Login successful!**\n"
                        f"Session added. Total: {self.session_manager.get_session_count()}"
                    )
                    del self.login_states[user_id]
                    
                else:
                    await event.respond(f"❌ Error: {result.get('message', 'Unknown')}")
                    del self.login_states[user_id]
            
            elif step == '2fa':
                password = text.strip()
                phone = login_state['phone']
                code = login_state['code']
                
                result = await self.session_manager.add_session_from_login(phone, code=code, password=password)
                
                if result['status'] == 'success':
                    await event.respond(
                        "✅ **Login successful!**\n"
                        f"Session added. Total: {self.session_manager.get_session_count()}"
                    )
                else:
                    await event.respond(f"❌ Error: {result.get('message', 'Unknown')}")
                
                del self.login_states[user_id]
    
    async def show_admin_panel(self, event):
        user_id = event.sender_id
        
        if user_id not in ADMIN_IDS:
            await event.answer("❌ Unauthorized!", alert=True)
            return
        
        # Get pending users
        conn = self.db.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username FROM users WHERE approved = 0 AND is_banned = 0")
        pending = cursor.fetchall()
        conn.close()
        
        text = (
            "🔧 **Admin Panel**\n\n"
            f"⏳ Pending Approvals: {len(pending)}\n"
            f"📱 Active Sessions: {self.session_manager.get_session_count()}\n"
        )
        
        buttons = [
            [Button.inline("🎫 Create Redeem Code", b"create_code")],
            [Button.inline("📱 List Sessions", b"list_sessions")],
            [Button.inline("🔙 Back", b"back_main")]
        ]
        
        # Add pending users
        for uid, uname in pending[:5]:
            buttons.insert(0, [
                Button.inline(f"✅ Approve {uname or uid}", f"approve_{uid}"),
                Button.inline(f"🚫 Ban", f"ban_{uid}")
            ])
        
        await event.edit(text, buttons=buttons, parse_mode='markdown')

# Main entry
async def main():
    bot = ReporterBot()
    await bot.start()

if __name__ == "__main__":
    asyncio.run(main())
