import asyncio
import json
import os
import re
import time

import discord
from discord import app_commands
import sqlite3
from datetime import datetime

from discord.ext import tasks
from discord.utils import escape_markdown
from dotenv import load_dotenv  # Python-dotenv package

from StartGG import get_games, get_tournament_info
from art import create_square_ratio_bar

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
load_dotenv()

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
guildId = int(os.getenv("GUILD_ID"))
betViews = {}

# Internal function to check if a user has permission to use certain commands
async def _isAdmin(userId):
    adminFile = open("admins.json")
    adminData = json.load(adminFile)
    adminFile.close()

    guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
    member = guild.get_member(userId) if guild.get_member(userId) is not None else await guild.fetch_member(userId)

    if member.guild_permissions.administrator:
        return True

    for adminId in adminData["users"]:
        if userId == adminId:
            return True

    roleIds = [role.id for role in member.roles]

    for roleId in adminData["roles"]:
        if roleId in roleIds:
            return True

    return False

# Function to remove points
async def _remove_points(userId, points, update=True):
    conn = sqlite3.connect('database.db', autocommit=True)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (id) VALUES (?);', (userId,))
    cursor.execute('UPDATE users SET points = points - ? WHERE id = ?;', (points, userId))
    conn.close()
    if update:
        await viewHelper.update_leaderboard()

# Function to give points
async def _give_points(userId, points, update=True):
    conn = sqlite3.connect('database.db', autocommit=True)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (id) VALUES (?);', (userId,))
    cursor.execute('UPDATE users SET points = points + ? WHERE id = ?;', (points, userId))
    conn.close()
    if update:
        await viewHelper.update_leaderboard()

# Helper function to format a statistic for the leaderboard from the db
async def _get_leaderboard(stat="points") -> list:
    guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
    con = sqlite3.connect('database.db')
    cur = con.cursor()
    cur.execute(f'SELECT id,{stat} FROM users ORDER BY {stat} DESC LIMIT 10')
    results = cur.fetchall()
    leaderboard = []

    for result in results:
        member = guild.get_member(result[0]) if guild.get_member(result[0]) is not None else await guild.fetch_member(
            result[0])
        leaderboard.append([member, result[1]])

    con.close()

    return leaderboard


# def _get_points_batch(playerIds):
#     con = sqlite3.connect('database.db')
#     cur = con.cursor()
#     cur.execute(f'SELECT points FROM users WHERE id = {playerIds}')
#     con.close()
#
#     return cur.fetchall()[0]


# Formats the get
def __format_leaderboard__(data):
    content = ""
    for index, stat in enumerate(data):
        content += f"{index}. {stat[0]}: {stat[1]}\n"
    return content

# Helper to keep the scoreboard updated and accurate
# It's not really a view, but it was once upon a time, so you can fight me
class ViewHelperScoreboard:
    def __init__(self, view, channelId, messageId=None):
        self.view = view
        self.channelId = channelId
        self.messageId = messageId
        self.embed = discord.Embed(title="Live Leaderboard", colour=discord.Colour.from_str("#F60143"))
        self.indexOfField = 1
        self.lastUpdate = time.time()

    async def update_leaderboard(self):
        if time.time() - self.lastUpdate > 60:
            channel = await bot.fetch_channel(self.channelId)
            message = await channel.fetch_message(self.messageId)

            self.embed = discord.Embed(title="Live Leaderboard", colour=discord.Colour.from_str("#F60143"))
            self.embed.add_field(name="WindCoin", inline=False, value=__format_leaderboard__(await _get_leaderboard()))
            self.embed.set_footer(text=f"Last Updated at {datetime.now().strftime('%m/%d %H:%M:%S')} NZST")

            await message.edit(embed=self.embed)
            self.lastUpdate = time.time()

    async def post_view(self):
        channel = await bot.fetch_channel(self.channelId)
        messagesData = {}
        sentMessage = await channel.send(embed=self.embed, view=self.view)
        self.messageId = sentMessage.id
        bot.add_view(self.view)
        messagesData[channel.id] = sentMessage.id
        with open("leaderboard.json", 'w') as outfile:
            json.dump(messagesData, outfile)
        await self.update_leaderboard()


viewHelper = ViewHelperScoreboard(None, None)


# The UI form that is created upon clicking a bet on button
class BetEntryForm(discord.ui.Modal, title="Bet on set"):
    def __init__(self, playerBetOn, setId):
        super().__init__()
        self.playerBetOn = playerBetOn
        self.setId = setId

    bet = discord.ui.TextInput(label=f"How much would you like to bet?")

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        betAmount = re.sub("[^0-9]", "", self.bet.value)
        try:
            betAmount = int(betAmount)
        except ValueError:
            await interaction.followup.send(f"Failed to bet, {self.bet.value} is not recognised as a number", ephemeral=True)
            return

        if betAmount == 0:
            await interaction.followup.send(f"You cannot bet 0 WindCoin", ephemeral=True)
            return

        con = sqlite3.connect('database.db', autocommit=True)
        cur = con.cursor()

        cur.execute('INSERT OR IGNORE INTO users (id) VALUES (?);', (interaction.user.id,))

        cur.execute('SELECT amount,winner FROM bets WHERE setId = ? AND userId = ?', (self.setId, interaction.user.id))
        betsMatching = cur.fetchall()

        if len(betsMatching) > 0:
            cur.execute('SELECT namePlayerOne,namePlayerTwo FROM sets WHERE setId = ?',
                        (self.setId,))
            players = cur.fetchall()[0]

            playerBetOn = players[betsMatching[0][1]]
            if playerBetOn != self.playerBetOn:
                # print(betsMatching[0][1])
                # print(self.playerBetOn)
                await interaction.followup.send(
                    f"You cannot bet on {self.playerBetOn} as you have already bet on {playerBetOn}",
                    ephemeral=True)
                con.close()
                return

        cur.execute('SELECT points FROM users WHERE id = ?', (interaction.user.id,))
        numPoints = cur.fetchall()[0][0]

        if betAmount > numPoints:
            await interaction.followup.send(f"You cannot bet {betAmount}, as you have {numPoints} points",
                                            ephemeral=True)
            con.close()
            return

        cur.execute('UPDATE users SET points = points - ? WHERE id = ?', (betAmount, interaction.user.id))

        cur.execute('SELECT * FROM sets WHERE setId = ?', (self.setId,))
        result = cur.fetchall()[0]

        # yuck
        if self.playerBetOn == result[5]:
            playerBet = "One"
            indexBet = 0
        else:
            playerBet = "Two"
            indexBet = 1

        queryVarBet = f"betsPlayer{playerBet}"

        cur.execute(f'UPDATE sets SET {queryVarBet} = {queryVarBet} + ? WHERE setId = ?', (betAmount, self.setId))

        if len(betsMatching) == 0:
            cur.execute('INSERT INTO bets (userId, setId, winner, amount) VALUES (?, ?, ?, ?);',
                        (interaction.user.id, self.setId, indexBet, betAmount))
            cur.execute('SELECT points FROM users WHERE id = ?', (interaction.user.id,))
            numPoints = cur.fetchall()[0][0]
            await interaction.followup.send(
                f"Bet {betAmount} WindCoin{"s" if betAmount > 1 else ""} on {self.playerBetOn}. Your balance is now {numPoints}", ephemeral=True)
        else:
            cur.execute('UPDATE bets SET amount = amount + ? WHERE setId = ? AND userId = ?',
                        (betAmount, self.setId, interaction.user.id))
            cur.execute('SELECT points FROM users WHERE id = ?', (interaction.user.id,))
            numPoints = cur.fetchall()[0][0]
            await interaction.followup.send(

                f"Bet an extra {betAmount} WindCoin{"s" if betAmount > 1 else ""} on {self.playerBetOn}. Your total bet is now {betsMatching[0][0] + betAmount}. Your remaining balance is now {numPoints}",
                ephemeral=True)

        con.close()

        await addToAuditLog(f"{interaction.user.display_name} bet {betAmount} on {self.playerBetOn} in the set {result[8]}: {result[7]}")

        global betViews
        await betViews[self.setId].update()

# Reconnect all the bet views when the bot restarts
async def reconnectBetViews():
    con = sqlite3.connect('database.db')
    cur = con.cursor()

    cur.execute('SELECT * FROM sets')
    sets = cur.fetchall()

    con.close()

    for betSet in sets:
        view = BetView(betSet[5], betSet[6], betSet[0], betSet[11], betSet[12] == 1, betSet[13] == 1)
        betViews[betSet[0]] = view
        bot.add_view(view)
        await view.update()

# The controller for the bet messages
class BetView(discord.ui.View):
    def __init__(self, playerOneName, playerTwoName, setId, timestamp, started=False, ended=False):
        super().__init__(timeout=None)
        self.playerOneName = playerOneName
        self.playerTwoName = playerTwoName
        self.setId = setId
        self.timeout = None
        self.hasStarted = started
        self.hasEnded = ended
        self.timeStarted = timestamp

        playerOneButton = discord.ui.Button(label=f'Bet for {self.playerOneName} to win',
                                            style=discord.ButtonStyle.green,
                                            custom_id=f'{self.setId}:{self.playerOneName}')
        playerOneButton.callback = self.playerOne
        self.add_item(playerOneButton)

        playerTwoButton = discord.ui.Button(label=f'Bet for {self.playerTwoName} to win',
                                            style=discord.ButtonStyle.blurple,
                                            custom_id=f'{self.setId}:{self.playerTwoName}')
        playerTwoButton.callback = self.playerTwo
        self.add_item(playerTwoButton)

        if self.hasEnded:
            self.endGame()

    # Handles the interaction to bet for player one
    async def playerOne(self, interaction):
        if self.hasStarted:
            return await interaction.response.send_message("This game has started, you can no longer bet on it.",
                                                           ephemeral=True)

        await interaction.response.send_modal(BetEntryForm(self.playerOneName, self.setId))
        await self.update()

    # Handles the interaction to bet for player two
    async def playerTwo(self, interaction):
        if self.hasStarted:
            return await interaction.response.send_message("This game has started, you can no longer bet on it.",
                                                           ephemeral=True)

        await interaction.response.send_modal(BetEntryForm(self.playerTwoName, self.setId))
        await self.update()

    # Internal helper function to update the message
    async def updateMessageObject(self, message: discord.Message):
        con = sqlite3.connect('database.db', autocommit=True)
        cur = con.cursor()

        cur.execute('UPDATE sets SET messageId = ?, channelId = ? WHERE setId = ?',
                    (message.id, message.channel.id, self.setId))

        con.close()
        await self.update(updateScoreboard=False)

    # Processes the score from the start.gg API
    async def updateScore(self, playerOneScore, playerTwoScore):

        con = sqlite3.connect('database.db', autocommit=True)
        cur = con.cursor()

        if type(playerOneScore) is not int:
            playerOneScore = 0

        if type(playerTwoScore) is not int:
            playerTwoScore = 0

        if not self.hasStarted and playerOneScore + playerTwoScore > 0:
            await self.startGame(override=True)

        cur.execute('SELECT scorePlayerOne,scorePlayerTwo FROM sets WHERE setId = ?', (self.setId,))
        result = cur.fetchall()

        if playerOneScore == result[0] and playerTwoScore == result[1]:
            con.close()
            return

        cur.execute('UPDATE sets SET scorePlayerOne = ?, scorePlayerTwo = ? WHERE setId = ?',
                    (playerOneScore, playerTwoScore, self.setId))
        con.close()
        await self.update()

    # Called after the game has started, will be called every time as the bet is stored in memory and not DB
    async def startGame(self, update=True, override=False):
        # Can be forcefully overridden, but should give at least 2 minutes for game to start
        if not self.hasStarted and ((round(time.time()) - self.timeStarted) > 120 or override):
            con = sqlite3.connect('database.db', autocommit=True)
            cur = con.cursor()
            cur.execute('UPDATE sets SET started = 1 WHERE setId = ?', (self.setId,))
            con.close()

            self.clear_items()
            self.hasStarted = True
            await addToAuditLog(f"{self.setId} has started")
            if update:
                await self.update()

    # Generic update handler
    async def update(self, updateScoreboard=True):
        if updateScoreboard:
            await viewHelper.update_leaderboard()

        guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)

        con = sqlite3.connect('database.db')
        cur = con.cursor()

        cur.execute(
            'SELECT messageId, channelId, setTitle, gameTitle, betsPlayerOne, betsPlayerTwo, scorePlayerOne, scorePlayerTwo FROM sets WHERE setId = ?',
            (self.setId,))
        result = cur.fetchall()[0]

        con.close()
        channel = guild.get_channel(result[1])
        if channel is None:
            channel = await guild.fetch_channel(result[1])

        message = await channel.fetch_message(result[0])

        embed = discord.Embed(title=f"{result[3]} - {result[2]}", colour=discord.Colour.from_str("#F62143"))
        embed.add_field(name=discord.utils.escape_markdown(self.playerOneName), inline=True, value=f"\n**Score**\n{result[6]} \n\n**Bets**\n{result[4]} WindCoin\n")
        embed.add_field(name='', value="vs", inline=True)
        embed.add_field(name=discord.utils.escape_markdown(self.playerTwoName), inline=True, value=f"\n**Score**\n{result[7]} \n\n**Bets**\n{result[5]} WindCoin\n")
        embed.add_field(name='Bet Ratio:', value="", inline=False)
        embed.set_image(url=f'attachment://{self.setId}.png')

        create_square_ratio_bar(result[4], result[5], f"{self.setId}.png")

        file = discord.File(f"{self.setId}.png")

        if self.hasStarted:
            embed.set_footer(text="Game has started, Betting no longer allowed")
            await message.edit(embed=embed, view=None)
            return

        await message.edit(embed=embed, attachments=[file])

        os.remove(f"{self.setId}.png")

    # Same as start game but for game finishing
    async def endGame(self):

        if not self.hasEnded:
            self.hasEnded = True
            await self.update()
            con = sqlite3.connect('database.db', autocommit=True)
            cur = con.cursor()

            cur.execute(
                'SELECT userId, winner, amount FROM bets WHERE setId = ?',
                (self.setId,)
            )
            bets = cur.fetchall()

            cur.execute('UPDATE sets SET ended = 1 WHERE setId = ?', (self.setId,))

            cur.execute(
                'SELECT betsPlayerOne, betsPlayerTwo, scorePlayerOne, scorePlayerTwo, namePlayerOne, namePlayerTwo, setTitle, gameTitle amount FROM sets WHERE setId = ?',
                (self.setId,)
            )
            info = cur.fetchall()[0]
            gameTitle = info[7]
            setTitle = info[6]
            playerOne = info[4]
            playerTwo = info[5]

            cur.execute("DELETE FROM bets WHERE setId = ?", (self.setId,))
            totalPayout = info[0] + info[1]

            winner = 0 if info[2] > info[3] else 1

            for bet in bets:
                if bet[1] == winner:

                    amount = bet[2]

                    payout = round((totalPayout / info[winner]) * amount)

                    if payout < amount*2:
                        payout = amount*2

                    await _give_points(bet[0], payout, False)

                    cur.execute(
                        'SELECT points FROM users WHERE id = ?',
                        (bet[0],)
                    )
                    bal = cur.fetchall()[0][0]
                    guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)

                    member = guild.get_member(bet[0]) if guild.get_member(bet[0]) is not None else await guild.fetch_member(bet[0])
                    await member.send(f"You won {amount} WindCoin from the bet placed on {escape_markdown(playerOne)} vs {escape_markdown(playerTwo)} (**{gameTitle}** - {setTitle}) \nYour total is now {bal}")
                    await addToAuditLog(f"{member.display_name} won {amount} WindCoin from the bet placed on {escape_markdown(playerOne)} vs {escape_markdown(playerTwo)} (**{gameTitle}** - {setTitle})")

            await viewHelper.update_leaderboard()


# Startup event
@bot.event
async def on_ready():
    # await tree.sync(guild=discord.Object(id=guildId))
    global viewHelper

    try:
        with open("leaderboard.json") as infile:
            messagesData = json.load(infile)
    except json.decoder.JSONDecodeError:
        messagesData = {}

    for channelId in messagesData:
        channelObject = await bot.fetch_channel(channelId)
        message = await channelObject.fetch_message(messagesData[channelId])
        view = discord.ui.View.from_message(message, timeout=None)
        if view is None:
            messagesData.pop(channelId)
        bot.add_view(view)
        viewHelper = ViewHelperScoreboard(view, channelObject.id, messageId=message.id)

    await reconnectBetViews()

    if os.path.exists("eventData.json"):
        pass
        # await updateGames()
        updateGames.start()

    print(f'Logged in as {bot.user.name}')
    print('------')

    if viewHelper is not None:
        await viewHelper.update_leaderboard()


@tree.command(name="give-points", description="Admin command to give a user points", guild=discord.Object(id=guildId))
async def give_points(interaction, member: discord.Member, points: int):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return
    await addToAuditLog(f"{interaction.user.display_name} (ADMIN) granted {points} points to {member.display_name} ")
    await _give_points(member.id, points)
    await interaction.followup.send(f"Added {points} points to {member.display_name}")


@tree.command(name="remove-points", description="Admin command to remove a user's points", guild=discord.Object(id=guildId))
async def remove_points(interaction, member: discord.Member, points: int):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return
    await addToAuditLog(f"{interaction.user.display_name} (ADMIN) removed {points} points from {member.display_name} ")
    await _remove_points(member.id, points)
    await interaction.followup.send(f"Removed {points} points from {member.display_name}")

# Internal transfer points function, will trust any information passed to it
async def _transfer_points(fromMemberId, toMemberId, num):
    guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
    await _remove_points(fromMemberId, num, update=False)
    await _give_points(toMemberId, num)
    toUser = guild.get_member(toMemberId) if guild.get_member(toMemberId) is not None else await guild.fetch_member(
        toMemberId)
    fromUser = guild.get_member(fromMemberId) if guild.get_member(
        fromMemberId) is not None else await guild.fetch_member(fromMemberId)
    await toUser.send(f"{fromUser.display_name} has sent you {num} points!")


@tree.command(name="transfer-points", description="Transfers your points to a user. Alias of pay-member",
              guild=discord.Object(id=guildId))
async def transfer_points(interaction, member: discord.Member, points: int):
    await interaction.response.defer(ephemeral=True)
    if points < 0:
        await interaction.followup.send(f"Tsk Tsk")
        return

    await addToAuditLog(f"{interaction.user.display_name} sent {member.display_name} {points} points")
    await _transfer_points(interaction.user.id, member.id, points)
    await interaction.followup.send(f"Sent {member.display_name} {points} points!")


@tree.command(name="pay-member", description="Transfers your points to a user. Alias of transfer-points",
              guild=discord.Object(id=guildId))
async def pay_member(interaction, member: discord.Member, points: int):
    await interaction.response.defer(ephemeral=True)
    if points < 0:
        await interaction.followup.send(f"Tsk Tsk")
        return

    await addToAuditLog(f"{interaction.user.display_name} sent {member.display_name} {points} points")
    await _transfer_points(interaction.user.id, member.id, points)
    await interaction.followup.send(f"Sent {member.display_name} {points} points!")


@tree.command(name="leaderboard", description="Gets the top 10 points collectors", guild=discord.Object(id=guildId))
async def post_leaderboard(interaction):
    await interaction.response.defer(ephemeral=True)
    leaderboard = "Points Leaderboard:"
    for member, points in await _get_leaderboard():
        leaderboard += f"\n* {member.display_name}: {points}"
    await interaction.followup.send(leaderboard)

# Allows users to check ballance
@tree.command(name="balance", description="Gets your balance", guild=discord.Object(id=guildId))
async def check_balance(interaction):
    await interaction.response.defer(ephemeral=True)
    con = sqlite3.connect('database.db')
    cur = con.cursor()
    cur.execute('SELECT points FROM users WHERE id = ?', (interaction.user.id,))
    results = cur.fetchall()
    con.close()

    if len(results) < 1:
        await interaction.followup.send("No record found")
        return

    await interaction.followup.send(f"You have {results[0][0]} points")

# Creates the points leaderboard
@tree.command(name="create-live-leaderboard", description="Create the live leaderboard",
              guild=discord.Object(id=guildId))
async def create_live_leaderboard(interaction):
    await interaction.response.defer()
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command", ephemeral=True)
        return
    view = discord.ui.View(timeout=None)
    vh = ViewHelperScoreboard(view, interaction.channel.id)
    global viewHelper
    viewHelper = vh
    await vh.post_view()
    await interaction.followup.send(f"Creating post", ephemeral=True, delete_after=3, silent=True)

#########################################################
# This bot uses an internal admin system. Users with Admin permissions from the server are also granted these permissions
#########################################################
@tree.command(name="add-admin-user", description="Adds a member to the list of admins",
              guild=discord.Object(id=guildId))
async def add_admin_user(interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    if os.path.exists("eventData.json"):
        guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
        with open("eventData.json") as data:
            jsonData = json.load(data)
            channel = guild.get_channel(jsonData["auditId"]) if guild.get_channel(jsonData["auditId"]) is not None else await guild.fetch_channel(jsonData["auditId"])
            await channel.set_permissions(member, read_messages=True)

    adminFile = open("admins.json")
    adminData = json.load(adminFile)

    adminData["users"].append(member.id)
    adminFile.close()

    adminFile = open("admins.json", "w")
    json.dump(adminData, adminFile)
    adminFile.close()

    await interaction.followup.send(f"Added {member.display_name} to the admin list")


@tree.command(name="remove-admin-user", description="Removes a member from the list of admins",
              guild=discord.Object(id=guildId))
async def remove_admin_user(interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    if os.path.exists("eventData.json"):
        guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
        with open("eventData.json") as data:
            jsonData = json.load(data)
            channel = guild.get_channel(jsonData["auditId"]) if guild.get_channel(jsonData["auditId"]) is not None else await guild.fetch_channel(jsonData["auditId"])
            await channel.set_permissions(member, read_messages=False)

    adminFile = open("admins.json")
    adminData = json.load(adminFile)

    try:
        adminData["users"].remove(member.id)
    except ValueError:
        pass

    adminFile.close()
    adminFile = open("admins.json", "w")
    json.dump(adminData, adminFile)
    adminFile.close()

    await interaction.followup.send(f"Removed {member.display_name} from the admin list")


@tree.command(name="add-admin-role", description="Adds a role to the list of roles that count as admins",
              guild=discord.Object(id=guildId))
async def add_admin_role(interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    if os.path.exists("eventData.json"):
        guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
        with open("eventData.json") as data:
            jsonData = json.load(data)
            channel = guild.get_channel(jsonData["auditId"]) if guild.get_channel(jsonData["auditId"]) is not None else await guild.fetch_channel(jsonData["auditId"])
            await channel.set_permissions(role, read_messages=True)

    adminFile = open("admins.json")
    adminData = json.load(adminFile)

    adminData["roles"].append(role.id)

    adminFile.close()
    adminFile = open("admins.json", "w")
    json.dump(adminData, adminFile)
    adminFile.close()

    await interaction.followup.send(f"Added {role.name} to the admin list")


@tree.command(name="remove-admin-role", description="Removes a role to the list from roles that count as admins",
              guild=discord.Object(id=guildId))
async def remove_admin_role(interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    if os.path.exists("eventData.json"):
        guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
        with open("eventData.json") as data:
            jsonData = json.load(data)
            channel = guild.get_channel(jsonData["auditId"]) if guild.get_channel(jsonData["auditId"]) is not None else await guild.fetch_channel(jsonData["auditId"])
            await channel.set_permissions(role, read_messages=False)

    adminFile = open("admins.json")
    adminData = json.load(adminFile)

    try:
        adminData["roles"].remove(role.id)
    except ValueError:
        pass

    adminFile.close()
    adminFile = open("admins.json", "w")
    json.dump(adminData, adminFile)
    adminFile.close()

    await interaction.followup.send(f"Removed {role.name} from the admin list")


# Command that inits the tournament
@tree.command(name="setup-tournament", guild=discord.Object(id=guildId))
async def setup_tournament(interaction, tournament_stub: str):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    tournament = await get_tournament_info(tournament_stub)
    if tournament is None:
        await interaction.followup.send(f"Could not get info from StartGG")
        return

    guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)

    con = sqlite3.connect('database.db', autocommit=True)
    cur = con.cursor()
    for member in guild.members:
        if not member.bot:
            cur.execute('INSERT OR IGNORE INTO users (id) VALUES (?);', (member.id,))
    con.close()

    await viewHelper.update_leaderboard()

    jsonData = {
        "stub": tournament_stub
    }


    category = await guild.create_category(tournament["name"])
    jsonData["categoryId"] = category.id

    auditLog = await category.create_text_channel("audit-log")

    await auditLog.set_permissions(guild.default_role, read_messages=False, send_messages=False)

    adminFile = open("admins.json")
    adminData = json.load(adminFile)
    adminFile.close()

    for roleId in adminData["roles"]:
        role = guild.get_role(roleId)
        await auditLog.set_permissions(role, read_messages=True)

    for memberId in adminData["users"]:
        member = guild.get_member(memberId) if guild.get_member(memberId) is not None else await guild.fetch_member(memberId)
        await auditLog.set_permissions(member, read_messages=True)

    jsonData["auditId"] = auditLog.id

    for event in tournament["events"]:
        name = event["name"]

        # Strip the Main/Side/TT prefix
        if ':' in name:
            name = name[name.find(':') + 2:]
        name = name.replace("+", " plus")

        channel = await category.create_text_channel(name)
        await channel.set_permissions(guild.default_role, read_messages=True, send_messages=False)
        jsonData[event["id"]] = channel.id

    updateGames.start()

    eventDataFile = open("eventData.json", "w")
    json.dump(jsonData, eventDataFile)
    eventDataFile.close()
    await interaction.followup.send(f"Tournament betting has been initialised")

# Clears the tournament to be a blank slate
@tree.command(name="clear-tournament", guild=discord.Object(id=guildId))
async def clear_tournament(interaction):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    con = sqlite3.connect('database.db', autocommit=True)
    cur = con.cursor()

    cur.execute("DELETE FROM sets")
    cur.execute("DELETE FROM bets")
    cur.execute("DELETE FROM users")

    con.close()

    if os.path.exists("eventData.json"):
        guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
        with open("eventData.json") as data:
            jsonData = json.load(data)
            for k, v in jsonData.items():
                if k == "stub":
                    continue
                channel = await guild.fetch_channel(v)
                await channel.delete()
        os.remove("eventData.json")

    global viewHelper
    await viewHelper.update_leaderboard()
    updateGames.stop()
    await interaction.followup.send(f"Tournament has been reset")

# Attempts to update games every 60 seconds but will not start it again if the last one is still running (i.e. a slow API call)
running = False
@tasks.loop(seconds=60)
async def updateGames():
    await viewHelper.update_leaderboard()
    if not running:
        await _updateGames()


# Main update loop, calls API and then updates all messages
async def _updateGames():
    global running
    running = True
    jsonFile = open("eventData.json")
    jsonData = json.load(jsonFile)
    jsonFile.close()

    guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
    eventUpdateTasks = []
    async for event in get_games(jsonData["stub"]):
        if event is None:
            continue
        eventUpdateTasks.append(_updateEvent(jsonData, event, guild))

    eventCoroutines = asyncio.gather(*eventUpdateTasks)
    await eventCoroutines

    running = False

# Removes the specified set's message and removes the set from DB
async def resetSet(guild, event, jsonData, gameSet, messageText="resetting bet"):
    con = sqlite3.connect('database.db', autocommit=True)
    cur = con.cursor()
    channelId = jsonData[str(event["id"])]
    channel = guild.get_channel(channelId) if guild.get_channel(channelId) is not None else await guild.fetch_channel(channelId)

    cur.execute(
        'SELECT messageId, channelId FROM sets WHERE setId = ?',
        (gameSet["id"],)
    )

    result = cur.fetchall()[0]

    channel = guild.get_channel(result[1])
    if channel is None:
        channel = await guild.fetch_channel(result[1])

    message = await channel.fetch_message(result[0])

    await message.delete()

    del betViews[gameSet["id"]]
    cur.execute(
        'DELETE FROM sets WHERE setId = ?',
        (gameSet["id"],)
    )
    cur.execute(
        'SELECT * FROM bets WHERE setId = ?',
        (gameSet["id"],)
    )
    betsToRefund = cur.fetchall()
    cur.execute(
        'DELETE FROM bets WHERE setId = ?',
        (gameSet["id"],)
    )
    for bet in betsToRefund:
        user = bet[0]
        amount = bet[3]
        await _give_points(user, amount)

    await addToAuditLog(f"{gameSet["id"]} has been reset, {messageText}")
    con.close()

# Updates each game from the data grabbed from the StartGG API
async def _updateEvent(jsonData, event, guild):
    con = sqlite3.connect('database.db', autocommit=True)
    cur = con.cursor()
    channelId = jsonData[str(event["id"])]
    channel = guild.get_channel(channelId) if guild.get_channel(
        channelId) is not None else await guild.fetch_channel(channelId)
    for phase in event["phases"]:
        for phaseGroup in phase["phaseGroups"]["nodes"]:
            for gameSet in phaseGroup["sets"]["nodes"]:
                if gameSet["slots"][0]["entrant"] is not None and gameSet["slots"][1]["entrant"] is not None:
                    # print(f"Set Id: {gameSet["id"]}")
                    # print(f"Channel Id: {channel.id}")
                    # print(f"Player one name: {gameSet["slots"][0]["entrant"]["name"]}")
                    # print(f"Player two name: {gameSet["slots"][1]["entrant"]["name"]}")
                    # print(f"Set title: {gameSet["fullRoundText"]}")
                    # print(f"Game title: {channel.name}")
                    # print(f"Phase name: {phase["name"]}")
                    # print(f"PhaseGroup name: {phaseGroup["displayIdentifier"]}")
                    pools = ["", "Pool 1", "Pool 2", "Pool 3", "Pool 4", "Pool 5"]
                    # Yuck
                    fullPhaseName = phase["name"] if len(phase["phaseGroups"][
                                                             "nodes"]) < 2 else f"{phase["name"]} {pools[int(phaseGroup["displayIdentifier"])]}"
                    gameFullTitle = f"{fullPhaseName}: {gameSet["fullRoundText"]}"
                    # print(f"FullyQualifiedGameTitle: {gameFullTitle}")
                    # print("")
                    gameSetCreated = gameSet["id"] in betViews.keys()

                    if not gameSetCreated and gameSet["state"] == 3:
                        continue

                    if gameSetCreated:
                        player1Name = gameSet["slots"][0]["entrant"]["name"]
                        player2Name = gameSet["slots"][1]["entrant"]["name"]
                        betView = betViews[gameSet["id"]]
                        if player1Name != betView.playerOneName or player2Name != betView.playerTwoName:
                            await resetSet(guild, event, jsonData, gameSet, "Names of players do not match cached names")

                    else:
                        cur.execute(f'SELECT * FROM sets WHERE setId = ?', (gameSet["id"],))
                        result = cur.fetchall()

                        if len(result) < 1:
                            player1Name = gameSet["slots"][0]["entrant"]["name"]
                            player2Name = gameSet["slots"][1]["entrant"]["name"]

                            gameName = event["name"]
                            if ':' in gameName:
                                gameName = gameName[gameName.find(':') + 2:]
                            timestamp = round(time.time())
                            cur.execute(
                                'INSERT OR IGNORE INTO sets (setId, namePlayerOne, namePlayerTwo, setTitle, gameTitle, timestamp) VALUES (?, ?, ?, ?, ?, ?)',
                                (gameSet["id"], player1Name, player2Name, gameFullTitle, gameName, timestamp)
                            )
                            view = BetView(player1Name, player2Name, gameSet["id"], timestamp)
                            message = await channel.send(
                                embed=discord.Embed(title="Working...", colour=discord.Colour.from_str("#F60143")),
                                view=view)
                            await view.updateMessageObject(message)
                            betViews[gameSet["id"]] = view

                    if gameSet["state"] == 2 or gameSet["state"] == 3:
                        await betViews[gameSet["id"]].startGame(update=False)
                        scorePlayerOne = gameSet["slots"][0]["standing"]["stats"]["score"]["value"]
                        scorePlayerTwo = gameSet["slots"][1]["standing"]["stats"]["score"]["value"]
                        await betViews[gameSet["id"]].updateScore(scorePlayerOne, scorePlayerTwo)
                    if gameSet["state"] == 3:
                        await betViews[gameSet["id"]].endGame()
                        continue
                    if gameSet["state"] == 6:
                        await betViews[gameSet["id"]].startGame()

                elif gameSet["id"] in betViews.keys() and gameSet["state"] != 3:
                    await resetSet(guild, event, jsonData, gameSet, "<2 users assigned to bracket")

# Helper function to add a message to specified audit log
async def addToAuditLog(message):
    if os.path.exists("eventData.json"):
        guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
        with open("eventData.json") as data:
            jsonData = json.load(data)
            channel = guild.get_channel(jsonData["auditId"]) if guild.get_channel(jsonData["auditId"]) is not None else await guild.fetch_channel(jsonData["auditId"])
            await channel.send(message)

bot.run(os.getenv("BOT_KEY"))
