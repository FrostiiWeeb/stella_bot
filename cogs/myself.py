import discord
import datetime
import contextlib
import time
import asyncio
import traceback
import io
import textwrap
from typing import Union
from discord.ext import commands
from discord.ext.commands import Greedy
from utils.decorators import event_check
from utils.useful import call, AfterGreedy, empty_page_format, MenuBase
from utils.new_converters import ValidCog, IsBot, DatetimeConverter, JumpValidator
from utils import flags as flg
from jishaku.codeblocks import codeblock_converter


class Myself(commands.Cog, command_attrs=dict(hidden=True)):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        return await commands.is_owner().predicate(ctx)

    @commands.command(cls=flg.SFlagCommand)
    @flg.add_flag("--joined_at", type=DatetimeConverter)
    @flg.add_flag("--jump_url", type=JumpValidator)
    @flg.add_flag("--requested_at", type=DatetimeConverter)
    @flg.add_flag("--reason", nargs="+")
    @flg.add_flag("--message", type=discord.Message)
    @flg.add_flag("--author", type=discord.Member)
    async def addbot(self, ctx, bot: IsBot, **flags):
        new_data = {'bot_id': bot.id}
        if message := flags.pop('message'):
            new_data['author_id'] = message.author.id
            new_data['reason'] = message.content
            new_data['requested_at'] = message.created_at
            new_data['jump_url'] = message.jump_url

        if auth := flags.pop('author'):
            new_data['author_id'] = auth.id
        for flag, item in flags.items():
            if item:
                new_data.update({flag: item})

        if exist := await self.bot.pool_pg.fetchrow("SELECT * FROM confirmed_bots WHERE bot_id=$1", bot.id):
            existing_data = dict(exist)
            existing_data.update(new_data)
            query = "UPDATE confirmed_bots SET "
            queries = [f"{k}=${i}" for i, k in enumerate(list(existing_data)[1:], start=2)]
            query += ", ".join(queries)
            query += " WHERE bot_id=$1"
            new_data = existing_data
        else:
            query = "INSERT INTO confirmed_bots VALUES($1, $2, $3, $4, $5, $6)"
            for x in "author_id", "reason", "requested_at", "jump_url":
                if new_data.get(x) is None:
                    new_data[x] = None
            new_data['joined_at'] = bot.joined_at
        values = [*new_data.values()]
        result = await self.bot.pool_pg.execute(query, *values)
        await ctx.maybe_reply(result)
        self.bot.confirmed_bots.add(bot.id)

    @commands.command()
    async def su(self, ctx, member: Union[discord.Member, discord.User], *, content):
        message = ctx.message
        message.author = member
        message.content = ctx.prefix + content
        self.bot.dispatch("message", message)

    @commands.command(cls=flg.SFlagCommand)
    @flg.add_flag("--uses", type=int, default=1)
    @flg.add_flag("--code", type=codeblock_converter)
    async def command(self, ctx, **flags):
        coding = {
            "_bot": self.bot,
            "commands": commands
        }
        content = flags["code"].content
        values = content.split("\n")
        values.pop()
        command = values.pop()
        values.append(f'_bot.add_command({command})')
        values.insert(1, f'@commands.is_owner()')
        exec("\n".join(values), coding)

        uses = flags["uses"]

        def check(ctx):
            return ctx.command.qualified_name == coding[command].qualified_name and self.bot.stella == ctx.author

        await ctx.message.add_reaction("<:next_check:754948796361736213>")
        while c := await self.bot.wait_for("command_completion", check=check):
            uses -= 1
            if uses <= 0:
                await ctx.message.add_reaction("<:checkmark:753619798021373974>")
                return self.bot.remove_command(c.command.qualified_name)

    @commands.command(name="eval", help="Eval for input/print feature", aliases=["e", "ev", "eva"])
    async def _eval(self, ctx, *, code: codeblock_converter):
        loop = ctx.bot.loop
        stdout = io.StringIO()

        def sending_print():
            nonlocal stdout
            content = stdout.getvalue()
            if content:
                printing(content, now=True)
                stdout.truncate(0)
                stdout.seek(0)

        # Shittiest code I've ever wrote remind me to think of another way
        def run_async(coro, wait_for_value=True):
            if wait_for_value:
                sending_print()
                ctx.waiting = datetime.datetime.utcnow() + datetime.timedelta(seconds=60)
                ctx.result = None

                async def getting_result():
                    ctx.result = await coro

                run = run_async(getting_result(), wait_for_value=False)
                while ctx.waiting > datetime.datetime.utcnow() and not run.done():
                    time.sleep(1)
                if not run.done():
                    raise asyncio.TimeoutError(f"{coro} took to long to give a result")
                return ctx.result

            task = loop.create_task(coro)

            def coroutine_dies(target_task):
                ctx.failed = target_task.exception()

            task.add_done_callback(coroutine_dies)
            return task

        def printing(*content, now=False, channel=ctx, reply=True, mention=False, **kwargs):
            async def sending(cont):
                nonlocal channel, reply, mention
                if c := channel is not ctx:
                    channel = await commands.TextChannelConverter().convert(ctx, str(channel))

                attr = ("send", "reply")[reply is not c]
                sent = getattr(channel, attr)
                text = textwrap.wrap(cont, 1000, replace_whitespace=False)
                ctx.channel_used = channel if channel is not ctx else ctx.channel
                if len(text) == 1:
                    kwargs = {"content": cont}
                    if attr == "reply":
                        kwargs.update({"mention_author": mention})
                    await sent(**kwargs)
                else:
                    menu = MenuBase(empty_page_format([*map("```{}```".format, text)]))
                    await menu.start(ctx)

            if now:
                showing = " ".join(map(lambda x: (str(x), '\u200b')[x == ''], content if content else ('\u200b',)))
                run_async(sending(showing), wait_for_value=False)
            else:
                print(*content, **kwargs)

        def inputting(*content, channel=ctx, target=(ctx.author.id,), **kwargs):
            target = discord.utils.SnowflakeList(target, is_sorted=True)

            async def waiting_respond():
                return await ctx.bot.wait_for("message", check=waiting, timeout=60)

            def waiting(m):
                return target.has(m.author.id) and m.channel == ctx.channel_used

            printing(*content, channel=channel, **kwargs)
            if result := run_async(waiting_respond()):
                return result.content

        async def giving_emote(e):
            if ctx.me.permissions_in(ctx.channel).external_emojis:
                await ctx.message.add_reaction(e)

        async def starting(startup):
            ctx.running = True
            while ctx.running:
                if datetime.datetime.utcnow() > startup + datetime.timedelta(seconds=5):
                    await giving_emote("<:next_check:754948796361736213>")
                    break
                await asyncio.sleep(1)

        variables = {
            "discord": discord,
            "commands": commands,
            "_channel": ctx.channel,
            "_bot": self.bot,
            "_ctx": ctx,
            "print": printing,
            "input": inputting,
            "_message": ctx.message,
            "_author": ctx.author,
            "_await": run_async
        }

        values = code.content.splitlines()
        if not values[-1].startswith(("return", "raise", " ", "yield")):
            values[-1] = f"return {values[-1]}"
        values.insert(0, "yield")
        values = [f"{'':>4}{v}" for v in values]
        values.insert(0, "def _to_run():")

        def running():
            yield (yield from variables['_to_run']())

        def in_exec():
            loop.create_task(starting(datetime.datetime.utcnow()))
            with contextlib.redirect_stdout(stdout):
                for result in running():
                    sending_print()
                    if result is not None:
                        loop.create_task(ctx.send(result))
        try:
            exec("\n".join(values), variables)
            await loop.run_in_executor(None, in_exec)
            if ctx.failed:
                raise ctx.failed from None
        except Exception as e:
            ctx.running = False
            await giving_emote("<:crossmark:753620331851284480>")
            lines = traceback.format_exception(type(e), e, e.__traceback__)
            error_trace = f"Evaluation failed:\n{''.join(lines)}"
            await ctx.reply(f"{stdout.getvalue()}```py\n{error_trace}```", delete_after=60)
        else:
            ctx.running = False
            await giving_emote("<:checkmark:753619798021373974>")

    @commands.Cog.listener()
    @event_check(lambda s, b, a: (b.content and a.content) or b.author.bot)
    async def on_message_edit(self, before, after):
        if await self.bot.is_owner(before.author) and not before.embeds and not after.embeds:
            await self.bot.process_commands(after)

    @commands.command()
    async def dispatch(self, ctx, message: discord.Message):
        self.bot.dispatch('message', message)
        await ctx.message.add_reaction("<:checkmark:753619798021373974>")

    async def cogs_handler(self, ctx, extensions):
        method = ctx.command.name

        def do_cog(exts):
            func = getattr(self.bot, f"{method}_extension")
            return func(f"cogs.{exts}")

        outputs = [call(do_cog, ext, ret=True) or f"cogs.{ext} is {method}ed"
                   for ext in extensions]
        await ctx.embed(description="\n".join(str(x) for x in outputs))


def setup(bot):
    cog = Myself(bot)
    for name in ("load", "unload", "reload"):
        @commands.command(name=name, aliases=["c"+name, name+"s"], cls=AfterGreedy)
        async def _cog_load(self, ctx, extension: Greedy[ValidCog]):
            await self.cogs_handler(ctx, extension)

        cog.__cog_commands__ += (_cog_load,)
    bot.add_cog(cog)
