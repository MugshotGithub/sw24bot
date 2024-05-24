import asyncio
import functools
import json
import os
import re
import typing

import discord
from discord import app_commands
import sqlite3
from datetime import datetime

from discord.ext import tasks
from discord.utils import escape_markdown
from dotenv import load_dotenv  # Python-dotenv package
import discord_colorize
from StartGG import get_games, get_tournament_info

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
load_dotenv()

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
guildId = int(os.getenv("GUILD_ID"))
betViews = {}


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


async def _remove_points(userId, points, update=True):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('BEGIN TRANSACTION;')
    cursor.execute('INSERT OR IGNORE INTO users (id) VALUES (?);', (userId,))
    cursor.execute('UPDATE users SET points = points - ? WHERE id = ?;', (points, userId))
    conn.commit()
    conn.close()
    if update:
        await viewHelper.update_leaderboard()


async def _give_points(userId, points, update=True):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('BEGIN TRANSACTION;')
    cursor.execute('INSERT OR IGNORE INTO users (id) VALUES (?);', (userId,))
    cursor.execute('UPDATE users SET points = points + ? WHERE id = ?;', (points, userId))
    conn.commit()
    conn.close()
    if update:
        await viewHelper.update_leaderboard()


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


def __format_leaderboard__(data):
    content = ""
    for index, stat in enumerate(data):
        content += f"{index}. {stat[0]}: {stat[1]}\n"
    return content


class ViewHelperScoreboard:
    def __init__(self, view, channelId, messageId=None):
        self.view = view
        self.channelId = channelId
        self.messageId = messageId
        self.embed = discord.Embed(title="Live Leaderboard", colour=discord.Colour.from_str("#F60143"))
        self.indexOfField = 1

    async def update_leaderboard(self):
        channel = await bot.fetch_channel(self.channelId)
        message = await channel.fetch_message(self.messageId)

        self.embed = discord.Embed(title="Live Leaderboard", colour=discord.Colour.from_str("#F60143"))
        self.embed.add_field(name="Points", inline=False, value=__format_leaderboard__(await _get_leaderboard()))
        self.embed.add_field(name="Bets won", inline=False,
                             value=__format_leaderboard__(await _get_leaderboard("betsWon")))
        self.embed.set_footer(text=f"Last Updated at {datetime.now().strftime('%m/%d %H:%M:%S')} NZST")

        await message.edit(embed=self.embed)

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
            await interaction.followup.send(f"You cannot bet 0 points", ephemeral=True)
            return

        con = sqlite3.connect('database.db')
        cur = con.cursor()

        cur.execute('INSERT OR IGNORE INTO users (id) VALUES (?);', (interaction.user.id,))

        cur.execute('SELECT amount,winner FROM bets WHERE setId = ? AND userId = ?', (self.setId, interaction.user.id))
        betsMatching = cur.fetchall()
        if len(betsMatching) > 0:
            if betsMatching[0][1] != self.playerBetOn:
                # print(betsMatching[0][1])
                # print(self.playerBetOn)
                await interaction.followup.send(
                    f"You cannot bet on {self.playerBetOn} as you have already bet on {betsMatching[0][1]}",
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
        con.commit()

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
        con.commit()

        if len(betsMatching) == 0:
            cur.execute('INSERT INTO bets (userId, setId, winner, amount) VALUES (?, ?, ?, ?);',
                        (interaction.user.id, self.setId, indexBet, betAmount))
            await interaction.followup.send(
                f"Bet {betAmount} point{"s" if betAmount > 1 else ""} on {self.playerBetOn}", ephemeral=True)
        else:
            cur.execute('UPDATE bets SET amount = amount + ? WHERE setId = ? AND userId = ?',
                        (betAmount, self.setId, interaction.user.id))
            await interaction.followup.send(

                f"Bet an extra {betAmount} point{"s" if betAmount > 1 else ""} on {self.playerBetOn}. Your total bet is now {betsMatching[0][0] + betAmount}",
                ephemeral=True)

        con.commit()

        con.close()

        await addToAuditLog(f"{interaction.user.display_name} bet {betAmount} on {self.playerBetOn} in the set {result[8]}: {result[7]}")

        global betViews
        await betViews[self.setId].update()


async def reconnectBetViews():
    con = sqlite3.connect('database.db')
    cur = con.cursor()

    cur.execute('SELECT * FROM sets')
    sets = cur.fetchall()

    con.close()

    for betSet in sets:
        view = BetView(betSet[5], betSet[6], betSet[0])
        betViews[betSet[0]] = view
        bot.add_view(view)


class BetView(discord.ui.View):
    def __init__(self, playerOneName, playerTwoName, setId):
        super().__init__(timeout=None)
        self.playerOneName = playerOneName
        self.playerTwoName = playerTwoName
        self.setId = setId
        self.timeout = None
        self.hasStarted = False
        self.hasEnded = False

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

    async def playerOne(self, interaction):
        if self.hasStarted:
            return await interaction.response.send_message("This game has started, you can no longer bet on it.",
                                                           ephemeral=True)

        await interaction.response.send_modal(BetEntryForm(self.playerOneName, self.setId))
        await self.update()

    async def playerTwo(self, interaction):
        if self.hasStarted:
            return await interaction.response.send_message("This game has started, you can no longer bet on it.",
                                                           ephemeral=True)

        await interaction.response.send_modal(BetEntryForm(self.playerTwoName, self.setId))
        await self.update()

    async def updateMessageObject(self, message: discord.Message):
        con = sqlite3.connect('database.db')
        cur = con.cursor()

        cur.execute('UPDATE sets SET messageId = ?, channelId = ? WHERE setId = ?',
                    (message.id, message.channel.id, self.setId))

        con.commit()
        con.close()
        await self.update(updateScoreboard=False)

    async def updateScore(self, playerOneScore, playerTwoScore):
        con = sqlite3.connect('database.db')
        cur = con.cursor()

        cur.execute('SELECT playerOneScore,playerTwoScore FROM sets WHERE setId = ?', (self.setId,))
        result = cur.fetchall()

        if playerOneScore == result[0] and playerTwoScore == result[1]:
            con.close()
            return

        cur.execute('UPDATE sets SET playerOneScore = ?, playerTwoScore = ? WHERE setId = ?',
                    (playerOneScore, playerTwoScore, self.setId))
        con.commit()
        con.close()
        await self.update()

    async def startGame(self, update=True):
        if not self.hasStarted:
            self.clear_items()
            self.hasStarted = True
            await addToAuditLog(f"{self.setId} has started")
            if update:
                await self.update()

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
        embed.add_field(name=discord.utils.escape_markdown(self.playerOneName), inline=True, value=result[6])
        embed.add_field(name='', value="vs", inline=True)
        embed.add_field(name=discord.utils.escape_markdown(self.playerTwoName), inline=True, value=result[7])
        embed.add_field(name='', inline=False, value='')

        embed.add_field(name='Bets total', inline=True, value=f"{result[4]} Points")
        embed.add_field(name='', inline=True, value='')
        embed.add_field(name=' ᲼᲼ ', inline=True, value=f"{result[5]} Points")
        colors = discord_colorize.Colors()

        totalBet = result[4] + result[5]
        totalHashes = 52

        if result[4] > 0:
            numPlayerOne = round((result[4] / totalBet) * totalHashes)
        else:
            numPlayerOne = 0

        if result[5] > 0:
            numPlayerTwo = round((result[5] / totalBet) * totalHashes)
        else:
            numPlayerTwo = 0

        numNone = 0 if result[4] + result[5] >= 1 else totalHashes

        #Formatting looks weird but it is what it is
        progressBar = f"""```ansi
{colors.colorize('𓃑' * numPlayerOne, fg='cyan')}{colors.colorize('𓃑' * numPlayerTwo, fg='blue')}{colors.colorize('𓃑' * numNone, fg='gray')}
```
        """
        embed.add_field(name='', value=progressBar, inline=False)

        if self.hasStarted:
            embed.set_footer(text="Game has started, Betting no longer allowed")

        await message.edit(embed=embed)

    async def endGame(self):
        if not self.hasEnded:
            self.hasEnded = True
            con = sqlite3.connect('database.db')
            cur = con.cursor()

            cur.execute(
                'SELECT betsPlayerOne, betsPlayerTwo, scorePlayerOne, scorePlayerTwo, namePlayerOne, namePlayerTwo, setTitle, gameTitle amount FROM sets WHERE setId = ?',
                (self.setId,)
            )
            info = cur.fetchall()[0]
            gameTitle = info[7]
            setTitle = info[6]
            playerOne = info[4]
            playerTwo = info[5]

            totalPayout = info[0] + info[1]

            winner = 0 if info[2] > info[3] else 1

            cur.execute(
                'SELECT userId, winner, amount FROM sets WHERE setId = ?',
                (self.setId,)
            )
            bets = cur.fetchall()

            for bet in bets:
                if bet[1] == winner:
                    amount = bet[2]

                    payout = (totalPayout / info[winner]) * amount

                    if payout < amount*2:
                        payout = amount*2

                    await _give_points(bet[0], payout, False)
                    guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)

                    member = guild.get_member(bet[0]) if guild.get_member(bet[0]) is not None else await guild.fetch_member(bet[0])
                    await member.send(f"You won {amount} from the bet placed on {escape_markdown(playerOne)} vs {escape_markdown(playerTwo)} (**{gameTitle}** - {setTitle})")

            await viewHelper.update_leaderboard()


@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=guildId))
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
    await addToAuditLog(f"{interaction.user.display_name} sent {member.display_name} {points} points")
    await _transfer_points(interaction.user.id, member.id, points)
    await interaction.followup.send(f"Sent {member.display_name} {points} points!")


@tree.command(name="pay-member", description="Transfers your points to a user. Alias of transfer-points",
              guild=discord.Object(id=guildId))
async def pay_member(interaction, member: discord.Member, points: int):
    await interaction.response.defer(ephemeral=True)
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


@tree.command(name="setup-tournament", guild=discord.Object(id=guildId))
async def setup_tournament(interaction, tournament_stub: str):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)

    con = sqlite3.connect('database.db')
    cur = con.cursor()
    for member in guild.members:
        if not member.bot:
            cur.execute('INSERT OR IGNORE INTO users (id) VALUES (?);', (member.id,))
    con.commit()
    con.close()

    await viewHelper.update_leaderboard()

    jsonData = {
        "stub": tournament_stub
    }

    tournament = await get_tournament_info(tournament_stub)
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
        name = name[name.find(':') + 2:]
        name = name.replace("+", " plus")

        channel = await category.create_text_channel(name)
        jsonData[event["id"]] = channel.id

    updateGames.start()

    eventDataFile = open("eventData.json", "w")
    json.dump(jsonData, eventDataFile)
    eventDataFile.close()
    await interaction.followup.send(f"Tournament betting has been initialised")


@tree.command(name="clear-tournament", guild=discord.Object(id=guildId))
async def clear_tournament(interaction):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    con = sqlite3.connect('database.db')
    cur = con.cursor()

    cur.execute("DELETE FROM sets")
    cur.execute("DELETE FROM bets")
    cur.execute("DELETE FROM users")

    con.commit()
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

running = False
@tasks.loop(seconds=60)
async def updateGames():
    if not running:
        await _updateGames()


async def _updateGames():
    global running
    running = True
    jsonFile = open("eventData.json")
    jsonData = json.load(jsonFile)
    jsonFile.close()

    guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
    eventUpdateTasks = []
    async for event in get_games(jsonData["stub"]):
        eventUpdateTasks.append(_updateEvent(jsonData, event, guild))

    eventCoroutines = asyncio.gather(*eventUpdateTasks)
    await eventCoroutines

    running = False

async def _updateEvent(jsonData, event, guild):
    con = sqlite3.connect('database.db')
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
                    print(f"Game title: {channel.name}")
                    # print(f"Phase name: {phase["name"]}")
                    # print(f"PhaseGroup name: {phaseGroup["displayIdentifier"]}")
                    pools = ["", "Pool 1", "Pool 2"]
                    # Yuck
                    fullPhaseName = phase["name"] if len(phase["phaseGroups"][
                                                             "nodes"]) < 2 else f"{phase["name"]} {pools[int(phaseGroup["displayIdentifier"])]}"
                    gameFullTitle = f"{fullPhaseName}: {gameSet["fullRoundText"]}"
                    # print(f"FullyQualifiedGameTitle: {gameFullTitle}")
                    # print("")

                    if gameSet["state"] == 1:
                        cur.execute(f'SELECT * FROM sets WHERE setId = ?', (gameSet["id"],))
                        result = cur.fetchall()

                        if len(result) < 1:
                            player1Name = gameSet["slots"][0]["entrant"]["name"]
                            player2Name = gameSet["slots"][1]["entrant"]["name"]

                            gameName = event["name"]
                            gameName = gameName[gameName.find(':') + 2:]

                            cur.execute(
                                'INSERT OR IGNORE INTO sets (setId, namePlayerOne, namePlayerTwo, setTitle, gameTitle) VALUES (?, ?, ?, ?, ?)',
                                (gameSet["id"], player1Name, player2Name, gameFullTitle, gameName)
                            )
                            con.commit()
                            view = BetView(player1Name, player2Name, gameSet["id"])
                            message = await channel.send(
                                embed=discord.Embed(title="Working...", colour=discord.Colour.from_str("#F60143")),
                                view=view)
                            await view.updateMessageObject(message)
                            betViews[gameSet["id"]] = view

                    else:
                        if gameSet["state"] == 2 or gameSet["state"] == 3:
                            betViews[gameSet["id"]].startGame(update=False)
                            scorePlayerOne = gameSet["slots"][0]["standing"]["stats"]["score"]["value"]
                            scorePlayerTwo = gameSet["slots"][1]["standing"]["stats"]["score"]["value"]
                            betViews[gameSet["id"]].updateScore(scorePlayerOne, scorePlayerTwo)
                            if gameSet["state"] == 3:
                                betViews[gameSet["id"]].endGame()
                            continue

                        await betViews[gameSet["id"]].startGame()

# @tree.command(name="test-create-bet", guild=discord.Object(id=guildId))
# async def createBet(interaction):
#     await interaction.response.defer(ephemeral=True)
#     conn = sqlite3.connect('database.db')
#     cursor = conn.cursor()
#
#     cursor.execute('INSERT OR IGNORE INTO sets (setId, namePlayerOne, namePlayerTwo, setTitle, gameTitle) VALUES ("Test", "LongerNameTest", "LongerNameTest2", "final", "smnash bronther")')
#
#     conn.commit()
#     conn.close()
#
#     await interaction.followup.send(f"Working")
#     view = BetView("LongerNameTest", "LongerNameTest2", "Test")
#
#     message = await interaction.channel.send(embed=discord.Embed(title="Working...", colour=discord.Colour.from_str("#F60143")), view=view)
#     await view.updateMessageObject(message)

async def addToAuditLog(message):
    if os.path.exists("eventData.json"):
        guild = bot.get_guild(guildId) if bot.get_guild(guildId) is not None else await bot.fetch_guild(guildId)
        with open("eventData.json") as data:
            jsonData = json.load(data)
            channel = guild.get_channel(jsonData["auditId"]) if guild.get_channel(jsonData["auditId"]) is not None else await guild.fetch_channel(jsonData["auditId"])
            await channel.send(message)



bot.run(os.getenv("BOT_KEY"))
