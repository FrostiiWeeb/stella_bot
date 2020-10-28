from discord.ext import commands
from utils.errors import NotValidCog, ThisEmpty
from discord.utils import _unique
from itertools import chain


class FetchUser(commands.Converter):
    async def convert(self, ctx, argument):
        return await ctx.bot.fetch_user(int(argument))


class CleanListGreedy:
    @classmethod
    async def after_greedy(cls, ctx, greedy_list):
        """
        This method will be called after greedy was processed. This will remove any duplicates of a list, putting list
        within a list into the current list. Set was not used to keep the original order.
        """
        unclean = list(chain.from_iterable(greedy_list))
        final = _unique(unclean)
        if not final:
            raise ThisEmpty(cls.__name__)
        final.reverse()
        return final


class ValidCog(CleanListGreedy):
    @classmethod
    async def convert(cls, ctx, argument):
        valid_cog = {"useful": ["use", "u"],
                     "helpful": ["help", "h"],
                     "myself": ["stella", "my", "self", "m"],
                     "find_bot": ["find", "f", "bot"],
                     "error_handler": ["error", "e", "err", "error_handlers"],
                     "all": ["al", "a", "*"]}

        for key in valid_cog:
            if key == argument or argument in valid_cog[key]:
                if key == "all":
                    return [x for x in valid_cog if key != x]
                return key
        raise NotValidCog(argument)

