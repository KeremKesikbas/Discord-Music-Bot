import os

from discord.ext import commands

# loading modules

client = commands.Bot(command_prefix = "-", help_command=None)

for filename in os.listdir("./src"):
    if filename.endswith(".py"):
        client.load_extension(f"src.{filename[:-3]}")


tokenFile = open("Token.txt", "r")

Token: str = tokenFile.read()

client.run(Token)