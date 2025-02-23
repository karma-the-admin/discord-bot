import discord
from discord.ext import commands
from datetime import datetime
import random
import config
from googletrans import Translator
from collections import defaultdict
from typing import Dict
from datetime import timedelta
import re
import asyncio
import aiohttp
from typing import Dict, Union, List
import json
import logging
import sys
from random import randint, choice
from asyncio import create_task
from functools import wraps


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=',', intents=intents)
deleted_messages = defaultdict(lambda: None)
autoresponders: Dict[int, Dict[str, Union[str, List[str]]]] = {}  # {guild_id: {trigger: response/reactions}}
reaction_roles = {}  # {message_id: {emoji: role_id}}

xp_data = {}  # {guild_id: {user_id: {"xp": amount, "level": level, "last_msg": timestamp}}}
XP_COOLDOWN = 60  # Seconds between XP gains
MIN_XP = 15  # Minimum XP per message
MAX_XP = 25  # Maximum XP per message

last_save_time = datetime.utcnow()
AUTOSAVE_INTERVAL = timedelta(minutes=5)  # Save every 5 minutes

def calculate_level(xp):
    """Calculate level based on XP amount"""
    return int((xp / 100) ** 0.5)

def save_xp_data():
    """Save XP data to JSON file"""
    with open('xp_data.json', 'w') as f:
        # Convert datetime objects to strings
        data = {}
        for guild_id, guild_data in xp_data.items():
            data[str(guild_id)] = {}
            for user_id, user_data in guild_data.items():
                data[str(guild_id)][str(user_id)] = {
                    "xp": user_data["xp"],
                    "level": user_data["level"],
                    "last_msg": user_data["last_msg"].isoformat() if user_data["last_msg"] else None
                }
        json.dump(data, f)

def load_xp_data():
    """Load XP data from JSON file"""
    try:
        with open('xp_data.json', 'r') as f:
            data = json.load(f)
            # Convert string timestamps back to datetime
            result = {}
            for guild_id, guild_data in data.items():
                result[int(guild_id)] = {}
                for user_id, user_data in guild_data.items():
                    result[int(guild_id)][int(user_id)] = {
                        "xp": user_data["xp"],
                        "level": user_data["level"],
                        "last_msg": datetime.fromisoformat(user_data["last_msg"]) if user_data["last_msg"] else None
                    }
            return result
    except FileNotFoundError:
        return {}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot_commands.log')
    ]
)

async def send_log_dm(bot, log_message: str):
    """Send log message to allowed user"""
    try:
        user = await bot.fetch_user(ALLOWED_USER_ID)
        if user:
            embed = discord.Embed(
                title="🔍 Bot Log",
                description=f"```\n{log_message}\n```",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            await user.send(embed=embed)
    except Exception as e:
        print(f"Failed to send log DM: {str(e)}")

def parse_time(time_str: str) -> int:
    """Convert time string to seconds
    Examples: 1d, 1h, 1m, 1s
    Returns seconds"""
    time_mapping = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400
    }
    
    match = re.match(r'(\d+)([smhd])', time_str.lower())
    if not match:
        raise ValueError("Invalid time format. Use: 1d, 1h, 1m, 1s")
        
    amount, unit = match.groups()
    return int(amount) * time_mapping[unit]

ALLOWED_USER_ID = config.ALLOWED_USER_ID

def is_allowed_user():
    """Check if user is allowed to use the command"""
    async def predicate(ctx):
        return ctx.author.id == ALLOWED_USER_ID
    return commands.check(predicate)

def admin_command():
    """Check if user is admin and allowed"""
    async def predicate(ctx):
        is_admin = ctx.author.guild_permissions.administrator
        is_allowed = ctx.author.id == ALLOWED_USER_ID
        return is_admin and is_allowed
    return commands.check(predicate)

def log_command():
    """Log command usage and send DM to allowed user"""
    def decorator(func):
        @wraps(func)
        async def wrapper(ctx, *args, **kwargs):
            command_name = ctx.command.name
            author = ctx.author
            guild = ctx.guild
            channel = ctx.channel
            args_str = ' '.join(str(arg) for arg in args)
            kwargs_str = ' '.join(f'{k}={v}' for k, v in kwargs.items())
            
            log_message = (
                f"Command: {command_name}\n"
                f"User: {author} (ID: {author.id})\n"
                f"Server: {guild.name} (ID: {guild.id})\n"
                f"Channel: #{channel.name} (ID: {channel.id})\n"
                f"Arguments: {args_str} {kwargs_str}".strip()
            )
            
            # Log to file
            logging.info(log_message)
            
            # Send DM to allowed user
            await send_log_dm(ctx.bot, log_message)
            
            return await func(ctx, *args, **kwargs)
        return wrapper
    return decorator

@bot.command()
@is_allowed_user()
@log_command()
async def serverinfo(ctx):
    server = ctx.guild
    embed = discord.Embed(
        title=f"{server.name} Info",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Server ID", value=server.id)
    embed.add_field(name="Members", value=len(server.members))
    embed.add_field(name="Created On", value=server.created_at.strftime("%B %d, %Y"))
    await ctx.send(embed=embed)

async def auto_save():
    """Periodically save XP and reaction roles data"""
    global last_save_time
    
    while True:
        now = datetime.utcnow()
        if now - last_save_time >= AUTOSAVE_INTERVAL:
            try:
                save_xp_data()
                save_reaction_roles()
                last_save_time = now
                log_message = f"Auto-saved XP and reaction roles data at {now}"
                print(log_message)
                await send_log_dm(bot, log_message)
            except Exception as e:
                error_message = f"Auto-save error at {now}: {str(e)}"
                print(error_message)
                await send_log_dm(bot, error_message)
        
        await asyncio.sleep(60)  # Check every minute

@bot.command()
@is_allowed_user()
@log_command() 
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(
        title=f"{member.name}'s Info",
        color=member.color,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%B %d, %Y"))
    await ctx.send(embed=embed)

@bot.command()
@admin_command()
@log_command() 
async def moderate(ctx, action: str, member: discord.Member, *, reason=None):
    action = action.lower()
    try:
        if action == "kick":
            await member.kick(reason=reason)
            message = f"kicked"
        elif action == "ban":
            await member.ban(reason=reason)
            message = f"banned"
        elif action == "mute":
            muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
            if not muted_role:
                muted_role = await ctx.guild.create_role(name="Muted")
                for channel in ctx.guild.channels:
                    await channel.set_permissions(muted_role, speak=False, send_messages=False)
            await member.add_roles(muted_role, reason=reason)
            message = f"muted"
        elif action == "unmute":
            muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
            await member.remove_roles(muted_role)
            message = f"unmuted"
        elif action == "warn":
            message = f"warned"
        else:
            return await ctx.send("Invalid action! Use: kick, ban, mute, unmute, or warn")
        
        await ctx.send(f"{member.mention} has been {message}. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to do that!")

@bot.command()
@admin_command()
@log_command() 
async def purge(ctx, option: str = None, *args):
    """
    Smart purge command with multiple filtering options
    Usage:
    !purge amount <number>                  - Purges last X messages
    !purge after <message_id> [limit]       - Purges messages after specified message
    !purge before <message_id> [limit]      - Purges messages before specified message
    !purge between <msg_id1> <msg_id2>     - Purges messages between two messages
    !purge contains <text> [limit]          - Purges messages containing text
    !purge from @user [limit]              - Purges messages from specific user
    """
    try:
        # Default limit for safety
        default_limit = 100
        
        # Create progress message
        progress = await ctx.send("🔄 Processing purge request...")
        
        if not option:
            await progress.edit(content="❌ Please specify a purge option! Use `!help purge` for details.")
            return
            
        option = option.lower()
        
        if option == "amount":
            if not args:
                await progress.edit(content="❌ Please specify the number of messages to purge!")
                return
                
            amount = int(args[0])
            if amount > 1000:
                await progress.edit(content="❌ Cannot purge more than 1000 messages at once!")
                return
                
            deleted = await ctx.channel.purge(limit=amount + 1)  # +1 for command message
            await ctx.send(f"✅ Purged {len(deleted)-1} messages.", delete_after=5)
            
        elif option == "after":
            if not args:
                await progress.edit(content="❌ Please specify the message ID!")
                return
                
            message_id = int(args[0])
            limit = int(args[1]) if len(args) > 1 else default_limit
            
            after_message = await ctx.channel.fetch_message(message_id)
            deleted = await ctx.channel.purge(after=after_message, limit=limit)
            await ctx.send(f"✅ Purged {len(deleted)} messages after specified message.", delete_after=5)
            
        elif option == "before":
            if not args:
                await progress.edit(content="❌ Please specify the message ID!")
                return
                
            message_id = int(args[0])
            limit = int(args[1]) if len(args) > 1 else default_limit
            
            before_message = await ctx.channel.fetch_message(message_id)
            deleted = await ctx.channel.purge(before=before_message, limit=limit)
            await ctx.send(f"✅ Purged {len(deleted)} messages before specified message.", delete_after=5)
            
        elif option == "between":
            if len(args) < 2:
                await progress.edit(content="❌ Please specify both message IDs!")
                return
                
            msg_id1, msg_id2 = int(args[0]), int(args[1])
            msg1 = await ctx.channel.fetch_message(msg_id1)
            msg2 = await ctx.channel.fetch_message(msg_id2)
            
            deleted = await ctx.channel.purge(after=msg1, before=msg2)
            await ctx.send(f"✅ Purged {len(deleted)} messages between specified messages.", delete_after=5)
            
        elif option == "contains":
            if not args:
                await progress.edit(content="❌ Please specify the text to search for!")
                return
                
            text = args[0].lower()
            limit = int(args[1]) if len(args) > 1 else default_limit
            
            check = lambda m: text in m.content.lower()
            deleted = await ctx.channel.purge(limit=limit, check=check)
            await ctx.send(f"✅ Purged {len(deleted)} messages containing '{text}'.", delete_after=5)
            
        elif option == "from":
            if not args:
                await progress.edit(content="❌ Please mention a user!")
                return
                
            try:
                user_id = int(args[0].strip('<@!>'))
                limit = int(args[1]) if len(args) > 1 else default_limit
                
                check = lambda m: m.author.id == user_id
                deleted = await ctx.channel.purge(limit=limit, check=check)
                await ctx.send(f"✅ Purged {len(deleted)} messages from user.", delete_after=5)
            except ValueError:
                await progress.edit(content="❌ Invalid user mention!")
                
        else:
            await progress.edit(content="❌ Invalid purge option! Use `!help purge` for details.")
            
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to delete messages!")
    except discord.NotFound:
        await ctx.send("❌ Message not found! Make sure the message ID is correct.")
    except ValueError:
        await ctx.send("❌ Invalid number provided!")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@is_allowed_user()
@log_command() 
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(
        title=f"{member.name}'s Avatar",
        color=discord.Color.blue()
    )
    embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)

@bot.command()
@admin_command()
@log_command() 
async def add_role(ctx, member: discord.Member, role: discord.Role):
    try:
        await member.add_roles(role)
        await ctx.send(f"Role {role.name} has been added to {member.mention}.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to add this role.")

@bot.command()
@is_allowed_user()
@log_command() 
async def translate(ctx, lang_to, *, text):
    """
    Translate text to a specified language
    Usage: !translate <language_code> <text>
    Example: !translate es Hello, how are you?
    """
    try:
        translator = Translator()
        translation = translator.translate(text, dest=lang_to)
        
        embed = discord.Embed(
            title="Translation",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Original", value=text, inline=False)
        embed.add_field(name=f"Translated to {lang_to}", value=translation.text, inline=False)
        embed.set_footer(text=f"From: {translation.src} | To: {lang_to}")
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")

@bot.command()
@admin_command()
@log_command() 
async def remove_role(ctx, member: discord.Member, role: discord.Role):
    try:
        await member.remove_roles(role)
        await ctx.send(f"Role {role.name} has been removed from {member.mention}.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to remove this role.")

@bot.command()
@admin_command()
@log_command() 
async def lockdown(ctx, channel: discord.TextChannel = None, *, reason=None):
    """
    Locks down a channel by preventing @everyone from sending messages
    Usage: !lockdown [channel] [reason]
    If no channel is specified, locks the current channel
    """
    channel = channel or ctx.channel
    reason = reason or "No reason provided"

    try:
        # Get the @everyone role
        everyone_role = ctx.guild.default_role
        
        # Set permissions
        await channel.set_permissions(everyone_role, send_messages=False, reason=reason)
        
        embed = discord.Embed(
            title="🔒 Channel Locked",
            description=f"This channel has been locked by {ctx.author.mention}",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to manage channel permissions!")
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")

@bot.command()
@admin_command()
@log_command() 
async def unlock(ctx, channel: discord.TextChannel = None, *, reason=None):
    """
    Unlocks a channel, allowing @everyone to send messages again
    Usage: !unlock [channel] [reason]
    If no channel is specified, unlocks the current channel
    """
    channel = channel or ctx.channel
    reason = reason or "No reason provided"

    try:
        # Get the @everyone role
        everyone_role = ctx.guild.default_role
        
        # Reset permissions
        await channel.set_permissions(everyone_role, send_messages=None, reason=reason)
        
        embed = discord.Embed(
            title="🔓 Channel Unlocked",
            description=f"This channel has been unlocked by {ctx.author.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to manage channel permissions!")
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")

@bot.command()
@is_allowed_user()
@log_command() 
async def roleinfo(ctx, role: discord.Role):
    """
    Displays information about a specified role
    Usage: !roleinfo @role
    """
    try:
        # Create embed
        embed = discord.Embed(
            title=f"Role Information: {role.name}",
            color=role.color,
            timestamp=datetime.utcnow()
        )
        
        # Add role information fields
        embed.add_field(name="Role ID", value=role.id, inline=True)
        embed.add_field(name="Color", value=str(role.color), inline=True)
        embed.add_field(name="Position", value=role.position, inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name="Members", value=len(role.members), inline=True)
        embed.add_field(name="Created At", value=role.created_at.strftime("%B %d, %Y"), inline=True)
        
        # Add key permissions if the role has any
        permissions = [perm[0].replace('_', ' ').title() for perm in role.permissions if perm[1]]
        if permissions:
            embed.add_field(name="Key Permissions", value="\n".join(permissions[:10]), inline=False)
            if len(permissions) > 10:
                embed.add_field(name="", value="...and more", inline=False)
        
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to fetch role information!")
    except Exception as e:
        await ctx.send(f"An error occurred: {str(e)}")

@bot.command()
@is_allowed_user()
@log_command() 
async def snipe(ctx):
    """
    Shows the most recently deleted message in the channel
    Usage: !snipe
    """
    message = deleted_messages[ctx.channel.id]
    
    if message is None:
        await ctx.send("There are no recently deleted messages to snipe!")
        return
        
    embed = discord.Embed(
        title="📝 Deleted Message",
        description=message['content'],
        color=discord.Color.red(),
        timestamp=message['timestamp']
    )
    
    embed.set_author(name=message['author'].name, icon_url=message['author'].display_avatar.url)
    embed.set_footer(text=f"Message sent at")
    
    # If message had attachments, add them to the embed
    if message['attachments']:
        embed.add_field(name="Attachments", value="\n".join(message['attachments']), inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
@admin_command()
@log_command() 
async def autoresponder(ctx, action: str, trigger: str = None, *, response: str = None):
    """
    Manage autoresponders for the server
    Usage: 
    !autoresponder add <trigger> <response>     - Add text response
    !autoresponder react <trigger> <emojis>     - Add reaction response (space-separated emojis)
    !autoresponder remove <trigger>             - Remove autoresponder
    !autoresponder list                         - List all autoresponders
    """
    guild_id = ctx.guild.id
    
    if guild_id not in autoresponders:
        autoresponders[guild_id] = {}
    
    if action.lower() == "add":
        if not trigger or not response:
            await ctx.send("❌ Please provide both trigger and response!")
            return
            
        autoresponders[guild_id][trigger.lower()] = {"type": "text", "response": response}
        embed = discord.Embed(
            title="✅ Autoresponder Added",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Trigger", value=trigger, inline=True)
        embed.add_field(name="Response", value=response, inline=True)
        await ctx.send(embed=embed)
        
    elif action.lower() == "react":
        if not trigger or not response:
            await ctx.send("❌ Please provide both trigger and emojis!")
            return
            
        # Split response into individual emojis
        emojis = response.split()
        
        # Validate emojis
        try:
            # Test message to verify emojis
            test_msg = await ctx.send("Testing emojis...")
            for emoji in emojis:
                try:
                    await test_msg.add_reaction(emoji)
                except:
                    await test_msg.delete()
                    await ctx.send(f"❌ Invalid emoji: {emoji}")
                    return
            await test_msg.delete()
        except:
            await ctx.send("❌ Failed to validate emojis!")
            return
            
        autoresponders[guild_id][trigger.lower()] = {"type": "reaction", "response": emojis}
        embed = discord.Embed(
            title="✅ Reaction Autoresponder Added",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Trigger", value=trigger, inline=True)
        embed.add_field(name="Reactions", value=" ".join(emojis), inline=True)
        await ctx.send(embed=embed)
        
    elif action.lower() == "remove":
        if not trigger:
            await ctx.send("❌ Please specify the trigger to remove!")
            return
            
        if trigger.lower() in autoresponders[guild_id]:
            del autoresponders[guild_id][trigger.lower()]
            await ctx.send(f"✅ Removed autoresponder for trigger: `{trigger}`")
        else:
            await ctx.send("❌ That trigger doesn't exist!")
            
    elif action.lower() == "list":
        if not autoresponders[guild_id]:
            await ctx.send("No autoresponders set up!")
            return
            
        embed = discord.Embed(
            title="📝 Autoresponders",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        for trigger, data in autoresponders[guild_id].items():
            response_type = data["type"]
            response = data["response"]
            if response_type == "text":
                value = f"Type: Text\nResponse: {response[:100]}{'...' if len(response) > 100 else ''}"
            else:
                value = f"Type: Reaction\nEmojis: {' '.join(response)}"
            embed.add_field(name=f"Trigger: {trigger}", value=value, inline=False)
            
        await ctx.send(embed=embed)
        
    else:
        await ctx.send("❌ Invalid action! Use: add, react, remove, or list")

@bot.command()
@admin_command()
@log_command() 
async def dm(ctx, member: discord.Member, *, message: str):
    """
    Sends a direct message to a specified user
    Usage: !dm @user <message>
    Example: !dm @username Hello, this is a message from the server!
    """
    try:
        # Create embed for DM
        embed = discord.Embed(
            title=f"Message from {ctx.guild.name}",
            description=message,
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Sent by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
        
        # Send DM
        await member.send(embed=embed)
        
        # Confirmation embed
        confirm_embed = discord.Embed(
            title="✉️ Message Sent",
            description=f"Successfully sent message to {member.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        confirm_embed.add_field(name="Message", value=message[:1024], inline=False)
        
        await ctx.send(embed=confirm_embed)
        
    except discord.Forbidden:
        await ctx.send("❌ I couldn't send a DM to that user. They might have DMs disabled or blocked the bot.")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@admin_command()
@log_command() 
async def timeout(ctx, member: discord.Member, duration: str, *, reason: str = None):
    """
    Timeout a member for a specified duration
    Usage: !timeout @user <duration> [reason]
    Duration format: #s, #m, #h, #d (seconds, minutes, hours, days)
    Example: !timeout @user 1h Spamming
    """
    try:
        # Convert duration string to seconds
        seconds = parse_time(duration)
        
        # Maximum timeout duration is 28 days
        if seconds > 28 * 24 * 60 * 60:
            await ctx.send("❌ Timeout duration cannot exceed 28 days!")
            return
            
        # Apply timeout
        until = datetime.utcnow() + timedelta(seconds=seconds)
        await member.timeout(until, reason=reason)
        
        # Create embed response
        embed = discord.Embed(
            title="⏰ Member Timed Out",
            description=f"{member.mention} has been timed out",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Duration", value=duration, inline=True)
        embed.add_field(name="Until", value=until.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
            
        await ctx.send(embed=embed)
        
    except ValueError as e:
        await ctx.send(f"❌ {str(e)}")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to timeout this member!")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@admin_command()
@log_command() 
async def untimeout(ctx, member: discord.Member, *, reason: str = None):
    """
    Remove timeout from a member
    Usage: !untimeout @user [reason]
    """
    try:
        await member.timeout(None, reason=reason)
        
        embed = discord.Embed(
            title="✅ Timeout Removed",
            description=f"Timeout has been removed from {member.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
            
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to remove timeout from this member!")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@admin_command()
@log_command() 
async def clonechannel(ctx, channel: discord.TextChannel = None, messages: int = 100):
    """
    Creates a copy of a channel including recent messages
    Usage: !clonechannel [#channel] [message_count]
    Example: !clonechannel #general 50
    Default: Clones current channel, last 100 messages
    """
    try:
        source_channel = channel or ctx.channel
        
        # Create progress message
        progress = await ctx.send(f"🔄 Cloning channel {source_channel.mention}...")
        
        # Clone the channel
        new_channel = await source_channel.clone(
            name=f"{source_channel.name}-copy",
            reason=f"Channel cloned by {ctx.author}"
        )
        
        # Update progress
        await progress.edit(content=f"📥 Fetching messages from {source_channel.mention}...")
        
        # Fetch messages from original channel (from newest to oldest)
        message_list = []
        async for message in source_channel.history(limit=messages, oldest_first=True):
            if not message.author.bot:  # Skip bot messages
                content = message.content
                
                # Handle embeds
                if message.embeds:
                    content += "\n[Message contained embeds]"
                
                # Handle attachments
                if message.attachments:
                    content += "\n" + "\n".join([f"[Attachment: {a.url}]" for a in message.attachments])
                
                message_list.append({
                    'author': message.author.name,
                    'content': content,
                    'timestamp': message.created_at.strftime("%Y-%m-%d %H:%M:%S")
                })
        
        # Update progress
        await progress.edit(content=f"📤 Sending messages to new channel...")
        
        # Send messages to new channel using webhooks for proper attribution
        webhook = await new_channel.create_webhook(name="Message Cloner")
        
        try:
            for msg in message_list:
                await webhook.send(
                    content=f"**{msg['author']} [{msg['timestamp']}]**\n{msg['content']}",
                    username=msg['author']
                )
                await asyncio.sleep(1)  # Avoid rate limits
        finally:
            await webhook.delete()
        
        # Create completion embed
        embed = discord.Embed(
            title="✅ Channel Cloned Successfully",
            description=f"Created new channel {new_channel.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Original Channel", value=source_channel.mention, inline=True)
        embed.add_field(name="Messages Cloned", value=len(message_list), inline=True)
        
        await progress.edit(content=None, embed=embed)
        
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to manage channels or create webhooks!")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@is_allowed_user()
@log_command() 
async def define(ctx, *, word: str):
    """
    Get the definition of a word from dictionary
    Usage: !define <word>
    Example: !define python
    """
    try:
        # Free dictionary API
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    embed = discord.Embed(
                        title=f"📚 Definition of '{word}'",
                        color=discord.Color.blue(),
                        timestamp=datetime.utcnow()
                    )
                    
                    # Get first entry's definitions
                    if data and isinstance(data, list):
                        entry = data[0]
                        
                        # Add phonetics if available
                        if "phonetic" in entry:
                            embed.add_field(name="Pronunciation", value=entry["phonetic"], inline=False)
                        
                        # Add definitions
                        for meaning in entry.get("meanings", [])[:3]:  # Limit to 3 meanings
                            part_of_speech = meaning.get("partOfSpeech", "unknown")
                            definitions = meaning.get("definitions", [])
                            
                            if definitions:
                                definition = definitions[0]  # Get first definition
                                value = f"**Definition:** {definition['definition']}\n"
                                if "example" in definition:
                                    value += f"**Example:** *{definition['example']}*"
                                    
                                embed.add_field(
                                    name=f"({part_of_speech})",
                                    value=value,
                                    inline=False
                                )
                    
                    await ctx.send(embed=embed)
                    
                elif response.status == 404:
                    await ctx.send(f"❌ No definition found for '{word}'")
                else:
                    await ctx.send("❌ An error occurred while fetching the definition")
                    
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command(aliases=['urban'])
@is_allowed_user()
@log_command() 
async def urbandict(ctx, *, term: str):
    """
    Look up a term on Urban Dictionary
    Usage: !urbandict <term> or !urban <term>
    Example: !urbandict yeet
    """
    try:
        # Urban Dictionary API
        url = "https://api.urbandictionary.com/v0/define"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"term": term}) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if (data["list"]):
                        # Get the highest voted definition
                        definition = max(data["list"], key=lambda x: x["thumbs_up"])
                        
                        embed = discord.Embed(
                            title=f"🏙️ Urban Dictionary: {term}",
                            url=definition["permalink"],
                            color=discord.Color.dark_green(),
                            timestamp=datetime.utcnow()
                        )
                        
                        # Clean up the definition text
                        def_text = definition["definition"][:1024]  # Discord limit
                        example = definition["example"][:1024]
                        
                        embed.add_field(name="Definition", value=def_text, inline=False)
                        if example:
                            embed.add_field(name="Example", value=f"*{example}*", inline=False)
                            
                        embed.add_field(
                            name="👍 Upvotes",
                            value=definition["thumbs_up"],
                            inline=True
                        )
                        embed.add_field(
                            name="👎 Downvotes",
                            value=definition["thumbs_down"],
                            inline=True
                        )
                        
                        embed.set_footer(text=f"By {definition['author']}")
                        
                        await ctx.send(embed=embed)
                    else:
                        await ctx.send(f"❌ No Urban Dictionary definition found for '{term}'")
                else:
                    await ctx.send("❌ An error occurred while fetching from Urban Dictionary")
                    
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.remove_command('help')  # Remove default help command

@bot.command()
@is_allowed_user()
@log_command() 
async def help(ctx, command_name: str = None):
    """
    Improved help command with detailed information and categories
    Usage: !help [command]
    Example: !help timeout
    """
    if command_name:
        # Show detailed help for specific command
        command = bot.get_command(command_name)
        if not command:
            await ctx.send(f"❌ Command `{command_name}` not found!")
            return
            
        embed = discord.Embed(
            title=f"Command: {command.name}",
            description=command.help or "No description available",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        # Add usage if available in docstring
        if command.help:
            usage = next((line.strip() for line in command.help.split('\n') 
                         if line.strip().startswith('Usage:')), None)
            if usage:
                embed.add_field(name="Usage", value=f"`{usage[7:]}`", inline=False)
                
            example = next((line.strip() for line in command.help.split('\n') 
                          if line.strip().startswith('Example:')), None)
            if example:
                embed.add_field(name="Example", value=f"`{example[9:]}`", inline=False)
        
        # Add aliases if any
        if command.aliases:
            embed.add_field(
                name="Aliases", 
                value=", ".join(f"`{alias}`" for alias in command.aliases),
                inline=False
            )
            
        await ctx.send(embed=embed)
        return

    # Show categorized help menu
    categories = {
        "🛡️ Moderation": ["moderate", "timeout", "untimeout", "purge", "lockdown", "unlock", "dm"],
        "👥 User Management": ["add_role", "remove_role", "clonechannel"],
        "ℹ️ Information": ["serverinfo", "userinfo", "roleinfo", "avatar", "ping"],
        "🔍 Utility": ["define", "urbandict", "translate", "snipe"],
        "⚙️ Configuration": ["autoresponder"]
    }
    
    embed = discord.Embed(
        title="📚 Bot Commands Help",
        description="Use `!help <command>` for detailed information about a specific command.",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    
    for category, commands in categories.items():
        valid_commands = []
        for cmd_name in commands:
            cmd = bot.get_command(cmd_name)
            if cmd and (await cmd.can_run(ctx)):
                valid_commands.append(f"`{cmd_name}`")
        
        if valid_commands:
            embed.add_field(
                name=category,
                value=" • ".join(valid_commands),
                inline=False
            )
    
    footer_text = "💡 Tip: Commands shown are based on your permissions"
    embed.set_footer(text=footer_text)
    
    await ctx.send(embed=embed)

@bot.command()
@admin_command()
@log_command() 
async def reactionrole(ctx, message_id: str, emoji: str, role: discord.Role):
    """
    Set up reaction roles on an existing message
    Usage: !reactionrole <message_id> <emoji> @role
    Example: !reactionrole 123456789 👍 @Member
    """
    try:
        # Convert message_id to int
        message_id = int(message_id)
        
        # Try to fetch the message
        try:
            message = await ctx.channel.fetch_message(message_id)
        except discord.NotFound:
            await ctx.send("❌ Message not found! Make sure the message ID is correct and in this channel.")
            return
        
        # Store the reaction role mapping
        if message_id not in reaction_roles:
            reaction_roles[message_id] = {}
            
        reaction_roles[message_id][emoji] = role.id
        
        # Add the initial reaction
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            await ctx.send("❌ Invalid emoji! Please use a valid emoji.")
            return
            
        # Confirmation embed
        embed = discord.Embed(
            title="✅ Reaction Role Added",
            description=f"Successfully set up reaction role for message.",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Message ID", value=message_id, inline=True)
        embed.add_field(name="Emoji", value=emoji, inline=True)
        embed.add_field(name="Role", value=role.mention, inline=True)
        
        await ctx.send(embed=embed)
        
    except ValueError:
        await ctx.send("❌ Invalid message ID! Please provide a valid message ID.")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

def save_reaction_roles():
    """Save reaction roles to a JSON file"""
    with open('reaction_roles.json', 'w') as f:
        # Convert all keys to strings for JSON serialization
        data = {str(msg_id): {str(emoji): role_id 
               for emoji, role_id in roles.items()}
               for msg_id, roles in reaction_roles.items()}
        json.dump(data, f)

def load_reaction_roles():
    """Load reaction roles from JSON file"""
    try:
        with open('reaction_roles.json', 'r') as f:
            data = json.load(f)
            # Convert message IDs back to integers
            return {int(msg_id): {str(emoji): role_id 
                   for emoji, role_id in roles.items()}
                   for msg_id, roles in data.items()}
    except FileNotFoundError:
        return {}

@bot.command()
@admin_command()
@log_command() 
async def save(ctx):
    """
    Save the current reaction role configuration
    Usage: !save
    """
    try:
        save_reaction_roles()
        await ctx.send("✅ Reaction roles configuration has been saved!")
    except Exception as e:
        await ctx.send(f"❌ Failed to save configuration: {str(e)}")

@bot.command()
@admin_command()
@log_command() 
async def tempchannel(ctx, channel_type: str, duration: str, *, name: str):
    """
    Creates a temporary text or voice channel that deletes itself after specified duration
    Usage: !tempchannel <type> <duration> <name>
    Types: text, voice
    Duration format: #s, #m, #h, #d (seconds, minutes, hours, days)
    Example: !tempchannel text 1h meeting-room
    Example: !tempchannel voice 30m Game Night
    """
    try:
        # Validate channel type
        channel_type = channel_type.lower()
        if channel_type not in ['text', 'voice']:
            await ctx.send("❌ Channel type must be 'text' or 'voice'!")
            return

        # Convert duration string to seconds
        seconds = parse_time(duration)
        
        # Create progress message
        progress = await ctx.send("🔄 Creating temporary channel...")
        
        # Create the channel
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True if channel_type == 'text' else False,
                connect=True if channel_type == 'voice' else False
            )
        }
        
        if channel_type == 'text':
            new_channel = await ctx.guild.create_text_channel(
                name=name,
                overwrites=overwrites,
                reason=f"Temporary channel created by {ctx.author}"
            )
        else:
            new_channel = await ctx.guild.create_voice_channel(
                name=name,
                overwrites=overwrites,
                reason=f"Temporary channel created by {ctx.author}"
            )
        
        # Create embed for confirmation
        embed = discord.Embed(
            title="✅ Temporary Channel Created",
            description=f"Channel will be deleted in {duration}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Name", value=new_channel.mention, inline=True)
        embed.add_field(name="Type", value=channel_type.capitalize(), inline=True)
        embed.add_field(name="Duration", value=duration, inline=True)
        
        await progress.edit(content=None, embed=embed)
        
        # Schedule channel deletion
        await asyncio.sleep(seconds)
        
        try:
            await new_channel.delete(reason="Temporary channel duration expired")
            # Notify in the original channel
            await ctx.send(f"📤 Temporary channel `{name}` has been deleted.")
        except discord.NotFound:
            # Channel was already deleted manually
            pass
        except discord.Forbidden:
            await ctx.send("❌ Failed to delete the temporary channel - missing permissions.")
            
    except ValueError as e:
        await ctx.send(f"❌ {str(e)}")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to manage channels!")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@is_allowed_user()
@log_command() 
async def invite(ctx, duration: int = 24, uses: int = 0):
    """
    Creates an invite link for the server
    Usage: !invite [duration_hours] [max_uses]
    Example: !invite 48 5
    Default: 24 hour duration, unlimited uses
    """
    try:
        # Create progress message
        progress = await ctx.send("🔄 Generating invite link...")
        
        # Convert duration to seconds
        seconds = duration * 3600  # hours to seconds
        
        # Create the invite
        invite = await ctx.channel.create_invite(
            max_age=seconds,
            max_uses=uses,
            unique=True,
            reason=f"Invite created by {ctx.author}"
        )
        
        # Create embed
        embed = discord.Embed(
            title="🎟️ Server Invite Created",
            description=f"Here's your invite link: {invite.url}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="Duration", 
            value=f"{duration} hours" if duration > 0 else "Never expires",
            inline=True
        )
        embed.add_field(
            name="Max Uses",
            value=str(uses) if uses > 0 else "Unlimited",
            inline=True
        )
        embed.add_field(
            name="Channel",
            value=ctx.channel.mention,
            inline=True
        )
        
        # Update progress message with embed
        await progress.edit(content=None, embed=embed)
        
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to create invites!")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@is_allowed_user()
@log_command() 
async def servers(ctx):
    """
    Shows information about all servers the bot is in
    Usage: !servers
    """
    try:
        # Create base embed
        embed = discord.Embed(
            title="🌐 Bot Server List",
            description=f"Currently in {len(bot.guilds)} servers",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )

        # Sort guilds by member count
        sorted_guilds = sorted(bot.guilds, key=lambda g: g.member_count, reverse=True)

        total_members = sum(g.member_count for g in bot.guilds)
        total_channels = sum(len(g.channels) for g in bot.guilds)

        # Add total statistics
        embed.add_field(
            name="📊 Total Statistics",
            value=f"Members: {total_members:,}\n"
                  f"Channels: {total_channels:,}",
            inline=False
        )

        # Add individual server information
        for guild in sorted_guilds[:10]:  # Limit to 10 servers to avoid embed limits
            # Calculate bot to user ratio
            bot_count = sum(1 for m in guild.members if m.bot)
            user_count = guild.member_count - bot_count
            
            value = (
                f"👥 Members: {guild.member_count:,} "
                f"({user_count:,} users, {bot_count:,} bots)\n"
                f"📺 Channels: {len(guild.channels):,}\n"
                f"👑 Owner: {guild.owner}\n"
                f"📅 Created: {guild.created_at.strftime('%Y-%m-%d')}"
            )
            
            embed.add_field(
                name=f"📍 {guild.name} (ID: {guild.id})",
                value=value,
                inline=False
            )

        # Add note if there are more servers
        if len(bot.guilds) > 10:
            embed.set_footer(text=f"And {len(bot.guilds) - 10} more servers...")

        await ctx.send(embed=embed)

    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to fetch server information!")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@is_allowed_user()
@log_command()
async def list_commands(ctx):
    """
    Shows a comprehensive list of all bot commands
    Usage: !commands
    """
    for page in [COMMAND_LIST[i:i+1994] for i in range(0, len(COMMAND_LIST), 1994)]:
        await ctx.send(f"```md\n{page}```")

@bot.command()
@is_allowed_user()
@log_command()
async def coinflip(ctx, bet: str = None):
    """
    Play a coin flip game
    Usage: !coinflip [heads/tails]
    Example: !coinflip heads
    """
    try:
        # Create initial embed
        embed = discord.Embed(
            title="🎲 Coin Flip Game",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )

        # If no bet is placed, just flip the coin
        if not bet:
            result = choice(['heads', 'tails'])
            embed.description = f"The coin landed on: **{result.upper()}**! 🪙"
            await ctx.send(embed=embed)
            return

        # Validate bet
        bet = bet.lower()
        if bet not in ['heads', 'tails']:
            await ctx.send("❌ Please bet either 'heads' or 'tails'!")
            return

        # Create suspense message
        msg = await ctx.send("🪙 Flipping the coin...")
        await asyncio.sleep(1.5)

        # Determine result
        result = choice(['heads', 'tails'])
        
        # Update embed based on result
        embed.add_field(name="Your Bet", value=bet.capitalize(), inline=True)
        embed.add_field(name="Result", value=result.capitalize(), inline=True)
        
        if bet == result:
            embed.description = "🎉 Congratulations! You won!"
            embed.color = discord.Color.green()
        else:
            embed.description = "😔 Better luck next time!"
            embed.color = discord.Color.red()

        await msg.edit(content=None, embed=embed)

    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@is_allowed_user()
@log_command()
async def numguess(ctx):
    """
    Play a number guessing game
    Usage: !numguess
    The bot will generate a number between 1-100
    """
    try:
        # Generate random number
        number = randint(1, 100)
        attempts = 0
        max_attempts = 7

        # Create initial embed
        embed = discord.Embed(
            title="🔢 Number Guessing Game",
            description=(
                "I've thought of a number between 1 and 100!\n"
                f"You have {max_attempts} attempts to guess it.\n"
                "Type your guess as a number."
            ),
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        await ctx.send(embed=embed)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()

        while attempts < max_attempts:
            try:
                guess_msg = await bot.wait_for('message', timeout=30.0, check=check)
            except asyncio.TimeoutError:
                await ctx.send("⏰ Time's up! The game has ended.")
                return

            guess = int(guess_msg.content)
            attempts += 1

            # Create response embed
            embed = discord.Embed(
                title="🔢 Number Guessing Game",
                timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="Attempts",
                value=f"{attempts}/{max_attempts}",
                inline=True
            )

            # Check guess
            if guess == number:
                embed.description = f"🎉 Congratulations! You got it in {attempts} attempts!"
                embed.color = discord.Color.green()
                await ctx.send(embed=embed)
                return
            elif guess < number:
                embed.description = "⬆️ Higher! Try again."
                embed.color = discord.Color.gold()
            else:
                embed.description = "⬇️ Lower! Try again."
                embed.color = discord.Color.gold()

            await ctx.send(embed=embed)

        # If player runs out of attempts
        embed = discord.Embed(
            title="🔢 Game Over",
            description=f"You've run out of attempts! The number was {number}.",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")

@bot.command()
@is_allowed_user()
@log_command()
async def rank(ctx, member: discord.Member = None):
    """
    Check your or another user's XP and level
    Usage: !rank [@user]
    Example: !rank @username
    """
    member = member or ctx.author
    guild_id = ctx.guild.id
    user_id = member.id
    
    if (guild_id not in xp_data or 
        user_id not in xp_data[guild_id]):
        await ctx.send(f"{member.display_name} hasn't earned any XP yet!")
        return
    
    user_data = xp_data[guild_id][user_id]
    
    # Calculate progress to next level
    current_level_xp = (user_data["level"] ** 2) * 100
    next_level_xp = ((user_data["level"] + 1) ** 2) * 100
    xp_needed = next_level_xp - current_level_xp
    xp_progress = user_data["xp"] - current_level_xp
    progress_percent = (xp_progress / xp_needed) * 100
    
    embed = discord.Embed(
        title=f"🏆 Rank Card - {member.display_name}",
        color=member.color,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=user_data["level"], inline=True)
    embed.add_field(name="Total XP", value=user_data["xp"], inline=True)
    embed.add_field(name="Progress to Next Level", 
                   value=f"{xp_progress}/{xp_needed} XP ({progress_percent:.1f}%)", 
                   inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
@is_allowed_user()
@log_command()
async def leaderboard(ctx, page: int = 1):
    """
    Show the server XP leaderboard
    Usage: !leaderboard [page]
    Example: !leaderboard 2
    """
    guild_id = ctx.guild.id
    if guild_id not in xp_data:
        await ctx.send("No XP data for this server yet!")
        return
    
    # Sort users by XP
    sorted_users = sorted(
        xp_data[guild_id].items(),
        key=lambda x: x[1]["xp"],
        reverse=True
    )
    
    # Paginate results (10 per page)
    pages = (len(sorted_users) + 9) // 10
    if page < 1 or page > pages:
        await ctx.send("Invalid page number!")
        return
    
    start_idx = (page - 1) * 10
    end_idx = start_idx + 10
    
    embed = discord.Embed(
        title=f"🏆 XP Leaderboard - {ctx.guild.name}",
        description=f"Page {page}/{pages}",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )
    
    for idx, (user_id, user_data) in enumerate(sorted_users[start_idx:end_idx], start=start_idx + 1):
        member = ctx.guild.get_member(user_id)
        name = member.display_name if member else f"User {user_id}"
        value = f"Level {user_data['level']} | {user_data['xp']} XP"
        embed.add_field(
            name=f"{idx}. {name}",
            value=value,
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command()
@admin_command()
@log_command()
async def givexp(ctx, member: discord.Member, amount: int):
    """
    Give XP to a user (Admin only)
    Usage: !givexp @user <amount>
    Example: !givexp @username 1000
    """
    try:
        # Validate amount
        if amount <= 0:
            await ctx.send("❌ XP amount must be positive!")
            return

        guild_id = ctx.guild.id
        user_id = member.id

        # Initialize guild/user data if needed
        if guild_id not in xp_data:
            xp_data[guild_id] = {}
        if user_id not in xp_data[guild_id]:
            xp_data[guild_id][user_id] = {
                "xp": 0,
                "level": 0,
                "last_msg": None
            }

        # Add XP and calculate new level
        user_data = xp_data[guild_id][user_id]
        old_level = user_data["level"]
        user_data["xp"] += amount
        user_data["level"] = calculate_level(user_data["xp"])

        # Create response embed
        embed = discord.Embed(
            title="✨ XP Added",
            description=f"Added {amount:,} XP to {member.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="New Total XP", 
            value=f"{user_data['xp']:,}", 
            inline=True
        )
        embed.add_field(
            name="New Level",
            value=str(user_data["level"]),
            inline=True
        )

        # Check for level up
        if user_data["level"] > old_level:
            embed.add_field(
                name="Level Up!",
                value=f"User advanced from level {old_level} to {user_data['level']}!",
                inline=False
            )

        await ctx.send(embed=embed)
        
        # Save XP data
        save_xp_data()

    except ValueError:
        await ctx.send("❌ Please provide a valid number for XP amount!")
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")










































@bot.event
async def on_raw_reaction_add(payload):
    """Handle reaction role addition"""
    try:
        if payload.user_id == bot.user.id:  # Ignore bot's own reactions
            return
            
        message_id = payload.message_id
        if message_id in reaction_roles:
            emoji = str(payload.emoji)
            if emoji in reaction_roles[message_id]:
                guild = bot.get_guild(payload.guild_id)
                if not guild:
                    return
                    
                role = guild.get_role(reaction_roles[message_id][emoji])
                if not role:
                    return
                    
                try:
                    member = await guild.fetch_member(payload.user_id)
                    if not member:
                        return
                        
                    await member.add_roles(role, reason="Reaction role")
                    
                    # Optional: Send DM confirmation
                    try:
                        await member.send(f"✅ You have been given the role: {role.name}")
                    except discord.Forbidden:
                        pass  # User has DMs disabled
                        
                except discord.Forbidden:
                    # Bot lacks permissions
                    channel = guild.get_channel(payload.channel_id)
                    if channel:
                        await channel.send(f"❌ I don't have permission to assign the {role.name} role!")
                except Exception as e:
                    print(f"Error in reaction role add: {str(e)}")
                    await send_log_dm(bot, f"Error in reaction role add: {str(e)}")
    except Exception as e:
        error_message = f"Error in reaction role add: {str(e)}"
        print(error_message)
        await send_log_dm(bot, error_message)

@bot.event
async def on_raw_reaction_remove(payload):
    """Handle reaction role removal"""
    try:
        if payload.user_id == bot.user.id:  # Ignore bot's own reactions
            return
            
        message_id = payload.message_id
        if message_id in reaction_roles:
            emoji = str(payload.emoji)
            if emoji in reaction_roles[message_id]:
                guild = bot.get_guild(payload.guild_id)
                if not guild:
                    return
                    
                role = guild.get_role(reaction_roles[message_id][emoji])
                if not role:
                    return
                    
                try:
                    member = await guild.fetch_member(payload.user_id)
                    if not member:
                        return
                        
                    await member.remove_roles(role, reason="Reaction role removed")
                    
                    # Optional: Send DM confirmation
                    try:
                        await member.send(f"❌ The role has been removed: {role.name}")
                    except discord.Forbidden:
                        pass  # User has DMs disabled
                        
                except discord.Forbidden:
                    # Bot lacks permissions
                    channel = guild.get_channel(payload.channel_id)
                    if channel:
                        await channel.send(f"❌ I don't have permission to remove the {role.name} role!")
                except Exception as e:
                    print(f"Error in reaction role remove: {str(e)}")
                    await send_log_dm(bot, f"Error in reaction role remove: {str(e)}")
    except Exception as e:
        error_message = f"Error in reaction role remove: {str(e)}"
        print(error_message)
        await send_log_dm(bot, error_message)

@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Handle XP system
    if message.guild:  # Only track XP for server messages
        guild_id = message.guild.id
        user_id = message.author.id
        
        # Initialize guild/user data if needed
        if guild_id not in xp_data:
            xp_data[guild_id] = {}
        if user_id not in xp_data[guild_id]:
            xp_data[guild_id][user_id] = {
                "xp": 0,
                "level": 0,
                "last_msg": None
            }
        
        # Check cooldown
        user_data = xp_data[guild_id][user_id]
        now = datetime.utcnow()
        if (user_data["last_msg"] is None or 
            (now - user_data["last_msg"]).total_seconds() >= XP_COOLDOWN):
            
            # Award XP
            xp_gained = random.randint(MIN_XP, MAX_XP)
            old_level = user_data["level"]
            user_data["xp"] += xp_gained
            user_data["level"] = calculate_level(user_data["xp"])
            user_data["last_msg"] = now
            
            # Check for level up
            if user_data["level"] > old_level:
                embed = discord.Embed(
                    title="🎉 Level Up!",
                    description=f"Congratulations {message.author.mention}!",
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="New Level", value=user_data["level"], inline=True)
                embed.add_field(name="Total XP", value=user_data["xp"], inline=True)
                await message.channel.send(embed=embed)
            
            # Save XP data periodically
            if random.random() < 0.1:  # 10% chance to save on each message
                save_xp_data()
    
    # Process autoresponders (your existing code)
    guild_id = message.guild.id
    if guild_id in autoresponders:
        content = message.content.lower()
        for trigger, data in autoresponders[guild_id].items():
            if trigger in content:
                if data["type"] == "text":
                    await message.channel.send(data["response"])
                else:  # reaction type
                    for emoji in data["response"]:
                        try:
                            await message.add_reaction(emoji)
                        except:
                            continue
                break
    
    # Process commands
    await bot.process_commands(message)

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    deleted_messages[message.channel.id] = {
        'author': message.author,
        'content': message.content,
        'timestamp': message.created_at,
        'attachments': [attachment.url for attachment in message.attachments]
    }

@bot.event
async def on_ready():
    global reaction_roles, xp_data
    reaction_roles = load_reaction_roles()
    xp_data = load_xp_data()
    print(f'Bot is ready! Logged in as {bot.user.name}')
    await bot.change_presence(activity=discord.Game(name="!help"))
    
    # Start auto-save task
    create_task(auto_save())

COMMAND_LIST = """
# Bot Commands List

## 🛡️ Moderation Commands
- `!moderate <action> <@user> [reason]` - Perform moderation actions (kick/ban/mute/unmute/warn)
- `!timeout <@user> <duration> [reason]` - Timeout a user for specified duration
- `!untimeout <@user> [reason]` - Remove timeout from a user
- `!purge <option> [args]` - Smart message purging with multiple options
- `!lockdown [#channel] [reason]` - Lock a channel
- `!unlock [#channel] [reason]` - Unlock a channel
- `!dm <@user> <message>` - Send a DM to a user

## 👥 User Management
- `!add_role <@user> <@role>` - Add a role to a user
- `!remove_role <@user> <@role>` - Remove a role from a user
- `!clonechannel [#channel] [message_count]` - Clone a channel with messages

## ℹ️ Information Commands
- `!serverinfo` - Display server information
- `!userinfo [@user]` - Display user information
- `!roleinfo <@role>` - Display role information
- `!avatar [@user]` - Show user's avatar
- `!ping` - Check bot's latency
- `!servers` - Show information about all servers the bot is in

## 🔍 Utility Commands
- `!define <word>` - Get word definition
- `!urbandict <term>` - Look up term on Urban Dictionary
- `!translate <lang_code> <text>` - Translate text
- `!snipe` - Show last deleted message
- `!tempchannel <type> <duration> <name>` - Create temporary channel
- `!invite [duration_hours] [max_uses]` - Create invite link
- `!coinflip [heads/tails]` - Play a coin flip game
- `!numguess` - Play a number guessing game

## ⚙️ Configuration Commands
- `!autoresponder <action> [args]` - Manage auto responses
- `!reactionrole <message_id> <emoji> <@role>` - Set up reaction roles
- `!save` - Save reaction roles configuration

## 🎮 XP System Commands
- `!rank [@user]` - Check XP rank and level
- `!leaderboard [page]` - Show server XP leaderboard
- `!givexp @user <amount>` - Give XP to a user (Admin only)

## 💡 Help
- `!help [command]` - Show help menu or command details
"""

command_list = COMMAND_LIST

bot.run(config.DISCORD_BOT_TOKEN)

