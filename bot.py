import os
import asyncio
import logging
import json
import random
from datetime import datetime, timedelta
from typing import Dict, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonViolence,
    InputReportReasonPornography,
    InputReportReasonOther,
    InputReportReasonFake,
    InputReportReasonChildAbuse
)

# ========== CONFIGURATION ==========
BOT_TOKEN = "8909174731:AAGvm8plRxoZLnBv2R1Z7f7o45FrT3XoXo8"
API_ID = "23907288"
API_HASH = "f9a47570ed19aebf8eb0f0a5ec1111e5"
ADMIN_IDS = [8960582061]  # Replace with your Telegram ID
DATABASE_FILE = "user_data.json"

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
                "user_stats": {}
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
        sub = self.data["subscriptions"].get(str(user_id))
        if not sub:
            return False
        expiry = datetime.fromisoformat(sub)
        return datetime.now() < expiry
    
    def create_redeem_code(self, code, days):
        self.data["redeem_codes"][code] = {
            "days": days,
            "used": False,
            "used_by": None
        }
        self.save_data()
    
    def use_redeem_code(self, code, user_id):
        if code in self.data["redeem_codes"]:
            code_data = self.data["redeem_codes"][code]
            if not code_data["used"]:
                code_data["used"] = True
                code_data["used_by"] = user_id
                self.add_subscription(user_id, code_data["days"])
                self.save_data()
                return True
        return False
    
    def update_stats(self, user_id, reports_made):
        stats = self.data["user_stats"].get(str(user_id), {"total_reports": 0})
        stats["total_reports"] += reports_made
        self.data["user_stats"][str(user_id)] = stats
        self.save_data()

db = Database()

# ========== COLORED BUTTONS ==========
def create_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔐 Login", callback_data='login'),
            InlineKeyboardButton("📊 Reports", callback_data='reports')
        ],
        [
            InlineKeyboardButton("💎 Subscription", callback_data='subscription'),
            InlineKeyboardButton("🎫 Redeem Code", callback_data='redeem')
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data='settings'),
            InlineKeyboardButton("📈 Stats", callback_data='stats')
        ],
        [
            InlineKeyboardButton("🆘 Help", callback_data='help'),
            InlineKeyboardButton("👑 Admin", callback_data='admin')
        ]
    ])

# ========== TELEGRAM CLIENT MANAGEMENT ==========
user_clients = {}

async def get_client(user_id: int):
    if user_id in user_clients:
        return user_clients[user_id]
    return None

# ========== GENUINE REPORT MESSAGES ==========
REPORT_MESSAGES = [
    "This user is violating community guidelines by posting spam content repeatedly.",
    "Multiple users have reported this account for sharing inappropriate adult content.",
    "This account is impersonating another user and causing confusion.",
    "Posting violent content that goes against our platform's safety policies.",
    "Sharing copyrighted material without permission.",
    "Engaging in harassment and bullying behavior towards other users.",
    "Suspected bot account spreading malicious links.",
    "Sharing fake news and misinformation deliberately.",
    "Account involved in phishing attempts and scams.",
    "Posting content that promotes self-harm or dangerous activities."
]

def get_report_message():
    return random.choice(REPORT_MESSAGES)

# ========== BOT HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_approved(user_id):
        await update.message.reply_text(
            "❌ *Access Denied*\n\n"
            "You are not approved to use this bot.\n"
            "Contact admin for approval.",
            parse_mode='Markdown'
        )
        return
    
    await update.message.reply_text(
        "🚀 *Advanced Mass Reporter Bot*\n\n"
        "• Session-based login system\n"
        "• Subscription with redeem codes\n"
        "• Configurable report settings\n"
        "• Admin approval required\n\n"
        "⚠️ *Use responsibly*",
        reply_markup=create_keyboard(),
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS and not db.is_approved(user_id):
        await query.edit_message_text("❌ You are not approved to use this bot.")
        return
    
    if query.data == 'login':
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 Phone Login", callback_data='login_phone')],
            [InlineKeyboardButton("🔑 Session String", callback_data='login_session')],
            [InlineKeyboardButton("🔙 Back", callback_data='back')]
        ])
        await query.edit_message_text(
            "🔐 *Login Options*\n\n"
            "Choose your login method:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif query.data == 'reports':
        if not db.check_subscription(user_id):
            await query.edit_message_text(
                "❌ *Subscription Required*\n\n"
                "You need an active subscription to use reporting features.\n"
                "Go to 💎 Subscription menu.",
                parse_mode='Markdown'
            )
            return
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Mass Report Group", callback_data='mass_group')],
            [InlineKeyboardButton("👤 Report User", callback_data='report_user')],
            [InlineKeyboardButton("🔄 Auto Reporter", callback_data='auto_report')],
            [InlineKeyboardButton("🔙 Back", callback_data='back')]
        ])
        await query.edit_message_text(
            "📊 *Reporting System*\n\n"
            "Select reporting method:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif query.data == 'subscription':
        sub_status = "✅ Active" if db.check_subscription(user_id) else "❌ Expired"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Check Status", callback_data='check_sub')],
            [InlineKeyboardButton("🎫 Enter Code", callback_data='redeem')],
            [InlineKeyboardButton("🔙 Back", callback_data='back')]
        ])
        await query.edit_message_text(
            f"💎 *Subscription Status*\n\n"
            f"Status: {sub_status}\n"
            f"User ID: `{user_id}`\n\n"
            f"Get redeem codes from admin.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif query.data == 'redeem':
        await query.edit_message_text(
            "🎫 *Redeem Code*\n\n"
            "Send your redeem code:\n"
            "Format: `CODE12345`",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_redeem'] = True
    
    elif query.data == 'settings':
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Report Delay", callback_data='set_delay')],
            [InlineKeyboardButton("🔢 Report Count", callback_data='set_count')],
            [InlineKeyboardButton("📝 Report Reason", callback_data='set_reason')],
            [InlineKeyboardButton("💬 Report Message", callback_data='set_message')],
            [InlineKeyboardButton("🔙 Back", callback_data='back')]
        ])
        await query.edit_message_text(
            "⚙️ *Bot Settings*\n\n"
            "Configure your reporting parameters:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif query.data == 'stats':
        stats = db.data["user_stats"].get(str(user_id), {"total_reports": 0})
        await query.edit_message_text(
            f"📈 *Your Statistics*\n\n"
            f"Total Reports Made: `{stats['total_reports']}`\n"
            f"Subscription: {'✅ Active' if db.check_subscription(user_id) else '❌ Expired'}\n"
            f"Approved: ✅ Yes\n\n"
            f"Keep up the good work!",
            parse_mode='Markdown'
        )
    
    elif query.data == 'admin' and user_id in ADMIN_IDS:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve User", callback_data='admin_approve')],
            [InlineKeyboardButton("🎫 Create Code", callback_data='admin_create_code')],
            [InlineKeyboardButton("📊 View Stats", callback_data='admin_stats')],
            [InlineKeyboardButton("🔙 Back", callback_data='back')]
        ])
        await query.edit_message_text(
            "👑 *Admin Panel*\n\n"
            "Admin-only commands:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif query.data == 'mass_group':
        client = await get_client(user_id)
        if not client:
            await query.edit_message_text("❌ You need to login first!")
            return
        
        await query.edit_message_text(
            "👥 *Mass Group Reporter*\n\n"
            "Send group username or invite link.\n"
            "Then I'll ask for:\n"
            "• Number of reports\n"
            "• Delay between reports\n"
            "• Report reason\n\n"
            "Format: `@groupusername`",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_group'] = True
    
    elif query.data == 'set_delay':
        await query.edit_message_text(
            "⚡ *Set Report Delay*\n\n"
            "Send delay in seconds between reports.\n"
            "Recommended: 1-5 seconds\n\n"
            "Example: `2` for 2 seconds delay",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_delay'] = True
    
    elif query.data == 'set_count':
        await query.edit_message_text(
            "🔢 *Set Report Count*\n\n"
            "Send number of reports to make.\n"
            "Maximum: 100 per session\n\n"
            "Example: `50` for 50 reports",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_count'] = True
    
    elif query.data == 'set_reason':
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚫 Spam", callback_data='reason_spam'),
                InlineKeyboardButton("🔪 Violence", callback_data='reason_violence')
            ],
            [
                InlineKeyboardButton("🔞 Porn", callback_data='reason_porn'),
                InlineKeyboardButton("👤 Fake", callback_data='reason_fake')
            ],
            [
                InlineKeyboardButton("🚸 Child Abuse", callback_data='reason_child'),
                InlineKeyboardButton("📛 Other", callback_data='reason_other')
            ],
            [InlineKeyboardButton("🔙 Back", callback_data='back')]
        ])
        await query.edit_message_text(
            "📝 *Report Reason*\n\n"
            "Select reason for reporting:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    elif query.data.startswith('reason_'):
        reason = query.data.replace('reason_', '')
        reason_names = {
            'spam': 'Spam',
            'violence': 'Violence',
            'porn': 'Pornography',
            'fake': 'Fake Account',
            'child': 'Child Abuse',
            'other': 'Other'
        }
        context.user_data['report_reason'] = reason
        await query.edit_message_text(
            f"✅ Report reason set to: *{reason_names.get(reason, 'Other')}*",
            parse_mode='Markdown'
        )
    
    elif query.data == 'back':
        await query.edit_message_text(
            "🚀 *Main Menu*",
            reply_markup=create_keyboard(),
            parse_mode='Markdown'
        )
    
    elif query.data == 'admin_approve' and user_id in ADMIN_IDS:
        await query.edit_message_text(
            "✅ *Approve User*\n\n"
            "Send user ID to approve:\n"
            "Format: `123456789`",
            parse_mode='Markdown'
        )
        context.user_data['admin_awaiting_approve'] = True
    
    elif query.data == 'admin_create_code' and user_id in ADMIN_IDS:
        await query.edit_message_text(
            "🎫 *Create Redeem Code*\n\n"
            "Send in format:\n"
            "`CODE DAYS`\n\n"
            "Example: `PREMIUM30 30`\n"
            "Creates code PREMIUM30 valid for 30 days",
            parse_mode='Markdown'
        )
        context.user_data['admin_awaiting_code'] = True

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if not db.is_approved(user_id):
        await update.message.reply_text("❌ You are not approved to use this bot.")
        return
    
    # Handle redeem code
    if context.user_data.get('awaiting_redeem'):
        if db.use_redeem_code(text.upper(), user_id):
            await update.message.reply_text(
                f"✅ *Code Redeemed Successfully!*\n\n"
                f"Subscription activated.\n"
                f"You can now use all features!",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Invalid or already used code.")
        context.user_data.clear()
    
    # Handle admin approve
    elif context.user_data.get('admin_awaiting_approve') and user_id in ADMIN_IDS:
        try:
            target_id = int(text)
            db.approve_user(target_id)
            await update.message.reply_text(f"✅ User {target_id} approved!")
        except:
            await update.message.reply_text("❌ Invalid user ID.")
        context.user_data.clear()
    
    # Handle admin create code
    elif context.user_data.get('admin_awaiting_code') and user_id in ADMIN_IDS:
        try:
            code, days = text.split()
            days = int(days)
            db.create_redeem_code(code.upper(), days)
            await update.message.reply_text(
                f"✅ Code created!\n"
                f"Code: `{code.upper()}`\n"
                f"Days: {days}\n\n"
                f"Share with users to redeem.",
                parse_mode='Markdown'
            )
        except:
            await update.message.reply_text("❌ Invalid format. Use: CODE DAYS")
        context.user_data.clear()
    
    # Handle group mass report
    elif context.user_data.get('awaiting_group'):
        if not db.check_subscription(user_id):
            await update.message.reply_text("❌ You need an active subscription!")
            context.user_data.clear()
            return
        
        client = await get_client(user_id)
        if not client:
            await update.message.reply_text("❌ Login first!")
            context.user_data.clear()
            return
        
        context.user_data['target_group'] = text
        await update.message.reply_text(
            "🔢 *How many reports?*\n\n"
            "Send number (1-100000):",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_group_count'] = True
        context.user_data['awaiting_group'] = False
    
    elif context.user_data.get('awaiting_group_count'):
        try:
            count = int(text)
            if 1 <= count <= 100000:
                context.user_data['report_count'] = count
                await update.message.reply_text(
                    "⚡ *Delay between reports?*\n\n"
                    "Send delay in seconds (1-10):",
                    parse_mode='Markdown'
                )
                context.user_data['awaiting_group_delay'] = True
                context.user_data['awaiting_group_count'] = False
            else:
                await update.message.reply_text("❌ Invalid! Send number between 1-100.")
        except:
            await update.message.reply_text("❌ Invalid number!")
    
    elif context.user_data.get('awaiting_group_delay'):
        try:
            delay = float(text)
            if 0.5 <= delay <= 10:
                group = context.user_data['target_group']
                count = context.user_data['report_count']
                reason = context.user_data.get('report_reason', 'spam')
                
                # Report reason mapping
                reason_map = {
                    'spam': InputReportReasonSpam(),
                    'violence': InputReportReasonViolence(),
                    'porn': InputReportReasonPornography(),
                    'fake': InputReportReasonFake(),
                    'child': InputReportReasonChildAbuse(),
                    'other': InputReportReasonOther()
                }
                report_reason = reason_map.get(reason, InputReportReasonSpam())
                
                await update.message.reply_text(
                    f"🚀 *Starting Mass Report*\n\n"
                    f"Target: `{group}`\n"
                    f"Count: `{count}` reports\n"
                    f"Delay: `{delay}` seconds\n"
                    f"Reason: `{reason}`\n\n"
                    f"Starting in 3 seconds...",
                    parse_mode='Markdown'
                )
                
                await asyncio.sleep(3)
                
                # Actual reporting
                reported = 0
                try:
                    entity = await client.get_entity(group)
                    participants = []
                    async for user in client.iter_participants(entity, limit=count):
                        participants.append(user)
                    
                    for i, user in enumerate(participants):
                        try:
                            await client(ReportPeerRequest(
                                peer=user,
                                reason=report_reason,
                                message=get_report_message()
                            ))
                            reported += 1
                            if i % 5 == 0:
                                await update.message.reply_text(
                                    f"📊 Progress: `{reported}/{count}` reports",
                                    parse_mode='Markdown'
                                )
                            await asyncio.sleep(delay)
                        except Exception as e:
                            logging.error(f"Error reporting: {e}")
                    
                    db.update_stats(user_id, reported)
                    
                    await update.message.reply_text(
                        f"✅ *Mass Report Complete!*\n\n"
                        f"Successfully reported: `{reported}` users\n"
                        f"Total reports made: `{db.data['user_stats'].get(str(user_id), {}).get('total_reports', 0)}`\n\n"
                        f"⚠️ Use responsibly!",
                        parse_mode='Markdown'
                    )
                    
                except Exception as e:
                    await update.message.reply_text(f"❌ Error: {str(e)}")
                
                context.user_data.clear()
            else:
                await update.message.reply_text("❌ Delay must be between 0.5 and 10 seconds.")
        except:
            await update.message.reply_text("❌ Invalid delay!")

async def login_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handle phone/session login
    pass

# ========== MAIN ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("🤖 Advanced Mass Reporter Bot Started!")
    print("👑 Admin IDs:", ADMIN_IDS)
    print("📊 Database loaded:", len(db.data["approved_users"]), "approved users")
    
    app.run_polling()

if __name__ == "__main__":
    main()
