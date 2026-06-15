import os
import asyncio
import logging
import json
import random
import aiohttp
import socks
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient, functions, types
from telethon.errors import (
    SessionPasswordNeededError, 
    PhoneCodeInvalidError,
    FloodWaitError,
    PhoneNumberInvalidError,
    AuthKeyUnregisteredError
)
from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonViolence,
    InputReportReasonPornography,
    InputReportReasonOther,
    InputReportReasonFake,
    InputReportReasonChildAbuse,
    InputPeerChannel,
    InputPeerUser
)

# ========== CONFIGURATION ==========
BOT_TOKEN = "8909174731:AAGvm8plRxoZLnBv2R1Z7f7o45FrT3XoXo8"
API_ID = "23907288"
API_HASH = "f9a47570ed19aebf8eb0f0a5ec1111e5"
ADMIN_IDS = [8960582061] # Your Telegram ID
DATABASE_FILE = "user_data.json"
SESSIONS_FOLDER = "sessions"
PROXIES_FILE = "proxies.txt"
LOG_FILE = "reporting.log"

# Ensure folders exist
os.makedirs(SESSIONS_FOLDER, exist_ok=True)

# ========== DATABASE ==========
class Database:
    def __init__(self):
        self.data = self.load_data()
    
    def load_data(self):
        try:
            with open(DATABASE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {
                "approved_users": [],
                "subscriptions": {},
                "redeem_codes": {},
                "user_stats": {},
                "user_sessions": {},
                "proxies": [],
                "session_pools": {},
                "admin_settings": {
                    "max_reports_per_session": 0,  # 0 = unlimited
                    "default_delay": 2,
                    "max_sessions_per_user": 50,
                    "rotation_delay": 1,
                    "max_reports_per_rotation": 10
                }
            }
    
    def save_data(self):
        with open(DATABASE_FILE, 'w') as f:
            json.dump(self.data, f, indent=4)
    
    def is_approved(self, user_id):
        return str(user_id) in self.data["approved_users"]
    
    def approve_user(self, user_id):
        if str(user_id) not in self.data["approved_users"]:
            self.data["approved_users"].append(str(user_id))
            self.save_data()
    
    def add_subscription(self, user_id, days):
        expiry = datetime.now() + timedelta(days=days)
        self.data["subscriptions"][str(user_id)] = expiry.isoformat()
        self.save_data()
    
    def check_subscription(self, user_id):
        if user_id in ADMIN_IDS:
            return True
        
        sub = self.data["subscriptions"].get(str(user_id))
        if not sub:
            return False
        expiry = datetime.fromisoformat(sub)
        return datetime.now() < expiry
    
    def create_redeem_code(self, code, days):
        self.data["redeem_codes"][code] = {
            "days": days,
            "used": False,
            "created_at": datetime.now().isoformat()
        }
        self.save_data()
    
    def use_redeem_code(self, code, user_id):
        if code in self.data["redeem_codes"]:
            code_data = self.data["redeem_codes"][code]
            if not code_data["used"]:
                code_data["used"] = True
                code_data["used_by"] = user_id
                code_data["used_at"] = datetime.now().isoformat()
                self.add_subscription(user_id, code_data["days"])
                self.save_data()
                return True
        return False
    
    def add_user_session(self, user_id, session_name, phone=None):
        if str(user_id) not in self.data["user_sessions"]:
            self.data["user_sessions"][str(user_id)] = []
        
        session_data = {
            "name": session_name,
            "phone": phone,
            "created_at": datetime.now().isoformat(),
            "last_used": datetime.now().isoformat(),
            "report_count": 0,
            "status": "active",
            "rotation_index": 0
        }
        
        sessions = self.data["user_sessions"][str(user_id)]
        sessions.append(session_data)
        
        # Initialize session pool
        if str(user_id) not in self.data["session_pools"]:
            self.data["session_pools"][str(user_id)] = {
                "active_sessions": [session_name],
                "rotation_queue": deque([session_name]),
                "last_rotation": datetime.now().isoformat()
            }
        else:
            pool = self.data["session_pools"][str(user_id)]
            if session_name not in pool["active_sessions"]:
                pool["active_sessions"].append(session_name)
                pool["rotation_queue"].append(session_name)
        
        self.save_data()
    
    def get_user_sessions(self, user_id):
        return self.data["user_sessions"].get(str(user_id), [])
    
    def get_active_sessions(self, user_id):
        sessions = self.get_user_sessions(user_id)
        return [s for s in sessions if s.get("status") == "active"]
    
    def get_next_session_for_rotation(self, user_id):
        """Get next session from rotation queue"""
        if str(user_id) not in self.data["session_pools"]:
            return None
        
        pool = self.data["session_pools"][str(user_id)]
        if not pool["rotation_queue"]:
            # Refill queue with active sessions
            active_sessions = self.get_active_sessions(user_id)
            if not active_sessions:
                return None
            
            session_names = [s["name"] for s in active_sessions]
            pool["rotation_queue"] = deque(session_names)
        
        # Rotate: take from front, put to back
        session_name = pool["rotation_queue"][0]
        pool["rotation_queue"].rotate(-1)
        
        # Update last rotation time
        pool["last_rotation"] = datetime.now().isoformat()
        
        self.save_data()
        return session_name
    
    def get_random_session(self, user_id):
        """Get random active session"""
        active_sessions = self.get_active_sessions(user_id)
        if not active_sessions:
            return None
        return random.choice(active_sessions)["name"]
    
    def update_session_stats(self, user_id, session_name, reports_made):
        sessions = self.data["user_sessions"].get(str(user_id), [])
        for session in sessions:
            if session["name"] == session_name:
                session["report_count"] += reports_made
                session["last_used"] = datetime.now().isoformat()
                break
        self.save_data()
    
    def update_user_stats(self, user_id, reports_made):
        stats = self.data["user_stats"].get(str(user_id), {
            "total_reports": 0, 
            "sessions_used": 0,
            "rotation_count": 0
        })
        stats["total_reports"] += reports_made
        stats["rotation_count"] = stats.get("rotation_count", 0) + 1
        self.data["user_stats"][str(user_id)] = stats
        self.save_data()
    
    def disable_session(self, user_id, session_name):
        sessions = self.data["user_sessions"].get(str(user_id), [])
        for session in sessions:
            if session["name"] == session_name:
                session["status"] = "disabled"
                break
        
        # Remove from rotation pool
        if str(user_id) in self.data["session_pools"]:
            pool = self.data["session_pools"][str(user_id)]
            if session_name in pool["active_sessions"]:
                pool["active_sessions"].remove(session_name)
            
            # Remove from rotation queue
            if session_name in pool["rotation_queue"]:
                pool["rotation_queue"] = deque([s for s in pool["rotation_queue"] if s != session_name])
        
        self.save_data()
    
    def add_proxy(self, proxy_str):
        if proxy_str not in self.data["proxies"]:
            self.data["proxies"].append(proxy_str)
            self.save_proxies_to_file()
    
    def get_random_proxy(self):
        if self.data["proxies"]:
            return random.choice(self.data["proxies"])
        return None
    
    def save_proxies_to_file(self):
        with open(PROXIES_FILE, 'w') as f:
            for proxy in self.data["proxies"]:
                f.write(proxy + '\n')
    
    def load_proxies_from_file(self):
        try:
            with open(PROXIES_FILE, 'r') as f:
                self.data["proxies"] = [line.strip() for line in f.readlines() if line.strip()]
                self.save_data()
        except:
            pass

db = Database()
db.load_proxies_from_file()

# ========== SESSION ROTATION MANAGER ==========
class SessionRotationManager:
    def __init__(self):
        self.session_manager = SessionManager()
        self.active_rotations = {}
    
    async def get_rotating_session(self, user_id: int) -> Tuple[Optional[TelegramClient], str]:
        """Get next session from rotation pool"""
        session_name = db.get_next_session_for_rotation(user_id)
        if not session_name:
            return None, "No active sessions available"
        
        client = await self.session_manager.get_client(session_name)
        if not client:
            # Session might be dead, disable it
            db.disable_session(user_id, session_name)
            # Try next session
            return await self.get_rotating_session(user_id)
        
        return client, session_name
    
    async def get_random_session_client(self, user_id: int) -> Tuple[Optional[TelegramClient], str]:
        """Get random session"""
        session_name = db.get_random_session(user_id)
        if not session_name:
            return None, "No active sessions available"
        
        client = await self.session_manager.get_client(session_name)
        if not client:
            db.disable_session(user_id, session_name)
            return await self.get_random_session_client(user_id)
        
        return client, session_name
    
    async def rotate_and_report(self, user_id: int, target: str, report_count: int, 
                               delay: float = 2.0, rotation_delay: float = 1.0,
                               max_per_session: int = 10) -> Dict:
        """Rotate through sessions for reporting"""
        
        results = {
            "total_success": 0,
            "total_failed": 0,
            "sessions_used": [],
            "errors": [],
            "start_time": datetime.now().isoformat()
        }
        
        reports_made = 0
        session_reports = {}
        
        while reports_made < report_count:
            # Get next session from rotation
            client, session_name = await self.get_rotating_session(user_id)
            if not client:
                results["errors"].append("No more active sessions available")
                break
            
            # Calculate how many reports to make with this session
            remaining = report_count - reports_made
            session_limit = min(max_per_session, remaining)
            
            if session_name not in session_reports:
                session_reports[session_name] = 0
            
            try:
                # Make reports with this session
                session_results = await self.make_reports_with_session(
                    client, session_name, target, session_limit, delay
                )
                
                reports_made += session_results["success"]
                session_reports[session_name] += session_results["success"]
                
                results["total_success"] += session_results["success"]
                results["total_failed"] += session_results.get("failed", 0)
                
                if session_name not in results["sessions_used"]:
                    results["sessions_used"].append(session_name)
                
                if session_results.get("errors"):
                    results["errors"].extend(session_results["errors"])
                
                # Update session stats
                db.update_session_stats(user_id, session_name, session_results["success"])
                
                # Log rotation
                logging.info(f"Session {session_name} made {session_results['success']} reports. Total: {reports_made}/{report_count}")
                
                # Rotation delay between sessions
                if reports_made < report_count:
                    await asyncio.sleep(rotation_delay)
                    
            except Exception as e:
                error_msg = f"Session {session_name} error: {str(e)}"
                results["errors"].append(error_msg)
                logging.error(error_msg)
                
                # Mark session as potentially dead
                db.disable_session(user_id, session_name)
        
        results["end_time"] = datetime.now().isoformat()
        results["session_reports"] = session_reports
        results["total_reports_made"] = reports_made
        
        # Update user stats
        db.update_user_stats(user_id, reports_made)
        
        return results
    
    async def make_reports_with_session(self, client: TelegramClient, session_name: str, 
                                       target: str, count: int, delay: float) -> Dict:
        """Make reports using a specific session"""
        
        # Generate realistic report messages
        report_messages = [
            "This account is sending spam messages repeatedly.",
            "User is sharing inappropriate adult content.",
            "This profile is impersonating someone else.",
            "Account is posting violent content.",
            "Sharing copyrighted material illegally.",
            "Engaging in harassment behavior.",
            "Suspected bot account spreading malware.",
            "Deliberately spreading fake news.",
            "Account involved in financial scams.",
            "Posting dangerous content."
        ]
        
        results = {
            "success": 0,
            "failed": 0,
            "errors": []
        }
        
        try:
            entity = await client.get_entity(target)
            
            # For groups/channels, get participants
            participants = []
            if hasattr(entity, 'participants_count'):
                async for user in client.iter_participants(entity, limit=count):
                    participants.append(user)
            else:
                participants = [entity] * count  # For single user
            
            for i, participant in enumerate(participants[:count]):
                try:
                    report_msg = random.choice(report_messages)
                    
                    await client(ReportPeerRequest(
                        peer=participant,
                        reason=InputReportReasonSpam(),
                        message=report_msg
                    ))
                    
                    results["success"] += 1
                    
                    # Variable delay to avoid detection
                    actual_delay = delay + random.uniform(-0.3, 0.3)
                    await asyncio.sleep(max(0.5, actual_delay))
                    
                except FloodWaitError as e:
                    wait_time = e.seconds
                    results["errors"].append(f"Flood wait {session_name}: {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue
                except Exception as e:
                    results["failed"] += 1
                    results["errors"].append(f"Report error {session_name}: {str(e)}")
                    continue
            
        except Exception as e:
            results["errors"].append(f"Session {session_name} setup error: {str(e)}")
        
        return results

# ========== SESSION MANAGER ==========
class SessionManager:
    def __init__(self):
        self.active_clients = {}
    
    async def create_client(self, session_name, use_proxy=False):
        session_path = os.path.join(SESSIONS_FOLDER, f"{session_name}.session")
        
        proxy = None
        if use_proxy:
            proxy_str = db.get_random_proxy()
            if proxy_str:
                try:
                    parts = proxy_str.split(':')
                    if len(parts) >= 2:
                        proxy_ip = parts[0]
                        proxy_port = int(parts[1])
                        proxy_username = parts[2] if len(parts) > 2 else None
                        proxy_password = parts[3] if len(parts) > 3 else None
                        
                        proxy = (
                            socks.SOCKS5,
                            proxy_ip,
                            proxy_port,
                            True,
                            proxy_username,
                            proxy_password
                        )
                except:
                    proxy = None
        
        client = TelegramClient(
            session_path,
            API_ID,
            API_HASH,
            proxy=proxy,
            connection=ConnectionTcpMTProxyRandomizedIntermediate if proxy else None
        )
        
        try:
            await client.connect()
            return client
        except Exception as e:
            logging.error(f"Failed to create client {session_name}: {e}")
            return None
    
    async def get_client(self, session_name):
        if session_name in self.active_clients:
            return self.active_clients[session_name]
        
        session_path = os.path.join(SESSIONS_FOLDER, f"{session_name}.session")
        if os.path.exists(session_path):
            client = await self.create_client(session_name)
            if client and await client.is_user_authorized():
                self.active_clients[session_name] = client
                return client
        
        return None

# Initialize managers
session_manager = SessionManager()
rotation_manager = SessionRotationManager()

# ========== BOT HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not db.is_approved(user_id) and user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Access denied. Contact admin.")
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Add Session", callback_data='add_session')],
        [InlineKeyboardButton("📊 Session Pool", callback_data='session_pool')],
        [InlineKeyboardButton("🚀 Rotating Report", callback_data='rotating_report')],
        [InlineKeyboardButton("⚡ Quick Report", callback_data='quick_report')],
        [InlineKeyboardButton("📈 Stats", callback_data='stats')],
        [InlineKeyboardButton("⚙️ Settings", callback_data='settings')]
    ])
    
    await update.message.reply_text(
        "🔄 *Session Rotation Bot*\n\n"
        "• Automatic session rotation\n"
        "• Unlimited reporting\n"
        "• SOCKS5 proxy support\n"
        "• Session pool management\n\n"
        "Select an option:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == 'rotating_report':
        if not db.check_subscription(user_id):
            await query.edit_message_text("❌ Subscription required.")
            return
        
        active_sessions = db.get_active_sessions(user_id)
        if not active_sessions:
            await query.edit_message_text("❌ No active sessions. Add sessions first.")
            return
        
        await query.edit_message_text(
            f"🔄 *Rotating Report Mode*\n\n"
            f"Active sessions: {len(active_sessions)}\n"
            f"Sessions will rotate automatically.\n\n"
            f"Send target (user/group):",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_rotation_target'] = True
    
    elif query.data == 'session_pool':
        active_sessions = db.get_active_sessions(user_id)
        
        if not active_sessions:
            await query.edit_message_text("❌ No sessions in pool.")
            return
        
        # Create session list
        session_text = "📊 *Session Pool*\n\n"
        for i, session in enumerate(active_sessions, 1):
            session_text += f"{i}. `{session['name']}`\n"
            session_text += f"   📱 {session.get('phone', 'N/A')}\n"
            session_text += f"   📊 Reports: {session['report_count']}\n"
            session_text += f"   ⏰ Last used: {session['last_used'][:16]}\n\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data='session_pool')],
            [InlineKeyboardButton("🔧 Manage", callback_data='manage_sessions')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_main')]
        ])
        
        await query.edit_message_text(session_text, reply_markup=keyboard, parse_mode='Markdown')
    
    elif query.data == 'quick_report':
        await query.edit_message_text(
            "⚡ *Quick Report*\n\n"
            "Send target and count in format:\n"
            "`target count`\n\n"
            "Example:\n"
            "`@username 50`\n\n"
            "Will use random sessions from pool.",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_quick_report'] = True

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Handle rotating report target
    if context.user_data.get('awaiting_rotation_target'):
        target = text.strip()
        context.user_data['rotation_target'] = target
        
        await update.message.reply_text(
            f"🎯 Target set: `{target}`\n\n"
            f"How many total reports?\n"
            f"(Will rotate through all sessions)",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_rotation_count'] = True
        context.user_data['awaiting_rotation_target'] = False
    
    # Handle rotation count
    elif context.user_data.get('awaiting_rotation_count'):
        try:
            count = int(text.strip())
            if count <= 0:
                await update.message.reply_text("❌ Count must be positive.")
                return
            
            target = context.user_data['rotation_target']
            active_sessions = len(db.get_active_sessions(user_id))
            
            await update.message.reply_text(
                f"⚙️ *Configuring Rotation*\n\n"
                f"Target: `{target}`\n"
                f"Total reports: `{count}`\n"
                f"Active sessions: `{active_sessions}`\n\n"
                f"Send delay between reports (seconds):\n"
                f"Recommended: 1-3",
                parse_mode='Markdown'
            )
            context.user_data['rotation_count'] = count
            context.user_data['awaiting_rotation_delay'] = True
            context.user_data['awaiting_rotation_count'] = False
            
        except ValueError:
            await update.message.reply_text("❌ Invalid number.")
    
    # Handle rotation delay
    elif context.user_data.get('awaiting_rotation_delay'):
        try:
            delay = float(text.strip())
            if delay < 0.5 or delay > 10:
                await update.message.reply_text("❌ Delay must be 0.5-10 seconds.")
                return
            
            target = context.user_data['rotation_target']
            count = context.user_data['rotation_count']
            
            # Calculate reports per session
            active_sessions = db.get_active_sessions(user_id)
            sessions_count = len(active_sessions)
            
            if sessions_count == 0:
                await update.message.reply_text("❌ No active sessions.")
                context.user_data.clear()
                return
            
            reports_per_session = max(1, count // sessions_count)
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Start Rotation", callback_data='start_rotation')],
                [InlineKeyboardButton("✏️ Edit Settings", callback_data='edit_rotation')],
                [InlineKeyboardButton("❌ Cancel", callback_data='cancel_rotation')]
            ])
            
            await update.message.reply_text(
                f"🔄 *Rotation Ready*\n\n"
                f"• Target: `{target}`\n"
                f"• Total reports: `{count}`\n"
                f"• Active sessions: `{sessions_count}`\n"
                f"• Reports per session: `{reports_per_session}`\n"
                f"• Delay: `{delay}` seconds\n"
                f"• Rotation delay: `1` second\n\n"
                f"Click ✅ to start:",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            
            context.user_data['rotation_delay'] = delay
            context.user_data['awaiting_rotation_delay'] = False
            
        except ValueError:
            await update.message.reply_text("❌ Invalid delay.")
    
    # Handle quick report
    elif context.user_data.get('awaiting_quick_report'):
        try:
            parts = text.strip().split()
            if len(parts) < 2:
                await update.message.reply_text("❌ Format: target count")
                return
            
            target = parts[0]
            count = int(parts[1])
            
            if count <= 0:
                await update.message.reply_text("❌ Count must be positive.")
                return
            
            active_sessions = db.get_active_sessions(user_id)
            if not active_sessions:
                await update.message.reply_text("❌ No active sessions.")
                return
            
            # Start quick report
            await update.message.reply_text(
                f"⚡ *Starting Quick Report*\n\n"
                f"Target: `{target}`\n"
                f"Count: `{count}`\n"
                f"Using random sessions...",
                parse_mode='Markdown'
            )
            
            # Use rotation manager
            results = await rotation_manager.rotate_and_report(
                user_id=user_id,
                target=target,
                report_count=count,
                delay=2.0,
                rotation_delay=1.0,
                max_per_session=5
            )
            
            # Send results
            result_text = f"✅ *Quick Report Complete*\n\n"
            result_text += f"Success: `{results['total_success']}`\n"
            result_text += f"Failed: `{results['total_failed']}`\n"
            result_text += f"Sessions used: `{len(results['sessions_used'])}`\n"
            result_text += f"Time: `{(datetime.fromisoformat(results['end_time']) - datetime.fromisoformat(results['start_time'])).seconds}`s\n\n"
            
            if results['session_reports']:
                result_text += "📊 Per session:\n"
                for session, reports in results['session_reports'].items():
                    result_text += f"• {session}: {reports} reports\n"
            
            await update.message.reply_text(result_text, parse_mode='Markdown')
            
            context.user_data.clear()
            
        except ValueError:
            await update.message.reply_text("❌ Invalid format.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")

# Handle callback for starting rotation
async def start_rotation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == 'start_rotation':
        # Get rotation parameters
        target = context.user_data.get('rotation_target')
        count = context.user_data.get('rotation_count')
        delay = context.user_data.get('rotation_delay', 2.0)
        
        if not all([target, count]):
            await query.edit_message_text("❌ Missing parameters.")
            return
        
        await query.edit_message_text(
            f"🚀 *Starting Rotation*\n\n"
            f"Rotating through all sessions...\n"
            f"This may take a while.",
            parse_mode='Markdown'
        )
        
        # Start rotation
        try:
            results = await rotation_manager.rotate_and_report(
                user_id=user_id,
                target=target,
                report_count=count,
                delay=delay,
                rotation_delay=1.0,
                max_per_session=10
            )
            
            # Format results
            result_text = f"🎯 *Rotation Complete*\n\n"
            result_text += f"✅ Success: `{results['total_success']}/{count}`\n"
            result_text += f"❌ Failed: `{results['total_failed']}`\n"
            result_text += f"🔄 Sessions used: `{len(results['sessions_used'])}`\n"
            result_text += f"⏱️ Duration: `{(datetime.fromisoformat(results['end_time']) - datetime.fromisoformat(results['start_time'])).seconds}` seconds\n\n"
            
            if results['session_reports']:
                result_text += "📊 Session performance:\n"
                for session, reports in results['session_reports'].items():
                    result_text += f"• `{session[:15]}...`: {reports} reports\n"
            
            if results.get('errors'):
                result_text += f"\n⚠️ Errors: {len(results['errors'])}\n"
            
            await query.message.reply_text(result_text, parse_mode='Markdown')
            
        except Exception as e:
            await query.message.reply_text(f"❌ Rotation error: {str(e)}")
        
        context.user_data.clear()

# ========== MAIN ==========
def main():
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ]
    )
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CallbackQueryHandler(start_rotation_callback, pattern='^(start_rotation|edit_rotation|cancel_rotation)$'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("🔄 Session Rotation Bot Started!")
    print(f"📊 Total approved users: {len(db.data['approved_users'])}")
    print(f"📱 Total sessions in database: {sum(len(sessions) for sessions in db.data['user_sessions'].values())}")
    
    app.run_polling()

if __name__ == "__main__":
    main()
