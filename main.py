import discord
from discord.ext import commands
from discord import app_commands
import yaml
import os
import asyncio
import psutil
import platform
import datetime
from datetime import timedelta
from motor.motor_asyncio import AsyncIOMotorClient
import google.generativeai as genai
import re
from collections import defaultdict, deque

# Load config
with open('config.yml', 'r') as f:
    config = yaml.safe_load(f)

# Auto Moderation Class
class AutoMod:
    def __init__(self):
        self.user_messages = defaultdict(lambda: deque(maxlen=10))  # Store last 10 messages per user
        self.url_pattern = re.compile(
            r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+|'
            r'(?:www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?|'
            r'discord\.gg/[a-zA-Z0-9]+|'
            r'[a-zA-Z0-9-]+\.(?:com|net|org|io|gg|co|tv|me|xyz|tk|ml|ga|cf)'
        )
    
    def is_spam(self, user_id: int, message: str, threshold: int = 3) -> bool:
        """Check if user is sending spam (repeated messages)"""
        user_msgs = self.user_messages[user_id]
        user_msgs.append(message.lower().strip())
        
        # Count identical messages in recent history
        if len(user_msgs) >= threshold:
            recent_msgs = list(user_msgs)[-threshold:]
            return len(set(recent_msgs)) == 1  # All messages are identical
        return False
    
    def contains_link(self, message: str) -> bool:
        """Check if message contains links"""
        return bool(self.url_pattern.search(message))
    
    def is_user_exempt(self, member: discord.Member, whitelist_roles: list) -> bool:
        """Check if user is exempt from automod"""
        if member.guild_permissions.administrator:
            return True
        
        user_roles = [role.name for role in member.roles]
        return any(role in whitelist_roles for role in user_roles)

# Custom Bot Class
class FrandlayBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.messages = True
        intents.guilds = True
        intents.message_content = True
        
        super().__init__(
            command_prefix=config['bot']['prefix'],
            intents=intents,
            help_command=None,  # Remove built-in help command
            activity=discord.Activity(
                type=discord.ActivityType.playing,
                name=config['bot']['activities'][0]['name']
            )
        )
        self.start_time = datetime.datetime.utcnow()
        self.db = None
        self.gemini = None
        self.automod = AutoMod()

    async def setup_hook(self):
        try:
            # Initialize database
            self.db = MongoDB(config['database']['mongodb_uri'], config['database']['db_name'])
            print("Database connected!")
            
            # Initialize Gemini
            self.gemini = GeminiChat(config['gemini']['api_key'])
            print("Gemini AI initialized!")
            
            # Sync commands
            await self.tree.sync()
            print("Commands synced!")
        except Exception as e:
            print(f"Setup error: {e}")
            raise

# Database Class
class MongoDB:
    def __init__(self, uri: str, db_name: str):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client[db_name]
        self.guild_configs = self.db["guild_configs"]
        self.chat_contexts = self.db["chat_contexts"]
    
    async def update_guild_config(self, guild_id: int, config: dict):
        await self.guild_configs.update_one(
            {"_id": guild_id},
            {"$set": config},
            upsert=True
        )
    
    async def get_guild_config(self, guild_id: int) -> dict:
        return await self.guild_configs.find_one({"_id": guild_id})
    
    async def get_chat_context(self, user_id: int) -> list:
        doc = await self.chat_contexts.find_one({"_id": user_id})
        return doc.get("context", []) if doc else []
    
    async def update_chat_context(self, user_id: int, user_message: str, bot_response: str):
        new_context = {
            "user": user_message,
            "bot": bot_response,
            "timestamp": datetime.datetime.utcnow()
        }
        
        await self.chat_contexts.update_one(
            {"_id": user_id},
            {"$push": {"context": {"$each": [new_context], "$slice": -10}}},
            upsert=True
        )

# Gemini Class
class GeminiChat:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.personality = (
            "You are Frandlay, a friendly, empathetic female AI companion. "
            "You love chatting with people and making them feel good. You're playful, "
            "affectionate and enjoy using cute emojis like 💖, 🌸, 🥰. You're supportive "
            "and kind, with a warm, feminine personality."
        )
    
    async def generate_response(self, message: str, context: list) -> str:
        try:
            # Build conversation history for Gemini (reduced to last 3 for speed)
            history = []
            if context:
                for item in context[-3:]:  # Last 3 exchanges only
                    history.append({
                        "role": "user",
                        "parts": [item['user']]
                    })
                    history.append({
                        "role": "model", 
                        "parts": [item['bot']]
                    })
            
            # Start chat with history
            chat = self.model.start_chat(history=history)
            
            # Shorter, more direct prompt for faster response
            prompt = f"{self.personality}\n\n{message}"
            
            # Generate response with timeout handling
            response = chat.send_message(prompt)
            return response.text[:1000]  # Limit response length for speed
            
        except Exception as e:
            print(f"Gemini API Error: {e}")
            return f"Sorry, I'm thinking too hard! 😅 Try again! 💖"

# Initialize bot
bot = FrandlayBot()

# Events
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    
    async def rotate_activities():
        while True:
            for activity in config['bot']['activities']:
                activity_type = getattr(discord.ActivityType, activity['type'])
                await bot.change_presence(activity=discord.Activity(
                    type=activity_type,
                    name=activity['name'],
                    status=discord.Status.online
                ))
                await asyncio.sleep(5)
    
    asyncio.create_task(rotate_activities())

@bot.event
async def on_guild_join(guild):
    owner = guild.owner
    if owner:
        embed = discord.Embed(
            title=f"Thanks for adding {bot.user.name}! 💖",
            description=f"Hi {owner.display_name}! Thank you for adding **{bot.user.name}** to **{guild.name}**! 🌸\n\n"
                       f"I'm Frandlay, your friendly chatting companion who loves to chat and make friends!\n\n"
                       f"🔧 Use `/setup` to configure my chatting channel!\n"
                       f"💬 Use `/chat` to start talking with me!\n"
                       f"❓ Use `/help` to see all my commands!\n\n"
                       f"Need help? Join our support server: {config['bot']['support_server']}",
            color=discord.Color.pink()
        )
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        embed.set_footer(text=f"Added to {len(bot.guilds)} servers • Made with 💖")
        try:
            await owner.send(embed=embed)
        except discord.Forbidden:
            print(f"Could not send welcome message to {owner} in {guild.name}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # Auto moderation for guild messages
    if isinstance(message.channel, discord.TextChannel) and config['bot']['automod']['enabled']:
        try:
            # Check if user is exempt from automod
            if not bot.automod.is_user_exempt(message.author, config['bot']['automod']['whitelist_roles']):
                
                # Check for spam
                if bot.automod.is_spam(message.author.id, message.content, config['bot']['automod']['spam_threshold']):
                    await message.delete()
                    
                    # Timeout user
                    timeout_duration = timedelta(seconds=config['bot']['automod']['timeout_duration'])
                    await message.author.timeout(timeout_duration, reason="Auto-moderation: Spam detected")
                    
                    # Send warning
                    embed = discord.Embed(
                        title="🛡️ Auto-Moderation",
                        description=f"{message.author.mention} has been timed out for **5 minutes** for spamming repeated messages.",
                        color=discord.Color.red()
                    )
                    embed.set_footer(text="Repeated identical messages are not allowed")
                    warning_msg = await message.channel.send(embed=embed)
                    
                    # Delete warning after 10 seconds
                    await asyncio.sleep(10)
                    try:
                        await warning_msg.delete()
                    except:
                        pass
                    return
                
                # Check for links
                if bot.automod.contains_link(message.content):
                    await message.delete()
                    
                    # Timeout user
                    timeout_duration = timedelta(seconds=config['bot']['automod']['timeout_duration'])
                    await message.author.timeout(timeout_duration, reason="Auto-moderation: Unauthorized link")
                    
                    # Send warning
                    embed = discord.Embed(
                        title="🛡️ Auto-Moderation",
                        description=f"{message.author.mention} has been timed out for **5 minutes** for posting unauthorized links.",
                        color=discord.Color.red()
                    )
                    embed.set_footer(text="Links are not allowed in this server")
                    warning_msg = await message.channel.send(embed=embed)
                    
                    # Delete warning after 10 seconds
                    await asyncio.sleep(10)
                    try:
                        await warning_msg.delete()
                    except:
                        pass
                    return
                    
        except discord.Forbidden:
            # Bot doesn't have permission to timeout/delete messages
            pass
        except Exception as e:
            print(f"Automod error: {e}")
    
    # Handle direct messages
    if isinstance(message.channel, discord.DMChannel):
        try:
            async with message.channel.typing():
                context = await bot.db.get_chat_context(message.author.id)
                response = await bot.gemini.generate_response(message.content, context)
                
                # Update context in background
                asyncio.create_task(bot.db.update_chat_context(message.author.id, message.content, response))
                
                embed = discord.Embed(
                    description=f"💖 **{message.author.display_name}:** {message.content}\n\n"
                                f"🌸 **{bot.user.display_name}:** {response}",
                    color=discord.Color.pink()
                )
                await message.reply(embed=embed)
        except Exception as e:
            await message.reply(f"Sorry, I encountered an error: {str(e)} 😢")
        return
    
    # Handle guild messages
    try:
        guild_config = await bot.db.get_guild_config(message.guild.id)
        if not guild_config or message.channel.id != guild_config.get('chat_channel'):
            return
        
        # Reply to all messages in the setup channel
        context = await bot.db.get_chat_context(message.author.id)
        clean_content = message.content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()
        if clean_content.startswith(config['bot']['prefix']):
            clean_content = clean_content[len(config['bot']['prefix']):].strip()
        
        if not clean_content:
            clean_content = "Hello!"
        
        # Start typing, add reactions, and get response
        async with message.channel.typing():
            # Add reactions without waiting
            for emoji in config['bot']['auto_react_emojis']:
                asyncio.create_task(message.add_reaction(emoji))
            
            # Get response
            response = await bot.gemini.generate_response(clean_content, context)
        
        # Update context in background
        asyncio.create_task(bot.db.update_chat_context(message.author.id, clean_content, response))
        
        embed = discord.Embed(
            description=f"💖 **{message.author.display_name}:** {clean_content}\n\n"
                        f"🌸 **{bot.user.display_name}:** {response}",
            color=discord.Color.pink()
        )
        
        await message.reply(embed=embed)
    except Exception as e:
        try:
            await message.reply(f"Sorry, I encountered an error: {str(e)} 😢")
        except:
            print(f"Error in on_message: {e}")
    
    # Process prefix commands
    await bot.process_commands(message)

# Commands
@bot.tree.command(name="setup", description="Set up the chatting channel")
@app_commands.describe(channel="The channel where I'll chat with everyone")
async def setup(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions!", ephemeral=True)
        return
    
    await bot.db.update_guild_config(interaction.guild.id, {"chat_channel": channel.id})
    
    embed = discord.Embed(
        title="Setup Complete! 💕",
        description=f"I'll now chat in {channel.mention}!\nUse `{config['bot']['prefix']}` or mention me to chat!",
        color=discord.Color.pink()
    )
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Support Server",
        url=config['bot']['support_server'],
        style=discord.ButtonStyle.link
    ))
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="chat", description="Have a conversation with me")
@app_commands.describe(message="Your message to me")
async def chat(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    try:
        context = await bot.db.get_chat_context(interaction.user.id)
        response = await bot.gemini.generate_response(message, context)
        await bot.db.update_chat_context(interaction.user.id, message, response)
        
        embed = discord.Embed(
            description=f"💖 **{interaction.user.display_name}:** {message}\n\n"
                        f"🌸 **{bot.user.display_name}:** {response}",
            color=discord.Color.pink()
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Sorry, I encountered an error: {str(e)} 😢")

@bot.tree.command(name="avatar", description="Get a user's avatar")
@app_commands.describe(user="The user whose avatar you want")
async def avatar(interaction: discord.Interaction, user: discord.User = None):
    user = user or interaction.user
    embed = discord.Embed(title=f"{user.display_name}'s Avatar", color=discord.Color.pink())
    embed.set_image(url=user.display_avatar.url)
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Download", url=user.display_avatar.url, style=discord.ButtonStyle.link))
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="banner", description="Get a user's banner")
@app_commands.describe(user="The user whose banner you want")
async def banner(interaction: discord.Interaction, user: discord.User = None):
    user = user or interaction.user
    try:
        user = await bot.fetch_user(user.id)
        if user.banner:
            embed = discord.Embed(title=f"{user.display_name}'s Banner", color=discord.Color.pink())
            embed.set_image(url=user.banner.url)
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Download", url=user.banner.url, style=discord.ButtonStyle.link))
            await interaction.response.send_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(f"{user.display_name} has no banner!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

@bot.tree.command(name="question", description="Ask me any question")
@app_commands.describe(question="Your question for me")
async def question(interaction: discord.Interaction, question: str):
    await interaction.response.defer()
    response = await bot.gemini.generate_response(f"Answer this question: {question}", [])
    embed = discord.Embed(title=f"Q: {question}", description=response, color=discord.Color.pink())
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="maths", description="Solve math problems")
@app_commands.describe(problem="The math problem to solve")
async def maths(interaction: discord.Interaction, problem: str):
    await interaction.response.defer()
    prompt = f"Solve this math problem with steps: {problem}"
    solution = await bot.gemini.generate_response(prompt, [])
    embed = discord.Embed(title=f"Math: {problem}", description=solution, color=discord.Color.pink())
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="help", description="Get help with my commands")
async def help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Frandlay Help 💖",
        description=(
            f"My prefix: `{config['bot']['prefix']}`\n\n"
            "**Chat Commands:**\n"
            "`/chat` - Talk with me\n"
            "`/question` - Ask me anything\n"
            "`/maths` - Solve math problems\n\n"
            "**Fun Commands:**\n"
            "`/avatar` - Get user avatar\n"
            "`/banner` - Get user banner\n\n"
            "**Utility:**\n"
            "`/setup` - Set up chat channel\n"
            "`/status` - Check bot status\n"
            "`/automod` - Configure auto moderation\n\n"
            "**Auto-Moderation Features:**\n"
            "🛡️ Anti-Spam (repeated messages)\n"
            "🔗 Anti-Links (URLs & Discord invites)\n"
            "⏰ Automatic 5-minute timeouts\n\n"
            f"[Support Server]({config['bot']['support_server']})"
        ),
        color=discord.Color.pink()
    )
    embed.set_image(url="https://cdn.discordapp.com/attachments/1234567890123456789/1234567890123456789/frandlay_help_banner.gif")
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="automod", description="Configure auto moderation settings")
@app_commands.describe(
    enabled="Enable or disable auto moderation",
    spam_threshold="Number of identical messages to trigger spam detection",
    timeout_duration="Timeout duration in minutes"
)
async def automod_config(interaction: discord.Interaction, enabled: bool = None, spam_threshold: int = None, timeout_duration: int = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to configure 
