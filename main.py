import json
import os

import discord
from discord import app_commands
import sqlite3
from datetime import datetime
from dotenv import load_dotenv # Python-dotenv package

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
load_dotenv()

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
guildId = int(os.getenv("GUILD_ID"))


viewHelper = None


async def _isAdmin(userId):
    adminFile = open("admins.json")
    adminData = json.load(adminFile)
    adminFile.close()

    guild = await bot.fetch_guild(guildId)
    member = await guild.fetch_member(userId)

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
    cursor.execute('INSERT OR IGNORE INTO users (id, points) VALUES (?, 0);', (userId,))
    cursor.execute('UPDATE users SET points = points - ? WHERE id = ?;', (points, userId))
    conn.commit()
    conn.close()
    if update:
        await viewHelper.update_leaderboard()


async def _give_points(userId, points, update=True):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('BEGIN TRANSACTION;')
    cursor.execute('INSERT OR IGNORE INTO users (id, points) VALUES (?, 0);', (userId,))
    cursor.execute('UPDATE users SET points = points + ? WHERE id = ?;', (points, userId))
    conn.commit()
    conn.close()
    if update:
        await viewHelper.update_leaderboard()


async def _get_leaderboard(stat="points") -> list:
    guild = await bot.fetch_guild(guildId)
    con = sqlite3.connect('database.db')
    cur = con.cursor()
    cur.execute(f'SELECT id,{stat} FROM users ORDER BY {stat} DESC LIMIT 10')
    results = cur.fetchall()
    leaderboard = []

    for result in results:
        member = await guild.fetch_member(result[0])
        leaderboard.append([member, result[1]])

    con.close()

    return leaderboard

def _get_points_batch(playerIds):
    con = sqlite3.connect('database.db')
    cur = con.cursor()
    cur.execute(f'SELECT points FROM users WHERE id = ?')
    con.close()

    return cur.fetchall()[0]


# async def button_func(interaction: discord.Interaction):

def __format_leaderboard__(data):
    content = ""
    for index, stat in enumerate(data):
        content += f"{index}. {stat[0]}: {stat[1]}\n"
    return content


class ViewHelper:
    def __init__(self, view, channelId, messageId=None):
        self.view = view
        self.channelId = channelId
        self.messageId = messageId
        self.embed = discord.Embed(title="Live Leaderboard", colour=discord.Colour.from_str("#F60143"))
        self.indexOfField = 1

    # async def add_button(self, label):
    #     button = discord.ui.Button(style=discord.ButtonStyle.primary, label=label,
    #                                custom_id=f"{self.channelId}:{self.indexOfField}")
    #     button.callback = button_func
    #     self.indexOfField += 1
    #
    #     self.view.add_item(button)
    #     self.embed.add_field(name=label + " (0)", inline=True, value="")

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
        viewHelper = ViewHelper(view, channelObject.id, messageId=message.id)
        # for child in view.children:
        #     child.callback = button_func

    print(f'Logged in as {bot.user.name}')
    print('------')

    if viewHelper is not None:
        await viewHelper.update_leaderboard()


@tree.command(name="give-points", description="Gives a user points", guild=discord.Object(id=guildId))
async def give_points(interaction, member: discord.Member, points: int):
    await interaction.response.defer()
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return
    await _give_points(member.id, points)
    await interaction.followup.send(f"Added {points} points to {member.display_name}")


@tree.command(name="remove-points", description="Removes a user's points", guild=discord.Object(id=guildId))
async def remove_points(interaction, member: discord.Member, points: int):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return
    await _remove_points(member.id, points)
    await interaction.followup.send(f"Added {points} points to {member.display_name}")


async def _transfer_points(fromMemberId, toMemberId, num):
    guild = await bot.fetch_guild(guildId)
    await _remove_points(fromMemberId, num, update=False)
    await _give_points(toMemberId, num)
    toUser = await bot.fetch_user(toMemberId)
    fromUser = await guild.fetch_member(fromMemberId)
    await toUser.send(f"{fromUser.display_name} has sent you {num} points!")


@tree.command(name="transfer-points", description="Transfers your points to a user. Alias of pay-member", guild=discord.Object(id=guildId))
async def transfer_points(interaction, member: discord.Member, points: int):
    await interaction.response.defer(ephemeral=True)
    await _transfer_points(interaction.user.id, member.id, points)
    await interaction.followup.send(f"Sent {member.display_name} {points} points!")


@tree.command(name="pay-member", description="Transfers your points to a user. Alias of transfer-points", guild=discord.Object(id=guildId))
async def pay_member(interaction, member: discord.Member, points: int):
    await interaction.response.defer(ephemeral=True)
    await _transfer_points(interaction.user.id, member.id, points)
    await interaction.followup.send(f"Sent {member.display_name} {points} points!")


@tree.command(name="leaderboard", description="Gets the top 10 points collectors", guild=discord.Object(id=guildId))
async def post_leaderboard(interaction):
    await interaction.response.defer(ephemeral=True)
    leaderboard = "Points Leaderboard:"
    for member, points in await _get_leaderboard():
        leaderboard += f"\n* {member.display_name}: {points}"
    await interaction.followup.send(leaderboard)


@tree.command(name="create-live-leaderboard", description="Create the live leaderboard", guild=discord.Object(id=guildId))
async def create_live_leaderboard(interaction):
    await interaction.response.defer()
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command", ephemeral=True)
        return
    view = discord.ui.View(timeout=None)
    vh = ViewHelper(view, interaction.channel.id)
    global viewHelper
    viewHelper = vh
    await vh.post_view()
    await interaction.followup.send(f"Creating post", ephemeral=True, delete_after=3, silent=True)

@tree.command(name="add-admin-user", description="Adds a member to the list of admins", guild=discord.Object(id=guildId))
async def add_admin_user(interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    adminFile = open("admins.json")
    adminData = json.load(adminFile)

    adminData["users"].append(member.id)
    adminFile.close()

    adminFile = open("admins.json", "w")
    json.dump(adminData, adminFile)
    adminFile.close()

    await interaction.followup.send(f"Added {member.display_name} to the admin list")

@tree.command(name="remove-admin-user", description="Removes a member from the list of admins", guild=discord.Object(id=guildId))
async def remove_admin_user(interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

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

@tree.command(name="add-admin-role", description="Adds a role to the list of roles that count as admins", guild=discord.Object(id=guildId))
async def add_admin_role(interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

    adminFile = open("admins.json")
    adminData = json.load(adminFile)

    adminData["roles"].append(role.id)

    adminFile.close()
    adminFile = open("admins.json", "w")
    json.dump(adminData, adminFile)
    adminFile.close()

    await interaction.followup.send(f"Added {role.name} to the admin list")

@tree.command(name="remove-admin-role", description="Removes a role to the list from roles that count as admins", guild=discord.Object(id=guildId))
async def add_admin_role(interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    if not await _isAdmin(interaction.user.id):
        await interaction.followup.send(f"You do not have permission to use this command")
        return

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


bot.run(os.getenv("BOT_KEY"))
