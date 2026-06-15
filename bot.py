ssion_pool')],
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
    
    app.run_polling()

if __name__ == "__main__":
    main()
