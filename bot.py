import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import aiosqlite
from datetime import datetime, timezone
import yfinance as yf

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

OWNER_ID = 123456789012345678   # ← Change to your Discord User ID

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = "economy.db"

# ====================== MARKET DATA ======================
STOCK_SYMBOLS = ["AAPL", "TSLA", "MSFT", "AMZN", "GOOGL", "NVDA", "META", "AMD"]
CRYPTO_SYMBOLS = ["BTC-USD", "ETH-USD"]
INDEX_SYMBOLS = ["^GSPC", "^IXIC", "^DJI", "^FTSE", "^N225"]

# ====================== LIVE PRICE ======================
def get_live_price(symbol: str):
    try:
        ticker = yf.Ticker(symbol.upper())
        price = ticker.info.get("regularMarketPrice")
        if price is None:
            hist = ticker.history(period="1d")
            if not hist.empty:
                price = hist["Close"].iloc[-1]
        return round(float(price)) if price else None
    except:
        return None

async def get_current_price(symbol: str) -> int:
    live = get_live_price(symbol)
    if live is not None:
        return live
    stock = await get_stock(symbol)
    return stock[2] if stock else 0

# ====================== DATABASE ======================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                registered_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_stocks (
                user_id INTEGER,
                symbol TEXT,
                shares REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, symbol)
            )
        """)
        await db.commit()

    await init_default_stocks()
    print("✅ Database initialized")

async def init_default_stocks():
    stocks = [
        ("AAPL", "Apple Inc.", 245), ("TSLA", "Tesla Inc.", 285),
        ("MSFT", "Microsoft Corporation", 420), ("AMZN", "Amazon.com Inc.", 195),
        ("GOOGL", "Alphabet Inc. (Google)", 175), ("NVDA", "NVIDIA Corporation", 125),
        ("META", "Meta Platforms Inc.", 510), ("AMD", "Advanced Micro Devices", 145),
    ]
    crypto = [
        ("BTC-USD", "Bitcoin", 65000),
        ("ETH-USD", "Ethereum", 3400),
    ]
    indices = [
        ("^GSPC", "S&P 500", 5500), ("^IXIC", "NASDAQ Composite", 18000),
        ("^DJI", "Dow Jones Industrial Average", 41000),
        ("^FTSE", "FTSE 100", 8200), ("^N225", "Nikkei 225", 39000),
    ]

    all_assets = stocks + crypto + indices

    async with aiosqlite.connect(DB_PATH) as db:
        for symbol, name, price in all_assets:
            await db.execute(
                "INSERT OR IGNORE INTO stocks (symbol, name, price) VALUES (?, ?, ?)",
                (symbol, name, price)
            )
        await db.commit()
    print(f"✅ Loaded {len(all_assets)} assets")

# ====================== USER FUNCTIONS ======================
async def is_registered(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        return await cur.fetchone() is not None

async def register_user(user_id: int) -> bool:
    if await is_registered(user_id):
        return False
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, balance, registered_at) VALUES (?, ?, ?)",
            (user_id, 10000, now)
        )
        await db.commit()
    return True

async def get_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0

async def update_balance(user_id: int, amount: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
    return await get_balance(user_id)

async def get_top_balances(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT ?", (limit,))
        return await cur.fetchall()

# ====================== STOCK FUNCTIONS ======================
async def get_stock(symbol: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT symbol, name, price FROM stocks WHERE symbol = ?", (symbol.upper(),))
        return await cur.fetchone()

async def get_all_stocks():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT symbol, name, price FROM stocks ORDER BY symbol")
        return await cur.fetchall()

async def update_stock_price(symbol: str, new_price: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE stocks SET price = ? WHERE symbol = ?", (new_price, symbol.upper()))
        await db.commit()

async def get_user_shares(user_id: int, symbol: str) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT shares FROM user_stocks WHERE user_id = ? AND symbol = ?", (user_id, symbol.upper()))
        row = await cur.fetchone()
        return row[0] if row else 0.0

async def get_user_portfolio(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT us.symbol, us.shares, s.name 
            FROM user_stocks us JOIN stocks s ON us.symbol = s.symbol 
            WHERE us.user_id = ? AND us.shares > 0
        """, (user_id,))
        return await cur.fetchall()

# ====================== BUY & SELL ======================
async def buy_shares(user_id: int, symbol: str, shares: float) -> tuple:
    price = await get_current_price(symbol)
    stock = await get_stock(symbol)
    if not stock or price == 0:
        return False, "Symbol unavailable.", 0
    name = stock[1]
    cost = shares * price
    if await get_balance(user_id) < cost:
        return False, f"Not enough money! Need **${cost:,}**.", await get_balance(user_id)
    new_bal = await update_balance(user_id, -cost)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_stocks (user_id, symbol, shares)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, symbol) DO UPDATE SET shares = shares + ?
        """, (user_id, symbol.upper(), shares, shares))
        await db.commit()
    return True, f"Bought **{shares}** {name} for **${cost:,}**!", new_bal

async def sell_shares(user_id: int, symbol: str, shares: float) -> tuple:
    price = await get_current_price(symbol)
    stock = await get_stock(symbol)
    if not stock or price == 0:
        return False, "Symbol unavailable.", 0
    owned = await get_user_shares(user_id, symbol)
    if owned < shares:
        return False, f"You only own **{owned}** of this asset.", await get_balance(user_id)
    revenue = shares * price
    new_bal = await update_balance(user_id, revenue)
    async with aiosqlite.connect(DB_PATH) as db:
        new_shares = owned - shares
        if new_shares <= 0:
            await db.execute("DELETE FROM user_stocks WHERE user_id = ? AND symbol = ?", (user_id, symbol.upper()))
        else:
            await db.execute("UPDATE user_stocks SET shares = ? WHERE user_id = ? AND symbol = ?", (new_shares, user_id, symbol.upper()))
        await db.commit()
    return True, f"Sold **{shares}** for **${revenue:,}**!", new_bal

# ====================== MARKET VIEW ======================
class MarketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    async def generate_embed(self, category: str):
        embed = discord.Embed(color=discord.Color.blue())
        if category == "stocks":
            symbols = STOCK_SYMBOLS
            embed.title = "📈 Stock Market"
        elif category == "crypto":
            symbols = CRYPTO_SYMBOLS
            embed.title = "🪙 Cryptocurrency Market"
        else:
            symbols = INDEX_SYMBOLS
            embed.title = "🌍 World Indices"

        for symbol in symbols:
            price = await get_current_price(symbol)
            try:
                ticker = yf.Ticker(symbol)
                name = ticker.info.get("shortName") or ticker.info.get("longName") or symbol
            except:
                name = symbol
            embed.add_field(name=f"{symbol} - {name}", value=f"**${price:,}**", inline=False)

        embed.set_footer(text="Data from Yahoo Finance • Click buttons to switch pages")
        return embed

    @discord.ui.button(label="Stocks", emoji="📈", style=discord.ButtonStyle.primary)
    async def stocks_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed = await self.generate_embed("stocks")
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Crypto", emoji="🪙", style=discord.ButtonStyle.success)
    async def crypto_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed = await self.generate_embed("crypto")
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Indices", emoji="🌍", style=discord.ButtonStyle.secondary)
    async def indices_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed = await self.generate_embed("indices")
        await interaction.edit_original_response(embed=embed, view=self)

# ====================== COMMANDS ======================

@bot.tree.command(name="register", description="Register and get $10,000 starter balance")
async def register(interaction: discord.Interaction):
    if await register_user(interaction.user.id):
        embed = discord.Embed(
            title="✅ Registration Successful!",
            description="Welcome to the economy system!\nYou received **$10,000** as your starter balance.",
            color=discord.Color.green()
        )
        embed.set_footer(text="Use /balance to check your money anytime")
        await interaction.response.send_message(embed=embed)
    else:
        embed = discord.Embed(
            title="❌ Already Registered",
            description="You're already in the system!",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="balance", description="Check your current balance")
async def balance(interaction: discord.Interaction):
    if not await is_registered(interaction.user.id):
        await interaction.response.send_message("Use `/register` first!", ephemeral=True)
        return
    bal = await get_balance(interaction.user.id)
    await interaction.response.send_message(f"💰 Your balance: **${bal:,}**")


@bot.tree.command(name="market", description="View live market (Stocks, Crypto & Indices)")
async def market(interaction: discord.Interaction):
    await interaction.response.defer()
    view = MarketView()
    embed = await view.generate_embed("stocks")
    await interaction.edit_original_response(embed=embed, view=view)


@bot.tree.command(name="buy", description="Buy stocks, crypto or indices")
@app_commands.describe(symbol="Symbol (e.g. BTC-USD, AAPL, ^GSPC)", amount="Amount to buy (supports decimals)")
async def buy(interaction: discord.Interaction, symbol: str, amount: float):
    if not await is_registered(interaction.user.id):
        await interaction.response.send_message("Please `/register` first!", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Amount must be greater than 0.", ephemeral=True)
        return

    success, message, new_balance = await buy_shares(interaction.user.id, symbol, amount)
    if success:
        embed = discord.Embed(title="✅ Purchase Successful", description=message, color=discord.Color.green())
        embed.add_field(name="New Balance", value=f"**${new_balance:,}**")
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="sell", description="Sell stocks, crypto or indices")
@app_commands.describe(symbol="Symbol", amount="Amount to sell (supports decimals)")
async def sell(interaction: discord.Interaction, symbol: str, amount: float):
    if not await is_registered(interaction.user.id):
        await interaction.response.send_message("Please `/register` first!", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Amount must be greater than 0.", ephemeral=True)
        return

    success, message, new_balance = await sell_shares(interaction.user.id, symbol, amount)
    if success:
        embed = discord.Embed(title="✅ Sale Successful", description=message, color=discord.Color.green())
        embed.add_field(name="New Balance", value=f"**${new_balance:,}**")
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="portfolio", description="View your investments with live prices")
async def portfolio(interaction: discord.Interaction):
    if not await is_registered(interaction.user.id):
        await interaction.response.send_message("Register first with `/register`!", ephemeral=True)
        return
    await interaction.response.defer()
    holdings = await get_user_portfolio(interaction.user.id)
    if not holdings:
        await interaction.edit_original_response(content="You don't own any assets yet.")
        return

    embed = discord.Embed(title=f"📊 {interaction.user.display_name}'s Portfolio", color=discord.Color.purple())
    total_value = 0
    for symbol, shares, name in holdings:
        price = await get_current_price(symbol)
        value = shares * price
        total_value += value
        embed.add_field(name=f"{symbol} ({name}) — {shares} shares", value=f"**${price:,}** each = **${value:,}**", inline=False)
    embed.add_field(name="💎 Total Portfolio Value", value=f"**${total_value:,}**", inline=False)
    await interaction.edit_original_response(embed=embed)


@bot.tree.command(name="leaderboard", description="Top 10 richest players by balance")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    top = await get_top_balances(10)
    if not top:
        await interaction.edit_original_response(content="No players registered yet.")
        return

    embed = discord.Embed(title="🏆 Economy Leaderboard", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    for i, (user_id, balance) in enumerate(top, 1):
        try:
            user = await bot.fetch_user(user_id)
            name = user.name
        except:
            name = f"User {user_id}"
        medal = medals[i-1] if i <= 3 else f"{i}."
        embed.add_field(name=f"{medal} {name}", value=f"**${balance:,}**", inline=False)
    embed.set_footer(text="Sorted by cash balance")
    await interaction.edit_original_response(embed=embed)


@bot.tree.command(name="pay", description="Send money to another user")
@app_commands.describe(user="User to pay", amount="Amount to send")
async def pay(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not await is_registered(interaction.user.id):
        await interaction.response.send_message("Register first with `/register`!", ephemeral=True)
        return
    if not await is_registered(user.id):
        await interaction.response.send_message("That user is not registered yet.", ephemeral=True)
        return
    if user.id == interaction.user.id:
        await interaction.response.send_message("You can't pay yourself!", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        return

    sender_bal = await get_balance(interaction.user.id)
    if sender_bal < amount:
        await interaction.response.send_message(f"You don't have enough money! You only have **${sender_bal:,}**.", ephemeral=True)
        return

    await update_balance(interaction.user.id, -amount)
    await update_balance(user.id, amount)

    embed = discord.Embed(title="💸 Payment Successful", color=discord.Color.green())
    embed.add_field(name="From", value=interaction.user.mention, inline=True)
    embed.add_field(name="To", value=user.mention, inline=True)
    embed.add_field(name="Amount", value=f"**${amount:,}**", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="setstockprice", description="Manually change a stock/crypto/index price (Owner only)")
@app_commands.describe(symbol="Symbol", new_price="New price")
async def setstockprice(interaction: discord.Interaction, symbol: str, new_price: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Only the bot owner can use this command.", ephemeral=True)
        return
    stock = await get_stock(symbol)
    if not stock:
        await interaction.response.send_message("Symbol not found.", ephemeral=True)
        return
    await update_stock_price(symbol, new_price)
    await interaction.response.send_message(f"✅ {stock[1]} price updated to **${new_price:,}**", ephemeral=True)


@bot.tree.command(name="ping", description="Test if the bot is working")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Pong! Bot is online.")

# ====================== BOT EVENTS ======================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await init_db()

    print(f"Number of commands currently registered: {len(bot.tree.get_commands())}")

    try:
        # Global sync (more reliable when guild sync fails)
        synced = await bot.tree.sync()          # ← Removed guild=
        print(f"🔄 Successfully synced {len(synced)} commands globally!")
        print("Note: Global commands can take up to 1 hour to appear.")
    except Exception as e:
        print(f"❌ Sync failed: {e}")

bot.run(TOKEN)