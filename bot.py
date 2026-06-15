import asyncio
import random
import string
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.types import (
    InputPeerUser,
    InputPeerChannel,
    InputPeerChat,
    
    InputReportReasonSpam,
    InputReportReasonViolence,
    InputReportReasonPornography,
    InputReportReasonChildAbuse,
    InputReportReasonOther
)
import socks

# ========= CONFIGURATION =========
BOT_TOKEN = "8909174731:AAGvm8plRxoZLnBv2R1Z7f7o45FrT3XoXo8"
API_ID = "23907288"
API_HASH = "f9a47570ed19aebf8eb0f0a5ec1111e5"
ADMIN_IDS = [8960582061]  # Admin user IDs
DATABASE_NAME = "reporter_bot.db"
PROXY_TYPE = socks.SOCKS5  # Change to socks.HTTP if needed
# =================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database setup
conn = sqlite3.connect(DATABASE_NAME, check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    is_approved INTEGER DEFAULT 0,
    subscription_ends TEXT,
    reports_today INTEGER DEFAULT 0,
    daily_limit INTEGER DEFAULT 50
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS sessions (
    session_string TEXT PRIMARY KEY,
    is_active INTEGER DEFAULT 1,
    proxy TEXT,
    last_used TEXT
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS redeem_codes (
    code TEXT PRIMARY KEY,
    days INTEGER,
    uses_left INTEGER,
    created_by INTEGER
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS report_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    target TEXT,
    reason TEXT,
    session_used TEXT,
    timestamp TEXT
)''')
conn.commit()

class MassReporterBot:
    def __init__(self):
        self.bot = TelegramClient(
            'bot_session',
            API_ID,
            API_HASH
        ).start(bot_token=BOT_TOKEN)

        self.active_sessions = []
        self.report_queue = asyncio.Queue()

        self.setup_handlers()

    async def run(self):
        asyncio.create_task(self.session_manager())
        asyncio.create_task(self.report_worker())

        # Bot ko alive rakho
        await self.bot.run_until_disconnected()
    def setup_handlers(self):
        @self.bot.on(events.NewMessage(pattern='/start'))
        async def start(event):
            user_id = event.sender_id
            cursor.execute('SELECT is_approved FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            if not user:
                cursor.execute('INSERT INTO users (user_id, is_approved) VALUES (?, ?)', (user_id, 0))
                conn.commit()
            
            if user_id in ADMIN_IDS or (user and user[0] == 1):
                buttons = [
                    [Button.inline('📡 Start Reporting', b'start_report')],
                    [Button.inline('🔐 Add Session', b'add_session'), Button.inline('📋 My Sessions', b'list_sessions')],
                    [Button.inline('🎫 Redeem Code', b'redeem'), Button.inline('📊 Stats', b'stats')],
                    [Button.inline('⚙️ Settings', b'settings')]
                ]
                if user_id in ADMIN_IDS:
                    buttons.append([Button.inline('👑 Admin Panel', b'admin_panel')])
                await event.respond(
                    '**🚀 DarkGPT Mass Reporter Bot**\n\n'
                    'Welcome to the ultimate reporting tool! Use the buttons below to control the bot.',
                    buttons=buttons
                )
            else:
                await event.respond('⛔ You are not approved to use this bot. Contact an admin.')

        @self.bot.on(events.CallbackQuery)
        async def callback_handler(event):
            data = event.data.decode()
            user_id = event.sender_id
            
            if data == 'start_report':
                if not await self.is_approved(user_id):
                    await event.answer('You are not approved!', alert=True)
                    return
                await self.start_report_wizard(event)
            elif data == 'add_session':
                await self.add_session_wizard(event)
            elif data == 'list_sessions':
                await self.list_sessions(event)
            elif data == 'redeem':
                await self.redeem_code_wizard(event)
            elif data == 'stats':
                await self.show_stats(event)
            elif data == 'settings':
                await self.settings_menu(event)
            elif data == 'admin_panel' and user_id in ADMIN_IDS:
                await self.admin_panel(event)
            elif data.startswith('report_'):
                await self.handle_report_callback(event, data)
            elif data.startswith('delete_session_'):
                session_str = data.split('_')[2]
                await self.delete_session(event, session_str)
            elif data == 'generate_code':
                await self.generate_redeem_code(event)
            elif data.startswith('approve_'):
                target_id = int(data.split('_')[1])
                await self.approve_user(event, target_id)

    async def session_manager(self):
        """Load and maintain active sessions from the database."""
        while True:
            cursor.execute('SELECT session_string, proxy FROM sessions WHERE is_active = 1')
            rows = cursor.fetchall()
            current_sessions = [s[0] for s in self.active_sessions]
            
            for session_str, proxy in rows:
                if session_str not in current_sessions:
                    try:
                        proxy_dict = None
                        if proxy:
                            host, port = proxy.split(':')
                            proxy_dict = {
                                'proxy_type': PROXY_TYPE,
                                'addr': host,
                                'port': int(port),
                                'username': None,
                                'password': None
                            }
                        client = TelegramClient(StringSession(session_str), API_ID, API_HASH, proxy=proxy_dict)
                        await client.connect()
                        if await client.is_user_authorized():
                            self.active_sessions.append((client, session_str))
                            logger.info(f'Session loaded: {session_str[:10]}...')
                        else:
                            cursor.execute('UPDATE sessions SET is_active = 0 WHERE session_string = ?', (session_str,))
                            conn.commit()
                    except Exception as e:
                        logger.error(f'Failed to load session: {e}')
                        cursor.execute('UPDATE sessions SET is_active = 0 WHERE session_string = ?', (session_str,))
                        conn.commit()
            
            await asyncio.sleep(30)

    async def report_worker(self):
        """Process reports from the queue using random sessions."""
        while True:
            report_data = await self.report_queue.get()
            user_id, target, reason, report_count, delay = report_data
            
            if not self.active_sessions:
                await self.bot.send_message(user_id, '❌ No active sessions available for reporting.')
                return
            
            for i in range(report_count):
                client, session_str = random.choice(self.active_sessions)
                try:
                    # Resolve target entity
                    entity = await client.get_input_entity(target)
                    reason_obj = self.get_report_reason(reason)
                    
                    # Execute report
                    await client(ReportPeerRequest(
                        peer=entity,
                        reason=reason_obj,
                        message='Violation of Telegram Terms of Service' if isinstance(reason_obj, InputReportReasonOther) else ''
                    ))
                    
                    # Log report
                    cursor.execute('INSERT INTO report_logs (user_id, target, reason, session_used, timestamp) VALUES (?, ?, ?, ?, ?)',
                                  (user_id, target, reason, session_str[:20], datetime.now().isoformat()))
                    cursor.execute('UPDATE users SET reports_today = reports_today + 1 WHERE user_id = ?', (user_id,))
                    conn.commit()
                    
                    await self.bot.send_message(user_id, f'✅ Report #{i+1} sent to {target} using session {session_str[:15]}...')
                except Exception as e:
                    await self.bot.send_message(user_id, f'❌ Report #{i+1} failed: {str(e)[:100]}')
                
                await asyncio.sleep(delay)
            
            await self.bot.send_message(user_id, f'🎉 Completed {report_count} reports to {target}')

    def get_report_reason(self, reason_str: str):
        reason_map = {
            'spam': InputReportReasonSpam(),
            'violence': InputReportReasonViolence(),
            'porn': InputReportReasonPornography(),
            'child abuse': InputReportReasonChildAbuse(),
            'other': InputReportReasonOther()
        }
        return reason_map.get(reason_str, InputReportReasonSpam())

    async def start_report_wizard(self, event):
        """Interactive report setup."""
        await event.edit(
            '**🔫 Mass Report Configuration**\n\n'
            'Please provide the following details:\n\n'
            '1. Target (username/user ID/chat link):',
            buttons=Button.inline('Cancel', b'cancel')
        )
        
        try:
            target_msg = await self.wait_for_response(event.sender_id)
            if target_msg.lower() == 'cancel':
                await event.respond('Cancelled.')
                return
            
            await event.respond(
                '2. Select report reason:',
                buttons=[
                    [Button.inline('Spam', b'reason_spam'), Button.inline('Violence', b'reason_violence')],
                    [Button.inline('Pornography', b'reason_porn'), Button.inline('Child Abuse', b'reason_child')],
                    [Button.inline('Other', b'reason_other'), Button.inline('Cancel', b'cancel')]
                ]
            )
            
            reason_event = await self.wait_for_callback(event.sender_id)
            reason = reason_event.data.decode().replace('reason_', '')
            if reason == 'cancel':
                await event.respond('Cancelled.')
                return
            
            await event.respond('3. Number of reports to send (max 100):')
            count_msg = await self.wait_for_response(event.sender_id)
            try:
                count = min(int(count_msg), 100)
            except:
                count = 10
            
            await event.respond('4. Delay between reports in seconds:')
            delay_msg = await self.wait_for_response(event.sender_id)
            try:
                delay = max(float(delay_msg), 0.5)
            except:
                delay = 2.0
            
            # Check daily limit
            cursor.execute('SELECT reports_today, daily_limit FROM users WHERE user_id = ?', (event.sender_id,))
            user_data = cursor.fetchone()
            if user_data and user_data[0] + count > user_data[1]:
                await event.respond(f'❌ Daily limit exceeded! You have {user_data[1] - user_data[0]} reports left today.')
                return
            
            # Confirm and start
            confirm_msg = (
                f'**⚠️ Confirm Report Request**\n\n'
                f'• Target: `{target_msg}`\n'
                f'• Reason: `{reason}`\n'
                f'• Reports: `{count}`\n'
                f'• Delay: `{delay}s`\n'
                f'• Sessions available: `{len(self.active_sessions)}`\n\n'
                f'Proceed?'
            )
            await event.respond(
                confirm_msg,
                buttons=[
                    [Button.inline('🚀 Yes, Start Reporting!', b'confirm_report'),
                     Button.inline('❌ Cancel', b'cancel')]
                ]
            )
            
            confirm_event = await self.wait_for_callback(event.sender_id)
            if confirm_event.data.decode() == 'confirm_report':
                await self.report_queue.put((event.sender_id, target_msg, reason, count, delay))
                await event.respond('📤 Report task queued! Processing will start shortly...')
            else:
                await event.respond('Cancelled.')
                
        except asyncio.TimeoutError:
            await event.respond('⏱️ Request timed out.')

    async def add_session_wizard(self, event):
        """Add new session via login or string."""
        await event.edit(
            '**🔐 Add New Session**\n\n'
            'Choose method:',
            buttons=[
                [Button.inline('📱 Login with Phone', b'login_phone'),
                 Button.inline('📋 Enter Session String', b'input_string')],
                [Button.inline('🔙 Back', b'start_report')]
            ]
        )
        
        method_event = await self.wait_for_callback(event.sender_id)
        method = method_event.data.decode()
        
        if method == 'login_phone':
            await event.respond('Please send your phone number (with country code, e.g., +12345678900):')
            phone_msg = await self.wait_for_response(event.sender_id)
            
            try:
                client = TelegramClient(StringSession(), API_ID, API_HASH)
                await client.connect()
                await client.send_code_request(phone_msg)
                
                await event.respond('Enter the code you received:')
                code_msg = await self.wait_for_response(event.sender_id)
                
                await client.sign_in(phone_msg, code_msg)
                session_str = StringSession.save(client.session)
                
                # Save session
                cursor.execute('INSERT OR REPLACE INTO sessions (session_string, is_active) VALUES (?, ?)',
                             (session_str, 1))
                conn.commit()
                
                await event.respond(f'✅ Session added successfully!\n\n`{session_str}`')
                await client.disconnect()
            except Exception as e:
                await event.respond(f'❌ Failed: {str(e)}')
                
        elif method == 'input_string':
            await event.respond('Paste your session string:')
            session_str = await self.wait_for_response(event.sender_id)
            
            try:
                cursor.execute('INSERT OR REPLACE INTO sessions (session_string, is_active) VALUES (?, ?)',
                             (session_str, 1))
                conn.commit()
                await event.respond('✅ Session string saved!')
            except Exception as e:
                await event.respond(f'❌ Error: {str(e)}')

    async def list_sessions(self, event):
        """Display all active sessions."""
        cursor.execute('SELECT session_string, is_active FROM sessions')
        sessions = cursor.fetchall()
        
        if not sessions:
            await event.edit('No sessions found.', buttons=Button.inline('🔙 Back', b'start_report'))
            return
        
        text = '**📋 Active Sessions**\n\n'
        for idx, (session_str, active) in enumerate(sessions, 1):
            status = '✅' if active else '❌'
            text += f'{idx}. `{session_str[:30]}...` {status}\n'
            if active:
                text += f'   [Delete]({session_str})\n'
        
        buttons = []
        for session_str, active in sessions:
            if active:
                buttons.append([Button.inline(f'🗑️ Delete {session_str[:15]}...', f'delete_session_{session_str}')])
        buttons.append([Button.inline('🔙 Back', b'start_report')])
        
        await event.edit(text, buttons=buttons)

    async def delete_session(self, event, session_str):
        """Remove a session."""
        cursor.execute('DELETE FROM sessions WHERE session_string = ?', (session_str,))
        conn.commit()
        await event.answer('Session deleted!', alert=True)
        await self.list_sessions(event)

    async def redeem_code_wizard(self, event):
        """Redeem subscription code."""
        await event.edit('Enter redeem code:')
        code = await self.wait_for_response(event.sender_id)
        
        cursor.execute('SELECT days, uses_left FROM redeem_codes WHERE code = ?', (code,))
        code_data = cursor.fetchone()
        
        if not code_data or code_data[1] <= 0:
            await event.respond('❌ Invalid or expired code.')
            return
        
        days, uses_left = code_data
        new_end_date = datetime.now() + timedelta(days=days)
        
        cursor.execute('UPDATE users SET subscription_ends = ? WHERE user_id = ?',
                      (new_end_date.isoformat(), event.sender_id))
        cursor.execute('UPDATE redeem_codes SET uses_left = uses_left - 1 WHERE code = ?', (code,))
        conn.commit()
        
        await event.respond(f'✅ Code redeemed! Subscription extended by {days} days.')

    async def show_stats(self, event):
        """Display user statistics."""
        user_id = event.sender_id
        cursor.execute('SELECT reports_today, daily_limit, subscription_ends FROM users WHERE user_id = ?', (user_id,))
        user_data = cursor.fetchone()
        cursor.execute('SELECT COUNT(*) FROM sessions WHERE is_active = 1')
        active_sessions = cursor.fetchone()[0]
        
        if not user_data:
            await event.respond('User not found.')
            return
        
        reports_today, daily_limit, sub_ends = user_data
        sub_status = 'Active' if (sub_ends and datetime.fromisoformat(sub_ends) > datetime.now()) else 'Expired'
        
        stats_msg = (
            f'**📊 Your Statistics**\n\n'
            f'• Reports today: {reports_today}/{daily_limit}\n'
            f'• Active sessions: {active_sessions}\n'
            f'• Subscription: {sub_status}\n'
            f'• Queue size: {self.report_queue.qsize()}\n'
        )
        await event.edit(stats_msg, buttons=Button.inline('🔙 Back', b'start_report'))

    async def settings_menu(self, event):
        """User settings."""
        buttons = [
            [Button.inline('📈 Increase Daily Limit', b'inc_limit')],
            [Button.inline('🔧 Set Proxy for Sessions', b'set_proxy')],
            [Button.inline('🔙 Back', b'start_report')]
        ]
        await event.edit('**⚙️ Settings**\n\nConfigure your bot experience:', buttons=buttons)

    async def admin_panel(self, event):
        """Admin control panel."""
        buttons = [
            [Button.inline('👥 Manage Users', b'manage_users')],
            [Button.inline('🎫 Generate Redeem Code', b'generate_code')],
            [Button.inline('📊 Bot Statistics', b'bot_stats')],
            [Button.inline('🔙 Back', b'start_report')]
        ]
        await event.edit('**👑 Admin Panel**\n\nSelect an option:', buttons=buttons)

    async def generate_redeem_code(self, event):
        """Generate new redeem code (admin only)."""
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
        await event.respond('Enter number of days for this code:')
        days_msg = await self.wait_for_response(event.sender_id)
        
        try:
            days = int(days_msg)
            cursor.execute('INSERT INTO redeem_codes (code, days, uses_left, created_by) VALUES (?, ?, ?, ?)',
                          (code, days, 10, event.sender_id))
            conn.commit()
            await event.respond(f'✅ Code generated: `{code}`\nValid for {days} days, 10 uses.')
        except Exception as e:
            await event.respond(f'❌ Error: {str(e)}')

    async def approve_user(self, event, target_id):
        """Approve a user (admin only)."""
        cursor.execute('UPDATE users SET is_approved = 1 WHERE user_id = ?', (target_id,))
        conn.commit()
        await event.answer(f'User {target_id} approved!', alert=True)

    async def is_approved(self, user_id):
        """Check if user is approved."""
        cursor.execute('SELECT is_approved, subscription_ends FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        if not user:
            return False
        is_approved, sub_ends = user
        if sub_ends and datetime.fromisoformat(sub_ends) < datetime.now():
            cursor.execute('UPDATE users SET is_approved = 0 WHERE user_id = ?', (user_id,))
            conn.commit()
            return False
        return is_approved == 1

    async def wait_for_response(self, user_id, timeout=60):
        """Wait for a text response from user."""
        future = self.bot.loop.create_future()
        
        @self.bot.on(events.NewMessage(from_users=user_id))
        async def handler(msg_event):
            if not msg_event.message.message.startswith('/'):
                future.set_result(msg_event.message.message)
                msg_event.client.remove_event_handler(handler)
        
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise
        finally:
            # Cleanup
            try:
                self.bot.remove_event_handler(handler)
            except:
                pass

    async def wait_for_callback(self, user_id, timeout=60):
        """Wait for a callback from user."""
        future = self.bot.loop.create_future()
        
        @self.bot.on(events.CallbackQuery(from_users=user_id))
        async def handler(callback_event):
            future.set_result(callback_event)
            callback_event.client.remove_event_handler(handler)
        
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise
        finally:
            try:
                self.bot.remove_event_handler(handler)
            except:
                pass

    async def run(self):
        """Start the bot."""
        await self.bot.start()
        logger.info('Bot started!')
        await self.bot.run_until_disconnected()

if __name__ == '__main__':
    bot = MassReporterBot()
    asyncio.run(bot.run())
